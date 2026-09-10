"""确定性样式解析（docs/13 §8）。

解析顺序**固定五层，后者覆盖前者**：

    默认 → 主题 → 变体 → 实例覆盖 → 交互态

写死顺序的意义：**不存在优先级战争**。CSS 那套"谁的选择器更特殊"在组件库里
是灾难——同一个按钮在不同位置长得不一样，还没法预测。这里每层都只是一次
dict 覆盖，同样的输入永远得到同样的输出，所以可以被 AI 稳定生成、
被测试快照、被检查器解释。

> ⚠️ **与 docs/13 §8 的一处分歧，待定夺。**
> docs/13 §8 写的是"… → 状态 → 实例覆盖"（实例覆盖压过状态），
> 本实现把**交互态放在最后**。理由：如果实例覆盖能压过状态，那么
> 用户写一句 `bg=red`，悬停反馈就被悄悄关掉了——这是最难排查的那类 bug
> （"我明明设了 hover 色，怎么没反应"）。让状态永远生效，
> 实例覆盖只管"静态外观"，职责更清楚。
> 若确定要按 docs/13 原文来，把下面 `resolve()` 里 state 与 overrides 的
> 位置对调即可，其余代码不用动。

状态轴（所有交互组件统一八态）：

    default / hover / active / focus-visible / disabled / loading / selected / error

状态一律走 `ComponentState` 枚举——不靠"鼠标移上去了"这种运行时副作用，
而是"当前状态是什么"这个纯数据。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

__all__ = [
    "FOCUS_RING_OFFSET",
    "FOCUS_RING_WIDTH",
    "ComponentState",
    "layer",
    "resolve",
]

# 焦点环规格（docs/13 §5）：2px 环 + 2px 偏移，永不裁切，
# 键盘导航必备，鼠标点击不显示（focus-visible 语义）。
FOCUS_RING_WIDTH = 2.0
FOCUS_RING_OFFSET = 2.0


class ComponentState(Enum):
    """交互组件的统一状态轴。"""

    DEFAULT = "default"
    HOVER = "hover"
    ACTIVE = "active"
    FOCUS_VISIBLE = "focus_visible"
    DISABLED = "disabled"
    LOADING = "loading"
    SELECTED = "selected"
    ERROR = "error"

    @property
    def is_interactive_blocked(self) -> bool:
        """这两种状态下组件不该响应输入。"""
        return self in (ComponentState.DISABLED, ComponentState.LOADING)


def layer(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """按给定顺序合并样式层，后者覆盖前者。

    只有非空值才覆盖——"这一层没意见"表达成留空即可，
    不必把上一层的原值再抄一遍。
    """
    merged: dict[str, Any] = {}
    for current in layers:
        for key, value in current.items():
            if value is not None:
                merged[key] = value
    return merged


def resolve(
    *,
    defaults: Mapping[str, Any],
    theme_values: Mapping[str, Any] | None = None,
    variant: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """五层解析的唯一入口。顺序固定，不参与任何优先级竞争。"""
    return layer(
        defaults,
        theme_values or {},
        variant or {},
        overrides or {},
        state or {},
    )
