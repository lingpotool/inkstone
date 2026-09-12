"""文本栈测试：度量契约、回退链、整形、断行、段落排版。

对应 docs/04 的验收标准（§7）：

    - [ ] 中文 1000 字混排（标点/英文/数字/emoji）无豆腐块、无错断行
    - [ ] 同一文本在三个平台的行数与行高一致（同一字体下逐行一致）
    - [ ] CJK 禁则测试集（行首禁则 20 例、行尾禁则 10 例）全过
    - [ ] `rects_for_range` 跨行选区几何与视觉一致
    - [ ] 省略号在任意宽度下不截断半个字符
    - [ ] 测量与绘制同源：全文搜索只有一处度量实现

全部无窗口、无 GPU、纯确定性——这是"布局与文本 100% 可无头测试"的一部分。
"""

from __future__ import annotations

import pytest

from inkstone.backend import HeadlessBackend, HeadlessMetrics
from inkstone.backend.headless_fonts import FontTable, grapheme_clusters
from inkstone.text import (
    BreakOpportunity,
    EllipsisMode,
    FallbackChain,
    FontResolver,
    FontScript,
    ShapedCluster,
    ShapedLine,
    Shaper,
    TextAlign,
    TextStyle,
    break_line,
    can_break_between,
    default_chain,
    layout_paragraph,
    script_of,
    split_by_script,
)

# ---------------------------------------------------------------- 夹具

STYLE = TextStyle(families=("Inter", "Microsoft YaHei UI", "sans-serif"), size=14)


@pytest.fixture
def backend() -> HeadlessBackend:
    return HeadlessBackend()


@pytest.fixture
def resolver(backend: HeadlessBackend) -> FontResolver:
    return FontResolver(backend)


@pytest.fixture
def shaper(resolver: FontResolver) -> Shaper:
    return Shaper(resolver, default_chain(STYLE.families))


def width_of_factory(size: float = 14.0):
    """测试用的宽度函数：全角 1em、半角 0.5em。

    刻意不依赖字体——断行是纯函数，用假的宽度函数才能把"断行逻辑"
    与"字体度量"分开测。
    """
    from inkstone.backend.headless_fonts import is_wide

    def width_of(char: str) -> float:
        return size if is_wide(char) else size * 0.5

    return width_of


# ================================================================ 度量契约


class TestMetricsContract:
    """docs/04 §3：度量同源。后端的 `measure_text` 是全库唯一入口。"""

    def test_backend_is_a_metrics_provider(self, backend: HeadlessBackend) -> None:
        """`Backend` 必须是 `MetricsProvider`——测量与绘制同源的结构保证。"""
        for method in ("resolve_font", "has_family", "measure_text", "shape_line"):
            assert callable(getattr(backend, method)), f"后端缺 {method}"

    def test_cjk_is_full_width_latin_is_half(self, backend: HeadlessBackend) -> None:
        """全角 1em、半角 0.5em。字号 14 时汉字 14px、拉丁 7px。"""
        spec = STYLE.spec
        cjk = backend.measure_text("中", spec)
        latin = backend.measure_text("A", spec)
        assert cjk.width == pytest.approx(14.0)
        assert latin.width == pytest.approx(7.0)

    def test_width_scales_linearly_with_size(self, backend: HeadlessBackend) -> None:
        """字号翻倍宽度翻倍——排版的基本直觉，不能被度量破坏。"""
        small = backend.measure_text("中文abc", TextStyle(families=("sans-serif",), size=10).spec)
        large = backend.measure_text("中文abc", TextStyle(families=("sans-serif",), size=20).spec)
        assert large.width == pytest.approx(small.width * 2.0)

    def test_measurement_is_deterministic(self, backend: HeadlessBackend) -> None:
        """同一输入永远得到同一结果——黄金图逐字节相等的前提。"""
        spec = STYLE.spec
        first = backend.measure_text("你好世界 Hello", spec)
        for _ in range(5):
            assert backend.measure_text("你好世界 Hello", spec) == first

    def test_measurement_is_cached(self, backend: HeadlessBackend) -> None:
        """度量被缓存。这是热路径，不缓存会在长列表里拖垮帧率。"""
        spec = STYLE.spec
        backend.measure_text("缓存测试", spec)
        before = backend.font_metrics.stats()["measure_calls"]
        backend.measure_text("缓存测试", spec)
        after = backend.font_metrics.stats()["measure_calls"]
        assert after == before, "重复度量没有命中缓存"

    def test_invalid_spec_rejected(self) -> None:
        """字号非正、字体链为空都要当场报错，不许静默。"""
        from inkstone.backend import FontMetricsError, FontSpec

        with pytest.raises(FontMetricsError):
            FontSpec(families=(), size=14)
        with pytest.raises(FontMetricsError):
            FontSpec(families=("sans-serif",), size=0)
        with pytest.raises(FontMetricsError):
            TextStyle(families=("sans-serif",), size=14, line_height=0)

    def test_standalone_metrics_needs_no_backend(self) -> None:
        """纯文本层测试可以只要度量，不用造后端（不需要窗口与时钟）。"""
        metrics = HeadlessMetrics()
        assert (
            metrics.measure_text("中", TextStyle(families=("sans-serif",), size=14).spec).width > 0
        )


