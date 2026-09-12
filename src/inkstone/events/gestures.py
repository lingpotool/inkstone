"""手势竞技场 —— tap / double-tap / long-press / drag 不是独立判断，是**竞争**。

docs/06 §5 的那条规格是这一切存在的理由：

    列表里的按钮：按下后 8px 内抬起是 tap，超过则列表滚动获胜，按钮收到取消。

没有竞技场，每个控件各自看指针事件就会写出玄学 bug：按钮先收到 DOWN 就
自己变 ACTIVE，滚动容器也同时开始滚——两者都"赢"了。竞技场把这变成一次
**显式裁决**：同一指针下的所有识别器同台竞争，赢家执行手势，输家收到
`on_reject`（一等公民的取消），组件据此把 ACTIVE 退回去。

层次：本模块是 L1，不 import 几何/主题。阈值（8px、500ms）由组件从
`style/tokens.py` 读出后经构造参数传进来；时间来自**事件自带的 `time_ms`**
（R5.2），超时靠 `check_timeout(now)` 推进，绝不读墙上时钟。

状态：已实现（R7.2）。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from ..backend.base import PointerEvent, PointerKind

__all__ = [
    "DoubleTapGestureRecognizer",
    "DragGestureRecognizer",
    "GestureArena",
    "GestureRecognizer",
    "GestureState",
    "LongPressGestureRecognizer",
    "TapGestureRecognizer",
]


class GestureState(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class GestureArena:
    """一个指针（一次按下-抬起）的裁决场。

    规则（刻意保持最小、可预测）：

    - 识别器主动 `accept` 即获胜，其余全部收到 reject；
    - 识别器 `reject` 自己后，若只剩一个**未 hold** 的成员，它自动获胜；
    - `hold` 表示"我可能还要等第二击/超时，先别急着判"；此时 sweep 不收敛，
      直到 hold 释放；
    - 抬起时 `sweep`：没有 hold 就判第一个未决成员获胜——这是"只注册了
      Tap 的普通点击"能出结果的路径。
    """

    def __init__(self, pointer_id: int) -> None:
        self.pointer_id = pointer_id
        self.members: list[GestureRecognizer] = []
        self._holders: set[GestureRecognizer] = set()
        self._resolved = False
        self._closed = False
        self._sweep_requested = False

    @property
    def resolved(self) -> bool:
        return self._resolved

    def add(self, member: GestureRecognizer) -> None:
        member.set_arena(self)
        self.members.append(member)

    def close(self) -> None:
        self._closed = True

    def accept(self, winner: GestureRecognizer) -> None:
        if self._resolved:
            return
        self._resolved = True
        for member in self.members:
            if member is winner:
                member._state = GestureState.ACCEPTED
                member.on_accept()
            elif member.state is not GestureState.REJECTED:
                member._state = GestureState.REJECTED
                member.on_reject()

    def reject(self, member: GestureRecognizer) -> None:
        if self._resolved or member.state is GestureState.REJECTED:
            return
        member._state = GestureState.REJECTED
        member.on_reject()
        self._maybe_auto_accept()

    def hold(self, member: GestureRecognizer) -> None:
        self._holders.add(member)

    def release_hold(self, member: GestureRecognizer) -> None:
        self._holders.discard(member)
        self._maybe_sweep()

    def has_other_holder(self, member: GestureRecognizer) -> bool:
        return any(other is not member for other in self._holders)

    def sweep(self) -> None:
        """指针抬起：强制收敛。

        有 hold 时不收敛，记为"待 sweep"；等 hold 释放或超时自决后再走。
        """
        self._sweep_requested = True
        self._maybe_sweep()

    # ------------------------------------------------------------ 内部

    def _pending(self) -> list[GestureRecognizer]:
        return [m for m in self.members if m.state is GestureState.PENDING]

    def _maybe_auto_accept(self) -> None:
        if self._resolved or self._holders:
            return
        pending = self._pending()
        if len(pending) == 1:
            self.accept(pending[0])

    def _maybe_sweep(self) -> None:
        if self._resolved or not self._sweep_requested or self._holders:
            return
        pending = self._pending()
        if pending:
            self.accept(pending[0])
        else:
            self._resolved = True


class GestureRecognizer:
    """识别器基类：在竞技场里声明胜负，回调组件。

    生命周期：`DOWN` 时被 Router 收集加入竞技场并调用 `on_pointer_down`；
    随后的 MOVE / UP 逐条送入；胜负由自己或竞技场裁决，落到
    `on_accept` / `on_reject`。
    """

    def __init__(self, *, slop: float = 0.0) -> None:
        self.slop = slop
        self._arena: GestureArena | None = None
        self._state = GestureState.PENDING
        #: 本次按下的位置，子类共用（超差判定在基类里做）
        self._down: tuple[float, float] | None = None

    @property
    def state(self) -> GestureState:
        return self._state

    @property
    def arena(self) -> GestureArena | None:
        return self._arena

    def set_arena(self, arena: GestureArena) -> None:
        self._arena = arena

    # ------------------------------------------------------------ 胜负声明

    def resolve_accept(self) -> None:
        if self._arena is not None:
            self._arena.accept(self)

    def resolve_reject(self) -> None:
        if self._arena is not None:
            self._arena.reject(self)

    def hold(self) -> None:
        """声明"我还要等第二击/超时"，阻止竞技场提前判定。"""
        if self._arena is not None:
            self._arena.hold(self)

    def release_hold(self) -> None:
        if self._arena is not None:
            self._arena.release_hold(self)

    # ------------------------------------------------------------ 事件入口

    def handle_event(self, event: PointerEvent) -> None:
        if self._state is GestureState.REJECTED:
            return
        kind = event.kind
        if kind is PointerKind.DOWN:
            self.on_pointer_down(event)
        elif kind is PointerKind.MOVE:
            self.on_pointer_move(event)
        elif kind is PointerKind.UP:
            self.on_pointer_up(event)

    def on_pointer_down(self, event: PointerEvent) -> None: ...

    def on_pointer_move(self, event: PointerEvent) -> None: ...

    def on_pointer_up(self, event: PointerEvent) -> None: ...

    def on_accept(self) -> None: ...

    def on_reject(self) -> None: ...

    # ------------------------------------------------------------ 超时

    def check_timeout(self, now_ms: float) -> bool:
        """推进超时。返回 True 表示还有未决的计时（需要继续排帧）。"""
        return False

    def next_deadline_ms(self) -> float | None:
        """下一个需要被唤醒的时刻；没有计时返回 None。"""
        return None

    # ------------------------------------------------------------ 工具

    def _moved_beyond_slop(self, event: PointerEvent) -> bool:
        if self._down is None:
            return False
        dx = event.x - self._down[0]
        dy = event.y - self._down[1]
        return dx * dx + dy * dy > self.slop * self.slop


def _call(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


class TapGestureRecognizer(GestureRecognizer):
    """单击。移动超过 slop 即让位（拒绝自己），滚动/拖拽才能获胜。"""

    def __init__(
        self,
        *,
        slop: float,
        on_tap: Callable[[], None] | None = None,
        on_tap_down: Callable[[], None] | None = None,
        on_tap_up: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(slop=slop)
        self._on_tap = on_tap
        self._on_tap_down = on_tap_down
        self._on_tap_up = on_tap_up
        self._on_cancel = on_cancel
        self._down: tuple[float, float] | None = None

    def on_pointer_down(self, event: PointerEvent) -> None:
        self._down = (event.x, event.y)
        _call(self._on_tap_down)

    def on_pointer_move(self, event: PointerEvent) -> None:
        if self._moved_beyond_slop(event):
            self.resolve_reject()

    def on_pointer_up(self, event: PointerEvent) -> None:
        # 竞技场里有人 hold（如双击在等第二击）时不抢着赢，否则单击会先出。
        if self._arena is not None and self._arena.has_other_holder(self):
            self.hold()
            return
        self.resolve_accept()

    def on_accept(self) -> None:
        _call(self._on_tap_up)
        _call(self._on_tap)

    def on_reject(self) -> None:
        _call(self._on_cancel)


class DoubleTapGestureRecognizer(GestureRecognizer):
    """双击，顺带负责单击——两者必须在**同一个识别器**里裁决。

    为什么不能拆成 Tap + DoubleTap 两个识别器：第一击抬起时 Tap 无法知道
    第二击会不会来。若 Tap 立刻赢，双击就永远出不来；若 Tap 等待，它又
    无法在第二击到来时撤回已经发出的单击。把"延迟的单击"和"双击"放进
    一个状态机，`double_tap_ms` 就是唯一的时窗，逻辑才闭合。
    """

    def __init__(
        self,
        *,
        slop: float,
        double_tap_ms: float,
        on_tap: Callable[[], None] | None = None,
        on_double_tap: Callable[[], None] | None = None,
        on_tap_down: Callable[[], None] | None = None,
        on_tap_up: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(slop=slop)
        self.double_tap_ms = double_tap_ms
        self._on_tap = on_tap
        self._on_double_tap = on_double_tap
        self._on_tap_down = on_tap_down
        self._on_tap_up = on_tap_up
        self._on_cancel = on_cancel
        self._down: tuple[float, float] | None = None
        self._first_ms: float | None = None
        self._second = False
        self._suppress_accept = False
        self._pending_arena: GestureArena | None = None

    def on_pointer_down(self, event: PointerEvent) -> None:
        self._down = (event.x, event.y)
        if self._first_ms is None:
            self._first_ms = event.time_ms
            self._second = False
            if self._arena is not None:
                self._arena.hold(self)
                self._pending_arena = self._arena
        else:
            # 第二击开始：先把上一场的"待定单击"关上，且不触发单击。
            self._suppress_accept = True
            pending = self._pending_arena
            if pending is not None and not pending.resolved:
                pending.accept(self)
            self._pending_arena = None
            self._second = True
            if self._arena is not None:
                self._arena.hold(self)
        _call(self._on_tap_down)

    def on_pointer_move(self, event: PointerEvent) -> None:
        if self._moved_beyond_slop(event):
            self._reset()
            self.resolve_reject()

    def on_pointer_up(self, event: PointerEvent) -> None:
        _call(self._on_tap_up)
        if self._second:
            self.resolve_accept()
        # 否则等 double_tap_ms 超时：超时后由 check_timeout 判为单击。

    def on_accept(self) -> None:
        if self._suppress_accept:
            self._suppress_accept = False
            return
        if self._second:
            _call(self._on_double_tap)
        else:
            _call(self._on_tap)
        self._reset()

    def on_reject(self) -> None:
        self._reset()
        _call(self._on_cancel)

    def check_timeout(self, now_ms: float) -> bool:
        if self._first_ms is None:
            return False
        if self._second:
            return False
        if now_ms - self._first_ms >= self.double_tap_ms:
            # 时窗内没有第二击：判为单击。
            self.resolve_accept()
            return False
        return True

    def next_deadline_ms(self) -> float | None:
        if self._first_ms is None or self._second:
            return None
        return self._first_ms + self.double_tap_ms

    def _reset(self) -> None:
        self._first_ms = None
        self._second = False
        self._suppress_accept = False
        self._pending_arena = None
        self._down = None
        self.release_hold()


class LongPressGestureRecognizer(GestureRecognizer):
    """长按。超时由注入的时间轴推进（`check_timeout`），不读墙上时钟。"""

    def __init__(
        self,
        *,
        slop: float,
        duration_ms: float,
        on_long_press: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(slop=slop)
        self.duration_ms = duration_ms
        self._on_long_press = on_long_press
        self._on_cancel = on_cancel
        self._down: tuple[float, float] | None = None
        self._start_ms: float | None = None
        self._fired = False

    def on_pointer_down(self, event: PointerEvent) -> None:
        self._down = (event.x, event.y)
        self._start_ms = event.time_ms
        self._fired = False

    def on_pointer_move(self, event: PointerEvent) -> None:
        if self._moved_beyond_slop(event):
            self._reset()
            self.resolve_reject()

    def on_pointer_up(self, event: PointerEvent) -> None:
        # 没到时间就抬起：长按失败，让位给 tap。
        self._reset()
        self.resolve_reject()

    def on_accept(self) -> None:
        self._fired = True
        _call(self._on_long_press)
        self._start_ms = None
        self._down = None

    def on_reject(self) -> None:
        self._reset()
        _call(self._on_cancel)

    def check_timeout(self, now_ms: float) -> bool:
        if self._start_ms is None or self._fired:
            return False
        if now_ms - self._start_ms >= self.duration_ms:
            self.resolve_accept()
            return False
        return True

    def next_deadline_ms(self) -> float | None:
        if self._start_ms is None or self._fired:
            return None
        return self._start_ms + self.duration_ms

    def _reset(self) -> None:
        self._start_ms = None
        self._down = None


class DragGestureRecognizer(GestureRecognizer):
    """拖拽（滚动容器用它）。超过 slop 才赢——这就是"8px 内抬起算点击"的另一面。"""

    def __init__(
        self,
        *,
        slop: float,
        on_start: Callable[[], None] | None = None,
        on_update: Callable[[float, float], None] | None = None,
        on_end: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        horizontal: bool = False,
        vertical: bool = True,
    ) -> None:
        super().__init__(slop=slop)
        self._on_start = on_start
        self._on_update = on_update
        self._on_end = on_end
        self._on_cancel = on_cancel
        self.horizontal = horizontal
        self.vertical = vertical
        self._down: tuple[float, float] | None = None
        self._last: tuple[float, float] = (0.0, 0.0)
        self._dragging = False

    def on_pointer_down(self, event: PointerEvent) -> None:
        self._down = (event.x, event.y)
        self._dragging = False

    def on_pointer_move(self, event: PointerEvent) -> None:
        if not self._dragging:
            if self._down is None or not self._moved_beyond_slop(event):
                return
            self.resolve_accept()
        if self._dragging:
            self._emit_update(event)

    def on_accept(self) -> None:
        self._dragging = True
        self._last = self._down if self._down is not None else (0.0, 0.0)
        _call(self._on_start)

    def on_pointer_up(self, event: PointerEvent) -> None:
        if self._dragging:
            _call(self._on_end)
            self._dragging = False
        else:
            # 没超过 slop 就抬起：不是拖拽，让位给 tap。
            self.resolve_reject()

    def on_reject(self) -> None:
        if self._dragging:
            _call(self._on_cancel)
            self._dragging = False

    def _emit_update(self, event: PointerEvent) -> None:
        if self._on_update is None:
            self._last = (event.x, event.y)
            return
        dx = event.x - self._last[0]
        dy = event.y - self._last[1]
        self._last = (event.x, event.y)
        if not self.horizontal:
            dx = 0.0
        if not self.vertical:
            dy = 0.0
        self._on_update(dx, dy)
