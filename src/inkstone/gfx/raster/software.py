"""软件光栅 —— 纯 Python 的确定性光栅器。

它是整个渲染体系里的"事实源"：

    同样的显示列表 → 逐字节相同的像素。

黄金图测试、CI、以及将来 GL 后端上线后的对比基准，都拿它当裁判。

抗锯齿的做法：**扫描线 + 水平方向解析覆盖**。

    圆角矩形在任意高度的水平跨度可以精确求解（圆弧方程），
    所以每个像素的**水平覆盖是精确值**，只有垂直方向做了 N 点采样。

    相比"每像素数子采样点"：质量从 2^N 量级跳到 256×N 量级，
    而且内部整段能用切片一次性写入，反而更快。

确定性从哪来：

    不用随机数（固定偏移）、不读时钟、不走任何平台路径。
    IEEE 754 双精度 + 正确舍入的 sqrt 在所有主流平台上给出完全相同的比特，
    所以**抗锯齿与确定性并不冲突**——之前"为了确定性关掉抗锯齿"
    是把两件事错误地划了等号。

另一条取舍：**不画阴影**。模糊阴影需要卷积滤镜，属 v1。
卡片现在有边框保层级，阴影令牌照常在，光栅端暂时忽略。

状态：已实现（扫描线解析抗锯齿）。
"""

from __future__ import annotations

import math
import struct
import zlib
from binascii import crc32
from dataclasses import dataclass

from ...layout.types import Rect
from ..color import Color
from ..display_list import DisplayList, FillRectOp, Op, StrokeRectOp, TextRunOp
from ..glyphs import BuiltinGlyphProvider, GlyphProvider, rect_of_mask
from .base import FrameBuffer, RasterBackend

__all__ = ["SoftwareRasterizer", "encode_png"]


@dataclass(frozen=True, slots=True)
class _RoundedBox:
    """圆角矩形的几何，能回答"在某个高度上它的水平跨度是多少"。"""

    center_x: float
    center_y: float
    half_w: float
    half_h: float
    radius: float

    @classmethod
    def of(cls, rect: Rect, radius: float) -> _RoundedBox:
        half_w, half_h = rect.width / 2.0, rect.height / 2.0
        return cls(
            center_x=rect.left + half_w,
            center_y=rect.top + half_h,
            half_w=half_w,
            half_h=half_h,
            radius=min(radius, half_w, half_h) if radius > 0.0 else 0.0,
        )

    def x_span_at(self, py: float) -> tuple[float, float] | None:
        """在高度 py 上，形状的水平跨度。None = 这个高度不在形状内。

        圆角矩形分两段：
          中段（|py−cy| ≤ half_h − r）  整宽
          圆角段                        由圆弧收窄：half = (half_w − r) + √(r² − t²)

        这是个精确解，不是近似——水平方向的抗锯齿质量就来自这里。
        """
        dy = abs(py - self.center_y)
        if dy >= self.half_h:
            return None

        half = self.half_w
        if self.radius > 0.0 and dy > self.half_h - self.radius:
            t = dy - (self.half_h - self.radius)
            under = self.radius * self.radius - t * t
            half = (self.half_w - self.radius) + (under if under > 0.0 else 0.0) ** 0.5

        if half <= 0.0:
            return None
        return (self.center_x - half, self.center_x + half)


