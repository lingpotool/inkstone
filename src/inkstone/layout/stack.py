"""层叠布局：Stack / Positioned / Align。

Stack 是角标、悬浮按钮、骨架屏、渐变遮罩的地基——凡是"两个东西叠在一起"的需求都归它。

两类子级：

    **非定位子级**：按 `alignment`（九宫格）摆放，参与决定 Stack 自身尺寸。
    **定位子级**：带 `PositionedSpec`，用 left/top/right/bottom/width/height 相对 Stack 边缘定位，
                 **不参与决定尺寸**（这是刻意的：否则"角标"会把卡片撑大）。

六个参数的解析规则和 CSS 的绝对定位一致，但**不允许过度约束**：
给了 left + right + width 时，left 与 width 生效、right 被忽略，而不是抛异常——
因为定位参数经常来自布局计算，严格的报错会让动态布局写起来很痛苦。

状态：已实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from .box import RenderBox
from .protocol import (
    ALIGN_TOP_LEFT,
    INF,
    Alignment,
    LayoutError,
    StackFit,
    align_offset,
)
from .types import BoxConstraints, Offset, Rect, Size

__all__ = ["PositionedSpec", "RenderAlign", "RenderStack", "StackItem"]


@dataclass(frozen=True, slots=True)
class PositionedSpec:
    """相对 Stack 边缘的定位参数。None 表示"未指定"。"""

    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    width: float | None = None
    height: float | None = None

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and value < 0.0:
                raise ValueError(f"{name} 不能为负，收到 {value}")

    @property
    def is_positioned(self) -> bool:
        return any(
            v is not None
            for v in (self.left, self.top, self.right, self.bottom, self.width, self.height)
        )


@dataclass(frozen=True, slots=True)
class StackItem:
    child: RenderBox
    spec: PositionedSpec | None = None


class RenderStack(RenderBox):
    """层叠容器。"""

    def __init__(
        self,
        *,
        alignment: Alignment = ALIGN_TOP_LEFT,
        fit: StackFit = StackFit.LOOSE,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.alignment: Alignment = alignment
        self.fit: StackFit = fit
        self._items: list[StackItem] = []

    # ------------------------------------------------------------ 树

    @property
    def items(self) -> tuple[StackItem, ...]:
        return tuple(self._items)

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return tuple(item.child for item in self._items)

    def add(self, child: RenderBox, spec: PositionedSpec | None = None) -> StackItem:
        item = StackItem(child=child, spec=spec)
        self.adopt(child)
        self._items.append(item)
        return item

    def add_positioned(
        self,
        child: RenderBox,
        *,
        left: float | None = None,
        top: float | None = None,
        right: float | None = None,
        bottom: float | None = None,
        width: float | None = None,
        height: float | None = None,
    ) -> StackItem:
        return self.add(
            child,
            PositionedSpec(
                left=left, top=top, right=right, bottom=bottom, width=width, height=height
            ),
        )

    def remove(self, child: RenderBox) -> None:
        for i, item in enumerate(self._items):
            if item.child is child:
                self._items.pop(i)
                self.orphan(child)
                return
        raise LayoutError(
            f"{child.debug_name} 不在 {self.debug_name} 里",
            path=self.debug_path,
            suggestion="检查 remove 的目标是否正确",
        )

    def _child_index(self, child: RenderBox) -> int:
        for i, item in enumerate(self._items):
            if item.child is child:
                return i
        return -1

    # ------------------------------------------------------------ 布局

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        inner = self.content_constraints(constraints)

        # ---- 非定位子级：决定 Stack 自身尺寸 ----
        content = Size(inner.min_width, inner.min_height)
        for item in self._items:
            if item.spec is not None and item.spec.is_positioned:
                continue
            size = self.layout_child(item.child, self._child_constraints(inner))
            content = Size(
                max(content.width, size.width + item.child.margin.horizontal),
                max(content.height, size.height + item.child.margin.vertical),
            )

        if self.fit is StackFit.EXPAND:
            if inner.has_bounded_width:
                content = Size(inner.max_width, content.height)
            if inner.has_bounded_height:
                content = Size(content.width, inner.max_height)

        outer = constraints.constrain(self.wrap(content))
        actual = self.padding.deflate_rect(Rect(0.0, 0.0, outer.width, outer.height))
        origin = actual.top_left
        container = actual.size

        # ---- 定位子级：尺寸依赖 Stack 的最终大小，所以放在最后 ----
        for item in self._items:
            if item.spec is None or not item.spec.is_positioned:
                continue
            child_constraints = self._positioned_constraints(item.spec, container)
            self.layout_child(item.child, child_constraints)

        # ---- 统一定位 ----
        for item in self._items:
            child = item.child
            child_size = child.size
            if item.spec is not None and item.spec.is_positioned:
                offset = self._resolve_position(item.spec, container, child_size)
            else:
                base = align_offset(self.alignment, container, child_size)
                offset = Offset(
                    base.dx + child.margin.left,
                    base.dy + child.margin.top,
                )
            self.place_child(child, Offset(origin.dx + offset.dx, origin.dy + offset.dy))

            if (
                offset.dx + child_size.width > container.width
                or offset.dy + child_size.height > container.height
            ):
                self.note_overflow(
                    max(
                        offset.dx + child_size.width - container.width,
                        offset.dy + child_size.height - container.height,
                    )
                )

        return outer

    # ------------------------------------------------------------ 内部辅助

    def _child_constraints(self, inner: BoxConstraints) -> BoxConstraints:
        if self.fit is StackFit.PASSTHROUGH:
            return inner
        if self.fit is StackFit.EXPAND:
            if inner.has_bounded_width and inner.has_bounded_height:
                return BoxConstraints.tight(Size(inner.max_width, inner.max_height))
            return inner
        return inner.loosen()

    def _positioned_constraints(self, spec: PositionedSpec, container: Size) -> BoxConstraints:
        width_min, width_max = self._axis_extent(spec.left, spec.right, spec.width, container.width)
        height_min, height_max = self._axis_extent(
            spec.top, spec.bottom, spec.height, container.height
        )
        return BoxConstraints(width_min, width_max, height_min, height_max)

    @staticmethod
    def _axis_extent(
        near: float | None,
        far: float | None,
        extent: float | None,
        container: float,
    ) -> tuple[float, float]:
        """由 (近端, 远端, 长度) 三选二推出该轴的约束。返回 (min, max)。"""
        if extent is not None:
            return extent, extent
        available = container - (near or 0.0) - (far or 0.0)
        available = max(0.0, available)
        if near is not None and far is not None:
            return available, available
        if container >= INF:
            return 0.0, INF
        return 0.0, available

    @staticmethod
    def _resolve_axis(
        near: float | None,
        far: float | None,
        child_extent: float,
        container: float,
        alignment: float,
    ) -> float:
        """定位已确定尺寸的子级在该轴上的坐标。"""
        if near is not None:
            return near
        if far is not None:
            return container - far - child_extent
        return (container - child_extent) * (alignment + 1.0) / 2.0

    def _resolve_position(self, spec: PositionedSpec, container: Size, child_size: Size) -> Offset:
        return Offset(
            self._resolve_axis(
                spec.left, spec.right, child_size.width, container.width, self.alignment.x
            ),
            self._resolve_axis(
                spec.top, spec.bottom, child_size.height, container.height, self.alignment.y
            ),
        )


class RenderAlign(RenderBox):
    """把单个子级按 Alignment 摆在自己内部的容器。

    Stack 处理"多个"，Align 处理"一个"——后者更轻，也更容易在检查器里读懂。
    """

    def __init__(
        self,
        child: RenderBox | None = None,
        *,
        alignment: Alignment = ALIGN_TOP_LEFT,
        width_factor: float | None = None,
        height_factor: float | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.alignment: Alignment = alignment
        self.width_factor = width_factor
        self.height_factor = height_factor
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
        if self._child is None:
            return constraints.constrain(
                self.wrap(Size(max(inner.min_width, 0.0), max(inner.min_height, 0.0)))
            )

        child_size = self.layout_child(self._child, inner.loosen())

        # 语义与 Flutter 的 Align 一致：给了 factor 就按子级的倍数，
        # 没给且父级有上界就撑满，父级无上界则贴合子级。
        if self.width_factor is not None:
            width = child_size.width * self.width_factor
        elif inner.has_bounded_width:
            width = inner.max_width
        else:
            width = child_size.width

        if self.height_factor is not None:
            height = child_size.height * self.height_factor
        elif inner.has_bounded_height:
            height = inner.max_height
        else:
            height = child_size.height

        size = constraints.constrain(self.wrap(Size(width, height)))
        actual = self.padding.deflate_rect(Rect(0.0, 0.0, size.width, size.height))
        base = align_offset(self.alignment, actual.size, child_size)
        self.place_child(
            self._child,
            Offset(
                actual.left + base.dx + self._child.margin.left,
                actual.top + base.dy + self._child.margin.top,
            ),
        )
        return size
