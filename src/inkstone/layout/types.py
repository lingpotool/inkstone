"""布局基础类型。

这一层的铁律：**纯数据、不可变、无副作用、不依赖任何后端**。
布局引擎里 90% 的 bug 都来自"某个对象在别处被改了"，所以这里全部是 frozen dataclass。
只要这些类型是对的，整个布局引擎就可以在没有窗口、没有显卡的 CI 里被完整测试。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import inf

__all__ = ["INF", "BoxConstraints", "EdgeInsets", "Offset", "Rect", "Size"]

INF = float(inf)


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


@dataclass(frozen=True, slots=True)
class Offset:
    """一个二维向量，通常表示相对位置。"""

    dx: float = 0.0
    dy: float = 0.0

    def __add__(self, other: Offset) -> Offset:
        return Offset(self.dx + other.dx, self.dy + other.dy)

    def __sub__(self, other: Offset) -> Offset:
        return Offset(self.dx - other.dx, self.dy - other.dy)

    def __neg__(self) -> Offset:
        return Offset(-self.dx, -self.dy)

    def scale(self, factor: float) -> Offset:
        return Offset(self.dx * factor, self.dy * factor)


@dataclass(frozen=True, slots=True)
class Size:
    """尺寸。宽高均大于 0 才算"有效尺寸"。"""

    width: float = 0.0
    height: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.width <= 0.0 or self.height <= 0.0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def aspect_ratio(self) -> float:
        if self.height == 0.0:
            return 0.0
        return self.width / self.height

    def contains(self, other: Size) -> bool:
        return self.width >= other.width and self.height >= other.height

    def clamp(self, low: Size, high: Size) -> Size:
        return Size(
            _clamp(self.width, low.width, high.width),
            _clamp(self.height, low.height, high.height),
        )

    def __add__(self, other: Size) -> Size:
        return Size(self.width + other.width, self.height + other.height)

    def __sub__(self, other: Size) -> Size:
        return Size(self.width - other.width, self.height - other.height)


@dataclass(frozen=True, slots=True)
class Rect:
    """轴对齐矩形，原点在左上角，y 轴向下（与 UI 直觉一致，渲染层负责翻转）。"""

    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @classmethod
    def from_ltrb(cls, left: float, top: float, right: float, bottom: float) -> Rect:
        return cls(left, top, right - left, bottom - top)

    @classmethod
    def from_offset_size(cls, offset: Offset, size: Size) -> Rect:
        return cls(offset.dx, offset.dy, size.width, size.height)

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)

    @property
    def top_left(self) -> Offset:
        return Offset(self.left, self.top)

    @property
    def is_empty(self) -> bool:
        return self.width <= 0.0 or self.height <= 0.0

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def intersects(self, other: Rect) -> bool:
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )

    def intersect(self, other: Rect) -> Rect:
        return Rect.from_ltrb(
            max(self.left, other.left),
            max(self.top, other.top),
            min(self.right, other.right),
            min(self.bottom, other.bottom),
        )

    def shift(self, dx: float, dy: float) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.width, self.height)

    def inflate(self, dx: float, dy: float) -> Rect:
        return Rect(self.left - dx, self.top - dy, self.width + 2 * dx, self.height + 2 * dy)

    def deflate(self, dx: float, dy: float) -> Rect:
        return self.inflate(-dx, -dy)


@dataclass(frozen=True, slots=True)
class EdgeInsets:
    """内边距 / 外边距。字段顺序为 left / top / right / bottom。"""

    left: float = 0.0
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0

    @classmethod
    def all(cls, value: float) -> EdgeInsets:
        return cls(value, value, value, value)

    @classmethod
    def symmetric(cls, horizontal: float = 0.0, vertical: float = 0.0) -> EdgeInsets:
        return cls(horizontal, vertical, horizontal, vertical)

    @classmethod
    def only(
        cls,
        left: float = 0.0,
        top: float = 0.0,
        right: float = 0.0,
        bottom: float = 0.0,
    ) -> EdgeInsets:
        return cls(left, top, right, bottom)

    @classmethod
    def from_list(cls, values: Sequence[float]) -> EdgeInsets:
        """接受 1 / 2 / 4 个值：[全部] / [上下, 左右] / [上, 右, 下, 左]。"""
        if len(values) == 1:
            return cls.all(values[0])
        if len(values) == 2:
            return cls.symmetric(horizontal=values[1], vertical=values[0])
        if len(values) == 4:
            return cls(values[3], values[0], values[1], values[2])
        raise ValueError(f"EdgeInsets 需要 1/2/4 个值，收到 {len(values)} 个")

    @property
    def horizontal(self) -> float:
        return self.left + self.right

    @property
    def vertical(self) -> float:
        return self.top + self.bottom

    @property
    def is_non_negative(self) -> bool:
        return self.left >= 0 and self.top >= 0 and self.right >= 0 and self.bottom >= 0

    def inflate_size(self, size: Size) -> Size:
        return Size(size.width + self.horizontal, size.height + self.vertical)

    def deflate_size(self, size: Size) -> Size:
        return Size(
            max(0.0, size.width - self.horizontal),
            max(0.0, size.height - self.vertical),
        )

    def deflate_rect(self, rect: Rect) -> Rect:
        return Rect(
            rect.left + self.left,
            rect.top + self.top,
            max(0.0, rect.width - self.horizontal),
            max(0.0, rect.height - self.vertical),
        )

    def inflate_rect(self, rect: Rect) -> Rect:
        return Rect(
            rect.left - self.left,
            rect.top - self.top,
            rect.width + self.horizontal,
            rect.height + self.vertical,
        )

    def clamp_non_negative(self) -> EdgeInsets:
        return EdgeInsets(
            max(0.0, self.left),
            max(0.0, self.top),
            max(0.0, self.right),
            max(0.0, self.bottom),
        )


@dataclass(frozen=True, slots=True)
class BoxConstraints:
    """布局约束：父级向下传递"你只能在多大范围内选尺寸"，子级向上回报选定尺寸。

    这是整个布局引擎的核心数据结构（Flutter 同款模型）。
    约定：min <= max；INF 表示无上界。
    """

    min_width: float = 0.0
    max_width: float = INF
    min_height: float = 0.0
    max_height: float = INF

    def __post_init__(self) -> None:
        if self.min_width > self.max_width:
            raise ValueError(f"min_width({self.min_width}) > max_width({self.max_width})")
        if self.min_height > self.max_height:
            raise ValueError(f"min_height({self.min_height}) > max_height({self.max_height})")

    # ---------- 构造 ----------

    @classmethod
    def tight(cls, size: Size) -> BoxConstraints:
        return cls(size.width, size.width, size.height, size.height)

    @classmethod
    def tight_for(cls, width: float | None = None, height: float | None = None) -> BoxConstraints:
        return cls(
            width or 0.0,
            width if width is not None else INF,
            height or 0.0,
            height if height is not None else INF,
        )

    @classmethod
    def loose(cls, size: Size) -> BoxConstraints:
        return cls(0.0, size.width, 0.0, size.height)

    @classmethod
    def expand(cls, width: float | None = None, height: float | None = None) -> BoxConstraints:
        return cls(
            width if width is not None else INF,
            width if width is not None else INF,
            height if height is not None else INF,
            height if height is not None else INF,
        )

    # ---------- 查询 ----------

    @property
    def is_tight(self) -> bool:
        return self.min_width >= self.max_width and self.min_height >= self.max_height

    @property
    def has_bounded_width(self) -> bool:
        return self.max_width < INF

    @property
    def has_bounded_height(self) -> bool:
        return self.max_height < INF

    @property
    def has_infinite_width(self) -> bool:
        return self.min_width >= INF

    @property
    def has_infinite_height(self) -> bool:
        return self.min_height >= INF

    @property
    def biggest(self) -> Size:
        return Size(
            INF if self.has_infinite_width else self.max_width,
            INF if self.has_infinite_height else self.max_height,
        )

    @property
    def smallest(self) -> Size:
        return Size(self.min_width, self.min_height)

    # ---------- 运算 ----------

    def constrain(self, size: Size) -> Size:
        """把一个期望尺寸夹到合法区间内，宽高互不影响。"""
        return Size(
            _clamp(size.width, self.min_width, self.max_width),
            _clamp(size.height, self.min_height, self.max_height),
        )

    def constrain_width(self, width: float | None = None) -> float:
        return _clamp(width if width is not None else INF, self.min_width, self.max_width)

    def constrain_height(self, height: float | None = None) -> float:
        return _clamp(height if height is not None else INF, self.min_height, self.max_height)

    def tighten(self, width: float | None = None, height: float | None = None) -> BoxConstraints:
        return BoxConstraints(
            width if width is not None else self.min_width,
            width if width is not None else self.max_width,
            height if height is not None else self.min_height,
            height if height is not None else self.max_height,
        )

    def loosen(self) -> BoxConstraints:
        return BoxConstraints(0.0, self.max_width, 0.0, self.max_height)

    def enforce(self, other: BoxConstraints) -> BoxConstraints:
        """与另一组约束取交集，结果仍然合法。"""
        return BoxConstraints(
            max(self.min_width, other.min_width),
            min(self.max_width, other.max_width),
            max(self.min_height, other.min_height),
            min(self.max_height, other.max_height),
        )

    def deflate(self, insets: EdgeInsets) -> BoxConstraints:
        """扣掉内边距后，留给内容区的约束。"""
        if not insets.is_non_negative:
            raise ValueError("deflate 不接受负的 EdgeInsets")
        remaining_width = max(0.0, self.max_width - insets.horizontal)
        remaining_height = max(0.0, self.max_height - insets.vertical)
        return BoxConstraints(
            min_width=min(max(0.0, self.min_width - insets.horizontal), remaining_width),
            max_width=remaining_width,
            min_height=min(max(0.0, self.min_height - insets.vertical), remaining_height),
            max_height=remaining_height,
        )

    def flipped(self) -> BoxConstraints:
        return BoxConstraints(
            self.min_height,
            self.max_height,
            self.min_width,
            self.max_width,
        )
