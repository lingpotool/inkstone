"""ScrollView 的单元测试。

核心是 docs/05 §6 那条规则：**滚动容器给子级的必须是无限主轴约束**。
这条做错的表现是"滚动区里的 fill 子级把整个窗口撑爆"，所以这里专门测它。
"""

import pytest

from inkstone.layout import (
    BoxConstraints,
    LayoutError,
    RenderColumn,
    RenderScroll,
    RenderSized,
    ScrollDirection,
    Sizing,
)


def tall_list(count: int = 5, item_height: float = 40.0) -> RenderColumn:
    col = RenderColumn(debug_name="List")
    for i in range(count):
        col.add(
            RenderSized(
                width=Sizing.fixed(100), height=Sizing.fixed(item_height), debug_name=f"I{i}"
            )
        )
    return col


def wide_content(width: float = 300.0, height: float = 40.0) -> RenderColumn:
    col = RenderColumn(debug_name="Wide")
    col.add(RenderSized(width=Sizing.fixed(width), height=Sizing.fixed(height), debug_name="W"))
    return col


class TestViewportAndContent:
    def test_viewport_takes_available_space(self):
        scroll = RenderScroll(tall_list(1), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.viewport.width == pytest.approx(120.0)
        assert scroll.viewport.height == pytest.approx(100.0)

    def test_content_can_exceed_viewport(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.content_size.height == pytest.approx(200.0)
        assert scroll.content_size.width == pytest.approx(100.0)

    def test_child_receives_unbounded_main_axis(self):
        """子级拿到无限高度——这就是"可滚动"的本质。"""
        captured: list[BoxConstraints] = []

        class Probe(RenderSized):
            def perform_layout(self, constraints: BoxConstraints) -> "object":
                captured.append(constraints)
                return super().perform_layout(constraints)

        probe = Probe(width=Sizing.fixed(50), height=Sizing.fixed(300))
        scroll = RenderScroll(probe, debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))

        assert captured
        assert captured[-1].max_height == float("inf")
        assert captured[-1].has_bounded_width

    def test_empty_scroll_collapses(self):
        scroll = RenderScroll(debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.content_size.height == pytest.approx(0.0)
        assert not scroll.can_scroll


class TestScrollOffset:
    def test_max_scroll_is_content_minus_viewport(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.max_scroll.dy == pytest.approx(100.0)
        assert scroll.max_scroll.dx == pytest.approx(0.0)

    def test_cannot_scroll_when_content_fits(self):
        scroll = RenderScroll(tall_list(1), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert not scroll.can_scroll
        assert scroll.max_scroll == scroll.scroll_offset

    def test_scroll_to_is_clamped(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        scroll.scroll_to(dy=999)
        assert scroll.scroll_offset.dy == pytest.approx(100.0)

    def test_scroll_to_rejects_negative(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        scroll.scroll_to(dy=-50)
        assert scroll.scroll_offset.dy == pytest.approx(0.0)

    def test_scroll_by_accumulates(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        scroll.scroll_by(dy=30)
        scroll.scroll_by(dy=30)
        assert scroll.scroll_offset.dy == pytest.approx(60.0)

    def test_child_is_offset_by_negative_scroll(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        child = scroll.children[0]
        scroll.scroll_to(dy=40)
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert child.offset.dy == pytest.approx(-40.0)


class TestDirection:
    def test_horizontal_scrolls_on_x_only(self):
        scroll = RenderScroll(
            wide_content(), direction=ScrollDirection.HORIZONTAL, debug_name="Scroll"
        )
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.max_scroll.dx == pytest.approx(180.0)  # 内容宽 300 - 视口 120
        assert scroll.max_scroll.dy == pytest.approx(0.0)  # 横向滚动时纵向锁死

    def test_vertical_scrolls_on_y_only(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.max_scroll.dx == pytest.approx(0.0)
        assert scroll.max_scroll.dy == pytest.approx(100.0)

    def test_both_directions(self):
        wide = RenderColumn(debug_name="Grid")
        wide.add(RenderSized(width=Sizing.fixed(300), height=Sizing.fixed(300)))
        scroll = RenderScroll(wide, direction=ScrollDirection.BOTH, debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.max_scroll.dx == pytest.approx(180.0)
        assert scroll.max_scroll.dy == pytest.approx(200.0)


class TestInfiniteConstraintDiagnostics:
    def test_fill_child_along_scroll_axis_raises(self):
        """滚动方向上要 fill = 逻辑矛盾：内容长度由内容决定，不能反过来吃视口。"""
        filler = RenderSized(width=Sizing.fixed(50), height=Sizing.fill(), debug_name="Filler")
        scroll = RenderScroll(filler, debug_name="Scroll")
        with pytest.raises(LayoutError) as exc:
            scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert "无限高度约束" in str(exc.value)

    def test_fill_child_across_scroll_axis_is_fine(self):
        """交叉轴仍然有界，所以横向 fill 是合法的。"""
        filler = RenderSized(width=Sizing.fill(), height=Sizing.fixed(300), debug_name="Filler")
        scroll = RenderScroll(filler, debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert filler.size.width == pytest.approx(120.0)


class TestOverflowSemantics:
    def test_scrolling_is_not_reported_as_overflow(self):
        """内容比视口长是设计意图，不是布局错误——否则检查器会被滚动区淹没。"""
        scroll = RenderScroll(tall_list(10), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert scroll.content_size.height > scroll.viewport.height
        assert not scroll.has_overflow


class TestReplacingChild:
    def test_swapping_child_resets_scroll(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        scroll.scroll_to(dy=80)
        assert scroll.scroll_offset.dy > 0.0

        scroll.child = tall_list(1)
        assert scroll.scroll_offset.dy == pytest.approx(0.0)

    def test_describe_reports_content_size(self):
        scroll = RenderScroll(tall_list(5), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        assert "content=100×200" in scroll.describe()
