"""颜色、设计令牌与主题的单元测试。

最有分量的一组测试是 `TestContrastCompliance`：
把 docs/13 §9 的验收标准（"明暗两版全部语义令牌对比度 ≥ WCAG 2.2 AA"）
变成机器可执行的断言。颜色值是我调的，**对比度是算出来的**——
靠眼睛保证对比度，迟早会看走眼。
"""

import pytest

from inkstone.gfx.color import Color
from inkstone.style import (
    DARK_SEMANTIC,
    DEFAULT_RAW,
    LIGHT_SEMANTIC,
    RAMP_STEPS,
    Theme,
    ThemeMode,
    TokenError,
    contrast_issues,
)


class TestColor:
    def test_from_hex_6_digits(self):
        assert Color.from_hex("#0F172A") == Color(15, 23, 42)

    def test_from_hex_8_digits_carries_alpha(self):
        color = Color.from_hex("#0F172A80")
        assert color.a == pytest.approx(128 / 255)

    def test_from_hex_rejects_bad_input(self):
        for bad in ("#FFF", "12345", "GGGGGG", ""):
            with pytest.raises(ValueError):
                Color.from_hex(bad)

    def test_channel_bounds_are_enforced(self):
        with pytest.raises(ValueError):
            Color(300, 0, 0)
        with pytest.raises(ValueError):
            Color(0, 0, 0, 1.5)

    def test_to_hex_round_trip(self):
        assert Color.from_hex("#4F46E5").to_hex() == "#4F46E5"
        assert Color(15, 23, 42, 0.5).to_hex(include_alpha=True) == "#0F172A80"

    def test_with_alpha(self):
        base = Color.from_hex("#000000")
        assert base.with_alpha(0.25).a == pytest.approx(0.25)

    def test_lerp_endpoints(self):
        a = Color(0, 0, 0)
        b = Color(255, 255, 255)
        assert a.lerp(b, 0.0) == a
        assert a.lerp(b, 1.0) == b
        assert a.lerp(b, 0.5) == Color(128, 128, 128)

    def test_contrast_ratio_extremes(self):
        black = Color(0, 0, 0)
        white = Color(255, 255, 255)
        assert black.contrast_ratio(white) == pytest.approx(21.0, abs=0.1)
        assert black.contrast_ratio(black) == pytest.approx(1.0)

    def test_meets_aa_thresholds(self):
        white = Color(255, 255, 255)
        black = Color(0, 0, 0)
        mid_gray = Color(128, 128, 128)  # 对白约 3.9:1

        assert black.meets_aa(white)
        assert not mid_gray.meets_aa(white)
        assert mid_gray.meets_aa(white, large_text=True)  # 大字只要 3:1


class TestRawTokens:
    def test_ramps_have_twelve_steps(self):
        assert tuple(DEFAULT_RAW.neutral) == RAMP_STEPS
        assert tuple(DEFAULT_RAW.brand) == RAMP_STEPS

    def test_ramp_is_monotonically_darkening(self):
        """色板必须从浅到深单调递减亮度，否则'取 600 比取 400 深'就没了意义。"""
        from itertools import pairwise

        for ramp in (DEFAULT_RAW.neutral, DEFAULT_RAW.brand):
            luminances = [ramp[step].relative_luminance() for step in RAMP_STEPS]
            for lighter, darker in pairwise(luminances):
                assert lighter >= darker, (
                    f"色板不是单调变深的：{[f'{lum:.3f}' for lum in luminances]}"
                )

    def test_spacing_follows_8pt_grid(self):
        for name, value in DEFAULT_RAW.spacing.items():
            assert value % 4 == 0, f"spacing[{name!r}]={value} 不在 8pt 网格上"

    def test_spacing_values_match_spec(self):
        assert DEFAULT_RAW.spacing["xs"] == 4
        assert DEFAULT_RAW.spacing["sm"] == 8
        assert DEFAULT_RAW.spacing["md"] == 12
        assert DEFAULT_RAW.spacing["lg"] == 16
        assert DEFAULT_RAW.spacing["xl"] == 24
        assert DEFAULT_RAW.spacing["4xl"] == 64

    def test_radius_scale(self):
        assert DEFAULT_RAW.radius["sm"] == 6
        assert DEFAULT_RAW.radius["md"] == 10
        assert DEFAULT_RAW.radius["lg"] == 14
        assert DEFAULT_RAW.radius["xl"] == 20
        assert DEFAULT_RAW.radius["pill"] == float("inf")

    def test_type_scale_matches_spec(self):
        scale = DEFAULT_RAW.type_scale
        assert scale["md"].px == 14
        assert scale["md"].line_height == pytest.approx(1.6)
        assert scale["2xl"].px == 24
        # 中文正文行高不低于 1.4（docs/13 §3.1）
        for size in scale.values():
            assert size.line_height >= 1.25

    def test_only_two_font_weights(self):
        assert DEFAULT_RAW.weight_regular == 400
        assert DEFAULT_RAW.weight_medium == 500

    def test_control_heights_match_spec(self):
        heights = DEFAULT_RAW.control_heights
        assert [heights[k] for k in ("xs", "sm", "md", "lg", "xl")] == [24, 28, 36, 44, 52]

    def test_shadow_levels_ascend(self):
        shadows = DEFAULT_RAW.shadows
        assert shadows["e0"] is None
        previous = 0.0
        for level in ("e1", "e2", "e3", "e4"):
            assert shadows[level] is not None
            assert shadows[level].offset_y > previous, f"{level} 应比上一级更深"
            previous = shadows[level].offset_y

    def test_motion_durations_are_capped(self):
        for name, ms in DEFAULT_RAW.motion_durations.items():
            assert ms <= 500, f"动效 {name}={ms}ms 超过 500ms 上限"

    def test_easings_cover_enter_exit_and_springs(self):
        easings = DEFAULT_RAW.easings
        assert "ease-out-expo" in easings
        assert "ease-in-out-quart" in easings
        assert easings["spring-default"].stiffness == 170


