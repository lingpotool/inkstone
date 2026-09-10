"""Element —— 生命周期（三棵树里的"工地"）。

Widget 是图纸，RenderBox 是楼，Element 是工地：它把两者对上，并持有状态。

核心机制是 **update_child 的三分支**：

    新旧 Widget 类型相同且 Key 相同  → 复用 Element，只 update（廉价）
    类型或 Key 变了                  → 丢弃旧 Element 及其整棵子树（含 RenderBox）
    旧的没了                          → 卸载

为什么不用 React 式"全量重建 + diff"：Python 对象分配不便宜，
大树全量重建每帧会产生数千次临时对象。Flutter 这套"Widget 廉价重建 +
Element 复用 + RenderBox 增量更新"是业界验证最充分的可变状态 UI 架构。

状态：已实现。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from ..layout import RenderBox
from ..style import Theme, default_theme
from .widget import RenderObjectWidget, StatefulWidget, StatelessWidget, Widget

if TYPE_CHECKING:
    from ..text import TextEngine
    from .binding import BuildOwner

__all__ = [
    "ComponentElement",
    "Element",
    "LeafRenderObjectElement",
    "MultiChildRenderObjectElement",
    "RenderObjectElement",
    "StatefulElement",
    "StatelessElement",
]


# 哨兵：区分"没传 slot"与"传了 None"（None 本身是合法槽位值）。
# 定义在类之前，因为它要用作方法默认参数。
_SLOT_UNCHANGED = object()


class Element:
    """所有 Element 的基类。"""

    def __init__(self, widget: Widget) -> None:
        self._widget: Widget = widget
        self._parent: Element | None = None
        self._owner: BuildOwner | None = None
        self._depth: int = 0
        self._dirty: bool = True
        self._active: bool = False
        # 槽位：父容器给本节点的"位置信息"（如 flex 权重）。
        # 它必须穿过 StatelessWidget / StatefulWidget 一直传到真正的渲染节点，
        # 否则 `Flexible(Button())` 里的 flex 会丢——Button 是个组件，中间隔了一层。
        self._slot: object | None = None

    # ------------------------------------------------------------ 属性

    @property
    def widget(self) -> Widget:
        return self._widget

    @property
    def parent(self) -> Element | None:
        return self._parent

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def active(self) -> bool:
        return self._active

    @property
    def owner(self) -> BuildOwner | None:
        """向上找 BuildOwner。只有根节点上存了引用，其余靠 parent 链。"""
        node: Element | None = self
        while node is not None:
            if node._owner is not None:
                return node._owner
            node = node._parent
        return None

    @property
    def render_object(self) -> RenderBox | None:
        return None

    def assign_owner(self, owner: BuildOwner | None) -> None:
        """由 BuildOwner 在挂载根节点时调用，其余节点沿 parent 链向上找。"""
        self._owner = owner

    @property
    def theme(self) -> Theme:
        """环境主题（BuildContext 的核心能力之一）。

        组件靠它取令牌，不必把 theme 当参数层层透传。
        没挂到 BuildOwner 上时退回到默认主题，保证单独构造也能工作。
        """
        owner = self.owner
        if owner is None:
            return default_theme()
        return owner.theme

    @property
    def text_engine(self) -> TextEngine | None:
        """环境文本引擎（与 `theme` 并列的共享服务）。

        组件排版文字时用它——**不要在自己内部 new 一个**：
        `TextEngine` 带度量缓存与整形缓存，每帧重建会让排版性能垮掉。
        返回 `None` 表示这棵树没有配置文本引擎（纯布局测试场景会很常见），
        组件此时应退化为"不排版文字"，而不是抛异常。
        """
        owner = self.owner
        if owner is None:
            return None
        return owner.text_engine

    # ------------------------------------------------------------ 生命周期

    def mount(self, parent: Element | None, slot: object | None) -> None:
        parent_owner = parent.owner if parent is not None else None
        self._parent = parent
        self._depth = 0 if parent is None else parent.depth + 1
        self._active = True
        self._dirty = False
        self._slot = slot
        if parent_owner is not None and self._owner is None:
            self._owner = parent_owner

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        """配置变了但身份没变——只换 Widget 引用，Element 与其状态保留。

        `slot` 默认"不变"；显式传值表示父容器改了本节点的位置信息
        （比如 flex 权重从 1 变成 2），新槽位会继续往下传到真正的渲染节点。
        """
        self._widget = new_widget
        if slot is not _SLOT_UNCHANGED:
            self._slot = slot

    def unmount(self) -> None:
        self._active = False
        self._parent = None

    # ------------------------------------------------------------ 重建

    def mark_needs_build(self) -> None:
        """请求重建。一帧内重复调用只会入队一次——这就是"批处理"。"""
        if not self._active or self._dirty:
            return
        self._dirty = True
        owner = self.owner
        if owner is not None:
            owner.schedule_build_for(self)

    def rebuild(self) -> None:
        if not self._active or not self._dirty:
            return
        self._dirty = False
        self.perform_rebuild()

    def perform_rebuild(self) -> None:
        """基类没有子树，什么都不做。"""

    # ------------------------------------------------------------ 子树

    def visit_children(self, visitor: Callable[[Element], None]) -> None:
        """遍历直接子级。检查器与调试用。"""

    def update_child(
        self,
        child: Element | None,
        new_widget: Widget | None,
        slot: object | None,
    ) -> Element | None:
        """把一个子槽位更新到 new_widget 描述的形态。

        这是整个框架里最关键的十行——Key 复用、身份丢失、子树卸载全在这里发生。
        """
        if new_widget is None:
            if child is not None:
                self._deactivate_child(child)
            return None

        if child is not None:
            if Widget.can_update(child.widget, new_widget):
                if child.widget is not new_widget or child._slot != slot:
                    child.update(new_widget, slot)
                return child
            # 类型或 Key 变了：旧身份作废，连 RenderBox 一起丢
            self._deactivate_child(child)

        new_child = new_widget.create_element()
        new_child.mount(self, slot)
        return new_child

    def _deactivate_child(self, child: Element) -> None:
        child.unmount()

    # ------------------------------------------------------------ 调试

    def describe_path(self) -> str:
        """从根到本节点的路径，如 `App > Column[1] > Text`。"""
        parts: list[str] = []
        node: Element | None = self
        while node is not None:
            parent = node._parent
            if parent is None:
                parts.append(type(node.widget).__name__)
            else:
                index = 0
                for i, sibling in enumerate(self._siblings_of(node, parent)):
                    if sibling is node:
                        index = i
                        break
                parts.append(f"{type(node.widget).__name__}[{index}]")
            node = parent
        return " > ".join(reversed(parts))

    @staticmethod
    def _siblings_of(node: Element, parent: Element) -> Sequence[Element]:
        siblings: list[Element] = []
        parent.visit_children(siblings.append)
        return siblings

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {type(self._widget).__name__} depth={self._depth}>"


# BuildContext 就是 Element 本身：context 能拿到的东西（父级、RenderBox、owner）
# 全都挂在 Element 上，没必要再造一个包装类型。
# 定义在文件末尾，因为 Element 必须先存在。
BuildContext = Element


class ComponentElement(Element):
    """通过 `build()` 生成子树的 Element。"""

    def __init__(self, widget: Widget) -> None:
        super().__init__(widget)
        self._child: Element | None = None

    def build(self) -> Widget:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 build")

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self.perform_rebuild()

    def perform_rebuild(self) -> None:
        built = self.build()
        # 关键：把本节点的槽位继续传给子级。写成 None 的话，
        # `Flexible(Button())` 里的 flex 会在这一层丢掉。
        self._child = self.update_child(self._child, built, self._slot)

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self.perform_rebuild()

    def visit_children(self, visitor: Callable[[Element], None]) -> None:
        if self._child is not None:
            visitor(self._child)

    def unmount(self) -> None:
        if self._child is not None:
            self._deactivate_child(self._child)
            self._child = None
        super().unmount()


class StatelessElement(ComponentElement):
    def __init__(self, widget: StatelessWidget) -> None:
        super().__init__(widget)

    def build(self) -> Widget:
        widget = self.widget
        assert isinstance(widget, StatelessWidget)
        return widget.build(self)


class StatefulElement(ComponentElement):
    def __init__(self, widget: StatefulWidget) -> None:
        super().__init__(widget)
        self.state = widget.create_state()
        self.state._element = self
        self.state.widget = widget

    def mount(self, parent: Element | None, slot: object | None) -> None:
        # init_state 必须在首次 build 之前跑完
        self.state.init_state()
        super().mount(parent, slot)

    def build(self) -> Widget:
        return self.state.build(self)

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        old_widget = self.widget
        super().update(new_widget, slot)
        assert isinstance(new_widget, StatefulWidget)
        self.state.widget = new_widget
        self.state.did_update_widget(old_widget)

    def unmount(self) -> None:
        self.state.dispose()
        self.state._element = None
        super().unmount()


class RenderObjectElement(Element):
    """持有（并管理）一个 RenderBox 的 Element。"""

    def __init__(self, widget: RenderObjectWidget) -> None:
        super().__init__(widget)
        self._render_object: RenderBox | None = None

    @property
    def render_object(self) -> RenderBox | None:
        return self._render_object

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        widget = self.widget
        assert isinstance(widget, RenderObjectWidget)
        self._render_object = widget.create_render_object()
        self.attach_render_object(self._render_object, slot)

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        assert self._render_object is not None
        widget = self.widget
        assert isinstance(widget, RenderObjectWidget)
        widget.update_render_object(self._render_object)
        self._render_object.mark_needs_paint()

    def unmount(self) -> None:
        if self._render_object is not None:
            self.detach_render_object(self._render_object)
            self._render_object = None
        super().unmount()

    # -------------------------------------------------- RenderBox 挂接

    def attach_render_object(self, render_object: RenderBox, slot: object | None) -> None:
        """向上找最近的渲染父级，由它把本节点的 RenderBox 挂进去。

        为什么不由自己挂：只有父容器知道该调 `add()` 还是 `child =`，
        以及要不要带 flex 权重——那是容器的知识，不该泄漏到基类。
        """
        parent = self._ancestor_render_object_element()
        if parent is not None:
            parent.insert_child_render_object(render_object, slot)

    def detach_render_object(self, render_object: RenderBox) -> None:
        parent = self._ancestor_render_object_element()
        if parent is not None:
            parent.remove_child_render_object(render_object)

    def insert_child_render_object(self, child: RenderBox, slot: object | None) -> None:
        raise NotImplementedError(
            f"{type(self).__name__} 需要实现 insert_child_render_object："
            "只有它知道该往自己的 RenderBox 上怎么挂子级"
        )

    def remove_child_render_object(self, child: RenderBox) -> None:
        raise NotImplementedError(f"{type(self).__name__} 需要实现 remove_child_render_object")

    def _ancestor_render_object_element(self) -> RenderObjectElement | None:
        node: Element | None = self._parent
        while node is not None:
            if isinstance(node, RenderObjectElement):
                return node
            node = node._parent
        return None


class LeafRenderObjectElement(RenderObjectElement):
    """没有 Widget 子级的渲染节点。它的 RenderBox 依然是别人的子级，
    所以它自己不会收到 insert 调用——收到就说明用错了。
    """

    def insert_child_render_object(self, child: RenderBox, slot: object | None) -> None:
        raise AssertionError(f"{type(self).__name__} 是叶子节点，不该收到子级挂接请求")

    def remove_child_render_object(self, child: RenderBox) -> None:
        raise AssertionError(f"{type(self).__name__} 是叶子节点，不该收到子级卸载请求")


class MultiChildRenderObjectElement(RenderObjectElement):
    """多个 Widget 子级的渲染节点。

    `insert_child_render_object` 留给具体子类：RenderRow 用 `add(ro, flex=...)`、
    RenderGrid 用 `add(ro, row=..., column=...)`——槽位语义各不相同，
    基类没法替它们决定。
    """

    def __init__(self, widget: RenderObjectWidget) -> None:
        super().__init__(widget)
        self._children: list[Element] = []

    @property
    def children(self) -> Sequence[Element]:
        return tuple(self._children)

    def child_widgets(self) -> Sequence[Widget]:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 child_widgets")

    def slot_for(self, index: int) -> object | None:
        """子级在父 RenderBox 里的槽位信息（如 flex 权重）。默认无。"""
        return None

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self._sync_children()

    def perform_rebuild(self) -> None:
        # RenderObjectElement 没有 build，重建就是重新对齐子级
        self._sync_children()

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self._sync_children()

    def _sync_children(self) -> None:
        """把子级列表对齐到 `child_widgets()`。

        两轮匹配，顺序很重要：

        1. **按下标直接匹配**：绝大多数帧里顺序没变，这一步就完事了，O(n)。
        2. **按 Key 兜底匹配**：处理"中间插入 / 删除 / 排序"。

        只有第 2 步才让"列表重排后输入框里的字还在原处"成立——
        只按下标匹配的话，第 3 项的内容会跟着位置跑到第 3 个槽位上去。
        """
        new_widgets = list(self.child_widgets())
        old_children = list(self._children)

        matched: list[Element | None] = [None] * len(new_widgets)
        used = [False] * len(old_children)

        for index, widget in enumerate(new_widgets):
            if (
                index < len(old_children)
                and not used[index]
                and Widget.can_update(old_children[index].widget, widget)
            ):
                matched[index] = old_children[index]
                used[index] = True

        leftovers = [e for e, is_used in zip(old_children, used, strict=True) if not is_used]
        for index, widget in enumerate(new_widgets):
            if matched[index] is not None or widget.key is None:
                continue
            for position, element in enumerate(leftovers):
                if Widget.can_update(element.widget, widget):
                    matched[index] = element
                    leftovers.pop(position)
                    break

        updated: list[Element] = []
        for index, widget in enumerate(new_widgets):
            reusable = matched[index]
            if reusable is not None:
                if reusable.widget is not widget:
                    reusable.update(widget)
                updated.append(reusable)
            else:
                new_element = widget.create_element()
                new_element.mount(self, self.slot_for(index))
                updated.append(new_element)

        kept = {id(e) for e in updated}
        for element in old_children:
            if id(element) not in kept:
                self._deactivate_child(element)
        self._children = updated

        # 顺序变了就要重挂 RenderBox：Element 的身份保住了，
        # 但渲染树里的先后顺序还得跟着变。
        # 首次挂载时 old_children 为空，挂载本身已按顺序插入，不必重排。
        if old_children and [id(e) for e in updated] != [id(e) for e in old_children]:
            self._reorder_render_objects(updated)

    def _reorder_render_objects(self, children: Sequence[Element]) -> None:
        """按新顺序重挂子级渲染对象。

        依赖 `remove_child_render_object` 是**幂等**的——卸载一个不在容器里的
        子级应当静默返回，而不是报错。具体容器元素实现时请注意这一点。
        """
        for element in children:
            render_object = self.render_object_of(element)
            if render_object is not None:
                self.remove_child_render_object(render_object)
        for index, element in enumerate(children):
            render_object = self.render_object_of(element)
            if render_object is not None:
                self.insert_child_render_object(render_object, self.slot_for(index))

    @staticmethod
    def render_object_of(element: Element) -> RenderBox | None:
        """找出"代表这个 element"的 RenderObject。

        组件型子级（StatelessWidget / StatefulWidget）自己不持有 RenderObject，
        真正的渲染节点在它下面一层或几层。重排序时必须往下找到它——
        只看 `element.render_object` 的话，`Row(children=[Button(), ...])`
        里的按钮会在重排后留在原地，于是界面顺序就乱了。
        """
        direct = element.render_object
        if direct is not None:
            return direct
        pending: list[Element] = []
        element.visit_children(pending.append)
        for child in pending:
            found = MultiChildRenderObjectElement.render_object_of(child)
            if found is not None:
                return found
        return None

    def visit_children(self, visitor: Callable[[Element], None]) -> None:
        for child in self._children:
            visitor(child)

    def unmount(self) -> None:
        for child in self._children:
            self._deactivate_child(child)
        self._children = []
        super().unmount()

    def _render_child(self, element: Element) -> RenderBox | None:
        return element.render_object
