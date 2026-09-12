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
在明暗两版都天然满足对比度。

状态：Button / Input 配方已实现；其余组件随最小组件集推进。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..gfx.color import Color
from .resolve import FOCUS_RING_WIDTH, ComponentState, resolve
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

    # 状态层：与"默认"不同的槽位才写
    states: dict[ComponentState, dict[str, object]] = {
        ComponentState.DEFAULT: {},
        ComponentState.HOVER: {
            "bg": s.primary_hover if variant is ButtonVariant.PRIMARY else s.surface_alt,
            "border": s.primary_hover if variant is ButtonVariant.PRIMARY else s.border_strong,
        },
        ComponentState.ACTIVE: {
            "bg": s.primary_press if variant is ButtonVariant.PRIMARY else s.border,
            "border": s.primary_press if variant is ButtonVariant.PRIMARY else s.border_strong,
        },
        ComponentState.SELECTED: {
            "bg": s.primary_soft if variant is not ButtonVariant.PRIMARY else s.primary_press,
            "fg": s.primary if variant is not ButtonVariant.PRIMARY else s.on_primary,
        },
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
            "border_width": 0.0 if variant in (ButtonVariant.GHOST, ButtonVariant.LINK) else None
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
