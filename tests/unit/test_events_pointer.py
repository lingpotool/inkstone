"""R7.1 命中测试与指针事件路由的回归测试（docs/21）。

这一包把"事件到手之后没有下一步"闭合起来：
backend 事件 → 命中链 → 三阶段分发 → 组件状态/回调。

每个测试都对应 docs/21 R7.1 验收里的一条，且都是"修复前必红"的形态：
在 R7.1 之前 `events/pointer.py` 是空壳，`RenderBox` 没有 `hit_test`，
`Button` 的 `set_component_state` 没有任何调用方。
"""

from __future__ import annotations

import pytest

from inkstone.backend import HeadlessBackend, ImeRect, WindowSpec
from inkstone.backend.base import PointerEvent, PointerKind
from inkstone.core import (
    BuildOwner,
    FrameError,
    LeafRenderObjectElement,
    RenderObjectWidget,
)
from inkstone.events.pointer import (
    DispatchPhase,
    HitTestResult,
    PointerDispatch,
    PointerRouter,
)
from inkstone.layout import (
    BoxConstraints,
    EdgeInsets,
    Offset,
    RenderBox,
    RenderColumn,
    RenderContainer,
    RenderRow,
    RenderScroll,
    RenderStack,
    ScrollDirection,
    Size,
    Sizing,
)
from inkstone.style import ComponentState, Theme
from inkstone.text import TextEngine
from inkstone.widgets import Button, Input

CONSTRAINTS = BoxConstraints(max_width=320, max_height=200)


# ---------------------------------------------------------------- 测试替身


class _Probe(RenderBox):
    """会记录收到的事件的布局节点。"""

    def __init__(self, width: float = 50.0, height: float = 50.0, name: str = "Probe") -> None:
        super().__init__(debug_name=name)
        self._width = width
        self._height = height
        self.events: list[tuple[PointerKind, DispatchPhase, float, float]] = []
        self.stop_phase: DispatchPhase | None = None

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(self._width, self._height))

    def handle_pointer_event(self, dispatch: PointerDispatch) -> None:
        self.events.append(
            (dispatch.event.kind, dispatch.phase, dispatch.local_x, dispatch.local_y)
        )
        if self.stop_phase is dispatch.phase:
            dispatch.stop_propagation()

    def phases(self, kind: PointerKind) -> list[DispatchPhase]:
        """只看某一类事件的分发阶段——hover 合成的 ENTER/LEAVE 也会落到这里，
        断言阶段顺序时要把它们滤掉。"""
        return [phase for event_kind, phase, _, _ in self.events if event_kind is kind]


class _ProbeContainer(RenderContainer):
    """既能装子级、又记录事件的父级。"""

    def __init__(self, child: RenderBox, *, padding: EdgeInsets | None = None) -> None:
        super().__init__(
            child,
            width=Sizing.fixed(100.0),
            height=Sizing.fixed(100.0),
            padding=padding,
            debug_name="ProbeContainer",
        )
        self.events: list[tuple[PointerKind, DispatchPhase]] = []
        self.stop_phase: DispatchPhase | None = None

    def handle_pointer_event(self, dispatch: PointerDispatch) -> None:
        self.events.append((dispatch.event.kind, dispatch.phase))
        if self.stop_phase is dispatch.phase:
            dispatch.stop_propagation()

    def phases(self, kind: PointerKind) -> list[DispatchPhase]:
        return [phase for event_kind, phase in self.events if event_kind is kind]


class _HookBox(RenderBox):
    """在布局/绘制阶段执行回调的盒子——用来验证阶段守卫没有开洞。"""

    def __init__(self, on_layout=None, on_paint=None) -> None:
        super().__init__(debug_name="HookBox")
        self.on_layout = on_layout
        self.on_paint = on_paint

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        if self.on_layout is not None:
            self.on_layout()
        return constraints.constrain(Size(10.0, 10.0))

    def paint(self, context: object) -> None:
        if self.on_paint is not None:
            self.on_paint()


class _HookWidget(RenderObjectWidget):
    def __init__(self, box: _HookBox) -> None:
        self.box = box

    def create_render_object(self) -> _HookBox:
        return self.box

    def update_render_object(self, render_object: RenderBox) -> None:
        pass

    def create_element(self) -> LeafRenderObjectElement:
        return LeafRenderObjectElement(self)


def _hit(root: RenderBox, x: float, y: float) -> HitTestResult:
    result = HitTestResult()
    root.hit_test(Offset(x, y), result)
    return result


