"""最小组件集的端到端测试。

这组测试回答一个问题：**把三棵树、令牌、布局串起来，能不能真的摆出一个界面？**
所以用的是真实场景（一个登录表单），不是各自为政的单元测试。

同时钉住几条架构约定：
- 组件里没有硬编码的尺寸（高度必须来自令牌）
- 状态变化会驱动重建并改变解析出的颜色
- Flexible 能让 Row 里的控件吃掉剩余宽度
"""

import pytest

from inkstone.core import BuildOwner
from inkstone.layout import BoxConstraints, RenderColumn, RenderFlex
from inkstone.style import (
    ButtonVariant,
    ComponentState,
    Theme,
    ThemeMode,
    resolve_button_style,
    resolve_input_style,
)
from inkstone.widgets import Box, Button, Card, Column, Flexible, Input, Row

CONSTRAINTS = BoxConstraints(max_width=320, max_height=600)


def login_form() -> Column:
    """一个写起来像样的登录表单。"""
    return Column(
        children=(
            Card(child=Box(height=64)),
            Input(placeholder="邮箱"),
            Input(placeholder="密码"),
            Row(
                children=(
                    Button("取消", variant=ButtonVariant.GHOST),
                    Flexible(Button("登录"), flex=1),
                ),
                gap=8,
            ),
        ),
        gap=16,
    )


class TestEndToEnd:
    def test_form_builds_and_layouts(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(login_form())
        size = owner.begin_frame(CONSTRAINTS)

        assert size is not None
        assert size.width == pytest.approx(320.0), "表单应当撑满可用宽度"

        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        assert len(root.children) == 4

    def test_theme_is_reachable_from_widgets(self):
        theme = Theme.dark()
        owner = BuildOwner(theme=theme)
        element = owner.mount(login_form())
        assert element.theme is theme

    def test_default_theme_is_light(self):
        owner = BuildOwner()
        assert owner.theme.mode is ThemeMode.LIGHT


class TestThemeHotSwap:
    """运行时换主题必须真的换掉颜色——"换肤"不能只是句承诺。

    修复前：`BuildOwner.theme` 是个普通可变属性，`Element.theme` 活读它，
    但样式只在 mount / update 的 `_apply_style` 里被写进渲染对象——
    运行时换主题，整棵树静默保留旧色：主题"换了"，界面一点没动。
    """

    @staticmethod
    def _owner() -> BuildOwner:
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Button("确定", width=120))
        owner.begin_frame(CONSTRAINTS)
        return owner

    @staticmethod
    def _expected_bg(theme: Theme) -> object:
        return resolve_button_style(
            theme,
            variant=ButtonVariant.PRIMARY,
            size="md",
            state=ComponentState.DEFAULT,
        ).bg

    def test_swapping_theme_changes_the_button_background(self) -> None:
        owner = self._owner()
        render_object = owner.root_render_object
        assert render_object is not None
        light_bg = render_object.bg
        assert light_bg == self._expected_bg(Theme.light())

        owner.theme = Theme.dark()
        owner.begin_frame(CONSTRAINTS)

        assert render_object.bg != light_bg, "换主题后按钮底色必须变"
        assert render_object.bg == self._expected_bg(Theme.dark())

    def test_swapping_theme_marks_the_whole_tree_dirty(self) -> None:
        owner = self._owner()
        owner.build_count = 0
        owner.theme = Theme.dark()

        assert owner.dirty_count > 0, "换主题必须把整棵树标脏"
        owner.begin_frame(CONSTRAINTS)
        assert owner.build_count > 0, "重建必须真的发生"

    def test_assigning_the_same_theme_object_is_a_no_op(self) -> None:
        owner = self._owner()
        owner.build_count = 0
        current = owner.theme

        owner.theme = current
        assert owner.dirty_count == 0, "同一个主题对象不该白白触发重建"
        owner.begin_frame(CONSTRAINTS)
        assert owner.build_count == 0

    def test_swapping_theme_before_mount_is_fine(self) -> None:
        owner = BuildOwner(theme=Theme.light())
        owner.theme = Theme.dark()  # 还没挂载，没有树可标脏
        owner.mount(Button("确定", width=120))
        owner.begin_frame(CONSTRAINTS)
        assert owner.root_render_object is not None
        assert owner.root_render_object.bg == self._expected_bg(Theme.dark())


