"""组件库（L7）：由 core 的三棵树 + style 的令牌组合而成。

分层铁律：**组件不许发明新值。** 颜色、字号、间距、圆角一律来自 `Theme`，
组件只表达"我要哪个变体、现在什么状态"。

```python
owner = BuildOwner(theme=Theme.dark())
owner.mount(
    Column(children=[
        Card(child=Box(height=80)),
        Row(children=[
            Button("取消", variant=ButtonVariant.GHOST),
            Flexible(Button("确定"), flex=1),
        ], gap=8),
    ], gap=16)
)
owner.begin_frame(BoxConstraints(max_width=320, max_height=600))
```

成熟度（docs/08）：当前全部为 Tier 2（可用但未打磨），
达到"生产可用"需要事件系统、文本层与焦点系统。

状态：Box / Card / Row / Column / Flexible / Button / Input 已实现。
"""

from .basic import Box, Card
from .form import Button, ButtonState, Input, InputState
from .layout import Column, Flex, Flexible, Row

__all__ = [
    "Box",
    "Button",
    "ButtonState",
    "Card",
    "Column",
    "Flex",
    "Flexible",
    "Input",
    "InputState",
    "Row",
]
