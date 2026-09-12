"""绘制上下文 —— 把绘制意图录成显示列表。

`RenderObject.paint(context)` 拿到的 `context` 就是它（在无头渲染时）。
GL 后端上线后，同一个接口会换成录制 GL 指令的实现，
但组件的 `paint()` 一行都不用改——这就是把绘制抽象成"写入显示列表"的意义。

坐标模型：

    组件在自己的**局部坐标系**里画（左上角永远是 (0,0)）。
    录制器维护**仿射变换栈**，录制时把局部坐标折算成**绝对坐标**存进指令。
    所以光栅端拿到的指令不需要再做任何树遍历，平铺着画就行。

**为什么是仿射矩阵而不是"累计平移"**（R3.3）：平移两个 float 表达不了缩放，
而 125%/150% DPI 缩放（docs/03 §5）与 `motion/` 的缩放动画都要靠它。
数据结构一次按全仿射留够（旋转、切变迟早要来），现阶段只暴露
`translate` 与 `scale` 两个操作。

状态：已实现。
"""

from __future__ import annotations

from ..layout.types import Offset, Rect
from .color import Color
from .display_list import (
    DisplayList,
    FillRectOp,
    Op,
    PositionedGlyph,
    StrokeRectOp,
    TextRunOp,
)
from .transform import IDENTITY, Affine

__all__ = ["DisplayListRecorder"]


