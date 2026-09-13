"""R7.2 手势竞技场的回归测试（docs/21）。

验收项：
- 滚动容器内嵌按钮，tap/scroll 竞争行为符合规格（8px 阈值）；
- 双击不错发两次单击；
- 长按计时用注入时间戳驱动，不睡真觉；
- 取消是一等公民：输给滚动的一方收到 cancel，ACTIVE 退回去。

在 R7.2 之前，Button 直接从裸 DOWN/UP 里判点击——滚动容器里放按钮必然
"两个都赢"：按钮变 ACTIVE，列表同时开始滚。这里把裁决权交给竞技场。
"""

from __future__ import annotations

import pytest

from inkstone.backend.base import PointerEvent, PointerKind
from inkstone.core import BuildOwner
from inkstone.events.gestures import (
    DragGestureRecognizer,
    GestureArena,
    GestureRecognizer,
    GestureState,
    HandleDragRecognizer,
    LongPressGestureRecognizer,
    TapGestureRecognizer,
)
from inkstone.events.pointer import HitTestResult, PointerRouter
from inkstone.layout import (
    BoxConstraints,
    Offset,
    RenderBox,
    RenderColumn,
    RenderScroll,
    RenderSized,
    ScrollDirection,
    Size,
    Sizing,
)
from inkstone.style import ComponentState, Theme
from inkstone.widgets import Button

CONSTRAINTS = BoxConstraints(max_width=200, max_height=100)


def _ev(kind: PointerKind, x: float, y: float, *, t: float = 0.0, button: int = 1) -> PointerEvent:
    return PointerEvent(kind=kind, x=x, y=y, button=button, time_ms=t)


class _PressNode(RenderBox):
    """带单击识别器的"按钮"节点——记录按下/命中/取消，供竞技场断言。"""

    def __init__(self, *, slop: float, width: float = 100.0, height: float = 30.0) -> None:
        super().__init__(debug_name="PressNode")
        self._width = width
        self._height = height
        self.pressed = False
        self.taps = 0
        self.cancels = 0
        self.recognizer = TapGestureRecognizer(
            slop=slop,
            on_tap=self._on_tap,
            on_tap_down=self._on_down,
            on_cancel=self._on_cancel,
        )

    def _on_down(self) -> None:
        self.pressed = True

    def _on_tap(self) -> None:
        self.taps += 1
        self.pressed = False

    def _on_cancel(self) -> None:
        self.cancels += 1
        self.pressed = False

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(self._width, self._height))

    def pointer_recognizers(self):
        return (self.recognizer,)


class TestTapVsScroll:
    """docs/06 §5 的核心场景：滚动列表里的按钮。"""

    @staticmethod
    def _rig(tap_slop: float) -> tuple[RenderScroll, _PressNode, PointerRouter]:
        theme = Theme.light()
        slop = theme.gesture("tap_slop")
        assert slop == tap_slop, "阈值必须来自令牌"

        node = _PressNode(slop=slop)
        column = RenderColumn(gap=0.0)
        column.add(node)
        column.add(RenderSized(height=Sizing.fixed(120.0)))
        scroll = RenderScroll(column, direction=ScrollDirection.VERTICAL)
        scroll.recognizers.append(
            DragGestureRecognizer(slop=slop, on_update=lambda dx, dy: scroll.scroll_by(dx, dy))
        )
        scroll.layout(BoxConstraints(max_width=100.0, max_height=50.0))
        return scroll, node, PointerRouter()

    @staticmethod
    def _send(router: PointerRouter, scroll: RenderScroll, event: PointerEvent) -> None:
        result = HitTestResult()
        scroll.hit_test(Offset(event.x, event.y), result)
        router.dispatch(result, event)

    def test_tap_within_slop_wins_and_does_not_scroll(self) -> None:
        scroll, node, router = self._rig(8.0)
        self._send(router, scroll, _ev(PointerKind.DOWN, 10.0, 10.0))
        self._send(router, scroll, _ev(PointerKind.UP, 10.0, 10.0))

        assert node.taps == 1, "8px 内的按下-抬起应当判为 tap"
        assert node.cancels == 0
        assert scroll.scroll_offset.dy == pytest.approx(0.0), "点击不该让列表滚动"

    def test_drag_beyond_slop_scrolls_and_cancels_the_button(self) -> None:
        scroll, node, router = self._rig(8.0)
        self._send(router, scroll, _ev(PointerKind.DOWN, 10.0, 10.0))
        self._send(router, scroll, _ev(PointerKind.MOVE, 10.0, 30.0))  # 移动 20px > 8px
        self._send(router, scroll, _ev(PointerKind.UP, 10.0, 30.0))

        assert node.taps == 0, "拖动超过 slop 不该再判为点击"
        assert node.cancels == 1, "输给滚动的一方必须收到取消"
        assert not node.pressed, "取消后 ACTIVE 必须退回去"
        assert scroll.scroll_offset.dy == pytest.approx(20.0), "滚动容器应当获胜并滚动"

    def test_exactly_at_slop_is_still_a_tap(self) -> None:
        """阈值边界：恰好 8px 仍算 tap（判据是"超过"而非"达到"）。"""
        scroll, node, router = self._rig(8.0)
        self._send(router, scroll, _ev(PointerKind.DOWN, 10.0, 10.0))
        self._send(router, scroll, _ev(PointerKind.MOVE, 18.0, 10.0))
        self._send(router, scroll, _ev(PointerKind.UP, 18.0, 10.0))
        assert node.taps == 1
        assert node.cancels == 0