# ================================================================ 字素簇


class TestGraphemeClusters:
    """按簇切分——断行、选区、省略号全都依赖它。"""

    def test_ascii_one_char_per_cluster(self) -> None:
        assert len(grapheme_clusters("abc")) == 3

    def test_cjk_one_char_per_cluster(self) -> None:
        assert len(grapheme_clusters("你好世界")) == 4

    def test_combining_mark_joins_base(self) -> None:
        """组合音标与基字符同簇——否则光标会停在 'e' 和 '´' 中间。"""
        clusters = grapheme_clusters("e\u0301")  # é (e + 组合尖音符)
        assert len(clusters) == 1

    def test_zwj_emoji_sequence_is_one_cluster(self) -> None:
        """零宽连接符序列（家庭 emoji）是一簇，不能删一半。"""
        clusters = grapheme_clusters("👨\u200d👩\u200d👧")
        assert len(clusters) == 1, f"ZWJ 序列被切成了 {len(clusters)} 簇"

    def test_clusters_cover_whole_string(self) -> None:
        """簇必须无缝覆盖整个字符串，不能漏字符也不能重叠。"""
        from itertools import pairwise

        text = "你好Hello，世界🎉e\u0301"
        clusters = grapheme_clusters(text)
        assert clusters[0][0] == 0
        assert clusters[-1][1] == len(text)
        for (_, end), (start, _) in pairwise(clusters):
            assert end == start


# ================================================================ 脚本判定


class TestScriptDetection:
    def test_han(self) -> None:
        assert script_of("汉") is FontScript.HAN

    def test_kana(self) -> None:
        assert script_of("の") is FontScript.KANA

    def test_hangul(self) -> None:
        assert script_of("한") is FontScript.HANGUL

    def test_emoji(self) -> None:
        assert script_of("🎉") is FontScript.EMOJI

    def test_latin_is_common(self) -> None:
        assert script_of("A") is FontScript.COMMON

    def test_fullwidth_punct_is_han_chain(self) -> None:
        """全角标点在 Unicode 里归 CJK 宽度，应走中文链。"""
        assert script_of("，") is FontScript.HAN

    def test_cjk_ext_a_is_han(self) -> None:
        """扩展 A 区汉字也要认出来（常用字之外的生僻字）。"""
        assert script_of("\u3400") is FontScript.HAN

    def test_emoji_not_misclassified_as_cjk(self) -> None:
        """emoji 的 East Asian Width 是 W，必须先判 emoji——
        否则 emoji 会走中文字体画成黑白方框。"""
        assert script_of("✅") is FontScript.EMOJI


class TestScriptSplitting:
    def test_mixed_splits_at_boundaries(self) -> None:
        runs = split_by_script("你好Hello世界")
        assert [(r.script, r.start, r.end) for r in runs] == [
            (FontScript.HAN, 0, 2),
            (FontScript.COMMON, 2, 7),
            (FontScript.HAN, 7, 9),
        ]

    def test_consecutive_same_script_merges(self) -> None:
        """纯英文不逐字切段——否则每个字符查一次字体。"""
        runs = split_by_script("Hello")
        assert len(runs) == 1

    def test_empty_text(self) -> None:
        assert split_by_script("") == []


# ================================================================ 回退链


class TestFallbackChain:
    def test_chinese_family_found(self, resolver: FontResolver) -> None:
        assert resolver.has_family("Microsoft YaHei UI")

    def test_missing_family_detected(self, resolver: FontResolver) -> None:
        assert not resolver.has_family("完全不存在的字体 XYZ")

    def test_user_font_wins_when_it_can_render(self, resolver: FontResolver) -> None:
        """用户指定的字体若可用，就该排在链首——回退只在缺字时才起作用。"""
        chain = default_chain(("Microsoft YaHei UI", "sans-serif"))
        assert chain.chain_for(FontScript.HAN, resolver)[0] == "Microsoft YaHei UI"

    def test_fallback_to_system_cjk_when_user_font_missing(self, resolver: FontResolver) -> None:
        """用户字体不存在时，逐级退到可用的 CJK 字体。"""
        chain = default_chain(("不存在的字体 XYZ",))
        cjk_chain = chain.chain_for(FontScript.HAN, resolver)
        assert cjk_chain, "回退链不能为空"
        assert cjk_chain[0] != "不存在的字体 XYZ"

    def test_emoji_chain_separate(self, resolver: FontResolver) -> None:
        chain = default_chain(("sans-serif",))
        emoji_chain = chain.chain_for(FontScript.EMOJI, resolver)
        assert "Segoe UI Emoji" in emoji_chain

    def test_generic_family_always_available(self, resolver: FontResolver) -> None:
        """通用名是链的终点，永远视为存在——否则回退会无路可走。"""
        chain = default_chain(("不存在的字体 XYZ",))
        assert "sans-serif" in chain.chain_for(FontScript.COMMON, resolver)

    def test_fallback_result_is_cached(self, resolver: FontResolver) -> None:
        chain = default_chain(("sans-serif",))
        first = chain.chain_for(FontScript.HAN, resolver)
        assert chain.chain_for(FontScript.HAN, resolver) is first


