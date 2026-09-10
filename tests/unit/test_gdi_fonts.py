"""真字体渲染测试（Windows / GDI）。

它守的是本项目最要命的一条：**中文必须渲染成真正的汉字，不是方块。**

之前软件光栅用的是内置确定性字形（5×7 位图 + 非拉丁占位块）。
那是验证后端的设计，好处是跨平台逐比特一致，代价是中文看不出是什么字——
对一个自称"中文一等公民"的 UI 库，这是不能接受的。

这里验证真字体路径的四件事：

1. **真字形**：画出来的掩码有形状、逐字符不同、与占位块完全不同。
2. **度量同源**：字形来自与度量**同一份** HFONT（`glyph_provider` 返回自己），
   所以字距与字形不会各说各话。
3. **自动配对**：组件树用哪个度量源，光栅就用哪个字形源——
   `devtools` 负责这件事，不可能配错。
4. **代理对**：emoji 是 UTF-16 代理对，长度必须按码元算而不是码点。
   传 `len()` 过去会让 GDI 只画半个代理对，字形变成空方框且不报错。

非 Windows 平台整体跳过：GDI 是 Windows 专有，其它平台等对应的
字体引擎（CoreText / FreeType）落地后本文件的断言可以照搬。
"""

from __future__ import annotations

import sys

import pytest

from inkstone.backend import HeadlessBackend, gdi_font_engine
from inkstone.backend.fonts import FontSpec
from inkstone.core import BuildOwner
from inkstone.devtools import render_to_png
from inkstone.devtools.screenshot import _provider_from
from inkstone.layout import BoxConstraints
from inkstone.style import Theme
from inkstone.text import TextEngine
from inkstone.widgets import Text

pytestmark = [
    pytest.mark.platform,
    pytest.mark.skipif(sys.platform != "win32", reason="GDI 字体引擎是 Windows 专有"),
]

CJK_FAMILY = "Microsoft YaHei UI"


@pytest.fixture(scope="module")
def engine():
    """一个共享的字体引擎。GDI 对象有线程亲和性，模块内复用即可。

    注意：**不做 close()**——engine 是模块级 fixture，且 `__del__` 兜底，
    显式关掉会让后续测试拿到已释放的 DC。
    """
    e = gdi_font_engine()
    if e is None:
        pytest.skip("本机拿不到 GDI 字体引擎")
    return e


# ================================================================ 字体发现


class TestFontDiscovery:
    def test_engine_available(self, engine) -> None:
        assert engine is not None

    def test_real_family_found(self, engine) -> None:
        """雅黑是 Windows 必备中文字体，必须能枚举到。"""
        assert engine.has_family(CJK_FAMILY), "枚举不到中文字体——中文会退化成方块"

    def test_missing_family_not_found(self, engine) -> None:
        """不存在的字体必须报 False。

        这一条很重要：GDI 的 `CreateFont` 在字体不存在时会**静默替换**，
        所以"创建成功"不能作为"字体存在"的依据。这里用的是真枚举。
        """
        assert not engine.has_family("完全不存在的字体 XYZ")

    def test_case_insensitive(self, engine) -> None:
        assert engine.has_family(CJK_FAMILY.lower())

    def test_generic_names_always_available(self, engine) -> None:
        """通用名永远可用——它们是回退链的终点，映射到具体字体是后端的活。"""
        for name in ("system-ui", "sans-serif", "monospace"):
            assert engine.has_family(name)

    def test_families_enumerated(self, engine) -> None:
        families = engine.families
        assert len(families) > 20, "枚举到的字体太少，枚举逻辑可能有问题"
        # 垂直书写（@ 前缀）的族名要过滤掉，它们不是可用的横排字体
        assert not any(f.startswith("@") for f in families)

    def test_resolve_picks_available(self, engine) -> None:
        face = engine.resolve_font(FontSpec((CJK_FAMILY,), 14.0))
        assert face.resolved_family == CJK_FAMILY

    def test_resolve_falls_back_when_missing(self, engine) -> None:
        """请求的字体一个都没有时，退到系统默认而不是崩掉。"""
        face = engine.resolve_font(FontSpec(("不存在的字体 XYZ",), 14.0))
        assert face.resolved_family


# ================================================================ 度量


