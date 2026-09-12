"""光栅后端协议 —— 把显示列表变成像素的接口（docs/03 §2）。

一帧的生命周期：

    begin_frame(size, scale)    按 size × scale 准备帧缓冲
    execute(display_list, clip) 把指令画进去；clip 是**本次要更新的区域**（脏矩形）
    end_frame()                 结束一帧（提交 / 交换）
    screenshot()                **显式**读回像素

**为什么读回不是唯一接口。** 旧协议只有一个
`rasterize(display_list) -> FrameBuffer`，它把"读回内存"当成了后端的唯一出口。
GL / Skia 后端的正常路径是"画到 GPU 表面再交换"，把像素读回内存是**异常操作**
（要同步、要回读、慢）。用旧协议，这两个后端在结构上无法实现——
协议画错了，不是实现偷懒。docs/03 §2 描述的形态才是对的。

**帧缓冲所有权在后端**（docs/14 §4 已拍板）：`screenshot()` 返回的是**一份拷贝**，
改它不会影响后端持有的下一帧。上层的正常路径是"提交并交换"，
不是"每帧把几 MB 像素搬回 Python"。

**脏矩形是 `execute` 的参数，不进显示列表**（同一处决策）：显示列表保持"纯图纸"
——同样的组件树永远产出同样的指令序列，黄金图的比对语义不被重绘策略污染。

两个落地方向（docs/03 §3 的三后端选型）：

    SoftwareRasterizer（本阶段）  纯 Python，画进内存缓冲区。
                                  慢，但**完全确定**——同样的显示列表逐像素相同，
                                  黄金图测试与 CI 都靠它。
    GLSkiaBackend（Phase 3）      GL + Skia，快。它上线后，
                                  黄金图继续用软件光栅做"事实源"，
                                  GL 的输出与它比对（容忍抗锯齿差异），见 docs/03。

状态：协议已按 docs/03 §2 重塑（R3.1）；软件光栅已实现新协议。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ...layout.types import Rect, Size
from ..display_list import DisplayList

__all__ = ["FrameBuffer", "RasterBackend", "RasterError"]


class RasterError(RuntimeError):
    """帧生命周期用错了（顺序不对、缺少配对调用）。

    与 `FrameError` 同一种立场：时序错误宁可响亮地失败，也不要留下
    "画面看起来还行、其实少画了半帧"这种要查一整天的诡异行为。
    """

    def __init__(self, message: str, *, suggestion: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion

    def __str__(self) -> str:
        lines = [f"RasterError: {self.message}"]
        if self.suggestion:
            lines.append(f"  建议: {self.suggestion}")
        return "\n".join(lines)


@dataclass(slots=True)
class FrameBuffer:
    """一段 RGBA 像素。`data` 长度必须等于 width × height × 4。"""

    width: int
    height: int
    data: bytearray

    def __post_init__(self) -> None:
        expected = self.width * self.height * 4
        if len(self.data) != expected:
            raise ValueError(f"像素数据长度应为 {expected}，收到 {len(self.data)}")

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        """取一个像素的 (r, g, b, a)。测试与检查器用。"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"像素坐标越界：({x}, {y})，画布 {self.width}×{self.height}")
        base = (y * self.width + x) * 4
        d = self.data
        return d[base], d[base + 1], d[base + 2], d[base + 3]


@runtime_checkable
class RasterBackend(Protocol):
    """光栅后端的唯一契约。

    实现者必须保证：**同样的显示列表 + 同样的尺寸 → 逐像素相同**。
    这条确定性是黄金图、无头 CI 与"AI 能稳定生成界面"的物理基础。

    标了 `runtime_checkable`：后端是否满足协议要能在测试里**结构性地**问出来
    （`isinstance(raster, RasterBackend)`），而不是靠人肉比对方法名——
    "SDL2Backend 声称实现了 Backend 协议、其实少了四个方法"就是审查抓到的真事。
    """

    def begin_frame(self, size: Size, scale: float) -> None:
        """开始一帧。

        `size` 是**逻辑尺寸**（组件树算出来的那个），`scale` 是设备像素比
        （100% / 125% / 150% 对应 1.0 / 1.25 / 1.5）。帧缓冲的物理尺寸是
        `size × scale`，所以光栅器拿到的指令坐标应当是**设备像素**——
        缩放由录制器的变换矩阵负责（见 `DisplayListRecorder.scale`）。
        """
        ...

    def execute(self, display_list: DisplayList, clip: Rect | None = None) -> None:
        """把显示列表画进当前帧缓冲。

        `clip` 是本次要更新的区域（**脏矩形**），`None` 表示整幅。
        区域之外保持上一帧的内容——这正是"只重画脏矩形"的落地方式，
        也是为什么它不能塞进显示列表：显示列表是图纸，重绘策略是执行细节。
        """
        ...

    def end_frame(self) -> None:
        """结束一帧（提交 / 交换）。此后才能 `screenshot()`。"""
        ...

    def screenshot(self) -> FrameBuffer:
        """读回当前帧缓冲的像素**拷贝**。

        它是显式操作而不是后端的唯一出口：正常路径是 end_frame 之后交换到屏幕，
        读回只用于截图、黄金图与调试。
        """
        ...

    def create_image(self, width: int, height: int, pixels: bytes) -> int:
        """上传一张位图，返回后端句柄。句柄由后端分配、由 `destroy_image` 释放。

        位图生命周期进协议是必须的：GL / Skia 的纹理资源不在 Python 的 GC 管辖内，
        没有显式释放就会稳定泄漏。软件光栅暂未实现（抛 `NotImplementedError`），
        它属"位图与图标"落地时的工作。
        """
        ...

    def destroy_image(self, handle: int) -> None:
        """释放 `create_image` 返回的句柄。对未知句柄的处理由实现决定（幂等为宜）。"""
        ...
