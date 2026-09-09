"""inkstone —— 一套为「AI 来写」而生的 Python 原生 UI 系统。

不依赖任何 UI 工具包：没有 Tk、没有 Qt、没有 WebView。
底层只有一个能画像素的窗口，其余全部自建。

    from inkstone import App, Row, Column, Card, Text, Button, Input

    app = App(title="我的工具")
    app.mount(Column(padding=24, gap=12, children=[
        Text("你好，世界", size=22),
        Row(gap=8, children=[
            Input(placeholder="说点什么…", w="fill"),
            Button("发送", on_click=lambda: print("go")),
        ], w="fill"),
    ]))
    app.run()
"""

from .tokens import LIGHT, DARK, THEMES, S, R, F
from .node import Node
from .widgets import (
    Box, Row, Column, Stack, Spacer, Divider,
    Text, Button, Input, Card, Badge, NavItem, Sidebar,
)
from .spec import build
from .app import App

__all__ = [
    "App", "Node", "Box", "Row", "Column", "Stack", "Spacer", "Divider",
    "Text", "Button", "Input", "Card", "Badge", "NavItem", "Sidebar",
    "build", "S", "R", "F", "LIGHT", "DARK", "THEMES",
]
