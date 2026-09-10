"""Key —— 组件树里的身份。

为什么需要它：Element 的复用规则是"类型相同且 Key 相同"。
没有 Key 时，只能按下标复用，**列表一重排就把状态串到别的项上**——
输入框里的字跑到别的行、滚动位置跳掉，都是这个原因。

一句话规则：**列表项必须给稳定 Key，用索引当 Key 等于没给。**
列表末尾追加用索引还凑合，中间插入 / 删除 / 排序一律要业务主键。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

__all__ = ["Key", "ValueKey", "same_identity"]


@dataclass(frozen=True, slots=True)
class Key:
    """身份标识基类。Key 相等即视为同一个节点。"""

    value: Hashable


@dataclass(frozen=True, slots=True)
class ValueKey(Key):
    """按值标识——列表项最常用，传业务主键（id、路径、枚举名）即可。"""

    def __init__(self, value: Hashable) -> None:
        # frozen + slots 下不能用 super().__init__ 传参以外的方式赋值，这里显式走一遍
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return f"ValueKey({self.value!r})"


def same_identity(a: Key | None, b: Key | None) -> bool:
    """两个 Key 是否指向同一身份。都为 None 视为相同（都"没给 Key"）。"""
    return a == b
