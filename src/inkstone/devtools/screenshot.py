"""确定性截图 —— 黄金图测试的基础设施。

一条命令把组件树变成 PNG 字节：

    widget tree → build/layout/paint → DisplayList → 软件光栅 → PNG

"确定性"在这里是全套保证的叠加：
布局不读时钟、显示列表是不可变数据、软件光栅是逐字节确定的、
PNG 编码用固定参数。所以**同样的组件树，换台机器也是同样的字节**。
这正是 docs/06 §7"未来 AI 能稳定生成界面"的物理基础。

状态：已实现。
"""

from __future__ import annotations

from ..core import BuildOwner
from ..gfx import (
    DisplayList,
    DisplayListRecorder,
    FrameBuffer,
    SoftwareRasterizer,
    encode_png,
)
from ..layout import BoxConstraints
from ..layout.types import Rect

__all__ = ["render_to_display_list", "render_to_framebuffer", "render_to_png"]


def render_to_display_list(
    owner: BuildOwner,
    constraints: BoxConstraints,
    *,
    background: bool = True,
) -> DisplayList:
    """跑完一帧，返回显示列表。`background=True` 时先用主题底色铺满。"""
    recorder = DisplayListRecorder()
    if background:
        recorder.fill_rect(
            Rect(0.0, 0.0, constraints.max_width, constraints.max_height),
            owner.theme.color("bg"),
        )
    # 强制重画：黄金图要的是"每帧完整画面"，不是"动画场景里跳过的优化版"
    owner.begin_frame(constraints, recorder, force_repaint=True)
    return recorder.finish(_ceil(constraints.max_width), _ceil(constraints.max_height))


def render_to_framebuffer(
    owner: BuildOwner,
    constraints: BoxConstraints,
    *,
    background: bool = True,
) -> FrameBuffer:
    """跑完一帧，返回像素缓冲区。"""
    display_list = render_to_display_list(owner, constraints, background=background)
    return SoftwareRasterizer().rasterize(display_list)


def render_to_png(
    owner: BuildOwner,
    constraints: BoxConstraints,
    *,
    background: bool = True,
) -> bytes:
    """跑完一帧，返回 PNG 字节。"""
    frame = render_to_framebuffer(owner, constraints, background=background)
    return encode_png(frame.width, frame.height, bytes(frame.data))


def _ceil(value: float) -> int:
    from math import ceil

    return max(1, ceil(value))
