"""字体库测试（docs/18 §R4.1）。

**全部用合成字体**（`fontTools.fontBuilder` 现造，每个 1KB 以内），
不依赖系统上装了什么：这样测试在三平台 CI 上的行为完全一样，
而"这台机器上恰好有 Microsoft YaHei"不该是测试通过的前提。

覆盖四件事：
1. 扫描与索引（含 TTC 子面枚举——**下标必须是真的**）；
2. 字重 / 斜体按 CSS Fonts 4 §5.2.2 匹配；
3. 用户字体注册（`@font-face` 等价物）与内嵌兜底；
4. 损坏文件跳过但**记账**（不许静默）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont

from inkstone.backend import FontMetricsError, FontSlant, FontWeight
from inkstone.backend.fontfiles import (
    DEFAULT_FALLBACK_FAMILY,
    FontLibrary,
    default_font_directories,
    embedded_font_path,
    generic_candidates,
    order_weights,
)


def build_font(
    path: Path,
    *,
    family: str = "Test Sans",
    subfamily: str = "Regular",
    weight: int = 400,
    italic: bool = False,
    typographic_family: str | None = None,
    has_os2: bool = True,
) -> Path:
    """造一个最小可用的字体文件（有真轮廓，能被 FreeType 加载）。"""
    builder = FontBuilder(1000, isTTF=True)
    order = [".notdef", "A"]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({65: "A"})
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name == "A":
            pen.moveTo((0, 0))
            pen.lineTo((300, 700))
            pen.lineTo((600, 0))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    names = {
        "familyName": family,
        "styleName": subfamily,
        "fullName": f"{family} {subfamily}",
        "psName": f"{family}-{subfamily}".replace(" ", ""),
    }
    if typographic_family is not None:
        names["typographicFamily"] = typographic_family
        names["typographicSubfamily"] = subfamily
    builder.setupNameTable(names)
    if has_os2:
        builder.setupOS2(
            usWeightClass=weight,
            fsSelection=1 if italic else 0,
            sTypoAscender=800,
            sTypoDescender=-200,
        )
    builder.setupPost()
    builder.save(str(path))
    return path


@pytest.fixture
def library(tmp_path: Path) -> FontLibrary:
    """一个只扫 `tmp_path` 的字体库（不碰真实系统字体目录）。

    `embedded=False` 是**刻意**的：这些测试测的是"扫描目录与匹配字重"，
    内嵌兜底字体（R4.4）是另一件事，混进来只会让"索引里到底有什么"
    变得含糊。内嵌字体自己的测试在 `TestEmbeddedFallback`。
    """
    return FontLibrary(directories=(tmp_path,), embedded=False)


# ---------------------------------------------------------------- 字重规则


class TestWeightMatchingRules:
    """CSS Fonts 4 §5.2.2。照抄规范，不自己发明。"""

    def test_target_400_prefers_500_before_lighter(self):
        assert order_weights([100, 400, 500, 700], 400) == [400, 500, 100, 700]

    def test_target_500_prefers_400_before_lighter(self):
        assert order_weights([100, 400, 500, 700], 500) == [500, 400, 100, 700]

    def test_target_above_500_prefers_heavier_first(self):
        assert order_weights([400, 700, 900], 700) == [700, 900, 400]

    def test_target_below_400_prefers_lighter_first(self):
        assert order_weights([100, 400, 700], 300) == [100, 400, 700]

    def test_exact_match_wins(self):
        assert order_weights([400, 700], 700)[0] == 700

    def test_empty_available(self):
        assert order_weights([], 400) == []


# ---------------------------------------------------------------- 扫描与索引


class TestScanning:
    def test_finds_families_in_a_directory(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "a.ttf", family="Alpha Sans")
        build_font(tmp_path / "b.ttf", family="Beta Serif")
        assert set(library.families()) == {"Alpha Sans", "Beta Serif"}

    def test_scan_is_idempotent_and_counts_faces_once(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "a.ttf", family="Alpha Sans")
        build_font(tmp_path / "b.ttf", family="Alpha Sans", subfamily="Bold", weight=700)
        library.scan()
        library.scan()
        assert library.stats()["faces"] == 2, "重复 scan 不该把面重复计数"

    def test_prefers_typographic_family_name(self, tmp_path: Path, library: FontLibrary):
        """nameID 16 优先于 nameID 1——否则 "YaHei Light" 这类族名永远找不到。"""
        build_font(
            tmp_path / "light.ttf",
            family="Test YaHei",
            subfamily="Light",
            weight=300,
            typographic_family="Test YaHei Light",
        )
        assert "Test YaHei Light" in library.families()
        assert "Test YaHei" not in library.families()

    def test_falls_back_to_legacy_family_name(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "old.ttf", family="Legacy Only")
        assert "Legacy Only" in library.families()

    def test_reads_weight_and_slant(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "b.ttf", family="W", subfamily="Bold", weight=700)
        build_font(tmp_path / "i.ttf", family="W", subfamily="Italic", weight=400, italic=True)
        bold = library.find("W", weight=FontWeight.BOLD)
        italic = library.find("W", slant=FontSlant.ITALIC)
        assert bold is not None and bold.weight == 700
        assert italic is not None and italic.slant is FontSlant.ITALIC

    def test_ignores_vertical_cjk_variants(self, tmp_path: Path, library: FontLibrary):
        """Windows 上 "@族名" 是竖排变体，横向排版不该选到它。"""
        build_font(tmp_path / "v.ttf", family="@Test Vertical")
        assert library.families() == ()

    def test_missing_os2_infers_weight_from_subfamily(self, tmp_path: Path, library: FontLibrary):
        """少数老字体没有 OS/2 表——不能因此把 Bold 当成 Regular。"""
        build_font(tmp_path / "old.ttf", family="Old Sans", subfamily="Bold", has_os2=False)
        record = library.find("Old Sans", weight=FontWeight.BOLD)
        assert record is not None and record.weight == 700

    def test_non_font_files_are_ignored(self, tmp_path: Path, library: FontLibrary):
        (tmp_path / "readme.txt").write_text("not a font", encoding="utf-8")
        build_font(tmp_path / "a.ttf", family="Alpha Sans")
        assert library.families() == ("Alpha Sans",)
        assert library.skipped == []


class TestCorruptFiles:
    """损坏文件**跳过但记账**：300 个系统字体里坏一个不该让应用起不来，
    但"跳过了什么"必须能查——否则就是静默失败。"""

    def test_corrupt_file_is_skipped_and_recorded(self, tmp_path: Path, library: FontLibrary):
        (tmp_path / "broken.ttf").write_bytes(b"this is not a font at all")
        build_font(tmp_path / "good.ttf", family="Good Sans")
        assert library.families() == ("Good Sans",)
        assert len(library.skipped) == 1
        assert "broken.ttf" in library.skipped[0][0]
        assert library.stats()["skipped"] == 1

    def test_truncated_font_is_skipped(self, tmp_path: Path, library: FontLibrary):
        good = build_font(tmp_path / "good.ttf", family="Good Sans")
        (tmp_path / "cut.ttf").write_bytes(good.read_bytes()[:120])
        library.scan()
        assert any("cut.ttf" in where for where, _ in library.skipped)


# ---------------------------------------------------------------- TTC 子面


class TestCollections:
    def test_ttc_subfaces_are_enumerated_with_real_indices(
        self, tmp_path: Path, library: FontLibrary
    ):
        """**下标必须是真的子面下标。**

        过滤掉竖排族时若用"过滤后的位置"当下标，就会加载到同文件里
        **另一个字体**——表现是"选 YaHei 出来的是 YaHei Light"，而且不报错。
        这里故意把竖排族放在第 0 位来钉住这一点。
        """
        first = build_font(tmp_path / "face0.ttf", family="@Vertical Variant")
        second = build_font(tmp_path / "face1.ttf", family="Real Sans")
        collection = TTCollection()
        collection.fonts = [TTFont(str(first)), TTFont(str(second))]
        ttc = tmp_path / "bundle.ttc"
        collection.save(str(ttc))

        record = library.find("Real Sans")
        assert record is not None
        assert record.index == 1, "竖排族占了第 0 个子面，真实下标应当是 1"

        # 而且按这个下标真能加载到那个面
        from fontTools.ttLib import TTCollection as Check

        assert Check(str(ttc), lazy=True).fonts[record.index]["name"].getDebugName(1) == "Real Sans"

    def test_otc_suffix_is_also_treated_as_a_collection(self, tmp_path: Path, library: FontLibrary):
        one = build_font(tmp_path / "one.ttf", family="One Sans")
        collection = TTCollection()
        collection.fonts = [TTFont(str(one))]
        collection.save(str(tmp_path / "bundle.otc"))
        assert library.find("One Sans") is not None


# ---------------------------------------------------------------- 匹配


class TestFindAndResolve:
    def test_find_returns_none_for_unknown_family(self, library: FontLibrary):
        assert library.find("No Such Family") is None

    def test_has_family(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "a.ttf", family="Alpha Sans")
        assert library.has_family("Alpha Sans")
        assert not library.has_family("Beta Sans")

    def test_picks_closest_weight(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "r.ttf", family="F", subfamily="Regular", weight=400)
        build_font(tmp_path / "b.ttf", family="F", subfamily="Bold", weight=700)
        medium = library.find("F", weight=FontWeight.MEDIUM)
        assert medium is not None and medium.weight == 400, "请求 500、只有 400/700 时应当选 400"

    def test_italic_preferred_over_weight(self, tmp_path: Path, library: FontLibrary):
        """斜体是"类别"，字重是"程度"：要斜体时先满足斜体。"""
        build_font(tmp_path / "b.ttf", family="F", subfamily="Bold", weight=700)
        build_font(tmp_path / "i.ttf", family="F", subfamily="Italic", weight=400, italic=True)
        hit = library.find("F", weight=FontWeight.BOLD, slant=FontSlant.ITALIC)
        assert hit is not None and hit.slant is FontSlant.ITALIC

    def test_empty_family_name_is_rejected(self, library: FontLibrary):
        with pytest.raises(FontMetricsError, match="不能为空"):
            library.find("")

    def test_resolve_walks_the_fallback_chain(self, tmp_path: Path, library: FontLibrary):
        build_font(tmp_path / "a.ttf", family="Second Choice")
        hit = library.resolve(("Missing One", "Second Choice", "Missing Two"))
        assert hit.family == "Second Choice"

    def test_resolve_without_fallback_font_fails_loudly(self, library: FontLibrary):
        """一个族都没有时**响亮失败**，不返回"随便挑的"字体。

        静默挑错的表现是"中文变成方块"或"字距全乱"；报错能让人一眼看出
        是环境没准备好。
        """
        with pytest.raises(FontMetricsError, match="回退链"):
            library.resolve(("Missing One", "Missing Two"))

    def test_resolve_falls_back_to_the_embedded_font(self, tmp_path: Path, library: FontLibrary):
        fallback = build_font(tmp_path / "fallback.ttf", family=DEFAULT_FALLBACK_FAMILY)
        library.register_file(fallback, is_fallback=True)
        hit = library.resolve(("Missing One", "Missing Two"))
        assert hit.family == DEFAULT_FALLBACK_FAMILY
        assert hit.is_fallback


# ---------------------------------------------------------------- 用户注册


class TestUserRegistration:
    def test_register_file_adds_it_to_the_index(self, tmp_path: Path, library: FontLibrary):
        outside = tmp_path.parent / "outside.ttf"
        build_font(outside, family="User Font")
        assert not library.has_family("User Font")

        records = library.register_file(outside)
        assert library.has_family("User Font")
        assert records[0].family == "User Font"

    def test_register_missing_file_raises(self, tmp_path: Path, library: FontLibrary):
        with pytest.raises(FontMetricsError, match="不存在"):
            library.register_file(tmp_path / "nope.ttf")

    def test_register_corrupt_file_raises_with_reason(self, tmp_path: Path, library: FontLibrary):
        broken = tmp_path / "broken.ttf"
        broken.write_bytes(b"nope")
        with pytest.raises(FontMetricsError, match="读不出任何字体面"):
            library.register_file(broken)

    def test_user_font_can_shadow_a_system_family(self, tmp_path: Path, library: FontLibrary):
        """同名族注册两份时，两份都进索引——由调用方按需选择（不静默覆盖）。

        为什么这样：用户注册的字体与系统字体同名是常见事（比如自带一份更粗的
        "Microsoft YaHei"）。静默覆盖会让"系统那份去哪了"变成没有答案的问题；
        两份都在，`find()` 就按字重/斜体挑。
        """
        build_font(tmp_path / "system.ttf", family="Shared", subfamily="Regular", weight=400)
        outside = tmp_path.parent / "user.ttf"
        build_font(outside, family="Shared", subfamily="Bold", weight=700)
        library.register_file(outside)

        regular = library.find("Shared", weight=FontWeight.REGULAR)
        bold = library.find("Shared", weight=FontWeight.BOLD)
        assert regular is not None and regular.weight == 400
        assert bold is not None and bold.weight == 700


# ---------------------------------------------------------------- 通用族与目录


class TestGenericFamilies:
    def test_generic_names_expand_to_candidates(self):
        assert (
            "sans-serif" in generic_candidates("sans-serif")[0].lower()
            or len(generic_candidates("sans-serif")) > 1
        )

    def test_mono_alias_matches_monospace(self):
        assert generic_candidates("mono") == generic_candidates("monospace")

    def test_non_generic_name_is_returned_as_is(self):
        assert generic_candidates("Microsoft YaHei") == ("Microsoft YaHei",)

    def test_whitespace_is_tolerated(self):
        assert generic_candidates("  sans-serif  ") == generic_candidates("sans-serif")

    def test_generic_request_resolves_to_a_platform_font(
        self, tmp_path: Path, library: FontLibrary
    ):
        """通用名走平台候选表：把候选里的某个族造出来，就应当命中它。"""
        candidate = generic_candidates("sans-serif")[0]
        build_font(tmp_path / "a.ttf", family=candidate)
        assert library.find("sans-serif") is not None

    def test_default_directories_are_existing_paths(self):
        for directory in default_font_directories():
            assert directory.is_dir()


# ---------------------------------------------------------------- 内嵌兜底字体


class TestEmbeddedFallback:
    """R4.4：内嵌兜底字体——"中文不出豆腐块"从赌运气变成结构保证。

    回退链的终点。系统里一个能用的字体都没有时（精简容器、CI 机器、
    刚装好的系统），中文会渲染成一排豆腐块。有了这份内嵌字体，
    那件事不再取决于用户装了什么。
    """

    def test_the_font_ships_with_the_package(self):
        path = embedded_font_path()
        assert path is not None, "内嵌字体没打进包里——打包配置漏了资源文件"
        assert path.is_file()

    def test_the_license_ships_alongside_it(self):
        """OFL 要求随字体分发许可。缺了它是**许可问题**，不是小事。"""
        path = embedded_font_path()
        assert path is not None
        license_file = path.with_name("OFL.txt")
        assert license_file.is_file(), "OFL.txt 必须和字体一起分发"
        text = license_file.read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE" in text.upper()

    def test_size_is_within_budget(self):
        """docs/18 §R4.4 的体积目标是 < 5MB。超了要么换档要么改文档口径。"""
        path = embedded_font_path()
        assert path is not None
        size_mb = path.stat().st_size / 1e6
        assert size_mb < 5.0, f"内嵌字体 {size_mb:.2f} MB，超出 5MB 目标"

    def test_family_name_matches_the_constant(self):
        path = embedded_font_path()
        assert path is not None
        font = TTFont(str(path), lazy=True)
        family = font["name"].getDebugName(16) or font["name"].getDebugName(1)
        assert family == DEFAULT_FALLBACK_FAMILY

    def test_the_default_library_registers_it(self):
        library = FontLibrary(directories=())
        assert library.stats()["embedded"] == 1
        assert library.has_family(DEFAULT_FALLBACK_FAMILY)

    def test_unknown_families_resolve_to_it(self):
        library = FontLibrary(directories=())
        record = library.resolve(("完全不存在的字体", "也不存在"))
        assert record.family == DEFAULT_FALLBACK_FAMILY
        assert record.is_fallback

    def test_disabling_it_gives_a_truly_empty_library(self):
        """`embedded=False` 必须真的没有兜底——否则"响亮失败"那类测试会永远通过。"""
        library = FontLibrary(directories=(), embedded=False)
        assert library.stats()["embedded"] == 0
        assert library.families() == ()
        with pytest.raises(FontMetricsError):
            library.resolve(("不存在",))

    @pytest.mark.parametrize("char", ["A", "中", "あ", "é", "Ω", "П", "→", "　"])
    def test_multilingual_coverage(self, char: str):
        """t3 口径：拉丁（含中欧）+ 中文 + 日文假名 + 希腊 + 西里尔 + 符号。

        "多语言基本盘几乎免费"——从"只有中文 + ASCII"加到这些只多 0.17MB，
        因为拉丁/希腊/西里尔/假名加起来才 640 个字形。
        """
        path = embedded_font_path()
        assert path is not None
        cmap = TTFont(str(path), lazy=True).getBestCmap()
        assert ord(char) in cmap, f"内嵌字体缺 {char!r}"

    def test_rare_hanzi_are_a_documented_gap(self):
        """生僻字**不在**覆盖范围内——这是文档化的取舍，不是漏了。

        GB2312 收 6763 个常用字；汉字基本区全部 20992 字要 7.31MB（+5.5MB）。
        """
        path = embedded_font_path()
        assert path is not None
        cmap = TTFont(str(path), lazy=True).getBestCmap()
        assert ord("龘") not in cmap

    def test_common_hanzi_coverage_is_complete(self):
        """GB2312 的 6763 个汉字一个都不能少——少一个就是"常用字出豆腐块"。"""
        path = embedded_font_path()
        assert path is not None
        cmap = set(TTFont(str(path), lazy=True).getBestCmap())
        missing = [char for char in _gb2312_hanzi() if ord(char) not in cmap]
        assert not missing, f"缺 {len(missing)} 个 GB2312 汉字，例如 {missing[:5]}"


def _gb2312_hanzi() -> str:
    """GB2312 的全部汉字（与 `tools/build_embedded_font.py` 同一套枚举口径）。"""
    out: list[str] = []
    for high in range(0xA1, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                char = bytes((high, low)).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= char <= "\u9fff":
                out.append(char)
    return "".join(out)


# ---------------------------------------------------------------- 内嵌兜底字体
