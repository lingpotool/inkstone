"""指针事件路由 —— 从"事件到手"到"组件收到"的那条链路（R7.1，docs/21）。

R5 把事件**模型**补齐了（`backend/base.py` 的 `PointerEvent` 含 window_id、
真时间戳、滚轮精度），但从后端拿到事件之后没有任何下一步：界面"能画不能摸"。
这个模块补上后半条链路：

    backend 事件 → 命中链（layout 的 RenderBox.hit_test 填） → 三阶段分发 → 组件回调

**本层不碰几何。** `events` 是 L1，`layout` 是 L4——依赖只能向下，
所以命中链的**坐标**由上层（layout）填进来，本层只负责顺序与分发语义：

- 命中链按 **target 优先**排列（最深的在最前），由 `RenderBox.hit_test` 逆序
  递归子级得到——后画的在上层，先命中的就是最上面那个。
- 分发分三阶段（docs/06 §5）：捕获（父→子）→ 目标 → 冒泡（子→父）。
  `PointerDispatch.stop_propagation()` 在任一阶段叫停后续。
- `ENTER` / `LEAVE` / hover **不信任后端直接发的**：SDL 的 enter/leave 是
  窗口级的，跟着鼠标进入窗口而不是进入控件。这里由指针位置事件（MOVE /
  DOWN / UP / WHEEL）的命中链**差分**生成——旧链有、新链没有的收 LEAVE，
  反之收 ENTER，于是"快速划过"也不会把 LEAVE 弄丢。把 DOWN 也算进来是为触屏：
  它没有悬停，按下时可能一条 MOVE 都没有。

状态：已实现（R7.1）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from ..backend.base import PointerEvent, PointerKind

__all__ = [
    "DispatchPhase",
    "HitTestEntry",
    "HitTestResult",
    "PointerDispatch",
    "PointerRouter",
    "PointerTarget",
]

# 会更新"指针下是什么"的事件。不能只认 MOVE：触屏没有悬停，
# 按下时可能一条 MOVE 都没有；只按 MOVE 差分会让"按下后移出取消"失效。
_POSITIONAL_KINDS = frozenset(
    (PointerKind.MOVE, PointerKind.DOWN, PointerKind.UP, PointerKind.WHEEL)
)


class DispatchPhase(Enum):
    """指针事件在一棵树里的三个分发阶段（docs/06 §5）。"""

    CAPTURE = "capture"  # 父 → 子，给父级"先看一眼"的机会
    TARGET = "target"  # 命中目标本身
    BUBBLE = "bubble"  # 子 → 父，默认的事件处理方式


@runtime_checkable
class PointerTarget(Protocol):
    """能接收指针事件的东西。`layout.RenderBox` 结构上满足它。

    刻意不用继承：`events`（L1）不能 import `layout`（L4），
    两边只共享这个结构性协议——Python 的鸭子类型在这里正好合适。
    """

    def handle_pointer_event(self, dispatch: PointerDispatch) -> None:
        """处理一次分发。`dispatch` 里带着阶段、局部坐标与叫停开关。"""
        ...


@dataclass(frozen=True, slots=True)
class HitTestEntry:
    """命中链上的一项：目标 + 事件在该目标**局部坐标系**里的位置。

    局部坐标让组件不必自己把祖先的偏移加起来——命中测试往下走时
    已经把每层的 `offset` 减掉了。
    """

    target: PointerTarget
    local_x: float
    local_y: float


class HitTestResult:
    """命中链。**target 优先**（最深的节点在 `entries[0]`）。

    由 `RenderBox.hit_test` 填充：它先递归子级、后加自己，
    所以子级自然排在前面对祖先的冒泡也就不用反着找了。
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: list[HitTestEntry] = []

    def add(self, target: PointerTarget, local_x: float, local_y: float) -> None:
        self._entries.append(HitTestEntry(target, local_x, local_y))

    @property
    def entries(self) -> tuple[HitTestEntry, ...]:
        return tuple(self._entries)

    @property
    def target(self) -> PointerTarget | None:
        """最深的命中目标（没有命中时 None）。"""
        return self._entries[0].target if self._entries else None

    def targets(self) -> tuple[PointerTarget, ...]:
        return tuple(entry.target for entry in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __iter__(self) -> Iterator[HitTestEntry]:
        return iter(self._entries)


@dataclass(slots=True)
class PointerDispatch:
    """一次分发的上下文。

    可变（`stop_propagation` 要改状态），但只在一次分发内有效——
    不缓存、不跨事件复用。
    """

    event: PointerEvent
    target: PointerTarget
    local_x: float
    local_y: float
    phase: DispatchPhase
    _stopped: bool = field(default=False, repr=False)

    def stop_propagation(self) -> None:
        """叫停后续阶段/祖先。当前处理函数返回后立即生效。"""
        self._stopped = True

    @property
    def propagation_stopped(self) -> bool:
        return self._stopped


class PointerRouter:
    """命中链 → 三阶段分发 + hover 差分。

    一个路由实例维护一份 hover 状态（上一条 MOVE 的命中链），
    所以要跟一棵树一一对应：`BuildOwner` 持有一个。
    """

    def __init__(self) -> None:
        # 上一条 MOVE 的命中目标，按 target 优先排列。
        self._hover: list[PointerTarget] = []

    @property
    def hover_chain(self) -> tuple[PointerTarget, ...]:
        return tuple(self._hover)

    def dispatch(self, result: HitTestResult, event: PointerEvent) -> bool:
        """分发给一个事件。返回"是否命中/有人处理"。

        任何带位置的事件都先做 hover 差分（先生成 ENTER/LEAVE，再走正常三阶段），
        这样组件既能收到"悬停开始/结束"，也能收到事件本身。
        """
        if event.kind in _POSITIONAL_KINDS:
            self._update_hover(result, event)

        entries = result.entries
        if not entries:
            return False
        return self._dispatch_phases(entries, event)

    # ------------------------------------------------------------ 三阶段

    def _dispatch_phases(self, entries: tuple[HitTestEntry, ...], event: PointerEvent) -> bool:
        target_entry = entries[0]
        ancestors = entries[1:]  # entries 是 target 优先，所以这里就是父级们

        stopped = False
        # 捕获：父 → 子（祖先里离根最远的先来，所以反转）
        for entry in reversed(ancestors):
            if stopped:
                break
            stopped = self._call(entry, event, DispatchPhase.CAPTURE)
        if not stopped:
            stopped = self._call(target_entry, event, DispatchPhase.TARGET)
        # 冒泡：子 → 父（ancestors 已是"最近的父在前"，正合适）
        for entry in ancestors:
            if stopped:
                break
            stopped = self._call(entry, event, DispatchPhase.BUBBLE)
        return True

    @staticmethod
    def _call(entry: HitTestEntry, event: PointerEvent, phase: DispatchPhase) -> bool:
        """调用一个目标，返回"是否叫停了传播"。"""
        dispatch = PointerDispatch(
            event=event,
            target=entry.target,
            local_x=entry.local_x,
            local_y=entry.local_y,
            phase=phase,
        )
        entry.target.handle_pointer_event(dispatch)
        return dispatch.propagation_stopped

    # ------------------------------------------------------------ hover 差分

    def _update_hover(self, result: HitTestResult, event: PointerEvent) -> None:
        new = list(result.targets())
        old = self._hover
        if new == old:
            return

        new_ids = {id(target) for target in new}
        old_ids = {id(target) for target in old}

        for target in old:
            if id(target) not in new_ids:
                self._deliver_hover(target, event, PointerKind.LEAVE)
        for target in new:
            if id(target) not in old_ids:
                self._deliver_hover(target, event, PointerKind.ENTER)

        self._hover = new

    @staticmethod
    def _deliver_hover(target: PointerTarget, event: PointerEvent, kind: PointerKind) -> None:
        """把一个合成的 ENTER/LEAVE 只送给该目标（目标阶段）。

        刻意不冒泡：hover 是"这个控件自己的事"，父级的 hover 由它自己
        在差分里收到的那一条决定——让 ENTER 冒泡会导致"鼠标在子级上、
        父级被重复 ENTER"。
        """
        synthetic = PointerEvent(
            kind=kind,
            x=event.x,
            y=event.y,
            window_id=event.window_id,
            time_ms=event.time_ms,
            pointer_type=event.pointer_type,
            pointer_id=event.pointer_id,
            modifiers=event.modifiers,
        )
        target.handle_pointer_event(
            PointerDispatch(
                event=synthetic,
                target=target,
                local_x=0.0,
                local_y=0.0,
                phase=DispatchPhase.TARGET,
            )
        )
