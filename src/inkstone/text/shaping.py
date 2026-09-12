"""整形编排（L3）—— 把"中英混排的一行"变成"带字体归属的字形序列"。

ADR-0005 的边界在这里落地：

    **真正的整形**（连字、字距调整、复杂脚本重排、双向文本）
    由后端提供——那是 HarfBuzz / DirectWrite / CoreText 的活，
    自造等于用两年换一个更差的版本。

    **本模块负责的是编排**：字符串 → 按脚本切段 → 逐段选字体（回退链）
    → 逐段交给后端整形 → 拼成完整一行。这些才是 UI 库的本职
    （docs/04 §2 原话："我们只写回退链、段落组装、命中测试、IME 组合渲染"）。

为什么"编排"值得单独一层，而不是让后端一次搞定整行：

    后端认识"字体"，不认识"回退链"。回退链是三平台的策略
    （docs/04 §4 那张表），属于 UI 库的知识，不属于平台 API。
    把策略放在 L3、机制放在 L0，才能在 Windows 上跑同一份回退逻辑。

结果类型：`ShapedLine`。它是**绘制、命中测试、选区**三件事的共同输入——
一行文字的所有几何信息，在这里一次性算完。

状态：已实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..gfx.display_list import PositionedGlyph
from .fallback import FallbackChain, FontScript, script_of, split_by_script
from .font import FontResolver, TextStyle

__all__ = [
    "ShapedCluster",
    "ShapedLine",
    "Shaper",
    "shaped_line_from_text",
]


@dataclass(frozen=True, slots=True)
class ShapedCluster:
    """一个**已定位**的字素簇：源下标 + 像素位置 + 归属字体 + 度量。

    比后端的 `GlyphPlacement` 多带三样东西，因为排版真正需要它们：

    - `index`：簇在整行里的序号（二分查找用）；
    - `script`：它属于哪个文字系统（诊断"这段为什么用了那个字体"）；
    - `ascent` / `descent`：**逐簇**纵向度量。混排时不同字体的
      ascent 不同，行级度量必须取并集，否则某个字体的大写字母会顶出上沿。
    """

    index: int
    start: int
    end: int
    x: float
    advance: float
    family: str
    script: FontScript
    ascent: float
    descent: float
    #: 相对**基线**的纵向偏移，向上为正（对应 HarfBuzz 的 `y_offset`）。
    #: 上下标、组合符、CJK 标点悬挂、多字体回退的基线差全靠它——
    #: 没有它，这些字形只能全部压在基线上。
    y: float = 0.0
    #: 本簇在字体里的字形 id（整形结果）。连字是"一个字形覆盖多个字素簇"，
    #: 那个 id 落在**第一个**被覆盖的簇上；光栅层据此画连字字形，
    #: 而不是按文本逐簇重新整形（那会画出分开的字母）。
    glyph_ids: tuple[int, ...] = ()
    #: `glyph_ids` 所属的具体字体面标识（R7.4）。光栅必须用同一个面解释这些
    #: id，否则多字重字体会"用甲的 id 查乙的轮廓"，画出别的字。
    face_key: str = ""

    @property
    def right(self) -> float:
        """簇的右边界（左闭右开区间的右端）。"""
        return self.x + self.advance

    def contains_x(self, x: float) -> bool:
        """`x` 是否落在本簇水平范围内。命中测试的原子判定。"""
        return self.x <= x < self.right


@dataclass(frozen=True, slots=True)
class ShapedLine:
    """整形完成的一行：字形序列 + 行级度量。

    这是文本管线的**核心产物**：

        绘制      → 按 `clusters` 逐簇提交（同 family 可合并 draw call）
        命中测试  → 在 `clusters` 里二分找 `contains_x`
        选区      → 用 `clusters` 的 x/right 切矩形
        行高      → `line_height`（来自令牌，不是字体自然行高）

    `baseline` 是从行顶到基线的距离，**不是**字体 ascent——
    行高由令牌决定，基线在行内居中偏下，见 `baseline` 的说明。
    """

    text: str
    clusters: tuple[ShapedCluster, ...]
    #: 行的高（逻辑像素）。来自 `TextStyle.line_height_px`。
    line_height: float
    #: 行内最大 ascent / descent（混排时各字体取并集）。
    ascent: float
    descent: float

    @property
    def width(self) -> float:
        """行宽 = 最后一个簇的右边界（空行则 0）。"""
        return self.clusters[-1].right if self.clusters else 0.0

    @property
    def baseline(self) -> float:
        """基线相对行顶的 y 偏移。

        算法：行高减去内容高度后**居中**分配，再把基线放在 ascent 处。
        这样单行文本在固定行高的行框里视觉居中，而不是贴着顶部——
        中文尤其明显：基线贴顶时汉字会跑到行框上沿之外。
        """
        content = self.ascent + self.descent
        leading = max(0.0, self.line_height - content)
        return leading / 2.0 + self.ascent

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)

    def cluster_at_x(self, x: float) -> ShapedCluster | None:
        """`x` 落在哪个簇上；越过行尾返回 `None`。

        二分查找而非线性扫描：命中测试在鼠标移动时每帧都跑，
        长行线性扫会让输入框拖尾。
        """
        if not self.clusters:
            return None
        low = 0
        high = len(self.clusters) - 1
        while low <= high:
            mid = (low + high) // 2
            cluster = self.clusters[mid]
            if x < cluster.x:
                high = mid - 1
            elif x >= cluster.right:
                low = mid + 1
            else:
                return cluster
        return None

    def positioned_glyphs(self) -> tuple[PositionedGlyph, ...]:
        """转成显示列表要的**已定位字形**序列（L3 → L2 的唯一转换点）。

        为什么收在这里：此前 `widgets/basic.py` 与 `widgets/form.py` 各写了
        一份一模一样的转换，两份副本意味着"加一个字段要改两处，漏一处就静默
        丢信息"——R3.2 加的 `y_offset` 就是这么被漏掉的（字段在、值永远是 0）。
        放在 `ShapedLine` 上还顺带说清了一件事：`text/` 产出字形，
        `gfx/` 只负责画（ADR-0008）。
        """
        return tuple(
            PositionedGlyph(
                text=self.text[cluster.start : cluster.end],
                x=cluster.x,
                advance=cluster.advance,
                family=cluster.family,
                y_offset=cluster.y,
                glyph_ids=cluster.glyph_ids,
                face_key=cluster.face_key,
            )
            for cluster in self.clusters
        )


class Shaper:
    """整形器：持有回退链与解析器，把文本变成 `ShapedLine`。

    它**不持有度量实现**——一切度量都经 `FontResolver` 走到后端。
    这就是"度量同源"在整形路径上的保证：整形与排版用的是同一组数字。
    """

    def __init__(self, resolver: FontResolver, chain: FallbackChain) -> None:
        self._resolver = resolver
        self._chain = chain
        self._family_cache: dict[tuple[FontScript, TextStyle], str] = {}

    @property
    def resolver(self) -> FontResolver:
        return self._resolver

    @property
    def chain(self) -> FallbackChain:
        return self._chain

    def shape(self, text: str, style: TextStyle) -> ShapedLine:
        """把**一行**文本（不含换行符）整形为 `ShapedLine`。

        流程：

            1. 按脚本把文本切成 run（中英混排会得到多段）；
            2. 每段按回退链选出**该脚本**实际可用的字体；
            3. 逐段调后端整形，拿到簇位置；
            4. 拼起来，算行级度量（ascent/descent 取并集）。
        """
        line_height = style.line_height_px
        if not text:
            return ShapedLine(
                text="",
                clusters=(),
                line_height=line_height,
                ascent=0.0,
                descent=0.0,
            )

        clusters: list[ShapedCluster] = []
        x_cursor = 0.0
        max_ascent = 0.0
        max_descent = 0.0
        index = 0

        for run in split_by_script(text):
            run_text = text[run.start : run.end]
            family = self._family_for(run.script, style)
            glyph_run = self._resolver.shape(run_text, self._style_with_family(style, family))

            for placement in glyph_run.placements:
                # 逐簇的脚本：run 内可能混有 emoji 变体选择符这类
                # 属于相邻脚本的字符，按首字符判定已足够（回退链只按大类分）
                clusters.append(
                    ShapedCluster(
                        index=index,
                        start=run.start + placement.start,
                        end=run.start + placement.end,
                        x=x_cursor + placement.x,
                        advance=placement.advance,
                        family=placement.family,
                        y=placement.y,
                        glyph_ids=placement.glyph_ids,
                        face_key=placement.face_key,
                        script=script_of(run_text[placement.start]),
                        ascent=glyph_run.metrics.ascent,
                        descent=glyph_run.metrics.descent,
                    )
                )
                index += 1

            x_cursor += glyph_run.metrics.width
            max_ascent = max(max_ascent, glyph_run.metrics.ascent)
            max_descent = max(max_descent, glyph_run.metrics.descent)

        return ShapedLine(
            text=text,
            clusters=tuple(clusters),
            line_height=line_height,
            ascent=max_ascent,
            descent=max_descent,
        )

    # ------------------------------------------------------------ 内部

    def _family_for(self, script: FontScript, style: TextStyle) -> str:
        """该脚本该用哪个字体族。结果缓存（同一段落反复整形时省掉探测）。"""
        key = (script, style)
        hit = self._family_cache.get(key)
        if hit is not None:
            return hit

        chain = self._chain.chain_for(script, self._resolver)
        family = chain[0] if chain else "sans-serif"
        self._family_cache[key] = family
        return family

    def _style_with_family(self, style: TextStyle, family: str) -> TextStyle:
        """把样式主字体换成选中的那个，其余（字号/字重/行高）不变。

        只把选中字体放在链首：让后端优先在它里面解析，缺字时才继续往下退。
        """
        return TextStyle(
            families=(family, *style.families),
            size=style.size,
            weight=style.weight,
            slant=style.slant,
            line_height=style.line_height,
        )

    def clear_caches(self) -> None:
        """清空字体选择缓存。主题或字体注册表变化时调用。"""
        self._family_cache.clear()


def shaped_line_from_text(
    text: str,
    style: TextStyle,
    resolver: FontResolver,
    chain: FallbackChain,
) -> ShapedLine:
    """一次性整形的便捷函数。适合测试与低频调用。

    高频调用（段落排版）请复用一个 `Shaper` 实例——它带字体选择缓存。
    """
    return Shaper(resolver, chain).shape(text, style)
