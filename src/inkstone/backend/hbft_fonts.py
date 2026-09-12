"""HarfBuzz 整形 + FreeType 光栅化（L0b/c）—— 跨平台文本栈的核心。

这是 R4 的主体：把 `_spike_hbft.py` 验证过的路线扶正为产品实现。

## 为什么必须自己带整形与光栅化

docs/04 §7 的验收标准是"同一文本在三个平台的行数与行高一致"。
用 DirectWrite / CoreText / fontconfig 三套引擎**在数学上不可能达成**——
同一份字体文件过三套整形器，行宽就是不一样。Flutter / Chrome / Android
的答案一致：自带 HarfBuzz（整形）+ FreeType（光栅化）。

## 度量与字形必须是**同一个对象**

`HbFtFontEngine` 同时实现 `MetricsProvider` 与 `GlyphProvider`，
共用同一份 face 缓存（ADR-0009 的精神延续）。如果度量用一套、字形用另一套，
字距就会和字形对不上——排版按 14px 排、字形按 13.2px 画，中英混排立刻出现
"有的字挤在一起、有的字留缝"，而且**只在真机上才看得见**。
把两者做成同一个对象之后，"对不上"在结构上不可能发生。

## 小数字号不许取整

字号走 26.6 定点（`size * 64`）：17.5px 就是 1120 个单位，**不是** 17 或 18。
GDI 那条路的 `lfHeight` 只有整数，125% DPI 下必然丢 0.5px；
HarfBuzz 与 FreeType 都吃定点数，所以这里一步都不许 round 到整像素。

代价要说清楚：advance 因此被**量化到 1/64 像素**（0.0156px）。
600/1000 em × 17px 精确值是 10.2，实际落在 10.203125。这个量级对排版
完全够用，但写测试的人要知道它不是"数值不稳"，而是定点数的分辨率。

## 连字的簇映射有个取舍

HarfBuzz 的 cluster 是**源串下标**，而度量要按**字素簇**给（`TextMetrics.advance`
的约定是"每个字素簇一个宽度"）。绝大多数情况两者一一对应，但连字会打破它：
"ffi" 三个字素簇只出一个字形、一个 cluster。本实现的取舍是**把那个字形的
advance 按簇内字素簇个数均分**——这样 `width == sum(advance)` 仍然成立，
命中测试与光标位置也落在连字的中间（浏览器的行为），代价是连字内部的
字形位置不再是"真实推进"（它本来也没有单独的字形）。

状态：已实现（R4.2）。`masks_for_run`（按 glyph id 取掩码，彻底绕开重整形）
属 R4.3。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .fontfiles import DEFAULT_FALLBACK_FAMILY, FontLibrary, FontRecord, default_font_library
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

if TYPE_CHECKING:  # 只为类型检查；运行期是延迟导入（见 mask_for）
    from ..gfx.glyphs import GlyphMask

__all__ = ["HbFtFontEngine", "hbft_font_engine"]

#: 26.6 定点的小数位数：HarfBuzz 与 FreeType 的位置单位都是 1/64 像素
_POSITION_SCALE = 64.0

#: 探测字符覆盖时用的字号。cmap 与字号无关，随便取一个只为能开 face。
_COVERAGE_PROBE_SIZE = 12.0


def _coverage_sample(text: str) -> str:
    """用来判断"这个族能不能承载这段文本"的样本字符。

    取**第一个非空白字符**。前提是"一个 run 的脚本是齐的"——文本层按脚本
    切分（`split_by_script`），所以一个 run 里不会既有拉丁又有汉字。

    为什么不逐字符全查：没有哪个字体能覆盖所有字符（emoji 就不行），
    逐字符要求"全都能画"会让每个族都被否掉、回退链直接失效。
    也不逐字符要求"有一个能画"——那等于没查。
    """
    for char in text:
        if not char.isspace():
            return char
    return ""


@dataclass(slots=True)
class _Face:
    """一个字体面 + 一个字号对应的 HarfBuzz / FreeType 对象对。

    两者**共用同一份字体数据**：`hb.Face` 吃 bytes，`freetype.Face` 按路径加载，
    但指向的是同一个文件、同一个子面。这是"度量与字形同源"的物理基础——
    不是靠纪律维持一致，而是根本只有一份来源。
    """

    record: FontRecord
    # uharfbuzz / freetype-py 都**没有 py.typed**，mypy 只能把它们当 Any。
    # 把互操作点集中在这两个字段上标 Any，好过在十几处散落 type: ignore——
    # 上游哪天带上类型存根，改这一处就够了。
    ft: Any
    hb: Any
    ascent: float
    descent: float
    line_gap: float


class HbFtFontEngine:
    """HarfBuzz 整形 + FreeType 光栅化。同时是 `MetricsProvider` 与 `GlyphProvider`。

    构造是**廉价**的：不扫字体目录、不加载任何字体。索引与 face 都按需建立，
    所以"创建引擎"这个动作本身不会有启动开销（无头测试里经常创建了却不用）。
    """

    def __init__(self, library: FontLibrary | None = None) -> None:
        self._library = library
        self._data: dict[str, bytes] = {}
        self._faces: dict[tuple[str, int, int], _Face] = {}
        self._shape_cache: dict[tuple[str, FontSpec], GlyphRun] = {}
        # 键的「文本位」可能是 str（按文本取）或 tuple[int, ...]（按字形 id 取）：
        # 两条路径的结果不能互相顶掉（同一个字形 id 序列与某段文本可能不同）
        self._mask_cache: dict[tuple[object, int, str], GlyphMask] = {}
        # 覆盖探测的缓存：(族, 基字符) → 能不能画。
        # 排版每行都要问几次，而 cmap 查询虽快也不该重复做
        self._glyph_coverage: dict[tuple[str, str], bool] = {}

    # ------------------------------------------------------------ 字体发现

    @property
    def library(self) -> FontLibrary:
        """字体索引（延迟创建：构造引擎时不该去扫系统字体目录）。"""
        if self._library is None:
            self._library = default_font_library()
        return self._library

    def has_family(self, family: str) -> bool:
        return self.library.has_family(family)

    def resolve_font(self, spec: FontSpec) -> FontFace:
        record = self._library_or_fallback(spec)
        return FontFace(
            resolved_family=record.family,
            size=spec.size,
            weight=spec.weight,
            slant=spec.slant,
        )

    def has_glyph(self, family: str, char: str) -> bool:
        """这个族能不能画出这个字符（cmap 查询）。

        **这是回退链的关键能力**：没有它，"回退"只能按"族存不存在"判断，
        于是"这个族在、但画不出日文假名"这种情况会一路走到豆腐块。
        有了它，`text/fallback.py` 才能问"这个族覆盖这个脚本的字符吗"。

        只看**基字符**（簇的第一个码点）：组合符跟随基字符，
        而 emoji 的零宽拼接序列没有任何单一字体会完整覆盖——
        按整簇判断会让回退链把所有候选族全部否掉。
        """
        if not char:
            return False
        base = char[0]
        cached = self._glyph_coverage.get((family, base))
        if cached is not None:
            return cached
        record = self._find(family)
        if record is None:
            self._glyph_coverage[(family, base)] = False
            return False
        face = self._face_for(record, _COVERAGE_PROBE_SIZE)
        covered = bool(face.ft.get_char_index(ord(base)))
        self._glyph_coverage[(family, base)] = covered
        return covered

    # ------------------------------------------------------------ 度量

    def measure_text(self, text: str, spec: FontSpec) -> TextMetrics:
        return self.shape_line(text, spec).metrics

    def shape_line(self, text: str, spec: FontSpec) -> GlyphRun:
        """整行过 HarfBuzz，产出**逐字素簇**的位置与度量。

        空串返回空 run（零宽度、零簇）——这是文档化的空结果，
        不是静默失败：调用方拿到的是一个合法的"什么都没有"。
        """
        key = (text, spec)
        hit = self._shape_cache.get(key)
        if hit is not None:
            return hit

        record = self._library_or_fallback(spec, text)
        if not text:
            empty = TextMetrics(width=0.0, ascent=0.0, descent=0.0, advance=())
            run = GlyphRun(text=text, placements=(), metrics=empty, start=0, end=0)
            self._shape_cache[key] = run
            return run

        face = self._face_for(record, spec.size)
        infos, positions = self._shape(text, face)

        clusters = grapheme_clusters(text)
        # 每个 hb 字形覆盖的字素簇区间 = [g(cluster_i), g(cluster_{i+1}))
        #
        # **必须看下一个字形的簇才能定右界**：连字（"ffi" 三字一形）的
        # cluster 是 0，而后面两个字素簇的 start 是 1、2 —— 只看"等于同一个
        # cluster"会把跨度算成 1，于是整个 advance 全落在第一个字素簇上。
        # （R4.2 初版就是这么错的，靠合成字体的连字测试抓出来。）
        advances = [0.0] * len(clusters)
        y_offsets = [0.0] * len(clusters)
        ids: list[list[int]] = [[] for _ in clusters]
        for index, (info, pos) in enumerate(zip(infos, positions, strict=True)):
            first = _grapheme_index(clusters, info.cluster)
            if first < 0:
                continue
            last = (
                _grapheme_index(clusters, infos[index + 1].cluster)
                if index + 1 < len(infos)
                else len(clusters)
            )
            if last <= first:
                # 与下一个字形共享同一个字素簇（基字符 + 组合符）：
                # 跨度是空的，但它的 advance 不能丢，记在所属字素簇上
                last = first + 1
            count = last - first
            advance = pos.x_advance / _POSITION_SCALE
            for target in range(first, last):
                # 连字的 advance 均分给簇内每个字素簇（见模块顶部的说明）
                advances[target] += advance / count
                y_offsets[target] = pos.y_offset / _POSITION_SCALE
            # 字形 id 只记在**第一个**被覆盖的簇上：连字只有一个字形，
            # 记三份会让光栅层把它画三遍
            ids[first].append(info.codepoint)

        placements: list[GlyphPlacement] = []
        x = 0.0
        for (start, end), advance, y_offset, glyph_ids in zip(
            clusters, advances, y_offsets, ids, strict=True
        ):
            placements.append(
                GlyphPlacement(
                    start=start,
                    end=end,
                    x=x,
                    y=y_offset,
                    advance=advance,
                    family=record.family,
                    glyph_ids=tuple(glyph_ids),
                )
            )
            x += advance

        metrics = TextMetrics(
            # width 取**累加值**而不是 hb 的总推进：这样 `width == sum(advance)`
            # 这条不变量永远成立，连字均分带来的浮点零头不会让两者对不上
            width=x,
            ascent=face.ascent,
            descent=face.descent,
            line_gap=face.line_gap,
            advance=tuple(advances),
        )
        run = GlyphRun(
            text=text, placements=tuple(placements), metrics=metrics, start=0, end=len(text)
        )
        self._shape_cache[key] = run
        return run

    # ------------------------------------------------------------ 字形

    @property
    def glyph_provider(self) -> HbFtFontEngine:
        """字形来源就是本对象自己——度量与字形同源（ADR-0009）。"""
        return self

    def is_placeholder_only(self, text: str) -> bool:
        """真字体栈下永远有真字形，所以恒为 False（占位块是内置后端的特征）。"""
        del text
        return False

    def mask_for(
        self,
        text: str,
        size: float,
        family: str,
        advance: float,
        glyph_ids: tuple[int, ...] = (),
    ) -> GlyphMask:
        """把一个簇的字形用 FreeType 光栅化成灰度覆盖度。

        两条取字形路径：

        - **给了 `glyph_ids`（且只有一个）→ 直接按 id 光栅化**。这是连字的唯一
          正解：连字在显示列表里被切成多个字素簇，按文本逐簇取掩码会拿到
          分开的 f/f/i，而排版算的是连字宽度——两者对不上。
        - 否则**按文本重新整形**。这条路对**组合符**是对的（GPOS 会把标记摆到
          基字符上），而 id 路径做不到：显示列表只带簇级的 x/y 偏移，
          不带簇内逐字形的偏移。

        所以调用方的规则是"**单字形簇走 id、多字形簇走文本**"——
        两条路各自覆盖自己擅长的情形，见 `software.py` 的 `_text`。

        一个簇可能有多个字形，这里**先整形再合成**：把簇内所有字形的位图按
        各自偏移叠进一张掩码，取覆盖度最大值。分开画的话，重叠部分的 alpha
        会被叠加两次，笔画变粗。

        `advance` 参数在这里不参与计算（字形由字体决定），但**必须收下**：
        内置后端靠它把位图画进给定宽度，协议要求两个实现签名一致。
        """
        del advance
        from ..gfx.glyphs import GlyphMask  # 延迟导入：不让 backend 在模块级依赖 gfx

        if not text or not text.strip():
            return GlyphMask(0, 0, 0, 0, b"")
        record = self._find(family)
        if record is None:
            return GlyphMask(0, 0, 0, 0, b"")

        key: tuple[object, int, str] = (
            glyph_ids if glyph_ids else text,
            round(size * _POSITION_SCALE),
            record.family,
        )
        hit = self._mask_cache.get(key)
        if hit is not None:
            return hit

        face = self._face_for(record, size)
        infos, positions = (
            self._shape(text, face) if not glyph_ids else self._shape_glyph_ids(glyph_ids, face)
        )
        boxes: list[tuple[float, float, Any]] = []
        min_left = max_right = 0.0
        min_top = max_bottom = 0
        pen_x = 0.0
        for info, pos in zip(infos, positions, strict=True):
            bitmap = self._render_glyph(face, info.codepoint)
            if bitmap is None:
                pen_x += pos.x_advance / _POSITION_SCALE
                continue
            bmp, bitmap_left, bitmap_top = bitmap
            left = pen_x + pos.x_offset / _POSITION_SCALE + bitmap_left
            # 掩码坐标向下为正，HarfBuzz 的 y_offset 向上为正 → 取负
            top = -(pos.y_offset / _POSITION_SCALE + bitmap_top)
            boxes.append((left, top, bmp))
            min_left = min(min_left, left)
            max_right = max(max_right, left + bmp.width)
            min_top = min(min_top, top)
            max_bottom = max(max_bottom, top + bmp.rows)
            pen_x += pos.x_advance / _POSITION_SCALE

        if not boxes:
            out = GlyphMask(0, 0, 0, 0, b"")
            self._mask_cache[key] = out
            return out

        left0 = int(min_left) - 1
        top0 = int(min_top) - 1
        width = int(max_right) - left0 + 1
        height = int(max_bottom) - top0 + 1
        coverage = bytearray(width * height)
        for box_left, box_top, bmp in boxes:
            origin_x = int(box_left) - left0
            origin_y = int(box_top) - top0
            data = bytes(bmp.buffer)
            for y in range(bmp.rows):
                source = y * bmp.pitch
                target = (origin_y + y) * width + origin_x
                for x in range(bmp.width):
                    value = data[source + x]
                    if value > coverage[target + x]:
                        coverage[target + x] = value

        out = GlyphMask(width, height, left0, top0, bytes(coverage))
        self._mask_cache[key] = out
        return out

    # ------------------------------------------------------------ 诊断

    def stats(self) -> dict[str, int]:
        return {
            "faces": len(self._faces),
            "fonts_loaded": len(self._data),
            "shape_cached": len(self._shape_cache),
            "mask_cached": len(self._mask_cache),
        }

    def clear_caches(self) -> None:
        """丢掉所有缓存。检查器强制重算、或换了字体文件时用。"""
        self._faces.clear()
        self._data.clear()
        self._shape_cache.clear()
        self._mask_cache.clear()
        self._glyph_coverage.clear()

    # ------------------------------------------------------------ 内部

    def _find(self, family: str) -> FontRecord | None:
        return self.library.find(family, weight=FontWeight.REGULAR, slant=FontSlant.NORMAL)

    def _library_or_fallback(self, spec: FontSpec, text: str = "") -> FontRecord:
        """按回退链挑字体；链上全无命中时用内嵌兜底字体。

        **覆盖探测也在这里做**（R4.4 补的，是一个真 bug 的修法）：
        回退链的终点是通用名（`sans-serif`），而通用名会展开成平台默认族
        （Windows 上是 `Segoe UI`）——**它存在，但一个汉字都没有**。
        只按"族存在"挑的话，中文 run 会落到它头上、渲染成一排豆腐块，
        而这恰恰是内嵌兜底字体要防的事。文本层那条按脚本的覆盖过滤管不到
        通用名端点（它总是被保留），所以这道判断必须在**知道文本的地方**做。

        内嵌兜底字体也缺席时抛 `FontMetricsError`——**响亮失败**。
        静默挑一个画不出字的字体，表现是"中文变方块"；报错能让人一眼看出
        是环境没准备好。
        """
        library = self.library
        sample = _coverage_sample(text)
        for family in spec.families:
            hit = library.find(family, weight=spec.weight, slant=spec.slant)
            if hit is not None and (not sample or self.has_glyph(hit.family, sample)):
                return hit
        if library._fallback is not None:
            return library._fallback
        raise FontMetricsError(
            f"回退链里一个族都没找到：{list(spec.families)}；"
            f"内嵌兜底字体（{DEFAULT_FALLBACK_FAMILY}）也还没注册。"
            f"请确认系统字体可用，或先 register_file(..., is_fallback=True)"
        )

    def _face_for(self, record: FontRecord, size: float) -> _Face:
        import freetype
        import uharfbuzz as hb

        size64 = round(size * _POSITION_SCALE)
        key = (record.path, record.index, size64)
        hit = self._faces.get(key)
        if hit is not None:
            return hit

        data = self._data.get(record.path)
        if data is None:
            data = _read_font_data(record.path)
            self._data[record.path] = data

        ft_face = freetype.Face(record.path, index=record.index)
        # 小数字号：26.6 定点，一步都不许 round 到整像素（见模块顶部）
        ft_face.set_char_size(size64)
        hb_font = hb.Font(hb.Face(data, record.index))
        hb_font.scale = (size64, size64)
        hb.ot_font_set_funcs(hb_font)

        hit = _Face(
            record=record,
            ft=ft_face,
            hb=hb_font,
            ascent=ft_face.size.ascender / _POSITION_SCALE,
            descent=-ft_face.size.descender / _POSITION_SCALE,
            line_gap=ft_face.size.height / _POSITION_SCALE
            - ft_face.size.ascender / _POSITION_SCALE
            + ft_face.size.descender / _POSITION_SCALE,
        )
        self._faces[key] = hit
        return hit

    def _shape(self, text: str, face: _Face) -> tuple[list[Any], list[Any]]:
        """过 HarfBuzz。返回 `(glyph_infos, glyph_positions)`。"""
        import uharfbuzz as hb

        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(face.hb, buffer, {})
        return list(buffer.glyph_infos), list(buffer.glyph_positions)

    def _shape_glyph_ids(
        self, glyph_ids: tuple[int, ...], face: _Face
    ) -> tuple[list[Any], list[Any]]:
        """把已知的字形 id 包成"整形结果"的形状（位置全零）。

        位置为零是**正确的**：显示列表只带簇级的 x/y 偏移，簇内逐字形的偏移
        不在里面。而这条路径只用于**单字形簇**（见 `mask_for` 的说明），
        单字形簇本来就没有簇内偏移可言。
        """
        return (
            [_GlyphInfo(glyph_id=glyph_id, cluster=0) for glyph_id in glyph_ids],
            [_GlyphPosition() for _ in glyph_ids],
        )

    def _render_glyph(self, face: _Face, glyph_id: int) -> tuple[Any, int, int] | None:
        """把一个字形光栅化成灰度位图。没有轮廓（如空格）返回 `None`。"""
        import freetype

        face.ft.load_glyph(glyph_id, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
        slot = face.ft.glyph
        bitmap = slot.bitmap
        if not bitmap.width or not bitmap.rows:
            return None
        return bitmap, slot.bitmap_left, slot.bitmap_top


@dataclass(frozen=True, slots=True)
class _GlyphInfo:
    """`hb.GlyphInfo` 的最小替身：只要 `codepoint` 与 `cluster`。"""

    glyph_id: int
    cluster: int = 0

    @property
    def codepoint(self) -> int:
        """HarfBuzz 把它叫 codepoint，其实是字形 id（历史命名）。"""
        return self.glyph_id


@dataclass(frozen=True, slots=True)
class _GlyphPosition:
    """`hb.GlyphPosition` 的最小替身：位置全零（簇内偏移不在显示列表里）。"""

    x_offset: int = 0
    y_offset: int = 0
    x_advance: int = 0
    y_advance: int = 0


def _read_font_data(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise FontMetricsError(f"字体文件读不了：{path}（{exc}）") from exc


def _grapheme_index(clusters: list[tuple[int, int]], cluster: int) -> int:
    """HarfBuzz 的 cluster（源串下标）落在第几个字素簇里；不在任何簇里返回 -1。

    理论上不会越界，但字体里的畸形 cmap 能让 cluster 指向源串之外——
    那种时候宁可少画一个字形，也不要 IndexError 把整行排版带崩。
    """
    for index, (start, end) in enumerate(clusters):
        if start <= cluster < end:
            return index
    return -1


_ENGINE: HbFtFontEngine | None = None


def hbft_font_engine() -> HbFtFontEngine:
    """进程级的默认 HB+FT 引擎（与 `default_font_library` 配套）。

    为什么单例：face 缓存与整形缓存都挂在引擎上，每次新建一个引擎等于
    每次重新加载字体、重新整形——排版会从毫秒级掉到秒级。
    要隔离（测试、多套字体配置）就自己 `HbFtFontEngine(FontLibrary(...))`。
    """
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = HbFtFontEngine()
    return _ENGINE
