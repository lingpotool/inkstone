"""排帧驱动：让"随时间变化的东西"每帧被推进一次（R14.1）。

**时间只有一条来源**（ADR-0015 的同一纪律）：`tick(now_ms)` 的 `now_ms` 来自
`BuildOwner.begin_frame(now_ms=…)`，而它又来自后端时钟。渲染层不读墙上时钟——
所以"动画播到第 300ms"在无头测试里和真机上完全一致：
`HeadlessBackend.advance(300)` 一推，断言就是确定的。

谁需要它：滚动条淡出、将来的过渡/弹簧（`motion/animation.py` 等）。
**不要**用它做逐帧无条件重绘——`tick` 返回 False 表示"我完事了"，
排帧就此停下，空闲时不该有帧。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Tickable", "Ticker"]


@runtime_checkable
class Tickable(Protocol):
    """能被"每帧推一下"的对象。"""

    def tick(self, now_ms: float) -> bool:
        """推进到 `now_ms`。返回 True 表示**还没完，请再排一帧**。"""


class Ticker:
    """一组 Tickable 的集合，外加"是否需要下一帧"的汇总。

    用法（`BuildOwner` 持有一个）：

        if ticker.tick(now_ms):      # 还有人没跑完
            request_frame()          # → 下一帧继续推进

    没有成员需要推进时 `tick` 返回 False，主循环因此可以空转在事件等待上，
    不会为了"可能有的动画"每帧空跑。
    """

    def __init__(self) -> None:
        self._items: list[Tickable] = []

    @property
    def active(self) -> int:
        return len(self._items)

    def add(self, item: Tickable) -> None:
        if item not in self._items:
            self._items.append(item)

    def remove(self, item: Tickable) -> None:
        if item in self._items:
            self._items.remove(item)

    def clear(self) -> None:
        self._items.clear()

    def tick(self, now_ms: float) -> bool:
        """推进一步。有成员跑完会自行退出集合——不靠调用方清理。"""
        pending = False
        for item in list(self._items):
            if item.tick(now_ms):
                pending = True
            else:
                self._items.remove(item)
        return pending
