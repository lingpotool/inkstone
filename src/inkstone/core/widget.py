"""Widget —— 不可变配置（三棵树里的"图纸"）。

铁律：**Widget 里不许有可变状态。** 它每次重建都是全新对象，廉价到可以每帧重建。
状态住在 Element（或 State）上。这条纪律换来的是"配置即数据"——
可以被序列化、被 diff、被 AI 生成、被快照测试。

对比 React 式"全量重建 + diff"：
Python 对象分配不便宜，全量重建在大树上每帧产生数千次临时对象。
我们采用 Flutter 验证过的结构——Widget 廉价重建、Element 复用、RenderBox 增量更新。

状态：已实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .key import Key

if TYPE_CHECKING:  # 只在类型检查时引入，避免与 element 形成运行时循环导入
    from ..layout import RenderBox
    from .element import BuildContext, Element, StatefulElement

__all__ = [
    "InheritedWidget",
    "RenderObjectWidget",
    "State",
    "StatefulWidget",
    "StatelessWidget",
    "Widget",
]

W = TypeVar("W", bound="StatefulWidget")


class Widget:
    """配置描述的基类。子类要嘛实现 `build`，要嘛实现 `create_render_object`。"""

    key: Key | None = None

    def __init__(self, *, key: Key | None = None) -> None:
        self.key = key

    def create_element(self) -> Element:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 create_element")

    @staticmethod
    def can_update(old: Widget, new: Widget) -> bool:
        """能否用 new 更新 old 对应的 Element。

        类型相同且 Key 相同才复用。类型变了必须重建子树——
        否则就会出现"Button 的配置被套用到 Text 上"这种荒唐事。
        """
        return type(old) is type(new) and old.key == new.key

    def __repr__(self) -> str:
        return f"<{type(self).__name__} key={self.key}>"


class StatelessWidget(Widget):
    """无状态组件：给定配置，算出子树。"""

    def build(self, context: BuildContext) -> Widget:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 build")

    def create_element(self) -> Element:
        from .element import StatelessElement

        return StatelessElement(self)


class StatefulWidget(Widget):
    """有状态组件：状态由配套的 State 持有。"""

    def create_state(self) -> State[Any]:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 create_state")

    def create_element(self) -> Element:
        from .element import StatefulElement

        return StatefulElement(self)


class InheritedWidget(Widget):
    """环境数据的载体（主题、locale、DPI……）：子树内所有后代都能读到。

    与 StatelessWidget 的区别不在"有没有状态"，而在**传播方式**：
    后代用 `context.depend_on(...)` 显式声明依赖；配置换成新 Widget 后，
    只有 `update_should_notify` 返回 True 时才定向标脏这些依赖者——
    而不是"父级重建、整棵子树不问青红皂白跟着重建"。

    状态：已实现（R6.1，docs/20）。
    """

    def __init__(self, child: Widget, *, key: Key | None = None) -> None:
        super().__init__(key=key)
        self.child = child

    def update_should_notify(self, old: InheritedWidget) -> bool:
        """配置从 old 换成 self 之后，依赖者要不要重建。"""
        raise NotImplementedError(f"{type(self).__name__} 必须实现 update_should_notify")

    def create_element(self) -> Element:
        from .element import InheritedElement

        return InheritedElement(self)


class State(Generic[W]):
    """StatefulWidget 的状态与行为。

    生命周期：`init_state → build → did_update_widget* → dispose`。
    `set_state` 是唯一的合法变更入口——它会标脏并排入帧调度，
    绝不允许"改了字段但没通知框架"。
    """

    def __init__(self) -> None:
        self.widget: W
        self._element: StatefulElement | None = None

    # ------------------------------------------------------------ 生命周期

    def init_state(self) -> None:
        """挂载时调用一次。订阅流、起定时器放在这里。"""

    def did_update_widget(self, old_widget: W) -> None:
        """父级重建并传下新配置时调用。默认什么都不做。"""

    def dispose(self) -> None:
        """卸载时调用一次。取消订阅、释放资源放在这里。"""

    def build(self, context: BuildContext) -> Widget:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 build")

    # ------------------------------------------------------------ 状态

    @property
    def mounted(self) -> bool:
        return self._element is not None

    @property
    def context(self) -> BuildContext:
        if self._element is None:
            raise RuntimeError(f"{type(self).__name__} 尚未挂载，拿不到 context")
        return self._element

    def set_state(self, mutate: Any = None) -> None:
        """变更状态并请求重建。

        两种写法都支持：先改字段再 `set_state()`，或把改动塞进回调
        `set_state(lambda: setattr(self, "count", self.count + 1))`——
        后者保证"改动"与"标脏"是原子的，不会因为中途抛异常而漏掉标脏。

        在 layout / paint 阶段调用会抛错：那是时序 bug 的高发区，
        框架宁可响亮地失败，也不要留下"改了但没生效"的诡异行为。
        """
        if self._element is None:
            raise RuntimeError(f"{type(self).__name__} 已卸载，不能再 set_state")
        if callable(mutate):
            mutate()
        self._element.mark_needs_build()


class RenderObjectWidget(Widget):
    """直接产出一个 RenderBox 的 Widget（叶子或布局容器）。

    这是"配置 → 布局/绘制"的边界：上面是声明式的 Widget 树，
    下面是真正持有几何的 RenderBox 树。
    """

    def create_render_object(self) -> RenderBox:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 create_render_object")

    def update_render_object(self, render_object: RenderBox) -> None:
        """配置变化时把新值写进已有的 RenderBox（不重建它）。"""

    def create_element(self) -> Element:
        from .element import RenderObjectElement

        return RenderObjectElement(self)
