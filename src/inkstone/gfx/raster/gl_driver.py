"""GL 驱动接缝 —— GPU 调用的最小契约（R8.1，docs/22）。

**为什么要有这一层**：GL 后端里只有很小一部分是"GPU 的事"（建上下文、
传纹理、下 draw call、读像素），大头是**与 GPU 无关的逻辑**——帧状态机、
裁剪语义、文本取掩码、资源生命周期、错误处理。把前者收进一个 Protocol，
后者就能在没有显卡的 CI 上被完整测试（测试注入 FakeDriver 记录调用序列），
真机 GL 调用只剩"照着协议填一个 ctypes 实现"。

这与 SDL2 后端的路线一致：**零运行时依赖，用 ctypes 直调平台库**
（铁律 5：不新增运行时依赖）。本模块只定义协议，不 import 任何 GL 库。

坐标口径：驱动收到的一切坐标都是**设备像素**（显示列表在录制时已经
乘过 DPI 档位，见 ADR-0015），驱动不再乘 scale。`scale` 只在 `begin`
里传一次，供线宽/字形取整等"按物理分辨率"的细节使用。
"""

from __future__ import annotations

from typing import Protocol

from ...layout.types import Rect
from ..color import Color
from ..glyphs import GlyphMask

__all__ = ["GLDriver"]


class GLDriver(Protocol):
    """一个 GL 实现要提供的能力。真机实现属 R8.2（ctypes 直调 opengl32/EGL/GLX）。"""

    def begin(self, width_px: int, height_px: int, scale: float) -> None:
        """准备物理尺寸为 `width_px × height_px` 的帧目标（FBO 或窗口后备缓冲）。"""

    def end(self) -> None:
        """结束一帧（提交；swap 由上层的呈现接缝决定，见 `FrameRenderer`）。"""

    def set_scissor(self, rect: Rect | None) -> None:
        """把裁剪框设为 `rect`（设备像素，左上原点），`None` = 关闭裁剪。

        GL 的 scissor 是"整帧状态"，所以每个绘制指令前都要按它的 clip 设置一次；
        `execute` 的脏矩形也并进同一个 scissor——两者取交。
        """

    def fill_rect(self, rect: Rect, radius: float, color: Color) -> None:
        """实心矩形/圆角矩形（设备像素）。`radius` 已由调用方夹到短边一半内。"""

    def stroke_rect(self, rect: Rect, radius: float, width: float, color: Color) -> None:
        """矩形描边，线宽向矩形内侧生长（与软件光栅语义一致）。"""

    def draw_glyph(self, mask: GlyphMask, pen_x: float, baseline_y: float, color: Color) -> None:
        """把一个灰度覆盖度掩码画到 `(pen_x, baseline_y)`。

        掩码的 `left` / `top` 是相对该点的偏移（见 `GlyphMask`）。驱动负责
        把它变成图集里的一块纹理并着色——**不要**在驱动里重新测量宽度，
        宽度是文本层的事（ADR-0006）。
        """

    def create_texture(self, width: int, height: int, pixels: bytes) -> int:
        """上传一张 RGBA 位图，返回纹理句柄。句柄由驱动分配、由调用方释放。"""

    def destroy_texture(self, handle: int) -> None:
        """释放纹理句柄。对未知句柄应当幂等（与协议约定一致）。"""

    def read_pixels(self) -> bytes:
        """读回当前帧目标，RGBA 行优先，长度 = width_px × height_px × 4。

        **这是异常操作**（要同步、要回读），只用于截图与黄金图；
        正常路径是 `end` 之后换链上屏。
        """