class _LongNode(RenderBox):
    """只注册长按识别器的节点——用来单独验证"排帧推进超时"这条契约。"""

    def __init__(self, *, slop: float, duration_ms: float) -> None:
        super().__init__(debug_name="LongNode")
        self.fired = 0
        self.recognizer = LongPressGestureRecognizer(
            slop=slop,
            duration_ms=duration_ms,
            on_long_press=self._fire,
        )

    def _fire(self) -> None:
        self.fired += 1

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(50.0, 50.0))

    def pointer_recognizers(self):
        return (self.recognizer,)


class TestLongPressDeadlineSchedulesFrame:
    def test_pending_deadline_requests_a_frame_and_tick_advances_it(self) -> None:
        node = _LongNode(slop=8.0, duration_ms=500.0)
        node.layout(BoxConstraints(max_width=100.0, max_height=100.0))
        router = PointerRouter()
        calls: list[int] = []
        router.request_timeout_check = lambda: calls.append(1)

        result = HitTestResult()
        node.hit_test(Offset(5.0, 5.0), result)
        router.dispatch(result, _ev(PointerKind.DOWN, 5.0, 5.0, t=0.0))

        assert calls == [1], "长按计时需要框架排一帧来推进超时"
        assert router.tick(499.0) is True, "未到点：仍需继续排帧"
        assert node.fired == 0
        assert router.tick(500.0) is False
        assert node.fired == 1


class TestDoubleTap:
    @staticmethod
    def _owner(taps: list[int], doubles: list[int]) -> BuildOwner:
        owner = BuildOwner(theme=Theme.light())
        owner.mount(
            Button(
                "确定",
                on_tap=lambda: taps.append(1),
                on_double_tap=lambda: doubles.append(1),
            )
        )
        owner.begin_frame(CONSTRAINTS)
        return owner

    @staticmethod
    def _tap(owner: BuildOwner, *, down_t: float, up_t: float) -> None:
        owner.dispatch_pointer(_ev(PointerKind.DOWN, 5.0, 5.0, t=down_t))
        owner.dispatch_pointer(_ev(PointerKind.UP, 5.0, 5.0, t=up_t))

    def test_double_tap_fires_once_not_two_singles(self) -> None:
        taps: list[int] = []
        doubles: list[int] = []
        owner = self._owner(taps, doubles)

        self._tap(owner, down_t=0.0, up_t=50.0)
        self._tap(owner, down_t=100.0, up_t=150.0)

        assert doubles == [1], "两次快速点击必须判为双击"
        assert taps == [], "双击不许顺带发出单击"

    def test_single_tap_fires_after_the_double_tap_window(self) -> None:
        taps: list[int] = []
        doubles: list[int] = []
        owner = self._owner(taps, doubles)

        self._tap(owner, down_t=0.0, up_t=10.0)
        assert taps == [], "时窗内不能抢着判单击"
        assert doubles == []

        owner.begin_frame(CONSTRAINTS, now_ms=400.0)  # > double_tap_ms(300)
        assert taps == [1], "时窗过后应当补发单击"
        assert doubles == []


