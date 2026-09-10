"""盒子模型（RenderBox / RenderSized / RenderContainer）的单元测试。

同样全部无窗口运行——这是"布局引擎 100% 无头可测"这条验收标准的证据。
"""

import pytest

from inkstone.layout import (
    Axis,
    BoxConstraints,
    EdgeInsets,
    LayoutError,
    Offset,
    RenderBox,
    RenderContainer,
    RenderSized,
    Size,
    Sizing,
    SizingKind,
    collect_descendants,
)


class CountingBox(RenderSized):
    """记录自己被布局了多少次的盒子——用来验证缓存与脏标记。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.calls = 0

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        self.calls += 1
        return super().perform_layout(constraints)


class TestRenderSized:
    def test_fixed_size(self):
        box = RenderSized(width=Sizing.fixed(40), height=Sizing.fixed(20))
        box.layout(BoxConstraints(max_width=100, max_height=100))
        assert box.size.width == pytest.approx(40.0)
        assert box.size.height == pytest.approx(20.0)

    def test_content_size_collapses_to_min(self):
        box = RenderSized()
        box.layout(BoxConstraints(max_width=100, max_height=100))
        assert box.size.width == pytest.approx(0.0)

    def test_fill_takes_all_available(self):
        box = RenderSized(width=Sizing.fill())
        box.layout(BoxConstraints(max_width=123))
        assert box.size.width == pytest.approx(123.0)

    def test_fill_under_unbounded_constraint_raises_with_path(self):
        box = RenderSized(width=Sizing.fill(), debug_name="Spacer")
        with pytest.raises(LayoutError) as exc:
            box.layout(BoxConstraints())
        message = str(exc.value)
        assert "fill" in message
        assert "Spacer" in message
        assert "建议" in message

    def test_size_is_clamped_into_constraints(self):
        box = RenderSized(width=Sizing.fixed(999), height=Sizing.fixed(5))
        box.layout(BoxConstraints(max_width=50, max_height=50))
        assert box.size.width == pytest.approx(50.0)

    def test_baseline_is_optional(self):
        assert RenderSized().layout(BoxConstraints()) is not None
        box = RenderSized(baseline=12.0)
        box.layout(BoxConstraints(max_width=10, max_height=10))
        assert box.baseline == pytest.approx(12.0)


class TestSizing:
    def test_rejects_negative_fixed(self):
        with pytest.raises(ValueError):
            Sizing.fixed(-1)

    def test_rejects_non_positive_fill_weight(self):
        with pytest.raises(ValueError):
            Sizing.fill(0)

    def test_str_is_readable(self):
        assert str(Sizing.content()) == "content"
        assert str(Sizing.fixed(8)) == "8px"
        assert str(Sizing.fill(2)) == "fill(2)"

    def test_only_fill_is_flexible(self):
        assert Sizing.fill().is_flexible
        assert not Sizing.content().is_flexible
        assert not Sizing.fixed(1).is_flexible


class TestDirtyAndCache:
    def test_mark_needs_layout_bubbles_to_parent(self):
        parent = RenderContainer(debug_name="Card")
        child = RenderSized(width=Sizing.fixed(10), height=Sizing.fixed(10))
        parent.child = child
        parent.layout(BoxConstraints(max_width=100, max_height=100))
        assert not parent.needs_layout
        assert not child.needs_layout

        child.mark_needs_layout()
        assert parent.needs_layout

    def test_clean_subtree_is_not_relayout(self):
        parent = RenderContainer(debug_name="Card")
        child = CountingBox(width=Sizing.fixed(10), height=Sizing.fixed(10))
        parent.child = child

        constraints = BoxConstraints(max_width=100, max_height=100)
        parent.layout(constraints)
        assert child.calls == 1

        # 同样的约束、没有脏标记 → 直接复用，不再进入 perform_layout
        parent.layout(constraints)
        assert child.calls == 1

        # 约束变了 → 必须重排
        parent.layout(BoxConstraints(max_width=200, max_height=100))
        assert child.calls == 2

    def test_adding_child_marks_parent_dirty(self):
        parent = RenderContainer(debug_name="Card")
        parent.layout(BoxConstraints(max_width=100, max_height=100))
        assert not parent.needs_layout
        parent.child = RenderSized(width=Sizing.fixed(5), height=Sizing.fixed(5))
        assert parent.needs_layout


class TestRenderContainer:
    def test_padding_is_added_around_child(self):
        child = RenderSized(width=Sizing.fixed(20), height=Sizing.fixed(10))
        card = RenderContainer(child, padding=EdgeInsets.all(8), debug_name="Card")
        card.layout(BoxConstraints(max_width=200, max_height=200))

        assert card.size.width == pytest.approx(36.0)
        assert card.size.height == pytest.approx(26.0)
        assert child.offset == Offset(8.0, 8.0)

    def test_padding_shrinks_child_constraints(self):
        child = RenderSized(width=Sizing.fill(), height=Sizing.fill())
        card = RenderContainer(child, padding=EdgeInsets.all(10), debug_name="Card")
        card.layout(BoxConstraints(max_width=100, max_height=60))

        assert card.size.width == pytest.approx(100.0)
        assert child.size.width == pytest.approx(80.0)
        assert child.size.height == pytest.approx(40.0)

    def test_fixed_size_wins_over_child(self):
        child = RenderSized(width=Sizing.fixed(90), height=Sizing.fixed(90))
        card = RenderContainer(
            child, width=Sizing.fixed(50), height=Sizing.fixed(50), debug_name="Card"
        )
        card.layout(BoxConstraints(max_width=200, max_height=200))
        assert card.size.width == pytest.approx(50.0)
        assert card.has_overflow

    def test_baseline_propagates_through_padding(self):
        child = RenderSized(width=Sizing.fixed(10), height=Sizing.fixed(20), baseline=15.0)
        card = RenderContainer(child, padding=EdgeInsets.all(4), debug_name="Card")
        card.layout(BoxConstraints(max_width=100, max_height=100))
        assert card.baseline == pytest.approx(19.0)

    def test_reparenting_is_rejected(self):
        a = RenderContainer(debug_name="A")
        b = RenderContainer(debug_name="B")
        child = RenderSized(width=Sizing.fixed(1), height=Sizing.fixed(1))
        a.child = child
        with pytest.raises(LayoutError):
            b.child = child


class TestDebugPath:
    def test_path_shows_hierarchy_with_indices(self):
        root = RenderContainer(debug_name="App")
        from inkstone.layout import RenderColumn

        col = RenderColumn(debug_name="Body")
        root.child = col
        card = RenderContainer(debug_name="Card")
        col.add(card)

        assert card.debug_path == "App > Body[0] > Card[0]"

    def test_describe_is_single_line(self):
        box = RenderSized(width=Sizing.fixed(10), height=Sizing.fixed(10), debug_name="Icon")
        box.layout(BoxConstraints(max_width=50, max_height=50))
        line = box.describe()
        assert "Icon" in line
        assert "\n" not in line

    def test_collect_descendants_is_preorder(self):
        from inkstone.layout import RenderRow

        row = RenderRow(debug_name="Row")
        a = RenderSized(debug_name="A")
        b = RenderSized(debug_name="B")
        row.add(a)
        row.add(b)
        row.layout(BoxConstraints(max_width=100))
        assert [n.debug_name for n in collect_descendants(row)] == ["Row", "A", "B"]


class TestLayoutError:
    def test_message_contains_path_and_suggestion(self):
        err = LayoutError("出事了", path="App > Row[0]", suggestion="试试这个")
        text = str(err)
        assert "出事了" in text
        assert "路径: App > Row[0]" in text
        assert "建议: 试试这个" in text

    def test_is_runtime_error(self):
        assert isinstance(LayoutError("x"), RuntimeError)


class TestAxisHelpers:
    def test_main_and_cross_switch_with_axis(self):
        from inkstone.layout import cross_of, main_of

        s = Size(120.0, 40.0)
        assert main_of(s, Axis.HORIZONTAL) == pytest.approx(120.0)
        assert main_of(s, Axis.VERTICAL) == pytest.approx(40.0)
        assert cross_of(s, Axis.HORIZONTAL) == pytest.approx(40.0)
        assert cross_of(s, Axis.VERTICAL) == pytest.approx(120.0)

    def test_flipped(self):
        assert Axis.HORIZONTAL.flipped is Axis.VERTICAL
        assert Axis.VERTICAL.flipped is Axis.HORIZONTAL


def test_sizing_kind_enum_values_are_stable():
    assert SizingKind.FIXED.value == "fixed"
    assert SizingKind.FILL.value == "fill"
    assert SizingKind.CONTENT.value == "content"


def test_render_box_perform_layout_must_be_overridden():
    with pytest.raises(NotImplementedError):
        RenderBox().layout(BoxConstraints(max_width=10))
