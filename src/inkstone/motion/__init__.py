"""动效引擎：时间驱动的值变化（缓动、弹簧、过渡、减少动效偏好）。

状态：**排帧基座 + 标量动画已实现（R14.1）**；时间线/过渡/弹簧待有消费者时再做。
"""

from .animation import AnimatedValue
from .reduced_motion import duration, prefers_reduced_motion, set_reduced_motion
from .ticker import Tickable, Ticker

__all__ = [
    "AnimatedValue",
    "Tickable",
    "Ticker",
    "duration",
    "prefers_reduced_motion",
    "set_reduced_motion",
]