class TestRealMetrics:
    def test_cjk_is_full_width(self, engine) -> None:
        """中式全角：一个汉字约等于一个字号。"""
        width = engine.measure_text("中", FontSpec((CJK_FAMILY,), 16.0)).width
        assert 14.0 <= width <= 18.0, f"汉字宽度 {width} 不像全角"

    def test_latin_narrower_than_cjk(self, engine) -> None:
        spec = FontSpec((CJK_FAMILY,), 16.0)
        latin = engine.measure_text("A", spec).width
        cjk = engine.measure_text("中", spec).width
        assert latin < cjk, "拉丁字母不该和汉字一样宽"

    def test_width_scales_with_size(self, engine) -> None:
        small = engine.measure_text("中文测试", FontSpec((CJK_FAMILY,), 12.0)).width
        large = engine.measure_text("中文测试", FontSpec((CJK_FAMILY,), 24.0)).width
        assert large > small * 1.8, "字号翻倍宽度没跟着变——度量没走系统字体"

    def test_advance_count_matches_clusters(self, engine) -> None:
        metrics = engine.measure_text("你好Hello", FontSpec((CJK_FAMILY,), 16.0))
        assert len(metrics.advance) == 7  # 2 汉字 + 5 拉丁，逐字素簇

    def test_width_equals_sum_of_advances(self, engine) -> None:
        """整串宽度必须等于逐簇之和——否则命中测试与绘制会错位。"""
        metrics = engine.measure_text("你好Hello世界", FontSpec((CJK_FAMILY,), 16.0))
        assert metrics.width == pytest.approx(sum(metrics.advance))

    def test_ascent_descent_positive(self, engine) -> None:
        metrics = engine.measure_text("中", FontSpec((CJK_FAMILY,), 16.0))
        assert metrics.ascent > 0
        assert metrics.descent > 0

    def test_measurement_deterministic(self, engine) -> None:
        spec = FontSpec((CJK_FAMILY,), 16.0)
        first = engine.measure_text("你好世界 Hello 123", spec)
        for _ in range(3):
            assert engine.measure_text("你好世界 Hello 123", spec) == first

    def test_emoji_surrogate_pair_measured(self, engine) -> None:
        """**回归测试**：emoji 是代理对，长度必须按 UTF-16 码元算。

        传 `len(text)`（码点数）会让 GDI 只量半个代理对，
        宽度变成一个荒唐的小数字，字形变成空方框，而且不报错。
        """
        width = engine.measure_text("🎉", FontSpec((CJK_FAMILY,), 16.0)).width
        assert width >= 8.0, f"emoji 宽度只有 {width}，代理对很可能没处理对"

    def test_emoji_after_cjk_measured(self, engine) -> None:
        """混合串里夹 emoji 不能把前面的宽度算丢。"""
        spec = FontSpec((CJK_FAMILY,), 16.0)
        without = engine.measure_text("中文", spec).width
        with_emoji = engine.measure_text("中文🎉", spec).width
        assert with_emoji > without


# ================================================================ 真字形


class TestRealGlyphs:
    def test_chinese_glyph_has_ink(self, engine) -> None:
        """汉字必须画出东西——这是"不再是方块"的最直接证据。"""
        mask = engine.mask_for("中", 16.0, CJK_FAMILY, 16.0)
        assert mask.width > 0 and mask.height > 0
        assert max(mask.coverage) > 128, "字形几乎全空，中文没渲染出来"

    def test_glyph_is_not_a_solid_block(self, engine) -> None:
        """真字形是**笔画**，不是一整块实心矩形。

        占位块的特征是"内部几乎全满"。汉字有大量留白，
        实心像素占比必然明显低于矩形。
        """
        mask = engine.mask_for("国", 32.0, CJK_FAMILY, 32.0)
        solid = sum(1 for b in mask.coverage if b > 200)
        ink = sum(1 for b in mask.coverage if b > 40)
        assert ink > 0
        assert solid / ink < 0.9, "字形内部几乎全是实心——看起来还是占位块"

    def test_distinct_characters_distinct_glyphs(self, engine) -> None:
        a = engine.mask_for("中", 16.0, CJK_FAMILY, 16.0)
        b = engine.mask_for("国", 16.0, CJK_FAMILY, 16.0)
        assert a.coverage != b.coverage, "两个不同的汉字画出了同一张图"

    def test_glyph_deterministic(self, engine) -> None:
        a = engine.mask_for("永", 16.0, CJK_FAMILY, 16.0)
        b = engine.mask_for("永", 16.0, CJK_FAMILY, 16.0)
        assert a == b

    def test_antialiased(self, engine) -> None:
        """要有中间灰度——只有 0 和 255 说明抗锯齿没生效（边缘会是硬锯齿）。"""
        mask = engine.mask_for("永", 24.0, CJK_FAMILY, 24.0)
        mid = [b for b in mask.coverage if 40 < b < 215]
        assert mid, "字形没有任何中间灰度，说明没有抗锯齿"

    def test_latin_glyph_has_ink(self, engine) -> None:
        mask = engine.mask_for("A", 16.0, CJK_FAMILY, 8.0)
        assert max(mask.coverage) > 128

    def test_space_draws_nothing(self, engine) -> None:
        mask = engine.mask_for(" ", 16.0, CJK_FAMILY, 4.0)
        assert not any(mask.coverage), "空格不该画出像素"

    def test_glyph_fits_advance(self, engine) -> None:
        """字形不能明显宽于它的 advance，否则相邻字会叠在一起。"""
        advance = engine.measure_text("门", FontSpec((CJK_FAMILY,), 16.0)).width
        mask = engine.mask_for("门", 16.0, CJK_FAMILY, advance)
        # 留 margin 的余量；超出太多就是错位
        assert mask.width <= advance + 6

    def test_emoji_glyph_has_ink(self, engine) -> None:
        """回归：emoji 必须画出图形，而不是一个空方框。"""
        spec = FontSpec(("Segoe UI Emoji", CJK_FAMILY), 16.0)
        advance = engine.measure_text("🎉", spec).width
        mask = engine.mask_for("🎉", 16.0, "Segoe UI Emoji", advance)
        assert max(mask.coverage) > 128, "emoji 画出来是空的（很可能是代理对没处理）"

    def test_is_never_placeholder(self, engine) -> None:
        assert not engine.is_placeholder_only("中")
        assert not engine.is_placeholder_only("A")


