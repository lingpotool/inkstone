"""仿射变换 —— 录制器的坐标系。

**为什么需要它**：录制器原先只有"累计平移"（两个 float），整个 gfx 层没有
缩放的概念。于是两件事无处可挂：

    - 125% / 150% DPI 缩放（docs/03 §5 有整节承诺）；
    - `motion/` 的缩放动画。

**为什么是 2×3 全仿射而不是"平移 + 缩放两个字段"**：旋转、切变迟早要来
（图标旋转、斜切强调），而把数据结构一次留够比事后改显示列表的形状便宜得多。
现阶段只**暴露** `translate` 与 `scale` 两个操作（旋转暂缓），但内部按全仿射算。

**为什么是不可变数据类**：显示列表的整条纪律是"同样的输入 → 同样的指令"。
变换是录制过程的一部分，它必须可比较、可复制、可序列化——用可变对象
（比如一个 3×3 列表）会让"为什么这两次录制不一样"变成一个没有答案的问题。

矩阵约定（与 SVG / CSS 的 `matrix(a,b,c,d,tx,ty)` 一致）：

    | a  c  tx |        x' = a·x + c·y + tx
    | b  d  ty |        y' = b·x + d·y + ty
    | 0  0  1  |

状态：已实现（R3.3）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..layout.types import Rect

__all__ = ["IDENTITY", "Affine"]


@dataclass(frozen=True, slots=True)
class Affine:
    """一个 2×3 仿射变换。不可变、可比较、可序列化。"""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    # ------------------------------------------------------------ 构造

    @classmethod
    def translation(cls, dx: float, dy: float) -> Affine:
        return cls(tx=dx, ty=dy)

    @classmethod
    def scaling(
        cls, sx: float, sy: float | None = None, *, origin_x: float = 0.0, origin_y: float = 0.0
    ) -> Affine:
        """绕 `(origin_x, origin_y)` 缩放。`sy` 省略时等比。

        绕原点缩放写成"先平移到原点、再缩放、再平移回去"，
        这样调用方不必自己拼三步——拼错了会得到"元素一边缩放一边跑掉"。
        """
        sy = sx if sy is None else sy
        return cls(
            a=sx,
            d=sy,
            tx=origin_x - sx * origin_x,
            ty=origin_y - sy * origin_y,
        )

    # ------------------------------------------------------------ 组合

    def then(self, outer: Affine) -> Affine:
        """先应用本变换，再应用 `outer`（即 `outer ∘ self`）。

        录制器的用法是 `current = Affine.translation(...).then(current)`：
        "后续绘制都相对新的原点"——新操作套在已有变换的**外面**。
        """
        return Affine(
            a=outer.a * self.a + outer.c * self.b,
            b=outer.b * self.a + outer.d * self.b,
            c=outer.a * self.c + outer.c * self.d,
            d=outer.b * self.c + outer.d * self.d,
            tx=outer.a * self.tx + outer.c * self.ty + outer.tx,
            ty=outer.b * self.tx + outer.d * self.ty + outer.ty,
        )

    # ------------------------------------------------------------ 查询

    @property
    def is_identity(self) -> bool:
        return self == IDENTITY

    def uniform_scale(self) -> float | None:
        """轴对齐等比缩放时返回那个比例，否则 `None`。

        文本要用它：字形的位置、advance、字号在缩放后都要跟着变，
        而"跟着变"只在等比且无旋转时才是"乘一个数"这么简单。
        非等比 / 带旋转的文本缩放需要光栅层参与，暂不支持（见 `map_rect` 的说明）。
        """
        if self.b == 0.0 and self.c == 0.0 and self.a == self.d:
            return self.a
        return None

    # ------------------------------------------------------------ 应用

    def map_point(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty)

    def map_rect(self, rect: Rect) -> Rect:
        """把矩形映射成**包围盒**。

        全仿射下矩形的像一般不是轴对齐矩形（旋转就是最明显的例子），
        所以这里取四角的包围盒——它是唯一诚实的答案。
        平移与轴对齐缩放下，包围盒恰好就是精确的像，不引入误差。
        """
        x0, y0 = self.map_point(rect.left, rect.top)
        x1, y1 = self.map_point(rect.right, rect.top)
        x2, y2 = self.map_point(rect.left, rect.bottom)
        x3, y3 = self.map_point(rect.right, rect.bottom)
        left, right = min(x0, x1, x2, x3), max(x0, x1, x2, x3)
        top, bottom = min(y0, y1, y2, y3), max(y0, y1, y2, y3)
        return Rect(left, top, right - left, bottom - top)

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        """`(a, b, c, d, tx, ty)` —— 序列化与快照比对用。"""
        return (self.a, self.b, self.c, self.d, self.tx, self.ty)


IDENTITY = Affine()
