"""Flex（Row / Column）的单元测试。

重点验证四件事：**几何正确、结果确定、无限约束报错、溢出可见**。
这四条对应 docs/05 §8 的验收标准。
"""

import pytest

from inkstone.layout import (
    BoxConstraints,
    CrossAxisAlignment,
    EdgeInsets,
    FlexFit,
    LayoutError,
    MainAxisAlignment,
    MainAxisSize,
    RenderColumn,
    RenderRow,
    RenderSized,
    Sizing,
    distribute_cross,
    distribute_main,
)


def sized(width: float, height: float, name: str = "Box") -> RenderSized:
    return RenderSized(width=Sizing.fixed(width), height=Sizing.fixed(height), debug_name=name)


class TestRowGeometry:
    def test_children_laid_out_along_main_axis(self):
        row = RenderRow(debug_name="Row")
        a = row.add(sized(40, 20, "A")).child
        b = row.add(sized(60, 30, "B")).child
        row.layout(BoxConstraints(max_width=400))

        assert row.size.width == pytest.approx(100.0)
        assert row.size.height == pytest.approx(30.0)
        assert a.offset.dx == pytest.approx(0.0)
        assert b.offset.dx == pytest.approx(40.0)

    def test_gap_is_inserted_between_children(self):
        row = RenderRow(gap=10, debug_name="Row")
        a = row.add(sized(20, 10, "A")).child
        b = row.add(sized(20, 10, "B")).child
        c = row.add(sized(20, 10, "C")).child
        row.layout(BoxConstraints(max_width=400))

        assert row.size.width == pytest.approx(80.0)  # 20*3 + 10*2
        assert (a.offset.dx, b.offset.dx, c.offset.dx) == pytest.approx((0, 30, 60))

    def test_single_child_needs_no_gap(self):
        row = RenderRow(gap=10, debug_name="Row")
        row.add(sized(20, 10))
        row.layout(BoxConstraints(max_width=400))
        assert row.size.width == pytest.approx(20.0)

    def test_column_stacks_vertically(self):
        col = RenderColumn(gap=5, debug_name="Col")
        a = col.add(sized(30, 10, "A")).child
        b = col.add(sized(50, 20, "B")).child
        col.layout(BoxConstraints(max_width=400, max_height=400))

        assert col.size.width == pytest.approx(50.0)
        assert col.size.height == pytest.approx(35.0)
        assert (a.offset.dy, b.offset.dy) == pytest.approx((0, 15))


class TestMainAxisAlignment:
    def _positions(self, justify: MainAxisAlignment, width: float = 100.0) -> list[float]:
        row = RenderRow(justify=justify, main_axis_size=MainAxisSize.MAX, debug_name="Row")
        row.add(sized(20, 10))
        row.add(sized(20, 10))
        row.layout(BoxConstraints(max_width=width))
        return [c.offset.dx for c in row.children]

    def test_start(self):
        assert self._positions(MainAxisAlignment.START) == pytest.approx([0, 20])

    def test_center(self):
        assert self._positions(MainAxisAlignment.CENTER) == pytest.approx([30, 50])

    def test_end(self):
        assert self._positions(MainAxisAlignment.END) == pytest.approx([60, 80])

    def test_space_between(self):
        assert self._positions(MainAxisAlignment.SPACE_BETWEEN) == pytest.approx([0, 80])

    def test_space_around(self):
        # free=60 平分成 2 份，每份 30：子级两侧各 15，因此间距为 30
        assert self._positions(MainAxisAlignment.SPACE_AROUND) == pytest.approx([15, 65])

    def test_space_evenly(self):
        # free=60 分成 3 份，每份 20
        assert self._positions(MainAxisAlignment.SPACE_EVENLY) == pytest.approx([20, 60])


