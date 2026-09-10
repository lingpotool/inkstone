"""显示列表 —— 与后端无关的绘制指令序列。

一帧渲染的中间产物，也是"黄金图测试"的物质基础：

    RenderObject.paint() → DisplayList（指令）→ RasterBackend → 像素

**为什么是显示列表而不是直接画**：同样的指令序列，GL 后端拿去上显卡，
软件光栅拿去画进内存缓冲区，检查器拿去序列化成文本——三者互不干扰。
它就是渲染管线的"中间表示"。

文档里那句"显示列表是**回放与比对的单位**"在这里落地：
指令全部是不可变数据类，坐标在录制时已经折算成绝对坐标，
所以 `dl1 == dl2` 就是逐指令逐坐标比对——确定性由此而来。

v0 的指令集刻意收敛：矩形填充 / 圆角填充 / 矩形描边 + 裁剪。
阴影、渐变、路径、文本属后续阶段（docs/03 的完整指令表）。

状态：已实现（v0 指令集）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from ..layout.types import Rect
from .color import Color

__all__ = [
    "DisplayList",
    "FillRectOp",
    "Op",
    "StrokeRectOp",
]


@dataclass(frozen=True, slots=True)
class FillRectOp:
    """实心矩形填充；`radius > 0` 时是圆角矩形。"""

    rect: Rect
    color: Color
    radius: float = 0.0
    clip: Rect | None = None


@dataclass(frozen=True, slots=True)
class StrokeRectOp:
    """矩形描边（边框）。`width` 为线宽，向矩形内侧生长。"""

    rect: Rect
    color: Color
    width: float
    radius: float = 0.0
    clip: Rect | None = None


# 指令联合类型。新增指令时只扩这里，光栅端同步加一个分支。
Op = Union[FillRectOp, StrokeRectOp]  # noqa: UP007


@dataclass(frozen=True, slots=True)
class DisplayList:
    """一帧的完整绘制指令 + 画布尺寸。"""

    width: int
    height: int
    ops: tuple[Op, ...]

    def __len__(self) -> int:
        return len(self.ops)

    def describe(self) -> str:
        """检查器用的可读文本。确定性调试从这里来。"""
        lines = [f"DisplayList({self.width}×{self.height}, {len(self.ops)} ops)"]
        for index, op in enumerate(self.ops):
            lines.append(f"  [{index}] {_describe_op(op)}")
        return "\n".join(lines)


def _describe_op(op: Op) -> str:
    if isinstance(op, FillRectOp):
        radius = f" r={op.radius:g}" if op.radius > 0 else ""
        clip = f" clip={_r(op.clip)}" if op.clip else ""
        return f"fill {_r(op.rect)}{radius} {op.color}{clip}"
    return (
        f"stroke {_r(op.rect)} w={op.width:g}"
        f"{' r=' + format(op.radius, 'g') if op.radius > 0 else ''} {op.color}"
        + (f" clip={_r(op.clip)}" if op.clip else "")
    )


def _r(rect: Rect | None) -> str:
    if rect is None:
        return "none"
    return f"({rect.left:g},{rect.top:g} {rect.width:g}×{rect.height:g})"