class DisplayListRecorder:
    """PaintContext 的无头实现：仿射变换栈 + 裁剪栈 + 指令累积。"""

    def __init__(self) -> None:
        self._ops: list[Op] = []
        self._transform: Affine = IDENTITY
        self._clip: Rect | None = None
        self._save_stack: list[tuple[Affine, Rect | None]] = []

    # ------------------------------------------------------------ 变换 / 状态

    def save(self) -> None:
        """保存当前变换与裁剪，与 restore 配对。"""
        self._save_stack.append((self._transform, self._clip))

    def restore(self) -> None:
        if not self._save_stack:
            raise RuntimeError("restore() 没有配对的 save()——save/restore 必须对称")
        self._transform, self._clip = self._save_stack.pop()

    def translate(self, dx: float, dy: float) -> None:
        """在当前坐标系里平移：后续绘制的坐标都相对新的原点。"""
        self._transform = Affine.translation(dx, dy).then(self._transform)

    def scale(
        self, factor: float, *, sy: float | None = None, origin: Offset | None = None
    ) -> None:
        """在当前坐标系里缩放。`sy` 省略时等比；`origin` 省略时绕局部原点。

        绕**局部原点**缩放是组件的自然语义："把我自己放大一倍"。
        想绕别的点缩放就传 `origin`（比如绕中心），别在外面手拼三步——
        拼错一步就会得到"元素一边缩放一边跑掉"。

        `sy` 是"非等比缩放"的入口（拉伸动画、贴图变形）。它只影响矩形与裁剪；
        线宽、圆角、字形度量在非等比下**不缩放**——那些在非等比下本来就不是
        简单乘一个数能表达的（圆角会变成椭圆角），当前指令形状表达不了。
        与其悄悄画个错的，不如保持原值。
        """
        if factor == 0.0 or (sy is not None and sy == 0.0):
            raise ValueError("缩放比例不能为 0——那会把整个子树压成不可见且不可逆")
        ox = origin.dx if origin is not None else 0.0
        oy = origin.dy if origin is not None else 0.0
        op = Affine.scaling(factor, sy, origin_x=ox, origin_y=oy)
        self._transform = op.then(self._transform)

    @property
    def transform(self) -> Affine:
        """当前变换。检查器与测试用；指令里存的是折算后的绝对坐标。"""
        return self._transform

    def clip_rect(self, rect: Rect) -> None:
        """把裁剪收窄到 rect（局部坐标）。只能收窄，不能扩大。"""
        absolute = self._transform.map_rect(rect)
        self._clip = absolute if self._clip is None else self._clip.intersect(absolute)

    # ------------------------------------------------------------ 绘制

    def fill_rect(self, rect: Rect, color: Color) -> None:
        """实心矩形（局部坐标）。"""
        self._ops.append(FillRectOp(self._map(rect), color, 0.0, self._clip))

    def round_rect(self, rect: Rect, radius: float, color: Color) -> None:
        """圆角矩形。`radius` 由**光栅端**夹在短边的一半以内（见 docs/03）。

        这里不夹：录制层夹的话，检查器看到的半径与调用方给的对不上，
        而"为什么我的 20px 圆角画出来是 10px"会变成一个要翻代码才知道的问题。

        圆角半径是**局部长度**，跟着缩放走——否则放大两倍后圆角看起来会变尖。
        """
        self._ops.append(FillRectOp(self._map(rect), color, self._scale_length(radius), self._clip))

    def stroke_rect(self, rect: Rect, width: float, color: Color, radius: float = 0.0) -> None:
        """描边。线宽向矩形内侧生长。

        线宽与圆角都是局部长度，一起跟着缩放——否则放大两倍后边框细得像没有。
        """
        self._ops.append(
            StrokeRectOp(
                self._map(rect),
                color,
                self._scale_length(width),
                self._scale_length(radius),
                self._clip,
            )
        )

    def text_run(
        self,
        origin: Offset,
        baseline: float,
        glyphs: tuple[PositionedGlyph, ...],
        size: float,
        color: Color,
        *,
        underline: bool = False,
    ) -> None:
        """录制一段已定位的文本（docs/03 的 `text_run`）。

        参数里的 `glyphs` 来自 L3 文本层的整形结果——**字形位置已经算好**，
        录制器不做任何度量、不碰字体。这正是"渲染层不认识字符串"的落地：
        换行、回退、字素簇的知识全留在文本层。

        `origin` 是**局部坐标**（当前变换前），录制时折算成绝对坐标，
        与其它指令一致。

        缩放下的文本：轴对齐等比缩放时，字形的位置、advance、字号与基线
        一起乘那个比例——否则会出现"框放大了、字没放大"。
        非等比或带旋转的文本缩放需要光栅层参与，暂不支持（见 `_text_op`）。
        """
        if not glyphs:
            return  # 空 run 不产生指令：空指令会白白占用一次光栅分派
        scale = self._transform.uniform_scale()
        # 非等比 / 带旋转时 `uniform_scale()` 给 None：字形度量原样传下去。
        # 那是**有意的限制**（见 `_map_glyphs`），不是漏了。
        metrics_scale = 1.0 if scale is None else scale
        self._ops.append(
            TextRunOp(
                origin=self._map_point(origin.dx, origin.dy),
                baseline=baseline * metrics_scale,
                glyphs=self._map_glyphs(glyphs, metrics_scale),
                size=size * metrics_scale,
                color=color,
                underline=underline,
                clip=self._clip,
            )
        )

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
        return self._transform.map_rect(rect)

    def _scale_length(self, value: float) -> float:
        """把"局部长度"（线宽、圆角半径）折算到绝对坐标。

        等比缩放时乘比例；非等比 / 带旋转时原样返回——一个圆角在非等比缩放下
        本来就不是圆角（是椭圆角），而当前指令形状表达不了椭圆角。
        与其悄悄画个错的，不如保持原值（那至少是"没缩放"这个明确的结果）。
        """
        scale = self._transform.uniform_scale()
        if scale is None:
            return value
        return value * scale

    def _map_point(self, x: float, y: float) -> Offset:
        mx, my = self._transform.map_point(x, y)
        return Offset(mx, my)

    def _map_glyphs(
        self, glyphs: tuple[PositionedGlyph, ...], scale: float
    ) -> tuple[PositionedGlyph, ...]:
        """按缩放比例折算字形的**度量**。

        只乘比例、不加平移：`glyph.x` 是相对 run 原点的偏移，
        而 run 原点已经在 `text_run` 里折算过绝对坐标了。这里再加一次平移
        就是重复计算——表现是"字整体被推到了画布外面"。

        等比缩放时位置、advance、y_offset、em 全部乘同一个比例，
        字号由 `size` 承担（光栅端据此取掩码），所以字形会真的变大，
        而不是被拉伸成一个形状不对的胖字。

        非等比 / 带旋转时调用方传 `scale=1.0`：真正做对需要光栅层在采样时
        变换掩码（那才是"斜着画字"的正解）。明确不支持好过半个实现对某些输入
        悄悄画错。
        """
        if scale == 1.0:
            return glyphs
        return tuple(
            PositionedGlyph(
                text=g.text,
                x=g.x * scale,
                advance=g.advance * scale,
                family=g.family,
                em=g.em * scale,
                y_offset=g.y_offset * scale,
                face_key=g.face_key,
            )
            for g in glyphs
        )