class TestCrossAxisAlignment:
    def _offsets(self, align: CrossAxisAlignment) -> list[float]:
        row = RenderRow(align=align, debug_name="Row")
        row.add(sized(20, 10, "A"))
        row.add(sized(20, 40, "B"))
        row.layout(BoxConstraints(max_width=400, max_height=40))
        return [c.offset.dy for c in row.children]

    def test_start(self):
        assert self._offsets(CrossAxisAlignment.START) == pytest.approx([0, 0])

    def test_center(self):
        assert self._offsets(CrossAxisAlignment.CENTER) == pytest.approx([15, 0])

    def test_end(self):
        assert self._offsets(CrossAxisAlignment.END) == pytest.approx([30, 0])

    def test_stretch_pulls_children_to_container_height(self):
        row = RenderRow(align=CrossAxisAlignment.STRETCH, debug_name="Row")
        a = row.add(sized(20, 10, "A")).child
        row.layout(BoxConstraints(max_width=400, max_height=50))
        assert row.size.height == pytest.approx(50.0)
        assert a.size.height == pytest.approx(50.0)

    def test_stretch_uses_main_axis_size_max(self):
        row = RenderRow(
            align=CrossAxisAlignment.STRETCH,
            main_axis_size=MainAxisSize.MAX,
            debug_name="Row",
        )
        row.add(sized(20, 10, "A"))
        row.layout(BoxConstraints(max_width=400, max_height=50))
        assert row.size.width == pytest.approx(400.0)

    def test_baseline_aligns_text_baselines(self):
        # A：高 30、基线在 20；B：高 40、基线在 10 → 两条基线都落在 y=20
        row = RenderRow(align=CrossAxisAlignment.BASELINE, debug_name="Row")
        a = row.add(
            RenderSized(width=Sizing.fixed(20), height=Sizing.fixed(30), baseline=20.0)
        ).child
        b = row.add(
            RenderSized(width=Sizing.fixed(20), height=Sizing.fixed(40), baseline=10.0)
        ).child
        row.layout(BoxConstraints(max_width=400))

        assert a.offset.dy == pytest.approx(0.0)
        assert b.offset.dy == pytest.approx(10.0)
        assert a.offset.dy + a.baseline == pytest.approx(b.offset.dy + b.baseline)

    def test_per_child_align_overrides_container(self):
        row = RenderRow(align=CrossAxisAlignment.START, debug_name="Row")
        a = row.add(sized(20, 10, "A")).child
        b = row.add(sized(20, 10, "B"), align=CrossAxisAlignment.END).child
        row.add(sized(20, 40, "Tall"))  # 把交叉轴撑到 40
        row.layout(BoxConstraints(max_width=400, max_height=40))
        assert a.offset.dy == pytest.approx(0.0)
        assert b.offset.dy == pytest.approx(30.0)


