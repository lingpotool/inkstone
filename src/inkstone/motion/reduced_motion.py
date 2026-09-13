"""`prefers-reduced-motion`：把系统的"减少动画"偏好接进动效层（R14.1）。

为什么这条不是可选的礼貌：前庭功能障碍的用户会因为视差/滑动/淡入而眩晕，
Windows 的"辅助功能 → 视觉效果 → 动画效果"就是给他们关的开关。系统让关，
我们就不播——**降级为瞬时到位，而不是把动画改慢**。

读法：Windows 上是 `SystemParametersInfo(SPI_GETCLIENTAREAANIMATION)`；
其他平台暂返回 False（SDL 没有这个查询；macOS 要走 NSWorkspace
`accessibilityDisplayShouldReduceMotion`，Linux 走 GTK 的
`gtk-enable-animations`——都在后续的跨平台工作流里补，接口不变）。

结果**缓存**（每帧查一次系统设置是浪费），测试可以 `set_reduced_motion()`
显式覆盖——否则同一条测试在开了/没开该选项的机器上行为不同。
"""

from __future__ import annotations

__all__ = ["duration", "prefers_reduced_motion", "reset_cache", "set_reduced_motion"]

_cached: bool | None = None


def _query_system() -> bool:
    """系统不让播动画 = 用户要求减少动效。"""
    return not _platform_animations_enabled()


def _platform_animations_enabled() -> bool:
    from ..backend import windows_shell

    return windows_shell.animations_enabled()


def prefers_reduced_motion() -> bool:
    """用户是否要求减少动效。"""
    global _cached
    if _cached is None:
        _cached = _query_system()
    return _cached


def set_reduced_motion(value: bool | None) -> None:
    """覆盖/清除缓存。传 None 表示"下次重新读系统设置"。测试与设置页用它。"""
    global _cached
    _cached = value


def reset_cache() -> None:
    set_reduced_motion(None)


def duration(ms: float) -> float:
    """按偏好折算动画时长：减少动效时一律 0（瞬时到位）。"""
    return 0.0 if prefers_reduced_motion() else ms
