"""段落排版（L3）—— 文本管线的终点站，也是"文本能让上层用起来"的那一层。

管线全景（docs/04 §1）：

    str → 回退解析（fallback）→ 整形（shaping）→ **断行（linebreak）**
        → **排版（本模块）**→ 段落对象（几何 + 命中测试 + 选区）

本模块把前三步的产物组装成一个对布局引擎友好的对象：`Paragraph`。

它回答上层（Text 组件、Input 组件）真正关心的四个问题：

    1. 这段文字在给定宽度下**占多大**？        → `size`
    2. 每一行**画在哪**（含对齐偏移）？         → `line_origin`
    3. 鼠标点这里**是第几个字符**？             → `position_for_point`
    4. 选这段文字要**高亮哪些矩形**？           → `rects_for_range`

第 3、4 问是"文本编辑的地基"（docs/04 §5 原话），所以它们不是可选项。

关于行高：**用令牌的行高，不用字体的自然行高**（docs/04 §5 硬要求）。
字体的 ascent+descent 通常小于 UI 需要的呼吸感，直接用会让中文行距挤成一团。

关于省略号：`max_lines` 截断后追加 "…"。**截断必须按字素簇**，
不能按码点——否则会把 emoji 或组合音标劈成半个（docs/04 §7 验收项）。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..backend.fonts import TextMetrics
from ..layout.types import Rect
from .font import FontResolver, TextStyle
from .linebreak import break_line
from .shaping import ShapedLine, Shaper

__all__ = [
    "EllipsisMode",
    "Paragraph",
    "ParagraphLayout",
    "TextAlign",
    "layout_paragraph",
]


class TextAlign(Enum):
    """水平对齐。`justify` 的完整实现（含中文标点悬挂）属 Phase 3。"""

    START = "start"
    CENTER = "center"
    END = "end"
    JUSTIFY = "justify"


class EllipsisMode(Enum):
    """超出 `max_lines` 时怎么办。"""

    NONE = "none"  # 直接裁掉（裁在簇边界，不产生半个字符）
    END = "end"  # 末尾追加 "…"（最常见的"阅读更多"形态）


@dataclass(frozen=True, slots=True)
class ParagraphLayout:
    """一行的几何：整形结果 + 它在段落里的位置。

    `origin_x` / `origin_y` 是行框左上角相对段落原点的偏移（不含对齐）。
    `align_offset` 是对齐产生的额外水平位移，绘制时两者相加。
    拆开是因为**命中测试要减掉对齐偏移**才能回到行内坐标——
    如果合成一个数，反算时容易漏。
    """

    line: ShapedLine
    origin_y: float
    align_offset: float = 0.0
    #: 本行在源文本里的下标区间 `[start, end)`（用于选区与光标映射）。
    source_start: int = 0
    source_end: int = 0
    #: 被省略号截断的标记（绘制时末尾画 "…"）。
    truncated: bool = False

    @property
    def height(self) -> float:
        return self.line.line_height

    @property
    def width(self) -> float:
        return self.line.width

    @property
    def x(self) -> float:
        """行内第一个簇的绝对 x（含对齐偏移）。"""
        return self.align_offset

    @property
    def baseline_y(self) -> float:
        """基线相对段落原点的 y。绘制文本时用它，不用行顶。"""
        return self.origin_y + self.line.baseline


@dataclass(frozen=True, slots=True)
class Paragraph:
    """排版完成的段落。**布局引擎与绘制层共用的文本对象。**

    `size` 是段落占用的盒尺寸；`lines` 是每行的几何。
    两者都来自同一次排版计算，不存在"量出来的高度"与"画出来的高度"不一致。
    """

    text: str
    style: TextStyle
    lines: tuple[ParagraphLayout, ...]
    width: float
    height: float
    #: 实际可用的最大宽度（用于 `justify` 与诊断）。
    max_width: float
    truncated: bool = False

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.lines

    def line_origin(self, index: int) -> tuple[float, float]:
        """第 `index` 行的绘制原点（左上角，含对齐偏移）。"""
        layout = self.lines[index]
        return (layout.x, layout.origin_y)

    # -------------------------------------------------------- 命中测试

    def position_for_point(self, x: float, y: float) -> int:
        """点 `(x, y)` 落在哪个**字符间隙**（返回源文本下标）。

        这是文本编辑的地基（docs/04 §5）。语义：

            返回的是"光标应该插在哪"，取值为 `0..len(text)`。
            点在字符左半边 → 插在它前面；右半边 → 插在它后面。

        水平方向超出段落时**夹到两端**（而不是返回 -1）——
        用户在行尾外侧点击，期望的是光标到行尾，不是"没点中"。
        """
        if not self.lines:
            return 0

        # 纵向：先定位行。y 在行外时夹到最近行（同上，不返回 -1）
        layout = self._line_at_y(y)
        line = layout.line
        # 行内相对下标 → 段落绝对下标（两套基准，必须显式转换）
        base = layout.source_start
        if not line.clusters:
            return min(base, len(self.text))

        # 水平：减掉对齐偏移，回到行内坐标
        local_x = x - layout.align_offset
        if local_x <= 0.0:
            return base + line.clusters[0].start
        if local_x >= line.width:
            return base + line.clusters[-1].end

        cluster = line.cluster_at_x(local_x)
        if cluster is None:
            return base + line.clusters[-1].end
        # 簇内左右半边判定：这是"点哪插哪"手感的关键
        midpoint = cluster.x + cluster.advance / 2.0
        return base + (cluster.start if local_x < midpoint else cluster.end)

    def _line_at_y(self, y: float) -> ParagraphLayout:
        """纵向定位行。二分查找 + 两端夹取。"""
        lines = self.lines
        if y <= lines[0].origin_y:
            return lines[0]
        last = lines[-1]
        if y >= last.origin_y + last.height:
            return last

        low = 0
        high = len(lines) - 1
        while low <= high:
            mid = (low + high) // 2
            layout = lines[mid]
            if y < layout.origin_y:
                high = mid - 1
            elif y >= layout.origin_y + layout.height:
                low = mid + 1
            else:
                return layout
        return lines[max(0, min(low, len(lines) - 1))]

    # -------------------------------------------------------- 选区几何

    def rects_for_range(self, start: int, end: int) -> tuple[Rect, ...]:
        """`[start, end)` 这段文字的选区矩形（跨行时多个）。

        这是 docs/04 §5 要求的"选区几何与视觉一致"的落地。

        每行产出一个矩形：

            左边界 = 起点所在簇的左边（起点在行首则是行首）
            右边界 = 终点所在簇的右边（终点在行尾则是行尾）

        空区间（start == end，即"只有一个光标"）返回**零宽矩形**——
        绘制层据此画光标竖线；返回空元组会让光标消失，那是 bug。
        """
        if not self.lines:
            return ()
        if start > end:
            start, end = end, start
        start = max(0, min(start, len(self.text)))
        end = max(0, min(end, len(self.text)))

        rects: list[Rect] = []
        is_caret = start == end
        for line_index, layout in enumerate(self.lines):
            line = layout.line
            # 行与区间是否相交。区间是**半开**的 `[start, end)`：
            # 与行区间 `[ls, le)` 相交的充要条件是 `ls < end and start < le`。
            # 用严格小于——否则"选区止于第 5 个字符"会把从第 5 个字符起
            # 的下一行也高亮进来（多选一整行，肉眼可见的错）。
            ls, le = layout.source_start, layout.source_end
            if is_caret:
                # 光标：落点在本行范围内就画在本行。恰好落在行尾且
                # 存在下一行时，画到下一行行首——否则光标会吊在上一行末尾外侧。
                has_next = line_index + 1 < len(self.lines)
                in_line = ls <= start <= le
                if in_line and start == le and has_next:
                    continue
                if not in_line:
                    continue
            elif not (ls < end and start < le):
                continue
            if not line.clusters:
                continue

            left = self._x_for_index(layout, start, side="left")
            right = self._x_for_index(layout, end, side="right")
            if right < left:
                left, right = right, left
            rects.append(
                Rect(
                    left=layout.x + left,
                    top=layout.origin_y,
                    width=right - left,
                    height=layout.height,
                )
            )
        return tuple(rects)

    def _x_for_index(self, layout: ParagraphLayout, index: int, *, side: str) -> float:
        """源下标 `index`（**段落绝对坐标**）在该行内的 x 坐标。

        坐标基准的转换在这里统一处理：`layout.line.clusters[].start` 是
        **行内相对下标**，而选区/光标用的是段落绝对下标，必须减掉
        `layout.source_start` 才能比较。混用两种基准的典型症状是
        "光标画到了行尾外面"，而且只在多行时出现——极难排查。

        落在簇边界上时，`left` 取簇左、`right` 取簇右，
        这样"选中整个字符"才包含它的完整宽度。
        """
        line = layout.line
        clusters = line.clusters
        if not clusters:
            return 0.0

        local = index - layout.source_start  # 转成行内相对下标

        if local <= clusters[0].start:
            return clusters[0].x
        if local >= clusters[-1].end:
            return clusters[-1].right

        for cluster in clusters:
            if cluster.start <= local < cluster.end:
                return cluster.x if side == "left" else cluster.right
            if local == cluster.end:
                # 恰好落在簇尾：左边界取簇右（该字符之后），
                # 右边界也取簇右（下一个簇的起点与之重合）
                return cluster.right
            if local < cluster.start:
                return cluster.x
        return clusters[-1].right

    # -------------------------------------------------------- 诊断

    def describe(self) -> str:
        """检查器用的可读文本。确定性调试（与显示列表的 `describe` 同风格）。"""
        head = (
            f"Paragraph({self.line_count} 行, {self.width:g}×{self.height:g}"
            f"{', 已省略' if self.truncated else ''})"
        )
        rows = [
            f"  [{i}] x={lay.x:g} y={lay.origin_y:g} w={lay.width:g} "
            f"src=[{lay.source_start},{lay.source_end})"
            for i, lay in enumerate(self.lines)
        ]
        return "\n".join([head, *rows])


def _width_of_factory(resolver: FontResolver, style: TextStyle) -> Callable[[str], float]:
    """构造"单字符多宽"的查询函数，交给断行算法。

    断行是纯函数，不认识字体——它只拿这个回调问宽度。
    这样断行逻辑可以脱离字体 100% 单测（喂一个假的宽度函数即可）。
    """
    cache: dict[str, float] = {}

    def width_of(char: str) -> float:
        hit = cache.get(char)
        if hit is None:
            hit = resolver.advance_of(char, style)
            cache[char] = hit
        return hit

    return width_of


def layout_paragraph(
    text: str,
    style: TextStyle,
    resolver: FontResolver,
    shaper: Shaper,
    *,
    max_width: float = 0.0,
    align: TextAlign = TextAlign.START,
    max_lines: int | None = None,
    ellipsis: EllipsisMode = EllipsisMode.END,
    max_width_metrics: TextMetrics | None = None,
) -> Paragraph:
    """把文本排成一个 `Paragraph`。**文本排版的唯一入口。**

    参数：

        max_width  可用宽度（逻辑像素）。`<= 0` 表示**无限宽**——不换行、
                   整段一行。这是 TextView 在 ScrollView 里的正常输入，
                   不是错误，所以不抛异常（对应铁律 5 的"无限约束"语义）。
        max_lines  最多显示几行；超出则按 `ellipsis` 处理。
        ellipsis   如何表示截断。`END` 会追加 "…" 并**按簇回退**，
                   保证不出现半个字符。

    为什么 `max_width_metrics` 这个参数存在：
        单行场景下调用方往往已经量过这段文字（为了决定要不要换行），
        再量一次纯属浪费。传进来可跳过重复度量。它是**提示**，
        不传也完全正确。
    """
    lines_source: list[str]
    line_starts: list[int]

    if not text:
        # 空文本 → 零行、零高。不能走断行拿到 ("",) 再算出一行的高度，
        # 否则空标签会占掉一行，界面里到处是"看不见但占位"的空隙。
        return Paragraph(
            text="",
            style=style,
            lines=(),
            width=0.0,
            height=0.0,
            max_width=max_width,
            truncated=False,
        )

    if max_width <= 0.0:
        # 无限宽：按硬换行切，但不做软换行
        raw_lines = text.split("\n")
        lines_source = raw_lines
        line_starts = _offsets_of(raw_lines)
    else:
        width_of = _width_of_factory(resolver, style)
        result = break_line(text, max_width, width_of)
        lines_source = list(result.lines)
        line_starts = _offsets_of(lines_source)

    # max_lines 截断：按簇回退，绝不切半个字符
    truncated = False
    if max_lines is not None and max_lines >= 0 and len(lines_source) > max_lines:
        truncated = True
        if max_lines == 0:
            lines_source = []
        else:
            lines_source = lines_source[:max_lines]
            lines_source[-1] = _truncate_for_ellipsis(
                lines_source[-1],
                style,
                resolver,
                max_width,
                ellipsis,
            )
        line_starts = _offsets_of(lines_source)

    # 逐行整形 + 定位
    layouts: list[ParagraphLayout] = []
    y = 0.0
    content_width = 0.0
    for index, line_text in enumerate(lines_source):
        shaped = shaper.shape(line_text, style)
        align_offset = _align_offset(shaped.width, max_width, align)
        start = line_starts[index] if index < len(line_starts) else 0
        is_last = truncated and index == len(lines_source) - 1 and ellipsis is EllipsisMode.END
        layouts.append(
            ParagraphLayout(
                line=shaped,
                origin_y=y,
                align_offset=align_offset,
                source_start=start,
                source_end=start + len(line_text),
                truncated=is_last,
            )
        )
        y += shaped.line_height
        content_width = max(content_width, shaped.width)

    # 段落宽度：有限宽时按"内容实际宽度"报，而不是占满可用宽度——
    # 否则一个短文本会把父容器撑满，中文短标签场景立刻露馅。
    final_width = min(max_width, content_width) if max_width > 0.0 else content_width

    return Paragraph(
        text=text,
        style=style,
        lines=tuple(layouts),
        width=final_width,
        height=y,
        max_width=max_width,
        truncated=truncated,
    )


def _offsets_of(lines: list[str]) -> list[int]:
    """每行在源文本里的起始下标。

    注意：断行会**裁掉行尾空白**，所以 `start + len(line)` 不等于下一行的 start。
    这是有意的——空白的归属按"行尾"算，选区时不该把行尾空白算进高亮。
    """
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    return offsets


def _truncate_for_ellipsis(
    line: str,
    style: TextStyle,
    resolver: FontResolver,
    max_width: float,
    mode: EllipsisMode,
) -> str:
    """把一行截断到能放下省略号，且**不切半个字符**。

    做法：预留省略号的宽度，从末尾**按字素簇**往外丢，直到剩余文本
    加省略号能放下。按簇丢是关键——按码点丢会把 emoji 或组合音标劈半。
    """
    if mode is EllipsisMode.NONE or not line:
        return line

    ellipsis = "…"
    ellipsis_width = resolver.advance_of(ellipsis, style)
    if max_width > 0.0 and ellipsis_width > max_width:
        # 连省略号都放不下：直接返回空（交给上层显示空行而不是溢出）
        return ""

    from ..backend.headless_fonts import grapheme_clusters

    clusters = grapheme_clusters(line)
    candidates = [line[a:b] for a, b in clusters]
    while candidates:
        joined = "".join(candidates) + ellipsis
        if max_width <= 0.0 or resolver.measure(joined, style).width <= max_width:
            return joined
        candidates.pop()
    return ellipsis


def _align_offset(line_width: float, max_width: float, align: TextAlign) -> float:
    """对齐产生的水平位移。

    `max_width <= 0`（无限宽）时无位移可言——没有"可对齐的容器"。
    `JUSTIFY` 当前按 `START` 处理：行内拉伸需要逐字调整 advance，
    中文的两端对齐还要标点悬挂，属 Phase 3（docs/04 §5 已注明）。
    """
    if max_width <= 0.0:
        return 0.0
    if align is TextAlign.CENTER:
        return max(0.0, (max_width - line_width) / 2.0)
    if align is TextAlign.END:
        return max(0.0, max_width - line_width)
    return 0.0
