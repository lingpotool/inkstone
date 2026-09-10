"""动态增删、参数校验与边界分支的测试。

这个文件存在的理由不是"把覆盖率刷上去"，而是补三类真的会出事的地方：

1. **动态增删**：`remove()` 是热重载与动态列表的基础操作，之前三个容器各有一份
   实现却一份都没测过。
2. **参数校验**：这些 raise 是公开契约的一部分，被调用方依赖。
3. **边界分支**：无界约束下的回退、除零保护等，真实场景会碰到。

反过来，像 `Offset.__add__` 这类纯算术运算符**刻意不测**——测它等于测 Python 本身，
只会让数字好看，不会让代码更可靠。
"""

import pytest

from inkstone.layout import (
    BoxConstraints,
    EdgeInsets,
    GridItem,
    LayoutError,
    RenderColumn,
    RenderContainer,
    RenderGrid,
    RenderRow,
    RenderScroll,
    RenderSized,
    RenderStack,
    Size,
    Sizing,
    StackFit,
    TrackSize,
)


def sized(width: float = 20.0, height: float = 10.0, name: str = "Box") -> RenderSized:
    return RenderSized(width=Sizing.fixed(width), height=Sizing.fixed(height), debug_name=name)


class TestRemoveFromFlex:
    def test_remove_shrinks_the_row(self):
        row = RenderRow(gap=4, debug_name="Row")
        row.add(sized(20, 10, "A"))
        b = row.add(sized(30, 10, "B"))
        row.add(sized(40, 10, "C"))
        row.layout(BoxConstraints(max_width=200))
        assert row.size.width == pytest.approx(20 + 4 + 30 + 4 + 40)

        row.remove(b.child)
        assert row.needs_layout  # 删完一定是脏的，否则下一帧不会重排
        row.layout(BoxConstraints(max_width=200))
        assert row.size.width == pytest.approx(20 + 4 + 40)
        assert tuple(c.debug_name for c in row.children) == ("A", "C")

    def test_removed_child_is_detached(self):
        row = RenderRow(debug_name="Row")
        a = row.add(sized(20, 10, "A")).child
        row.remove(a)
        assert a.parent is None

    def test_remove_unknown_child_raises(self):
        row = RenderRow(debug_name="Row")
        row.add(sized())
        with pytest.raises(LayoutError) as exc:
            row.remove(sized(1, 1, "Stranger"))
        assert "Row" in str(exc.value)

    def test_column_remove_works_too(self):
        col = RenderColumn(gap=2, debug_name="Col")
        a = col.add(sized(20, 10, "A"))
        col.add(sized(20, 15, "B"))
        col.layout(BoxConstraints(max_width=100, max_height=100))
        col.remove(a.child)
        # 删前 A(10) + gap2 + B(15) = 27；删掉 A 后只剩 B 的 15
        col.layout(BoxConstraints(max_width=100, max_height=100))
        assert col.size.height == pytest.approx(15.0)


class TestRemoveFromGrid:
    def test_remove_frees_the_cell(self):
        grid = RenderGrid([TrackSize.fixed(50), TrackSize.fixed(50)], debug_name="Grid")
        grid.add(sized(20, 10, "A"), row=0, column=0)
        b = grid.add(sized(20, 10, "B"), row=0, column=1)
        grid.layout(BoxConstraints(max_width=400, max_height=400))

        grid.remove(b.child)
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert tuple(c.debug_name for c in grid.children) == ("A",)

    def test_remove_unknown_child_raises(self):
        grid = RenderGrid([TrackSize.fixed(50)], debug_name="Grid")
        grid.add(sized())
        with pytest.raises(LayoutError):
            grid.remove(sized(1, 1, "Stranger"))


