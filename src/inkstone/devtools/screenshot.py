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
from ..layout.types import Rect, Size

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
    glyph_provider: object | None = None,
) -> FrameBuffer:
    """跑完一帧，返回像素缓冲区。

    `glyph_provider` 默认**跟着这棵树的度量来源走**：如果 `TextEngine`
    背后的度量对象同时能提供字形（比如 `GdiFontEngine`），就用它。
    这样"度量"与"字形"必然出自同一份字体——字距与字形不可能对不上。

    为什么不能各自独立挑：度量用确定性表、字形用系统字体的话，
    排版按 14px 排、字形按 13.2px 画，中英混排时会出现
    "有的字挤在一起、有的字之间留缝"，而且只在真机上才看得见。

    走的是 docs/03 §2 的帧生命周期（R3.1 之后）：
    `begin_frame → execute → end_frame → screenshot`。
    截图是**读回**操作，不是后端的唯一出口——`scale=1.0` 是因为
    设备像素比的接线属 DPI 那一项工作（docs/03 §5），届时这里会带上真实比例。
    """
    display_list = render_to_display_list(owner, constraints, background=background)
    provider = glyph_provider if glyph_provider is not None else _provider_from(owner)
    raster = (
        SoftwareRasterizer() if provider is None else SoftwareRasterizer(glyph_provider=provider)  # type: ignore[arg-type]
    )
    raster.begin_frame(Size(float(display_list.width), float(display_list.height)), 1.0)
    raster.execute(display_list)
    raster.end_frame()
    return raster.screenshot()


def _provider_from(owner: BuildOwner) -> object | None:
    """从组件树的文本引擎里取出字形提供方（没有则返回 None → 用内置字形）。

    优先问 `glyph_provider`：那是后端**显式**声明的"我能画这些字形"，
    比能力嗅探（有没有 `mask_for`）可靠——后者分不清"真能画"
    和"转发给了一个画不了的东西"。
    """
    engine = owner.text_engine
    if engine is None:
        return None
    metrics: object | None = getattr(engine, "metrics", None)
    if metrics is None:
        return None
    provider: object | None = getattr(metrics, "glyph_provider", None)
    return provider


def render_to_png(
    owner: BuildOwner,
    constraints: BoxConstraints,
    *,
    background: bool = True,
    glyph_provider: object | None = None,
) -> bytes:
    """跑完一帧，返回 PNG 字节。"""
    frame = render_to_framebuffer(
        owner, constraints, background=background, glyph_provider=glyph_provider
    )
    return encode_png(frame.width, frame.height, bytes(frame.data))


def _ceil(value: float) -> int:
    from math import ceil

    return max(1, ceil(value))
