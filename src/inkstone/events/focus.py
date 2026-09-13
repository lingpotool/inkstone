"""焦点系统 —— 谁接收键盘与 IME（docs/06 §6，docs/09）。

三个问题必须一次回答清楚，否则键盘输入只能靠"谁碰巧记得自己聚焦了"：

1. **唯一焦点**：同一时刻只有一个节点持有焦点，且**点击别处自动失焦**。
   没有这条，所有被点过的控件都会一直"聚焦"——表现就是界面上一堆控件
   同时带着焦点环（真机上看到的那圈"多出来的边框"）。
2. **focus-visible 语义**（docs/13 §5）：环只在**键盘**导航时显示。
   鼠标点击可以让控件获得焦点（按钮、输入框都需要），但不该画环。
   所以"聚焦"与"可见地聚焦"是两个状态，不是一个布尔。
3. **遍历顺序**：按挂载顺序（= 树的前序，也就是视觉顺序），可用
   `order` 覆盖。作用域/焦点圈禁（对话框）属 `primitives/focus_trap`，后续。

分层：本模块是 L1，不认识组件树也不碰平台——它只管"节点集合 + 当前焦点"。
组件在自己的 State 里创建 `FocusNode`，挂载时交给 `FocusManager`（由
`BuildOwner` 持有），并把键盘/文本/IME 事件的处理函数挂在节点上。

状态：已实现（R9.1）。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

__all__ = ["FocusManager", "FocusNode", "FocusSource"]


class FocusSource(Enum):
    """焦点是怎么来的——决定要不要显示焦点环。"""

    KEYBOARD = "keyboard"  # Tab / 方向键：显示环（focus-visible）
    POINTER = "pointer"  # 鼠标/触摸点击：聚焦但**不**显示环
    PROGRAMMATIC = "programmatic"  # 代码调用：默认不显示环


class FocusNode:
    """一个可聚焦对象。通常由组件 State 持有（如 `InputState`）。

    `on_event` 是键盘/文本/IME 事件的处理器（返回是否消费）；`on_change` 在
    聚焦态或可见性变化时被调用，组件据此刷新样式（状态轴里的 FOCUS_VISIBLE）。
    """

    def __init__(
        self,
        *,
        on_event: Callable[[object], bool] | None = None,
        on_change: Callable[[bool, bool], None] | None = None,
        order: int | None = None,
        debug_name: str = "FocusNode",
    ) -> None:
        self.on_event = on_event
        self.on_change = on_change
        self.order = order
        self.debug_name = debug_name
        self.enabled: bool = True
        self._manager: FocusManager | None = None
        self._focused = False
        self._focus_visible = False

    # ------------------------------------------------------------ 状态

    @property
    def attached(self) -> bool:
        return self._manager is not None

    @property
    def focused(self) -> bool:
        return self._focused

    @property
    def focus_visible(self) -> bool:
        """是否应当显示焦点环（只有键盘来源为真）。"""
        return self._focus_visible

    # ------------------------------------------------------------ 请求

    def request_focus(self, source: FocusSource = FocusSource.PROGRAMMATIC) -> None:
        if self._manager is not None:
            self._manager.request(self, source)

    def unfocus(self) -> None:
        if self._manager is not None and self._manager.current is self:
            self._manager.clear()

    # ------------------------------------------------------------ 内部

    def _set_state(self, focused: bool, visible: bool) -> None:
        if (focused, visible) == (self._focused, self._focus_visible):
            return
        self._focused = focused
        self._focus_visible = visible
        if self.on_change is not None:
            self.on_change(focused, visible)

    def __repr__(self) -> str:
        flag = "focused" if self._focused else "idle"
        visible = "+visible" if self._focus_visible else ""
        return f"<FocusNode {self.debug_name} {flag}{visible}>"


class FocusManager:
    """节点注册表 + 唯一焦点。一个 `BuildOwner` 持有一个。

    **遍历顺序就是挂载顺序**：元素是前序挂载的，于是"注册顺序"天然等于
    视觉顺序（Row 里左边的先注册）。`FocusNode.order` 可以覆盖它
    （显式指定 Tab 顺序时用）。
    """

    def __init__(self) -> None:
        self._nodes: list[FocusNode] = []
        self._current: FocusNode | None = None
        # 一次指针分发内是否有人请求了焦点——用于"点空白处失焦"
        self._pointer_dispatch_active = False
        self._focus_requested_in_dispatch = False

    # ------------------------------------------------------------ 注册

    def attach(self, node: FocusNode) -> None:
        if node in self._nodes:
            return
        self._nodes.append(node)
        node._manager = self

    def detach(self, node: FocusNode) -> None:
        if node not in self._nodes:
            return
        if self._current is node:
            self.clear()
        self._nodes.remove(node)
        node._manager = None

    @property
    def nodes(self) -> tuple[FocusNode, ...]:
        return tuple(self._nodes)

    @property
    def current(self) -> FocusNode | None:
        return self._current

    @property
    def focus_visible(self) -> bool:
        return self._current is not None and self._current.focus_visible

    # ------------------------------------------------------------ 焦点变更

    def request(self, node: FocusNode, source: FocusSource = FocusSource.PROGRAMMATIC) -> None:
        if not node.attached or not node.enabled:
            return
        if self._pointer_dispatch_active:
            self._focus_requested_in_dispatch = True
        visible = source is FocusSource.KEYBOARD
        if node is self._current:
            # 同一个节点：来源可能改变"可见性"（鼠标点过之后再按 Tab 就该出环）
            node._set_state(True, visible)
            return
        previous = self._current
        if previous is not None:
            previous._set_state(False, False)
        self._current = node
        node._set_state(True, visible)

    def clear(self) -> None:
        """失焦。点击空白、窗口失焦、卸载时调用。"""
        current = self._current
        if current is None:
            return
        self._current = None
        current._set_state(False, False)

    def move(self, step: int = 1) -> bool:
        """按遍历顺序移动焦点（Tab / Shift+Tab）。返回是否真的移动了。

        跳过 `enabled=False` 的节点；没有焦点时从列表端部开始。
        """
        candidates = [node for node in sorted(self._nodes, key=self._sort_key) if node.enabled]
        if not candidates:
            return False
        if self._current in candidates:
            index = (candidates.index(self._current) + step) % len(candidates)
        else:
            index = 0 if step >= 0 else len(candidates) - 1
        self.request(candidates[index], FocusSource.KEYBOARD)
        return True

    def _sort_key(self, node: FocusNode) -> tuple[float, int]:
        # order 显式指定时优先；否则按挂载顺序（= 视觉顺序）
        explicit = node.order
        return (float("inf") if explicit is None else float(explicit), self._nodes.index(node))

    # ------------------------------------------------------------ 事件分发

    def dispatch(self, event: object) -> bool:
        """把事件交给当前焦点节点。返回是否被消费。"""
        current = self._current
        if current is None or current.on_event is None:
            return False
        return bool(current.on_event(event))

    def begin_pointer_dispatch(self) -> None:
        """指针分发开始：记录这次分发里有没有人请求焦点（R9.2 的失焦判定）。"""
        self._pointer_dispatch_active = True
        self._focus_requested_in_dispatch = False

    def end_pointer_dispatch(self) -> bool:
        """指针分发结束。若无人请求焦点（点到了空白/非聚焦控件），则失焦。

        这条规则解决的是"点过的控件全都一直聚焦"：没被点中的输入框不会
        收到焦点，而点到别处时当前焦点必须让出来。
        """
        self._pointer_dispatch_active = False
        if self._focus_requested_in_dispatch:
            return False
        had_focus = self._current is not None
        if had_focus:
            self.clear()
        return had_focus
