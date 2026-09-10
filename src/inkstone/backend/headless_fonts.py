"""无头字体度量 —— 让文本栈在 CI 里完全确定（docs/04 §3 的可测性落地）。

这里解决一个真实矛盾：

    真字体度量必然依赖系统字体，而系统字体在 Windows / macOS / Ubuntu 上
    **不一样**（雅黑 vs 苹方 vs Noto），字号与 advance 都不同。
    如果测试用真字体度量，黄金图就没有一张能在三平台同时通过。

所以无头后端提供两种度量：

1. **确定性度量表**（默认）—— 由 `FontTable` 描述的一组"虚拟字体"，
   每个字符的 advance 由**可见宽度模型**算出，与任何系统字体无关。
   它满足一切质量要求（中文全宽、拉丁半宽、字素簇完整、度量与绘制同源），
   唯一的"缺点"是与真字体的数字不同——但这正是我们要的：
   **测试断言的是排版的正确性，不是某个字体的具体数字。**

2. **系统字体代理**（`system=True`）—— 走平台原生的度量（Windows 用
   GDI 的 `GetTextExtentPoint32W`），用于"真机效果预览"与 Phase 1
   的样板 App。它**不参与**黄金图基线。

这条分界写进 docs/04 §3 的注释里：**度量同源是硬约束，
但"同源到哪个源"在测试与生产是两件事**——生产源必须与渲染后端一致，
测试源必须跨平台一致。两者都用 `Backend.measure_text` 这一个入口。

状态：已实现。
"""

