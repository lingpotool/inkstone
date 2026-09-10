"""布局协议层：轴、对齐、尺寸模式、以及"能读懂的"布局错误。

这一层与 `types.py` 一样是纯数据层：**只有枚举、不可变数据类和异常，没有副作用、
不依赖任何后端**。把它单独拎出来，是为了让上层（Flex / Stack / 组件）共享同一套
词汇表——否则"主轴"在 Row 里叫 width、在 Column 里叫 height，很快就会写出两套代码。

术语约定（全局统一，别处不得再发明）：

    主轴 main       Row 的水平方向 / Column 的垂直方向
    交叉轴 cross    与主轴垂直的那个方向
    近端 near       主轴起点（Row 的左 / Column 的上）
    远端 far        主轴终点（Row 的右 / Column 的下）

状态：已实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .types import INF, BoxConstraints, Offset, Size

__all__ = [
    "ALIGN_BOTTOM_CENTER",
    "ALIGN_BOTTOM_LEFT",
    "ALIGN_BOTTOM_RIGHT",
    "ALIGN_CENTER",
    "ALIGN_CENTER_LEFT",
    "ALIGN_CENTER_RIGHT",
    "ALIGN_TOP_CENTER",
    "ALIGN_TOP_LEFT",
    "ALIGN_TOP_RIGHT",
    "INF",  # 从 types 再导出：上层判断"约束是否有界"时离不开它
    "Alignment",
    "Axis",
    "CrossAxisAlignment",
    "FlexFit",
    "LayoutError",
    "MainAxisAlignment",
    "MainAxisSize",
    "Sizing",
    "SizingKind",
    "StackFit",
    "align_offset",
    "constraints_from",
    "cross_extents",
    "cross_of",
    "main_extents",
    "main_of",
    "resolve_sizing",
    "size_from",
]


class Axis(Enum):
    """布局轴。HORIZONTAL 用于 Row，VERTICAL 用于 Column。"""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"

    @property
    def flipped(self) -> Axis:
        return Axis.VERTICAL if self is Axis.HORIZONTAL else Axis.HORIZONTAL

    @property
    def label(self) -> str:
        """中文标签，用于错误信息。"""
        return "宽度" if self is Axis.HORIZONTAL else "高度"


class MainAxisAlignment(Enum):
    """主轴对齐：决定剩余空间如何分配到子级之间。"""

    START = "start"
    CENTER = "center"
    END = "end"
    SPACE_BETWEEN = "space_between"
    SPACE_AROUND = "space_around"
    SPACE_EVENLY = "space_evenly"


class CrossAxisAlignment(Enum):
    """交叉轴对齐。

    STRETCH 会把子级在交叉轴上"拉满"，BASELINE 则按文字基线对齐——
    表单里「标签与输入框看起来齐」靠的就是它。
    """

    START = "start"
    CENTER = "center"
    END = "end"
    STRETCH = "stretch"
    BASELINE = "baseline"


class MainAxisSize(Enum):
    """容器自身在主轴上占多大：MIN = 恰好包住内容，MAX = 吃满父级给的上限。"""

    MIN = "min"
    MAX = "max"


class StackFit(Enum):
    """Stack 传给非定位子级的约束强度。"""

    LOOSE = "loose"  # 放宽到 0..max，子级按内容取尺寸
    EXPAND = "expand"  # 收紧到 max，子级被拉满
    PASSTHROUGH = "passthrough"  # 原样透传父级约束


class FlexFit(Enum):
    """flex 子级如何使用分到的空间。

    LOOSE：分到的是"上限"，子级可以只用一部分（Flexible 的默认行为）。
    TIGHT：分到的是"确切值"，子级必须占满（Expanded 的行为）。
    """

    LOOSE = "loose"
    TIGHT = "tight"


class SizingKind(Enum):
    """一个盒子在某个轴上如何决定自己的尺寸。"""

    CONTENT = "content"  # 按内容
    FIXED = "fixed"  # 固定值
    FILL = "fill"  # 吃满可用上限（在 Flex 里等价于带权重的 flex）


@dataclass(frozen=True, slots=True)
class Sizing:
    """单轴尺寸模式。`value` 在 FIXED 下是像素值，在 FILL 下是 flex 权重。"""

    kind: SizingKind = SizingKind.CONTENT
    value: float = 1.0

    def __post_init__(self) -> None:
        if self.kind is SizingKind.FIXED and self.value < 0.0:
            raise ValueError(f"fixed 尺寸不能为负，收到 {self.value}")
        if self.kind is SizingKind.FILL and self.value <= 0.0:
            raise ValueError(f"fill 权重必须为正，收到 {self.value}")

    @classmethod
    def content(cls) -> Sizing:
        return cls(SizingKind.CONTENT, 0.0)

    @classmethod
    def fixed(cls, value: float) -> Sizing:
        return cls(SizingKind.FIXED, value)

    @classmethod
    def fill(cls, weight: float = 1.0) -> Sizing:
        return cls(SizingKind.FILL, weight)

    @property
    def is_flexible(self) -> bool:
        """该模式是否需要在 Flex 里参与剩余空间分配。"""
        return self.kind is SizingKind.FILL

    def __str__(self) -> str:
        if self.kind is SizingKind.CONTENT:
            return "content"
        if self.kind is SizingKind.FIXED:
            return f"{self.value:g}px"
        return f"fill({self.value:g})"


@dataclass(frozen=True, slots=True)
class Alignment:
    """九宫格对齐。`x`/`y` 取 -1（近端）/ 0（居中）/ 1（远端），也允许中间值。"""

    x: float = 0.0
    y: float = 0.0

    def lerp_to(self, other: Alignment, t: float) -> Alignment:
        return Alignment(
            self.x + (other.x - self.x) * t,
            self.y + (other.y - self.y) * t,
        )


ALIGN_TOP_LEFT = Alignment(-1.0, -1.0)
ALIGN_TOP_CENTER = Alignment(0.0, -1.0)
ALIGN_TOP_RIGHT = Alignment(1.0, -1.0)
ALIGN_CENTER_LEFT = Alignment(-1.0, 0.0)
ALIGN_CENTER = Alignment(0.0, 0.0)
ALIGN_CENTER_RIGHT = Alignment(1.0, 0.0)
ALIGN_BOTTOM_LEFT = Alignment(-1.0, 1.0)
ALIGN_BOTTOM_CENTER = Alignment(0.0, 1.0)
ALIGN_BOTTOM_RIGHT = Alignment(1.0, 1.0)


class LayoutError(RuntimeError):
    """布局错误。

    玩具库布局错了是"界面花了"，企业库布局错了是**一条能读懂的错误**：

        LayoutError: Row 的 fill 子节点遇到无限宽度约束
          路径: App > Column[2] > Card > Row[0]
          建议: 给 Row 一个有限宽度，或把 fill 换成固定宽度

    `path` 由 RenderBox 的 debug 路径生成，`suggestion` 给出可执行的下一步。
    """

    def __init__(self, message: str, *, path: str = "", suggestion: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.suggestion = suggestion

    def __str__(self) -> str:
        lines = [f"LayoutError: {self.message}"]
        if self.path:
            lines.append(f"  路径: {self.path}")
        if self.suggestion:
            lines.append(f"  建议: {self.suggestion}")
        return "\n".join(lines)


# ---------------------------------------------------------------- 轴换算

# 下面这组函数是 Flex / Stack 唯一的"轴翻译入口"。
# 有了它们，Row 与 Column 可以共用同一份算法，不必写两遍。


def main_of(size: Size, axis: Axis) -> float:
    return size.width if axis is Axis.HORIZONTAL else size.height


def cross_of(size: Size, axis: Axis) -> float:
    return size.height if axis is Axis.HORIZONTAL else size.width


def size_from(axis: Axis, main: float, cross: float) -> Size:
    return Size(main, cross) if axis is Axis.HORIZONTAL else Size(cross, main)


def main_extents(constraints: BoxConstraints, axis: Axis) -> tuple[float, float]:
    """返回主轴上的 (min, max)。"""
    if axis is Axis.HORIZONTAL:
        return constraints.min_width, constraints.max_width
    return constraints.min_height, constraints.max_height


def cross_extents(constraints: BoxConstraints, axis: Axis) -> tuple[float, float]:
    """返回交叉轴上的 (min, max)。"""
    if axis is Axis.HORIZONTAL:
        return constraints.min_height, constraints.max_height
    return constraints.min_width, constraints.max_width


def constraints_from(
    axis: Axis,
    main_min: float,
    main_max: float,
    cross_min: float,
    cross_max: float,
) -> BoxConstraints:
    """按给定轴拼一组约束——避免每个调用点手写 if horizontal。"""
    if axis is Axis.HORIZONTAL:
        return BoxConstraints(main_min, main_max, cross_min, cross_max)
    return BoxConstraints(cross_min, cross_max, main_min, main_max)


def resolve_sizing(
    sizing: Sizing,
    content: float,
    lower: float,
    upper: float,
    *,
    axis: Axis,
    path: str = "",
) -> float:
    """把一个 Sizing 解析成具体像素值，并夹进 [lower, upper]。

    FILL 遇到无上界约束时抛 LayoutError —— 这是文档里"无限约束诊断"的核心规则：
    **报错并给出路径与建议，而不是渲染一个 0 或无穷大。**
    """
    if sizing.kind is SizingKind.FIXED:
        return min(max(sizing.value, lower), upper)

    if sizing.kind is SizingKind.FILL:
        if upper >= INF:
            raise LayoutError(
                f"{axis.label}为 fill 的盒子遇到无限{axis.label}约束，无法确定尺寸",
                path=path,
                suggestion=f"给它的父级一个有限的{axis.label}，或把 fill 换成固定尺寸",
            )
        return upper

    return min(max(content, lower), upper)


def align_offset(alignment: Alignment, container: Size, child: Size) -> Offset:
    """把 child 按 alignment 摆进 container，返回左上角偏移。"""
    return Offset(
        (container.width - child.width) * (alignment.x + 1.0) / 2.0,
        (container.height - child.height) * (alignment.y + 1.0) / 2.0,
    )