# ================================================================ 度量同源


class TestMetricsAndGlyphAreOneSource:
    """docs/04 §3：测量与绘制必须同源。真字体路径靠"同一个对象"保证。"""

    def test_glyph_provider_is_self(self, engine) -> None:
        assert engine.glyph_provider is engine, "度量与字形不是同一个对象——这样字距和字形可能对不上"

    def test_devtools_pairs_real_engine(self) -> None:
        """组件树用真字体度量 → 光栅自动用真字形。"""
        owner = BuildOwner(
            theme=Theme.light(),
            text_engine=TextEngine(HeadlessBackend(font_engine=gdi_font_engine())),
        )
        assert _provider_from(owner) is not None

    def test_devtools_pairs_deterministic_as_none(self) -> None:
        """确定性度量表没有字形能力 → 返回 None → 光栅用内置字形。

        黄金图走的正是这条路，所以它跨平台逐字节一致。
        """
        owner = BuildOwner(theme=Theme.light(), text_engine=TextEngine(HeadlessBackend()))
        assert _provider_from(owner) is None

    def test_headless_with_system_fonts_uses_real_engine(self) -> None:
        backend = HeadlessBackend(system_fonts=True)
        assert backend.glyph_provider is not None, "system_fonts=True 没有用上真字体"
        assert type(backend.glyph_provider).__name__ == "GdiFontEngine"

    def test_headless_default_has_no_glyph_provider(self) -> None:
        backend = HeadlessBackend()
        assert backend.glyph_provider is None

    def test_font_stats_reported(self) -> None:
        backend = HeadlessBackend(system_fonts=True)
        stats = backend.font_stats()
        assert stats["families"] > 0


# ================================================================ 端到端


class TestRealFontRendering:
    """从组件树到像素：真字体路径必须真的把汉字画出来。"""

    @staticmethod
    def _render(theme: Theme, system_fonts: bool) -> bytes:
        backend = HeadlessBackend(system_fonts=system_fonts)
        owner = BuildOwner(theme=theme, text_engine=TextEngine(backend))
        owner.mount(Text("中文渲染测试 ABC", size="lg"))
        return render_to_png(owner, BoxConstraints(max_width=300, max_height=80))

    def test_real_font_differs_from_deterministic(self) -> None:
        """真字体与确定性字形的产物**必须不同**——否则说明真字形没被用上。"""
        real = self._render(Theme.light(), system_fonts=True)
        det = self._render(Theme.light(), system_fonts=False)
        assert real != det, "真字体渲染结果与占位字形一样，说明真字形没生效"

    def test_real_render_is_deterministic(self) -> None:
        """同一台机器上重复渲染必须一致（同一份字体、同一套光栅）。"""
        first = self._render(Theme.light(), system_fonts=True)
        for _ in range(2):
            assert self._render(Theme.light(), system_fonts=True) == first

    def test_real_glyphs_leave_different_ink_than_blocks(self) -> None:
        """真汉字笔画细、留白多；占位块是实心矩形。

        背景是不透明的，所以不能数 alpha——要数**与背景色不同**的像素。
        这里只要求两者有可观测差异，不锁定具体数值（字体与字号会变）。
        """
        from inkstone.backend import HeadlessBackend as HB
        from inkstone.devtools import render_to_framebuffer
        from inkstone.layout import BoxConstraints as BC

        def ink_pixels(system_fonts: bool) -> int:
            backend = HB(system_fonts=system_fonts)
            theme = Theme.light()
            owner = BuildOwner(theme=theme, text_engine=TextEngine(backend))
            owner.mount(Text("国国国国", size="xl"))
            # background=True 会用主题的 bg 令牌铺底，所以"与背景不同"就是字迹
            frame = render_to_framebuffer(owner, BC(max_width=200, max_height=60))
            bg = theme.color("bg")
            count = 0
            for i in range(0, len(frame.data), 4):
                if (
                    abs(frame.data[i] - bg.r) > 8
                    or abs(frame.data[i + 1] - bg.g) > 8
                    or abs(frame.data[i + 2] - bg.b) > 8
                ):
                    count += 1
            return count

        real = ink_pixels(True)
        blocks = ink_pixels(False)
        assert real > 0 and blocks > 0, "两条路径都得画出东西"
        assert real != blocks, "真字形与占位块的字迹像素数完全相同，可疑"
        assert real < blocks, "真汉字留白多，字迹像素应当**少于**实心占位块"