class TestFlexWeights:
    def test_single_flex_child_takes_remaining_space(self):
        row = RenderRow(debug_name="Row")
        row.add(sized(40, 20, "A"))
        c = row.add(RenderSized(height=Sizing.fixed(20), debug_name="C"), flex=1).child
        row.layout(BoxConstraints(max_width=300))
        assert c.size.width == pytest.approx(260.0)
        assert row.size.width == pytest.approx(300.0)

    def test_weights_split_remaining_space(self):
        row = RenderRow(debug_name="Row")
        a = row.add(RenderSized(height=Sizing.fixed(10), debug_name="A"), flex=1).child
        b = row.add(RenderSized(height=Sizing.fixed(10), debug_name="B"), flex=3).child
        row.layout(BoxConstraints(max_width=200))
        assert a.size.width == pytest.approx(50.0)
        assert b.size.width == pytest.approx(150.0)

    def test_even_split_is_exact(self):
        row = RenderRow(debug_name="Row")
        children = [
            row.add(RenderSized(height=Sizing.fixed(10), debug_name=f"C{i}"), flex=1).child
            for i in range(3)
        ]
        row.layout(BoxConstraints(max_width=300))
        assert [c.size.width for c in children] == pytest.approx([100.0, 100.0, 100.0])

    def test_float_remainder_is_absorbed_by_last_flex_child(self):
        """100 分给 3 个等权子级除不尽。

        前 n-1 个各拿 space_per_flex*flex，最后一个拿"剩下的全部"，
        于是浮点误差全部沉淀在末尾——同一棵树反复布局，结果逐位相同。
        """
        row = RenderRow(debug_name="Row")
        children = [
            row.add(RenderSized(height=Sizing.fixed(10), debug_name=f"C{i}"), flex=1).child
            for i in range(3)
        ]

        snapshots = []
        for _ in range(3):
            row.mark_needs_layout()
            row.layout(BoxConstraints(max_width=100))
            snapshots.append(tuple(c.size.width for c in children))

        assert len(set(snapshots)) == 1, "重复布局必须得到逐位相同的几何"
        assert sum(snapshots[0]) == pytest.approx(100.0)
        assert snapshots[0][0] == pytest.approx(snapshots[0][1])

    def test_loose_fit_allows_child_to_be_smaller(self):
        row = RenderRow(debug_name="Row")
        c = row.add(sized(30, 10, "C"), flex=1, fit=FlexFit.LOOSE).child
        row.layout(BoxConstraints(max_width=100))
        assert c.size.width == pytest.approx(30.0)

    def test_tight_fit_forces_child_to_fill(self):
        row = RenderRow(debug_name="Row")
        c = row.add(sized(30, 10, "C"), flex=1, fit=FlexFit.TIGHT).child
        row.layout(BoxConstraints(max_width=100))
        assert c.size.width == pytest.approx(100.0)

    def test_flex_child_in_column_grows_vertically(self):
        col = RenderColumn(debug_name="Col")
        a = col.add(sized(10, 20, "A")).child
        b = col.add(RenderSized(width=Sizing.fixed(10), debug_name="B"), flex=1).child
        col.layout(BoxConstraints(max_width=100, max_height=100))
        assert a.size.height == pytest.approx(20.0)
        assert b.size.height == pytest.approx(80.0)


class TestUnboundedConstraints:
    def test_flex_child_with_unbounded_main_axis_raises(self):
        row = RenderRow(debug_name="Row")
        row.add(RenderSized(height=Sizing.fixed(10), debug_name="Fill"), flex=1)
        with pytest.raises(LayoutError) as exc:
            row.layout(BoxConstraints())
        text = str(exc.value)
        assert "无限宽度约束" in text
        assert "Row" in text

    def test_non_flex_children_survive_unbounded_main_axis(self):
        row = RenderRow(debug_name="Row")
        a = row.add(sized(30, 10, "A")).child
        row.layout(BoxConstraints())
        assert a.size.width == pytest.approx(30.0)
        assert row.size.width == pytest.approx(30.0)

    def test_error_message_points_at_the_offending_node(self):
        col = RenderColumn(debug_name="Body")
        row = RenderRow(debug_name="Row")
        col.add(row)
        row.add(RenderSized(height=Sizing.fixed(10), debug_name="Fill"), flex=1)
        # 只给高度上界 → Column 交叉轴（宽度）无界 → 内层 Row 的主轴无界
        with pytest.raises(LayoutError) as exc:
            col.layout(BoxConstraints(max_height=100))
        assert "Body > Row[0]" in str(exc.value)


