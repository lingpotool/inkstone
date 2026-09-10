"""盒子模型：RenderBox —— 布局引擎的节点基类。

整个布局引擎只有一条铁律：

    **父级给约束，子级选尺寸，父级定位置。**
    子级不许读父级的几何，父级不许改子级的尺寸——只有"约束"这一条通道。

这条纪律换来的是布局永远收敛、永远可预测、永远可以在没有窗口的 CI 里跑完整棵树。

关于分层：RenderBox 放在 `layout` 而不是 `core`，是因为它**纯粹是布局概念**
（约束、尺寸、padding、溢出），不涉及状态与绘制。core 的 RenderObject 会在它之上
叠加 paint / semantics 关注点，依赖方向是 core → layout，不反向。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Sequence

from .protocol import (
    INF,
    Axis,
    LayoutError,
    Sizing,
    constraints_from,
    main_of,
    resolve_sizing,
)
from .types import BoxConstraints, EdgeInsets, Offset, Rect, Size

__all__ = ["RenderBox", "RenderContainer", "RenderSized"]


class RenderBox:
    """布局树上的节点。

    生命周期只有两步：父级调用 `layout(constraints)`，节点在 `perform_layout` 里
    给子级派发约束、收集尺寸、再给子级定位置。整个过程不读时钟、不碰后端。

    子类只需实现 `perform_layout(constraints) -> Size`。
    """

    def __init__(
        self,
        *,
        padding: EdgeInsets | None = None,
        margin: EdgeInsets | None = None,
        debug_name: str | None = None,
    ) -> None:
        # ---- 盒子模型数据（公开可变，改了记得 mark_needs_layout） ----
        self.padding: EdgeInsets = padding if padding is not None else EdgeInsets()
        self.margin: EdgeInsets = margin if margin is not None else EdgeInsets()
        self.debug_name: str = debug_name if debug_name is not None else type(self).__name__

        # ---- 布局产物（布局后只读） ----
        self._parent: RenderBox | None = None
        self._constraints: BoxConstraints | None = None
        self._size: Size = Size(0.0, 0.0)
        self._offset: Offset = Offset(0.0, 0.0)
        self._baseline: float | None = None
        self._overflow: float = 0.0
        self._needs_layout: bool = True
        self._needs_paint: bool = True

    # ------------------------------------------------------------ 树

    @property
    def parent(self) -> RenderBox | None:
        return self._parent

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return ()

    def adopt(self, child: RenderBox) -> None:
        """把 child 挂到本节点下（只负责认亲，不负责布局）。"""
        if child._parent is not None:
            raise LayoutError(
                f"{child.debug_name} 已经有一个父级（{child._parent.debug_name}），不能重复挂载",
                path=child.debug_path,
                suggestion="先从原父级 remove，或检查是否误把同一个节点加了两次",
            )
        child._parent = self
        self.mark_needs_layout()

    def orphan(self, child: RenderBox) -> None:
        if child._parent is self:
            child._parent = None
            self.mark_needs_layout()

    # ------------------------------------------------------------ 脏标记

    @property
    def needs_layout(self) -> bool:
        return self._needs_layout

    def mark_needs_layout(self) -> None:
        """标脏并向上冒泡。

        子级尺寸可能变，父级就必须重排——所以脏标记一定是向上传播的。
        已经脏的节点直接返回，避免同一帧里的重复遍历。
        """
        if self._needs_layout:
            return
        self._needs_layout = True
        if self._parent is not None:
            self._parent.mark_needs_layout()

    # ------------------------------------------------------------ 布局

    @property
    def size(self) -> Size:
        return self._size

    @property
    def constraints(self) -> BoxConstraints | None:
        return self._constraints

    @property
    def offset(self) -> Offset:
        """相对父级内容区左上角的位置。"""
        return self._offset

    @property
    def rect(self) -> Rect:
        return Rect.from_offset_size(self._offset, self._size)

    @property
    def baseline(self) -> float | None:
        """本节点顶边到基线的距离；没有基线的节点返回 None。"""
        return self._baseline

    @property
    def overflow(self) -> float:
        """内容超出本节点的量（0 表示没超）。由父级/自身在布局时写入。"""
        return self._overflow

    @property
    def has_overflow(self) -> bool:
        return self._overflow > 0.0

    def layout(self, constraints: BoxConstraints) -> Size:
        """布局入口。带缓存：约束没变且不脏，直接复用上次的尺寸。"""
        if not self._needs_layout and self._constraints == constraints:
            return self._size
        self._overflow = 0.0
        self._baseline = None
        self._constraints = constraints
        self._size = self.perform_layout(constraints)
        self._needs_layout = False
        return self._size

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 perform_layout")

    def layout_child(self, child: RenderBox, constraints: BoxConstraints) -> Size:
        """给子级派发约束并取回尺寸。"""
        if child._parent is not self:
            raise LayoutError(
                f"{child.debug_name} 不是 {self.debug_name} 的子级，不能由它布局",
                path=self.debug_path,
                suggestion="检查是否忘了把节点加进父级",
            )
        return child.layout(constraints)

    def measure_unbounded(self, axis: Axis, cross_extent: float = INF) -> float:
        """沿 `axis` 试算"最大内容尺寸"（Grid 的 auto 轨道靠它定尺寸）。

        做法是用无上界约束真实地布局一次，然后**把节点重新标脏**——
        因为这次结果只是测量值，不是最终几何，最终那一趟会重新布局它。

        代价是被测量的节点多走一次布局。Grid 只在 auto 轨道上用它，且只对
        "恰好占 1 格"的子级测量，所以开销可控。
        """
        size = self.layout(constraints_from(axis, 0.0, INF, 0.0, cross_extent))
        self.mark_needs_layout()
        return main_of(size, axis)

    def place_child(self, child: RenderBox, offset: Offset) -> None:
        """给子级定位置。位置相对本节点的**内容区**左上角。"""
        if child._parent is not self:
            raise LayoutError(
                f"{child.debug_name} 不是 {self.debug_name} 的子级，不能由它定位",
                path=self.debug_path,
                suggestion="检查是否忘了把节点加进父级",
            )
        child._offset = offset

    # ------------------------------------------------------------ 盒子模型辅助

    def content_constraints(self, constraints: BoxConstraints) -> BoxConstraints:
        """扣掉 padding 后留给内容区的约束。"""
        if self.padding.horizontal == 0.0 and self.padding.vertical == 0.0:
            return constraints
        return constraints.deflate(self.padding)

    def wrap(self, content: Size) -> Size:
        """内容尺寸加上 padding 得到外框尺寸。"""
        return self.padding.inflate_size(content)

    def content_rect(self) -> Rect:
        """内容区相对本节点左上角的矩形。"""
        return self.padding.deflate_rect(Rect(0.0, 0.0, self._size.width, self._size.height))

    def outer_size(self, child: RenderBox) -> Size:
        """子级连 margin 一起的外廓尺寸——父级按它来算占位。"""
        return child.margin.inflate_size(child.size)

    def note_overflow(self, amount: float) -> None:
        """记录溢出量。宁可标记出来给检查器看，也不静默裁切。"""
        if amount > self._overflow:
            self._overflow = amount

    # ------------------------------------------------------------ 绘制脏标记

    # 为什么绘制脏标记也在 RenderBox 上，而不单独开一个类：
    # 布局与绘制是**同一个节点**的两件事（Flutter 亦如此）。拆成两个类只会带来
    # "每个容器都要再写一个组合类"的排列爆炸，却换不到任何实际收益。
    # 这一层只提供标记与遍历，`paint` 本身是空钩子，真正的绘制由 gfx 层实现。

    @property
    def needs_paint(self) -> bool:
        return self._needs_paint

    def mark_needs_paint(self) -> None:
        """标脏并向上冒泡。

        冒泡让根节点能 O(1) 判断"这棵树有没有要重绘的"，
        从而整棵跳过干净的子树——1000 节点的界面里，多数帧只脏几个节点。
        """
        if self._needs_paint:
            return
        self._needs_paint = True
        if self._parent is not None:
            self._parent.mark_needs_paint()

    def clear_needs_paint(self) -> None:
        self._needs_paint = False

    def paint(self, context: object) -> None:
        """绘制自身。默认什么都不画；由 gfx 层或具体节点覆写。

        `context` 的具体类型由 gfx 层定义，这一层不解释它——
        布局引擎不该知道画布长什么样。
        """

    def paint_tree(self, context: object) -> None:
        """绘制整棵脏子树，干净子树整棵跳过。"""
        if not self._needs_paint:
            return
        self.paint(context)
        self._needs_paint = False
        for child in self.children:
            child.paint_tree(context)

    # ------------------------------------------------------------ 调试

    def _child_index(self, child: RenderBox) -> int:
        for i, c in enumerate(self.children):
            if c is child:
                return i
        return -1

    @property
    def debug_path(self) -> str:
        """从根到本节点的路径，如 `App > Column[2] > Card > Row[0]`。"""
        parts: list[str] = []
        node: RenderBox | None = self
        while node is not None:
            parent = node._parent
            if parent is not None:
                parts.append(f"{node.debug_name}[{parent._child_index(node)}]")
            else:
                parts.append(node.debug_name)
            node = parent
        return " > ".join(reversed(parts))

    def describe(self) -> str:
        """检查器用的一行摘要。"""
        c = self._constraints
        constraint_text = (
            "未布局"
            if c is None
            else f"{c.smallest.width:g}×{c.smallest.height:g}..{c.biggest.width:g}×{c.biggest.height:g}"
        )
        overflow_text = f" overflow={self._overflow:g}" if self.has_overflow else ""
        return (
            f"{self.debug_name} size={self._size.width:g}×{self._size.height:g} "
            f"offset=({self._offset.dx:g},{self._offset.dy:g}) "
            f"constraints={constraint_text}{overflow_text}"
        )

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} {self.debug_name} {self._size.width:g}×{self._size.height:g}>"
        )


class RenderSized(RenderBox):
    """按 Sizing 决定自身尺寸的叶子盒子。

    没有子级、没有内容，所以 CONTENT 模式下内容尺寸为 0。
    它是测试的标尺，也是 SizedBox / Spacer 这类控件的地基。
    """

    def __init__(
        self,
        *,
        width: Sizing | None = None,
        height: Sizing | None = None,
        baseline: float | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.width_sizing: Sizing = width if width is not None else Sizing.content()
        self.height_sizing: Sizing = height if height is not None else Sizing.content()
        self._explicit_baseline = baseline

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        path = self.debug_path
        width = resolve_sizing(
            self.width_sizing,
            0.0,
            constraints.min_width,
            constraints.max_width,
            axis=Axis.HORIZONTAL,
            path=path,
        )
        height = resolve_sizing(
            self.height_sizing,
            0.0,
            constraints.min_height,
            constraints.max_height,
            axis=Axis.VERTICAL,
            path=path,
        )
        self._baseline = self._explicit_baseline
        return Size(width, height)


class RenderContainer(RenderBox):
    """通用单子容器：盒子模型 + padding + 尺寸模式。

    Card / Box / Panel 这类"包一层"的控件都落在它身上。
    它把「扣 padding → 量子级 → 加回 padding → 夹进父约束」这条主干走通，
    多子容器（Flex / Stack）复用同样的辅助方法。
    """

    def __init__(
        self,
        child: RenderBox | None = None,
        *,
        width: Sizing | None = None,
        height: Sizing | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.width_sizing: Sizing = width if width is not None else Sizing.content()
        self.height_sizing: Sizing = height if height is not None else Sizing.content()
        self._child: RenderBox | None = None
        if child is not None:
            self.child = child

    @property
    def child(self) -> RenderBox | None:
        return self._child

    @child.setter
    def child(self, value: RenderBox | None) -> None:
        if self._child is not None:
            self.orphan(self._child)
        self._child = value
        if value is not None:
            self.adopt(value)

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return () if self._child is None else (self._child,)

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        inner = self.content_constraints(constraints)
        path = self.debug_path

        content = Size(0.0, 0.0)
        if self._child is not None:
            content = self.layout_child(self._child, inner)

        # 尺寸模式作用于**外框**（border-box 语义，与 Flutter Container 一致）：
        # width=Sizing.fixed(160) 且 padding=12 时，总宽就是 160，内容区只有 136。
        # 若把 fixed 解释成内容宽度，padding 会把盒子撑大——那不是任何人想要的。
        width = resolve_sizing(
            self.width_sizing,
            content.width + self.padding.horizontal,
            constraints.min_width,
            constraints.max_width,
            axis=Axis.HORIZONTAL,
            path=path,
        )
        height = resolve_sizing(
            self.height_sizing,
            content.height + self.padding.vertical,
            constraints.min_height,
            constraints.max_height,
            axis=Axis.VERTICAL,
            path=path,
        )

        size = constraints.constrain(Size(width, height))

        if self._child is not None:
            # 子级恒摆放在内容区左上角（对齐由外层容器负责），保证结果确定。
            self.place_child(self._child, Offset(self.padding.left, self.padding.top))

            remaining = self.padding.deflate_rect(Rect(0.0, 0.0, size.width, size.height))
            if content.width > remaining.width or content.height > remaining.height:
                self.note_overflow(
                    max(content.width - remaining.width, content.height - remaining.height)
                )

            if self._child.baseline is not None:
                self._baseline = self.padding.top + self._child.baseline

        return size


def collect_descendants(root: RenderBox) -> Sequence[RenderBox]:
    """前序遍历整棵子树，供测试与检查器使用。"""
    out: list[RenderBox] = []
    stack: list[RenderBox] = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(node.children))
    return out