class SoftwareRasterizer(RasterBackend):
    """扫描线光栅器：水平方向解析覆盖，垂直方向 N 点采样。

    `samples` 只影响垂直方向的细腻度（水平永远是精确的）。
    默认 4 已经远超"每像素 16 点子采样"的质量。
    """

    def __init__(
        self,
        *,
        samples: int = 4,
        glyph_provider: GlyphProvider | None = None,
    ) -> None:
        if samples < 1:
            raise ValueError(f"垂直采样数必须 ≥ 1，收到 {samples}")
        self._samples = samples
        # 固定偏移（不用随机数）——确定性全靠这一点
        self._offsets = tuple((j + 0.5) / samples for j in range(samples))
        self._weight = 1.0 / samples
        # 字形来源可替换：默认是内置确定性字形（无字体文件）。
        # 平台字形后端上线后从这里注入，光栅逻辑一行不改。
        self._glyphs: GlyphProvider = (
            glyph_provider if glyph_provider is not None else BuiltinGlyphProvider()
        )

    def rasterize(self, display_list: DisplayList) -> FrameBuffer:
        width, height = display_list.width, display_list.height
        buffer = bytearray(width * height * 4)
        row = [0.0] * width
        for op in display_list.ops:
            self._rasterize_op(buffer, width, height, row, op)
        return FrameBuffer(width, height, buffer)

    # ------------------------------------------------------------ 指令分派

    def _rasterize_op(self, buf: bytearray, w: int, h: int, row: list[float], op: Op) -> None:
        if isinstance(op, FillRectOp):
            self._fill(buf, w, h, row, op.rect, op.color, op.radius, op.clip)
        elif isinstance(op, StrokeRectOp):
            self._stroke(buf, w, h, row, op)
        elif isinstance(op, TextRunOp):
            self._text(buf, w, h, op)

    # ------------------------------------------------------------ 填充

    def _fill(
        self,
        buf: bytearray,
        w: int,
        h: int,
        row: list[float],
        rect: Rect,
        color: Color,
        radius: float,
        clip: Rect | None,
    ) -> None:
        if color.a == 0.0 or rect.width <= 0.0 or rect.height <= 0.0:
            return
        shape = _RoundedBox.of(rect, radius)
        # 注意两个上限各归其位：行范围按**画布高度**夹，列范围按**画布宽度**夹。
        # 传反的后果不是报错而是静默错画——宽扁画布直接崩、竖长画布少画一截。
        y0, y1 = _row_range(rect, clip, h)
        x_lo, x_hi = _column_range(rect, clip, w)

        for y in range(y0, y1):
            touched = False
            for offset in self._offsets:
                span = shape.x_span_at(y + offset)
                if span is not None and _accumulate(row, span, self._weight, x_lo, x_hi):
                    touched = True
            if touched:
                self._paint_row(buf, w, row, y, x_lo, x_hi, color)
                _clear(row, x_lo, x_hi)

    # ------------------------------------------------------------ 描边

    def _stroke(self, buf: bytearray, w: int, h: int, row: list[float], op: StrokeRectOp) -> None:
        """描边 = 外圈覆盖 **减去** 内圈覆盖。

        两条边界各自做解析抗锯齿，所以圆环严丝合缝地贴在轮廓上：
        四角既无缝隙，边缘也平滑。
        """
        rect, width, color = op.rect, op.width, op.color
        if color.a == 0.0 or width <= 0.0 or rect.width <= 0.0 or rect.height <= 0.0:
            return

        outer = _RoundedBox.of(rect, op.radius)
        inner_w, inner_h = rect.width - 2 * width, rect.height - 2 * width
        inner = (
            _RoundedBox.of(
                Rect(rect.left + width, rect.top + width, inner_w, inner_h),
                max(0.0, outer.radius - width),
            )
            if inner_w > 0.0 and inner_h > 0.0
            else None
        )

        y0, y1 = _row_range(rect, op.clip, h)
        x_lo, x_hi = _column_range(rect, op.clip, w)

        for y in range(y0, y1):
            touched = False
            for offset in self._offsets:
                py = y + offset
                span = outer.x_span_at(py)
                if span is not None and _accumulate(row, span, self._weight, x_lo, x_hi):
                    touched = True
                if inner is not None:
                    inner_span = inner.x_span_at(py)
                    if inner_span is not None:
                        _accumulate(row, inner_span, -self._weight, x_lo, x_hi)
            if touched:
                self._paint_row(buf, w, row, y, x_lo, x_hi, color)
                _clear(row, x_lo, x_hi)

    # ------------------------------------------------------------ 文本

    def _text(self, buf: bytearray, w: int, h: int, op: TextRunOp) -> None:
        """绘制一段已定位的文本。

        **位置一律取自 `glyph.x`，不自己累加 advance。**
        这一点很关键：文本层可能为了字距调整、两端对齐、标点悬挂而
        把字形放得比"累加宽度"更远或更近。光栅层如果自作主张按 advance
        重算位置，那些排版决策会被静默丢掉——表现是"排版算对了但画歪了"。
        指令里已经有位置，就只照位置画。

        `advance` 仍然有用（推进笔位置、命中测试），但那是文本层与自己
        的事；光栅层只消费结果。

        字形掩码由 `GlyphProvider` 提供（默认内置确定性字形）。
        `underline` 走复用 `_fill` 画一条细线（IME 组合态下划线用）。
        """
        if op.color.a == 0.0 or not op.glyphs:
            return

        baseline_y = op.origin.dy + op.baseline
        pen_x = op.origin.dx
        last_x = pen_x

        for glyph in op.glyphs:
            glyph_x = op.origin.dx + glyph.x
            # 只对"有实际形状"的字形取掩码：空格没有字形，
            # 但它的 advance 照样推进笔位置（否则词间距会塌掉）
            if glyph.text.strip():
                mask = self._glyphs.mask_for(glyph.text, op.size, glyph.family, glyph.advance)
                self._blit_mask(buf, w, h, mask, glyph_x, baseline_y, op.color, op.clip)
            pen_x = glyph_x + glyph.advance
            last_x = max(last_x, pen_x)

        if op.underline:
            self._underline(buf, w, h, op, last_x, baseline_y)

    def _underline(
        self, buf: bytearray, w: int, h: int, op: TextRunOp, end_x: float, baseline_y: float
    ) -> None:
        """组合态下划线：基线下方 1–2px 的一条细线，覆盖整段 run。"""
        thickness = max(1.0, op.size / 14.0)
        start = op.origin.dx
        if end_x <= start:
            return
        rect = Rect(
            left=start,
            top=baseline_y + max(1.0, op.size * 0.12),
            width=end_x - start,
            height=thickness,
        )
        self._fill(buf, w, h, [0.0] * w, rect, op.color, 0.0, op.clip)

    def _blit_mask(
        self,
        buf: bytearray,
        w: int,
        h: int,
        mask: object,
        pen_x: float,
        baseline_y: float,
        color: Color,
        clip: Rect | None,
    ) -> None:
        """把一个字形掩码按覆盖度 source-over 合成到缓冲区。

        掩码落位是**整数像素**（字形天然对齐像素栅格），
        所以这里不需要抗锯齿采样——抗锯齿信息已经在掩码的覆盖度里了。
        """
        from ..glyphs import GlyphMask

        assert isinstance(mask, GlyphMask)
        rect = rect_of_mask(mask, pen_x, baseline_y)
        left = round(rect.left)
        top = round(rect.top)

        # 裁剪边界：用 ceil 而不是 `int(...) + 1`。
        # 像素 x 覆盖 [x, x+1)。裁剪矩形 `clip` 覆盖 [clip.left, clip.right)。
        # 像素与裁剪相交 ⟺ x < clip.right，所以上界（开区间）应当是
        # `ceil(clip.right)`——当 right 恰好落在整数上时，`int(right)+1`
        # 会多放一个像素进来（在裁剪边界上漏 1px 出去）。
        cx0, cy0 = 0, 0
        cx1, cy1 = w, h
        if clip is not None:
            cx0 = max(cx0, math.ceil(clip.left))
            cy0 = max(cy0, math.ceil(clip.top))
            cx1 = min(cx1, math.ceil(clip.right))
            cy1 = min(cy1, math.ceil(clip.bottom))

        for my in range(mask.height):
            py = top + my
            if py < cy0 or py >= cy1:
                continue
            row_off = my * mask.width
            row_start = max(0, cx0 - left)
            row_end = min(mask.width, cx1 - left)
            if row_start >= row_end:
                continue
            for mx in range(row_start, row_end):
                coverage = mask.coverage[row_off + mx]
                if coverage == 0:
                    continue
                alpha = color.a * (coverage / 255.0)
                if alpha <= 0.0:
                    continue
                _blend_pixel(
                    buf,
                    w,
                    left + mx,
                    py,
                    color if alpha >= 1.0 else color.with_alpha(alpha),
                )

    # ------------------------------------------------------------ 上色

    def _paint_row(
        self,
        buf: bytearray,
        w: int,
        row: list[float],
        y: int,
        x_lo: int,
        x_hi: int,
        color: Color,
    ) -> None:
        base = y * w * 4
        opaque = bytes((color.r, color.g, color.b, 255))
        x = x_lo
        while x < x_hi:
            coverage = row[x]
            if coverage <= 0.0:
                x += 1
                continue
            # 内部整段：一次性切片写入，走 C 速度，Python 循环只跑边缘
            if coverage >= 1.0 and color.a >= 1.0:
                start = x
                while x < x_hi and row[x] >= 1.0:
                    x += 1
                buf[base + start * 4 : base + x * 4] = opaque * (x - start)
                continue
            alpha = color.a * min(coverage, 1.0)
            _blend_pixel(buf, w, x, y, color if alpha >= 1.0 else color.with_alpha(alpha))
            x += 1