class TestSemanticTokens:
    def test_dark_surface_is_lighter_than_bg(self):
        """暗色不是反色：面要比底亮，层级靠这个（docs/13 §2.3）。"""
        dark = DARK_SEMANTIC
        assert dark.surface.relative_luminance() > dark.bg.relative_luminance()
        assert dark.surface_alt.relative_luminance() > dark.surface.relative_luminance()

    def test_light_surface_is_lighter_than_text(self):
        light = LIGHT_SEMANTIC
        assert light.text.relative_luminance() < light.surface.relative_luminance()

    def test_market_colors_follow_chinese_convention_by_default(self):
        """涨红跌绿是中文惯例的默认值，不是硬编码（docs/13 §2.4）。"""
        assert LIGHT_SEMANTIC.market_up.to_hex() == "#DC2626"
        assert LIGHT_SEMANTIC.market_down.to_hex() == "#15803D"

    def test_dark_and_light_are_genuinely_different_mappings(self):
        assert LIGHT_SEMANTIC.primary != DARK_SEMANTIC.primary
        assert LIGHT_SEMANTIC.text != DARK_SEMANTIC.text


class TestContrastCompliance:
    """docs/13 §9 的验收标准：明暗两版全部语义令牌 ≥ WCAG 2.2 AA。"""

    def test_light_theme_passes_aa(self):
        issues = contrast_issues(LIGHT_SEMANTIC)
        assert issues == [], "亮色主题存在对比度问题：\n  " + "\n  ".join(issues)

    def test_dark_theme_passes_aa(self):
        issues = contrast_issues(DARK_SEMANTIC)
        assert issues == [], "暗色主题存在对比度问题：\n  " + "\n  ".join(issues)

    def test_checker_actually_catches_bad_tokens(self):
        """守卫本身要被测：把 text 改成浅灰色，检查器必须报出来。"""
        import dataclasses

        bad = dataclasses.replace(LIGHT_SEMANTIC, text=Color.from_hex("#CBD5E1"))
        issues = contrast_issues(bad)
        assert issues, "对比度检查器没有发现明显的违规"
        assert any("text 对" in issue for issue in issues)


class TestTheme:
    def test_light_and_dark_factories(self):
        light = Theme.light()
        dark = Theme.dark()
        assert light.mode is ThemeMode.LIGHT
        assert dark.is_dark
        assert not light.is_dark

    def test_color_lookup_uses_hyphenated_names(self):
        theme = Theme.light()
        assert theme.color("surface") == LIGHT_SEMANTIC.surface
        assert theme.color("text-dim") == LIGHT_SEMANTIC.text_dim
        assert theme.color("text_dim") == LIGHT_SEMANTIC.text_dim  # 下划线也接受

    def test_all_accessors(self):
        theme = Theme.dark()
        assert theme.space("lg") == 16.0
        assert theme.radius("md") == 10.0
        assert theme.font_size("md").px == 14
        assert theme.control_height("md") == 36.0
        assert theme.shadow("e1") is not None
        assert theme.duration("enter") == 200.0
        assert theme.easing("spring-default").damping == 26.0
        assert "Inter" in theme.font_sans

    def test_unknown_token_raises_with_available_names(self):
        theme = Theme.light()
        with pytest.raises(TokenError) as exc:
            theme.space("huge")
        assert "huge" in str(exc.value)
        assert "lg" in str(exc.value), "报错里应当列出可用令牌"

    def test_with_semantic_reuses_raw(self):
        theme = Theme.light()
        western = theme.with_semantic(
            theme.semantic.__class__(
                **{
                    **_semantic_kwargs(theme.semantic),
                    "market_up": theme.semantic.market_down,
                    "market_down": theme.semantic.market_up,
                }
            ),
            name="western",
        )
        assert western.name == "western"
        assert western.raw is theme.raw, "基础层应当原样复用"
        assert western.color("market-up") == theme.color("market-down")

    def test_theme_contrast_issues_delegates(self):
        assert Theme.light().contrast_issues() == []
        assert Theme.dark().contrast_issues() == []


def _semantic_kwargs(semantic) -> dict[str, Color]:
    import dataclasses

    return {f.name: getattr(semantic, f.name) for f in dataclasses.fields(semantic)}


class TestUnsetSentinel:
    """R6.3：`UNSET` 区分"没填"与"填了 None"（resolve.py）。"""

    def test_unset_means_no_opinion(self):
        from inkstone.style import UNSET
        from inkstone.style.resolve import layer

        assert layer({"a": 1}, {"a": UNSET}) == {"a": 1}

    def test_none_is_a_real_override(self):
        """把 e1 阴影降回 e0（None）必须能生效——修复前 None 被当成"没填"跳过。"""
        from inkstone.style.resolve import layer

        assert layer({"shadow": "e1"}, {"shadow": None}) == {"shadow": None}


class TestThemeColorCache:
    def test_semantic_map_is_built_once_per_semantic(self):
        """R6.3：`Theme.color()` 是热路径，26 键 dict 不许每次调用重建。"""
        from inkstone.style.theme import _semantic_map

        theme = Theme.light()
        assert _semantic_map(theme.semantic) is _semantic_map(theme.semantic)
