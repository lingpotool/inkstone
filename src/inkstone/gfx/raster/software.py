"""软件光栅 —— 纯 Python 的确定性光栅器。

它是整个渲染体系里的"事实源"：

    同样的显示列表 → 逐字节相同的像素。

黄金图测试、CI、以及将来 GL 后端上线后的对比基准，都拿它当裁判。

抗锯齿怎么做的，以及为什么它仍然确定：

    每个像素计算它到形状边缘的**有符号距离**（SDF），
    边缘 1px 宽内按距离做线性覆盖。这是纯浮点数学——
    不采样、不用随机数、不读时钟、不走任何平台路径。
    IEEE 754 双精度 + 正确舍入的 sqrt 在所有主流平台上
    给出完全相同的比特，所以结果**依然逐字节确定**。
    之前"为了确定性而关掉抗锯齿"是把两件事错误地划了等号。

另一条取舍：**不画阴影**。模糊阴影需要卷积滤镜，属 v1。
卡片现在有边框保层级，阴影令牌照常在，光栅端暂时忽略。

状态：已实现（SDF 抗锯齿）。
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
        half_w, half_h = rect.width / 2.0, rect.height / 2.0
        center_x, center_y = rect.left + half_w, rect.top + half_h

        x0, y0 = _clamp_to_canvas(rect.left, rect.top, clip, w, h)
        x1, y1 = _clamp_to_canvas(rect.right, rect.bottom, clip, w, h)

        for y in range(y0, y1):
            # 把不随 x 变化的部分提到行外
            qy = abs(y + 0.5 - center_y) - half_h + radius
            for x in range(x0, x1):
                qx = abs(x + 0.5 - center_x) - half_w + radius
                coverage = _edge_coverage(_rounded_sdf(qx, qy, radius))
                if coverage <= 0.0:
                    continue
                if coverage >= 1.0:
                    _blend_pixel(buf, w, x, y, color)
                else:
                    _blend_pixel(buf, w, x, y, color.with_alpha(color.a * coverage))

    # ------------------------------------------------------------ 描边

    def _stroke(self, buf: bytearray, w: int, h: int, op: StrokeRectOp) -> None:
        """描边 = 圆环：外圈覆盖 **减去** 内圈覆盖。

        外圈和内圈各自走 SDF 抗锯齿，圆环把描边严格贴合在圆角轮廓上——
        四角既无缝隙（早期"四条矩形条"的 bug），边缘也平滑。
        """
        rect, width, color = op.rect, op.width, op.color
        if color.a == 0.0 or width <= 0.0 or rect.width <= 0.0 or rect.height <= 0.0:
            return

        outer_radius = (
            min(op.radius, rect.width / 2.0, rect.height / 2.0) if op.radius > 0.0 else 0.0
        )
        inner_w, inner_h = rect.width - 2 * width, rect.height - 2 * width
        has_inner = inner_w > 0.0 and inner_h > 0.0
        inner_radius = max(0.0, outer_radius - width)

        outer_half_w, outer_half_h = rect.width / 2.0, rect.height / 2.0
        center_x, center_y = rect.left + outer_half_w, rect.top + outer_half_h
        # 内圈与外圈同心（等宽内缩），只是半长小了 width
        inner_half_w, inner_half_h = max(inner_w, 0.0) / 2.0, max(inner_h, 0.0) / 2.0

        x0, y0 = _clamp_to_canvas(rect.left, rect.top, op.clip, w, h)
        x1, y1 = _clamp_to_canvas(rect.right, rect.bottom, op.clip, w, h)

        for y in range(y0, y1):
            py = y + 0.5
            qy_outer = abs(py - center_y) - outer_half_h + outer_radius
            qy_inner = abs(py - center_y) - inner_half_h + inner_radius
            for x in range(x0, x1):
                px = x + 0.5
                qx_outer = abs(px - center_x) - outer_half_w + outer_radius
                cov_outer = _edge_coverage(_rounded_sdf(qx_outer, qy_outer, outer_radius))
                if cov_outer <= 0.0:
                    continue

                cov_inner = 0.0
                if has_inner:
                    qx_inner = abs(px - center_x) - inner_half_w + inner_radius
                    cov_inner = _edge_coverage(_rounded_sdf(qx_inner, qy_inner, inner_radius))

                ring = cov_outer - cov_inner
                if ring <= 0.0:
                    continue
                if ring >= 1.0:
                    _blend_pixel(buf, w, x, y, color)
                else:
                    _blend_pixel(buf, w, x, y, color.with_alpha(color.a * ring))


# ---------------------------------------------------------------- 几何辅助

# 抗锯齿过渡带宽（像素）。边缘两侧各 0.5px 内做线性覆盖。
_AA_WIDTH = 0.5


def _rounded_sdf(qx: float, qy: float, radius: float) -> float:
    """圆角矩形的有符号距离。`qx/qy = abs(p−center) − half + radius`。

    负数在形状内，正数在外。这是 Inigo Quilez 的标准公式，
    内部像素走无开方的快路径。
    """
    outside = 0.0
    if qx > 0.0 or qy > 0.0:
        ox = max(qx, 0.0)
        oy = max(qy, 0.0)
        outside = (ox * ox + oy * oy) ** 0.5
    inside = min(max(qx, qy), 0.0)
    return float(outside + inside - radius)


def _edge_coverage(distance: float) -> float:
    """有符号距离 → 覆盖率 0..1。边缘 1px 内线性过渡。"""
    if distance <= -_AA_WIDTH:
        return 1.0
    if distance >= _AA_WIDTH:
        return 0.0
    return _AA_WIDTH - distance


def _clamp_to_canvas(x: float, y: float, clip: Rect | None, w: int, h: int) -> tuple[int, int]:
    return (
        _clamp_int(x, (clip.left if clip else 0.0), (clip.right if clip else float(w)), w),
        _clamp_int(y, (clip.top if clip else 0.0), (clip.bottom if clip else float(h)), h),
    )


def _clamp_int(value: float, low: float, high: float, limit: int) -> int:
    return max(0, min(limit, int(max(low, min(high, value)))))


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
