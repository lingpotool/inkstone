"""字体管理（L3）—— 字体规格、度量缓存与"度量同源"的守门人。

它做的事只有一件：**把"我要一段中文，字号 14"翻译成后端的度量请求，
并把结果缓存起来**。它自己不量字、不碰平台。

为什么这层还需要存在，而不是让上层直接调 `backend.measure_text`：

1. **令牌 → FontSpec 的翻译只写一次。** `style` 里有 `font_sans` 家族链
   与 `type_scale` 的字号/行高，`text/` 的每个调用点都手写这个翻译，
   早晚会有一处写错族顺序或漏掉字号。
2. **缓存要有归属。** 度量是全库最频繁的调用，缓存必须有明确的生命周期
   （DPI 变化、主题切换、字体注册表变化时清空）。散落在调用点的
   `lru_cache` 没法统一失效。
3. **同源守门。** 本模块**只持有 `MetricsProvider`**，不持有任何平台句柄。
   这是 docs/04 §3 在代码结构上的保证点：拿不到平台 API，就写不出第二套度量。

关于字体回退：本模块负责"家族链的**存在性**探测与解析"，
`fallback.py` 负责"**逐字符**归到哪个字体"。前者是"系统里有没有这个字体"，
后者是"这个汉字该用哪个已存在的字体画"。职责不同，别混。

状态：已实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..backend.fonts import (
    FontMetricsError,
    FontSlant,
    FontSpec,
    FontWeight,
    GlyphRun,
    MetricsProvider,
    TextMetrics,
)

__all__ = [
    "FontRegistry",
    "FontResolver",
    "FontSlant",
    "FontWeight",
    "ResolvedTextStyle",
    "TextStyle",
]


@dataclass(frozen=True, slots=True)
class TextStyle:
    """一段文本的样式：字体链 + 字号 + 字重 + 倾斜 + 行高。

    **行高在这里，不在字体度量里。** docs/04 §5 明确说"行高来自字体排印令牌，
    不是字体度量的默认值"——字体的自然行高（ascent+descent）通常小于
    UI 想要的呼吸感，直接用会让中文行距挤成一团。

    `families` 是优先级链，构造时应当来自主题令牌（`theme.font_sans`），
    而不是在组件里手写。
    """

    families: tuple[str, ...]
    size: float
    weight: FontWeight = FontWeight.REGULAR
    slant: FontSlant = FontSlant.NORMAL
    #: 行高倍数（相对字号）。1.6 是中文正文的舒适值。
    line_height: float = 1.6

    def __post_init__(self) -> None:
        if not self.families:
            raise FontMetricsError("TextStyle.families 不能为空：至少要有一个字体族")
        if self.size <= 0:
            raise FontMetricsError(f"字号必须为正，收到 {self.size!r}")
        if self.line_height <= 0:
            raise FontMetricsError(f"行高倍数必须为正，收到 {self.line_height!r}")

    @property
    def spec(self) -> FontSpec:
        """转成后端认识的字体规格。"""
        return FontSpec(
            families=self.families,
            size=self.size,
            weight=self.weight,
            slant=self.slant,
        )

    @property
    def line_height_px(self) -> float:
        """行高（逻辑像素）。段落排版用它，不用字体的自然行高。"""
        return self.size * self.line_height

    def with_size(self, size: float) -> TextStyle:
        """派生出仅换字号的样式（不建议这样用——请用令牌里的具名档位）。"""
        return TextStyle(
            families=self.families,
            size=size,
            weight=self.weight,
            slant=self.slant,
            line_height=self.line_height,
        )

    def bold(self) -> TextStyle:
        """加粗。注意：字重变化会改变宽度，**必须重新布局**。"""
        return TextStyle(
            families=self.families,
            size=self.size,
            weight=FontWeight.BOLD,
            slant=self.slant,
            line_height=self.line_height,
        )


@dataclass(frozen=True, slots=True)
class ResolvedTextStyle:
    """`TextStyle` 经后端解析后的结果：真实字体族 + 全部度量常量。

    `resolved_family` 是真正选中的字体。若它不等于请求链里的任何一个，
    说明用户想要的字体没装、走了兜底——检查器据此提示。
    """

    style: TextStyle
    resolved_family: str
    #: 全角字符宽度（通常等于字号）。
    em: float
    #: 基线以上 / 以下（逻辑像素）。
    ascent: float
    descent: float

    @property
    def is_fallback_family(self) -> bool:
        """请求的字体链一个都没命中，退到了系统默认。"""
        return self.resolved_family not in self.style.families

    @property
    def natural_height(self) -> float:
        """字体自身的自然高度（不等于排版行高，见 `TextStyle` 说明）。"""
        return self.ascent + self.descent

    @property
    def line_height(self) -> float:
        return self.style.line_height_px


class FontResolver:
    """字体解析与度量的**唯一下游**。持有 `MetricsProvider`，不持有平台句柄。

    这是"度量同源"的守门人：全库想量字只能经过这里，
    而这里只能经过后端——两条路都指向后端那一个实现。
    """

    def __init__(self, metrics: MetricsProvider) -> None:
        self._metrics = metrics
        self._style_cache: dict[TextStyle, ResolvedTextStyle] = {}
        self._family_probe: dict[str, bool] = {}
        self._glyph_probe: dict[tuple[str, str], bool] = {}

    # ------------------------------------------------------------ 解析

    def resolve_style(self, style: TextStyle) -> ResolvedTextStyle:
        """解析样式：选定真实字体族，并缓存它的度量常量。"""
        hit = self._style_cache.get(style)
        if hit is not None:
            return hit

        face = self._metrics.resolve_font(style.spec)
        # 解析出的字体参与后续度量：用解析后的族链测量，保证与绘制一致
        resolved_spec = FontSpec(
            families=(face.resolved_family,),
            size=style.size,
            weight=style.weight,
            slant=style.slant,
        )
        probe = self._metrics.measure_text("中", resolved_spec)
        em = probe.advance[0] if probe.advance else style.size

        out = ResolvedTextStyle(
            style=style,
            resolved_family=face.resolved_family,
            em=em,
            ascent=probe.ascent,
            descent=probe.descent,
        )
        self._style_cache[style] = out
        return out

    def has_family(self, family: str) -> bool:
        """系统里有没有这个字体族。回退链逐级探测时用，结果缓存在本层。"""
        hit = self._family_probe.get(family)
        if hit is None:
            hit = self._metrics.has_family(family)
            self._family_probe[family] = hit
        return hit

    def has_glyph(self, family: str, char: str) -> bool:
        """这个族画不画得出这个字符。回退链的**逐字符覆盖探测**。

        为什么要和 `has_family` 分开：`Segoe UI` 在 Windows 上确实存在，
        但它一个汉字都没有。只看"族存在"的话，中文 run 会被分给 Segoe UI、
        渲染成一排豆腐块——内置后端至少还画占位块，所以这个坑一直没显形。

        缓存按 `(族, 字符)`：回退链对每个脚本只探一个代表字符，
        所以缓存很小；而排版过程中同一对会被反复问到。
        """
        key = (family, char)
        hit = self._glyph_probe.get(key)
        if hit is None:
            hit = self._metrics.has_glyph(family, char)
            self._glyph_probe[key] = hit
        return hit

    # ------------------------------------------------------------ 度量

    def measure(self, text: str, style: TextStyle) -> TextMetrics:
        """测量单行文本。**全库文本测量的唯一入口。**"""
        return self._metrics.measure_text(text, self._spec_for(style))

    def shape(self, text: str, style: TextStyle) -> GlyphRun:
        """整形一行。字形位置与逐簇字体归属由后端给出。"""
        return self._metrics.shape_line(text, self._spec_for(style))

    def advance_of(self, char: str, style: TextStyle) -> float:
        """单个字素簇的宽度。断行时高频调用。

        单独开这个方法而不是让调用方 `measure(char).width`，
        语义更清楚，也让缓存键更小。断行是 O(字符数) 的热路径。
        """
        return self.measure(char, style).width

    def _spec_for(self, style: TextStyle) -> FontSpec:
        """统一的"样式 → 后端规格"翻译。

        全部度量都经这里转一次，所以"用哪个字体量"只有一个答案，
        不会出现"断行时用链、绘制时用解析结果"这种漂移。
        """
        resolved = self.resolve_style(style)
        return FontSpec(
            families=(resolved.resolved_family,),
            size=style.size,
            weight=style.weight,
            slant=style.slant,
        )

    # ------------------------------------------------------------ 生命周期

    def clear_caches(self) -> None:
        """清空解析与探测缓存。

        何时调用：DPI 缩放变化导致度量基准变化、主题切换导致字体链变化、
        或是测试里要在同一进程内换度量表。**不调用的后果是界面用错字体度量**，
        比"多量几次"糟糕得多。
        """
        self._style_cache.clear()
        self._family_probe.clear()


class FontRegistry:
    """字体家族的**注册与查询**中心。

    与 `FontResolver` 的分工：
    - `FontRegistry` 回答"这个族名可用吗、它的兜底是谁"（静态信息，跨主题共享）；
    - `FontResolver` 回答"这段文字多宽"（动态度量，与样式绑定）。

    为什么要独立一层：用户可能注册自定义字体（Phase 2 的字体加载），
    注册表变化时必须让 resolver 的探测缓存失效；两者生命周期不同。
    """

    def __init__(self, metrics: MetricsProvider) -> None:
        self._metrics = metrics
        self._registered: dict[str, tuple[str, ...]] = {}
        self._aliases: dict[str, str] = {}

    def register(self, family: str, *, fallbacks: tuple[str, ...] = ()) -> None:
        """注册字体族。`fallbacks` 是该族的首选回退链（如中文字体链）。"""
        if not family.strip():
            raise FontMetricsError("字体族名不能为空")
        self._registered[family] = fallbacks

    def alias(self, alias_name: str, target: str) -> None:
        """给字体族起别名（`system-ui` → 平台实际字体）。"""
        self._aliases[alias_name] = target

    def resolve_chain(self, desired: tuple[str, ...]) -> tuple[str, ...]:
        """把"想要的族链"过滤成"系统里真有的族链"。

        这是回退链的**第一步**：去掉不存在的族，避免每次度量都在后端
        反复探测。通用名（`sans-serif` 等）永远视为存在——它们是链的终点。
        """
        out: list[str] = []
        for family in desired:
            real = self._aliases.get(family, family)
            if self._metrics.has_family(real) and real not in out:
                out.append(real)
            # 注册过的族，把它声明的回退链也接上
            for fallback in self._registered.get(real, ()):
                if self._metrics.has_family(fallback) and fallback not in out:
                    out.append(fallback)
        if not out:
            # 一个都没有也要能跑：通用名是最后兜底
            out.append("sans-serif")
        return tuple(out)

    @property
    def families(self) -> tuple[str, ...]:
        """已注册的族（不含别名）。检查器用来展示"你注册了什么"。"""
        return tuple(self._registered)
