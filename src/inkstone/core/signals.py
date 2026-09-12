"""信号 —— 高频状态的精确更新通道（docs/06 §3 状态双轨的另一轨）。

与 Inherited（element.py）共用同一套内核：**读时登记、写时标脏**。

    build 期间读 `signal.value` → 当前组件登记为该信号的依赖者
    写 `signal.value = x`       → 只标脏这些依赖者，整棵树其余部分不动

`setState` 标脏整棵子树，`Signal` 精确到"读过它的那几个组件"——
行情、进度、计数器这类每秒更新十几次的东西走这里；其余用 setState。

三个原语：

- `Signal[T]`   可写的源
- `Computed[T]` 派生值：缓存 + 惰性——依赖变了只标脏，下次读才重算，
                并把脏继续传给下游
- `Effect`      副作用：依赖变了就重跑。给了 `owner` 就排队到下一帧
                build 之前（与 UI 同帧结算）；没给就同步重跑（纯逻辑场景）

状态：已实现（R6.2，docs/20）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, TypeVar

from .element import (
    DependencySource,
    Dependent,
    current_dependency_collector,
    dependency_collection,
)

if TYPE_CHECKING:
    from .binding import BuildOwner

__all__ = ["Computed", "Effect", "Signal"]

T = TypeVar("T")


class Signal(Generic[T]):
    """可写信号。读 = 登记依赖，写 = 标脏依赖者。"""

    def __init__(self, value: T) -> None:
        self._value = value
        self._dependents: set[Dependent] = set()

    @property
    def value(self) -> T:
        collector = current_dependency_collector()
        if collector is not None:
            collector._track_dependency(self)
        return self._value

    @value.setter
    def value(self, new_value: T) -> None:
        """写入并标脏所有依赖者。

        刻意不做"同值跳过"：值可能是原地修改后写回的同一个对象，
        判等跳过会把这种更新吞掉（静默失效，最难查的那类 bug）。
        不想触发，就别写。
        """
        self._value = new_value
        for dependent in list(self._dependents):
            dependent._on_dependency_dirty()

    def peek(self) -> T:
        """读值但不登记依赖——"我只是看一眼"的显式写法。"""
        return self._value

    # ---- DependencySource 接口
    def _subscribe(self, dependent: Dependent) -> None:
        self._dependents.add(dependent)

    def _unsubscribe(self, dependent: Dependent) -> None:
        self._dependents.discard(dependent)


class Computed(Generic[T]):
    """派生信号：既是下游的源（DependencySource），又是上游的依赖者（Dependent）。"""

    def __init__(self, fn: Callable[[], T]) -> None:
        self._fn = fn
        self._dependencies: set[DependencySource] = set()
        self._dependents: set[Dependent] = set()
        self._dirty = True
        self._value: T  # 惰性：首次读取时才计算

    @property
    def value(self) -> T:
        collector = current_dependency_collector()
        if collector is not None:
            collector._track_dependency(self)
        if self._dirty:
            self._recompute()
        return self._value

    def peek(self) -> T:
        """读值但不登记依赖。"""
        if self._dirty:
            self._recompute()
        return self._value

    def _recompute(self) -> None:
        # 依赖每次重算重新登记：fn 里读哪些信号可能随分支变化
        for source in self._dependencies:
            source._unsubscribe(self)
        self._dependencies.clear()
        with dependency_collection(self):
            self._value = self._fn()
        self._dirty = False

    # ---- Dependent 接口（上游脏了）
    def _on_dependency_dirty(self) -> None:
        if self._dirty:
            return  # 已经脏过：下游在第一次变脏时就通知过了
        self._dirty = True
        for dependent in list(self._dependents):
            dependent._on_dependency_dirty()

    # ---- DependencySource 接口（自己是下游的源）
    def _subscribe(self, dependent: Dependent) -> None:
        self._dependents.add(dependent)

    def _unsubscribe(self, dependent: Dependent) -> None:
        self._dependents.discard(dependent)

    # ---- Collector 接口（重算时收集自己的依赖）
    def _track_dependency(self, source: DependencySource) -> None:
        if source in self._dependencies:
            return
        self._dependencies.add(source)
        source._subscribe(self)


class Effect:
    """依赖信号变了就重跑的副作用。

    调度语义：`owner` 给出时，重跑排队到下一帧 **build 之前**
    （docs/20 R6.2）——effect 里 set_state 产生的脏由同一帧的
    flush_build 收掉，UI 与副作用同帧结算，不会出现"界面慢一拍"。
    不给 owner 则同步重跑：用于不碰 UI 的纯逻辑（日志、派生同步）。
    """

    def __init__(self, fn: Callable[[], None], *, owner: BuildOwner | None = None) -> None:
        self._fn = fn
        self._owner = owner
        self._dependencies: set[DependencySource] = set()
        self._pending = False
        self._disposed = False
        self._run()  # 建立初始依赖

    def flush(self) -> None:
        """由 BuildOwner 在下一帧 build 之前调用。"""
        if self._disposed or not self._pending:
            return
        self._run()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._release()

    # ---- Dependent 接口
    def _on_dependency_dirty(self) -> None:
        if self._disposed or self._pending:
            return  # 一帧内多次失效只重跑一次——与脏集合的批处理同理
        self._pending = True
        owner = self._owner
        if owner is None:
            self._run()
        else:
            owner.schedule_effect(self)

    # ---- Collector 接口
    def _track_dependency(self, source: DependencySource) -> None:
        if source in self._dependencies:
            return
        self._dependencies.add(source)
        source._subscribe(self)

    def _run(self) -> None:
        self._release()
        self._pending = False
        with dependency_collection(self):
            self._fn()

    def _release(self) -> None:
        for source in self._dependencies:
            source._unsubscribe(self)
        self._dependencies.clear()