class TestCardPaddingComesFromTokens:
    def test_card_padding_is_the_token_value(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Card(child=Box(height=20)))
        owner.begin_frame(CONSTRAINTS)

        card = owner.root_render_object
        assert card is not None
        assert card.padding.left == pytest.approx(16.0)  # space("lg")

    def test_explicit_padding_wins(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Card(child=Box(height=20), padding=4))
        owner.begin_frame(CONSTRAINTS)
        assert owner.root_render_object is not None
        assert owner.root_render_object.padding.left == pytest.approx(4.0)


class TestFlexibleInRow:
    def test_flexible_child_takes_remaining_width(self):
        owner = BuildOwner(theme=Theme.light())
        row = Row(children=(Box(width=60, height=20), Flexible(Box(height=20), flex=1)), gap=8)
        owner.mount(row)
        owner.begin_frame(BoxConstraints(max_width=300))

        flex_box = owner.root_render_object
        assert isinstance(flex_box, RenderFlex)
        first, second = flex_box.children
        assert first.size.width == pytest.approx(60.0)
        # 300 - 60 - 8(gap) = 232
        assert second.size.width == pytest.approx(232.0)

    def test_flexible_cannot_be_mounted_alone(self):
        from inkstone.widgets import Flexible

        with pytest.raises(AssertionError):
            Flexible(Box()).create_element()


class TestControlHeightsComeFromTokens:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [("xs", 24.0), ("sm", 28.0), ("md", 36.0), ("lg", 44.0), ("xl", 52.0)],
    )
    def test_button_heights(self, size: str, expected: float):
        theme = Theme.light()
        style = resolve_button_style(theme, size=size)
        assert style.height == pytest.approx(expected)

    def test_input_height_comes_from_tokens(self):
        style = resolve_input_style(Theme.light(), size="lg")
        assert style.height == pytest.approx(44.0)


class TestButtonVariants:
    def test_primary_uses_brand_color(self):
        theme = Theme.light()
        style = resolve_button_style(theme, variant=ButtonVariant.PRIMARY)
        assert style.bg == theme.color("primary")
        assert style.fg == theme.color("on-primary")

    def test_ghost_has_no_background(self):
        style = resolve_button_style(Theme.light(), variant=ButtonVariant.GHOST)
        assert style.is_transparent_bg
        assert style.border_width == pytest.approx(0.0)

    def test_danger_uses_the_state_token_pair_not_invented_red(self):
        """danger 变体不许发明 solid 红——语义令牌只定义了"浅底 + 深字"这一对。"""
        theme = Theme.light()
        style = resolve_button_style(theme, variant=ButtonVariant.DANGER)
        assert style.bg == theme.color("danger-bg")
        assert style.fg == theme.color("danger-text")
        # 顺带证明它是对比度安全的
        assert style.fg.meets_aa(style.bg)

    def test_all_variants_are_contrast_safe(self):
        """每个变体的"字色 / 底色"组合都必须达到 AA（docs/13 §9）。"""
        for theme in (Theme.light(), Theme.dark()):
            for variant in ButtonVariant:
                style = resolve_button_style(theme, variant=variant)
                if style.is_transparent_bg:
                    continue  # 透明底由父级表面决定，不是这个断言的适用范围
                assert style.fg.meets_aa(style.bg), (
                    f"{theme.name}/{variant.value} 的字色对比度不足："
                    f"{style.fg.contrast_ratio(style.bg):.2f}:1"
                )


class TestStateAxis:
    def test_hover_changes_background(self):
        theme = Theme.light()
        default = resolve_button_style(theme, state=ComponentState.DEFAULT)
        hover = resolve_button_style(theme, state=ComponentState.HOVER)
        assert hover.bg != default.bg

    def test_focus_visible_draws_a_ring(self):
        theme = Theme.light()
        normal = resolve_button_style(theme, state=ComponentState.DEFAULT)
        focused = resolve_button_style(theme, state=ComponentState.FOCUS_VISIBLE)
        assert normal.focus_ring_width == pytest.approx(0.0)
        assert focused.focus_ring_width == pytest.approx(2.0)

    def test_disabled_is_visually_distinct(self):
        theme = Theme.light()
        disabled = resolve_button_style(theme, state=ComponentState.DISABLED)
        default = resolve_button_style(theme, state=ComponentState.DEFAULT)
        assert disabled.fg != default.fg

    def test_input_focus_uses_primary_border(self):
        theme = Theme.light()
        focused = resolve_input_style(theme, state=ComponentState.FOCUS_VISIBLE)
        assert focused.border == theme.color("primary")

    def test_interactive_states_are_flagged(self):
        assert ComponentState.DISABLED.is_interactive_blocked
        assert ComponentState.LOADING.is_interactive_blocked
        assert not ComponentState.HOVER.is_interactive_blocked


