"""声明式构建：给 AI 用的那一层。

AI 写 JSON 比写嵌套构造函数稳得多 —— 结构错了能一眼看出来，语法不会错。
    {"type": "card", "bg": "surface", "children": [{"type": "text", "value": "你好"}]}
"""

from . import widgets as W
from .node import Node

REGISTRY = {
    "box": W.Box,
    "row": W.Row,
    "column": W.Column,
    "stack": W.Stack,
    "spacer": W.Spacer,
    "divider": W.Divider,
    "text": W.Text,
    "button": W.Button,
    "input": W.Input,
    "card": W.Card,
    "badge": W.Badge,
    "nav": W.NavItem,
    "sidebar": W.Sidebar,
}

_HANDLER_KEYS = ("on_click", "on_change", "on_submit", "on_select")


def build(spec, handlers=None):
    """把一棵 dict 变成节点树。handlers 用名字引用回调，避免 JSON 里塞函数。"""
    if spec is None:
        return None
    if isinstance(spec, Node):
        return spec
    handlers = handlers or {}
    spec = dict(spec)
    kind = spec.pop("type", "box")
    kids = spec.pop("children", None)
    for hk in _HANDLER_KEYS:
        if isinstance(spec.get(hk), str):
            spec[hk] = handlers.get(spec[hk])
    node = REGISTRY[kind](**spec)
    for k in kids or []:
        node.add(build(k, handlers))
    return node
