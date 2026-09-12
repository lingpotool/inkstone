"""组件变体配方（docs/13 §8）——令牌层的第三层。

前两层（raw / semantic）在 `tokens.py`，这一层把它们组合成**组件能直接用的样式**：

    button-primary-bg、input-border-focus、card-padding …

组件里绝不允许出现 `Color.from_hex("#4F46E5")` 这种东西——
它只能说"我要 primary 变体的背景"，然后由这里算出具体颜色。
**改一处令牌，全盘变**，这就是换肤不用改业务代码的机制。

配方按 docs/13 §8 的 Button 定义：

    variant: primary | secondary | outline | ghost | danger | link
    size:    xs | sm | md | lg | xl
    state:   八态（见 resolve.ComponentState）

一条刻意的克制：**danger 变体不用"实心红"。** 语义令牌只定义了危险色的
"浅底 + 深字"这一对，没有 solid 版本。硬造一个实心红等于发明新值，
而且它在暗色主题下必然翻车。所以 danger 按钮 = 危险浅底 + 危险深字，
hover/active 用**同一对令牌反转**表达反馈——不发明第三种颜色，
在明暗两版都天然满足对比度（对比度对互换对称）。

状态配方的对称性（R6.3）：每个变体的 hover / active / selected 都有
**明确的视觉反馈**——修复前非 PRIMARY 变体 hover 一律给中性灰，
DANGER 悬停时危险浅底消失变灰、SECONDARY 的 hover 与默认毫无差别，
都属于"配方自相矛盾"。每个配方由对比度测试兜底（WCAG 2.2 AA）。

状态：Button / Input 配方已实现；其余组件随最小组件集推进。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..gfx.color import Color
from .resolve import FOCUS_RING_WIDTH, UNSET, ComponentState, resolve
from .theme import Theme
from .tokens import FontSize

__all__ = [
    "ButtonStyle",
    "ButtonVariant",
    "InputStyle",
    "resolve_button_style",
    "resolve_input_style",
]

# 透明色：用于 ghost / outline / link 这类"没有背景"的变体
TRANSPARENT = Color(0, 0, 0, 0.0)


class ButtonVariant(Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    OUTLINE = "outline"
    GHOST = "ghost"
    DANGER = "danger"
    LINK = "link"


@dataclass(frozen=True, slots=True)
class ButtonStyle:
    """Button 的最终样式。所有字段都来自令牌，没有一个是字面量颜色。"""

    bg: Color
    fg: Color
    border: Color
    border_width: float
    radius: float
    height: float
    padding_h: float
    font: FontSize
    focus_ring: Color
    focus_ring_width: float

    @property
    def is_transparent_bg(self) -> bool:
        return self.bg.a == 0.0


@dataclass(frozen=True, slots=True)
class InputStyle:
    """Input 的最终样式。"""

    bg: Color
    fg: Color
    placeholder: Color
    border: Color
    border_width: float
    radius: float
    height: float
    padding_h: float
    font: FontSize
    focus_ring: Color
    focus_ring_width: float


# 各 size 对应的字号档位（docs/13 §3.1 的 7 档阶梯）
_SIZE_FONT = {"xs": "xs", "sm": "sm", "md": "md", "lg": "lg", "xl": "lg"}

# 各 size 对应的水平内边距令牌名
_SIZE_PADDING = {"xs": "sm", "sm": "md", "md": "lg", "lg": "lg", "xl": "xl"}


def _variant_colors(theme: Theme, variant: ButtonVariant) -> dict[str, Color]:
    """变体配方：给出该变体在 DEFAULT 状态下的底色 / 字色 / 描边。"""
    s = theme.semantic
    table = {
        ButtonVariant.PRIMARY: {"bg": s.primary, "fg": s.on_primary, "border": s.primary},
        ButtonVariant.SECONDARY: {"bg": s.surface_alt, "fg": s.text, "border": s.border},
        ButtonVariant.OUTLINE: {"bg": TRANSPARENT, "fg": s.primary, "border": s.border_strong},
        ButtonVariant.GHOST: {"bg": TRANSPARENT, "fg": s.text_dim, "border": TRANSPARENT},
        ButtonVariant.DANGER: {"bg": s.danger_bg, "fg": s.danger_text, "border": s.danger_text},
        ButtonVariant.LINK: {"bg": TRANSPARENT, "fg": s.primary, "border": TRANSPARENT},
    }
    return table[variant]


def _state_recipe(theme: Theme, variant: ButtonVariant, state: ComponentState) -> dict[str, object]:
    """逐变体的状态配方（R6.3）：每个变体的 hover / active / selected 都有明确反馈。

    反面教材（修复前）：非 PRIMARY 变体 hover 一律给中性灰——
    DANGER 悬停时危险浅底消失变灰，SECONDARY 的 hover 与默认毫无差别。

    SELECTED 统一用 `primary-soft + primary`（danger 用危险对反转）：
    暗色主题的 `primary_press` 是中调亮色，配 `on_primary` 只有 3.6:1，
    达不到 AA——这条是写对比度测试时被抓出来的，不是看出来的。
    """
    s = theme.semantic
    if state is ComponentState.HOVER:
        hover: dict[ButtonVariant, dict[str, object]] = {
            # 主按钮：品牌色加深（暗色主题里 hover/press 令牌是"更亮"，由令牌管方向）
            ButtonVariant.PRIMARY: {"bg": s.primary_hover, "border": s.primary_hover},
            # 次级：中性色阶往下走一档（bg 本来就是 surface_alt，原配方 hover 无反馈）
            ButtonVariant.SECONDARY: {"bg": s.border, "border": s.border_strong},
            # 描边钮：淡品牌底色浮出
            ButtonVariant.OUTLINE: {"bg": s.primary_soft, "border": s.primary},
            # 幽灵钮：浮出中性底，字色提一档
            ButtonVariant.GHOST: {"bg": s.surface_alt, "fg": s.text},
            # 危险钮：同一对令牌反转，不发明第三种颜色
            ButtonVariant.DANGER: {"bg": s.danger_text, "fg": s.danger_bg},
            # 链接：只有字色变化
            ButtonVariant.LINK: {"fg": s.primary_hover},
        }
        return hover[variant]
    if state is ComponentState.ACTIVE:
        active: dict[ButtonVariant, dict[str, object]] = {
            ButtonVariant.PRIMARY: {"bg": s.primary_press, "border": s.primary_press},
            ButtonVariant.SECONDARY: {"bg": s.border_strong, "border": s.border_strong},
            ButtonVariant.OUTLINE: {"bg": s.primary_soft, "border": s.primary_press},
            ButtonVariant.GHOST: {"bg": s.border, "fg": s.text},
            # 按下时描边并入底色：反转底上"边框消失"就是按进去的观感
            ButtonVariant.DANGER: {
                "bg": s.danger_text,
                "fg": s.danger_bg,
                "border": s.danger_bg,
            },
            ButtonVariant.LINK: {"fg": s.primary_press},
        }
        return active[variant]
    if state is ComponentState.SELECTED:
        if variant is ButtonVariant.DANGER:
            return {"bg": s.danger_text, "fg": s.danger_bg}
        return {"bg": s.primary_soft, "fg": s.primary}
    return {}


def resolve_button_style(
    theme: Theme,
    *,
    variant: ButtonVariant = ButtonVariant.PRIMARY,
    size: str = "md",
    state: ComponentState = ComponentState.DEFAULT,
    overrides: dict[str, object] | None = None,
) -> ButtonStyle:
    """按 变体 / 尺寸 / 状态 解析出 Button 的最终样式。"""
    s = theme.semantic
    colors = _variant_colors(theme, variant)

    # 状态层：逐变体配方（hover/active/selected）+ 与变体无关的状态
    states: dict[ComponentState, dict[str, object]] = {
        ComponentState.DEFAULT: {},
        ComponentState.HOVER: _state_recipe(theme, variant, ComponentState.HOVER),
        ComponentState.ACTIVE: _state_recipe(theme, variant, ComponentState.ACTIVE),
        ComponentState.SELECTED: _state_recipe(theme, variant, ComponentState.SELECTED),
        ComponentState.DISABLED: {
            "bg": s.surface_alt,
            "fg": s.text_faint,
            "border": s.border,
        },
        # loading 保持变体外观，只是不响应输入（由组件决定是否画 spinner）
        ComponentState.LOADING: {},
        ComponentState.ERROR: {
            "bg": s.danger_bg,
            "fg": s.danger_text,
            "border": s.danger_text,
        },
        ComponentState.FOCUS_VISIBLE: {},
    }

    merged = resolve(
        defaults={
            "bg": colors["bg"],
            "fg": colors["fg"],
            "border": colors["border"],
            "border_width": theme.border_width("hairline"),
            "radius": theme.radius("md"),
            "height": theme.control_height(size),
            "padding_h": theme.space(_SIZE_PADDING[size]),
            "font": theme.font_size(_SIZE_FONT[size]),
            "focus_ring": s.focus_ring,
            "focus_ring_width": 0.0,
        },
        variant={
            "border_width": 0.0 if variant in (ButtonVariant.GHOST, ButtonVariant.LINK) else UNSET
        },
        overrides=overrides,
        state=states[state],
    )

    # 焦点环只在 focus-visible 时画出来（鼠标点击不显示，docs/13 §5）
    if state is ComponentState.FOCUS_VISIBLE:
        merged["focus_ring_width"] = FOCUS_RING_WIDTH

    return ButtonStyle(**merged)


def resolve_input_style(
    theme: Theme,
    *,
    size: str = "md",
    state: ComponentState = ComponentState.DEFAULT,
    overrides: dict[str, object] | None = None,
) -> InputStyle:
    """按 尺寸 / 状态 解析出 Input 的最终样式。"""
    s = theme.semantic

    states: dict[ComponentState, dict[str, object]] = {
        ComponentState.DEFAULT: {},
        ComponentState.HOVER: {"border": s.border_strong},
        ComponentState.ACTIVE: {"border": s.primary},
        ComponentState.FOCUS_VISIBLE: {"border": s.primary},
        ComponentState.DISABLED: {
            "bg": s.surface_alt,
            "fg": s.text_faint,
            "border": s.border,
        },
        ComponentState.ERROR: {"border": s.danger_text},
        ComponentState.LOADING: {},
        ComponentState.SELECTED: {},
    }

    merged = resolve(
        defaults={
            "bg": s.surface_alt,
            "fg": s.text,
            "placeholder": s.text_faint,
            "border": s.border,
            "border_width": theme.border_width("hairline"),
            "radius": theme.radius("md"),
            "height": theme.control_height(size),
            "padding_h": theme.space("md"),
            "font": theme.font_size("md"),
            "focus_ring": s.focus_ring,
            "focus_ring_width": 0.0,
        },
        overrides=overrides,
        state=states[state],
    )

    if state is ComponentState.FOCUS_VISIBLE:
        merged["focus_ring_width"] = FOCUS_RING_WIDTH

    return InputStyle(**merged)
