"""Grid 的单元测试。

核心是三态轨道（fixed / fr / auto）、span、以及"无限约束遇到 fr 要报错"。
"""

import pytest

from inkstone.layout import (
    BoxConstraints,
    GridItem,
    LayoutError,
    RenderGrid,
    RenderSized,
    Sizing,
    TrackKind,
    TrackSize,
)


def sized(width: float, height: float, name: str = "Box") -> RenderSized:
    return RenderSized(width=Sizing.fixed(width), height=Sizing.fixed(height), debug_name=name)


class TestTrackSize:
    def test_kinds(self):
        assert TrackSize.fixed(30).kind is TrackKind.FIXED
        assert TrackSize.fr(2).kind is TrackKind.FR
        assert TrackSize.auto().kind is TrackKind.AUTO

    def test_rejects_negative_fixed(self):
        with pytest.raises(ValueError):
            TrackSize.fixed(-1)

    def test_rejects_non_positive_fr(self):
        with pytest.raises(ValueError):
            TrackSize.fr(0)

    def test_str(self):
        assert str(TrackSize.fixed(30)) == "30px"
        assert str(TrackSize.fr(2)) == "fr(2)"
        assert str(TrackSize.auto()) == "auto"


class TestFixedTracks:
    def test_columns_and_rows_use_declared_size(self):
        grid = RenderGrid([TrackSize.fixed(50), TrackSize.fixed(70)], gap=4, debug_name="Grid")
        grid.add(sized(10, 10), row=0, column=0)
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        # 50 + 4 + 70 = 124
        assert grid.size.width == pytest.approx(124.0)

    def test_child_is_placed_at_cell_origin(self):
        grid = RenderGrid([TrackSize.fixed(50), TrackSize.fixed(50)], gap=10, debug_name="Grid")
        a = grid.add(sized(20, 10, "A"), row=0, column=0).child
        b = grid.add(sized(20, 10, "B"), row=0, column=1).child
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert a.offset.dx == pytest.approx(0.0)
        assert b.offset.dx == pytest.approx(60.0)


class TestFrTracks:
    def test_fr_takes_remaining_space(self):
        grid = RenderGrid([TrackSize.fixed(60), TrackSize.fr(1)], debug_name="Grid")
        grid.add(sized(10, 10), row=0, column=0)
        grid.add(sized(10, 10), row=0, column=1)
        grid.layout(BoxConstraints(max_width=300))
        assert grid.size.width == pytest.approx(300.0)

    def test_fr_weights_are_proportional(self):
        grid = RenderGrid([TrackSize.fr(1), TrackSize.fr(3)], gap=0, debug_name="Grid")
        a = grid.add(sized(10, 10, "A"), row=0, column=0).child
        b = grid.add(sized(10, 10, "B"), row=0, column=1).child
        grid.layout(BoxConstraints(max_width=200))
        # 剩余 200 按 1:3 分 → 50 / 150
        assert a.offset.dx == pytest.approx(0.0)
        assert b.offset.dx == pytest.approx(50.0)

    def test_fr_under_unbounded_constraint_raises(self):
        grid = RenderGrid([TrackSize.fr(1)], debug_name="Grid")
        grid.add(sized(10, 10))
        with pytest.raises(LayoutError) as exc:
            grid.layout(BoxConstraints())
        assert "fr" in str(exc.value)
        assert "Grid" in str(exc.value)

    def test_fr_rows_need_bounded_height(self):
        grid = RenderGrid([TrackSize.fixed(50)], [TrackSize.fr(1)], debug_name="Grid")
        grid.add(sized(10, 10))
        with pytest.raises(LayoutError):
            grid.layout(BoxConstraints(max_width=400))


class TestAutoTracks:
    def test_auto_column_sizes_to_content(self):
        grid = RenderGrid([TrackSize.auto(), TrackSize.fr(1)], column_gap=8, debug_name="Form")
        label = grid.add(sized(60, 20, "Label"), row=0, column=0).child
        value = grid.add(
            RenderSized(width=Sizing.fill(), height=Sizing.fixed(20), debug_name="Value"),
            row=0,
            column=1,
        ).child
        grid.layout(BoxConstraints(max_width=300))

        assert label.size.width == pytest.approx(60.0)
        assert value.offset.dx == pytest.approx(68.0)  # 60 + gap 8
        assert value.size.width == pytest.approx(232.0)

    def test_auto_takes_the_widest_child(self):
        grid = RenderGrid([TrackSize.auto()], debug_name="Grid")
        grid.add(sized(30, 10, "Narrow"), row=0, column=0)
        grid.add(sized(90, 10, "Wide"), row=1, column=0)
        grid.layout(BoxConstraints(max_width=400))
        assert grid.size.width == pytest.approx(90.0)

    def test_child_spanning_multiple_tracks_does_not_size_auto_track(self):
        """横跨多格的子级不参与 auto 定尺寸——宽度该算到哪一列没有唯一答案。"""
        grid = RenderGrid([TrackSize.auto(), TrackSize.auto()], debug_name="Grid")
        grid.add(sized(200, 10, "Wide"), row=0, column_span=2)
        grid.layout(BoxConstraints(max_width=400))
        assert grid.size.width == pytest.approx(0.0)

    def test_auto_rows_size_to_content(self):
        grid = RenderGrid([TrackSize.fixed(50)], debug_name="Grid")
        grid.add(sized(10, 30, "A"), row=0, column=0)
        grid.add(sized(10, 60, "B"), row=1, column=0)
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert grid.size.height == pytest.approx(90.0)


