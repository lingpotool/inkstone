"""绘制上下文 —— 把绘制意图录成显示列表。

`RenderObject.paint(context)` 拿到的 `context` 就是它（在无头渲染时）。
GL 后端上线后，同一个接口会换成录制 GL 指令的实现，
但组件的 `paint()` 一行都不用改——这就是把绘制抽象成"写入显示列表"的意义。

坐标模型：

    组件在自己的**局部坐标系**里画（左上角永远是 (0,0)）。
    录制器维护平移栈，录制时把局部坐标折算成**绝对坐标**存进指令。
    所以光栅端拿到的指令不需要再做任何树遍历，平铺着画就行。

状态：已实现。
"""

from __future__ import annotations

from ..layout.types import Rect
from .color import Color
from .display_list import DisplayList, FillRectOp, Op, StrokeRectOp

__all__ = ["DisplayListRecorder"]


class DisplayListRecorder:
    """PaintContext 的无头实现：平移栈 + 裁剪栈 + 指令累积。"""

    def __init__(self) -> None:
        self._ops: list[Op] = []
        self._ox = 0.0
        self._oy = 0.0
        self._clip: Rect | None = None
        self._save_stack: list[tuple[float, float, Rect | None]] = []

    # ------------------------------------------------------------ 变换 / 状态

    def save(self) -> None:
        """保存当前平移与裁剪，与 restore 配对。"""
        self._save_stack.append((self._ox, self._oy, self._clip))

    def restore(self) -> None:
        if not self._save_stack:
            raise RuntimeError("restore() 没有配对的 save()——save/restore 必须对称")
        self._ox, self._oy, self._clip = self._save_stack.pop()

    def translate(self, dx: float, dy: float) -> None:
        self._ox += dx
        self._oy += dy

    def clip_rect(self, rect: Rect) -> None:
        """把裁剪收窄到 rect（局部坐标）。只能收窄，不能扩大。"""
        absolute = Rect(rect.left + self._ox, rect.top + self._oy, rect.width, rect.height)
        self._clip = absolute if self._clip is None else self._clip.intersect(absolute)

    # ------------------------------------------------------------ 绘制

    def fill_rect(self, rect: Rect, color: Color) -> None:
        """实心矩形（局部坐标）。"""
        self._ops.append(FillRectOp(self._map(rect), color, 0.0, self._clip))

    def round_rect(self, rect: Rect, radius: float, color: Color) -> None:
        """圆角矩形。`radius` 会被夹在短边的一半以内。"""
        self._ops.append(FillRectOp(self._map(rect), color, radius, self._clip))

    def stroke_rect(self, rect: Rect, width: float, color: Color, radius: float = 0.0) -> None:
        """描边。线宽向矩形内侧生长。"""
        self._ops.append(StrokeRectOp(self._map(rect), color, width, radius, self._clip))

    # ------------------------------------------------------------ 收尾

    @property
    def op_count(self) -> int:
        return len(self._ops)

    def finish(self, width: int, height: int) -> DisplayList:
        """结束录制，产出不可变的显示列表。"""
        if self._save_stack:
            raise RuntimeError(f"还有 {len(self._save_stack)} 个 save() 没有配对的 restore()")
        if width <= 0 or height <= 0:
            raise ValueError(f"画布尺寸必须为正，收到 {width}×{height}")
        return DisplayList(width, height, tuple(self._ops))

    # ------------------------------------------------------------ 内部

    def _map(self, rect: Rect) -> Rect:
        return Rect(rect.left + self._ox, rect.top + self._oy, rect.width, rect.height)
