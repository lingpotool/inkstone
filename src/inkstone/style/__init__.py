"""样式与主题（L6）：令牌驱动、确定性解析。

一句话记住这套规则：

    **组件里出现任何字面量颜色 / 字号 / 间距 = 审查打回。**
    只能用令牌；要加新值，先加进令牌，再使用。

```python
theme = Theme.dark()
card_bg = theme.color("surface")     # 语义色
gap = theme.space("lg")              # 16
radius = theme.radius("md")          # 10
theme.contrast_issues()              # WCAG AA 检查，CI 里必须为空
```

状态：令牌 + 明暗主题已实现；变体解析与组件令牌待最小组件集引入。
"""

from ..gfx.color import Color
from .resolve import FOCUS_RING_OFFSET, FOCUS_RING_WIDTH, UNSET, ComponentState, resolve
from .theme import Theme, ThemeMode, TokenError, default_theme
from .tokens import (
    DARK_SEMANTIC,
    DEFAULT_RAW,
    LIGHT_SEMANTIC,
    RAMP_STEPS,
    CubicBezier,
    FontSize,
    RawTokens,
    SemanticTokens,
    ShadowSpec,
    SpringSpec,
    contrast_issues,
)
from .variants import (
    ButtonStyle,
    ButtonVariant,
    InputStyle,
    resolve_button_style,
    resolve_input_style,
)

__all__ = [
    "DARK_SEMANTIC",
    "DEFAULT_RAW",
    "FOCUS_RING_OFFSET",
    "FOCUS_RING_WIDTH",
    "LIGHT_SEMANTIC",
    "RAMP_STEPS",
    "UNSET",
    "ButtonStyle",
    "ButtonVariant",
    "Color",
    "ComponentState",
    "CubicBezier",
    "FontSize",
    "InputStyle",
    "RawTokens",
    "SemanticTokens",
    "ShadowSpec",
    "SpringSpec",
    "Theme",
    "ThemeMode",
    "TokenError",
    "contrast_issues",
    "default_theme",
    "resolve",
    "resolve_button_style",
    "resolve_input_style",
]
