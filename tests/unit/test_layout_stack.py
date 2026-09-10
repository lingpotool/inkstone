"""Stack / Positioned / Align 的单元测试。

Stack 的关键契约只有一条：**定位子级不参与决定 Stack 的尺寸**。
角标不该把卡片撑大，这条一旦破了，所有"叠一层"的需求都会变形。
"""

import pytest

from inkstone.layout import (
    ALIGN_BOTTOM_RIGHT,
    ALIGN_CENTER,
    ALIGN_CENTER_RIGHT,
    ALIGN_TOP_LEFT,
    BoxConstraints,
    EdgeInsets,
    PositionedSpec,
    RenderAlign,
    RenderContainer,
    RenderSized,
    RenderStack,
    Size,
    Sizing,
    StackFit,
)


def sized(width: float, height: float, name: str = "Box") -> RenderSized:
    return RenderSized(width=Sizing.fixed(width), height=Sizing.fixed(height), debug_name=name)


class TestStackSizing:
    def test_sizes_to_biggest_child(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(30, 20, "A"))
        stack.add(sized(50, 10, "B"))
        stack.layout(BoxConstraints(max_width=400, max_height=400))
        assert stack.size.width == pytest.approx(50.0)
        assert stack.size.height == pytest.approx(20.0)

    def test_empty_stack_collapses_to_min(self):
        stack = RenderStack(debug_name="Stack")
        stack.layout(BoxConstraints(max_width=400, max_height=400))
        assert stack.size.width == pytest.approx(0.0)

    def test_respects_tight_constraints(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(10, 10))
        stack.layout(BoxConstraints.tight(Size(200, 100)))
        assert stack.size.width == pytest.approx(200.0)
        assert stack.size.height == pytest.approx(100.0)

    def test_positioned_children_do_not_affect_size(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(40, 40, "Card"))
        stack.add_positioned(sized(500, 500, "Badge"), right=-10, bottom=-10)
        stack.layout(BoxConstraints(max_width=400, max_height=400))
        # 角标不该把卡片撑大
        assert stack.size.width == pytest.approx(40.0)
        assert stack.size.height == pytest.approx(40.0)
        assert stack.has_overflow

    def test_padding_is_included(self):
        stack = RenderStack(padding=EdgeInsets.all(6), debug_name="Stack")
        child = stack.add(sized(20, 10, "A")).child
        stack.layout(BoxConstraints(max_width=400, max_height=400))
        assert stack.size.width == pytest.approx(32.0)
        assert child.offset.dx == pytest.approx(6.0)