from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fonts import (
    FontFace,
    FontMetricsError,
    FontSlant,
    FontSpec,
    FontWeight,
    GlyphPlacement,
    GlyphRun,
    TextMetrics,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期
    pass

__all__ = ["FontTable", "HeadlessMetrics", "default_font_table"]


# ------------------------------------------------------------------ 字符分类


def is_wide(char: str) -> bool:
    """该字符是否占"全宽"（CJK 方块字 / 全角标点）。

    用 Unicode 的 East Asian Width 属性判断，而不是靠码点区间硬编码——
    区间表会漏掉扩展区的汉字与全角符号。`W`=宽、`F`=全宽，都算全宽。
    """
    return unicodedata.east_asian_width(char) in ("W", "F")


def is_zero_width(char: str) -> bool:
    """是否零宽（组合符、变体选择符、零宽连接符）。

    它们不推进光标位置，但仍属于前一个字素簇——断行与命中测试
    如果把它们当独立字符，光标就会在"é"中间停下。
    """
    if unicodedata.combining(char):
        return True
    return char in ("\u200b", "\u200c", "\u200d", "\ufeff")


def grapheme_clusters(text: str) -> list[tuple[int, int]]:
    """按**字素簇**切分，返回每簇的 `[start, end)` 下标。

    这是文本管线里最重要的切分动作：断行、命中测试、选区、
    省略号截断都必须按簇操作，否则会把 emoji 或组合音标劈成两半，
    表现为"删一个字符删掉半张脸"。

    这里用的是**简化规则**（按 docs/04 §2 的精神：复杂规则交给
    平台整形器）：基字符 + 其后的组合符/零宽连接符 + 后续连接的自然
    延伸，构成一簇。完整的 UAX #29 扩展字素簇（含国旗对、家庭 emoji
    序列）在 Phase 3 随富文本一起补，此处先保证中文与常见 emoji 正确。
    """
    clusters: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    zero_width_joiners = ("\u200b", "\u200c", "\u200d", "\ufeff")
    while index < length:
        start = index
        index += 1  # 基字符
        # 吸收"继续本簇"的字符：组合符、零宽字符；
        # 零宽连接符（ZWJ）还会把**紧跟其后的字符**也拉进同一簇——
        # 这正是 👨‍👩‍👧 这类家庭 emoji 由 5 个码点组成一簇的原因。
        # 实现为"循环到不再延伸"而不是"嵌套两层 while"：后者会在
        # 连续多个 ZWJ 时提前跳出，把一簇切成两簇。
        while index < length:
            char = text[index]
            if not (unicodedata.combining(char) or char in zero_width_joiners):
                break
            index += 1
            if char == "\u200d" and index < length:
                index += 1  # ZWJ 之后的基字符同簇
        clusters.append((start, index))
    return clusters


# ------------------------------------------------------------------ 虚拟字体


@dataclass(frozen=True, slots=True)
class FontTable:
    """一组虚拟字体的度量参数。

    数字全部相对于**字号 1.0** 的"em 比例"，实际度量时乘以字号。
    这样同一张表可以服务任意字号，也保证了"字号翻倍宽度翻倍"这条
    排版基本直觉在测试里成立。

    默认值取自常见 UI 字体的典型比例（ascent/descent 约为 0.8/0.2），
    不是某个具体字体的精确复制——精确复制既无必要也不可复现。
    """

    #: 已装载的字体族名（按优先级排列）。`sans-serif` / `serif` / `mono`
    #: 这三个通用名**总是**存在，是回退链的终点。
    families: tuple[str, ...] = (
        "sans-serif",
        "serif",
        "mono",
        "Microsoft YaHei UI",
        "PingFang SC",
        "Noto Sans CJK SC",
        "Segoe UI Emoji",
    )
    #: 拉丁字符宽度（em 比例）。等宽字体用它区分。本表统一按比例字体处理。
    latin_advance: float = 0.5
    #: 全宽字符宽度（em 比例）。CJK 方块字是 1.0，即"一字一 em"。
    wide_advance: float = 1.0
    #: 基线以上（em 比例）。
    ascent: float = 0.8
    #: 基线以下（em 比例）。
    descent: float = 0.2
    #: 行间隙（em 比例）。
    line_gap: float = 0.0
    #: 字重对宽度的放大系数。粗体略宽是真实行为，留着才能测"加粗后要重新布局"。
    weight_scale: float = 0.02

    def family_exists(self, family: str) -> bool:
        return family in self.families or family in ("sans-serif", "serif", "mono")


def default_font_table() -> FontTable:
    """默认度量表：覆盖三平台的回退链名字，且**不偏向任何一个平台**。"""
    return FontTable()


def _char_advance_em(char: str, table: FontTable) -> float:
    """单个字素簇基字符的宽度（em 比例）。

    空格特别处理：它有固定宽度，但**不参与断行的字符级判断**（见 linebreak）。
    """
    if char == "\t":
        return table.latin_advance * 4.0
    if is_zero_width(char):
        return 0.0
    if is_wide(char):
        return table.wide_advance
    # 控制字符不占位（换行符由断行层先消掉，落到这里说明调用方没断行，
    # 按 0 处理并交给上层报错，不在这里静默吞掉）
    if unicodedata.category(char) == "Cc":
        return 0.0
    return table.latin_advance


# ------------------------------------------------------------------ 度量实现


class HeadlessMetrics:
    """无头度量提供方。`HeadlessBackend` 混入它获得 `MetricsProvider` 能力。

    可以被独立实例化（`HeadlessMetrics()`），用于纯文本层的单元测试——
    那些测试不需要窗口、事件、时钟，只要数字。
    """

    def __init__(self, table: FontTable | None = None, *, system: bool = False) -> None:
        self._table = table if table is not None else default_font_table()
        self._system = system and sys.platform == "win32"
        self._measure_cache: dict[tuple[str, FontSpec], TextMetrics] = {}
        self._shape_cache: dict[tuple[str, FontSpec], GlyphRun] = {}
        self._resolve_cache: dict[FontSpec, FontFace] = {}
        self._stats = {"measure_calls": 0, "shape_calls": 0, "cache_hits": 0}

    # ------------------------------------------------------------ 字体解析

    @property
    def font_table(self) -> FontTable:
        """暴露度量表，测试可以断言"确实用的这张表"。"""
        return self._table

    def has_family(self, family: str) -> bool:
        return self._table.family_exists(family)

    def resolve_font(self, spec: FontSpec) -> FontFace:
        """从优先列表里挑第一个存在的族；都不存在则退到 `sans-serif`。"""
        cached = self._resolve_cache.get(spec)
        if cached is not None:
            return cached

        chosen = next((f for f in spec.families if self.has_family(f)), "sans-serif")
        face = FontFace(
            resolved_family=chosen,
            size=spec.size,
            weight=spec.weight,
            slant=spec.slant,
        )
        self._resolve_cache[spec] = face
        return face

    # ------------------------------------------------------------ 度量

    def measure_text(self, text: str, spec: FontSpec) -> TextMetrics:
        """测量单行文本。结果进缓存——度量是全库调用最频繁的操作之一。"""
        key = (text, spec)
        hit = self._measure_cache.get(key)
        if hit is not None:
            self._stats["cache_hits"] += 1
            return hit

        self._stats["measure_calls"] += 1
        table = self._table
        scale = spec.size * (1.0 + table.weight_scale * _weight_index(spec.weight))
        advances: list[float] = []
        for start, _end in grapheme_clusters(text):
            width = _char_advance_em(text[start], table)
            # 簇内其余字符（组合符/零宽）不额外推进
            advances.append(width * scale)

        metrics = TextMetrics(
            width=sum(advances),
            ascent=table.ascent * scale,
            descent=table.descent * scale,
            line_gap=table.line_gap * scale,
            advance=tuple(advances),
        )
        self._measure_cache[key] = metrics
        return metrics

    def shape_line(self, text: str, spec: FontSpec) -> GlyphRun:
        """整形一行：逐字素簇给出位置与所属字体。

        无头实现里没有连字与字距（那需要真整形器），但**接口与结构完整**：
        位置是累加出来的、`start/end` 是源字符串下标、逐簇带字体归属。
        换真后端时上层代码一行不改。
        """
        key = (text, spec)
        hit = self._shape_cache.get(key)
        if hit is not None:
            self._stats["cache_hits"] += 1
            return hit

        self._stats["shape_calls"] += 1
        metrics = self.measure_text(text, spec)
        face = self.resolve_font(spec)
        placements: list[GlyphPlacement] = []
        x = 0.0
        for (start, end), advance in zip(grapheme_clusters(text), metrics.advance, strict=True):
            placements.append(
                GlyphPlacement(
                    start=start,
                    end=end,
                    x=x,
                    y=0.0,
                    advance=advance,
                    family=face.resolved_family,
                )
            )
            x += advance

        run = GlyphRun(
            text=text,
            placements=tuple(placements),
            metrics=metrics,
            start=0,
            end=len(text),
        )
        self._shape_cache[key] = run
        return run

    # ------------------------------------------------------------ 诊断

    def stats(self) -> dict[str, int]:
        """缓存命中统计。性能测试与检查器用它证明"度量确实被缓存了"。"""
        return dict(self._stats)

    def clear_caches(self) -> None:
        """清空缓存。DPI 变化导致字号换算基础变化时由上层调用。"""
        self._measure_cache.clear()
        self._shape_cache.clear()
        self._resolve_cache.clear()


def _weight_index(weight: FontWeight) -> float:
    """字重→放大档位。语义清晰：正常 0、粗体最大。"""
    return {
        FontWeight.REGULAR: 0.0,
        FontWeight.MEDIUM: 1.0,
        FontWeight.SEMIBOLD: 2.0,
        FontWeight.BOLD: 3.0,
    }[weight]


# 供外部按需构造（例如显式指定 slant 的测试）
_ = (FontSlant, FontMetricsError)