class TestButtonStateDrivesRebuild:
    def test_changing_state_rebuilds_the_tree(self):
        owner = BuildOwner(theme=Theme.light())
        button = Button("确定")
        element = owner.mount(button)
        owner.begin_frame(CONSTRAINTS)

        state = element.state
        assert state.component_state is ComponentState.DEFAULT

        owner.build_count = 0
        state.set_component_state(ComponentState.HOVER)
        assert owner.dirty_count == 1
        owner.begin_frame(CONSTRAINTS)
        assert owner.build_count == 1

    def test_disabled_button_starts_disabled(self):
        owner = BuildOwner(theme=Theme.light())
        element = owner.mount(Button("确定", disabled=True))
        owner.begin_frame(CONSTRAINTS)
        assert element.state.component_state is ComponentState.DISABLED
        assert not element.state.enabled


class TestSlotPropagatesThroughComponents:
    """slot（flex 权重）必须能穿过 StatelessWidget / StatefulWidget。

    这是靠真实 bug 换来的测试：曾经 `Flexible(Button())` 里的 flex 会丢，
    因为 slot 在 ComponentElement 这一层被写死成 None。
    """

    def test_flex_weight_survives_a_stateful_wrapper(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(
            Row(children=(Box(width=80, height=20), Flexible(Button("确定"), flex=1)), gap=8)
        )
        owner.begin_frame(BoxConstraints(max_width=320))

        row = owner.root_render_object
        assert row is not None
        from inkstone.layout import RenderFlex

        assert isinstance(row, RenderFlex)
        assert row.items[1].flex == 1
        fixed, flexible = row.children
        assert fixed.size.width == pytest.approx(80.0)
        assert flexible.size.width == pytest.approx(232.0)  # 320 - 80 - 8

    def test_mount_preserves_declared_order(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Column(children=(Card(child=Box(height=20)), Box(height=10), Box(height=30))))
        owner.begin_frame(CONSTRAINTS)

        column = owner.root_render_object
        assert column is not None
        heights = [c.size.height for c in column.children]
        # 顺序必须是 声明的顺序：Card(96=20+32×2? 不，padding 只有外边 16+16=32 + 20 = 52?) —— 见下断言
        assert heights[0] > heights[1], "Card（含 padding）必须排在最前"
        assert heights == pytest.approx([52.0, 10.0, 30.0])

    def test_reorder_moves_component_render_objects(self):
        owner = BuildOwner(theme=Theme.light())
        element = owner.mount(
            Column(children=(Card(child=Box(height=10), key=KeyA()), Box(height=20, key=KeyB())))
        )
        owner.begin_frame(CONSTRAINTS)

        column = owner.root_render_object
        assert column is not None
        first_heights = [c.size.height for c in column.children]

        # 调换两个子级的位置（Key 相同、类型相同 → 复用 Element，只换顺序）
        element.update(
            Column(children=(Box(height=20, key=KeyB()), Card(child=Box(height=10), key=KeyA())))
        )
        owner.begin_frame(CONSTRAINTS)

        second_heights = [c.size.height for c in column.children]
        assert first_heights != second_heights, "重排后渲染树里的顺序必须跟着变"
        assert second_heights == pytest.approx(list(reversed(first_heights)))


def KeyA() -> object:
    from inkstone.core import ValueKey

    return ValueKey("a")


def KeyB() -> object:
    from inkstone.core import ValueKey

    return ValueKey("b")


class TestNoHardcodedValues:
    """docs/13 §9：组件里不许出现字面量颜色 / 字号 / 间距。"""

    def test_radius_and_font_come_from_theme(self):
        theme = Theme.light()
        style = resolve_button_style(theme)
        assert style.radius == theme.radius("md")
        assert style.font == theme.font_size("md")

    def test_switching_theme_switches_colors(self):
        light = resolve_button_style(Theme.light(), variant=ButtonVariant.PRIMARY)
        dark = resolve_button_style(Theme.dark(), variant=ButtonVariant.PRIMARY)
        assert light.bg != dark.bg

    def test_unknown_size_raises(self):

        with pytest.raises(KeyError):
            resolve_button_style(Theme.light(), size="gigantic")


class TestControlTextRendering:
    """Button / Input 的文字：尺寸来自真实度量，不估算（docs/04 §3）。"""

    @staticmethod
    def _owner(theme=None):
        from inkstone.backend import HeadlessBackend
        from inkstone.text import TextEngine

        return BuildOwner(
            theme=theme if theme is not None else Theme.light(),
            text_engine=TextEngine(HeadlessBackend()),
        )

    def test_button_shrinks_to_label(self):
        """**没有显式宽度时，按钮按标签文字的真实度量收缩。**

        文本栈落地前做不到这一点——只能撑满或写死宽度，因为文字宽度
        不许估算。现在宽度 = 文字度量 + 两侧内边距。
        """
        owner = self._owner()
        owner.mount(Button("确定"))
        size = owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        assert size is not None
        assert size.width < 320, "按钮没有按标签收缩，仍然撑满了可用宽度"
        assert size.width > 0

    def test_longer_label_makes_wider_button(self):
        wide, narrow = None, None
        for label in ("确定", "确定并继续下一步"):
            owner = self._owner()
            owner.mount(Button(label))
            size = owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
            assert size is not None
            if label == "确定":
                narrow = size.width
            else:
                wide = size.width
        assert wide is not None and narrow is not None
        assert wide > narrow, "更长的标签没有让按钮变宽——说明没按度量算"

    def test_button_in_flexible_still_fills(self):
        """`flex=1` 分到的是紧约束：按钮必须撑满份额，不能收缩成小按钮。"""
        owner = self._owner()
        owner.mount(Row(children=(Flexible(Button("登录"), flex=1),)))
        size = owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        assert size is not None
        assert size.width == pytest.approx(320.0)

    def test_explicit_width_wins(self):
        owner = self._owner()
        owner.mount(Button("确定", width=200))
        size = owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        assert size is not None
        assert size.width == pytest.approx(200.0)

    def test_input_fills_available_width(self):
        """输入框与按钮不同：它应当占满一行。"""
        owner = self._owner()
        owner.mount(Input(placeholder="邮箱"))
        size = owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        assert size is not None
        assert size.width == pytest.approx(320.0)

    def test_button_label_is_painted(self):
        from inkstone.gfx import DisplayListRecorder, TextRunOp

        owner = self._owner()
        owner.mount(Button("确定"))
        owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        recorder = DisplayListRecorder()
        owner.flush_paint(recorder)
        ops = [op for op in recorder.finish(320, 100).ops if isinstance(op, TextRunOp)]
        assert len(ops) == 1, "按钮标签没有被绘制"
        assert "".join(g.text for g in ops[0].glyphs) == "确定"

    def test_input_placeholder_is_painted(self):
        from inkstone.gfx import DisplayListRecorder, TextRunOp
        from inkstone.widgets import Input as InputWidget

        owner = self._owner()
        owner.mount(InputWidget(placeholder="邮箱"))
        owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        recorder = DisplayListRecorder()
        owner.flush_paint(recorder)
        ops = [op for op in recorder.finish(320, 100).ops if isinstance(op, TextRunOp)]
        assert len(ops) == 1, "占位符没有被绘制"

    def test_input_value_replaces_placeholder(self):
        from inkstone.gfx import DisplayListRecorder, TextRunOp
        from inkstone.widgets import Input as InputWidget

        owner = self._owner()
        owner.mount(InputWidget(value="user@example.com", placeholder="邮箱"))
        owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        recorder = DisplayListRecorder()
        owner.flush_paint(recorder)
        op = next(op for op in recorder.finish(320, 100).ops if isinstance(op, TextRunOp))
        assert "".join(g.text for g in op.glyphs) == "user@example.com"

    def test_placeholder_uses_placeholder_color(self):
        """占位符要用占位符色，有值用正文色——否则两者视觉上无法区分。"""
        from inkstone.gfx import DisplayListRecorder, TextRunOp
        from inkstone.widgets import Input as InputWidget

        empty = self._owner()
        empty.mount(InputWidget(placeholder="邮箱"))
        empty.flush_layout(BoxConstraints(max_width=320, max_height=100))
        rec = DisplayListRecorder()
        empty.flush_paint(rec)
        placeholder_op = next(op for op in rec.finish(320, 100).ops if isinstance(op, TextRunOp))

        filled = self._owner()
        filled.mount(InputWidget(value="邮箱"))
        filled.flush_layout(BoxConstraints(max_width=320, max_height=100))
        rec2 = DisplayListRecorder()
        filled.flush_paint(rec2)
        value_op = next(op for op in rec2.finish(320, 100).ops if isinstance(op, TextRunOp))

        assert placeholder_op.color != value_op.color

    def test_without_text_engine_controls_still_layout(self):
        """没配文本引擎时按钮退化为最小宽度，**不崩**。"""
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Button("确定"))
        size = owner.flush_layout(BoxConstraints(max_width=320, max_height=100))
        assert size is not None
        assert size.height > 0