def _event(kind: PointerKind, x: float, y: float, button: int = 1) -> PointerEvent:
    return PointerEvent(kind=kind, x=x, y=y, button=button)


# ---------------------------------------------------------------- 命中测试


class TestHitTest:
    def test_overlapping_child_painted_later_wins(self) -> None:
        """叠层里后画的在上层——命中测试必须逆序找子级。"""
        stack = RenderStack()
        bottom = _Probe(name="Bottom")
        top = _Probe(name="Top")
        stack.add(bottom)
        stack.add(top)
        stack.layout(BoxConstraints(max_width=200, max_height=200))

        result = _hit(stack, 10.0, 10.0)

        assert result.target is top, "命中的应当是后画的 top"
        assert bottom not in result.targets(), "下层兄弟不该同时接到事件"
        assert result.targets() == (top, stack), "命中链应把祖先也带上"

    def test_point_outside_returns_empty_chain(self) -> None:
        probe = _Probe(width=40.0, height=40.0)
        probe.layout(BoxConstraints(max_width=200, max_height=200))
        assert not _hit(probe, 100.0, 100.0)

    def test_scroll_clips_hits_to_viewport(self) -> None:
        """滚出视口的子级点不到；视口内的位置映射到正确的子级。

        修复前没有命中测试；一个"忽略滚动偏移 / 不查视口边界"的实现
        会把点在上半区的事件送进已经滚出去的第一个条目。
        """
        column = RenderColumn(gap=0.0)
        first = _Probe(width=50.0, height=50.0, name="First")
        second = _Probe(width=50.0, height=50.0, name="Second")
        column.add(first)
        column.add(second)

        scroll = RenderScroll(column, direction=ScrollDirection.VERTICAL)
        scroll.layout(BoxConstraints(max_width=50.0, max_height=50.0))
        scroll.scroll_to(dy=50.0)
        scroll.layout(BoxConstraints(max_width=50.0, max_height=50.0))

        inside = _hit(scroll, 10.0, 10.0)
        assert inside.target is second, "视口内 y=10 应当是滚上来的第二个条目"
        assert first not in inside.targets(), "滚出视口的第一个条目不该被命中"
        # 局部坐标是相对该节点自己的，与滚动偏移无关
        assert second.events == []
        assert inside.entries[0].local_y == pytest.approx(10.0)

        assert not _hit(scroll, 10.0, 90.0), "视口下方的点子级再长也不该命中"

    def test_local_coordinates_are_relative_to_each_node(self) -> None:
        """每层拿到的坐标都扣掉了祖先偏移——组件不必自己累加。"""
        parent = _ProbeContainer(_Probe(width=20.0, height=20.0), padding=EdgeInsets.all(5.0))
        parent.layout(BoxConstraints(max_width=200, max_height=200))

        result = _hit(parent, 15.0, 15.0)

        child, container = result.entries
        assert (child.local_x, child.local_y) == (pytest.approx(10.0), pytest.approx(10.0))
        assert (container.local_x, container.local_y) == (pytest.approx(15.0), pytest.approx(15.0))


# ---------------------------------------------------------------- 三阶段分发


class TestThreePhaseRouting:
    def test_phase_order_is_capture_target_bubble(self) -> None:
        child = _Probe(width=50.0, height=50.0)
        parent = _ProbeContainer(child)
        parent.layout(BoxConstraints(max_width=200, max_height=200))

        router = PointerRouter()
        result = _hit(parent, 10.0, 10.0)
        assert router.dispatch(result, _event(PointerKind.DOWN, 10.0, 10.0))

        down = PointerKind.DOWN
        assert parent.phases(down) == [DispatchPhase.CAPTURE, DispatchPhase.BUBBLE]
        assert child.phases(down) == [DispatchPhase.TARGET]

    def test_stop_propagation_in_target_cuts_bubble(self) -> None:
        child = _Probe(width=50.0, height=50.0)
        child.stop_phase = DispatchPhase.TARGET
        parent = _ProbeContainer(child)
        parent.layout(BoxConstraints(max_width=200, max_height=200))

        router = PointerRouter()
        router.dispatch(_hit(parent, 10.0, 10.0), _event(PointerKind.DOWN, 10.0, 10.0))

        down = PointerKind.DOWN
        assert child.phases(down) == [DispatchPhase.TARGET]
        assert parent.phases(down) == [DispatchPhase.CAPTURE], "冒泡应在目标处被截断"

    def test_stop_propagation_in_capture_skips_everything_downstream(self) -> None:
        child = _Probe(width=50.0, height=50.0)
        parent = _ProbeContainer(child)
        parent.stop_phase = DispatchPhase.CAPTURE
        parent.layout(BoxConstraints(max_width=200, max_height=200))

        router = PointerRouter()
        router.dispatch(_hit(parent, 10.0, 10.0), _event(PointerKind.DOWN, 10.0, 10.0))

        down = PointerKind.DOWN
        assert parent.phases(down) == [DispatchPhase.CAPTURE]
        assert child.phases(down) == [], "捕获阶段叫停后，目标与冒泡都不该发生"

    def test_empty_chain_is_not_handled(self) -> None:
        probe = _Probe(width=10.0, height=10.0)
        probe.layout(BoxConstraints(max_width=200, max_height=200))
        router = PointerRouter()
        assert not router.dispatch(_hit(probe, 99.0, 99.0), _event(PointerKind.DOWN, 99.0, 99.0))