class TestPlacement:
    def test_auto_placement_fills_row_major(self):
        grid = RenderGrid([TrackSize.fixed(50), TrackSize.fixed(50)], gap=4, debug_name="Grid")
        items = [grid.add(sized(20, 10, f"C{i}")).child for i in range(4)]
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert [(c.offset.dx, c.offset.dy) for c in items] == pytest.approx(
            [(0, 0), (54, 0), (0, 14), (54, 14)]
        )

    def test_column_span_wraps_to_next_row(self):
        grid = RenderGrid([TrackSize.fixed(50), TrackSize.fixed(50)], gap=4, debug_name="Grid")
        a = grid.add(sized(20, 10, "A")).child
        b = grid.add(sized(20, 10, "B")).child
        c = grid.add(sized(20, 10, "C"), column_span=2).child
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert (a.offset.dx, a.offset.dy) == pytest.approx((0, 0))
        assert (b.offset.dx, b.offset.dy) == pytest.approx((54, 0))
        assert (c.offset.dx, c.offset.dy) == pytest.approx((0, 14))

    def test_explicit_column_only_finds_free_row(self):
        grid = RenderGrid([TrackSize.fixed(50), TrackSize.fixed(50)], debug_name="Grid")
        a = grid.add(sized(20, 10, "A"), column=0).child
        b = grid.add(sized(20, 10, "B"), column=0).child
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert a.offset.dy == pytest.approx(0.0)
        assert b.offset.dy == pytest.approx(10.0)

    def test_declared_tracks_exist_without_children(self):
        grid = RenderGrid([TrackSize.fixed(80)], debug_name="Grid")
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert grid.size.width == pytest.approx(80.0)

    def test_implicit_rows_are_created_on_demand(self):
        grid = RenderGrid([TrackSize.fixed(50)], debug_name="Grid")
        for i in range(3):
            grid.add(sized(10, 20, f"R{i}"), row=i)
        grid.layout(BoxConstraints(max_width=400, max_height=400))
        assert grid.size.height == pytest.approx(60.0)

    def test_span_wider_than_declared_columns_raises(self):
        grid = RenderGrid([TrackSize.fixed(50)], debug_name="Grid")
        grid.add(sized(20, 10), column_span=3)
        with pytest.raises(LayoutError) as exc:
            grid.layout(BoxConstraints(max_width=400))
        assert "列" in str(exc.value)


class TestGridOverflow:
    def test_tracks_exceeding_container_are_reported_not_squeezed(self):
        """200+200 的列塞进 300 的容器：报溢出，且列宽仍是 200（不悄悄压扁）。"""
        grid = RenderGrid([TrackSize.fixed(200), TrackSize.fixed(200)], debug_name="Grid")
        a = grid.add(sized(50, 10, "A"), row=0, column=0).child
        b = grid.add(sized(50, 10, "B"), row=0, column=1).child
        grid.layout(BoxConstraints(max_width=300))

        assert grid.size.width == pytest.approx(300.0)
        assert grid.has_overflow
        assert grid.overflow == pytest.approx(100.0)
        assert a.offset.dx == pytest.approx(0.0)
        assert b.offset.dx == pytest.approx(200.0)

    def test_fitting_grid_has_no_overflow(self):
        grid = RenderGrid([TrackSize.fixed(50)], debug_name="Grid")
        grid.add(sized(50, 10))
        grid.layout(BoxConstraints(max_width=300, max_height=300))
        assert not grid.has_overflow


class TestGridItemValidation:
    def test_span_must_be_positive(self):
        with pytest.raises(ValueError):
            GridItem(child=sized(1, 1), column_span=0)

    def test_negative_index_rejected(self):
        with pytest.raises(ValueError):
            GridItem(child=sized(1, 1), column=-1)


class TestDeterminism:
    def test_repeated_layout_is_identical(self):
        def build() -> RenderGrid:
            grid = RenderGrid([TrackSize.auto(), TrackSize.fr(2)], gap=6, debug_name="Grid")
            grid.add(sized(40, 20, "A"), row=0, column=0)
            grid.add(sized(40, 20, "B"), row=0, column=1)
            grid.add(sized(40, 20, "C"), row=1, column_span=2)
            grid.layout(BoxConstraints(max_width=280, max_height=300))
            return grid

        first = [n.describe() for n in build().children]
        for _ in range(3):
            assert [n.describe() for n in build().children] == first
