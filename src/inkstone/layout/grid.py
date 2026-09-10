"""网格布局：Grid。

Grid 是为**表单、属性面板、仪表盘**准备的。文档里有一句很清醒的话：

    "不许拿 Grid 当全局布局滥用（那是 Flex 和 Stack 的活）"

所以这里的定位是明确的二维小表格，不是页面框架。

轨道三态：

    fixed(v)   固定像素
    fr(w)      剩余空间的权重份额（类似 Flex 的 flex）
    auto()     按内容（取该轨道上"恰好占 1 格"的子级的最大内容尺寸）

两个容易吵起来的设计取舍，这里的选择及理由：

1. **跨多格的子级不参与 auto 轨道的定尺寸。**
   一个横跨 3 列的子级该把宽度算到哪一列上？答案不唯一（CSS 为此有一整套
   复杂算法）。这里选择"不算"——它简单、可预测，且真实场景里横跨列的
   子级本身通常是 fr 或固定宽度。

2. **格内默认近端对齐（左上），不是拉伸。**
   子级拿到的是 `0..格宽 / 0..格高` 的宽松约束：固定尺寸的子级保持自身尺寸，
   `Sizing.fill()` 的子级撑满格子。这比一律 tight 更符合直觉，也更好测。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from .box import RenderBox
from .protocol import (
    INF,
    Axis,
    LayoutError,
)
from .types import BoxConstraints, Offset, Rect, Size

__all__ = ["GridItem", "RenderGrid", "TrackKind", "TrackSize"]


class TrackKind(Enum):
    """网格轨道的尺寸模式。"""

    FIXED = "fixed"
    FR = "fr"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class TrackSize:
    """一条网格轨道。`value` 在 FIXED 下是像素，在 FR 下是权重，AUTO 下忽略。"""

    kind: TrackKind = TrackKind.AUTO
    value: float = 1.0

    def __post_init__(self) -> None:
        if self.kind is TrackKind.FIXED and self.value < 0.0:
            raise ValueError(f"fixed 轨道不能为负，收到 {self.value}")
        if self.kind is TrackKind.FR and self.value <= 0.0:
            raise ValueError(f"fr 权重必须为正，收到 {self.value}")

    @classmethod
    def fixed(cls, value: float) -> TrackSize:
        return cls(TrackKind.FIXED, value)

    @classmethod
    def fr(cls, weight: float = 1.0) -> TrackSize:
        return cls(TrackKind.FR, weight)

    @classmethod
    def auto(cls) -> TrackSize:
        return cls(TrackKind.AUTO, 0.0)

    def __str__(self) -> str:
        if self.kind is TrackKind.FIXED:
            return f"{self.value:g}px"
        if self.kind is TrackKind.FR:
            return f"fr({self.value:g})"
        return "auto"


@dataclass(frozen=True, slots=True)
class GridItem:
    """一个子级在 Grid 里的位置参数。column / row 为 None 时自动放置。"""

    child: RenderBox
    column: int | None = None
    row: int | None = None
    column_span: int = 1
    row_span: int = 1

    def __post_init__(self) -> None:
        if self.column_span < 1 or self.row_span < 1:
            raise ValueError("span 必须 >= 1")
        if self.column is not None and self.column < 0:
            raise ValueError("column 不能为负")
        if self.row is not None and self.row < 0:
            raise ValueError("row 不能为负")


class RenderGrid(RenderBox):
    """二维网格容器。"""

    def __init__(
        self,
        columns: Sequence[TrackSize] = (),
        rows: Sequence[TrackSize] = (),
        *,
        gap: float = 0.0,
        column_gap: float | None = None,
        row_gap: float | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if gap < 0.0:
            raise ValueError(f"gap 不能为负，收到 {gap}")
        self.columns: tuple[TrackSize, ...] = tuple(columns)
        self.rows: tuple[TrackSize, ...] = tuple(rows)
        self.gap: float = gap
        self.column_gap: float = gap if column_gap is None else column_gap
        self.row_gap: float = gap if row_gap is None else row_gap
        self._items: list[GridItem] = []

    # ------------------------------------------------------------ 树

    @property
    def items(self) -> tuple[GridItem, ...]:
        return tuple(self._items)

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return tuple(item.child for item in self._items)

    def add(
        self,
        child: RenderBox,
        *,
        column: int | None = None,
        row: int | None = None,
        column_span: int = 1,
        row_span: int = 1,
    ) -> GridItem:
        item = GridItem(
            child=child,
            column=column,
            row=row,
            column_span=column_span,
            row_span=row_span,
        )
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
        inner = self.content_constraints(constraints)
        path = self.debug_path

        cells = self._assign_cells()

        # 声明过的轨道一定存在（哪怕没有子级占用），子级用到的隐式轨道额外补 AUTO
        n_cols = max(
            [len(self.columns)]
            + [c + it.column_span for it, (c, _) in zip(self._items, cells, strict=True)]
        )
        n_rows = max(
            [len(self.rows)]
            + [r + it.row_span for it, (_, r) in zip(self._items, cells, strict=True)]
        )

        width_bounded = inner.has_bounded_width
        height_bounded = inner.has_bounded_height
        available_width = inner.max_width if width_bounded else INF
        available_height = inner.max_height if height_bounded else INF

        # 列：测量时用"可用的高"当交叉轴（有界则用上界，否则不约束）
        col_sizes = self._resolve_tracks(
            declared=self.columns,
            count=n_cols,
            available=available_width,
            gap=self.column_gap,
            axis=Axis.HORIZONTAL,
            cross_extent=available_height,
            items=self._items,
            cells=cells,
            span_of=lambda it: it.column_span,
            index_of=lambda it, cell: cell[0],
            path=path,
            axis_label="列",
        )

        # 行：此时列宽已知，用"该子级所在格的宽"当交叉轴，测量更准
        row_sizes = self._resolve_tracks(
            declared=self.rows,
            count=n_rows,
            available=available_height,
            gap=self.row_gap,
            axis=Axis.VERTICAL,
            cross_extent=0.0,  # 按子级实际格宽逐个覆盖
            items=self._items,
            cells=cells,
            span_of=lambda it: it.row_span,
            index_of=lambda it, cell: cell[1],
            path=path,
            axis_label="行",
            per_item_cross=lambda it, cell: self._span_extent(
                col_sizes, cell[0], it.column_span, self.column_gap
            ),
        )

        content_width = self._total(col_sizes, self.column_gap)
        content_height = self._total(row_sizes, self.row_gap)

        # fr 轨道只有在有界时才分得到空间；无界时上面已抛错，这里直接夹取即可
        content = Size(content_width, content_height)
        outer = constraints.constrain(self.wrap(content))
        origin = Offset(self.padding.left, self.padding.top)

        # 内容超出容器时**报溢出，不回缩轨道**。
        # 按比例压缩 fixed 轨道属于"悄悄压扁"——界面看起来没坏，但 200px 的
        # 列实际只有 150px，这种 bug 很难查。宁可让检查器看见。
        actual = self.padding.deflate_rect(Rect(0.0, 0.0, outer.width, outer.height))
        if content_width > actual.width:
            self.note_overflow(content_width - actual.width)
        if content_height > actual.height:
            self.note_overflow(content_height - actual.height)

        for item, (c, r) in zip(self._items, cells, strict=True):
            cell_width = self._span_extent(col_sizes, c, item.column_span, self.column_gap)
            cell_height = self._span_extent(row_sizes, r, item.row_span, self.row_gap)
            cell_constraints = BoxConstraints(0.0, cell_width, 0.0, cell_height)
            self.layout_child(item.child, cell_constraints)

            x = self._track_offset(col_sizes, c, self.column_gap)
            y = self._track_offset(row_sizes, r, self.row_gap)
            self.place_child(item.child, Offset(origin.dx + x, origin.dy + y))

            if item.child.size.width > cell_width or item.child.size.height > cell_height:
                self.note_overflow(
                    max(item.child.size.width - cell_width, item.child.size.height - cell_height)
                )

        return outer

    # ------------------------------------------------------------ 内部辅助

    def _assign_cells(self) -> list[tuple[int, int]]:
        """给每个子级分配格子。显式指定的优先，其余按行优先自动放置。"""
        occupied: set[tuple[int, int]] = set()
        cells: list[tuple[int, int]] = []
        placement_width = len(self.columns) if self.columns else 1

        for item in self._items:
            if item.column_span > placement_width and self.columns:
                raise LayoutError(
                    f"{item.child.debug_name} 横跨 {item.column_span} 列，"
                    f"但 {self.debug_name} 只声明了 {placement_width} 列",
                    path=item.child.debug_path,
                    suggestion="把 column_span 收窄，或在 Grid 的 columns 里补上隐式列",
                )

            if item.column is not None and item.row is not None:
                c, r = item.column, item.row
            elif item.column is not None:
                c = item.column
                r = self._first_free_row(occupied, c, item.column_span, item.row_span)
            else:
                c, r = self._first_free(occupied, item.column_span, item.row_span, placement_width)

            for dc in range(item.column_span):
                for dr in range(item.row_span):
                    occupied.add((c + dc, r + dr))
            cells.append((c, r))

        return cells

    @staticmethod
    def _fits(occupied: set[tuple[int, int]], c: int, r: int, span_c: int, span_r: int) -> bool:
        return all((c + dc, r + dr) not in occupied for dc in range(span_c) for dr in range(span_r))

    def _first_free(
        self, occupied: set[tuple[int, int]], span_c: int, span_r: int, width: int
    ) -> tuple[int, int]:
        r = 0
        while r < 10_000:  # 防御性上限：真实界面不会有上万行
            for c in range(width - span_c + 1):
                if self._fits(occupied, c, r, span_c, span_r):
                    return c, r
            r += 1
        raise LayoutError(
            f"{self.debug_name} 自动放置失败：找不到能放下 {span_c}×{span_r} 的空格",
            path=self.debug_path,
            suggestion="显式指定 column / row，或减少子级数量",
        )

    def _first_free_row(
        self, occupied: set[tuple[int, int]], c: int, span_c: int, span_r: int
    ) -> int:
        r = 0
        while r < 10_000:
            if self._fits(occupied, c, r, span_c, span_r):
                return r
            r += 1
        raise LayoutError(
            f"{self.debug_name} 在第 {c} 列上找不到空行",
            path=self.debug_path,
            suggestion="显式指定 row",
        )

    def _resolve_tracks(
        self,
        *,
        declared: Sequence[TrackSize],
        count: int,
        available: float,
        gap: float,
        axis: Axis,
        cross_extent: float,
        items: Sequence[GridItem],
        cells: Sequence[tuple[int, int]],
        span_of: Callable[[GridItem], int],
        index_of: Callable[[GridItem, tuple[int, int]], int],
        path: str,
        axis_label: str,
        per_item_cross: Callable[[GridItem, tuple[int, int]], float] | None = None,
    ) -> list[float]:
        tracks = list(declared[:count]) + [TrackSize.auto()] * max(0, count - len(declared))
        total_fr = sum(t.value for t in tracks if t.kind is TrackKind.FR)
        bounded = available < INF

        if total_fr > 0 and not bounded:
            raise LayoutError(
                f"{self.debug_name} 的 fr {axis_label}遇到无限{axis.label}约束",
                path=path,
                suggestion=f"给 {self.debug_name} 一个有限的{axis.label}，或把 fr 换成 fixed/auto",
            )

        sizes = [0.0] * count
        remaining = available - gap * max(0, count - 1) if bounded else INF

        for i, track in enumerate(tracks):
            if track.kind is TrackKind.FIXED:
                sizes[i] = track.value
                remaining -= track.value
            elif track.kind is TrackKind.AUTO:
                measured = 0.0
                for item, cell in zip(items, cells, strict=True):
                    if span_of(item) != 1 or index_of(item, cell) != i:
                        continue
                    cross = per_item_cross(item, cell) if per_item_cross else cross_extent
                    measured = max(measured, item.child.measure_unbounded(axis, cross))
                sizes[i] = measured
                remaining -= measured

        remaining = max(0.0, remaining)
        if total_fr > 0:
            for i, track in enumerate(tracks):
                if track.kind is TrackKind.FR:
                    sizes[i] = remaining * (track.value / total_fr)

        return sizes

    @staticmethod
    def _total(sizes: Sequence[float], gap: float) -> float:
        if not sizes:
            return 0.0
        return sum(sizes) + gap * (len(sizes) - 1)

    @staticmethod
    def _span_extent(sizes: Sequence[float], start: int, span: int, gap: float) -> float:
        end = min(start + span, len(sizes))
        return RenderGrid._total(sizes[start:end], gap)

    @staticmethod
    def _track_offset(sizes: Sequence[float], index: int, gap: float) -> float:
        return sum(sizes[:index]) + gap * index