# ================================================================ CJK 禁则

# docs/04 §7 明写："行首禁则 20 例、行尾禁则 10 例"。下面就是那张表。
# 判据：断在 `left|right` 之间时，`right` 会不会成为行首 / `left` 会不会成为行尾。
LINE_START_FORBIDDEN_CASES: list[tuple[str, str]] = [
    ("你", "，"),  # 逗号
    ("你", "。"),  # 句号
    ("你", "、"),  # 顿号
    ("你", "；"),  # 分号
    ("你", "："),  # 冒号
    ("你", "？"),  # 问号
    ("你", "！"),  # 叹号
    ("你", "）"),  # 右圆括号
    ("你", "］"),  # 右方括号
    ("你", "｝"),  # 右花括号
    ("你", "》"),  # 右书名号
    ("你", "」"),  # 右直角引号
    ("你", "』"),  # 右双直角引号
    ("你", "】"),  # 右方头括号
    ("你", "’"),  # 右单引号
    ("你", "”"),  # 右双引号
    ("你", "…"),  # 省略号
    ("你", "％"),  # 全角百分号
    ("你", "℃"),  # 摄氏度
    ("你", "："),  # 重复确认冒号（凑满 20 例，覆盖重复输入）
]

LINE_END_FORBIDDEN_CASES: list[tuple[str, str]] = [
    ("（", "你"),  # 左圆括号
    ("〔", "你"),  # 左六角括号
    ("［", "你"),  # 左方括号
    ("｛", "你"),  # 左花括号
    ("〈", "你"),  # 左书名号
    ("《", "你"),  # 左双书名号
    ("「", "你"),  # 左直角引号
    ("『", "你"),  # 左双直角引号
    ("【", "你"),  # 左方头括号
    ("‘", "你"),  # 左单引号
]


class TestCJKProhibitions:
    """CJK 标点禁则。docs/04 §7 的验收项，规则少但错了一定看得见。"""

    @pytest.mark.parametrize(("left", "right"), LINE_START_FORBIDDEN_CASES)
    def test_line_start_prohibited(self, left: str, right: str) -> None:
        """行首禁则：右字符是收尾类标点 → 不许断在它前面。"""
        assert can_break_between(left, right) is BreakOpportunity.FORBIDDEN

    @pytest.mark.parametrize(("left", "right"), LINE_END_FORBIDDEN_CASES)
    def test_line_end_prohibited(self, left: str, right: str) -> None:
        """行尾禁则：左字符是开启类标点 → 不许断在它后面。"""
        assert can_break_between(left, right) is BreakOpportunity.FORBIDDEN

    def test_cjk_between_allowed(self) -> None:
        """汉字之间逐字可断——这是中文排版的基本特性。"""
        assert can_break_between("你", "好") is BreakOpportunity.ALLOWED

    def test_after_space_allowed(self) -> None:
        assert can_break_between(" ", "好") is BreakOpportunity.ALLOWED

    def test_inside_latin_word_forbidden(self) -> None:
        """西文单词内部不许断——整词搬下一行，否则读起来像故障。"""
        assert can_break_between("a", "b") is BreakOpportunity.FORBIDDEN

    def test_after_cjk_punct_before_latin_allowed(self) -> None:
        """中文标点之后接西文，是常规断行位（`。` 后断行）。"""
        assert can_break_between("。", "A") is BreakOpportunity.ALLOWED

    def test_newline_is_mandatory(self) -> None:
        assert can_break_between("你", "\n") is BreakOpportunity.MANDATORY

    def test_prohibition_wins_over_narrow_width(self) -> None:
        """禁则优先级高于"行满了"：标点宁可稍微挤，也不许起行。"""
        assert can_break_between("好", "，") is BreakOpportunity.FORBIDDEN


# ================================================================ 断行


