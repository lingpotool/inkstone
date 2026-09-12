"""Spike：HarfBuzz + FreeType 字体引擎，插进现有架构渲染 hello 界面。

目的只有一个：用像素证明"自有文本栈"这条路可行——
真整形（kerning/连字）、真字形光栅化、度量与字形同源，全部来自同一字体文件。

用法：.venv/Scripts/python.exe _spike_hbft.py
产出：_hello_hbft.png（与 _hello_gdi.png 同构图对比）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "src")

import freetype  # noqa: E402
import uharfbuzz as hb  # noqa: E402
from fontTools.ttLib import TTCollection, TTFont  # noqa: E402

from inkstone.backend.fonts import (  # noqa: E402
    FontFace,
    FontSpec,
    GlyphRun,
    GlyphPlacement,
    TextMetrics,
)
from inkstone.backend.headless_fonts import grapheme_clusters  # noqa: E402
from inkstone.gfx.glyphs import GlyphMask  # noqa: E402

FONTS_DIR = r"C:\Windows\Fonts"

_GENERIC = {
    "system-ui": "Segoe UI",
    "sans-serif": "Segoe UI",
    "serif": "Times New Roman",
    "monospace": "Consolas",
    "mono": "Consolas",
}


def build_family_index() -> dict[str, tuple[str, int]]:
    """扫描系统字体目录：族名 → (文件路径, face 序号)。只读 name 表，秒级。"""
    index: dict[str, tuple[str, int]] = {}

    def record(font: TTFont, path: str, num: int) -> None:
        try:
            name = font["name"]
            family = name.getDebugName(16) or name.getDebugName(1)
        except Exception:
            return
        if family and family not in index and not family.startswith("@"):
            index[family] = (path, num)

    for fname in os.listdir(FONTS_DIR):
        path = os.path.join(FONTS_DIR, fname)
        lower = fname.lower()
        try:
            if lower.endswith((".ttc", ".otc")):
                coll = TTCollection(path, lazy=True)
                for i, font in enumerate(coll.fonts):
                    record(font, path, i)
            elif lower.endswith((".ttf", ".otf")):
                record(TTFont(path, lazy=True), path, 0)
        except Exception:
            continue
    return index


class _Face:
    """一个族 + 一个字号对应的 FreeType/HarfBuzz 对象对（度量与字形同源）。"""

    def __init__(self, path: str, index: int, data: bytes, size: float) -> None:
        self.ft = freetype.Face(path, index=index)
        self.ft.set_char_size(int(round(size * 64)))
        hb_face = hb.Face(data, index)
        self.hb = hb.Font(hb_face)
        size64 = int(round(size * 64))
        self.hb.scale = (size64, size64)
        hb.ot_font_set_funcs(self.hb)
        self.ascent = self.ft.size.ascender / 64.0
        self.descent = -self.ft.size.descender / 64.0
        self.line_gap = self.ft.size.height / 64.0 - self.ascent - self.descent


class HbFtEngine:
    """HarfBuzz 整形 + FreeType 光栅化。同时是 MetricsProvider 与 GlyphProvider。"""

    def __init__(self, family_index: dict[str, tuple[str, int]]) -> None:
        self._index = family_index
        self._data: dict[str, bytes] = {}
        self._faces: dict[tuple[str, int], _Face] = {}
        self._shape_cache: dict[tuple[str, FontSpec], GlyphRun] = {}
        self._mask_cache: dict[tuple[str, int, str], GlyphMask] = {}

    # ------------------------------------------------------------ 字体发现

    def _canonical(self, family: str) -> str:
        return _GENERIC.get(family, family)

    def has_family(self, family: str) -> bool:
        return family in _GENERIC or self._canonical(family) in self._index

    def resolve_font(self, spec: FontSpec) -> FontFace:
        for family in spec.families:
            if self.has_family(family):
                chosen = self._canonical(family)
                return FontFace(resolved_family=chosen, size=spec.size,
                                weight=spec.weight, slant=spec.slant)
        chosen = _GENERIC["sans-serif"]
        return FontFace(resolved_family=chosen, size=spec.size,
                        weight=spec.weight, slant=spec.slant)

    def _first_available(self, families: tuple[str, ...]) -> str:
        for family in families:
            if self.has_family(family):
                return self._canonical(family)
        return _GENERIC["sans-serif"]

    def _face_for(self, family: str, size: float) -> _Face:
        key = (family, int(round(size * 64)))
        hit = self._faces.get(key)
        if hit is None:
            path, index = self._index[family]
            data = self._data.get(path)
            if data is None:
                with open(path, "rb") as fh:
                    data = fh.read()
                self._data[path] = data
            hit = _Face(path, index, data, size)
            self._faces[key] = hit
        return hit

    # ------------------------------------------------------------ 整形

    def _hb_shape(self, text: str, family: str, size: float):
        """整串过 HarfBuzz：返回 (glyph 信息, 位置, face)。单位：像素。"""
        face = self._face_for(family, size)
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(face.hb, buf, {})
        infos = buf.glyph_infos
        positions = buf.glyph_positions
        return infos, positions, face

    def shape_line(self, text: str, spec: FontSpec) -> GlyphRun:
        key = (text, spec)
        hit = self._shape_cache.get(key)
        if hit is not None:
            return hit

        family = self._first_available(spec.families)
        infos, positions, face = self._hb_shape(text, family, spec.size)

        # 把 HarfBuzz 的簇映射回字素簇：同一字素簇内的 glyph 推进求和
        clusters = grapheme_clusters(text)
        advances = [0.0] * len(clusters)
        for info, pos in zip(infos, positions):
            ci = info.cluster
            for gi, (start, end) in enumerate(clusters):
                if start <= ci < end:
                    advances[gi] += pos.x_advance / 64.0
                    break

        placements: list[GlyphPlacement] = []
        x = 0.0
        for (start, end), advance in zip(clusters, advances):
            placements.append(GlyphPlacement(
                start=start, end=end, x=x, y=0.0, advance=advance, family=family,
            ))
            x += advance

        metrics = TextMetrics(
            width=x,
            ascent=face.ascent,
            descent=face.descent,
            line_gap=face.line_gap,
            advance=tuple(advances),
        )
        run = GlyphRun(text=text, placements=tuple(placements),
                       metrics=metrics, start=0, end=len(text))
        self._shape_cache[key] = run
        return run

    def measure_text(self, text: str, spec: FontSpec) -> TextMetrics:
        return self.shape_line(text, spec).metrics

    # ------------------------------------------------------------ 字形

    @property
    def glyph_provider(self) -> HbFtEngine:
        return self

    def is_placeholder_only(self, text: str) -> bool:
        return False

    def mask_for(self, text: str, size: float, family: str, advance: float) -> GlyphMask:
        """把一个簇的字形用 FreeType 光栅化成灰度覆盖度。"""
        if not text or not text.strip():
            return GlyphMask(0, 0, 0, 0, b"")

        canonical = self._canonical(family)
        key = (text, int(round(size * 64)), canonical)
        hit = self._mask_cache.get(key)
        if hit is not None:
            return hit

        infos, positions, face = self._hb_shape(text, canonical, size)

        # 第一遍：算合成边界（相对笔位置 + 基线）
        x_cursor = 0.0
        glyphs = []
        min_left, max_right = 0.0, 0.0
        min_bottom, max_top = 0, 0
        for info, pos in zip(infos, positions):
            ft_face = face.ft
            ft_face.load_glyph(
                info.codepoint,
                freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL,
            )
            slot = ft_face.glyph
            bmp = slot.bitmap
            gx = x_cursor + pos.x_offset / 64.0
            gy = -pos.y_offset / 64.0
            if bmp.width and bmp.rows:
                left = gx + slot.bitmap_left
                top = -(gy + slot.bitmap_top)  # 相对基线，向上为负
                glyphs.append((left, top, bmp))
                min_left = min(min_left, left)
                max_right = max(max_right, left + bmp.width)
                max_top = min(max_top, top)
                min_bottom = max(min_bottom, top + bmp.rows)
            x_cursor += pos.x_advance / 64.0

        if not glyphs:
            out = GlyphMask(0, 0, 0, 0, b"")
            self._mask_cache[key] = out
            return out

        left0 = int(min_left) - 1
        top0 = int(max_top) - 1
        width = int(max_right) - left0 + 1
        height = int(min_bottom) - top0 + 1
        buf = bytearray(width * height)
        for gleft, gtop, bmp in glyphs:
            ox = int(gleft) - left0
            oy = int(gtop) - top0
            data = bytes(bmp.buffer)
            for y in range(bmp.rows):
                src = y * bmp.pitch
                dst = (oy + y) * width + ox
                for x in range(bmp.width):
                    v = data[src + x]
                    if v > buf[dst + x]:
                        buf[dst + x] = v

        out = GlyphMask(width, height, left0, top0, bytes(buf))
        self._mask_cache[key] = out
        return out

    def stats(self) -> dict[str, int]:
        return {
            "faces": len(self._faces),
            "shape_cached": len(self._shape_cache),
            "mask_cached": len(self._mask_cache),
        }


def main() -> None:
    print("扫描系统字体…", flush=True)
    index = build_family_index()
    print(f"发现 {len(index)} 个字体族")
    for probe in ("Microsoft YaHei UI", "Segoe UI", "Noto Sans SC", "KaiTi"):
        print(f"  {probe}: {'有' if probe in index else '无'}")

    engine = HbFtEngine(index)

    # 证据 1：kerning 真实存在（GDI 路线从构造上就没有）
    spec = FontSpec(families=("Segoe UI",), size=20.0)
    pair = engine.measure_text("To", spec).width
    solo = engine.measure_text("T", spec).width + engine.measure_text("o", spec).width
    print(f"\nkerning 验证（Segoe UI 20px）: 'To'={pair:.2f}px, 'T'+'o'={solo:.2f}px, "
          f"收紧 {solo - pair:.2f}px")

    # 证据 2：小数字号不再取整（GDI 的 lfHeight 只收整数）
    m = engine.measure_text("中文测试", FontSpec(families=("Microsoft YaHei UI",), size=17.5))
    print(f"小数字号 17.5px 下「中文测试」宽 {m.width:.2f}px（整数取整会丢掉这 0.5）")

    # 证据 3：真界面渲染
    print("\n渲染 hello 界面…", flush=True)
    sys.path.insert(0, "examples")
    import hello  # noqa: E402

    from inkstone.backend import HeadlessBackend  # noqa: E402
    from inkstone.core import BuildOwner  # noqa: E402
    from inkstone.devtools import render_to_png  # noqa: E402
    from inkstone.layout import BoxConstraints  # noqa: E402
    from inkstone.style import Theme  # noqa: E402
    from inkstone.text import TextEngine  # noqa: E402

    backend = HeadlessBackend(font_engine=engine)
    owner = BuildOwner(theme=Theme.light(), text_engine=TextEngine(backend))
    hello.build(owner)
    png = render_to_png(owner, BoxConstraints(max_width=hello.WIDTH, max_height=hello.HEIGHT))
    with open("_hello_hbft.png", "wb") as fh:
        fh.write(png)
    print(f"已写出 _hello_hbft.png（{len(png)} 字节）")
    print("stats:", engine.stats())


if __name__ == "__main__":
    main()
