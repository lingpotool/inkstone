"""自研 GL 光栅后端（R8.1 骨架）—— 把显示列表翻译成驱动调用。

它与软件光栅实现**同一个协议**（`raster/base.py`）：

    begin_frame(size, scale) → execute(display_list, clip)* → end_frame() → screenshot()

对 GL 来说这条形状本来就更自然：正常路径是画进 GPU 表面再交换（`end_frame`
交给 `FrameRenderer` 上屏），`execute` 的脏矩形直接落成 `glScissor`，
`screenshot()` 是一次显式的 `glReadPixels` 回读——**异常操作**，不是主路径。

## 这一版做到了什么、没做到什么（诚实）

做到了：帧状态机（顺序用错抛 `RasterError`）、裁剪语义（op.clip 与脏矩形取交、
空交跳过）、圆角半径钳制、文本取掩码与连字/组合符规则（与软件光栅共用
`glyph_mask_plan`）、IME 下划线的几何、纹理资源生命周期、读回校验。
这些**全部可在无显卡的 CI 上测**——注入一个 `GLDriver` 假实现记录调用即可。

没做到：真实 GL 调用。本机与 CI 都没有可用的 GL 上下文，写一份无法验证的
ctypes 绑定是"看起来完成了 80%"，比没有更危险。真机驱动属 R8.2（见 docs/22），
它要做的只是把 `GLDriver` 协议照着填一遍；本模块一行不用改。

路径指令（`PathFillOp` / `PathStrokeOp`）暂未实现，与软件光栅**同等能力**
（软件光栅也抛 `NotImplementedError`）——不让 GL 悄悄比软件后端多会一点，
否则"两个后端画出来的不一样"会在黄金图比对时才暴露。
"""

from __future__ import annotations

import math
from enum import Enum

from ...layout.types import Rect, Size
from ..display_list import (
    DisplayList,
    FillRectOp,
    PathFillOp,
    PathStrokeOp,
    StrokeRectOp,
    TextRunOp,
)
from ..glyphs import BuiltinGlyphProvider, GlyphProvider, glyph_mask_plan
from .base import FrameBuffer, RasterError
from .gl_driver import GLDriver

__all__ = ["GLRasterBackend"]


class _Phase(Enum):
    IDLE = "idle"
    FRAME = "frame"
    ENDED = "ended"