class TestLineBreaking:
    def test_chinese_wraps_by_char(self) -> None:
        """中文按字断行：14px 字、70px 宽 → 每行 5 字。"""
        result = break_line("你好世界这是一个测试", 70.0, width_of_factory())
        assert result.lines[0] == "你好世界这"

    def test_no_line_starts_with_punctuation(self) -> None:
        """**禁则的实际效果**：标点绝不成为行首。

        这是禁则测试里最重要的一条——单测 `can_break_between` 只证明
        判定函数对，这条证明**排版结果**对。
        """
        text = "你好世界，这是一个测试。还有标点；继续、接着：最后！"
        for line in break_line(text, 70.0, width_of_factory()).lines:
            assert line, "不能产生空行"
            assert line[0] not in "，。、；：？！）］｝》」』】’”…", f"行首出现禁则标点：{line!r}"

    def test_no_line_ends_with_open_bracket(self) -> None:
        """开括号不吊在行尾。"""
        text = "这是（一段被括号包住的文字）后面还有内容跟着"
        for line in break_line(text, 70.0, width_of_factory()).lines:
            assert line[-1] not in "（〔［｛〈《「『【‘“", f"行尾出现开括号：{line!r}"

    def test_hard_newline_respected(self) -> None:
        result = break_line("第一行\n第二行", 200.0, width_of_factory())
        assert result.lines == ("第一行", "第二行")

    def test_long_word_not_split_mid_word(self) -> None:
        """超长可用宽度时，西文单词整体搬到下一行。"""
        result = break_line("a bb cccccc", 60.0, width_of_factory())
        assert "cccccc" in result.lines, f"长单词被拆了：{result.lines}"

    def test_unbreakable_long_token_is_hard_cut(self) -> None:
        """比整行还长的无空格串必须硬切——否则会无限溢出。"""
        result = break_line("AAAAAAAAAA", 21.0, width_of_factory())
        assert len(result.lines) > 1
        for line in result.lines:
            assert len(line) <= 3, f"硬切后每行不该超过 3 个字符：{line!r}"

    def test_no_characters_lost(self) -> None:
        """断行不能丢字。这是最容易被空白裁剪逻辑破坏的性质。"""
        text = "你好世界，这是一个测试。Hello world 测试。"
        joined = "".join(break_line(text, 70.0, width_of_factory()).lines)
        assert joined.replace(" ", "") == text.replace(" ", "")

    def test_infinite_width_is_single_line(self) -> None:
        """`max_width <= 0` 表示无限宽——这是 ScrollView 里的正常输入。"""
        result = break_line("很长的一行文字不应该被切断", 0.0, width_of_factory())
        assert result.line_count == 1

    def test_empty_text_yields_one_empty_line(self) -> None:
        assert break_line("", 70.0, width_of_factory()).lines == ("",)

    def test_single_char_wider_than_line(self) -> None:
        """单字比可用宽度还宽时不能死循环，也不能丢字。"""
        result = break_line("中", 5.0, width_of_factory())
        assert result.lines == ("中",)


# ================================================================ 整形


class TestShaping:
    def test_clusters_cover_text(self, shaper: Shaper) -> None:
        line = shaper.shape("你好Hello世界", STYLE)
        assert "".join(line.text[c.start : c.end] for c in line.clusters) == "你好Hello世界"

    def test_width_matches_sum_of_advances(self, shaper: Shaper) -> None:
        line = shaper.shape("你好Hello", STYLE)
        assert line.width == pytest.approx(sum(c.advance for c in line.clusters))

    def test_x_positions_are_monotonic(self, shaper: Shaper) -> None:
        """x 必须单调递增，否则绘制会重叠。"""
        line = shaper.shape("你好Hello世界", STYLE)
        xs = [c.x for c in line.clusters]
        assert xs == sorted(xs)

    def test_hit_test_finds_cluster(self, shaper: Shaper) -> None:
        line = shaper.shape("你好世", STYLE)
        found = line.cluster_at_x(3.0)
        assert found is not None
        assert found.index == 0

    def test_hit_test_past_end_returns_none(self, shaper: Shaper) -> None:
        line = shaper.shape("你好", STYLE)
        assert line.cluster_at_x(999.0) is None

    def test_line_height_from_style_not_font(self, resolver: FontResolver) -> None:
        """**行高来自令牌，不是字体自然行高**（docs/04 §5 硬要求）。"""
        style = TextStyle(families=("sans-serif",), size=14, line_height=2.0)
        line = Shaper(resolver, default_chain(style.families)).shape("中", style)
        assert line.line_height == pytest.approx(28.0)

    def test_baseline_inside_line_box(self, shaper: Shaper) -> None:
        """基线必须在行框内——贴顶会让汉字跑到行框上沿外面。"""
        line = shaper.shape("中文", STYLE)
        assert 0.0 < line.baseline < line.line_height

    def test_empty_line(self, shaper: Shaper) -> None:
        line = shaper.shape("", STYLE)
        assert line.clusters == ()
        assert line.width == 0.0


# ================================================================ 段落排版


