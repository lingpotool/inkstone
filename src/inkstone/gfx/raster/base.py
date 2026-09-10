"""光栅后端协议 —— 把显示列表变成像素的接口。

两个落地方向（docs/03 §8 的"分层先行"）：

    SoftwareRasterizer（本阶段）  纯 Python，画进内存缓冲区。
                                  慢，但**完全确定**——同样的显示列表逐字节相同，
                                  黄金图测试与 CI 都靠它。
    GLSkiaBackend（Phase 3）      GL + Skia，快。它上线后，
                                  黄金图继续用软件光栅做"事实源"，
                                  GL 的输出与它比对（容忍抗锯齿差异），见 docs/03。

状态：协议已实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..display_list import DisplayList

__all__ = ["FrameBuffer", "RasterBackend"]


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


class RasterBackend(Protocol):
    """光栅后端的唯一契约。"""

    def rasterize(self, display_list: DisplayList) -> FrameBuffer:
        """把显示列表画成像素。"""
        ...