# ---------------------------------------------------------------- hover 差分


class TestHoverDiff:
    def test_enter_and_leave_are_generated_from_move_diff(self) -> None:
        row = RenderRow(gap=0.0)
        left = _Probe(width=50.0, height=50.0, name="Left")
        right = _Probe(width=50.0, height=50.0, name="Right")
        row.add(left)
        row.add(right)
        row.layout(BoxConstraints(max_width=100.0, max_height=50.0))

        router = PointerRouter()

        def move(x: float) -> None:
            router.dispatch(_hit(row, x, 25.0), _event(PointerKind.MOVE, x, 25.0))

        move(25.0)  # 进入 left
        assert PointerKind.ENTER in [kind for kind, *_ in left.events]

        move(75.0)  # 快速划到 right：不经过中间事件，left 的 LEAVE 不能丢
        left_kinds = [kind for kind, *_ in left.events]
        right_kinds = [kind for kind, *_ in right.events]
        assert PointerKind.LEAVE in left_kinds, "快速划过时 LEAVE 被弄丢了"
        assert PointerKind.ENTER in right_kinds

        move(200.0)  # 划出所有控件
        assert [kind for kind, *_ in right.events].count(PointerKind.LEAVE) == 1

    def test_ancestors_on_the_chain_also_get_hover(self) -> None:
        row = RenderRow(gap=0.0)
        child = _Probe(width=50.0, height=50.0)
        row.add(child)
        row.layout(BoxConstraints(max_width=100.0, max_height=50.0))

        router = PointerRouter()
        router.dispatch(_hit(row, 10.0, 10.0), _event(PointerKind.MOVE, 10.0, 10.0))

        assert PointerKind.ENTER in [kind for kind, _, _, _ in child.events]
        assert row in router.hover_chain


# ---------------------------------------------------------------- 组件接线


class TestButtonWiring:
    @staticmethod
    def _owner(**kwargs) -> BuildOwner:
        return BuildOwner(theme=Theme.light(), **kwargs)

    def test_tap_fires_callback_exactly_once(self) -> None:
        fired: list[int] = []
        owner = self._owner()
        element = owner.mount(Button("确定", on_tap=lambda: fired.append(1)))
        owner.begin_frame(CONSTRAINTS)

        owner.dispatch_pointer(_event(PointerKind.DOWN, 5.0, 5.0))
        assert element.state.component_state is ComponentState.ACTIVE
        owner.dispatch_pointer(_event(PointerKind.UP, 5.0, 5.0))

        assert fired == [1], "一次点击必须恰好触发一次回调"

    def test_up_without_down_does_not_fire(self) -> None:
        fired: list[int] = []
        owner = self._owner()
        owner.mount(Button("确定", on_tap=lambda: fired.append(1)))
        owner.begin_frame(CONSTRAINTS)
        owner.dispatch_pointer(_event(PointerKind.UP, 5.0, 5.0))
        assert fired == []

    def test_disabled_button_ignores_pointer(self) -> None:
        fired: list[int] = []
        owner = self._owner()
        element = owner.mount(Button("确定", disabled=True, on_tap=lambda: fired.append(1)))
        owner.begin_frame(CONSTRAINTS)

        owner.dispatch_pointer(_event(PointerKind.DOWN, 5.0, 5.0))
        owner.dispatch_pointer(_event(PointerKind.UP, 5.0, 5.0))

        assert fired == []
        assert element.state.component_state is ComponentState.DISABLED

    def test_hover_enter_and_leave_change_state(self) -> None:
        owner = self._owner()
        element = owner.mount(Button("确定"))
        owner.begin_frame(CONSTRAINTS)

        owner.dispatch_pointer(_event(PointerKind.MOVE, 5.0, 5.0))
        assert element.state.component_state is ComponentState.HOVER

        owner.dispatch_pointer(_event(PointerKind.MOVE, 500.0, 500.0))
        assert element.state.component_state is ComponentState.DEFAULT

    def test_pointer_leaving_cancels_press(self) -> None:
        """按下后移出再抬起不算点击（取消是一等公民，R7.2 会由竞技场接管）。"""
        fired: list[int] = []
        owner = self._owner()
        element = owner.mount(Button("确定", on_tap=lambda: fired.append(1)))
        owner.begin_frame(CONSTRAINTS)

        owner.dispatch_pointer(_event(PointerKind.DOWN, 5.0, 5.0))
        owner.dispatch_pointer(_event(PointerKind.MOVE, 500.0, 500.0))
        owner.dispatch_pointer(_event(PointerKind.UP, 500.0, 500.0))

        assert fired == []
        # 离开时按压被取消。焦点虽然还在按钮上，但**鼠标来源不算 focus-visible**
        # （R9.1）：按钮不该因为被点过就永久挂一圈焦点环。
        assert element.state.component_state is ComponentState.DEFAULT