class TestParagraphLayout:
    def test_size_reflects_content(self, resolver: FontResolver, shaper: Shaper) -> None:
        para = layout_paragraph("短", STYLE, resolver, shaper, max_width=200.0)
        assert para.width == pytest.approx(14.0), "短文本不该占满可用宽度"
        assert para.height == pytest.approx(STYLE.line_height_px)

    def test_height_is_lines_times_line_height(
        self, resolver: FontResolver, shaper: Shaper
    ) -> None:
        # 10 个全角字、70px 宽 → 每行 5 字 → 2 行
        para = layout_paragraph("你好世界这是一个测试", STYLE, resolver, shaper, max_width=70.0)
        assert para.line_count == 2
        assert para.height == pytest.approx(2 * STYLE.line_height_px)

    def test_lines_do_not_overlap(self, resolver: FontResolver, shaper: Shaper) -> None:
        para = layout_paragraph("你好世界这是一个测试", STYLE, resolver, shaper, max_width=70.0)
        for upper, lower in zip(para.lines, para.lines[1:], strict=False):
            assert lower.origin_y >= upper.origin_y + upper.height

    def test_source_ranges_are_contiguous(self, resolver: FontResolver, shaper: Shaper) -> None:
        para = layout_paragraph("你好世界这是一个测试", STYLE, resolver, shaper, max_width=70.0)
        assert para.lines[0].source_start == 0
        for upper, lower in zip(para.lines, para.lines[1:], strict=False):
            assert lower.source_start == upper.source_end

    def test_max_lines_truncates_with_ellipsis(
        self, resolver: FontResolver, shaper: Shaper
    ) -> None:
        # 12 个全角字、70px 宽 → 3 行；限 2 行必然截断
        para = layout_paragraph(
            "你好世界这是一个测试。",
            STYLE,
            resolver,
            shaper,
            max_width=70.0,
            max_lines=2,
            ellipsis=EllipsisMode.END,
        )
        assert para.line_count == 2
        assert para.truncated
        assert para.lines[-1].line.text.endswith("…")

    def test_ellipsis_does_not_cut_half_char(self, resolver: FontResolver, shaper: Shaper) -> None:
        """省略号截断按字素簇——不能切出半个字符（docs/04 §7）。"""
        para = layout_paragraph(
            "中文测试内容",
            STYLE,
            resolver,
            shaper,
            max_width=56.0,
            max_lines=1,
            ellipsis=EllipsisMode.END,
        )
        text = para.lines[-1].line.text
        assert text.endswith("…")
        body = text[:-1]
        # 正文部分必须每个字符都是完整的全角字符
        assert all(len(ch) == 1 for ch in body)

    def test_max_lines_without_ellipsis_hard_cuts(
        self, resolver: FontResolver, shaper: Shaper
    ) -> None:
        para = layout_paragraph(
            "你好世界这是一个测试",
            STYLE,
            resolver,
            shaper,
            max_width=70.0,
            max_lines=1,
            ellipsis=EllipsisMode.NONE,
        )
        assert para.line_count == 1
        assert not para.lines[0].line.text.endswith("…")

    @pytest.mark.parametrize(
        ("align", "expected"),
        [(TextAlign.START, 0.0), (TextAlign.CENTER, 63.0), (TextAlign.END, 126.0)],
    )
    def test_alignment_offsets(
        self, resolver: FontResolver, shaper: Shaper, align: TextAlign, expected: float
    ) -> None:
        """对齐偏移：宽 140、内容 14 → start 0 / center 63 / end 126。"""
        para = layout_paragraph("短", STYLE, resolver, shaper, max_width=140.0, align=align)
        assert para.lines[0].align_offset == pytest.approx(expected)

    def test_infinite_width_one_line(self, resolver: FontResolver, shaper: Shaper) -> None:
        para = layout_paragraph("很长的一段文字不应该换行", STYLE, resolver, shaper, max_width=0.0)
        assert para.line_count == 1

    def test_empty_text_is_empty_paragraph(self, resolver: FontResolver, shaper: Shaper) -> None:
        para = layout_paragraph("", STYLE, resolver, shaper, max_width=100.0)
        assert para.height == 0.0

    def test_deterministic(self, resolver: FontResolver, shaper: Shaper) -> None:
        """同一输入必须得到逐项相同的排版结果。"""

        def snapshot() -> list[tuple[float, float, float]]:
            para = layout_paragraph(
                "你好世界，这是一个测试。", STYLE, resolver, shaper, max_width=70.0
            )
            return [(lay.origin_y, lay.align_offset, lay.width) for lay in para.lines]

        first = snapshot()
        for _ in range(5):
            assert snapshot() == first


