"""滚动容器：ScrollView。

docs/05 §6 里有一条很容易做错的规则，Scroll 就是它的全部意义所在：

    **Scroll 容器给子级的是无限主轴约束**——这必须显式，
    否则子级的 fill 会爆炸。

也就是说：滚动容器的子级拿到"你想要多长就有多长"的约束，
容器自己则夹在父级给的视口尺寸里。这两件事必须分开，
混在一起就会出现"滚动区域里的 fill 子级把整个窗口撑爆"这种经典 bug。

另外一条取舍：**滚动导致的溢出不算 overflow。**
内容比视口长是设计意图，不是布局错误——所以这里刻意不去调 `note_overflow`，
否则检查器会被滚动区域淹没，真正的问题反而看不见。

状态：已实现（本阶段只做单轴滚动与偏移，滚动条 / 锚点保持 / 过滚动留到 Phase 2）。
"""

from __future__ import annotations

from enum import Enum

from .box import RenderBox
from .protocol import (
    INF,
    Axis,
    constraints_from,
)
from .types import BoxConstraints, Offset, Size

__all__ = ["RenderScroll", "ScrollDirection"]


class ScrollDirection(Enum):
    """滚动方向。`BOTH` 用于表格与代码编辑器。"""

    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    BOTH = "both"


class RenderScroll(RenderBox):
    """单/双向滚动容器。"""

    def __init__(
        self,
        child: RenderBox | None = None,
        *,
        direction: ScrollDirection = ScrollDirection.VERTICAL,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.direction: ScrollDirection = direction
        self._child: RenderBox | None = None
        self._scroll: Offset = Offset(0.0, 0.0)
        self._content_size: Size = Size(0.0, 0.0)
        if child is not None:
            self.child = child

    # ------------------------------------------------------------ 树

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
        self._scroll = Offset(0.0, 0.0)

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return () if self._child is None else (self._child,)

    # ------------------------------------------------------------ 滚动状态

    @property
    def content_size(self) -> Size:
        """内容的完整尺寸（不含视口裁剪）。"""
        return self._content_size

    @property
    def viewport(self) -> Size:
        """视口尺寸，即本节点自身的尺寸。"""
        return self._size

    @property
    def scroll_offset(self) -> Offset:
        return self._scroll

    @property
    def max_scroll(self) -> Offset:
        """各方向能滚动的最大距离。"""
        dx = max(0.0, self._content_size.width - self._size.width)
        dy = max(0.0, self._content_size.height - self._size.height)
        if self.direction is ScrollDirection.VERTICAL:
            dx = 0.0
        elif self.direction is ScrollDirection.HORIZONTAL:
            dy = 0.0
        return Offset(dx, dy)

    @property
    def can_scroll(self) -> bool:
        m = self.max_scroll
        return m.dx > 0.0 or m.dy > 0.0

    def scroll_to(self, dx: float | None = None, dy: float | None = None) -> None:
        """设置滚动偏移，自动夹取到合法范围。

        偏移变了必须标脏：否则下一帧因为"约束没变且不脏"直接走缓存，
        子级位置不会更新，表现就是**滚了但界面不动**。
        """
        limit = self.max_scroll
        new = Offset(
            min(max(dx if dx is not None else self._scroll.dx, 0.0), limit.dx),
            min(max(dy if dy is not None else self._scroll.dy, 0.0), limit.dy),
        )
        if new != self._scroll:
            self._scroll = new
            self.mark_needs_layout()

    def scroll_by(self, dx: float = 0.0, dy: float = 0.0) -> None:
        self.scroll_to(self._scroll.dx + dx, self._scroll.dy + dy)

    # ------------------------------------------------------------ 布局

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        inner = self.content_constraints(constraints)

        # 视口：在父级给的范围内尽量取大
        size = constraints.constrain(
            self.wrap(
                Size(
                    inner.max_width if inner.has_bounded_width else 0.0,
                    inner.max_height if inner.has_bounded_height else 0.0,
                )
            )
        )

        if self._child is None:
            self._content_size = Size(0.0, 0.0)
            return size

        # 关键一步：给子级**无限主轴**约束。
        # 这才是"可滚动"的本质——子级想多长就多长，超出的部分由视口裁剪。
        child_constraints = self._child_constraints(inner)
        self._content_size = self.layout_child(self._child, child_constraints)

        self.scroll_to()
        self.place_child(self._child, Offset(-self._scroll.dx, -self._scroll.dy))
        return size

    def _child_constraints(self, inner: BoxConstraints) -> BoxConstraints:
        """按滚动方向决定哪个轴给子级无限空间。"""
        if self.direction is ScrollDirection.VERTICAL:
            return constraints_from(
                Axis.VERTICAL,
                inner.min_height,
                INF,
                inner.min_width,
                inner.max_width,
            )
        if self.direction is ScrollDirection.HORIZONTAL:
            return constraints_from(
                Axis.HORIZONTAL,
                inner.min_width,
                INF,
                inner.min_height,
                inner.max_height,
            )
        return BoxConstraints(
            inner.min_width,
            INF,
            inner.min_height,
            INF,
        )

    def describe(self) -> str:
        base = super().describe()
        return f"{base} content={self._content_size.width:g}×{self._content_size.height:g}"