class TestRemoveFromStack:
    def test_remove_drops_the_layer(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        badge = stack.add(sized(10, 10, "Badge"))
        stack.layout(BoxConstraints(max_width=400, max_height=400))

        stack.remove(badge.child)
        assert tuple(c.debug_name for c in stack.children) == ("Bg",)

    def test_remove_unknown_child_raises(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized())
        with pytest.raises(LayoutError):
            stack.remove(sized(1, 1, "Stranger"))


class TestReplacingChild:
    def test_container_replacing_child_orphans_the_old_one(self):
        first = sized(20, 10, "First")
        second = sized(40, 20, "Second")
        card = RenderContainer(first, debug_name="Card")
        card.layout(BoxConstraints(max_width=200, max_height=200))

        card.child = second
        assert first.parent is None
        assert second.parent is card
        card.layout(BoxConstraints(max_width=200, max_height=200))
        assert card.size.width == pytest.approx(40.0)

    def test_scroll_replacing_child_orphans_the_old_one(self):
        first = RenderColumn(debug_name="First")
        first.add(sized(100, 400, "Tall"))
        second = RenderColumn(debug_name="Second")
        second.add(sized(100, 50, "Short"))

        scroll = RenderScroll(first, debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.can_scroll

        scroll.child = second
        assert first.parent is None
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert not scroll.can_scroll


class TestParameterValidation:
    def test_flex_rejects_negative_gap(self):
        with pytest.raises(ValueError):
            RenderRow(gap=-1)

    def test_flex_rejects_negative_flex_weight(self):
        row = RenderRow(debug_name="Row")
        with pytest.raises(ValueError):
            row.add(sized(), flex=-1)

    def test_flex_rejects_negative_shrink_weight(self):
        row = RenderRow(debug_name="Row")
        with pytest.raises(ValueError):
            row.add(sized(), shrink=-1)

    def test_grid_rejects_negative_gap(self):
        with pytest.raises(ValueError):
            RenderGrid([TrackSize.fixed(10)], gap=-1)

    def test_grid_item_rejects_negative_row(self):
        with pytest.raises(ValueError):
            GridItem(child=sized(), row=-1)

    def test_constraints_reject_inverted_height(self):
        with pytest.raises(ValueError):
            BoxConstraints(min_height=10, max_height=5)

    def test_deflate_rejects_negative_insets(self):
        with pytest.raises(ValueError):
            BoxConstraints(max_width=100, max_height=100).deflate(EdgeInsets.all(-1))


class TestLayoutChildContract:
    def test_layouting_a_non_child_raises(self):
        card = RenderContainer(debug_name="Card")
        stranger = sized(1, 1, "Stranger")
        with pytest.raises(LayoutError):
            card.layout_child(stranger, BoxConstraints(max_width=10))

    def test_placing_a_non_child_raises(self):
        card = RenderContainer(debug_name="Card")
        stranger = sized(1, 1, "Stranger")
        with pytest.raises(LayoutError):
            card.place_child(stranger, stranger.offset)


class TestBoundaryBranches:
    def test_stack_expand_falls_back_when_constraints_are_unbounded(self):
        """EXPAND 遇到无界约束时不能硬造 tight(inf)，要退回原约束。"""
        stack = RenderStack(fit=StackFit.EXPAND, debug_name="Stack")
        child = stack.add(sized(30, 20, "A")).child
        stack.layout(BoxConstraints())
        assert child.size == Size(30.0, 20.0)
        assert stack.size == Size(30.0, 20.0)

    def test_aspect_ratio_protects_against_zero_height(self):
        assert Size(100, 0).aspect_ratio == pytest.approx(0.0)
        assert Size(200, 100).aspect_ratio == pytest.approx(2.0)

    def test_grid_row_overflow_is_reported(self):
        grid = RenderGrid(
            [TrackSize.fixed(50)], [TrackSize.fixed(60), TrackSize.fixed(60)], debug_name="Grid"
        )
        grid.add(sized(10, 10), row=0)
        grid.add(sized(10, 10), row=1)
        grid.layout(BoxConstraints(max_width=200, max_height=100))
        assert grid.has_overflow
        assert grid.overflow == pytest.approx(20.0)
