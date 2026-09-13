"""ScrollView 的单元测试。

核心是 docs/05 §6 那条规则：**滚动容器给子级的必须是无限主轴约束**。
这条做错的表现是"滚动区里的 fill 子级把整个窗口撑爆"，所以这里专门测它。

后半部分（`TestViewportClipping`）跨到绘制侧：滚动容器还必须**裁剪视口**，
否则滚出去的内容会画在视口外面。那条链路要跑显示列表与软件光栅才看得见。
"""

import pytest

from inkstone.gfx import (
    DisplayList,
    DisplayListRecorder,
    FrameBuffer,
    SoftwareRasterizer,
    resolve_state_ops,
)
from inkstone.gfx.color import Color
from inkstone.layout import (
    BoxConstraints,
    EdgeInsets,
    LayoutError,
    RenderBox,
    RenderColumn,
    RenderContainer,
    RenderScroll,
    RenderSized,
    ScrollDirection,
    Sizing,
)
from inkstone.layout.types import Rect, Size


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


# ================================================================ 视口裁剪

_BG = Color.from_hex("#FF00FF")
_INK = Color.from_hex("#FFFFFF")


class _PaintableBox(RenderBox):
    """会真的往显示列表里录一条指令的叶子——用来断言"画在哪、有没有画"。"""

    def __init__(self, width: float, height: float, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.box_width = width
        self.box_height = height

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(self.box_width, self.box_height))

    def paint(self, context: object) -> None:  # type: ignore[override]
        fill = getattr(context, "fill_rect", None)
        if fill is not None:
            fill(Rect(0.0, 0.0, self.size.width, self.size.height), _INK)


def _long_list(count: int = 6, item_height: float = 40.0) -> RenderColumn:
    col = RenderColumn(debug_name="List")
    for i in range(count):
        col.add(_PaintableBox(100.0, item_height, debug_name=f"I{i}"))
    return col


def _record(root: RenderBox, width: int, height: int) -> DisplayList:
    recorder = DisplayListRecorder()
    root.paint_tree(recorder)
    return recorder.finish(width, height)


def _rasterize(display_list: DisplayList) -> FrameBuffer:
    """走一遍帧生命周期（R3.1 之后光栅的唯一入口）。"""
    raster = SoftwareRasterizer()
    raster.begin_frame(Size(float(display_list.width), float(display_list.height)), 1.0)
    raster.execute(display_list)
    raster.end_frame()
    return raster.screenshot()


class TestViewportClipping:
    """滚出视口的内容必须被裁掉——`paint_tree` 只 translate，不 clip。"""

    VIEWPORT = (120, 100)

    def _scrolled(self) -> RenderScroll:
        scroll = RenderScroll(_long_list(), debug_name="Scroll")
        constraints = BoxConstraints(max_width=120, max_height=100)
        scroll.layout(constraints)
        _record(scroll, *self.VIEWPORT)  # 先清干净
        scroll.scroll_to(dy=50)
        scroll.layout(constraints)
        return scroll

    def test_scrolled_ops_carry_the_viewport_clip(self) -> None:
        """R8.4 后裁剪由状态指令提供；这里断言**展开后**的等价语义。"""
        scroll = self._scrolled()
        dl = _record(scroll, *self.VIEWPORT)

        ops = list(resolve_state_ops(dl.ops))
        assert len(ops) == 6
        expected = Rect(0.0, 0.0, 120.0, 100.0)
        for op in ops:
            assert op.clip == expected, f"视口外的指令没带裁剪：{op}"

    def test_clip_is_relative_to_the_scroll_node(self) -> None:
        """裁剪矩形是**本节点**的 bounds，父级给的偏移不算在里面。"""
        scroll = RenderScroll(_long_list(), debug_name="Scroll")
        holder = RenderContainer(
            scroll,
            width=Sizing.fixed(160),
            height=Sizing.fixed(140),
            padding=EdgeInsets.all(20),
            debug_name="Holder",
        )
        constraints = BoxConstraints(max_width=160, max_height=140)
        holder.layout(constraints)
        _record(holder, 160, 140)
        scroll.scroll_to(dy=50)
        holder.layout(constraints)

        dl = _record(holder, 160, 140)
        expected = Rect(20.0, 20.0, 120.0, 100.0)
        for op in resolve_state_ops(dl.ops):
            assert op.clip == expected

    def test_pixels_outside_the_viewport_stay_clean(self) -> None:
        """端到端：光栅出图后，视口外不许有墨迹。"""
        scroll = RenderScroll(_long_list(), debug_name="Scroll")
        holder = RenderContainer(
            scroll,
            width=Sizing.fixed(160),
            height=Sizing.fixed(140),
            padding=EdgeInsets.all(20),
            debug_name="Holder",
        )
        constraints = BoxConstraints(max_width=160, max_height=140)
        holder.layout(constraints)
        _record(holder, 160, 140)
        scroll.scroll_to(dy=50)
        holder.layout(constraints)

        recorder = DisplayListRecorder()
        recorder.fill_rect(Rect(0.0, 0.0, 160.0, 140.0), _BG)
        holder.paint_tree(recorder)
        fb = _rasterize(recorder.finish(160, 140))

        # 视口 = (20,20) 起、120×100；外面那一圈必须是底色
        for y in range(140):
            for x in range(160):
                inside = 20 <= x < 140 and 20 <= y < 120
                if not inside:
                    assert fb.pixel(x, y)[:3] == (_BG.r, _BG.g, _BG.b), (
                        f"视口外的 ({x},{y}) 有墨迹——内容没被裁掉"
                    )
        # 视口内必须有内容（否则这个测试只是在验证"什么都没画"）
        assert fb.pixel(80, 80)[:3] == (_INK.r, _INK.g, _INK.b)

    def test_without_scrolling_nothing_is_clipped_away_inside_the_viewport(self) -> None:
        scroll = RenderScroll(_long_list(), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120, max_height=100))
        dl = _record(scroll, *self.VIEWPORT)
        ops = list(resolve_state_ops(dl.ops))
        assert len(ops) == 6, "视口内的内容不许被裁掉"
        assert all(op.clip == Rect(0.0, 0.0, 120.0, 100.0) for op in ops)
