"""布局引擎（L5）：约束向下、尺寸向上，纯 Python 且可无窗口测试。

组成：

    types      不可变几何原语（Offset/Size/Rect/EdgeInsets/BoxConstraints）
    protocol   轴、对齐、尺寸模式、带路径的 LayoutError
    box        盒子模型 RenderBox（约束 / padding / 脏标记 / 溢出 / 基线）
    flex       Row / Column 共用的弹性算法
    stack      Stack / Positioned / Align

一句话记住用法：

    ```python
    row = RenderRow(gap=8, align=CrossAxisAlignment.CENTER, debug_name="Row")
    row.add(RenderSized(width=Sizing.fixed(40), height=Sizing.fixed(20)))
    row.add(RenderSized(), flex=1)          # 吃掉剩余宽度
    row.layout(BoxConstraints(max_width=300))
    ```
"""

from .box import RenderBox, RenderContainer, RenderSized, collect_descendants
from .flex import (
    FlexItem,
    RenderColumn,
    RenderFlex,
    RenderRow,
    distribute_cross,
    distribute_main,
)
from .protocol import (
    ALIGN_BOTTOM_CENTER,
    ALIGN_BOTTOM_LEFT,
    ALIGN_BOTTOM_RIGHT,
    ALIGN_CENTER,
    ALIGN_CENTER_LEFT,
    ALIGN_CENTER_RIGHT,
    ALIGN_TOP_CENTER,
    ALIGN_TOP_LEFT,
    ALIGN_TOP_RIGHT,
    Alignment,
    Axis,
    CrossAxisAlignment,
    FlexFit,
    LayoutError,
    MainAxisAlignment,
    MainAxisSize,
    Sizing,
    SizingKind,
    StackFit,
    align_offset,
    constraints_from,
    cross_extents,
    cross_of,
    main_extents,
    main_of,
    resolve_sizing,
    size_from,
)
from .stack import PositionedSpec, RenderAlign, RenderStack, StackItem
from .types import (
    INF,
    BoxConstraints,
    EdgeInsets,
    Offset,
    Rect,
    Size,
)

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
    # 几何原语
    "INF",
    "Alignment",
    # 协议
    "Axis",
    "BoxConstraints",
    "CrossAxisAlignment",
    "EdgeInsets",
    "FlexFit",
    # Flex
    "FlexItem",
    "LayoutError",
    "MainAxisAlignment",
    "MainAxisSize",
    "Offset",
    # Stack
    "PositionedSpec",
    "Rect",
    "RenderAlign",
    # 盒子模型
    "RenderBox",
    "RenderColumn",
    "RenderContainer",
    "RenderFlex",
    "RenderRow",
    "RenderSized",
    "RenderStack",
    "Size",
    "Sizing",
    "SizingKind",
    "StackFit",
    "StackItem",
    "align_offset",
    "collect_descendants",
    "constraints_from",
    "cross_extents",
    "cross_of",
    "distribute_cross",
    "distribute_main",
    "main_extents",
    "main_of",
    "resolve_sizing",
    "size_from",
]