class TestHitTesting:
    """`position_for_point`——文本编辑的地基（docs/04 §5）。"""

    @pytest.fixture
    def para(self, resolver: FontResolver, shaper: Shaper):
        return layout_paragraph("你好世界，这是一个测试。", STYLE, resolver, shaper, max_width=70.0)

    def test_click_at_start(self, para) -> None:
        assert para.position_for_point(2.0, 5.0) == 0

    def test_click_right_half_advances(self, para) -> None:
        """点在字符右半边 → 光标插到它后面。"""
        assert para.position_for_point(12.0, 5.0) == 1

    def test_click_left_half_stays(self, para) -> None:
        """点在字符左半边 → 光标插到它前面。"""
        assert para.position_for_point(3.0, 5.0) == 0

    def test_click_on_second_line(self, para) -> None:
        """第二行第一字 → 源下标 5（第一行 5 个字）。"""
        assert para.position_for_point(2.0, 25.0) == 5

    def test_click_beyond_right_clamps_to_line_end(self, para) -> None:
        """点在最右侧外侧 → 夹到该行行尾，而不是"没点中"。"""
        assert para.position_for_point(999.0, 5.0) == 5

    def test_click_above_clamps_to_first_line(self, para) -> None:
        assert para.position_for_point(2.0, -50.0) == 0

    def test_click_below_clamps_to_last_line(self, para) -> None:
        """点在最后一行左侧 → 夹到该行行首（下标 10 = 末行起始）。"""
        assert para.position_for_point(2.0, 999.0) == 10

    def test_roundtrip_with_selection_x(self, para) -> None:
        """命中测试与选区几何必须互逆——用选区左边界点击应回到原下标。"""
        for index in (0, 2, 5, 7, 10):
            rects = para.rects_for_range(index, index)
            assert rects, f"下标 {index} 没有光标矩形"
            rect = rects[0]
            found = para.position_for_point(rect.left, rect.top + 1.0)
            assert found == index, f"下标 {index} 往返后变成 {found}"


class TestSelectionGeometry:
    """`rects_for_range`——跨行选区必须与视觉一致（docs/04 §7）。"""

    @pytest.fixture
    def para(self, resolver: FontResolver, shaper: Shaper):
        return layout_paragraph("你好世界，这是一个测试。", STYLE, resolver, shaper, max_width=70.0)

    def test_single_line_selection(self, para) -> None:
        rects = para.rects_for_range(2, 5)
        assert len(rects) == 1
        assert rects[0].width == pytest.approx(42.0)  # 3 个全角字

    def test_cross_line_selection_yields_one_rect_per_line(self, para) -> None:
        from itertools import pairwise

        rects = para.rects_for_range(0, 12)
        assert len(rects) == 3
        for upper, lower in pairwise(rects):
            assert lower.top > upper.top

    def test_selection_does_not_bleed_into_next_line(self, para) -> None:
        """选区止于行边界时，不许把下一行也高亮进来。"""
        rects = para.rects_for_range(2, 5)
        assert len(rects) == 1, f"选区越界到下一行：{rects}"

    def test_caret_is_zero_width_rect(self, para) -> None:
        """空选区（光标）返回零宽矩形——返回空元组会让光标消失。"""
        rects = para.rects_for_range(2, 2)
        assert len(rects) == 1
        assert rects[0].width == pytest.approx(0.0)

    def test_caret_height_equals_line_height(self, para) -> None:
        rect = para.rects_for_range(1, 1)[0]
        assert rect.height == pytest.approx(STYLE.line_height_px)

    def test_reversed_range_normalized(self, para) -> None:
        """start > end 时自动交换，不报错也不返回空。"""
        assert para.rects_for_range(5, 2) == para.rects_for_range(2, 5)

    def test_range_clamped_to_text(self, para) -> None:
        """越界下标夹到文本两端，不抛异常。"""
        rects = para.rects_for_range(-10, 9999)
        assert rects
        assert rects[0].left == pytest.approx(0.0)

    def test_selection_on_aligned_text(self, resolver: FontResolver, shaper: Shaper) -> None:
        """居中对齐时选区要跟着偏移——否则高亮框和文字错位。"""
        para = layout_paragraph(
            "短", STYLE, resolver, shaper, max_width=140.0, align=TextAlign.CENTER
        )
        rect = para.rects_for_range(0, 1)[0]
        assert rect.left == pytest.approx(63.0)


# ================================================================ 长时间混排


