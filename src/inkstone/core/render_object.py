"""RenderObject —— 三棵树里的"楼"。

一个重要的分层决定：**RenderObject 不重新发明任何几何逻辑。**
它直接继承 `layout.RenderBox`，复用布局引擎的全部能力（约束、padding、
脏标记、溢出、基线），本模块只做两件事：

1. 声明 `PaintContext` —— 绘制上下文的契约，具体实现由 gfx 层给。
2. 提供 `RenderObject` —— 自定义渲染对象该继承的基类。

为什么绘制脏标记在 `layout.RenderBox` 上而不在这里：
布局与绘制是同一个节点的两件事。如果拆成两个类，那每个布局容器
（Flex / Grid / Stack / Scroll）都得再配一个"RenderBox + RenderObject"的组合类，
纯属排列爆炸。所以脏标记统一挂在 RenderBox，`paint` 在那里是空钩子。

状态：已实现（paint 仍为空实现，待 gfx 填充）。
"""

from __future__ import annotations

from typing import Protocol

from ..layout import RenderBox

__all__ = ["PaintContext", "RenderObject"]


class PaintContext(Protocol):
    """绘制上下文的契约。

    当前是空协议：gfx 层会提供真正的实现（显示列表 / 画布）。
    core 只依赖这个抽象，不依赖任何具体光栅后端，
    所以光栅后端可以换（自研 GL → Skia）而不动 core 一行。
    """


class RenderObject(RenderBox):
    """自定义渲染对象的基类。

    写控件时继承它，实现 `perform_layout`（尺寸规则）与 `paint`（外观）。
    几何、脏标记、溢出这些统统不用自己管——`layout.RenderBox` 已经做好了。
    """