class TestStackAlignment:
    def test_default_is_top_left(self):
        stack = RenderStack(debug_name="Stack")
        a = stack.add(sized(10, 10, "A")).child
        stack.layout(BoxConstraints(max_width=100, max_height=50))
        assert (a.offset.dx, a.offset.dy) == pytest.approx((0, 0))

    def test_center(self):
        stack = RenderStack(alignment=ALIGN_CENTER, debug_name="Stack")
        stack.add(sized(20, 20, "Small"))
        stack.add(sized(80, 40, "Big"))
        stack.layout(BoxConstraints(max_width=400, max_height=400))
        small = stack.children[0]
        assert (small.offset.dx, small.offset.dy) == pytest.approx((30, 10))

    def test_bottom_right(self):
        stack = RenderStack(alignment=ALIGN_BOTTOM_RIGHT, debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        a = stack.add(sized(10, 10, "A")).child
        stack.layout(BoxConstraints(max_width=100, max_height=50))
        assert (a.offset.dx, a.offset.dy) == pytest.approx((90, 40))

    def test_center_right(self):
        stack = RenderStack(alignment=ALIGN_CENTER_RIGHT, debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        a = stack.add(sized(10, 10, "A")).child
        stack.layout(BoxConstraints(max_width=100, max_height=50))
        assert (a.offset.dx, a.offset.dy) == pytest.approx((90, 20))


class TestPositioned:
    def test_left_top(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        badge = stack.add_positioned(sized(10, 10, "Badge"), left=5, top=7).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert (badge.offset.dx, badge.offset.dy) == pytest.approx((5, 7))

    def test_right_bottom(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        badge = stack.add_positioned(sized(10, 10, "Badge"), right=4, bottom=6).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert (badge.offset.dx, badge.offset.dy) == pytest.approx((86, 34))

    def test_left_and_right_derive_width(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        bar = stack.add_positioned(
            RenderSized(height=Sizing.fixed(4), debug_name="Bar"), left=10, right=20
        ).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert bar.size.width == pytest.approx(70.0)
        assert bar.offset.dx == pytest.approx(10.0)

    def test_top_and_bottom_derive_height(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        rail = stack.add_positioned(
            RenderSized(width=Sizing.fixed(4), debug_name="Rail"), top=5, bottom=5
        ).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert rail.size.height == pytest.approx(40.0)

    def test_explicit_width_with_right(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        box = stack.add_positioned(sized(30, 10, "Box"), right=0, width=30).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert box.offset.dx == pytest.approx(70.0)

    def test_negative_offset_places_outside(self):
        stack = RenderStack(debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        badge = stack.add_positioned(sized(10, 10, "Badge"), left=-5, top=-5).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert (badge.offset.dx, badge.offset.dy) == pytest.approx((-5, -5))

    def test_positioned_without_edges_falls_back_to_alignment(self):
        stack = RenderStack(alignment=ALIGN_CENTER, debug_name="Stack")
        stack.add(sized(100, 50, "Bg"))
        only_width = stack.add_positioned(sized(20, 10, "X"), width=20).child
        stack.layout(BoxConstraints(max_width=200, max_height=200))
        assert only_width.offset.dx == pytest.approx(40.0)
        assert only_width.offset.dy == pytest.approx(20.0)

    def test_spec_rejects_negative_size(self):
        with pytest.raises(ValueError):
            PositionedSpec(width=-1)


class TestStackFit:
    def test_loose_lets_children_keep_content_size(self):
        stack = RenderStack(fit=StackFit.LOOSE, debug_name="Stack")
        a = stack.add(sized(20, 10, "A")).child
        stack.layout(BoxConstraints(max_width=300, max_height=300))
        assert a.size == Size(20.0, 10.0)

    def test_expand_stretches_children(self):
        stack = RenderStack(fit=StackFit.EXPAND, debug_name="Stack")
        a = stack.add(sized(20, 10, "A")).child
        stack.layout(BoxConstraints(max_width=300, max_height=120))
        assert a.size.width == pytest.approx(300.0)
        assert a.size.height == pytest.approx(120.0)

    def test_passthrough_forwards_lower_bound(self):
        stack = RenderStack(fit=StackFit.PASSTHROUGH, debug_name="Stack")
        a = stack.add(sized(20, 10, "A")).child
        stack.layout(BoxConstraints(min_width=150, min_height=80, max_width=300, max_height=300))
        assert a.size.width == pytest.approx(150.0)
        assert a.size.height == pytest.approx(80.0)


class TestRenderAlign:
    def test_centers_child_when_bounded(self):
        align = RenderAlign(sized(20, 10, "A"), alignment=ALIGN_CENTER, debug_name="Align")
        child = align.children[0]
        align.layout(BoxConstraints(max_width=100, max_height=40))
        assert (child.offset.dx, child.offset.dy) == pytest.approx((40, 15))

    def test_expands_when_parent_is_bounded(self):
        align = RenderAlign(sized(20, 10, "A"), alignment=ALIGN_CENTER, debug_name="Align")
        align.layout(BoxConstraints(max_width=100, max_height=100))
        assert align.size.width == pytest.approx(100.0)

    def test_shrinks_to_child_when_unbounded(self):
        align = RenderAlign(sized(20, 10, "A"), alignment=ALIGN_CENTER, debug_name="Align")
        align.layout(BoxConstraints())
        assert align.size == Size(20.0, 10.0)

    def test_width_factor_multiplies_child_size(self):
        align = RenderAlign(
            sized(20, 10, "A"), alignment=ALIGN_CENTER, width_factor=2.0, height_factor=3.0
        )
        align.layout(BoxConstraints(max_width=100, max_height=100))
        assert align.size.width == pytest.approx(40.0)
        assert align.size.height == pytest.approx(30.0)

    def test_padding_offsets_child(self):
        align = RenderAlign(
            sized(20, 10, "A"),
            alignment=ALIGN_TOP_LEFT,
            padding=EdgeInsets.all(5),
            debug_name="Align",
        )
        child = align.children[0]
        align.layout(BoxConstraints(max_width=100, max_height=100))
        assert (child.offset.dx, child.offset.dy) == pytest.approx((5, 5))


class TestStackComposition:
    def test_badge_on_card_is_the_canonical_case(self):
        """真实场景：卡片右上角叠一个角标。"""
        card = RenderContainer(
            padding=EdgeInsets.all(12),
            width=Sizing.fixed(160),
            height=Sizing.fixed(90),
            debug_name="Card",
        )
        stack = RenderStack(debug_name="Stack")
        card.child = stack
        stack.add(sized(136, 66, "Body"))
        badge = stack.add_positioned(sized(16, 16, "Badge"), right=-6, top=-6).child

        card.layout(BoxConstraints(max_width=400, max_height=400))

        assert card.size.width == pytest.approx(160.0)
        assert badge.offset.dx == pytest.approx(136.0 - 16.0 + 6.0)
        assert badge.offset.dy == pytest.approx(-6.0)