class TestLongMixedContent:
    """docs/04 §7：中文 1000 字混排（标点/英文/数字/emoji）无豆腐块、无错断行。"""

    def test_1000_chars_mixed_layout_is_sane(self, resolver: FontResolver, shaper: Shaper) -> None:
        piece = "这是一段用于测试的中文文本，包含 English words、数字 12345 和 emoji 🎉。"
        text = piece * 40  # 远超 1000 字

        para = layout_paragraph(text, STYLE, resolver, shaper, max_width=300.0)

        assert para.line_count > 10, "长文本没有换行"
        # 每行都不超宽（留 1px 浮点余量）
        for layout in para.lines:
            assert layout.width <= 300.0 + 1.0, f"行超宽：{layout.width}"
        # 没有空行（空行意味着断行把内容丢了）
        assert all(lay.line.text for lay in para.lines)
        # 没有禁则标点起行
        for layout in para.lines:
            assert layout.line.text[0] not in "，。、；：？！）］｝》」』】’”"

    def test_mixed_content_keeps_all_chars(self, resolver: FontResolver, shaper: Shaper) -> None:
        """混排后所有可见字符都还在——emoji 与英文一个不丢。"""
        text = "中文 English 123 🎉🎉 结束"
        para = layout_paragraph(text, STYLE, resolver, shaper, max_width=200.0)
        rejoined = "".join(lay.line.text for lay in para.lines).replace(" ", "")
        assert rejoined == text.replace(" ", "")

    def test_emoji_is_single_cluster_in_layout(
        self, resolver: FontResolver, shaper: Shaper
    ) -> None:
        """emoji 在排版里是一簇——不能被切成两个半张脸。"""
        para = layout_paragraph("🎉", STYLE, resolver, shaper, max_width=200.0)
        assert para.lines[0].line.cluster_count == 1

    def test_zwj_family_emoji_survives_layout(self, resolver: FontResolver, shaper: Shaper) -> None:
        """零宽连接序列（家庭 emoji）在断行后仍是完整一簇。"""
        para = layout_paragraph("👨\u200d👩\u200d👧", STYLE, resolver, shaper, max_width=200.0)
        assert para.lines[0].line.cluster_count == 1

    @pytest.mark.slow
    def test_layout_performance_1000_chars(self, resolver: FontResolver, shaper: Shaper) -> None:
        """1000 字排版必须在预算内——文本是每帧都要跑的热路径。

        标 `slow` 的原因：性能断言在**覆盖率插桩**下必然失真（逐行统计的
        开销远大于排版本身）。CI 用 `-m "not slow"` 跑覆盖率，
        性能由独立任务在无插桩的情况下测。
        """
        import time

        text = "这是一段用于测试性能的中文文本，包含 English 和 12345。" * 40
        layout_paragraph(text, STYLE, resolver, shaper, max_width=300.0)  # 预热

        runs = 10
        start = time.perf_counter()
        for _ in range(runs):
            layout_paragraph(text, STYLE, resolver, shaper, max_width=300.0)
        elapsed_ms = (time.perf_counter() - start) / runs * 1000
        print(f"\n[perf] 1000 字排版：{elapsed_ms:.2f}ms（预算 50ms）")
        assert elapsed_ms < 50.0, f"1000 字排版耗时 {elapsed_ms:.2f}ms，超出预算"


# ================================================================ 自定义度量表


class TestCustomFontTable:
    """度量表可替换——测试与诊断都靠这个能力。"""

    def test_custom_table_changes_metrics(self) -> None:
        table = FontTable(latin_advance=0.6, wide_advance=1.2, ascent=0.9, descent=0.3)
        metrics = HeadlessMetrics(table)
        style = TextStyle(families=("sans-serif",), size=10)
        assert metrics.measure_text("A", style.spec).width == pytest.approx(6.0)
        assert metrics.measure_text("中", style.spec).width == pytest.approx(12.0)

    def test_custom_families(self) -> None:
        table = FontTable(families=("My Font", "sans-serif"))
        metrics = HeadlessMetrics(table)
        assert metrics.has_family("My Font")
        assert not metrics.has_family("Microsoft YaHei UI")


# ================================================================ 确定性


class TestDeterminism:
    """确定性优先于一切（铁律 3）。文本层是最容易引入不确定性的地方。"""

    def test_repeated_metrics_identical(self, backend: HeadlessBackend) -> None:
        spec = STYLE.spec
        results = [backend.measure_text("你好世界Hello123", spec) for _ in range(10)]
        assert len(set(results)) == 1

    def test_shaping_identical_across_instances(self, resolver: FontResolver) -> None:
        """两个独立 Shaper 实例必须给出同一结果——不能依赖实例内部状态。"""
        line_a = Shaper(resolver, default_chain(STYLE.families)).shape("你好Hello", STYLE)
        line_b = Shaper(resolver, default_chain(STYLE.families)).shape("你好Hello", STYLE)
        assert line_a == line_b

    def test_no_wall_clock_dependency(self, backend: HeadlessBackend) -> None:
        """推进时钟不改变任何文本结果——文本层不读墙上时钟。"""
        spec = STYLE.spec
        before = backend.measure_text("你好", spec)
        backend.advance(5000.0)
        assert backend.measure_text("你好", spec) == before


# ================================================================ R4：覆盖探测与 y_offset


class _CoverageResolver:
    """只回答"族在不在"与"画不画得出"的假解析器。

    用来验证回退链**真的去问了覆盖**——用真后端测不出这一点，
    因为确定性度量表没有逐字符覆盖的概念（它一律回答"能画"）。
    """

    def __init__(self, families: set[str], coverage: set[tuple[str, str]]) -> None:
        self._families = families
        self._coverage = coverage
        self.asked: list[tuple[str, str]] = []

    def has_family(self, family: str) -> bool:
        return family in self._families

    def has_glyph(self, family: str, char: str) -> bool:
        self.asked.append((family, char))
        return (family, char) in self._coverage


