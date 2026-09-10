"""字形提供方 —— 把"文本"变成"像素覆盖"，并把这件事的**来源**做成可替换的。

为什么需要这一层（而不是让光栅器自己画字）：

    光栅器认识的是"覆盖度"，不认识"字形"。真字形来自字体文件
    （FreeType / DirectWrite / CoreText），而字体文件不属于渲染层。
    加一个 `GlyphProvider` 协议，就把"谁提供字形"和"谁合成像素"分开了。

    Phase 1 的现状与后续：

    - `BuiltinGlyphProvider`（本模块，默认）：**无字体文件**的确定性实现。
      ASCII 用内置位图字形（真的可读），非拉丁字符按度量宽度画占位块。
      它是**验证后端**——服务于黄金图与无头 CI，要求的是"逐比特确定"
      与"布局可验证"，不是字形美观。
    - 平台字形后端（Phase 1 item 2 的 GL 后端 / Phase 2）：走系统字体，
      提供真字形。届时替换 provider 即可，光栅器与组件一行不改。

关于"度量同源"（docs/04 §3）：
    **字形宽度永远来自 `PositionedGlyph.advance`**，那是文本层用后端度量
    算出来的。本模块只负责"在给定的宽度里画个形状"，**绝不自己算宽度**。
    所以即使占位字形不精确，排版几何也不会漂移。

关于确定性（铁律 3）：
    位图字形是纯数据查表，占位块是纯几何计算，不采样随机数、不读时钟。
    同一输入在任何机器上得到逐比特相同的像素。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..layout.types import Rect
from .color import Color

__all__ = [
    "BuiltinGlyphProvider",
    "GlyphMask",
    "GlyphProvider",
]


@dataclass(frozen=True, slots=True)
class GlyphMask:
    """一个字形在**设备像素**上的覆盖度掩码。

    `coverage` 长度是 `width * height`，每字节 0–255（0=全透，255=全实）。
    `left` / `top` 是掩码左上角相对"文本原点（基线 + 笔位置）"的偏移，
    由 provider 填好——光栅器不需要知道字形的内部结构。

    为什么用 8 位覆盖度而不是 1 位位图：文本最常见的观感问题是锯齿，
    而覆盖度是免费拿到抗锯齿的办法（位图放大时做盒式滤波即可）。
    """

    width: int
    height: int
    left: int
    top: int
    coverage: bytes

    def at(self, x: int, y: int) -> int:
        """掩码内 (x, y) 的覆盖度；越界返回 0。"""
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return 0
        return self.coverage[y * self.width + x]

    def split_rows(self) -> list[bytes]:
        """按行切分，便于行式合成。"""
        return [self.coverage[y * self.width : (y + 1) * self.width] for y in range(self.height)]


class GlyphProvider(Protocol):
    """字形来源。光栅器只依赖这个协议，不依赖任何字体实现。"""

    def mask_for(self, text: str, size: float, family: str, advance: float) -> GlyphMask:
        """给出某字符的覆盖度掩码。

        `advance` 是文本层分配给这个字形的**水平空间**——这是本方法
        最关键的一个参数，也是"度量同源"的体现：

            字形必须画进 advance 给定的宽度里。如果 provider 按"字号"
            自由决定字形宽度，就可能画出比 advance 更宽的字形，
            相邻字形直接叠在一起（小字号下尤其明显）。

        所以 provider 的职责是"在给定宽度内画一个像样的形状"，
        而**不是**重新决定这个字形该多宽——宽度是文本层的事。

        实现必须**可缓存且确定性**：同一组参数永远同一结果。
        """

    def is_placeholder_only(self, text: str) -> bool:
        """该字符是否只有占位字形（没有真实字形）。

        检查器据此提示"这段文字在你的后端上只能看到占位形状"，
        避免用户以为渲染坏了。ASCII 返回 False，其余默认 True。
        """


# ------------------------------------------------------------------ 内置位图字体
#
# 5×7 点阵，按 ASCII 32..126 顺序排列。每个字形 5 列 × 7 行，
# 用 5 个字节表示（每字节 7 位，bit0 是最上面一行）。
#
# 为什么手写点阵而不是用系统字体：这是**验证后端**，要的是"任何机器
# 逐比特一致"。5×7 是 UI 里最常见的点阵规格，足以验证字距、行高、
# 截断、对齐是否算对，同时把数据量控制在可审阅的范围。
_FONT_HEIGHT = 7
_FONT_WIDTH = 5

# 字形表：每个字符 5 个字节，bit i 对应第 i 行（自上而下）。
# 只收录 ASCII 32–126；未收录的字符按占位块处理。
_GLYPHS: dict[str, tuple[int, int, int, int, int]] = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x00, 0x00, 0x5F, 0x00, 0x00),
    '"': (0x00, 0x07, 0x00, 0x07, 0x00),
    "#": (0x14, 0x7F, 0x14, 0x7F, 0x14),
    "$": (0x24, 0x2A, 0x7F, 0x2A, 0x12),
    "%": (0x23, 0x13, 0x08, 0x64, 0x62),
    "&": (0x36, 0x49, 0x55, 0x22, 0x50),
    "'": (0x00, 0x05, 0x03, 0x00, 0x00),
    "(": (0x00, 0x1C, 0x22, 0x41, 0x00),
    ")": (0x00, 0x41, 0x22, 0x1C, 0x00),
    "*": (0x14, 0x08, 0x3E, 0x08, 0x14),
    "+": (0x08, 0x08, 0x3E, 0x08, 0x08),
    ",": (0x00, 0x50, 0x30, 0x00, 0x00),
    "-": (0x08, 0x08, 0x08, 0x08, 0x08),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00),
    "/": (0x20, 0x10, 0x08, 0x04, 0x02),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E),
    "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46),
    "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10),
    "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30),
    "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36),
    "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    ":": (0x00, 0x36, 0x36, 0x00, 0x00),
    ";": (0x00, 0x56, 0x36, 0x00, 0x00),
    "<": (0x08, 0x14, 0x22, 0x41, 0x00),
    "=": (0x14, 0x14, 0x14, 0x14, 0x14),
    ">": (0x00, 0x41, 0x22, 0x14, 0x08),
    "?": (0x02, 0x01, 0x51, 0x09, 0x06),
    "@": (0x32, 0x49, 0x79, 0x41, 0x3E),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E),
    "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22),
    "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41),
    "F": (0x7F, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A),
    "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0x00, 0x41, 0x7F, 0x41, 0x00),
    "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41),
    "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F),
    "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E),
    "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E),
    "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31),
    "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F),
    "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x7F, 0x20, 0x18, 0x20, 0x7F),
    "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x03, 0x04, 0x78, 0x04, 0x03),
    "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
    "[": (0x00, 0x7F, 0x41, 0x41, 0x00),
    "\\": (0x02, 0x04, 0x08, 0x10, 0x20),
    "]": (0x00, 0x41, 0x41, 0x7F, 0x00),
    "^": (0x04, 0x02, 0x01, 0x02, 0x04),
    "_": (0x40, 0x40, 0x40, 0x40, 0x40),
    "`": (0x00, 0x01, 0x02, 0x04, 0x00),
    "a": (0x20, 0x54, 0x54, 0x54, 0x78),
    "b": (0x7F, 0x48, 0x44, 0x44, 0x38),
    "c": (0x38, 0x44, 0x44, 0x44, 0x20),
    "d": (0x38, 0x44, 0x44, 0x48, 0x7F),
    "e": (0x38, 0x54, 0x54, 0x54, 0x18),
    "f": (0x08, 0x7E, 0x09, 0x01, 0x02),
    "g": (0x0C, 0x52, 0x52, 0x52, 0x3E),
    "h": (0x7F, 0x08, 0x04, 0x04, 0x78),
    "i": (0x00, 0x44, 0x7D, 0x40, 0x00),
    "j": (0x20, 0x40, 0x44, 0x3D, 0x00),
    "k": (0x7F, 0x10, 0x28, 0x44, 0x00),
    "l": (0x00, 0x41, 0x7F, 0x40, 0x00),
    "m": (0x7C, 0x04, 0x18, 0x04, 0x78),
    "n": (0x7C, 0x08, 0x04, 0x04, 0x78),
    "o": (0x38, 0x44, 0x44, 0x44, 0x38),
    "p": (0x7C, 0x14, 0x14, 0x14, 0x08),
    "q": (0x08, 0x14, 0x14, 0x18, 0x7C),
    "r": (0x7C, 0x08, 0x04, 0x04, 0x08),
    "s": (0x48, 0x54, 0x54, 0x54, 0x20),
    "t": (0x04, 0x3F, 0x44, 0x40, 0x20),
    "u": (0x3C, 0x40, 0x40, 0x20, 0x7C),
    "v": (0x1C, 0x20, 0x40, 0x20, 0x1C),
    "w": (0x3C, 0x40, 0x30, 0x40, 0x3C),
    "x": (0x44, 0x28, 0x10, 0x28, 0x44),
    "y": (0x0C, 0x50, 0x50, 0x50, 0x3C),
    "z": (0x44, 0x64, 0x54, 0x4C, 0x44),
    "{": (0x00, 0x08, 0x36, 0x41, 0x00),
    "|": (0x00, 0x00, 0x7F, 0x00, 0x00),
    "}": (0x00, 0x41, 0x36, 0x08, 0x00),
    "~": (0x10, 0x08, 0x08, 0x10, 0x08),
}


def _bitmap_rows(char: str) -> list[list[int]] | None:
    """取出字符的 7×5 布尔位图；未收录返回 None。"""
    glyph = _GLYPHS.get(char)
    if glyph is None:
        return None
    rows: list[list[int]] = []
    for y in range(_FONT_HEIGHT):
        rows.append([1 if (glyph[x] >> y) & 1 else 0 for x in range(_FONT_WIDTH)])
    return rows


def _box_upscale(rows: list[list[int]], target_w: int, target_h: int) -> bytes:
    """把 7×5 位图放大到目标尺寸，用**盒式滤波**得到灰度覆盖度。

    盒式（面积平均）而不是最近邻：最近邻在非整数倍放大时笔画粗细不均，
    看起来像"有的笔画粗有的细"。面积平均能给出平滑的边缘，
    且完全是确定性的算术运算（无采样、无随机）。
    """
    src_h = len(rows)
    src_w = len(rows[0]) if rows else 0
    if src_h == 0 or src_w == 0 or target_w <= 0 or target_h <= 0:
        return b""

    out = bytearray(target_w * target_h)
    for ty in range(target_h):
        # 该目标行覆盖的源行区间
        sy0 = ty * src_h / target_h
        sy1 = (ty + 1) * src_h / target_h
        for tx in range(target_w):
            sx0 = tx * src_w / target_w
            sx1 = (tx + 1) * src_w / target_w
            area = 0.0
            for sy in range(int(sy0), min(int(sy1) + 1, src_h) or 1):
                if sy >= src_h:
                    break
                y_overlap = min(sy1, sy + 1) - max(sy0, sy)
                if y_overlap <= 0:
                    continue
                for sx in range(int(sx0), min(int(sx1) + 1, src_w) or 1):
                    if sx >= src_w:
                        break
                    x_overlap = min(sx1, sx + 1) - max(sx0, sx)
                    if x_overlap <= 0 or not rows[sy][sx]:
                        continue
                    area += x_overlap * y_overlap
            out[ty * target_w + tx] = min(
                255, int(area / ((sx1 - sx0) * (sy1 - sy0)) * 255.0 + 0.5)
            )
    return bytes(out)


class BuiltinGlyphProvider:
    """内置字形提供方：ASCII 真位图 + 非拉丁占位块。**确定性，零字体文件。**

    这是**验证后端**，服务于黄金图与无头 CI。它保证：

    - 同一输入在任何平台产出逐比特相同的像素；
    - ASCII 文本真实可读，方便人眼核对字距/对齐/截断；
    - 非拉丁字符按其**度量宽度**画占位块——所以即使看不见字形，
      也能从块的排布验证换行、禁则、对齐全部算对了。

    真字形由平台后端提供（Phase 1 item 2 的 GL 后端）。这个类的存在
    让那条路可以后补，而上层代码一行不改。
    """

    def __init__(self, *, block_radius: float = 1.0) -> None:
        self._block_radius = block_radius
        self._cache: dict[tuple[str, int, int], bytes] = {}

    def is_placeholder_only(self, text: str) -> bool:
        return not all(ch in _GLYPHS for ch in text)

    def mask_for(self, text: str, size: float, family: str, advance: float) -> GlyphMask:
        """给出一个字符的覆盖度掩码。

        **字形画进 `advance` 给定的宽度里**（见协议说明）——宽度由文本层
        决定，provider 只负责在里头放一个像样的形状。

        ASCII 的尺寸推导（关键）：

            5×7 点阵的宽高比是 5:7 ≈ 0.714，而 Latin 的 advance 恰是
            0.5em、常见 cap height 约 0.7em（比值 0.714）——两者几乎一致。
            所以"宽度取 advance、高度取 advance × 7/5"既贴合度量，
            又几乎不产生形变。

        整数倍优先：当 `advance` 允许 2 倍或 3 倍整数放大时用整数倍，
        笔画最锋利；否则退回面积平均（仍在"宽度不超 advance"的前提下）。
        """
        if len(text) == 1 and text in _GLYPHS:
            return self._ascii_mask(text, advance)
        return self._block_mask(size, advance)

    def _ascii_mask(self, text: str, advance: float) -> GlyphMask:
        """把 5×7 点阵画进 `advance` 宽度内。"""
        # 目标宽度不超过 advance（允许 1px 的亚像素余量，避免临界抖动）
        target_w = max(_FONT_WIDTH, round(advance))
        # 高度按点阵固有比例推导，形变最小
        target_h = max(_FONT_HEIGHT, round(target_w * _FONT_HEIGHT / _FONT_WIDTH))

        # 若宽高都接近整数倍，直接用整数倍——笔画更锋利
        for scale in (3, 2, 1):
            if (
                abs(target_w - _FONT_WIDTH * scale) <= 1
                and abs(target_h - _FONT_HEIGHT * scale) <= 1
            ):
                target_w, target_h = _FONT_WIDTH * scale, _FONT_HEIGHT * scale
                break

        cache_key = (text, target_w, target_h)
        hit = self._cache.get(cache_key)
        if hit is None:
            rows = _bitmap_rows(text)
            assert rows is not None
            hit = _box_upscale(rows, target_w, target_h)
            self._cache[cache_key] = hit

        # 5×7 点阵第 0–5 行在基线上方，第 6 行是降部。
        # 字形顶相对基线 = -(6/7) × 字高，由点阵结构直接给出。
        top = -round(target_h * 6.0 / 7.0)
        return GlyphMask(target_w, target_h, 0, top, hit)

    def _block_mask(self, size: float, advance: float) -> GlyphMask:
        """非拉丁字符的占位块。

        宽度取 advance（全角时即 1em），高度取 0.8 倍字号——
        比字身略矮一点，看起来像"一个待填的汉字"。
        描边用低覆盖度、字身用高覆盖度：一眼能看出这是占位形状，
        而不是以为渲染出了故障（一个纯黑方块很容易被误读成豆腐块）。
        """
        w = max(1, round(advance))
        h = max(1, round(size * 0.8))
        key = ("\u2588", w, h)
        hit = self._cache.get(key)
        if hit is None:
            inset = 1 if w > 4 else 0
            coverage = bytearray(w * h)
            for y in range(h):
                for x in range(w):
                    if x < inset or y < inset or x >= w - inset or y >= h - inset:
                        coverage[y * w + x] = 40  # 淡边框，勾出字身范围
                    else:
                        coverage[y * w + x] = 210  # 字身
            hit = bytes(coverage)
            self._cache[key] = hit
        # 基线大致在字身底部下方：向上偏移 h，留 1 像素给下沉
        top = -(h - 1)
        return GlyphMask(w, h, 0, top, hit)

    def clear_cache(self) -> None:
        self._cache.clear()


def rect_of_mask(mask: GlyphMask, pen_x: float, baseline_y: float) -> Rect:
    """掩码在画布上的落位矩形（左上角 + 尺寸）。光栅器与检查器共用。"""
    return Rect(
        left=pen_x + mask.left,
        top=baseline_y + mask.top,
        width=float(mask.width),
        height=float(mask.height),
    )


# `Color` 仅用于类型注解的语义说明（掩码本身不带颜色，着色由光栅器做）
_ = Color
