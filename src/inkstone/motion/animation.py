"""动画对象：一个随时间从当前值走到目标值的标量（R14.1）。

刻意的**极简**：这一版只解决"淡入/淡出"这类单一标量过渡——不做时间线、
不排队、不链式。真正的过渡/弹簧留给 `transition.py` / `spring.py`，等有
消费者（弹层入场、页面切换）时再写：先做一个够用的，比先做一个全面的稳。

两条纪律：

1. **时间由外部注入**（`tick(now_ms)`），对象自己不读时钟；
2. **到了目标就停**：`tick` 返回 False，排帧随之停下（空闲不留帧）。
"""

from __future__ import annotations

from collections.abc import Callable

from .ticker import Tickable

__all__ = ["AnimatedValue"]


def _linear(t: float) -> float:
    return t


class AnimatedValue(Tickable):
    """在 `duration_ms` 内从当前值走到目标值的标量，可缓动。

    `set_target` 总是**从当前值重新起步**（而不是从上一次的目标）——
    这正是"淡出到一半又被唤醒"该有的行为：不跳变。
    """

    def __init__(
        self,
        value: float = 0.0,
        *,
        duration_ms: float = 0.0,
        easing: Callable[[float], float] = _linear,
    ) -> None:
        self._value = value
        self._start_value = value
        self._target = value
        self._start_ms = 0.0
        self._duration_ms = max(0.0, duration_ms)
        self._easing = easing
        self._running = False

    @property
    def value(self) -> float:
        return self._value

    @property
    def target(self) -> float:
        return self._target

    @property
    def running(self) -> bool:
        return self._running

    def set_target(self, target: float, *, now_ms: float, duration_ms: float | None = None) -> None:
        """改目标并从**当前值**重新开始计时。"""
        if duration_ms is not None:
            self._duration_ms = max(0.0, duration_ms)
        if target == self._target and self._running:
            return
        self._target = target
        if self._duration_ms <= 0.0 or self._value == target:
            self._value = target
            self._running = False
            return
        self._start_value = self._value
        self._start_ms = now_ms
        self._running = True

    def tick(self, now_ms: float) -> bool:
        if not self._running:
            return False
        if self._duration_ms <= 0.0:
            self._value = self._target
            self._running = False
            return False
        elapsed = now_ms - self._start_ms
        if elapsed >= self._duration_ms:
            self._value = self._target
            self._running = False
            return False
        t = max(0.0, elapsed / self._duration_ms)
        self._value = self._start_value + (self._target - self._start_value) * self._easing(t)
        return True

    def finish(self) -> None:
        """立刻到位——`prefers-reduced-motion` 的降级路径：不播动画。"""
        self._value = self._target
        self._running = False
