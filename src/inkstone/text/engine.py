"""文本引擎 —— 把文本层层叠的能力收成一个对象给上层用。

为什么要有这一层：

    上层（`core` 的 BuildOwner、`widgets` 的 Text）需要的是"给我排版一段文字"，
    而不是"先建 FontResolver，再用 families 建 FallbackChain，再建 Shaper"。
    把这三步的装配收在这里，上层只认识 `TextEngine`。

装配一次、复用多次：`Shaper` 带字体选择缓存、`FontResolver` 带度量缓存，
段落排版是每帧的热路径，绝不能每帧重建这些对象。

它在分层里的位置（L3）：

    TextEngine 只依赖 `MetricsProvider`（L0 协议）——不碰窗口、不碰 style、
    不碰 core。上层把主题令牌翻译成 `TextStyle` 再交给它，
    所以"样式知识"留在 L6/L7，"文本知识"留在 L3，谁也不越界。

状态：已实现。
"""

from __future__ import annotations

from ..backend.fonts import MetricsProvider, TextMetrics
from .fallback import FallbackChain, default_chain
from .font import FontResolver, ResolvedTextStyle, TextStyle
from .paragraph import EllipsisMode, Paragraph, TextAlign, layout_paragraph
from .shaping import ShapedLine, Shaper

__all__ = ["TextEngine"]


class TextEngine:
    """文本能力的门面：解析、整形、断行、排版，共用一个度量源。"""

    def __init__(self, metrics: MetricsProvider) -> None:
        self._metrics = metrics
        # `FontResolver` 是"度量同源"的守门人：全库文本测量只经过它
        self._resolver = FontResolver(metrics)
        # 按字体链缓存 Shaper（它带字体选择缓存，重建会丢掉缓存）
        self._shapers: dict[tuple[str, ...], Shaper] = {}
        # 段落排版结果缓存（R8.3 热路径）。键是**全部输入**，所以命中即等价：
        # 同样的文字/样式/宽度/行数/对齐/省略号必然得到同一个 `Paragraph`，
        # 而 Paragraph 是不可变的，共享它没有任何副作用。
        #
        # 为什么必须有：滚动、resize、以及任何"每帧重排"的场景里，绝大多数
        # 段落的输入根本没变，但断行 + 整形会被完整重跑（实测文本密集页
        # 每帧 37ms 的纯 Python 开销，大头在这里）。
        self._paragraphs: dict[tuple[object, ...], Paragraph] = {}
        self._paragraph_limit = 2048

    # ------------------------------------------------------------ 基本访问

    @property
    def resolver(self) -> FontResolver:
        return self._resolver

    @property
    def metrics(self) -> MetricsProvider:
        return self._metrics

    def shaper_for(self, style: TextStyle) -> Shaper:
        """取该字体链对应的整形器（带缓存）。"""
        key = style.families
        hit = self._shapers.get(key)
        if hit is not None:
            return hit
        shaper = Shaper(self._resolver, default_chain(style.families))
        self._shapers[key] = shaper
        return shaper

    # ------------------------------------------------------------ 度量 / 整形

    def resolve_style(self, style: TextStyle) -> ResolvedTextStyle:
        """解析样式：真实字体族 + 度量常量。"""
        return self._resolver.resolve_style(style)

    def measure(self, text: str, style: TextStyle) -> TextMetrics:
        """测量**单行**文本。全库文本度量的唯一入口。"""
        return self._resolver.measure(text, style)

    def measure_width(self, text: str, style: TextStyle) -> float:
        """单行宽度（最常用的度量）。"""
        return self._resolver.measure(text, style).width

    def shape(self, text: str, style: TextStyle) -> ShapedLine:
        """整形一行（含逐簇字体归属）。绘制层要的就是它。"""
        return self.shaper_for(style).shape(text, style)

    # ------------------------------------------------------------ 段落排版

    def paragraph(
        self,
        text: str,
        style: TextStyle,
        *,
        max_width: float = 0.0,
        align: TextAlign = TextAlign.START,
        max_lines: int | None = None,
        ellipsis: EllipsisMode = EllipsisMode.END,
    ) -> Paragraph:
        """排版一段文字。`max_width <= 0` 表示无限宽（不换行）。

        结果按全部输入缓存：命中时直接复用上次的 `Paragraph`（不可变，可安全共享）。
        这让"每帧重排但内容没变"的滚动/resize 场景从"全量断行 + 整形"
        降到一次 dict 查询（R8.3 实测把文本密集页的纯 Python 耗时砍掉约九成）。
        """
        key: tuple[object, ...] = (text, style, max_width, align, max_lines, ellipsis)
        hit = self._paragraphs.get(key)
        if hit is not None:
            return hit
        paragraph = layout_paragraph(
            text,
            style,
            self._resolver,
            self.shaper_for(style),
            max_width=max_width,
            align=align,
            max_lines=max_lines,
            ellipsis=ellipsis,
        )
        if len(self._paragraphs) >= self._paragraph_limit:
            # FIFO 淘汰：不同文字/宽度会持续产生新键，无上界就是内存泄漏
            self._paragraphs.pop(next(iter(self._paragraphs)))
        self._paragraphs[key] = paragraph
        return paragraph

    # ------------------------------------------------------------ 生命周期

    def clear_caches(self) -> None:
        """清空度量与整形缓存。

        何时调用：DPI 变化、主题切换（字体链变了）、运行期注册字体之后。
        只清 resolver 与 shaper 的内部缓存，不重建对象——
        上层持有 TextEngine 的引用，不该因为清缓存而失效。
        """
        self._resolver.clear_caches()
        for shaper in self._shapers.values():
            shaper.clear_caches()
        self._paragraphs.clear()

    @property
    def chain(self) -> FallbackChain:
        """默认回退链（诊断用；正式排版请用 `shaper_for(style)`）。"""
        return default_chain(("sans-serif",))