class GLRasterBackend:
    """把显示列表交给 `GLDriver` 的光栅后端。

    `glyph_provider` 决定字形从哪来：默认是内置确定性字形（无头测试与黄金图）；
    真机由 App 注入 `HbFtFontEngine`（它同时是 `MetricsProvider` 与
    `GlyphProvider`），于是"度量与字形同源"在 GL 路径上同样成立。
    """

    def __init__(
        self,
        driver: GLDriver,
        *,
        glyph_provider: GlyphProvider | None = None,
    ) -> None:
        self._driver = driver
        self._glyphs: GlyphProvider = (
            glyph_provider if glyph_provider is not None else BuiltinGlyphProvider()
        )
        self._phase = _Phase.IDLE
        self._width = 0
        self._height = 0
        self._clip: Rect | None = None

    # ------------------------------------------------------------ 帧生命周期

    def begin_frame(self, size: Size, scale: float) -> None:
        """按 `size × scale` 准备帧目标。`size` 是逻辑尺寸，物理 = 逻辑 × scale。"""
        if self._phase is _Phase.FRAME:
            raise RasterError(
                f"begin_frame 时上一帧还没结束（当前处于 {self._phase.value} 阶段）",
                suggestion="检查 begin_frame / end_frame 是否成对；一帧里可以多次 execute",
            )
        if scale <= 0.0:
            raise ValueError(f"设备像素比必须为正，收到 {scale}")
        self._width = max(1, math.ceil(size.width * scale))
        self._height = max(1, math.ceil(size.height * scale))
        self._driver.begin(self._width, self._height, scale)
        self._phase = _Phase.FRAME

    def execute(self, display_list: DisplayList, clip: Rect | None = None) -> None:
        """把显示列表画进当前帧目标。`clip` 是脏矩形（设备像素），None = 整幅。"""
        if self._phase is not _Phase.FRAME:
            raise RasterError(
                f"execute 必须在 begin_frame 之后、end_frame 之前调用"
                f"（当前处于 {self._phase.value} 阶段）",
                suggestion="补一次 begin_frame(Size(...), scale)",
            )
        if display_list.width != self._width or display_list.height != self._height:
            raise ValueError(
                f"显示列表尺寸 {display_list.width}×{display_list.height} "
                f"与帧目标 {self._width}×{self._height} 不一致。"
                f"帧尺寸由 begin_frame(size, scale) 决定，显示列表应当在**设备像素**下录制"
            )
        self._clip = clip
        try:
            for op in display_list.ops:
                self._execute_op(op)
        finally:
            # scissor 是整帧状态，用完复位——下一帧/下一个后端调用者不该继承它
            self._driver.set_scissor(None)
            self._clip = None

    def end_frame(self) -> None:
        if self._phase is not _Phase.FRAME:
            raise RasterError(
                f"end_frame 没有配对的 begin_frame（当前处于 {self._phase.value} 阶段）",
                suggestion="begin_frame / execute* / end_frame 必须按顺序成组",
            )
        self._driver.end()
        self._phase = _Phase.ENDED

    def screenshot(self) -> FrameBuffer:
        """读回像素**拷贝**（异常操作，只用于截图/黄金图）。"""
        if self._phase is not _Phase.ENDED:
            raise RasterError(
                f"screenshot 必须在 end_frame 之后调用（当前处于 {self._phase.value} 阶段）",
                suggestion="正常路径是交换到屏幕；读回只用于截图、黄金图与调试",
            )
        data = self._driver.read_pixels()
        expected = self._width * self._height * 4
        if len(data) != expected:
            raise RasterError(
                f"驱动读回 {len(data)} 字节，应为 {expected}（{self._width}×{self._height}×4）",
                suggestion="检查驱动的 read_pixels 是否按 RGBA 行优先返回整个帧目标",
            )
        return FrameBuffer(self._width, self._height, bytearray(data))

    # ------------------------------------------------------------ 位图资源

    def create_image(self, width: int, height: int, pixels: bytes) -> int:
        return self._driver.create_texture(width, height, pixels)

    def destroy_image(self, handle: int) -> None:
        self._driver.destroy_texture(handle)

    # ------------------------------------------------------------ 指令分派

    def _execute_op(self, op: object) -> None:
        if isinstance(op, FillRectOp):
            if not self._set_scissor(op.clip):
                return
            self._driver.fill_rect(op.rect, _clamp_radius(op.rect, op.radius), op.color)
        elif isinstance(op, StrokeRectOp):
            if not self._set_scissor(op.clip):
                return
            self._driver.stroke_rect(op.rect, _clamp_radius(op.rect, op.radius), op.width, op.color)
        elif isinstance(op, TextRunOp):
            self._execute_text(op)
        elif isinstance(op, (PathFillOp, PathStrokeOp)):
            kind = "PathFillOp" if isinstance(op, PathFillOp) else "PathStrokeOp"
            raise NotImplementedError(
                f"GL 后端暂未实现 {kind}（与软件光栅同等能力，见 docs/22 R8.3）"
            )
        else:
            raise NotImplementedError(f"GL 后端不认识指令 {type(op).__name__}")

    def _execute_text(self, op: TextRunOp) -> None:
        if not op.glyphs:
            return  # 空 run 不产生任何调用（与软件光栅一致）
        if not self._set_scissor(op.clip):
            return
        run_has_ids = any(g.glyph_ids for g in op.glyphs)
        baseline_y = op.origin.dy + op.baseline
        last_x = op.origin.dx
        for glyph in op.glyphs:
            plan = glyph_mask_plan(glyph, run_has_ids=run_has_ids)
            glyph_x = op.origin.dx + glyph.x
            if plan is not None and glyph.text.strip():
                mask = self._glyphs.mask_for(
                    glyph.text,
                    op.size,
                    glyph.family,
                    glyph.advance,
                    plan,
                    face_key=glyph.face_key,
                )
                self._driver.draw_glyph(mask, glyph_x, baseline_y - glyph.y_offset, op.color)
            last_x = max(last_x, glyph_x + glyph.advance)
        if op.underline and last_x > op.origin.dx:
            # IME 组合态下划线：基线下方一条细线，覆盖整段 run（与软件光栅同几何）
            thickness = max(1.0, op.size / 14.0)
            rect = Rect(
                left=op.origin.dx,
                top=baseline_y + max(1.0, op.size * 0.12),
                width=last_x - op.origin.dx,
                height=thickness,
            )
            self._set_scissor(op.clip)
            self._driver.fill_rect(rect, 0.0, op.color)

    # ------------------------------------------------------------ 裁剪

    def _set_scissor(self, op_clip: Rect | None) -> bool:
        """按 op.clip 与脏矩形取交设置 scissor。返回 False = 无交集、跳过本指令。"""
        rect = _intersect_clips(self._clip, op_clip)
        if rect is not None and rect.is_empty:
            return False
        self._driver.set_scissor(rect)
        return True


def _intersect_clips(frame_clip: Rect | None, op_clip: Rect | None) -> Rect | None:
    if frame_clip is None:
        return op_clip
    if op_clip is None:
        return frame_clip
    return frame_clip.intersect(op_clip)


def _clamp_radius(rect: Rect, radius: float) -> float:
    """圆角半径夹到短边一半内——与软件光栅的 `_RoundedBox.of` 同一规则。

    录制层刻意不夹（检查器要看到调用方给的原值），所以每个光栅后端都得夹；
    把规则放在两个后端会漂移，这里保持与软件光栅逐字一致并在测试里对齐。
    """
    if radius <= 0.0:
        return 0.0
    return min(radius, rect.width / 2.0, rect.height / 2.0)
