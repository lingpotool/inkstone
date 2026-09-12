"""弹性布局：Row / Column 共用的一份算法。

**为什么 Row 和 Column 只有一份实现？**
因为所有几何都先换算到 (main, cross) 这对抽象轴上算完，最后一步才翻译回 (x, y)。
写两遍的结果一定是"Row 有某个 bug，Column 没有"，或者更糟——两边都有，但不一样。

算法分四趟，与 Flutter 的 RenderFlex 同构：

    1. 非 flex 子级：主轴给到上限，按内容取尺寸，累加已分配量
    2. flex 子级：把剩余空间按权重切分；**余数全部给最后一个 flex 子级**，
       这样"3 个 flex 分 100px"的结果在每一次运行中都一模一样（确定性）
    3. 收缩：主轴溢出时，按 shrink 权重收缩。默认 shrink=0，
       也就是"宁可溢出报出来，也不悄悄把文字压扁"（见 docs/05 §4）
    4. 定位：主轴按 justify 分配剩余空间，交叉轴按 align 对齐

关于 fill 的正确用法：
    `row.add(child, flex=1)` 才表示"参与剩余空间分配"。
    子级自己的 `Sizing.fill()` 只在**非 flex 父级**（如 Column 里控制宽度）下生效；
    在 Flex 主轴上想撑满，请用 flex 权重——这是显式优于隐式。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .box import RenderBox
from .protocol import (
    INF,
    Axis,
    CrossAxisAlignment,
    FlexFit,
    LayoutError,
    MainAxisAlignment,
    MainAxisSize,
    constraints_from,
    cross_extents,
    cross_of,
    main_extents,
    main_of,
    size_from,
)
from .types import BoxConstraints, Offset, Rect, Size

__all__ = [
    "FlexItem",
    "RenderColumn",
    "RenderFlex",
    "RenderRow",
    "distribute_cross",
    "distribute_main",
]


@dataclass(frozen=True, slots=True)
class FlexItem:
    """一个子级在 Flex 里的位置参数。

    flex 与 shrink 都是**权重**而非像素：实际分到多少，取决于同轴的兄弟节点。
    """

    child: RenderBox
    flex: int = 0  # >0 表示参与剩余空间分配
    shrink: int = 0  # >0 表示空间不足时允许被压缩
    # 默认 TIGHT：说"给它 flex 权重"时，人的意图几乎总是"吃掉分到的份额"。
    # 想让子级只用一部分（Flutter 的 Flexible），显式传 fit=FlexFit.LOOSE。
    fit: FlexFit = FlexFit.TIGHT
    align: CrossAxisAlignment | None = None  # 覆盖容器的 align


def distribute_main(
    sizes: Sequence[float],
    container: float,
    justify: MainAxisAlignment,
    gap: float = 0.0,
) -> list[float]:
    """把 `sizes` 沿主轴摆进长度 `container` 的容器，返回各自的近端坐标。

    纯函数，无副作用——这是布局引擎"100% 无窗口可测"的基本盘。
    """
    n = len(sizes)
    if n == 0:
        return []

    total = sum(sizes) + gap * (n - 1)
    free = container - total

    leading = 0.0
    between = gap

    if justify is MainAxisAlignment.CENTER:
        leading = free / 2.0
    elif justify is MainAxisAlignment.END:
        leading = free
    elif justify is MainAxisAlignment.SPACE_BETWEEN:
        leading = 0.0
        between = gap + (free / (n - 1) if n > 1 else 0.0)
    elif justify is MainAxisAlignment.SPACE_AROUND:
        slot = free / n
        leading = slot / 2.0
        between = gap + slot
    elif justify is MainAxisAlignment.SPACE_EVENLY:
        slot = free / (n + 1)
        leading = slot
        between = gap + slot

    positions: list[float] = []
    pos = leading
    for size in sizes:
        positions.append(pos)
        pos += size + between
    return positions


def distribute_cross(
    extents: Sequence[float],
    container: float,
    align: CrossAxisAlignment,
    baselines: Sequence[float | None] | None = None,
) -> list[float]:
    """把 `extents` 沿交叉轴摆进长度 `container` 的容器，返回各自的近端坐标。

    BASELINE 按"每个子级顶边到其基线的距离"对齐：所有子级的基线落在同一条线上。
    没有基线的子级按 0 处理（顶边贴到公共基线），这样混排也不会崩。
    """
    if align is CrossAxisAlignment.BASELINE:
        if not baselines:
            return [0.0] * len(extents)
        reference = max((b if b is not None else 0.0) for b in baselines)
        return [reference - (b if b is not None else 0.0) for b in baselines]

    if align is CrossAxisAlignment.STRETCH:
        return [0.0] * len(extents)
    if align is CrossAxisAlignment.CENTER:
        return [(container - e) / 2.0 for e in extents]
    if align is CrossAxisAlignment.END:
        return [container - e for e in extents]
    return [0.0] * len(extents)


class RenderFlex(RenderBox):
    """Row / Column 的渲染对象。"""

    def __init__(
        self,
        direction: Axis = Axis.HORIZONTAL,
        *,
        justify: MainAxisAlignment = MainAxisAlignment.START,
        align: CrossAxisAlignment = CrossAxisAlignment.START,
        main_axis_size: MainAxisSize = MainAxisSize.MIN,
        gap: float = 0.0,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if gap < 0.0:
            raise ValueError(f"gap 不能为负，收到 {gap}")
        self.direction: Axis = direction
        self.justify: MainAxisAlignment = justify
        self.cross_align: CrossAxisAlignment = align
        self.main_axis_size: MainAxisSize = main_axis_size
        self.gap: float = gap
        self._items: list[FlexItem] = []

    # ------------------------------------------------------------ 树

    @property
    def items(self) -> tuple[FlexItem, ...]:
        return tuple(self._items)

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return tuple(item.child for item in self._items)

    def add(
        self,
        child: RenderBox,
        *,
        flex: int = 0,
        shrink: int = 0,
        fit: FlexFit = FlexFit.TIGHT,
        align: CrossAxisAlignment | None = None,
    ) -> FlexItem:
        if flex < 0:
            raise ValueError(f"flex 权重不能为负，收到 {flex}")
        if shrink < 0:
            raise ValueError(f"shrink 权重不能为负，收到 {shrink}")
        item = FlexItem(child=child, flex=flex, shrink=shrink, fit=fit, align=align)
        self.adopt(child)
        self._items.append(item)
        return item

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
        axis = self.direction
        inner = self.content_constraints(constraints)
        main_min, main_max = main_extents(inner, axis)
        cross_min, cross_max = cross_extents(inner, axis)
        path = self.debug_path

        items = self._items
        n = len(items)
        gap_total = self.gap * max(0, n - 1)
        main_bounded = main_max < INF

        total_flex = sum(item.flex for item in items if item.flex > 0)
        if total_flex > 0 and not main_bounded:
            raise LayoutError(
                f"{self.debug_name} 的 fill 子节点遇到无限{axis.label}约束",
                path=path,
                suggestion=f"给 {self.debug_name} 一个有限的{axis.label}，或把 fill 换成固定尺寸",
            )

        sizes: list[Size] = [Size(0.0, 0.0)] * n

        # ---- 第一趟：非 flex 子级 ----
        allocated = 0.0
        for i, item in enumerate(items):
            if item.flex > 0:
                continue
            c_min, c_max = self._child_cross_extents(item, cross_min, cross_max)
            child_constraints = constraints_from(axis, 0.0, main_max, c_min, c_max)
            sizes[i] = self.layout_child(item.child, child_constraints)
            allocated += self._main_slot(sizes[i], item.child, axis)

        # ---- 第二趟：flex 子级 ----
        if total_flex > 0:
            free = main_max - allocated - gap_total
            space_per_flex = free / total_flex
            flex_indices = [i for i, item in enumerate(items) if item.flex > 0]
            remaining = free
            for k, i in enumerate(flex_indices):
                item = items[i]
                is_last = k == len(flex_indices) - 1
                # 余数全给最后一个 flex 子级：保证多次运行结果逐像素一致
                extent = remaining if is_last else space_per_flex * item.flex
                extent = max(0.0, extent)
                remaining -= extent

                c_min, c_max = self._child_cross_extents(item, cross_min, cross_max)
                if item.fit is FlexFit.TIGHT:
                    child_constraints = constraints_from(axis, extent, extent, c_min, c_max)
                else:
                    child_constraints = constraints_from(axis, 0.0, extent, c_min, c_max)
                sizes[i] = self.layout_child(item.child, child_constraints)
                allocated += self._main_slot(sizes[i], item.child, axis)

        # ---- 第三趟：收缩（仅对显式声明 shrink 的子级）----
        total_shrink = sum(item.shrink for item in items if item.shrink > 0)
        if main_bounded and total_shrink > 0 and allocated + gap_total > main_max:
            overflow = allocated + gap_total - main_max
            for i, item in enumerate(items):
                if item.shrink <= 0:
                    continue
                c_min, c_max = self._child_cross_extents(item, cross_min, cross_max)
                current = self._main_slot(sizes[i], item.child, axis)
                target = max(0.0, current - overflow * (item.shrink / total_shrink))
                child_constraints = constraints_from(axis, target, target, c_min, c_max)
                new_size = self.layout_child(item.child, child_constraints)
                allocated += self._main_slot(new_size, item.child, axis) - current
                sizes[i] = new_size

        # ---- 汇总尺寸 ----
        content_main = allocated + gap_total
        if main_bounded and content_main > main_max:
            self.note_overflow(content_main - main_max)

        if self.main_axis_size is MainAxisSize.MAX and main_bounded:
            container_main = main_max
        else:
            container_main = max(content_main, main_min)
        if main_bounded:
            container_main = min(max(container_main, main_min), main_max)

        # 对齐方式只取决于 item 的配置，提前算好——交叉轴定尺寸要用到它。
        aligns = [item.align if item.align is not None else self.cross_align for item in items]

        max_cross = 0.0
        for i, item in enumerate(items):
            max_cross = max(max_cross, self._cross_slot(sizes[i], item.child, axis))

        # 基线对齐组要额外容下"基线以下"的那部分（descender）。
        #
        # 参考线是组内最大 baseline，这没错；但容器交叉轴尺寸若只取
        # `max(子级高度)`，基线偏低（或基线以下更长）的子级就会从容器底部
        # 溢出去——而且因为它压根没参与定尺寸，连 overflow 都不会报。
        # 正确算法（Flutter 同款）：`max(above) + max(below)`。
        if any(a is CrossAxisAlignment.BASELINE for a in aligns):
            above = 0.0
            below = 0.0
            for i, item in enumerate(items):
                if aligns[i] is not CrossAxisAlignment.BASELINE:
                    continue
                slot = self._cross_slot(sizes[i], item.child, axis)
                near = self._near_cross_margin(item.child, axis)
                baseline = item.child.baseline
                if baseline is None:
                    # 没有基线的子级按"顶边贴参考线"处理，整块算进 above
                    above = max(above, slot)
                    continue
                offset = baseline + near
                above = max(above, offset)
                below = max(below, slot - offset)
            max_cross = max(max_cross, above + below)

        container_cross = max(max_cross, cross_min)
        if self.cross_align is CrossAxisAlignment.STRETCH and cross_max < INF:
            container_cross = cross_max
        container_cross = min(max(container_cross, cross_min), cross_max)

        content_size = size_from(axis, container_main, container_cross)
        outer = constraints.constrain(self.wrap(content_size))

        # 外层夹取可能让内容区比预期小，按实际内容区重新计算摆放基准
        actual_content = self.padding.deflate_rect(Rect(0.0, 0.0, outer.width, outer.height))
        origin = actual_content.top_left
        actual_main = main_of(actual_content.size, axis)
        actual_cross = cross_of(actual_content.size, axis)

        # ---- 第四趟：定位 ----
        main_slots = [self._main_slot(sizes[i], items[i].child, axis) for i in range(n)]
        main_positions = distribute_main(main_slots, actual_main, self.justify, self.gap)

        cross_slots = [self._cross_slot(sizes[i], items[i].child, axis) for i in range(n)]
        baselines: list[float | None] = []
        for item in items:
            b = item.child.baseline
            baselines.append(None if b is None else b + self._near_cross_margin(item.child, axis))

        cross_positions: list[float] = [0.0] * n

        # 基线必须**整组**一起算：参考线是组内最大的 baseline。
        # 若逐个子级单独调用，每个子级都会拿自己当参考线，于是谁都不动——
        # 这正是"看起来写了 baseline 对齐，但界面毫无变化"的经典成因。
        baseline_indices = [i for i, a in enumerate(aligns) if a is CrossAxisAlignment.BASELINE]
        if baseline_indices:
            offsets = distribute_cross(
                [cross_slots[i] for i in baseline_indices],
                actual_cross,
                CrossAxisAlignment.BASELINE,
                [baselines[i] for i in baseline_indices],
            )
            for i, off in zip(baseline_indices, offsets, strict=True):
                cross_positions[i] = off

        for i, align in enumerate(aligns):
            if align is CrossAxisAlignment.BASELINE:
                continue
            cross_positions[i] = distribute_cross([cross_slots[i]], actual_cross, align)[0]

        for i, item in enumerate(items):
            child = item.child
            main_pos = main_positions[i] + self._near_main_margin(child, axis)
            cross_pos = cross_positions[i] + self._near_cross_margin(child, axis)
            if axis is Axis.HORIZONTAL:
                self.place_child(child, Offset(origin.dx + main_pos, origin.dy + cross_pos))
            else:
                self.place_child(child, Offset(origin.dx + cross_pos, origin.dy + main_pos))

        # 容器基线：取第一个有基线的子级，换算到本节点坐标系。
        # Row 的交叉轴是垂直方向，基线沿交叉轴找；Column 的子级沿主轴堆叠，基线沿主轴找。
        for i, item in enumerate(items):
            child_baseline = item.child.baseline
            if child_baseline is None:
                continue
            if axis is Axis.HORIZONTAL:
                child_top = (
                    origin.dy + cross_positions[i] + self._near_cross_margin(item.child, axis)
                )
            else:
                child_top = origin.dy + main_positions[i] + self._near_main_margin(item.child, axis)
            self._baseline = child_top + child_baseline
            break

        return outer

    # ------------------------------------------------------------ 内部辅助

    def _child_cross_extents(
        self, item: FlexItem, cross_min: float, cross_max: float
    ) -> tuple[float, float]:
        align = item.align if item.align is not None else self.cross_align
        if align is CrossAxisAlignment.STRETCH and cross_max < INF:
            return cross_max, cross_max
        return 0.0, cross_max

    def _main_margin(self, child: RenderBox, axis: Axis) -> tuple[float, float]:
        """主轴上的 (近端 margin, 远端 margin)。"""
        if axis is Axis.HORIZONTAL:
            return child.margin.left, child.margin.right
        return child.margin.top, child.margin.bottom

    def _cross_margin(self, child: RenderBox, axis: Axis) -> tuple[float, float]:
        """交叉轴上的 (近端 margin, 远端 margin)。"""
        if axis is Axis.HORIZONTAL:
            return child.margin.top, child.margin.bottom
        return child.margin.left, child.margin.right

    def _near_main_margin(self, child: RenderBox, axis: Axis) -> float:
        return self._main_margin(child, axis)[0]

    def _near_cross_margin(self, child: RenderBox, axis: Axis) -> float:
        return self._cross_margin(child, axis)[0]

    def _main_slot(self, size: Size, child: RenderBox, axis: Axis) -> float:
        """子级在主轴上占用的槽位长度（含两侧 margin）。"""
        near, far = self._main_margin(child, axis)
        return main_of(size, axis) + near + far

    def _cross_slot(self, size: Size, child: RenderBox, axis: Axis) -> float:
        near, far = self._cross_margin(child, axis)
        return cross_of(size, axis) + near + far


class RenderRow(RenderFlex):
    """水平弹性布局。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(Axis.HORIZONTAL, **kwargs)  # type: ignore[arg-type]

    def __repr__(self) -> str:
        return f"<RenderRow {self.debug_name} {self._size.width:g}×{self._size.height:g}>"


class RenderColumn(RenderFlex):
    """垂直弹性布局。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(Axis.VERTICAL, **kwargs)  # type: ignore[arg-type]

    def __repr__(self) -> str:
        return f"<RenderColumn {self.debug_name} {self._size.width:g}×{self._size.height:g}>"
