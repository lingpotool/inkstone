"""Windows 字体引擎 —— 真字形 + 真度量，来自**同一份字体缓存**。

这一层解决的是最要命的一类问题：中文渲染成方块。

    之前的软件光栅用内置的确定性字形（5×7 位图 + 非拉丁占位块）。
    那是验证后端的设计，好处是跨平台逐比特一致，代价是**中文看不出是什么字**。
    对一个自称"中文一等公民"的 UI 库，这是不能接受的。

关键设计：**度量与字形必须来自同一个 HFONT。**

    如果度量用一套（比如内置表），字形用另一套（系统字体），
    字距就会和字形对不上——排版按 14px 排版，字形却按 13.2px 画，
    于是有的字挤在一起、有的字之间留缝。这正是 docs/04 §3
    「测量与绘制同源」要防的事，而且在中文上格外明显。

    所以本模块把两件事收进**一个对象**：`GdiFontEngine` 同时实现
    `MetricsProvider`（度量）与 `GlyphProvider`（字形），
    共用同一份 HFONT 缓存。谁都没法让它们不一致。

两条实现取径（都经过实证，不是拍脑袋）：

1. **度量**：`GetCharABCWidthsFloatW` 给逐字素簇的亚像素宽度，
   再用 `GetTextExtentPoint32W` 的**精确整串宽度**做一次归一化。
   为什么要归一化：GDI 的单字符 API 只能给整数宽度，逐个累加会累积
   舍入误差（40 个字符最多能偏出一二十像素，行尾对齐肉眼可见）。
   用整串精确值按比例微调，既拿到亚像素精度，又保证"逐字之和不超出整串"。

2. **字形**：不取轮廓，而是**把字画进一块 32bpp 内存 DIB 再读回像素**。
   为什么不用 `GetGlyphOutline`：那个 API 在字体链接（font linking）、
   颜色 emoji、字体替换上有一堆边界情况。直接画则天然拿到平台自己
   认可的结果——**GDI 怎么排版，我们就怎么取像素**，不会有第二套解释。
   白字黑底，像素亮度就是覆盖度，抗锯齿是 GDI 送的。

分层：本模块属于 L0（平台抽象层）。`ctypes` / `windll` 只允许出现在这里，
`text/` 与 `gfx/` 都不许碰——有架构测试守着。

状态：已实现（Windows）。
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from .fonts import (
    FontFace,
    FontMetricsError,
    FontSlant,
    FontSpec,
    FontWeight,
    GlyphPlacement,
    GlyphRun,
    TextMetrics,
)
from .headless_fonts import grapheme_clusters

__all__ = ["GdiFontEngine", "gdi_font_engine"]


# ------------------------------------------------------------------ GDI 契约
#
# 只声明用到的部分。字段顺序与类型必须与 wingdi.h 严格一致——
# 错一个类型，后面所有字段都会读到垃圾值，而且不会报错，只会画出诡异的结果。

_LOGPIXELSY = 90

_DEFAULT_CHARSET = 1
_OUT_DEFAULT_PRECIS = 0
_CLIP_DEFAULT_PRECIS = 0
# 用灰度抗锯齿而不是 ClearType：ClearType 生成的是亚像素 RGB 条纹，
# 读回像素时无法还原成单一覆盖度（会在字形边缘出现红蓝边）。
_ANTIALIASED_QUALITY = 4
_DEFAULT_PITCH = 0
_FF_DONTCARE = 0

_FW_NORMAL = 400
_FW_MEDIUM = 500
_FW_SEMIBOLD = 600
_FW_BOLD = 700

_BI_RGB = 0
_DIB_RGB_COLORS = 0

# PatBlt 的栅格操作码。BLACKNESS(0x42) 把目标填成全黑——这正是我们要的
# "黑底"，白字画上去之后像素亮度就是覆盖度。注意别写成 WHITENESS(0x62)，
# 那会得到反过来的掩码（前景透明、背景全不透明），而且不会报错。
_BLACKNESS = 0x00000042

_TA_LEFT = 0
_TA_BASELINE = 24
_TRANSPARENT = 1

_DEFAULT_GUI_FONT = 17

# 一个通用名在 Windows 上落到哪个真字体。这是**平台知识**，所以只能待在 L0。
# `system-ui` 在 CSS 里是"平台默认 UI 字体"，Windows 上就是 Segoe UI。
_GENERIC_FAMILIES: dict[str, str] = {
    "system-ui": "Segoe UI",
    "sans-serif": "Segoe UI",
    "serif": "Times New Roman",
    "monospace": "Consolas",
    "mono": "Consolas",
}


class _ABC(ctypes.Structure):
    """`GetCharABCWidthsFloatW` 的输出。A/B/C 分别是左留白、字身、右留白。"""

    _fields_ = [
        ("abcA", ctypes.c_float),
        ("abcB", ctypes.c_float),
        ("abcC", ctypes.c_float),
    ]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _TEXTMETRICW(ctypes.Structure):
    _fields_ = [
        ("tmHeight", wintypes.LONG),
        ("tmAscent", wintypes.LONG),
        ("tmDescent", wintypes.LONG),
        ("tmInternalLeading", wintypes.LONG),
        ("tmExternalLeading", wintypes.LONG),
        ("tmAveCharWidth", wintypes.LONG),
        ("tmMaxCharWidth", wintypes.LONG),
        ("tmWeight", wintypes.LONG),
        ("tmOverhang", wintypes.LONG),
        ("tmDigitizedAspectX", wintypes.LONG),
        ("tmDigitizedAspectY", wintypes.LONG),
        ("tmFirstChar", wintypes.WCHAR),
        ("tmLastChar", wintypes.WCHAR),
        ("tmDefaultChar", wintypes.WCHAR),
        ("tmBreakChar", wintypes.WCHAR),
        ("tmItalic", ctypes.c_byte),
        ("tmUnderlined", ctypes.c_byte),
        ("tmStruckOut", ctypes.c_byte),
        ("tmPitchAndFamily", ctypes.c_byte),
        ("tmCharSet", ctypes.c_byte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _LOGFONTW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", wintypes.LONG),
        ("lfWidth", wintypes.LONG),
        ("lfEscapement", wintypes.LONG),
        ("lfOrientation", wintypes.LONG),
        ("lfWeight", wintypes.LONG),
        ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte),
        ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte),
        ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte),
        ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName", wintypes.WCHAR * 32),
    ]


def _load_gdi() -> tuple[Any, Any] | None:
    """加载 gdi32 / user32。非 Windows 或加载失败时返回 None（由调用方降级）。"""
    if sys.platform != "win32":
        return None
    try:
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return None

    # 显式声明签名。64 位下句柄是 64 位，不声明会被截断成 int32，
    # 表现是"有时候能画有时候画出乱码"，极难查。
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    gdi32.CreateFontIndirectW.argtypes = [ctypes.POINTER(_LOGFONTW)]
    gdi32.CreateFontIndirectW.restype = wintypes.HFONT
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ

    gdi32.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.POINTER(_TEXTMETRICW)]
    gdi32.GetTextMetricsW.restype = wintypes.BOOL
    gdi32.GetTextExtentPoint32W.argtypes = [
        wintypes.HDC,
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(_SIZE),
    ]
    gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
    gdi32.GetCharABCWidthsFloatW.argtypes = [
        wintypes.HDC,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(_ABC),
    ]
    gdi32.GetCharABCWidthsFloatW.restype = wintypes.BOOL

    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP

    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetTextAlign.argtypes = [wintypes.HDC, wintypes.UINT]
    gdi32.TextOutW.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    gdi32.TextOutW.restype = wintypes.BOOL
    gdi32.PatBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.PatBlt.restype = wintypes.BOOL

    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    return gdi32, user32


def _weight_value(weight: FontWeight) -> int:
    return {
        FontWeight.REGULAR: _FW_NORMAL,
        FontWeight.MEDIUM: _FW_MEDIUM,
        FontWeight.SEMIBOLD: _FW_SEMIBOLD,
        FontWeight.BOLD: _FW_BOLD,
    }[weight]


def _utf16_len(text: str) -> int:
    """文本的 UTF-16 **码元**个数。

    必须用这个而不是 `len(text)`：Win32 的 W 系 API 数的是 UTF-16 码元，
    而 Python 的 `str` 数的是码点。两者只在基本多文种平面内相等——
    一个 emoji 是 1 个码点但 **2 个码元**。传 `len()` 过去会让 GDI
    只量/只画半个代理对，结果是宽度错、字形变成一个空方框，
    而且不报错。这个坑对"emoji 能否正确显示"是决定性的。
    """
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True, slots=True)
class _FaceKey:
    """字体缓存的键：族 + 整数像素字号 + 字重 + 倾斜。

    用**整数**像素作为字号，是因为 GDI 的 lfHeight 本来就是整数；
    把 `spec`（可能带小数）直接当键会导致同一个字号被反复创建字体。
    """

    family: str
    pixel_size: int
    weight: int
    italic: bool


@dataclass
class _Face:
    """一份已创建的字体 + 它的度量常量 + 预留的 DIB。"""

    handle: int
    ascent: int
    descent: int
    external_leading: int
    pixel_size: int


class GdiFontEngine:
    """Windows 字体引擎：度量与字形来自同一份 HFONT。

    同时满足两个协议：

        MetricsProvider —— has_family / resolve_font / measure_text / shape_line
        GlyphProvider   —— mask_for / is_placeholder_only

    所以它既能喂给 `TextEngine`（排版），也能喂给 `SoftwareRasterizer`（绘制），
    两边用的是**同一个**字体对象，字距与字形天然对齐。

    **必须在同一个线程内使用。** GDI 的 DC 与 GDI 对象有线程亲和性。
    对一个 UI 库来说这不是限制——UI 本来就在主线程。
    """

    #: DIB 四周留的余量（像素）。字形可能超出 advance 一点（斜体、曲线外凸），
    #: 留 2px 免得被切掉。
    _MARGIN = 2

    def __init__(self) -> None:
        loaded = _load_gdi()
        if loaded is None:
            raise FontMetricsError(
                "GDI 字体引擎只在 Windows 上可用。"
                "其他平台请用 HeadlessMetrics（确定性度量表），"
                "或等对应的平台字体引擎（CoreText / FreeType）落地。"
            )
        self._gdi32: Any = loaded[0]
        self._user32: Any = loaded[1]

        self._screen_dc: int = 0
        self._mem_dc: int = 0
        self._default_font: int = 0

        self._faces: dict[_FaceKey, _Face] = {}
        self._families: set[str] | None = None
        self._measure_cache: dict[tuple[str, FontSpec], TextMetrics] = {}
        self._shape_cache: dict[tuple[str, FontSpec], GlyphRun] = {}
        self._mask_cache: dict[tuple[str, int, str, int], tuple[int, int, int, int, bytes]] = {}

        self._dib: int = 0
        self._dib_bits: int = 0
        self._dib_width: int = 0
        self._dib_height: int = 0

        self._open()

    # ------------------------------------------------------------ 生命周期

    def _open(self) -> None:
        self._screen_dc = self._user32.GetDC(None)
        if not self._screen_dc:
            raise FontMetricsError("GetDC 失败：拿不到屏幕 DC，无法创建字体")
        self._mem_dc = self._gdi32.CreateCompatibleDC(self._screen_dc)
        if not self._mem_dc:
            raise FontMetricsError("CreateCompatibleDC 失败，无法创建字体")
        # 选中一个默认字体，避免"DC 里没有字体时 GetTextMetrics 拿到垃圾值"
        self._default_font = self._gdi32.GetStockObject(_DEFAULT_GUI_FONT)
        self._gdi32.SelectObject(self._mem_dc, self._default_font)

    def close(self) -> None:
        """释放所有 GDI 对象。不调用会泄漏 DC 与字体句柄。"""
        self._release_dib()
        for face in self._faces.values():
            self._gdi32.DeleteObject(face.handle)
        self._faces.clear()
        if self._mem_dc:
            self._gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = 0
        if self._screen_dc:
            self._user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = 0

    def __del__(self) -> None:  # pragma: no cover - 兜底，正常路径应显式 close
        with contextlib.suppress(Exception):
            self.close()

    @property
    def glyph_provider(self) -> GdiFontEngine:
        """返回**自己**。

        度量与字形出自同一个对象，这就是 docs/04 §3「测量与绘制同源」
        在真字体路径上的实现方式——不是靠纪律维持一致，而是根本只有一份。
        上层（`devtools`）拿这个属性做自动配对，于是"排版用的字体"
        与"画字形用的字体"不可能分家。
        """
        return self

    # ------------------------------------------------------------ 字体发现

    @property
    def families(self) -> set[str]:
        """已安装的字体族名集合（只枚举一次，之后走缓存）。

        用 `EnumFontFamiliesExW` 真枚举，而不是"试着创建再看看"——
        后者拿不到可靠答案：字体不存在时 GDI 会**静默替换**成别的字体，
        于是"这个字体存在吗"永远返回"存在"。
        """
        if self._families is None:
            self._families = self._enumerate_families()
        return self._families

    def _enumerate_families(self) -> set[str]:
        gdi32 = self._gdi32
        names: set[str] = set()

        class _ENUMLOGFONTEXW(ctypes.Structure):
            _fields_ = [
                ("elfLogFont", _LOGFONTW),
                ("elfFullName", wintypes.WCHAR * 64),
                ("elfStyle", wintypes.WCHAR * 32),
                ("elfScript", wintypes.WCHAR * 32),
            ]

        _ENUMFONTPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.POINTER(_ENUMLOGFONTEXW),
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.LPARAM,
        )

        def _callback(logfont, _metric, _type, _param):  # type: ignore[no-untyped-def]
            names.add(logfont.contents.elfLogFont.lfFaceName)
            return 1

        callback = _ENUMFONTPROC(_callback)
        logfont = _LOGFONTW()
        logfont.lfCharSet = _DEFAULT_CHARSET
        logfont.lfFaceName = ""
        # 注意：这里会带上垂直书写（带 @ 前缀）的族名，过滤掉
        gdi32.EnumFontFamiliesExW.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(_LOGFONTW),
            _ENUMFONTPROC,
            wintypes.LPARAM,
            wintypes.DWORD,
        ]
        gdi32.EnumFontFamiliesExW.restype = ctypes.c_int
        gdi32.EnumFontFamiliesExW(self._mem_dc, ctypes.byref(logfont), callback, 0, 0)
        return {name for name in names if name and not name.startswith("@")}

    def has_family(self, family: str) -> bool:
        """系统里有没有这个族。通用名（`system-ui` 等）永远为真——
        它们是回退链的终点，映射到具体字体是 `resolve_font` 的事。"""
        if family in _GENERIC_FAMILIES:
            return True
        if family in self.families:
            return True
        # 大小写不敏感匹配：GDI 的族名大小写不总是和 CSS 里写的一致
        lowered = family.casefold()
        return any(name.casefold() == lowered for name in self.families)

    def _canonical(self, family: str) -> str:
        """把请求的族名映射成 GDI 里真实存在的族名（处理大小写与通用名）。"""
        if family in _GENERIC_FAMILIES:
            return _GENERIC_FAMILIES[family]
        if family in self.families:
            return family
        lowered = family.casefold()
        for name in self.families:
            if name.casefold() == lowered:
                return name
        return family

    def resolve_font(self, spec: FontSpec) -> FontFace:
        """从优先列表里挑第一个真装了的族。"""
        chosen: str | None = None
        for family in spec.families:
            if self.has_family(family):
                chosen = self._canonical(family)
                break
        if chosen is None:
            chosen = _GENERIC_FAMILIES["sans-serif"]
        return FontFace(
            resolved_family=chosen,
            size=spec.size,
            weight=spec.weight,
            slant=spec.slant,
        )

    # ------------------------------------------------------------ 字体句柄

    def _pixel_size(self, size: float) -> int:
        """逻辑字号 → 整数像素高度。至少 1，否则 GDI 会用默认大小。"""
        return max(1, round(size))

    def _face_for(self, family: str, size: float, weight: FontWeight, italic: bool) -> _Face:
        key = _FaceKey(
            self._canonical(family), self._pixel_size(size), _weight_value(weight), italic
        )
        hit = self._faces.get(key)
        if hit is not None:
            return hit

        logfont = _LOGFONTW()
        # 负值 = 字符高度（em 高度），正值 = 单元格高度。
        # 用负值才对应"字号 14 就是 14px"，正是我们要的语义。
        logfont.lfHeight = -key.pixel_size
        logfont.lfWeight = key.weight
        logfont.lfItalic = 1 if key.italic else 0
        logfont.lfCharSet = _DEFAULT_CHARSET
        logfont.lfOutPrecision = _OUT_DEFAULT_PRECIS
        logfont.lfClipPrecision = _CLIP_DEFAULT_PRECIS
        logfont.lfQuality = _ANTIALIASED_QUALITY
        logfont.lfPitchAndFamily = _DEFAULT_PITCH | _FF_DONTCARE
        logfont.lfFaceName = key.family

        handle = self._gdi32.CreateFontIndirectW(ctypes.byref(logfont))
        if not handle:
            raise FontMetricsError(f"创建字体失败：{key.family} @ {key.pixel_size}px")

        previous = self._gdi32.SelectObject(self._mem_dc, handle)
        metrics = _TEXTMETRICW()
        if not self._gdi32.GetTextMetricsW(self._mem_dc, ctypes.byref(metrics)):
            self._gdi32.SelectObject(self._mem_dc, previous)
            self._gdi32.DeleteObject(handle)
            raise FontMetricsError(f"读不到字体度量：{key.family} @ {key.pixel_size}px")
        # 恢复原字体，字体对象留在缓存里供后续 SelectObject 使用
        self._gdi32.SelectObject(self._mem_dc, previous)

        face = _Face(
            handle=int(handle),
            ascent=int(metrics.tmAscent),
            descent=int(metrics.tmDescent),
            external_leading=int(metrics.tmExternalLeading),
            pixel_size=key.pixel_size,
        )
        self._faces[key] = face
        return face

    # ------------------------------------------------------------ 度量

    def measure_text(self, text: str, spec: FontSpec) -> TextMetrics:
        """测量单行文本。宽度来自系统字体，与字形同一份 HFONT。"""
        key = (text, spec)
        hit = self._measure_cache.get(key)
        if hit is not None:
            return hit

        face = self._face_for(
            self._first_available(spec.families),
            spec.size,
            spec.weight,
            spec.slant is FontSlant.ITALIC,
        )
        previous = self._gdi32.SelectObject(self._mem_dc, face.handle)
        try:
            advances = self._advances(text, face)
        finally:
            self._gdi32.SelectObject(self._mem_dc, previous)

        metrics = TextMetrics(
            width=sum(advances),
            ascent=float(face.ascent),
            descent=float(face.descent),
            line_gap=float(face.external_leading),
            advance=tuple(advances),
        )
        self._measure_cache[key] = metrics
        return metrics

    def _first_available(self, families: tuple[str, ...]) -> str:
        for family in families:
            if self.has_family(family):
                return family
        return _GENERIC_FAMILIES["sans-serif"]

    def _advances(self, text: str, face: _Face) -> list[float]:
        """逐字素簇的推进宽度。

        两步：

            1. 每个簇用 `GetCharABCWidthsFloatW` 取亚像素宽度（A+B+C）；
            2. 用整串的 `GetTextExtentPoint32W` 结果**归一化**——
               按比例微调，使逐簇之和恰好等于整串精确宽度。

        第 2 步是关键：GDI 的逐字符 API 只有 1px 精度，40 个字符逐个累加
        最多能偏出一二十像素，中文行尾对齐一眼就能看出来。整串 API 是精确的，
        用它当"总量"约束，既保住亚像素的相对比例，又不会累积漂移。
        """
        clusters = grapheme_clusters(text)
        if not clusters:
            return []

        raw: list[float] = []
        total_chars = 0
        for start, end in clusters:
            cluster = text[start:end]
            width = 0.0
            for char in cluster:
                width += self._char_advance(char)
            raw.append(width)
            total_chars += len(cluster)

        exact = self._extent(text)
        raw_total = sum(raw)
        if raw_total > 0.0 and exact > 0.0:
            scale = exact / raw_total
            return [w * scale for w in raw]
        # 退化路径：逐簇 API 全失败时按整串宽度均分，至少保证"总和正确"
        if total_chars > 0 and exact > 0.0:
            return [exact / len(clusters)] * len(clusters)
        return raw

    def _char_advance(self, char: str) -> float:
        """单字符宽度（A+B+C）。失败时退到整串 API 的单字符调用。"""
        code = ord(char)
        if code > 0xFFFF:
            # 基本多文种平面之外（如 emoji）没有 ABC 宽度，用延伸量兜底
            return self._extent(char)
        abc = _ABC()
        if self._gdi32.GetCharABCWidthsFloatW(self._mem_dc, code, code, ctypes.byref(abc)):
            total = float(abc.abcA + abc.abcB + abc.abcC)
            if total > 0.0:
                return total
        return self._extent(char)

    def _extent(self, text: str) -> float:
        """整串精确宽度（整数像素，来自 GDI）。"""
        size = _SIZE()
        if not self._gdi32.GetTextExtentPoint32W(
            self._mem_dc, text, _utf16_len(text), ctypes.byref(size)
        ):
            return 0.0
        return float(size.cx)

    def shape_line(self, text: str, spec: FontSpec) -> GlyphRun:
        """整形一行：逐字素簇给出位置与字体归属。

        位置由**同一份度量**累加而来（`measure_text` 的 advance），
        所以后面光栅化取字形时，位置与字形必然对得上。
        """
        key = (text, spec)
        hit = self._shape_cache.get(key)
        if hit is not None:
            return hit

        family = self._first_available(spec.families)
        metrics = self.measure_text(text, spec)
        placements: list[GlyphPlacement] = []
        x = 0.0
        for (start, end), advance in zip(grapheme_clusters(text), metrics.advance, strict=True):
            placements.append(
                GlyphPlacement(
                    start=start,
                    end=end,
                    x=x,
                    y=0.0,
                    advance=advance,
                    family=family,
                )
            )
            x += advance

        run = GlyphRun(
            text=text,
            placements=tuple(placements),
            metrics=metrics,
            start=0,
            end=len(text),
        )
        self._shape_cache[key] = run
        return run

    # ------------------------------------------------------------ 字形

    def is_placeholder_only(self, text: str) -> bool:
        """真字体引擎渲染真字形，不存在"只有占位形状"的字符。"""
        return False

    def mask_for(self, text: str, size: float, family: str, advance: float) -> object:
        """把一个字符画进内存 DIB，读回灰度作为覆盖度掩码。

        为什么不用 `GetGlyphOutline`：那个 API 在字体链接、颜色 emoji、
        字体替换上边界情况很多，而且拿到的是**另一套**字形数据——
        与 GDI 自己的排版结果未必一致。直接画则天然一致：
        GDI 怎么画，我们就取什么像素。

        白字画在黑底上，所以**像素亮度就是覆盖度**，抗锯齿由 GDI 提供。
        """
        from ..gfx.glyphs import GlyphMask

        if not text or not text.strip():
            return GlyphMask(0, 0, 0, 0, b"")

        pixel_size = self._pixel_size(size)
        canonical = self._canonical(family)
        box_width = max(1, round(advance)) + 2 * self._MARGIN

        cache_key = (text, pixel_size, canonical, box_width)
        hit = self._mask_cache.get(cache_key)
        if hit is not None:
            width, height, left, top, data = hit
            return GlyphMask(width, height, left, top, data)

        face = self._face_for(canonical, size, FontWeight.REGULAR, False)
        height = face.ascent + face.descent + 2 * self._MARGIN
        if height <= 0 or box_width <= 0:
            return GlyphMask(0, 0, 0, 0, b"")

        coverage = self._draw_to_coverage(text, face, box_width, height)
        # 掩码相对 (笔位置, 基线) 的偏移：我们是从
        # (pen_x - MARGIN, baseline - ascent - MARGIN) 开始读的
        left = -self._MARGIN
        top = -(face.ascent + self._MARGIN)
        self._mask_cache[cache_key] = (box_width, height, left, top, coverage)
        return GlyphMask(box_width, height, left, top, coverage)

    def _ensure_dib(self, width: int, height: int) -> None:
        """准备一块足够大的 32bpp 自上而下 DIB。"""
        if self._dib and self._dib_width >= width and self._dib_height >= height:
            return
        self._release_dib()

        new_width = max(width, self._dib_width, 64)
        new_height = max(height, self._dib_height, 64)

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = new_width
        # 负高度 = 自上而下：第 0 行在内存最前面，读起来不用翻转
        info.bmiHeader.biHeight = -new_height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB
        info.bmiHeader.biSizeImage = new_width * new_height * 4

        bits = ctypes.c_void_p()
        dib = self._gdi32.CreateDIBSection(
            self._screen_dc, ctypes.byref(info), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not dib or not bits:
            raise FontMetricsError("创建 DIB 失败，无法光栅化字形")
        self._dib = int(dib)
        self._dib_bits = int(bits.value or 0)
        self._dib_width = new_width
        self._dib_height = new_height

    def _release_dib(self) -> None:
        if self._dib:
            self._gdi32.DeleteObject(self._dib)
            self._dib = 0
            self._dib_bits = 0
            self._dib_width = 0
            self._dib_height = 0

    def _draw_to_coverage(self, text: str, face: _Face, width: int, height: int) -> bytes:
        """把 text 以白字黑底画进 DIB，返回 width×height 的覆盖度字节。"""
        self._ensure_dib(width, height)
        dib = self._dib
        previous_bitmap = self._gdi32.SelectObject(self._mem_dc, dib)
        previous_font = self._gdi32.SelectObject(self._mem_dc, face.handle)

        try:
            # 黑底：PatBlt BLACKNESS — 一次调用填满，比逐像素快得多
            self._gdi32.PatBlt(self._mem_dc, 0, 0, width, height, _BLACKNESS)
            self._gdi32.SetBkMode(self._mem_dc, _TRANSPARENT)
            self._gdi32.SetTextColor(self._mem_dc, 0x00FFFFFF)
            self._gdi32.SetTextAlign(self._mem_dc, _TA_LEFT | _TA_BASELINE)

            baseline = self._MARGIN + face.ascent
            self._gdi32.TextOutW(self._mem_dc, self._MARGIN, baseline, text, _utf16_len(text))

            buffer = (ctypes.c_ubyte * (self._dib_width * self._dib_height * 4)).from_address(
                self._dib_bits
            )
            out = bytearray(width * height)
            stride = self._dib_width * 4
            for y in range(height):
                row_base = y * stride
                out_base = y * width
                for x in range(width):
                    offset = row_base + x * 4
                    # BGRA；白字黑底，任取一个通道都是覆盖度
                    blue = buffer[offset]
                    green = buffer[offset + 1]
                    red = buffer[offset + 2]
                    out[out_base + x] = max(blue, green, red)
            return bytes(out)
        finally:
            self._gdi32.SelectObject(self._mem_dc, previous_font)
            self._gdi32.SelectObject(self._mem_dc, previous_bitmap)

    # ------------------------------------------------------------ 缓存

    def stats(self) -> dict[str, int]:
        return {
            "faces": len(self._faces),
            "families": len(self.families),
            "measure_cached": len(self._measure_cache),
            "shape_cached": len(self._shape_cache),
            "mask_cached": len(self._mask_cache),
        }

    def clear_caches(self) -> None:
        """清度量与字形缓存（不动已创建的字体对象——重建字体很贵）。"""
        self._measure_cache.clear()
        self._shape_cache.clear()
        self._mask_cache.clear()


def gdi_font_engine() -> GdiFontEngine | None:
    """在可用时返回 Windows 字体引擎，否则返回 None。

    调用方（devtools / 未来的 App）用这个工厂做"有真字体就用真字体，
    没有就退回确定性表"的降级，而不是自己判断平台。
    """
    if sys.platform != "win32":
        return None
    try:
        return GdiFontEngine()
    except (FontMetricsError, OSError):
        return None