class TestInputFocusReportsIme:
    """输入框获焦后要真的打开 IME 通道并上报候选框位置（R5.7 协议 + R7.1 接线）。"""

    def _owner(self) -> tuple[BuildOwner, HeadlessBackend, int]:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())
        owner = BuildOwner(theme=Theme.light(), text_engine=TextEngine(backend))
        owner.on_text_input = lambda active: (
            backend.start_text_input(window) if active else backend.stop_text_input(window)
        )
        owner.on_ime_rect = lambda rect: backend.set_ime_rect(
            window, ImeRect(rect.left, rect.top, rect.width, rect.height)
        )
        return owner, backend, window

    def test_focus_turns_on_text_input_and_reports_caret(self) -> None:
        owner, backend, window = self._owner()
        owner.mount(Input(placeholder="邮箱"))
        owner.begin_frame(CONSTRAINTS)
        assert not backend.text_input_active(window)

        owner.dispatch_pointer(_event(PointerKind.DOWN, 5.0, 5.0))
        owner.begin_frame(CONSTRAINTS)  # 聚焦触发重建，元素在这一帧上报

        assert backend.text_input_active(window), "获焦必须打开 IME 通道"
        rect = backend.ime_rect(window)
        assert rect is not None, "候选框位置必须上报"
        assert rect.width == 0.0, "还没有编辑模型，光标是零宽矩形"
        assert rect.height > 0.0

    def test_unfocus_stops_text_input(self) -> None:
        owner, backend, window = self._owner()
        element = owner.mount(Input(placeholder="邮箱"))
        owner.begin_frame(CONSTRAINTS)
        owner.dispatch_pointer(_event(PointerKind.DOWN, 5.0, 5.0))
        owner.begin_frame(CONSTRAINTS)
        assert backend.text_input_active(window)

        element.state.unfocus()
        owner.begin_frame(CONSTRAINTS)
        assert not backend.text_input_active(window)


# ---------------------------------------------------------------- 阶段守卫


class TestPhaseGuardNotBypassed:
    """回调里改状态在 layout / paint 阶段必须响亮失败（沿用 R1 的阶段守卫）。"""

    @staticmethod
    def _down() -> PointerEvent:
        return _event(PointerKind.DOWN, 1.0, 1.0)

    def test_dispatch_during_layout_raises(self) -> None:
        owner = BuildOwner(theme=Theme.light())
        box = _HookBox(on_layout=lambda: owner.dispatch_pointer(self._down()))
        owner.mount(_HookWidget(box))

        with pytest.raises(FrameError, match="派发指针事件"):
            owner.flush_layout(CONSTRAINTS)

    def test_dispatch_during_paint_raises(self) -> None:
        owner = BuildOwner(theme=Theme.light())
        box = _HookBox(on_paint=lambda: owner.dispatch_pointer(self._down()))
        owner.mount(_HookWidget(box))
        owner.flush_layout(CONSTRAINTS)

        with pytest.raises(FrameError, match="派发指针事件"):
            owner.flush_paint(object())

    def test_handler_state_change_outside_frame_phase_is_allowed(self) -> None:
        """对照组：正常情况下（IDLE 阶段）点击回调改状态是合法的。"""
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Button("确定"))
        owner.begin_frame(CONSTRAINTS)
        owner.dispatch_pointer(_event(PointerKind.DOWN, 5.0, 5.0))
        assert owner.dirty_count > 0
