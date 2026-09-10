"""软件光栅 —— 纯 Python 的确定性光栅器。

它是整个渲染体系里的"事实源"：

    同样的显示列表 → 逐字节相同的像素。
    不读时钟、不用随机数、不走任何平台路径。

黄金图测试、CI、以及将来 GL 后端上线后的对比基准，都拿它当裁判。

两条刻意的取舍：

1. **不做抗锯齿。** 抗锯齿的实现方式太多了，不同算法会得到不同像素。
   硬边是丑了点，但它是**确定性的**——先保证"逐字节可比对"，
   抗锯齿以后作为一个可开可关的选项加回来（开了就不再追求逐字节一致）。
2. **不画阴影。** 模糊阴影需要卷积滤镜，属于 v1 的事。
   卡片现在有边框保层级，阴影令牌照常在，只是光栅端暂时忽略。

状态：已实现。
"""

from __future__ import annotations

import struct
import zlib
from binascii import crc32

from ...layout.types import Rect
from ..color import Color
from ..display_list import DisplayList, FillRectOp, Op, StrokeRectOp
from .base import FrameBuffer, RasterBackend

__all__ = ["SoftwareRasterizer", "encode_png"]


class SoftwareRasterizer(RasterBackend):
    """逐行扫描的确定性光栅器。"""

    def rasterize(self, display_list: DisplayList) -> FrameBuffer:
        width, height = display_list.width, display_list.height
        buffer = bytearray(width * height * 4)
        for op in display_list.ops:
            self._rasterize_op(buffer, width, height, op)
        return FrameBuffer(width, height, buffer)

    # ------------------------------------------------------------ 指令分派

    def _rasterize_op(self, buf: bytearray, w: int, h: int, op: Op) -> None:
        if isinstance(op, FillRectOp):
            self._fill(buf, w, h, op.rect, op.color, op.radius, op.clip)
        elif isinstance(op, StrokeRectOp):
            self._stroke(buf, w, h, op)

    # ------------------------------------------------------------ 填充

    def _fill(
        self,
        buf: bytearray,
        w: int,
        h: int,
        rect: Rect,
        color: Color,
        radius: float,
        clip: Rect | None,
    ) -> None:
        if color.a == 0.0 or rect.width <= 0.0 or rect.height <= 0.0:
            return

        radius = min(radius, rect.width / 2.0, rect.height / 2.0)
        x0, y0 = _clamp_to_canvas(rect.left, rect.top, clip, w, h)
        x1, y1 = _clamp_to_canvas(rect.right, rect.bottom, clip, w, h)

        for y in range(y0, y1):
            for x in range(x0, x1):
                if radius > 0.0 and not _inside_rounded(x + 0.5, y + 0.5, rect, radius):
                    continue
                _blend_pixel(buf, w, x, y, color)

    def _stroke(self, buf: bytearray, w: int, h: int, op: StrokeRectOp) -> None:
        """描边 = 圆环：外圈圆角矩形 **减去** 内圈圆角矩形。

        曾经用"四条矩形条拼边框"，结果圆角处会漏：条的两端被圆角裁掉，
        填充又是圆的，于是**边框与圆角之间裂开露出底色**。
        圆环法让描边严格贴合圆角轮廓，四角不会有任何缝隙。
        """
        rect, width, color = op.rect, op.width, op.color
        if color.a == 0.0 or width <= 0.0 or rect.width <= 0.0 or rect.height <= 0.0:
            return

        outer_radius = (
            min(op.radius, rect.width / 2.0, rect.height / 2.0) if op.radius > 0.0 else 0.0
        )
        inner_w, inner_h = rect.width - 2 * width, rect.height - 2 * width
        has_inner = inner_w > 0.0 and inner_h > 0.0
        inner = Rect(rect.left + width, rect.top + width, max(inner_w, 0.0), max(inner_h, 0.0))
        inner_radius = max(0.0, outer_radius - width)

        x0, y0 = _clamp_to_canvas(rect.left, rect.top, op.clip, w, h)
        x1, y1 = _clamp_to_canvas(rect.right, rect.bottom, op.clip, w, h)

        for y in range(y0, y1):
            for x in range(x0, x1):
                px, py = x + 0.5, y + 0.5
                if not _inside_rounded(px, py, rect, outer_radius):
                    continue
                if has_inner and _inside_rounded(px, py, inner, inner_radius):
                    continue
                _blend_pixel(buf, w, x, y, color)


# ---------------------------------------------------------------- 几何辅助


def _clamp_to_canvas(x: float, y: float, clip: Rect | None, w: int, h: int) -> tuple[int, int]:
    return (
        _clamp_int(x, (clip.left if clip else 0.0), (clip.right if clip else float(w)), w),
        _clamp_int(y, (clip.top if clip else 0.0), (clip.bottom if clip else float(h)), h),
    )


def _clamp_int(value: float, low: float, high: float, limit: int) -> int:
    return max(0, min(limit, int(max(low, min(high, value)))))


def _inside_rounded(px: float, py: float, rect: Rect, radius: float) -> bool:
    """点是否在圆角矩形内。用像素中心采样，保证确定性。"""
    cx = min(max(px, rect.left + radius), rect.right - radius)
    cy = min(max(py, rect.top + radius), rect.bottom - radius)
    return (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius


# ---------------------------------------------------------------- 像素混合


def _blend_pixel(buf: bytearray, width: int, x: int, y: int, color: Color) -> None:
    """source-over 混合写入一个像素。"""
    base = (y * width + x) * 4
    sa = color.a
    if sa >= 1.0:
        buf[base] = color.r
        buf[base + 1] = color.g
        buf[base + 2] = color.b
        buf[base + 3] = 255
        return
    inv = 1.0 - sa
    buf[base] = round(color.r * sa + buf[base] * inv)
    buf[base + 1] = round(color.g * sa + buf[base + 1] * inv)
    buf[base + 2] = round(color.b * sa + buf[base + 2] * inv)
    buf[base + 3] = round(255 * sa + buf[base + 3] * inv)


# ---------------------------------------------------------------- PNG 编码

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """把 RGBA 像素编码成 PNG（纯 stdlib，确定性）。

    用 zlib + struct + crc32 手写编码，而不是引入 Pillow——
    少一个依赖，而且编码过程完全可控：同样的像素永远得到同样的字节。
    这是黄金图"逐字节比对"成立的前提。
    """
    if len(rgba) != width * height * 4:
        raise ValueError(f"像素数据长度应为 {width * height * 4}，收到 {len(rgba)}")

    # 每行一个 filter-type=0 的前导字节
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(height))
    compressed = zlib.compress(raw, level=6)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        _PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", crc32(kind + payload) & 0xFFFFFFFF)
    )
