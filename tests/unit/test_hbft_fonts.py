"""HarfBuzz + FreeType 引擎测试（docs/18 §R4.2）。

全部用**合成字体**（`tests/font_fixtures.py` 现造），所以：
- 三平台 CI 行为一致（不赌"这台机器装了什么"）；
- 能精确构造想测的东西——传统 `kern` 表、GSUB 连字、不同字宽的字重。

这一组测试守的是 R4 的**存在理由**：kerning 真实生效、小数字号不取整、
度量与字形同源、缺字能被探测出来。它们红了就说明"换文本栈"这件事没做成。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from font_fixtures import build_font, build_kerned_font
from inkstone.backend import (
    DEFAULT_FALLBACK_FAMILY,
    FontLibrary,
    FontMetricsError,
    FontSlant,
    FontSpec,
    FontWeight,
    HbFtFontEngine,
)


def engine_for(*paths: Path, **kwargs: object) -> HbFtFontEngine:
    """建一个只认给定字体文件的引擎（`directories=()` = 一个系统字体都不扫）。"""
    library = FontLibrary(directories=())
    for path in paths:
        library.register_file(path, **kwargs)  # type: ignore[arg-type]
    return HbFtFontEngine(library)


# ---------------------------------------------------------------- 存在理由


class TestKerning:
    """**kerning 真实生效**——这是换文本栈最直接的收益。

    GDI 那条路逐簇 `TextOut`，从构造上就不可能做 kerning（它不知道相邻字形）。
    这里用一张手写的传统 `kern` 表来证明：整串整形之后，"AA" 比两个单独的 "A" 窄。
    """

    def test_kerning_tightens_the_pair(self, tmp_path: Path):
        font = build_kerned_font(tmp_path / "kern.ttf")
        engine = engine_for(font)
        spec = FontSpec(families=("Kern Test",), size=20.0)

        pair = engine.measure_text("AA", spec).width
        solo = engine.measure_text("A", spec).width * 2
        assert pair < solo, "整串整形应当比逐字宽度之和更紧"
        # 表里写的是 -200/1000 em，20px 下正好收紧 4px
        assert solo - pair == pytest.approx(4.0)

    def test_kerning_absent_when_the_font_has_no_kern_table(self, tmp_path: Path):
        """没有 kern 表的字体不该凭空收紧——否则"收紧"就不是来自字体了。"""
        font = build_font(tmp_path / "plain.ttf", family="Plain")
        engine = engine_for(font)
        spec = FontSpec(families=("Plain",), size=20.0)
        assert engine.measure_text("AA", spec).width == pytest.approx(
            engine.measure_text("A", spec).width * 2
        )


class TestFractionalSize:
    """**小数字号不许取整**：17.5px 就是 17.5px。

    GDI 的 `lfHeight` 只吃整数，125% DPI 下必然丢 0.5px。HarfBuzz 与 FreeType
    都走 26.6 定点（`size × 64`），所以这里一步都不许 round 到整像素。
    """

    #: HarfBuzz 把缩放后的 advance 量化到 26.6 定点，即 **1/64 像素**：
    #: 600/1000 em × 17px = 10.2，实际落在 10.203125（653/64）。
    #: 所以容差取 1/64 而不是默认的 1e-6——这不是"数值不稳"，
    #: 是定点数的分辨率，量级 0.016px，对排版完全够用。
    FIXED_POINT = 1.0 / 64.0

    @pytest.mark.parametrize(("size", "expected"), [(17.5, 10.5), (17.0, 10.2), (18.0, 10.8)])
    def test_advance_is_exact_at_fractional_sizes(
        self, tmp_path: Path, size: float, expected: float
    ):
        font = build_font(tmp_path / "f.ttf", family="Frac", advance=600)
        engine = engine_for(font)
        spec = FontSpec(families=("Frac",), size=size)
        # 600/1000 em × 字号
        assert engine.measure_text("A", spec).width == pytest.approx(expected, abs=self.FIXED_POINT)

    def test_half_pixel_is_not_lost(self, tmp_path: Path):
        font = build_font(tmp_path / "f.ttf", family="Frac", advance=600)
        engine = engine_for(font)
        at_17_5 = engine.measure_text("A", FontSpec(families=("Frac",), size=17.5)).width
        at_17 = engine.measure_text("A", FontSpec(families=("Frac",), size=17.0)).width
        at_18 = engine.measure_text("A", FontSpec(families=("Frac",), size=18.0)).width
        assert at_17 < at_17_5 < at_18, "0.5px 的差别必须体现出来（取整就会落在两端）"


class TestWeightSelection:
    """按 CSS 规则挑字重——请求 BOLD 就该拿到 700 那个面。"""

    def test_bold_picks_the_bold_face(self, tmp_path: Path):
        build_font(tmp_path / "r.ttf", family="W", subfamily="Regular", weight=400, advance=600)
        build_font(tmp_path / "b.ttf", family="W", subfamily="Bold", weight=700, advance=900)
        engine = engine_for(tmp_path / "r.ttf", tmp_path / "b.ttf")

        regular = engine.measure_text("A", FontSpec(families=("W",), size=20.0))
        bold = engine.measure_text(
            "A", FontSpec(families=("W",), size=20.0, weight=FontWeight.BOLD)
        )
        assert regular.width == pytest.approx(12.0)
        assert bold.width == pytest.approx(18.0), "BOLD 应当选到 advance=900 的那个面"

    def test_italic_is_preferred_over_weight(self, tmp_path: Path):
        build_font(tmp_path / "b.ttf", family="W", subfamily="Bold", weight=700, advance=900)
        build_font(
            tmp_path / "i.ttf",
            family="W",
            subfamily="Italic",
            weight=400,
            italic=True,
            advance=700,
        )
        engine = engine_for(tmp_path / "b.ttf", tmp_path / "i.ttf")
        spec = FontSpec(families=("W",), size=20.0, weight=FontWeight.BOLD, slant=FontSlant.ITALIC)
        # 斜体是"类别"、字重是"程度"：要斜体时先满足斜体
        assert engine.measure_text("A", spec).width == pytest.approx(14.0)


# ---------------------------------------------------------------- 覆盖探测


class TestGlyphCoverage:
    """`has_glyph`：回退链从"族存在"升级为"族覆盖该脚本"的判据。"""

    def test_covered_and_uncovered_chars(self, tmp_path: Path):
        font = build_font(tmp_path / "a.ttf", family="Latin Only")
        engine = engine_for(font)
        assert engine.has_glyph("Latin Only", "A")
        assert not engine.has_glyph("Latin Only", "中")
        assert not engine.has_glyph("Latin Only", "あ")

    def test_unknown_family_has_nothing(self, tmp_path: Path):
        engine = engine_for()
        assert not engine.has_glyph("No Such Family", "A")

    def test_empty_char_is_false(self, tmp_path: Path):
        font = build_font(tmp_path / "a.ttf", family="Latin Only")
        engine = engine_for(font)
        assert not engine.has_glyph("Latin Only", "")

    def test_only_the_base_char_is_probed(self, tmp_path: Path):
        """只探基字符：组合符跟随基字符，按整簇判断会把所有候选族都否掉。"""
        font = build_font(tmp_path / "a.ttf", family="Latin Only")
        engine = engine_for(font)
        assert engine.has_glyph("Latin Only", "A\u0301"), "基字符有字形就算覆盖"


# ---------------------------------------------------------------- 度量与字形


class TestMetricsAndGlyphs:
    """ADR-0009：度量与字形必须是**同一个对象**，否则字距与字形会对不上。"""

    def test_the_engine_is_its_own_glyph_provider(self, tmp_path: Path):
        engine = engine_for(build_font(tmp_path / "a.ttf", family="F"))
        assert engine.glyph_provider is engine

    def test_resolve_font_reports_the_family_actually_used(self, tmp_path: Path):
        build_font(tmp_path / "a.ttf", family="Real Family")
        engine = engine_for(tmp_path / "a.ttf")
        face = engine.resolve_font(FontSpec(families=("Missing", "Real Family")))
        assert face.resolved_family == "Real Family"

    def test_mask_is_a_real_glyph(self, tmp_path: Path):
        build_font(tmp_path / "a.ttf", family="F", advance=600)
        engine = engine_for(tmp_path / "a.ttf")
        mask = engine.mask_for("A", 20.0, "F", 12.0)
        assert mask.width > 0 and mask.height > 0
        assert any(mask.coverage), "掩码不能全是 0——那是「算了但没画」"
        # 掩码带 1px 余量（抗锯齿的边缘可能落在整数格之外），所以原点是 0 或 -1
        assert -1 <= mask.left <= 0, "掩码原点应当在笔位置上（允许 1px 余量）"
        assert mask.top < 0, "字形在基线**上方**，top 应当为负"

    def test_mask_scales_with_size(self, tmp_path: Path):
        build_font(tmp_path / "a.ttf", family="F", advance=600)
        engine = engine_for(tmp_path / "a.ttf")
        small = engine.mask_for("A", 12.0, "F", 7.2)
        large = engine.mask_for("A", 24.0, "F", 14.4)
        assert large.height > small.height

    def test_whitespace_has_no_mask(self, tmp_path: Path):
        engine = engine_for(build_font(tmp_path / "a.ttf", family="F"))
        assert engine.mask_for(" ", 14.0, "F", 7.0).width == 0
        assert engine.mask_for("", 14.0, "F", 7.0).width == 0

    def test_is_never_placeholder_only(self, tmp_path: Path):
        """真字体栈下永远有真字形——占位块是内置后端的特征。"""
        engine = engine_for(build_font(tmp_path / "a.ttf", family="F"))
        assert engine.is_placeholder_only("中文") is False


class TestInvariants:
    def test_width_equals_sum_of_advances(self, tmp_path: Path):
        font = build_kerned_font(tmp_path / "k.ttf")
        engine = engine_for(font)
        run = engine.shape_line("AfiA", FontSpec(families=("Kern Test",), size=16.0))
        assert run.metrics.width == pytest.approx(sum(run.metrics.advance))
        assert len(run.metrics.advance) == len(run.placements)

    def test_placements_are_contiguous_and_ordered(self, tmp_path: Path):
        font = build_kerned_font(tmp_path / "k.ttf")
        engine = engine_for(font)
        run = engine.shape_line("Afi", FontSpec(families=("Kern Test",), size=16.0))
        for previous, current in zip(run.placements, run.placements[1:], strict=False):
            assert current.x == pytest.approx(previous.x + previous.advance)
            assert current.start == previous.end, "簇下标必须首尾相接，命中测试靠它"

    def test_empty_text_is_a_documented_empty_result(self, tmp_path: Path):
        engine = engine_for(build_font(tmp_path / "a.ttf", family="F"))
        run = engine.shape_line("", FontSpec(families=("F",), size=14.0))
        assert run.placements == ()
        assert run.metrics.width == 0.0


class TestLigatures:
    """连字：一个 hb 簇覆盖多个字素簇时的簇映射。

    这是 R4.2 初版写错的地方——它把整个 advance 全算在第一个字素簇上，
    于是光标在连字中间会跳到末尾。修法是看**下一个**字形的簇来定右界。
    """

    def test_ligature_advance_is_split_evenly(self, tmp_path: Path):
        font = build_kerned_font(tmp_path / "k.ttf")
        engine = engine_for(font)
        run = engine.shape_line("ffi", FontSpec(families=("Kern Test",), size=20.0))

        assert len(run.placements) == 3, "字素簇数不受连字影响——始终是三个"
        assert run.metrics.advance == pytest.approx((4.0, 4.0, 4.0))
        assert run.metrics.width == pytest.approx(12.0)

    def test_caret_positions_land_inside_the_ligature(self, tmp_path: Path):
        """均分之后，簇位置落在连字**内部**（浏览器的行为），而不是全挤在末尾。"""
        font = build_kerned_font(tmp_path / "k.ttf")
        engine = engine_for(font)
        run = engine.shape_line("ffi", FontSpec(families=("Kern Test",), size=20.0))
        assert [round(p.x, 3) for p in run.placements] == [0.0, 4.0, 8.0]

    def test_ligature_keeps_the_total_width(self, tmp_path: Path):
        """均分不许改变总宽——否则断行与对齐会跟着漂。"""
        font = build_kerned_font(tmp_path / "k.ttf")
        engine = engine_for(font)
        spec = FontSpec(families=("Kern Test",), size=20.0)
        run = engine.shape_line("ffi", spec)
        assert run.metrics.width == pytest.approx(sum(run.metrics.advance))


# ---------------------------------------------------------------- 错误与缓存


class TestFailuresAreLoud:
    def test_no_family_and_no_fallback_fails_loudly(self, tmp_path: Path):
        """一个族都没有、又没有内嵌兜底字体时**响亮失败**。

        静默挑一个系统字体的表现是"中文变方块"，而报错能让人一眼看出
        是环境没准备好（缺 R4.4 的内嵌字体）。
        """
        engine = engine_for()
        with pytest.raises(FontMetricsError, match="回退链"):
            engine.measure_text("A", FontSpec(families=("Missing",), size=14.0))

    def test_embedded_fallback_takes_over(self, tmp_path: Path):
        """注册了兜底字体之后，找不到任何族也能出真字形。"""
        fallback = build_font(tmp_path / "fb.ttf", family=DEFAULT_FALLBACK_FAMILY)
        library = FontLibrary(directories=())
        library.register_file(fallback, is_fallback=True)
        engine = HbFtFontEngine(library)
        assert engine.measure_text("A", FontSpec(families=("Missing",), size=14.0)).width > 0

    def test_unreadable_font_file_is_reported(self, tmp_path: Path):
        broken = tmp_path / "broken.ttf"
        broken.write_bytes(b"not a font")
        library = FontLibrary(directories=())
        with pytest.raises(FontMetricsError, match="读不出任何字体面"):
            library.register_file(broken)


class TestCaching:
    def test_repeated_queries_hit_the_cache(self, tmp_path: Path):
        font = build_kerned_font(tmp_path / "k.ttf")
        engine = engine_for(font)
        spec = FontSpec(families=("Kern Test",), size=16.0)

        engine.shape_line("Afi", spec)
        first = engine.stats()
        for _ in range(5):
            engine.shape_line("Afi", spec)
        assert engine.stats() == first, "重复整形不该增长任何缓存"

    def test_mask_cache_key_includes_text_size_and_family(self, tmp_path: Path):
        build_font(tmp_path / "a.ttf", family="F1", advance=600)
        build_font(tmp_path / "b.ttf", family="F2", advance=900)
        engine = engine_for(tmp_path / "a.ttf", tmp_path / "b.ttf")
        engine.mask_for("A", 20.0, "F1", 12.0)
        engine.mask_for("A", 20.0, "F2", 18.0)
        engine.mask_for("A", 40.0, "F1", 24.0)
        assert engine.stats()["mask_cached"] == 3, "文本/字号/族任一不同就该是另一条缓存"

    def test_clear_caches_empties_everything(self, tmp_path: Path):
        engine = engine_for(build_kerned_font(tmp_path / "k.ttf"))
        engine.shape_line("A", FontSpec(families=("Kern Test",), size=14.0))
        engine.mask_for("A", 14.0, "Kern Test", 8.4)
        engine.clear_caches()
        stats = engine.stats()
        assert stats["shape_cached"] == 0
        assert stats["mask_cached"] == 0
        assert stats["faces"] == 0

    def test_construction_is_cheap(self, tmp_path: Path):
        """构造引擎**不扫字体目录**——无头测试里经常创建了却不用。"""
        engine = HbFtFontEngine()
        assert engine.stats()["faces"] == 0
        assert engine.stats()["fonts_loaded"] == 0
