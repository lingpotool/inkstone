"""性能测试的计时装置 —— 全仓共用一份，免得两处各写一套口径。

**断言用最快一次，不用平均。** 这不是为了让数字好看，而是因为墙上时钟的噪声
是**单向的**：别的进程抢 CPU、GC、调度抖动只会让某一次变慢，绝不会让它比真实
成本更快。所以 N 次里的最小值是对真实成本最稳的估计，也把"偶尔被干扰"这个
失败模式从根上消掉。

用平均会怎样（R2 收尾时实测到的）：1001 节点全量布局采样 200 次，
p50=16.2ms、p99=30.8ms、max=35.4ms，**有 2 次越过 30ms 预算**。
20 次取平均虽然把单次抖动摊薄了 20 倍，但一次上百毫秒的抖动照样能把平均拉过线——
于是门禁"偶尔红一次"。而一个偶尔红的门禁比没有更糟：它会训练所有人忽略 CI。

中位数照常返回并打印：趋势比门槛有价值。

**别为了让门禁"稳"而调大预算**——那是把问题藏起来；先换估计量，再谈阈值。
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

__all__ = ["measure_best"]


def measure_best(
    action: Callable[[], object], *, runs: int = 20, warmup: int = 3
) -> tuple[float, float]:
    """跑 `runs` 轮，返回 `(最快一次, 中位数)`，单位毫秒。

    `action` 每轮调一次；预热轮次用于避开首次执行的导入与分支预测开销。
    """
    for _ in range(warmup):
        action()
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        action()
        samples.append((time.perf_counter() - start) * 1000)
    return min(samples), statistics.median(samples)