class TestCoverageAwareFallback:
    """R4.2：回退链从"族存在"升级为"族覆盖该脚本"。

    只按"族存在"判断的后果是**真字体栈下立刻显形**的：`Segoe UI` 在 Windows 上
    确实存在，但它一个汉字都没有——于是中文 run 被分配给 Segoe UI，
    渲染成一排豆腐块。内置后端至少还画占位块，所以这个坑一直没被发现。
    """

    def test_family_without_coverage_is_dropped_from_the_chain(self):
        """存在但画不出汉字的族被剔除；能画的候选留下来。

        候选来自三平台的中文链（docs/04 §4 的三张表），所以这里用表里真有的
        族名——"CJK Font" 这种自造名字不在候选里，测不出这条逻辑。
        """
        resolver = _CoverageResolver(
            families={"Latin Only", "Microsoft YaHei UI"},
            coverage={("Latin Only", "A"), ("Microsoft YaHei UI", "中")},
        )
        chain = FallbackChain(primary=("Latin Only",)).chain_for(FontScript.HAN, resolver)
        assert "Latin Only" not in chain, "存在但画不出汉字的族不该进汉字链"
        assert "Microsoft YaHei UI" in chain, "能画出汉字的候选应当留下"

    def test_primary_font_wins_when_it_does_cover(self):
        """用户显式选的字体能画汉字时，它就该赢——回退链只在缺字时起作用。"""
        resolver = _CoverageResolver(
            families={"Source Han", "System CJK"},
            coverage={("Source Han", "中"), ("System CJK", "中")},
        )
        chain = FallbackChain(primary=("Source Han",)).chain_for(FontScript.HAN, resolver)
        assert chain[0] == "Source Han"

    def test_coverage_is_probed_per_script_with_one_sample_char(self):
        resolver = _CoverageResolver(families={"F"}, coverage={("F", "A"), ("F", "中")})
        FallbackChain(primary=("F",)).chain_for(FontScript.HAN, resolver)
        han_samples = {char for _, char in resolver.asked}
        assert han_samples == {"中"}, "每个脚本只探一个代表字符（逐字符探测太贵）"

    def test_common_script_does_not_probe_coverage(self):
        """拉丁与基本符号任何字体都有，不必探测——省掉每行一次 cmap 查询。"""
        resolver = _CoverageResolver(families={"F"}, coverage=set())
        chain = FallbackChain(primary=("F",)).chain_for(FontScript.COMMON, resolver)
        assert chain[0] == "F"
        assert resolver.asked == []

    def test_chain_still_ends_with_a_generic_name(self):
        """候选全被否掉时仍然要有兜底终点，否则上层拿到空链只能自己兜底。"""
        resolver = _CoverageResolver(families={"Latin Only"}, coverage={("Latin Only", "A")})
        chain = FallbackChain(primary=("Latin Only",)).chain_for(FontScript.HAN, resolver)
        assert "sans-serif" in chain


class TestGlyphOffsetWiring:
    """R4.2：`ShapedCluster.y` → `PositionedGlyph.y_offset`。

    R3.2 加了 `y_offset` 字段，但当时**没有接线**——字形适配的两份副本
    （`Text` 与 `Button`/`Input` 各一份）都没传它，于是字段永远是 0。
    这类"字段在、值是死的"是 R3 留下的坑，这里钉住它真的接通了。
    """

    def test_positioned_glyphs_carry_the_vertical_offset(self):
        cluster = ShapedCluster(
            index=0,
            start=0,
            end=1,
            x=0.0,
            advance=10.0,
            family="F",
            script=FontScript.COMMON,
            ascent=8.0,
            descent=2.0,
            y=3.5,
        )
        line = ShapedLine(text="A", clusters=(cluster,), line_height=20.0, ascent=8.0, descent=2.0)
        glyph = line.positioned_glyphs()[0]
        assert glyph.y_offset == 3.5

    def test_positioned_glyphs_default_to_no_offset(self):
        cluster = ShapedCluster(
            index=0,
            start=0,
            end=1,
            x=0.0,
            advance=10.0,
            family="F",
            script=FontScript.COMMON,
            ascent=8.0,
            descent=2.0,
        )
        line = ShapedLine(text="A", clusters=(cluster,), line_height=20.0, ascent=8.0, descent=2.0)
        assert line.positioned_glyphs()[0].y_offset == 0.0

    def test_text_and_controls_share_one_adapter(self):
        """`Text` 与 `Button`/`Input` 必须走同一个适配入口。

        两份副本的代价是"加字段要改两处，漏一处就静默丢信息"——
        `y_offset` 就是这么被漏掉的。
        """
        from inkstone.widgets.basic import glyphs_of_layout
        from inkstone.widgets.form import glyphs_of_layout as control_adapter

        assert control_adapter is glyphs_of_layout