class TestLongPress:
    def test_long_press_fires_on_injected_time_no_sleep(self) -> None:
        longs: list[int] = []
        owner = BuildOwner(theme=Theme.light())
        let = owner.mount(Button("确定", on_long_press=lambda: longs.append(1)))
        owner.begin_frame(CONSTRAINTS)

        owner.dispatch_pointer(_ev(PointerKind.DOWN, 5.0, 5.0, t=0.0))
        owner.begin_frame(CONSTRAINTS, now_ms=499.0)
        assert longs == [], "未到时长不该触发"

        owner.begin_frame(CONSTRAINTS, now_ms=500.0)
        assert longs == [1], "到点必须触发（时间由注入的 time_ms 推进）"
        assert let.state.component_state is not ComponentState.ACTIVE, "长按后按压态要退掉"

    def test_quick_release_does_not_long_press(self) -> None:
        longs: list[int] = []
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Button("确定", on_long_press=lambda: longs.append(1)))
        owner.begin_frame(CONSTRAINTS)

        owner.dispatch_pointer(_ev(PointerKind.DOWN, 5.0, 5.0, t=0.0))
        owner.dispatch_pointer(_ev(PointerKind.UP, 5.0, 5.0, t=50.0))
        owner.begin_frame(CONSTRAINTS, now_ms=1000.0)
        assert longs == []


class TestThresholdsComeFromTokens:
    def test_gesture_tokens_exist(self) -> None:
        theme = Theme.light()
        assert theme.gesture("tap_slop") == pytest.approx(8.0)
        assert theme.gesture("long_press_ms") == pytest.approx(500.0)
        assert theme.gesture("double_tap_ms") == pytest.approx(300.0)

    def test_button_recognizer_uses_the_token_value(self) -> None:
        owner = BuildOwner(theme=Theme.light())
        element = owner.mount(Button("确定"))
        owner.begin_frame(CONSTRAINTS)
        recognizer = element.state._tap
        assert recognizer is not None
        assert recognizer.slop == pytest.approx(Theme.light().gesture("tap_slop"))


class TestArenaUnit:
    def test_reject_auto_accepts_the_last_remaining(self) -> None:
        arena = GestureArena(0)
        a = TapGestureRecognizer(slop=8.0)
        b = TapGestureRecognizer(slop=8.0)
        arena.add(a)
        arena.add(b)
        arena.close()

        arena.reject(b)
        assert a.state is GestureState.ACCEPTED
        assert b.state is GestureState.REJECTED

    def test_hold_defers_sweep(self) -> None:
        arena = GestureArena(0)
        holder = TapGestureRecognizer(slop=8.0)
        other = TapGestureRecognizer(slop=8.0)
        arena.add(holder)
        arena.add(other)
        arena.close()
        arena.hold(holder)

        arena.sweep()
        assert not arena.resolved, "有 hold 时 sweep 不该收敛"

        arena.release_hold(holder)
        assert arena.resolved and holder.state is GestureState.ACCEPTED


