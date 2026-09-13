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
from enum import Enum

from ...layout.types import Rect, Size
from ..color import Color
from ..display_list import (
    DisplayList,
    FillRectOp,
    Op,
    PathFillOp,
    PathStrokeOp,
    StrokeRectOp,
    TextRunOp,
    resolve_state_ops,
)
from ..glyphs import BuiltinGlyphProvider, GlyphProvider, glyph_mask_plan, rect_of_mask
from .base import FrameBuffer, RasterBackend, RasterError

__all__ = ["SoftwareRasterizer", "encode_png"]


class _FramePhase(Enum):
    """帧生命周期所处的位置。顺序错了就响亮地失败，不猜。"""

    IDLE = "idle"
    FRAME = "frame"
    ENDED = "ended"


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

    实现的是 docs/03 §2 的帧生命周期（R3.1 重塑）：
    `begin_frame → execute → end_frame → screenshot`。
    帧缓冲归后端所有，跨 `execute` 调用**保持内容**——这样 `clip` 才能真正
    当脏矩形用（区域外保留上一帧）。想整幅重画就传 `clip=None`。
    """

    def __init__(
        self,
        *,
        samples: int = 4,
        glyph_provider: GlyphProvider | None = None,
        subpixel_glyphs: bool = False,
        fast_paths: bool = True,
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
        # 字形落位是否保留亚像素相位（见 `_blit_mask` 的说明）。
        # **默认关**，因为默认的字形源是位图掩码：它本来就按像素栅格生成，
        # 用小数相位去重采样只会让笔画变软（实测：text_block 5.6% 的像素变化，
        # 其中 2799 个是"变亮"，也就是笔画被摊到邻像素上）。
        # R4 换上 FreeType 的**轮廓**掩码后，掩码本身按相位生成、不会变软，
        # 那时把它打开才是收益。
        self._subpixel_glyphs = subpixel_glyphs
        # 快路径开关（R3.5）。默认开；关掉是为了**证明快慢两条路径等价**
        # （测试里逐像素比对），也方便在"某张图变了"时二分定位是不是快路径的锅。
        self._fast_paths = fast_paths
        # ---- 帧状态 ----
        self._phase = _FramePhase.IDLE
        self._width = 0
        self._height = 0
        self._buffer: bytearray | None = None

    # ------------------------------------------------------------ 帧生命周期

    def begin_frame(self, size: Size, scale: float) -> None:
        """按 `size × scale` 准备帧缓冲。

        尺寸没变时**复用**上一帧的缓冲（不清空）——脏矩形的语义就建立在这上面：
        `execute` 只覆盖 `clip` 内的像素，区域外必须留着上一帧的内容。

        上一帧已经 `end_frame` 过（或还没开始）都是合法的；只有"上一帧没结束"
        才报错——那才是真的漏了配对。
        """
        if self._phase is _FramePhase.FRAME:
            raise RasterError(
                f"begin_frame 时上一帧还没结束（当前处于 {self._phase.value} 阶段）",
                suggestion="检查 begin_frame / end_frame 是否成对；一帧里可以多次 execute",
            )
        if scale <= 0.0:
            raise ValueError(f"设备像素比必须为正，收到 {scale}")
        width = max(1, math.ceil(size.width * scale))
        height = max(1, math.ceil(size.height * scale))
        if self._buffer is None or self._width != width or self._height != height:
            self._buffer = bytearray(width * height * 4)
        self._width = width
        self._height = height
        self._phase = _FramePhase.FRAME

    def execute(self, display_list: DisplayList, clip: Rect | None = None) -> None:
        """把显示列表画进当前帧缓冲，只更新 `clip` 区域。

        内部先做**全量**光栅（进一块临时缓冲），再按 `clip` 合成进帧缓冲。
        这样"裁剪语义"是对的，代价是暂时没有省下计算量——快路径见 R3.5，
        真正的脏矩形增量光栅留给后续（它要的是显示列表分段，不是重绘策略）。
        """
        if self._phase is not _FramePhase.FRAME:
            raise RasterError(
                f"execute 必须在 begin_frame 之后、end_frame 之前调用"
                f"（当前处于 {self._phase.value} 阶段）",
                suggestion="补一次 begin_frame(Size(...), scale)",
            )
        if display_list.width != self._width or display_list.height != self._height:
            raise ValueError(
                f"显示列表尺寸 {display_list.width}×{display_list.height} "
                f"与帧缓冲 {self._width}×{self._height} 不一致。"
                f"帧尺寸由 begin_frame(size, scale) 决定，显示列表应当在**设备像素**下录制"
                f"（recorder.finish 也要用同一组数字）"
            )
        assert self._buffer is not None

        frame = bytearray(len(self._buffer))
        row = [0.0] * self._width
        for op in resolve_state_ops(display_list.ops):
            self._rasterize_op(frame, self._width, self._height, row, op)
        _composite(self._buffer, frame, self._width, self._height, clip)

    def end_frame(self) -> None:
        if self._phase is not _FramePhase.FRAME:
            raise RasterError(
                f"end_frame 没有配对的 begin_frame（当前处于 {self._phase.value} 阶段）",
                suggestion="begin_frame / execute* / end_frame 必须按顺序成组",
            )
        self._phase = _FramePhase.ENDED

    def screenshot(self) -> FrameBuffer:
        """读回像素**拷贝**（帧缓冲仍归后端所有，改拷贝不影响下一帧）。"""
        if self._phase is not _FramePhase.ENDED or self._buffer is None:
            raise RasterError(
                f"screenshot 必须在 end_frame 之后调用（当前处于 {self._phase.value} 阶段）",
                suggestion="正常路径是交换到屏幕；读回只用于截图、黄金图与调试",
            )
        return FrameBuffer(self._width, self._height, bytearray(self._buffer))

    # ------------------------------------------------------------ 位图资源

    def create_image(self, width: int, height: int, pixels: bytes) -> int:
        raise NotImplementedError(
            "软件光栅暂未实现位图上传。协议里留着这两个方法，是因为 GL/Skia 的纹理"
            "资源不在 Python 的 GC 管辖内、没有显式释放就会稳定泄漏；"
            "软件端等「位图与图标」落地时再实现（那之前没有消费者）"
        )

    def destroy_image(self, handle: int) -> None:
        raise NotImplementedError("软件光栅暂未实现位图上传，见 create_image 的说明")

    # ------------------------------------------------------------ 指令分派

    def _rasterize_op(self, buf: bytearray, w: int, h: int, row: list[float], op: Op) -> None:
        if isinstance(op, FillRectOp):
            self._fill(buf, w, h, row, op.rect, op.color, op.radius, op.clip)
        elif isinstance(op, StrokeRectOp):
            self._stroke(buf, w, h, row, op)
        elif isinstance(op, TextRunOp):
            self._text(buf, w, h, op)
        elif isinstance(op, (PathFillOp, PathStrokeOp)):
            # **响亮地未实现**，不静默跳过。
            # 静默跳过会让"图标没画出来"变成一个要查半天的问题，
            # 而这里一句话就能说清是"还没做"。
            kind = "PathFillOp" if isinstance(op, PathFillOp) else "PathStrokeOp"
            raise NotImplementedError(
                f"软件光栅还没有实现路径指令（{kind}）。路径的**数据形状**已定"
                f"（display_list.py 的扁平 verb 数组），光栅实现随 `icons/stroke.py`"
                f"（docs/13 承诺的 SVG 图标）一起做——那之前没有消费者。"
            )
        else:  # pragma: no cover - 新增指令类型时这里会拦住
            raise TypeError(f"未知的绘制指令：{type(op).__name__}")

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
        # 快路径：不透明 + 直角 + **像素对齐** → 整行切片直通（R3.5）。
        #
        # "像素对齐"是判据里最关键的一条：边缘落在像素中间时那一列/行需要
        # 部分覆盖，而切片赋值给不出部分覆盖。对齐时两条路径的输出
        # **逐比特相同**，所以这不是"为了快牺牲正确"，而是
        # "对齐时没必要做逐像素的垂直采样"。
        if (
            self._fast_paths
            and radius <= 0.0
            and color.a >= 1.0
            and _fill_aligned_opaque(buf, w, h, rect, color, clip)
        ):
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
        # 整个 run 有没有字形 id：有就说明字形源是整形器（HB+FT），
        # 于是"空 id"有确定含义——被前面的连字覆盖了。见 `_glyph_plan`。
        run_has_ids = any(g.glyph_ids for g in op.glyphs)

        for glyph in op.glyphs:
            glyph_x = op.origin.dx + glyph.x
            # 每个字形自己的基线：y_offset 向上为正，屏幕坐标向下为正
            glyph_baseline = baseline_y - glyph.y_offset
            plan = glyph_mask_plan(glyph, run_has_ids=run_has_ids)
            # 只对"有实际形状"的字形取掩码：空格没有字形，
            # 但它的 advance 照样推进笔位置（否则词间距会塌掉）
            if plan is not None and glyph.text.strip():
                mask = self._glyphs.mask_for(
                    glyph.text,
                    op.size,
                    glyph.family,
                    glyph.advance,
                    plan,
                    face_key=glyph.face_key,
                )
                self._blit_mask(buf, w, h, mask, glyph_x, glyph_baseline, op.color, op.clip)
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

        **掩码落位**有两种口径，由 `subpixel_glyphs` 选（R3.2）：

        - 关（默认）：把原点 `round()` 到整数像素。这是**位图掩码**的正确选择
          ——掩码本来就是按像素栅格生成的，再拿小数相位去重采样只会把笔画摊薄。
        - 开：保留小数相位，按双线性权重把每个掩码像素的覆盖度摊到相邻 4 个
          目标像素上。这是**轮廓掩码**（FreeType/真实字体）的正确选择：掩码按
          相位生成、不会变软，而笔位置的小数部分不再被丢弃。

        为什么需要"不丢弃相位"这条路：笔位置几乎从来不是整数——字距调整、居中、
        两端对齐、标点悬挂都会产生小数。落位时 `round()` 等于把文本层算出来的
        排版精度在最后一步扔掉，表现是"整行字一会儿挤一会儿松"，只在某些字号下可见。

        实测（内置位图字形、text_block 黄金图）：打开亚像素后 5.6% 的像素变化，
        其中 2799 个"变亮"——笔画被摊到邻像素，文字整体变软。这就是它默认关掉的
        原因：对位图源是倒退，对轮廓源才是收益（R4 落地时打开）。
        """
        from ..glyphs import GlyphMask

        assert isinstance(mask, GlyphMask)
        rect = rect_of_mask(mask, pen_x, baseline_y)
        if self._subpixel_glyphs:
            base_x = math.floor(rect.left)
            base_y = math.floor(rect.top)
            fx = rect.left - base_x
            fy = rect.top - base_y
        else:
            base_x = round(rect.left)
            base_y = round(rect.top)
            fx = fy = 0.0
        wx = (1.0 - fx, fx)
        wy = (1.0 - fy, fy)

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
            row_off = my * mask.width
            for mx in range(mask.width):
                coverage = mask.coverage[row_off + mx]
                if coverage == 0:
                    continue
                alpha = color.a * (coverage / 255.0)
                if alpha <= 0.0:
                    continue
                for dy in (0, 1):
                    weight_y = wy[dy]
                    if weight_y <= 0.0:
                        continue
                    py = base_y + my + dy
                    if py < cy0 or py >= cy1:
                        continue
                    for dx in (0, 1):
                        weight_x = wx[dx]
                        if weight_x <= 0.0:
                            continue
                        px = base_x + mx + dx
                        if px < cx0 or px >= cx1:
                            continue
                        sample = alpha * weight_x * weight_y
                        if sample <= 0.0:
                            continue
                        _blend_pixel(
                            buf, w, px, py, color if sample >= 1.0 else color.with_alpha(sample)
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
    """把行缓冲的一段清零。切片赋值走 C 速度，比逐元素循环快一个量级。"""
    if x_hi > x_lo:
        row[x_lo:x_hi] = _ZEROS[: x_hi - x_lo]


#: 供 `_clear` 切片用的零值池（比每次 `[0.0] * n` 少一次分配）
_ZEROS: list[float] = [0.0] * 4096


def _fill_aligned_opaque(
    buf: bytearray, w: int, h: int, rect: Rect, color: Color, clip: Rect | None
) -> bool:
    """不透明直角矩形且**边缘落在整数像素上**时，整行切片直通。

    返回 `True` 表示"已经处理完了"（包括"完全在画布外，什么都不用做"），
    调用方不必再走通用路径；返回 `False` 表示对齐条件不满足，请走通用路径。

    为什么值得单独开这条路：1280×800 的全屏填充在通用路径下要跑
    80 万次"逐像素垂直采样"，实测 749ms（预算 14ms/帧）。
    对齐时每一行的覆盖度**恒为 1.0**，那 80 万次采样全是白算的。

    为什么要求**精确**对齐（不用容差）：容差会让"10.0000001 走快路径、
    10.0 走通用路径"这种边界上的两张图差一个像素的覆盖度。
    精确判据下快慢两条路径的输出逐比特相同，快路径永远可以关掉而不改变画面。
    """
    left = max(rect.left, clip.left if clip is not None else 0.0, 0.0)
    right = min(rect.right, clip.right if clip is not None else float(w), float(w))
    top = max(rect.top, clip.top if clip is not None else 0.0, 0.0)
    bottom = min(rect.bottom, clip.bottom if clip is not None else float(h), float(h))
    if right <= left or bottom <= top:
        return True  # 完全在画布/裁剪区外
    for value in (left, right, top, bottom):
        if value != math.floor(value):
            return False
    x0, x1 = int(left), int(right)
    y0, y1 = int(top), int(bottom)
    span = bytes((color.r, color.g, color.b, 255)) * (x1 - x0)
    width_bytes = (x1 - x0) * 4
    for y in range(y0, y1):
        start = (y * w + x0) * 4
        buf[start : start + width_bytes] = span
    return True


def _composite(dst: bytearray, src: bytearray, width: int, height: int, clip: Rect | None) -> None:
    """把 `src` 的 `clip` 区域**整块覆盖**到 `dst`。

    是覆盖（replace）而不是 alpha 合成：`src` 已经是这一帧的完整光栅结果，
    再合一次会让半透明像素叠两层。区域外一个字节都不动——那正是脏矩形的意义。

    裁剪边界用 `floor(left)` / `ceil(right)`：部分覆盖的像素也算在内（保守取大），
    与形状裁剪那边的"半开区间"口径不同，因为这里问的是"哪些像素需要被更新"，
    而不是"哪些像素与形状有交集"。
    """
    if clip is None:
        dst[:] = src
        return
    x0 = max(0, math.floor(clip.left))
    x1 = min(width, math.ceil(clip.right))
    y0 = max(0, math.floor(clip.top))
    y1 = min(height, math.ceil(clip.bottom))
    if x0 >= x1 or y0 >= y1:
        return
    for y in range(y0, y1):
        start = (y * width + x0) * 4
        end = (y * width + x1) * 4
        dst[start:end] = src[start:end]


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
