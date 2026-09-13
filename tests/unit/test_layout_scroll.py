"""ScrollView 的单元测试。

核心是 docs/05 §6 那条规则：**滚动容器给子级的必须是无限主轴约束**。
这条做错的表现是"滚动区里的 fill 子级把整个窗口撑爆"，所以这里专门测它。

后半部分（`TestViewportClipping`）跨到绘制侧：滚动容器还必须**裁剪视口**，
否则滚出去的内容会画在视口外面。那条链路要跑显示列表与软件光栅才看得见。
"""

import pytest

from inkstone.backend.base import PointerEvent, PointerKind
from inkstone.core import BuildOwner
from inkstone.events.pointer import HitTestResult, PointerRouter
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
    ScrollbarStyle,
    ScrollDirection,
    Sizing,
)
from inkstone.layout.types import Offset, Rect, Size


def tall_list(count: int = 5, item_height: float = 40.0) -> RenderColumn:
    col = RenderColumn(debug_name="List")
    for i in range(count):
        col.add(
            RenderSized(
                width=Sizing.fixed(100), height=Sizing.fixed(item_height), debug_name=f"I{i}"
            )
        )
    return col


def fluid_list(count: int = 5, item_height: float = 40.0) -> RenderColumn:
    """宽度**跟着约束走**的列表——用来观察"滚动条槽位"这类对可用宽度的改动。

    `tall_list` 用的是固定宽度子级，约束变化看不出来；这里换成 fill。
    """
    col = RenderColumn(debug_name="Fluid")
    for i in range(count):
        col.add(
            RenderSized(height=Sizing.fixed(item_height), width=Sizing.fill(), debug_name=f"F{i}")
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


class TestWheelScrolling:
    """鼠标滚轮（R12）——Phase 1 DoD 的「能滚」在桌面上就是它。

    滚轮不是手势（没有 DOWN/UP），所以不进竞技场，走 `handle_pointer_event`
    这条非手势输入的分发缝。这里既测渲染对象本身，也测**嵌套冒泡**：
    内层滚到底之后外层接管——这条不写特判，靠"偏移没变就不叫停传播"成立。
    """

    @staticmethod
    def _wheel(dy: float, dx: float = 0.0) -> PointerEvent:
        return PointerEvent(kind=PointerKind.WHEEL, x=10.0, y=10.0, wheel_dx=dx, wheel_dy=dy)

    @staticmethod
    def _dispatch(scroll: RenderScroll, event: PointerEvent) -> None:
        result = HitTestResult()
        scroll.hit_test(Offset(10.0, 10.0), result)
        PointerRouter().dispatch(result, event)

    def _viewport(self) -> RenderScroll:
        scroll = RenderScroll(tall_list(5, 40.0), debug_name="Scroll")
        scroll.layout(BoxConstraints(max_width=120.0, max_height=100.0))
        return scroll

    def test_wheel_down_scrolls_down(self) -> None:
        scroll = self._viewport()
        self._dispatch(scroll, self._wheel(-1.0))
        assert scroll.scroll_offset.dy == pytest.approx(scroll.wheel_step)

    def test_wheel_up_scrolls_back(self) -> None:
        scroll = self._viewport()
        self._dispatch(scroll, self._wheel(-2.0))
        self._dispatch(scroll, self._wheel(1.0))
        assert scroll.scroll_offset.dy == pytest.approx(scroll.wheel_step)

    def test_wheel_is_clamped_to_max_scroll(self) -> None:
        scroll = self._viewport()
        self._dispatch(scroll, self._wheel(-100.0))
        assert scroll.scroll_offset.dy == pytest.approx(scroll.max_scroll.dy)

    def test_horizontal_wheel_ignores_vertical_scroller(self) -> None:
        """纵向滚动容器不吃横向滚轮——否则触控板横滑会把列表带偏。"""
        scroll = self._viewport()
        self._dispatch(scroll, self._wheel(0.0, dx=1.0))
        assert scroll.scroll_offset.dx == 0.0

    def test_horizontal_scroller_follows_horizontal_wheel(self) -> None:
        scroll = RenderScroll(
            wide_content(300.0, 40.0), direction=ScrollDirection.HORIZONTAL, debug_name="H"
        )
        scroll.layout(BoxConstraints(max_width=120.0, max_height=100.0))
        self._dispatch(scroll, self._wheel(0.0, dx=-1.0))
        assert scroll.scroll_offset.dx == pytest.approx(scroll.wheel_step)

    def test_exhausted_scroller_lets_the_ancestor_take_over(self) -> None:
        """嵌套滚动：内层到底后再滚，外层接管——结构成立，不写特判。"""
        inner = RenderScroll(tall_list(5, 40.0), debug_name="Inner")
        wrapper = RenderContainer(inner, width=Sizing.fixed(100.0), height=Sizing.fixed(80.0))
        content = RenderColumn(debug_name="OuterContent")
        content.add(wrapper)
        content.add(RenderSized(width=Sizing.fixed(100.0), height=Sizing.fixed(100.0)))
        outer = RenderScroll(content, debug_name="Outer")
        outer.layout(BoxConstraints(max_width=120.0, max_height=100.0))

        inner.scroll_to(dy=inner.max_scroll.dy)  # 内层先滚到底
        inner_max = inner.scroll_offset.dy
        assert inner_max > 0.0, "内层得真的能滚，这条测试才有意义"

        self._dispatch(outer, self._wheel(-1.0))

        assert inner.scroll_offset.dy == pytest.approx(inner_max), "内层已到底，不该再动"
        assert outer.scroll_offset.dy == pytest.approx(outer.wheel_step), "该由外层接管"


class TestScrollViewWheelWiring:
    """组件层只做一件事：把主题令牌送进渲染对象（布局层不读主题）。

    滚轮步长如果忘了送，渲染对象会用它自己的默认值——行为看起来对，
    但主题改了不生效。这条测试钉住"令牌真的接上了"。
    """

    def test_wheel_step_comes_from_the_theme_token(self) -> None:
        from inkstone.core import BuildOwner
        from inkstone.style import Theme
        from inkstone.widgets import Column, ScrollView, Text

        owner = BuildOwner(theme=Theme.light())
        owner.mount(ScrollView(Column(children=[Text("一"), Text("二")])))
        owner.begin_frame(BoxConstraints(max_width=200.0, max_height=100.0))

        found: list[RenderScroll] = []

        def walk(node: object) -> None:
            if isinstance(node, RenderScroll):
                found.append(node)
            for child in getattr(node, "children", ()) or ():
                walk(child)

        walk(owner.root_render_object)
        assert found, "挂载 ScrollView 之后应该能在渲染树里找到 RenderScroll"
        expected = Theme.light().gesture("wheel_step")
        assert found[0].wheel_step == pytest.approx(expected)


def _scrollbar_style(**overrides: object) -> ScrollbarStyle:
    """测试用的滚动条外观：与 tokens.scrollbar 同档，可逐项覆盖。"""
    base = {
        "thickness": 10.0,
        "hover_thickness": 14.0,
        "min_thumb": 32.0,
        "radius": 5.0,
        "margin": 2.0,
        "color": Color.from_rgba(0, 0, 0, 0.5),
        "hover_color": Color.from_rgba(0, 0, 0, 0.8),
        "hold_ms": 900.0,
        "fade_ms": 250.0,
    }
    base.update(overrides)
    return ScrollbarStyle(**base)  # type: ignore[arg-type]


class TestScrollbarGeometry:
    """滚动条拇指的几何是纯函数（视口/内容/偏移 → 矩形），先把它测穿。

    渲染层拿到的是"画在哪"，算错的表现是拇指跑出轨道、长度不随内容变化、
    或者内容装得下时还杵着一根条——都是肉眼可见的错。
    """

    @staticmethod
    def _styled(count: int = 20, item: float = 40.0, viewport: float = 100.0) -> RenderScroll:
        scroll = RenderScroll(tall_list(count, item), debug_name="Scroll")
        scroll.scrollbar = _scrollbar_style()
        scroll.layout(BoxConstraints(max_width=120.0, max_height=viewport))
        return scroll

    def test_no_thumb_when_content_fits(self) -> None:
        """装得下就不画——"这里没东西可滚"要被诚实表达，也不给静态页加噪音。"""
        scroll = self._styled(count=2, item=20.0)
        assert not scroll.can_scroll
        assert scroll.thumb_rect() is None

    def test_thumb_sits_against_the_right_edge(self) -> None:
        scroll = self._styled()
        rect = scroll.thumb_rect()
        assert rect is not None
        assert rect.width == pytest.approx(10.0)
        assert rect.left == pytest.approx(120.0 - 10.0 - 2.0)
        assert rect.top == pytest.approx(0.0), "在顶部时拇指贴顶"

    def test_thumb_length_is_proportional_and_has_a_floor(self) -> None:
        scroll = self._styled(count=20, item=40.0, viewport=100.0)
        rect = scroll.thumb_rect()
        assert rect is not None
        # 视口 100 / 内容 800 → 拇指 12.5px，被最小长度 32 顶住
        assert rect.height == pytest.approx(32.0)

    def test_thumb_grows_with_shorter_content(self) -> None:
        short = self._styled(count=5, item=40.0, viewport=100.0)  # 内容 200，视口 100
        long = self._styled(count=20, item=40.0, viewport=100.0)  # 内容 800
        short_rect, long_rect = short.thumb_rect(), long.thumb_rect()
        assert short_rect is not None and long_rect is not None
        assert short_rect.height > long_rect.height

    def test_thumb_reaches_the_bottom_at_max_scroll(self) -> None:
        scroll = self._styled()
        scroll.scroll_to(dy=scroll.max_scroll.dy)
        rect = scroll.thumb_rect()
        assert rect is not None
        assert rect.bottom == pytest.approx(100.0), "滚到底时拇指必须贴底，否则像没滚完"

    def test_thumb_travels_proportionally(self) -> None:
        scroll = self._styled()
        scroll.scroll_to(dy=scroll.max_scroll.dy / 2.0)
        rect = scroll.thumb_rect()
        assert rect is not None
        assert rect.top == pytest.approx((100.0 - rect.height) / 2.0, abs=0.5)

    def test_horizontal_scrollbar_sits_against_the_bottom(self) -> None:
        scroll = RenderScroll(
            wide_content(600.0, 40.0), direction=ScrollDirection.HORIZONTAL, debug_name="H"
        )
        scroll.scrollbar = _scrollbar_style()
        scroll.layout(BoxConstraints(max_width=120.0, max_height=100.0))
        rect = scroll.thumb_rect()
        assert rect is not None
        assert rect.height == pytest.approx(10.0)
        assert rect.top == pytest.approx(100.0 - 10.0 - 2.0)


class TestScrollbarPainting:
    """拇指画在专用槽位里；内容为它让位，两者不重叠（ADR-0026）。"""

    def _record(self, *, fluid: bool = False) -> tuple[RenderScroll, list[object]]:
        content = fluid_list(20, 40.0) if fluid else tall_list(20, 40.0)
        scroll = RenderScroll(content, debug_name="Scroll")
        scroll.scrollbar = _scrollbar_style()
        scroll.layout(BoxConstraints(max_width=120.0, max_height=100.0))
        recorder = DisplayListRecorder()
        recorder.fill_rect(Rect(0.0, 0.0, 120.0, 100.0), _BG)
        scroll.mark_subtree_needs_paint()
        scroll.paint_tree(recorder)
        return scroll, list(resolve_state_ops(recorder.finish(120, 100).ops))

    def test_thumb_is_painted_after_the_content(self) -> None:
        scroll, ops = self._record()
        rect = scroll.thumb_rect()
        assert rect is not None
        # 拇指是最后一个圆角矩形，且几何与 thumb_rect 一致
        last = ops[-1]
        assert getattr(last, "rect", None) == rect
        assert getattr(last, "radius", None) == pytest.approx(5.0)

    def test_overlay_scrollbar_reserves_no_space(self) -> None:
        """**覆盖式**：滚动条不占布局宽度（ADR-0026 v2，Chromium/Flutter 路线）。

        内容按整个视口排版，拇指浮在右缘之上；代价是出现时短暂压住右缘内容，
        收益是不因"刚好溢出"而整体重排一次。"""
        scroll, _ = self._record(fluid=True)
        assert scroll.content_size.width == pytest.approx(120.0)

    def test_thumb_hugs_the_right_edge(self) -> None:
        """拇指贴在视口右缘内侧（留 margin 的缝），这是覆盖层该有的位置。"""
        scroll, _ = self._record(fluid=True)
        rect = scroll.thumb_rect()
        assert rect is not None
        assert rect.right == pytest.approx(120.0 - 2.0)

    def test_pointer_over_the_thumb_switches_to_the_hover_color(self) -> None:
        scroll, ops = self._record()
        rect = scroll.thumb_rect()
        assert rect is not None
        idle = ops[-1].color
        result = HitTestResult()
        scroll.hit_test(Offset(rect.left + 2.0, rect.top + 2.0), result)
        PointerRouter().dispatch(
            result,
            PointerEvent(kind=PointerKind.MOVE, x=0.0, y=0.0, window_id=0, time_ms=0.0),
        )
        assert scroll._thumb_hover is True
        scroll.mark_subtree_needs_paint()
        recorder = DisplayListRecorder()
        scroll.paint_tree(recorder)
        hovered = list(resolve_state_ops(recorder.finish(120, 100).ops))[-1].color
        assert hovered != idle, "悬停时拇指必须变亮，否则用户不知道它可拖"


class TestScrollbarHoverDoesNotDisturbScrolling:
    """悬停只改颜色——曾经怀疑它会把偏移打回 0（真机截图有歧义）。

    这条用确定性事件把它钉死：移动指针到拇指上不得改变偏移，
    否则"鼠标划过滚动条，列表跳回顶部"会成为最难查的那类 bug。
    """

    def test_hovering_the_thumb_keeps_the_offset(self) -> None:
        scroll = TestScrollbarGeometry._styled()
        scroll.scroll_to(dy=120.0)
        offset = scroll.scroll_offset
        rect = scroll.thumb_rect()
        assert rect is not None

        result = HitTestResult()
        scroll.hit_test(Offset(rect.left + 2.0, rect.top + 2.0), result)
        router = PointerRouter()
        for _ in range(3):
            router.dispatch(
                result,
                PointerEvent(kind=PointerKind.MOVE, x=0.0, y=0.0, window_id=0, time_ms=0.0),
            )
        assert scroll.scroll_offset == offset
        assert scroll._thumb_hover is True

    def test_leaving_the_thumb_clears_hover_without_scrolling(self) -> None:
        scroll = TestScrollbarGeometry._styled()
        scroll.scroll_to(dy=120.0)
        offset = scroll.scroll_offset
        rect = scroll.thumb_rect()
        assert rect is not None
        # 指针在视口内、但不在拇指上（偏左）
        result = HitTestResult()
        scroll.hit_test(Offset(10.0, 10.0), result)
        PointerRouter().dispatch(
            result,
            PointerEvent(kind=PointerKind.MOVE, x=0.0, y=0.0, window_id=0, time_ms=0.0),
        )
        assert scroll._thumb_hover is False
        assert scroll.scroll_offset == offset


class TestScrollbarThumbDrag:
    """拖拇指改偏移（R13.2）。映射是纯几何，先测穿。

    命中判定与抓取点都按**视口局部坐标**；绝对定位（抓住哪一点就跟着走），
    不是增量拖动——增量会让拇指与指针逐渐错位。
    """

    def test_grab_keeps_the_offset_while_the_pointer_has_not_moved(self) -> None:
        scroll = TestScrollbarGeometry._styled()
        rect = scroll.thumb_rect()
        assert rect is not None
        scroll.begin_thumb_drag(rect.left + 2.0, rect.top + 3.0)
        assert scroll._thumb_drag is True
        assert scroll.scroll_offset.dy == 0.0, "只按下不该滚动"

    def test_dragging_to_the_bottom_reaches_max_scroll(self) -> None:
        scroll = TestScrollbarGeometry._styled()
        rect = scroll.thumb_rect()
        assert rect is not None
        grab_y = rect.top + 3.0
        scroll.begin_thumb_drag(rect.left + 2.0, grab_y)
        scroll.drag_thumb_to(rect.left + 2.0, 100.0)  # 拖到视口底
        assert scroll.scroll_offset.dy == pytest.approx(scroll.max_scroll.dy)
        scroll.end_thumb_drag()
        assert scroll._thumb_drag is False

    def test_dragging_to_the_top_reaches_zero(self) -> None:
        scroll = TestScrollbarGeometry._styled()
        scroll.scroll_to(dy=scroll.max_scroll.dy)
        rect = scroll.thumb_rect()
        assert rect is not None
        scroll.begin_thumb_drag(rect.left + 2.0, rect.top + 3.0)
        scroll.drag_thumb_to(rect.left + 2.0, 3.0)
        assert scroll.scroll_offset.dy == pytest.approx(0.0)

    def test_grab_point_is_preserved_so_the_thumb_does_not_jump(self) -> None:
        """按在拇指下缘再拖：拇指不该"跳"到指针对齐。"""
        scroll = TestScrollbarGeometry._styled()
        rect = scroll.thumb_rect()
        assert rect is not None
        depth = rect.height - 2.0  # 抓住拇指底部附近
        scroll.begin_thumb_drag(rect.left + 2.0, rect.top + depth)
        scroll.drag_thumb_to(rect.left + 2.0, rect.top + depth + 10.0)
        moved = scroll.scroll_offset.dy
        assert moved > 0.0
        # 拇指位移与指针位移一致（不是跳到指针处）
        new_rect = scroll.thumb_rect()
        assert new_rect is not None
        assert new_rect.top == pytest.approx(rect.top + 10.0, abs=1.0)


class TestThumbDragInsideAScrollView:
    """整链：ScrollView 的把手识别器 + 内容拖拽识别器共处一个竞技场。"""

    @staticmethod
    def _owner() -> tuple[BuildOwner, RenderScroll]:
        from inkstone.style import Theme
        from inkstone.widgets import Box, Column, ScrollView

        # 用固定高度的 Box 而不是 Text：这条测试不关心度量，而没注入 text_engine
        # 的 owner 会把 Text 量成 0 高，"可滚动"就不存在了。
        owner = BuildOwner(theme=Theme.light())
        owner.mount(ScrollView(Column(children=[Box(height=30.0) for _ in range(40)])))
        owner.begin_frame(BoxConstraints(max_width=200.0, max_height=120.0))
        found: list[RenderScroll] = []

        def walk(node: object) -> None:
            if isinstance(node, RenderScroll):
                found.append(node)
            for child in getattr(node, "children", ()) or ():
                walk(child)

        walk(owner.root_render_object)
        assert found
        return owner, found[0]

    def test_two_recognizers_are_registered(self) -> None:
        _, scroll = self._owner()
        kinds = [type(r).__name__ for r in scroll.recognizers]
        assert kinds == ["DragGestureRecognizer", "HandleDragRecognizer"]

    def test_dragging_the_thumb_scrolls_and_releasing_ends_the_drag(self) -> None:
        owner, scroll = self._owner()
        assert scroll.can_scroll
        rect = scroll.thumb_rect()
        assert rect is not None
        origin = scroll.local_to_global(Offset(0.0, 0.0))
        thumb_x = origin.dx + rect.left + rect.width / 2.0
        thumb_y = origin.dy + rect.top + 2.0

        owner.dispatch_pointer(
            PointerEvent(kind=PointerKind.DOWN, x=thumb_x, y=thumb_y, window_id=0, button=1)
        )
        assert scroll.scroll_offset.dy == 0.0, "按住拇指不移动时不滚动"
        owner.dispatch_pointer(
            PointerEvent(kind=PointerKind.MOVE, x=thumb_x, y=thumb_y + 40.0, window_id=0)
        )
        assert scroll.scroll_offset.dy > 0.0, "往下拖拇指必须滚动内容"
        owner.dispatch_pointer(
            PointerEvent(kind=PointerKind.UP, x=thumb_x, y=thumb_y + 40.0, window_id=0)
        )
        assert scroll._thumb_drag is False

    def test_a_press_on_content_does_not_scroll_before_the_slop(self) -> None:
        """整链回归：把手识别器**不能**把内容拖拽的 slop 门槛弄丢。

        这正是 `HandleDragRecognizer` 用 hold 退场而不是直接 reject 的原因——
        没有这条，鼠标在列表上轻微一抖就会滚动（ADR-0014 修掉的 bug）。
        """
        owner, scroll = self._owner()
        origin = scroll.local_to_global(Offset(0.0, 0.0))
        owner.dispatch_pointer(
            PointerEvent(
                kind=PointerKind.DOWN,
                x=origin.dx + 40.0,
                y=origin.dy + 40.0,
                window_id=0,
                button=1,
            )
        )
        for delta in (1.0, 2.0, 3.0):  # 都没超过 tap_slop=8
            owner.dispatch_pointer(
                PointerEvent(
                    kind=PointerKind.MOVE,
                    x=origin.dx + 40.0,
                    y=origin.dy + 40.0 + delta,
                    window_id=0,
                )
            )
        assert scroll.scroll_offset.dy == 0.0, "8px 以内的抖动不许滚动"
        owner.dispatch_pointer(
            PointerEvent(
                kind=PointerKind.MOVE,
                x=origin.dx + 40.0,
                y=origin.dy + 40.0 + 40.0,
                window_id=0,
            )
        )
        assert scroll.scroll_offset.dy > 0.0, "超过 slop 后必须能滚"


class TestScrollbarAutoHide:
    """覆盖式滚动条的可见度（R14.2）：活动时亮起，闲置后淡出。

    时间一律走 `tick(now_ms)` 注入——这条链路在无头测试里完全确定。
    """

    def _element_and_scroll(self, *, reduced: bool = False) -> tuple[object, RenderScroll]:
        from inkstone.motion import reduced_motion
        from inkstone.style import Theme
        from inkstone.widgets import Box, Column, ScrollView

        # 显式设定：否则同一条测试在开了/没开"减少动画"的机器上行为不同
        reduced_motion.set_reduced_motion(reduced)
        owner = BuildOwner(theme=Theme.light())
        owner.mount(ScrollView(Column(children=[Box(height=30.0) for _ in range(40)])))
        owner.begin_frame(BoxConstraints(max_width=200.0, max_height=120.0), now_ms=0.0)
        found: list[RenderScroll] = []

        def walk(node: object) -> None:
            if isinstance(node, RenderScroll):
                found.append(node)
            for child in getattr(node, "children", ()) or ():
                walk(child)

        walk(owner.root_render_object)
        return owner, found[0]

    def test_scrolling_wakes_the_bar(self) -> None:
        owner, scroll = self._element_and_scroll()
        scroll.opacity = 0.0
        origin = scroll.local_to_global(Offset(0.0, 0.0))
        owner.dispatch_pointer(
            PointerEvent(
                kind=PointerKind.WHEEL,
                x=origin.dx + 20.0,
                y=origin.dy + 20.0,
                window_id=0,
                wheel_dy=-1.0,
                time_ms=100.0,
            )
        )
        assert scroll.opacity == 1.0, "滚一下必须把条亮起来"

    def test_bar_fades_out_after_the_hold_window(self) -> None:
        owner, scroll = self._element_and_scroll()
        style = scroll.scrollbar
        assert style is not None
        scroll.wake(0.0)
        # 指针不在视口内：hold 窗口内仍然可见
        owner.begin_frame(
            BoxConstraints(max_width=200.0, max_height=120.0), now_ms=style.hold_ms - 1
        )
        assert scroll.opacity == 1.0
        # 过了 hold + fade：淡到 0 并**停止排帧**（空闲不留帧）
        owner.begin_frame(
            BoxConstraints(max_width=200.0, max_height=120.0),
            now_ms=style.hold_ms + style.fade_ms + 10.0,
        )
        assert scroll.opacity == 0.0
        assert owner.ticker.active == 0, "淡出跑完就该退出排帧集合"

    def test_reduced_motion_hides_instantly(self) -> None:
        from inkstone.motion import reduced_motion

        try:
            owner, scroll = self._element_and_scroll(reduced=True)
            scroll.wake(0.0)
            owner.begin_frame(BoxConstraints(max_width=200.0, max_height=120.0), now_ms=1000.0)
            assert scroll.opacity == 0.0, "减少动效时应瞬时到位，不播淡出"
        finally:
            reduced_motion.reset_cache()

    def test_hovering_the_bar_makes_it_thicker(self) -> None:
        _, scroll = self._element_and_scroll()
        rect = scroll.thumb_rect()
        assert rect is not None
        thin = rect.width
        scroll._thumb_hover = True
        thick_rect = scroll.thumb_rect()
        assert thick_rect is not None
        assert thick_rect.width > thin
        assert thick_rect.right == pytest.approx(rect.right), "变粗只向左长，不移动右缘"
