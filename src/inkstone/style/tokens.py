"""设计令牌 —— "精致"的唯一真相源（docs/13）。

三层结构，一层比一层具体：

    基础令牌 raw         色板 12 级、字号阶梯、8pt 间距网格、圆角、阴影、动效
     │                   —— **永远不许在组件里直接使用**
     ▼
    语义令牌 semantic    surface / text / primary / border / focus-ring …
     │                   —— 明暗主题的映射发生在这一层
     ▼
    组件令牌 component   button-primary-bg、input-border-focus …
                        —— Phase 1 最小组件集时引入

为什么这么较真：**任何组件不许发明新值。** 设计的敌人不是"不够好看"，
是"60 个组件各自为政"。颜色只有语义那一小撮、间距只有那一格、
圆角只有那一档——改令牌全盘变，这就是换肤不用改业务代码的机制。

状态：raw + semantic 已实现；组件令牌待最小组件集引入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..gfx.color import Color

__all__ = [
    "DARK_SEMANTIC",
    "DEFAULT_RAW",
    "LIGHT_SEMANTIC",
    "RAMP_STEPS",
    "CubicBezier",
    "FontSize",
    "RawTokens",
    "SemanticTokens",
    "ShadowSpec",
    "SpringSpec",
    "contrast_issues",
]

# 色板 12 级：50 最浅，950 最深（docs/13 §2.1）
RAMP_STEPS: tuple[str, ...] = (
    "50",
    "100",
    "150",
    "200",
    "300",
    "400",
    "500",
    "600",
    "700",
    "800",
    "900",
    "950",
)


def _ramp(values: tuple[str, ...]) -> dict[str, Color]:
    """从 12 个 hex 值构造色板，键数量不对当场报错。"""
    if len(values) != len(RAMP_STEPS):
        raise ValueError(
            f"色板需要 {len(RAMP_STEPS)} 级（{RAMP_STEPS[0]}–{RAMP_STEPS[-1]}），"
            f"收到 {len(values)} 级"
        )
    return {
        step: Color.from_hex(hex_value) for step, hex_value in zip(RAMP_STEPS, values, strict=True)
    }


# ---------------------------------------------------------------- 数据类型


@dataclass(frozen=True, slots=True)
class FontSize:
    """字号 + 行高（倍数）。中文正文行高低于 1.4 会挤（docs/13 §3.1）。"""

    px: int
    line_height: float

    @property
    def line_height_px(self) -> float:
        return self.px * self.line_height


@dataclass(frozen=True, slots=True)
class ShadowSpec:
    """一级阴影。画"环境色影"不画黑影：颜色带透明度，由令牌给出。"""

    offset_y: float
    blur: float
    spread: float = 0.0
    color: Color = field(default_factory=lambda: Color(0, 0, 0, 0.15))


@dataclass(frozen=True, slots=True)
class CubicBezier:
    """三次贝塞尔缓动。"""

    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class SpringSpec:
    """弹簧物理参数（可中断的动效用它，不用时长）。"""

    mass: float = 1.0
    stiffness: float = 170.0
    damping: float = 26.0


# ---------------------------------------------------------------- 基础令牌


@dataclass(frozen=True, slots=True)
class RawTokens:
    """基础层：色板与刻度。组件不许直接碰这一层。"""

    # 色族。中性灰低饱和、微微偏蓝；品牌色默认 Indigo，可整体替换
    neutral: Mapping[str, Color]
    brand: Mapping[str, Color]

    # 间距：8pt 网格，组件内边距与 gap 只能从这里取（docs/13 §4）
    spacing: Mapping[str, float]

    # 圆角档位。同族组件同档；小控件不超过 md
    radius: Mapping[str, float]

    # 字号阶梯（docs/13 §3.1）
    type_scale: Mapping[str, FontSize]

    # 字重只有两档——禁止第三档，重用色彩与字号区分（docs/13 §3.2）
    weight_regular: int
    weight_medium: int

    # 字体栈，按平台回退；等宽给代码与数字
    font_sans: tuple[str, ...]
    font_mono: tuple[str, ...]

    # 控件高度阶梯：所有可交互控件必须从这一列取值，保证表单对齐
    control_heights: Mapping[str, float]

    # 描边宽度档位。边框、分隔线、焦点环都从这里取——
    # "1px 还是 2px"是设计决定，不该散落在组件里当字面量。
    border_widths: Mapping[str, float]

    # 阴影 e0–e4（docs/13 §5）
    shadows: Mapping[str, ShadowSpec | None]

    # 动效时长（ms）。超过 500ms = 审查打回（docs/13 §6.1）
    motion_durations: Mapping[str, float]

    # 缓动曲线与弹簧
    easings: Mapping[str, CubicBezier | SpringSpec]

    # 手势识别参数。8px 阈值、长按/双击时窗都是**设计决定**，
    # 散落在识别器里当字面量就没人能一次改全（docs/21 R7.2）。
    gestures: Mapping[str, float]

    # 文本编辑装饰：光标宽度、组合态下划线宽度与下移量。
    # 这些是**排版细节**，但同样是设计决定（docs/13 §3），组件里不许写字面量。
    decorations: Mapping[str, float]

    # 滚动条几何：粗细、最小拇指长度、圆角、离视口边缘的距离。
    # 颜色走语义色（`scrollbar` / `scrollbar-hover`），几何走这里——
    # 两处分开是因为颜色要参与明暗主题与对比度检查，几何不用。
    scrollbar: Mapping[str, float]


_PILL = float("inf")

DEFAULT_RAW = RawTokens(
    neutral=_ramp(
        (
            "#F8FAFC",
            "#F1F5F9",
            "#E9EFF6",
            "#E2E8F0",
            "#CBD5E1",
            "#94A3B8",
            "#64748B",
            "#475569",
            "#334155",
            "#1E293B",
            "#0F172A",
            "#020617",
        )
    ),
    brand=_ramp(
        (
            "#EEF2FF",
            "#E0E7FF",
            "#D3DCFE",
            "#C7D2FE",
            "#A5B4FC",
            "#818CF8",
            "#6366F1",
            "#4F46E5",
            "#4338CA",
            "#3730A3",
            "#312E81",
            "#1E1B4B",
        )
    ),
    spacing={
        "0": 0.0,
        "xs": 4.0,
        "sm": 8.0,
        "md": 12.0,
        "lg": 16.0,
        "xl": 24.0,
        "2xl": 32.0,
        "3xl": 48.0,
        "4xl": 64.0,
    },
    radius={"sm": 6.0, "md": 10.0, "lg": 14.0, "xl": 20.0, "pill": _PILL},
    type_scale={
        "xs": FontSize(11, 1.5),
        "sm": FontSize(12, 1.55),
        "md": FontSize(14, 1.6),
        "lg": FontSize(16, 1.55),
        "xl": FontSize(20, 1.4),
        "2xl": FontSize(24, 1.3),
        "3xl": FontSize(30, 1.25),
    },
    weight_regular=400,
    weight_medium=500,
    font_sans=("Inter", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "system-ui"),
    font_mono=("JetBrains Mono", "Cascadia Code", "Consolas", "monospace"),
    control_heights={"xs": 24.0, "sm": 28.0, "md": 36.0, "lg": 44.0, "xl": 52.0},
    border_widths={"hairline": 1.0, "thick": 2.0},
    shadows={
        "e0": None,
        "e1": ShadowSpec(1.0, 3.0, 0.0, Color.from_hex("#0F172A").with_alpha(0.08)),
        "e2": ShadowSpec(4.0, 12.0, 0.0, Color.from_hex("#0F172A").with_alpha(0.10)),
        "e3": ShadowSpec(8.0, 24.0, 0.0, Color.from_hex("#0F172A").with_alpha(0.14)),
        "e4": ShadowSpec(16.0, 40.0, 0.0, Color.from_hex("#0F172A").with_alpha(0.20)),
    },
    motion_durations={
        "micro": 75.0,  # 微交互
        "hover": 150.0,  # 悬停
        "enter": 200.0,  # 进入
        "exit": 300.0,  # 离开 / 大位移
        "page": 500.0,  # 页面级，上限
    },
    easings={
        "ease-out-expo": CubicBezier(0.16, 1.0, 0.3, 1.0),
        "ease-out-quart": CubicBezier(0.25, 1.0, 0.5, 1.0),
        "ease-in-quart": CubicBezier(0.5, 0.0, 0.75, 0.0),
        "ease-in-out-quart": CubicBezier(0.76, 0.0, 0.24, 1.0),
        "spring-default": SpringSpec(1.0, 170.0, 26.0),
        "spring-gentle": SpringSpec(1.0, 120.0, 20.0),
    },
    gestures={
        # 触摸滑动容差：按下后移动不超过它，抬起仍算 tap（超过则让位给滚动/拖拽）。
        # 8px 是触摸屏上"手抖但不构成滑动"的经验分界（docs/06 §5）。
        "tap_slop": 8.0,
        # 长按判定时长与双击第二击时窗。
        "long_press_ms": 500.0,
        "double_tap_ms": 300.0,
        # 鼠标滚轮一格滚多少**逻辑像素**。Windows 默认"一次滚 3 行"，
        # 正文行高约 16px → 48px。SDK 给的是格数，换算成距离要有统一口径，
        # 否则各处的滚动速度会各说各话（docs/13 §3）。
        "wheel_step": 48.0,
    },
    decorations={
        # 光标竖线宽度（1px 是文本输入的通用观感）
        "caret_width": 1.0,
        # 组合态下划线：线宽 + 相对文本框底部的下移量
        "underline_width": 1.5,
        "underline_offset": 2.0,
    },
    scrollbar={
        # 覆盖层两档宽度：静止 10px（Chromium overlay 的档位），悬停/拖拽 14px。
        # 覆盖式**不占布局宽度**，所以变粗不会推动内容（见 ADR-0026）。
        "thickness": 10.0,
        "hover_thickness": 14.0,
        # 拇指最短长度：内容再长也要留出可抓的一段，否则长列表的拇指细如发丝。
        "min_thumb": 32.0,
        # 圆角取宽度一半 = 胶囊形。
        "radius": 5.0,
        # 离视口右/下边缘的留白，让拇指看起来是"浮"在上面的。
        "margin": 2.0,
        # 闲置多久开始淡出、淡出用多久。900ms 是 VS Code / Flutter 的档位：
        # 短了会闪、长了像常驻；淡出 250ms 刚好"看得见地消失"而不拖沓。
        "hold_ms": 900.0,
        "fade_ms": 250.0,
    },
)


# ---------------------------------------------------------------- 语义令牌


@dataclass(frozen=True, slots=True)
class SemanticTokens:
    """语义层：组件唯一允许直接使用的颜色词汇表。

    暗色模式不是反色，是**重新映射**（docs/13 §2.3）：
    底越深、面越亮（surface 比 bg 亮），层级靠文字色阶保持。
    """

    # 表面
    bg: Color
    surface: Color
    surface_alt: Color

    # 描边
    border: Color
    border_strong: Color

    # 文字三级
    text: Color
    text_dim: Color
    text_faint: Color

    # 品牌五态
    primary: Color
    primary_hover: Color
    primary_press: Color
    primary_soft: Color
    on_primary: Color

    # 焦点环（键盘导航专用，focus-visible 语义）
    focus_ring: Color

    # 滚动条拇指：静止 / 悬停与拖拽。带一点透明度是为了不与内容抢视觉，
    # 但它画在**专用槽位**里、不压在条目上（见 ADR-0026）。
    scrollbar: Color
    scrollbar_hover: Color

    # 状态语义：各配 浅底 + 深字
    success_bg: Color
    success_text: Color
    warning_bg: Color
    warning_text: Color
    danger_bg: Color
    danger_text: Color
    info_bg: Color
    info_text: Color

    # 金融语义：默认遵守中文惯例（涨红跌绿），可被西方习惯主题覆盖
    market_up: Color
    market_down: Color


def _hex(value: str) -> Color:
    return Color.from_hex(value)


LIGHT_SEMANTIC = SemanticTokens(
    bg=_hex("#F8FAFC"),
    surface=_hex("#FFFFFF"),
    surface_alt=_hex("#F1F5F9"),
    border=_hex("#E2E8F0"),
    border_strong=_hex("#CBD5E1"),
    text=_hex("#0F172A"),
    text_dim=_hex("#475569"),
    text_faint=_hex("#64748B"),
    primary=_hex("#4F46E5"),
    primary_hover=_hex("#4338CA"),
    primary_press=_hex("#3730A3"),
    primary_soft=_hex("#E0E7FF"),
    on_primary=_hex("#FFFFFF"),
    focus_ring=_hex("#6366F1"),
    # 对比度检查不覆盖它们（滚动条不是文字）；半透明让它在浅底/深底上都协调。
    scrollbar=Color.from_rgba(100, 116, 139, 0.55),
    scrollbar_hover=Color.from_rgba(71, 85, 105, 0.80),
    success_bg=_hex("#ECFDF5"),
    success_text=_hex("#065F46"),
    warning_bg=_hex("#FFFBEB"),
    warning_text=_hex("#92400E"),
    danger_bg=_hex("#FEF2F2"),
    danger_text=_hex("#991B1B"),
    info_bg=_hex("#EFF6FF"),
    info_text=_hex("#1E40AF"),
    market_up=_hex("#DC2626"),
    # 注意：不能用 green-600（#16A34A），它对白底只有 3.3:1，正文不达 AA。
    # green-700 实测 4.8:1。这条注释就是"对比度要算不要看"的证据。
    market_down=_hex("#15803D"),
)

DARK_SEMANTIC = SemanticTokens(
    bg=_hex("#020617"),
    surface=_hex("#0F172A"),
    surface_alt=_hex("#1E293B"),
    border=_hex("#334155"),
    border_strong=_hex("#475569"),
    text=_hex("#F1F5F9"),
    text_dim=_hex("#CBD5E1"),
    text_faint=_hex("#94A3B8"),
    primary=_hex("#818CF8"),
    primary_hover=_hex("#A5B4FC"),
    # 暗色里"按得更深"=更亮（docs/13 §2.3：暗色是重新映射，不是反色）。
    # 不能沿用品牌 600（#6366F1）：它是中调，配 on_primary 只有 3.6:1，不达 AA——
    # 这条是 R6.3 的全变体×全状态对比度测试抓出来的，不是看出来的。
    primary_press=_hex("#C7D2FE"),
    primary_soft=_hex("#1E1B4B"),
    on_primary=_hex("#1E1B4B"),
    focus_ring=_hex("#A5B4FC"),
    # 暗色下拇指要**更亮**（深底上的浅灰），这是重新映射不是反色。
    scrollbar=Color.from_rgba(148, 163, 184, 0.50),
    scrollbar_hover=Color.from_rgba(203, 213, 225, 0.75),
    success_bg=_hex("#064E3B"),
    success_text=_hex("#A7F3D0"),
    warning_bg=_hex("#78350F"),
    warning_text=_hex("#FDE68A"),
    danger_bg=_hex("#7F1D1D"),
    danger_text=_hex("#FECACA"),
    info_bg=_hex("#1E3A8A"),
    info_text=_hex("#BFDBFE"),
    market_up=_hex("#F87171"),
    market_down=_hex("#4ADE80"),
)


# ---------------------------------------------------------------- 校验


def contrast_issues(semantic: SemanticTokens) -> list[str]:
    """检查全部语义令牌是否达到 WCAG 2.2 AA（docs/13 §9 的自动化检查）。

    返回违规清单；空列表 = 通过。这条检查要进 CI——
    让"对比度不达标"在提交时就失败，而不是上线后被用户发现看不清。
    """
    issues: list[str] = []

    text_pairs = [
        ("text", semantic.text),
        ("text-dim", semantic.text_dim),
        ("text-faint", semantic.text_faint),
        ("market-up", semantic.market_up),
        ("market-down", semantic.market_down),
    ]
    for name, color in text_pairs:
        for surface_name, surface in (("bg", semantic.bg), ("surface", semantic.surface)):
            if not color.meets_aa(surface):
                issues.append(
                    f"{name} 对 {surface_name} 的对比度 "
                    f"{color.contrast_ratio(surface):.2f}:1，正文需要 ≥ 4.5:1"
                )

    on_color_pairs = [
        ("on-primary", semantic.on_primary, semantic.primary),
        ("primary", semantic.primary, semantic.primary_soft),
        ("success-text", semantic.success_text, semantic.success_bg),
        ("warning-text", semantic.warning_text, semantic.warning_bg),
        ("danger-text", semantic.danger_text, semantic.danger_bg),
        ("info-text", semantic.info_text, semantic.info_bg),
    ]
    for name, color, background in on_color_pairs:
        if not color.meets_aa(background):
            issues.append(
                f"{name} 对其底色的对比度 {color.contrast_ratio(background):.2f}:1，需要 ≥ 4.5:1"
            )

    # 焦点环是"非文本 UI 组件"，AA 1.4.11 要求 ≥ 3:1
    for surface_name, surface in (("bg", semantic.bg), ("surface", semantic.surface)):
        ratio = semantic.focus_ring.contrast_ratio(surface)
        if ratio < 3.0:
            issues.append(
                f"focus-ring 对 {surface_name} 的对比度 {ratio:.2f}:1，非文本组件需要 ≥ 3:1"
            )

    return issues
