"""布局组件：Row / Column / Flexible。

`Flexible` 是个有意思的东西：**它自己不产生 RenderObject**，
只给父级携带"这个子级要参与剩余空间分配，权重是多少"这条信息。
所以它在 Element 树里不留节点——少了这一层，diff 与热重载都更省。

```python
Row(children=[
    Box(width=40, height=20),
    Flexible(Input(), flex=1),   # 吃掉剩余宽度
])
```

状态：Row / Column / Flexible 已实现。
Grid / Stack / ScrollView 的 Widget 包装待各自有真实用例时补
（渲染对象已就绪，包一层很快）。
"""

from __future__ import annotations

from ..core import MultiChildRenderObjectElement, RenderObjectWidget, Widget
from ..core.element import Element
from ..core.key import Key
from ..layout import Axis, RenderBox, RenderColumn, RenderFlex, RenderRow
from ..layout.protocol import CrossAxisAlignment, MainAxisAlignment, MainAxisSize
from ..layout.types import EdgeInsets

__all__ = ["Column", "Flex", "Flexible", "Row"]


class Flexible(Widget):
    """只携带 flex 权重的元数据组件，不产生 RenderObject。"""

    def __init__(self, child: Widget, *, flex: int = 1, key: Key | None = None) -> None:
        if flex <= 0:
            raise ValueError(f"flex 权重必须为正，收到 {flex}")
        self.child = child
        self.flex = flex
        self.key = key

    def create_element(self) -> Element:
        raise AssertionError(
            "Flexible 只是元数据，不该被单独挂载——它必须直接出现在 Row / Column 的 children 里"
        )


class _FlexElement(MultiChildRenderObjectElement):
    def _flex_widget(self) -> Flex:
        widget = self.widget
        assert isinstance(widget, Flex)
        return widget

    def child_widgets(self) -> tuple[Widget, ...]:
        return tuple(_unwrap(child) for child in self._flex_widget().children)

    def slot_for(self, index: int) -> object | None:
        child = self._flex_widget().children[index]
        return child.flex if isinstance(child, Flexible) else 0

    def insert_child_render_object(self, child: RenderBox, slot: object | None) -> None:
        container = self.render_object
        assert isinstance(container, RenderFlex)
        container.add(child, flex=int(slot) if isinstance(slot, int) else 0)

    def remove_child_render_object(self, child: RenderBox) -> None:
        """幂等：不在容器里的子级静默返回。"""
        container = self.render_object
        if not isinstance(container, RenderFlex):
            return
        if any(c is child for c in container.children):
            container.remove(child)


class Flex(RenderObjectWidget):
    """Row 与 Column 共用的实现——方向只是个参数。"""

    def __init__(
        self,
        direction: Axis,
        children: tuple[Widget, ...] = (),
        *,
        gap: float = 0.0,
        justify: MainAxisAlignment = MainAxisAlignment.START,
        align: CrossAxisAlignment = CrossAxisAlignment.START,
        main_axis_size: MainAxisSize = MainAxisSize.MIN,
        padding: EdgeInsets | None = None,
        key: Key | None = None,
    ) -> None:
        self.direction = direction
        self.children = tuple(children)
        self.gap = gap
        self.justify = justify
        self.align = align
        self.main_axis_size = main_axis_size
        self.padding = padding
        self.key = key

    def create_render_object(self) -> RenderFlex:
        # 产出具体的 Row / Column 类型而不是裸 RenderFlex：
        # 检查器与 repr 里能一眼看出是横的还是竖的。
        if self.direction is Axis.HORIZONTAL:
            return RenderRow(
                gap=self.gap,
                justify=self.justify,
                align=self.align,
                main_axis_size=self.main_axis_size,
                padding=self.padding,
            )
        return RenderColumn(
            gap=self.gap,
            justify=self.justify,
            align=self.align,
            main_axis_size=self.main_axis_size,
            padding=self.padding,
        )

    def update_render_object(self, render_object: RenderBox) -> None:
        assert isinstance(render_object, RenderFlex)
        render_object.gap = self.gap
        render_object.justify = self.justify
        render_object.cross_align = self.align
        render_object.main_axis_size = self.main_axis_size
        render_object.padding = self.padding if self.padding is not None else EdgeInsets()
        render_object.mark_needs_layout()

    def create_element(self) -> Element:
        return _FlexElement(self)


class Row(Flex):
    """水平布局。"""

    def __init__(self, children: tuple[Widget, ...] = (), **kwargs: object) -> None:
        super().__init__(Axis.HORIZONTAL, children, **kwargs)  # type: ignore[arg-type]


class Column(Flex):
    """垂直布局。"""

    def __init__(self, children: tuple[Widget, ...] = (), **kwargs: object) -> None:
        super().__init__(Axis.VERTICAL, children, **kwargs)  # type: ignore[arg-type]


def _unwrap(child: Widget) -> Widget:
    """剥掉 Flexible 外壳，露出真正的子 Widget。"""
    return child.child if isinstance(child, Flexible) else child
