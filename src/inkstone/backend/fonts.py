"""字体度量契约 —— 全库**唯一**的度量源（docs/04 §3 落地处）。

为什么它住在 L0 后端，而不是 `text/`：

    docs/04 §3 的铁律是"测量与绘制必须同一套度量，全库只允许一处实现"。
    如果 `text/` 自己调系统 API 量字、`gfx/` 光栅再量一遍，两套度量必然漂移，
    表现是文字截断、基线抖动、行数与视觉不符——而这类 bug 极难定位。

    度量本身是"平台相关"的事（DirectWrite / CoreText / fontconfig 各一套），
    所以按铁律 1「平台差异不出 L0」，它就**必须**待在后端。
    把度量放进 `Backend` 协议后，"测量"和"绘制"由**同一个后端对象**完成，
    同源就成了结构上的必然，而不是靠纪律维持的巧合。

分层：

    L3 text/  ──面向本模块的协议说话──▶  L0 backend/
    `text/` 里不允许出现 `ctypes` / `windll` / 字体路径 / `sys.platform` 分支。

关于确定性：

    度量结果必须**可缓存且可复现**。无头后端实现的是"度量表"驱动，
    同一串文字在同一字号下永远返回同一组数字，黄金图才谈得上逐字节相等。

状态：已实现（契约层 + 无头实现见 `headless.py`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

__all__ = [
    "FontFace",
    "FontMetricsError",
    "FontSlant",
    "FontSpec",
    "FontWeight",
    "GlyphPlacement",
    "GlyphRun",
    "MetricsProvider",
    "TextMetrics",
]


class FontMetricsError(RuntimeError):
    """字体度量/发现失败。报错必须带上字体名与建议。"""


class FontSlant(Enum):
    """字体的倾斜。与 `FontWeight` 一起构成"字体变体"这条轴。"""

    NORMAL = "normal"
    ITALIC = "italic"


class FontWeight(Enum):
    """字重。用 CSS/OpenType 的标准数值区间，避免各平台命名打架。

    不直接用数字，是因为"Medium 到底是 500 还是 550"在平台上没有共识；
    枚举值经后端映射到该平台的真实字重，映射表在后端里。
    """

    REGULAR = "regular"
    MEDIUM = "medium"
    SEMIBOLD = "semibold"
    BOLD = "bold"


@dataclass(frozen=True, slots=True)
class FontSpec:
    """一次度量请求要用的字体描述。

    `family` 是**优先级列表**（回退链的产物）：后端从前往后挑第一个装了的。
    这就意味着回退决策（`text/fallback.py`）产出的是 family 列表，
    而"这个 family 到底存不存在"由后端回答——分层不越界。

    `size` 是**逻辑像素**的字号（即 UI 上说的"字号 14"），不是磅值。
    """

    families: tuple[str, ...] = ("sans-serif",)
    size: float = 14.0
    weight: FontWeight = FontWeight.REGULAR
    slant: FontSlant = FontSlant.NORMAL

    def __post_init__(self) -> None:
        if not self.families:
            raise FontMetricsError("FontSpec.families 不能为空：至少要有一个字体族或通用名")
        if self.size <= 0:
            raise FontMetricsError(f"字号必须为正，收到 {self.size!r}")

    def with_size(self, size: float) -> FontSpec:
        """派生出同族同字重、仅换字号的新规格（排版的常用动作）。"""
        return FontSpec(families=self.families, size=size, weight=self.weight, slant=self.slant)


@dataclass(frozen=True, slots=True)
class FontFace:
    """后端解析 `FontSpec` 之后的**具体**字体。

    `resolved_family` 是真正选中的那个族名（可能不等于请求里的任何一个，
    因为通用名 `sans-serif` 会落到系统默认）。它用于诊断与检查器展示。
    """

    resolved_family: str
    size: float
    weight: FontWeight = FontWeight.REGULAR
    slant: FontSlant = FontSlant.NORMAL
    #: 后端内部的句柄（如 HFONT / CTFontRef）。上层不许解释它，只许透传。
    handle: int = 0

    @property
    def is_fallback(self) -> bool:
        """是否由通用名兜底解析而来（检查器用来提示"你想要的字体没装"）。"""
        return self.resolved_family.startswith(("sans", "serif", "mono"))


@dataclass(frozen=True, slots=True)
class TextMetrics:
    """一段文本的度量结果。**测量与绘制共用的那组数字。**

    单位统一为逻辑像素。

    - `width`：整段（单行）的推进宽度，即 `advance` 之和；
    - `ascent`：基线以上高度（负号已取掉，值为正）；
    - `descent`：基线以下高度（正值）；
    - `line_gap`：字体建议的行间隙，**排版不直接采用**（行高来自排印令牌，
      见 docs/04 §5），但它影响"字体默认行高"这个诊断值；
    - `advance`：每个**字素簇**的宽度，`len(advance) == 字素簇数`。
      断行与命中测试都靠它，所以它必须按"可见字符"切，不能按码点切
      （emoji 的零宽拼接符、组合音标都要求按字素簇算）。
    """

    width: float
    ascent: float
    descent: float
    line_gap: float = 0.0
    advance: tuple[float, ...] = ()

    @property
    def height(self) -> float:
        """字体自身的自然行高（ascent + descent + line_gap）。

        注意：这是**字体的**行高，不等于排版用的行高。
        排版行高来自令牌，通常大于它。两者混用会让行距忽大忽小。
        """
        return self.ascent + self.descent + self.line_gap


@dataclass(frozen=True, slots=True)
class GlyphPlacement:
    """单个字素簇放置到画布上的位置（相对文本原点的偏移）。"""

    #: 字素簇在源字符串里的起止（Python 下标，`[start, end)`）。
    #: 用下标而不是"第几个字形"，是因为命中测试与选区要回到字符串坐标。
    start: int
    end: int
    #: 相对文本原点的 x 偏移；y 相对**基线**。
    x: float
    y: float
    #: 该字素簇的推进宽度。
    advance: float
    #: 实际承载它的字体族（回退链可能让同一行的字来自不同字体）。
    family: str


@dataclass(frozen=True, slots=True)
class GlyphRun:
    """一行文本整形后的结果：字形位置序列 + 该行度量。

    行是**文本管线的基本单位**：绘制按行提交，选区按行切矩形，
    命中测试先定行再定字。所以整形与断行都产出它。
    """

    text: str
    placements: tuple[GlyphPlacement, ...]
    metrics: TextMetrics
    #: 该行在源字符串中的起止（`[start, end)`），用于选区回映射。
    start: int = 0
    end: int = 0

    @property
    def width(self) -> float:
        return self.metrics.width


class MetricsProvider(Protocol):
    """字体度量的提供方。`Backend` 必须实现它（`Backend` 继承本协议）。

    刻意拆成独立的 Protocol：`text/` 只需要这几个方法，
    不该被迫依赖整个 `Backend`（窗口、剪贴板、事件）。
    依赖最窄接口，测试也好替换。
    """

    def resolve_font(self, spec: FontSpec) -> FontFace:
        """把字体规格解析成具体字体；找不到时按优先级退到系统默认。

        这是"这个族名存在吗"的唯一裁判。**返回值必须可缓存**。
        """

    def has_family(self, family: str) -> bool:
        """系统里是否存在该字体族。回退链用它逐级探测。

        判断必须廉价（实现里应走已扫描的字体表，不每次查盘）。
        """

    def measure_text(self, text: str, spec: FontSpec) -> TextMetrics:
        """测量一段**单行**文本。含换行符时按制表处理（调用方负责先断行）。

        这是全库唯一的文本度量入口。任何"每字 N 像素"式的估算都是 bug。
        """

    def shape_line(self, text: str, spec: FontSpec) -> GlyphRun:
        """把一行文本整形为字形位置序列（含逐簇字体归属）。

        简单脚本下它就是"逐字素簇累加 advance"；复杂脚本
        （阿拉伯语连写、天城文重排）需要真正的 HarfBuzz 级整形——
        那是后端的职责，上层只拿结果，见 docs/04 §2 的 ADR-0005。
        """

    def stats(self) -> dict[str, int]:
        """缓存与资源统计，供检查器与性能测试使用。

        度量是全库调用最频繁的操作，缓存是否生效直接决定帧率。
        要求实现提供这个口子，是为了让"缓存到底有没有命中"可被观测，
        而不是靠猜。
        """