class TestShrinkAndOverflow:
    def test_overflow_is_recorded_not_silently_clipped(self):
        row = RenderRow(debug_name="Row")
        row.add(sized(80, 10, "A"))
        row.add(sized(80, 10, "B"))
        row.layout(BoxConstraints(max_width=100))
        assert row.has_overflow
        assert row.overflow == pytest.approx(60.0)

    def test_children_without_shrink_are_not_squeezed(self):
        row = RenderRow(debug_name="Row")
        a = row.add(sized(80, 10, "A")).child
        row.add(sized(80, 10, "B"))
        row.layout(BoxConstraints(max_width=100))
        assert a.size.width == pytest.approx(80.0)

    def test_shrink_weights_absorb_overflow(self):
        row = RenderRow(debug_name="Row")
        a = row.add(sized(80, 10, "A"), shrink=1).child
        b = row.add(sized(80, 10, "B"), shrink=1).child
        row.layout(BoxConstraints(max_width=100))
        assert a.size.width == pytest.approx(50.0)
        assert b.size.width == pytest.approx(50.0)
        assert not row.has_overflow

    def test_shrink_is_proportional_to_weight(self):
        row = RenderRow(debug_name="Row")
        a = row.add(sized(100, 10, "A"), shrink=1).child
        b = row.add(sized(100, 10, "B"), shrink=3).child
        row.layout(BoxConstraints(max_width=100))  # 溢出 100
        assert a.size.width == pytest.approx(75.0)
        assert b.size.width == pytest.approx(25.0)


class TestMarginAndPadding:
    def test_margin_consumes_space_and_offsets_child(self):
        row = RenderRow(debug_name="Row")
        a = sized(20, 10, "A")
        a.margin = EdgeInsets.symmetric(horizontal=5)
        b = sized(20, 10, "B")
        row.add(a)
        row.add(b)
        row.layout(BoxConstraints(max_width=400))

        assert row.size.width == pytest.approx(50.0)  # 5 + 20 + 5 + 20
        assert a.offset.dx == pytest.approx(5.0)
        assert b.offset.dx == pytest.approx(30.0)

    def test_padding_offsets_all_children(self):
        row = RenderRow(padding=EdgeInsets.all(8), debug_name="Row")
        a = row.add(sized(20, 10, "A")).child
        row.layout(BoxConstraints(max_width=400))
        assert row.size.width == pytest.approx(36.0)
        assert a.offset.dx == pytest.approx(8.0)
        assert a.offset.dy == pytest.approx(8.0)


class TestDeterminism:
    def test_relayout_produces_identical_geometry(self):
        def build() -> RenderRow:
            row = RenderRow(gap=6, align=CrossAxisAlignment.CENTER, debug_name="Row")
            row.add(sized(17, 13, "A"))
            row.add(sized(29, 41, "B"), flex=1)
            row.add(sized(7, 5, "C"), flex=2)
            row.layout(BoxConstraints(max_width=333, max_height=200))
            return row

        first = build()
        second = build()
        assert [describe(c) for c in first.children] == [describe(c) for c in second.children]
        assert first.size == second.size


def describe(box: RenderSized) -> tuple[float, float, float, float]:
    return (box.offset.dx, box.offset.dy, box.size.width, box.size.height)


class TestPureDistributeFunctions:
    def test_distribute_main_start(self):
        assert distribute_main([10, 20], 100, MainAxisAlignment.START) == pytest.approx([0, 10])

    def test_distribute_main_with_gap(self):
        assert distribute_main([10, 20], 100, MainAxisAlignment.START, 5) == pytest.approx([0, 15])

    def test_distribute_main_empty(self):
        assert distribute_main([], 100, MainAxisAlignment.CENTER) == []

    def test_distribute_main_space_between_single_child(self):
        # 只有一个子级时没有"之间"可言，摆在起点
        assert distribute_main([10], 100, MainAxisAlignment.SPACE_BETWEEN) == pytest.approx([0])

    def test_distribute_cross_baseline_aligns_reference(self):
        result = distribute_cross([30, 40], 40, CrossAxisAlignment.BASELINE, [20, 10])
        assert result == pytest.approx([0, 10])

    def test_distribute_cross_baseline_with_missing_baseline(self):
        result = distribute_cross([10, 10], 40, CrossAxisAlignment.BASELINE, [None, 0.0])
        assert result == pytest.approx([0, 0])

    def test_distribute_cross_stretch_is_always_zero(self):
        assert distribute_cross([10, 20], 50, CrossAxisAlignment.STRETCH) == pytest.approx([0, 0])