class TestHandleDragRecognizer:
    """把手拖拽（R13.2）：只在按下点命中把手时参与竞争，且**不破坏**既有仲裁。

    关键回归：按下点不在把手上时，识别器必须"握着不放"到抬起再退出。
    否则竞技场"只剩一个未 hold 的成员就判胜"的规则会让滚动拖拽在 DOWN
    当场获胜，绕过它的 slop 门槛——轻微抖动就滚动（ADR-0014 修掉的 bug）。
    """

    def _rig(self) -> tuple[HandleDragRecognizer, list[tuple[float, float]], list[str]]:
        drags: list[tuple[float, float]] = []
        events: list[str] = []
        rec = HandleDragRecognizer(
            to_local=lambda x, y: (x, y),
            hit_test=lambda x, y: x > 50.0,  # 右半边是"把手"
            on_start=lambda x, y: events.append("start"),
            on_drag=lambda x, y: drags.append((x, y)),
            on_end=lambda: events.append("end"),
        )
        return rec, drags, events

    def _press(self, router: PointerRouter, x: float, y: float, node: RenderBox) -> None:
        result = HitTestResult()
        node.hit_test(Offset(x, y), result)
        router.dispatch(
            result, PointerEvent(kind=PointerKind.DOWN, x=x, y=y, window_id=0, time_ms=0.0)
        )

    def _node(self, rec: HandleDragRecognizer) -> RenderBox:
        return _grip_node(rec)

    def test_dragging_the_handle_tracks_the_pointer_absolutely(self) -> None:
        rec, drags, events = self._rig()
        node = self._node(rec)
        router = PointerRouter()
        self._press(router, 80.0, 10.0, node)
        assert rec.state is GestureState.ACCEPTED, "抓住把手应当场获胜"
        router.dispatch(
            node_hit(node, 80.0, 40.0),
            PointerEvent(kind=PointerKind.MOVE, x=80.0, y=40.0, window_id=0, time_ms=1.0),
        )
        router.dispatch(
            node_hit(node, 80.0, 60.0),
            PointerEvent(kind=PointerKind.UP, x=80.0, y=60.0, window_id=0, time_ms=2.0),
        )
        assert drags == [(80.0, 40.0), (80.0, 60.0)], "拖动要按绝对位置跟随指针"
        assert events == ["start", "end"]

    def test_press_outside_the_handle_does_not_steal_the_gesture(self) -> None:
        """按下点不在把手上 → 退出，交给竞争的另一方。"""
        rec, drags, events = self._rig()
        other = _CountingRecognizer()
        node = _grip_node(other, rec)

        router = PointerRouter()
        self._press(router, 10.0, 10.0, node)
        # 另一个识别器持有（长按/双击会 hold），这里让它 explicitly 获胜
        other.resolve_accept()
        assert rec.state is GestureState.REJECTED
        assert drags == [] and events == []

    def test_bailing_out_does_not_auto_accept_a_lone_peer_on_down(self) -> None:
        """**回归的核心**：把手不参与时，同级的拖拽不许在 DOWN 当场获胜。

        直接 reject 会触发竞技场的自动判胜，让滚动拖拽跳过 slop 门槛。
        """
        rec, _, _ = self._rig()
        peer = _SlopProbe(slop=8.0)
        node = _grip_node(peer, rec)

        router = PointerRouter()
        self._press(router, 10.0, 10.0, node)  # 没命中把手
        assert peer.state is GestureState.PENDING, "同级拖拽不该在 DOWN 就被判胜"
        # 微动（未过 slop）仍然不许开始
        router.dispatch(
            node_hit(node, 12.0, 10.0),
            PointerEvent(kind=PointerKind.MOVE, x=12.0, y=10.0, window_id=0, time_ms=1.0),
        )
        assert peer.started is False, "8px 以内不得开始拖拽"
        # 超过 slop 才赢
        router.dispatch(
            node_hit(node, 30.0, 10.0),
            PointerEvent(kind=PointerKind.MOVE, x=30.0, y=10.0, window_id=0, time_ms=2.0),
        )
        assert peer.state is GestureState.ACCEPTED and peer.started is True


class _GripNode(RenderBox):
    """固定 100×100 的可命中节点，用来挂识别器。"""

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(100.0, 100.0))


def _grip_node(*recognizers: GestureRecognizer) -> _GripNode:
    node = _GripNode(debug_name="Grip")
    node.layout(BoxConstraints(max_width=100.0, max_height=100.0))
    node.recognizers = list(recognizers)
    return node


class _CountingRecognizer(GestureRecognizer):
    pass


class _SlopProbe(GestureRecognizer):
    """记录"是否真的开始拖拽"，用来验证 slop 门槛没有被绕过。"""

    def __init__(self, *, slop: float) -> None:
        super().__init__(slop=slop)
        self.started = False

    def on_pointer_down(self, event: PointerEvent) -> None:
        self._down = (event.x, event.y)

    def on_pointer_move(self, event: PointerEvent) -> None:
        if self.started or self._down is None:
            return
        if not self._moved_beyond_slop(event):
            return
        self.resolve_accept()
        self.started = True


def node_hit(node: RenderBox, x: float, y: float) -> HitTestResult:
    result = HitTestResult()
    node.hit_test(Offset(x, y), result)
    return result