# ---------------------------------------------------------------- 行缓冲


def _accumulate(
    row: list[float],
    span: tuple[float, float],
    weight: float,
    x_lo: int,
    x_hi: int,
) -> bool:
    """把一段水平跨度按面积累加进行缓冲。返回是否碰到了任何像素。"""
    left, right = span
    start = max(int(left), x_lo)
    end = min(int(right) + 1, x_hi)
    if start >= end:
        return False
    for x in range(start, end):
        overlap = min(right, x + 1.0) - max(left, float(x))
        if overlap > 0.0:
            row[x] += overlap * weight
    return True


def _clear(row: list[float], x_lo: int, x_hi: int) -> None:
    for x in range(x_lo, x_hi):
        row[x] = 0.0


def _row_range(rect: Rect, clip: Rect | None, h: int) -> tuple[int, int]:
    """本次要扫的像素行区间 `[y0, y1)`。

    像素 y 覆盖 `[y, y+1)`，形状覆盖 `[top, bottom)`（下边界开）：
    相交 ⟺ `y < bottom`，所以上界（开区间）是 `ceil(bottom)`。
    写成 `int(bottom) + 1` 的话，bottom 恰好落在整数上时会多扫一行——
    那一行与形状其实毫无交集，多扫出来的墨迹就是"裁剪边漏 1px"。
    下界用 `floor`（`int`）是对的：`y + 1 > top` 的最小的 y 就是 `floor(top)`。
    """
    top = max(0.0, clip.top if clip else 0.0)
    bottom = min(float(h), clip.bottom if clip else float(h))
    return (
        max(0, min(h, int(max(top, rect.top)))),
        max(0, min(h, math.ceil(min(bottom, rect.bottom)))),
    )


def _column_range(rect: Rect, clip: Rect | None, w: int) -> tuple[int, int]:
    """本次要扫的像素列区间 `[x0, x1)`。语义与 `_row_range` 完全对称。"""
    left = max(0.0, clip.left if clip else 0.0)
    right = min(float(w), clip.right if clip else float(w))
    return (
        max(0, min(w, int(max(left, rect.left)))),
        max(0, min(w, math.ceil(min(right, rect.right)))),
    )


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
