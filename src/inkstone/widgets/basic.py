"""基础组件：Box / Card。

Box 是"一块有尺寸、有颜色、有圆角的矩形"——卡片、占位、分隔都从它来。
Card 是 Box 的语义化封装：默认带上内边距，让"卡片"在代码里读起来就是卡片，
而不是一堆 Box 参数。

**零硬编码**：这里没有出现任何字面量颜色、字号或间距。
Card 的默认内边距来自 `context.theme`（环境主题挂在 BuildOwner 上），
而不是写死 16——否则换主题时它就换不动了。

待补：Text / Image / Icon / Divider / Spacer。
Text 必须等 `text/` 的字体度量落地——**文字宽度不能估算**，
估算出来的度量与渲染不一致，正是 docs/04 反复警告的坑。

状态：Box / Card 已实现。
"""

from __future__ import annotations

from collections.abc import Callable

from ..core import (
    LeafRenderObjectElement,
    RenderObjectElement,
    RenderObjectWidget,
    Widget,
)
from ..core.element import _SLOT_UNCHANGED, Element
from ..core.key import Key
from ..gfx.color import Color
from ..layout import BoxConstraints, RenderBox, Size
from ..layout.types import EdgeInsets, Offset, Rect

__all__ = ["Box", "Card"]


class _BoxRenderObject(RenderBox):
    """Box 的渲染对象。外观字段存着，等 gfx 的 paint 来用。"""

    def __init__(self, width: float | None, height: float | None) -> None:
        super().__init__()
        self.width_hint = width
        self.height_hint = height
        self.color: Color | None = None
        self.radius: float | None = None

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        width = self.width_hint
        height = self.height_hint
        if width is None:
            width = constraints.max_width if constraints.has_bounded_width else 0.0
        if height is None:
            height = constraints.max_height if constraints.has_bounded_height else 0.0
        return constraints.constrain(Size(width, height))

    def paint(self, context: object) -> None:
        color = self.color
        if color is None or color.a == 0.0:
            return
        rect = Rect(0.0, 0.0, self.size.width, self.size.height)
        radius = self.radius or 0.0
        round_rect = getattr(context, "round_rect", None)
        fill_rect = getattr(context, "fill_rect", None)
        if radius > 0.0 and round_rect is not None:
            round_rect(rect, radius, color)
        elif fill_rect is not None:
            fill_rect(rect, color)


class _CardRenderObject(RenderBox):
    """Card 的渲染对象：尺寸由子级（连 padding）决定。"""

    def __init__(self, padding: EdgeInsets) -> None:
        super().__init__(padding=padding)
        self._child: RenderBox | None = None
        self.elevation: str = "e1"
        self.surface_color: Color | None = None
        self.border_color: Color | None = None
        self.radius: float | None = None

    @property
    def child(self) -> RenderBox | None:
        return self._child

    @child.setter
    def child(self, value: RenderBox | None) -> None:
        if self._child is not None:
            self.orphan(self._child)
        self._child = value
        if value is not None:
            self.adopt(value)

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return () if self._child is None else (self._child,)

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        inner = self.content_constraints(constraints)
        content = Size(0.0, 0.0)
        if self._child is not None:
            content = self.layout_child(self._child, inner)
            self.place_child(self._child, Offset(self.padding.left, self.padding.top))
        return constraints.constrain(self.wrap(content))

    def paint(self, context: object) -> None:
        rect = Rect(0.0, 0.0, self.size.width, self.size.height)
        radius = self.radius or 10.0

        round_rect = getattr(context, "round_rect", None)
        if self.surface_color is not None and self.surface_color.a > 0.0 and round_rect is not None:
            round_rect(rect, radius, self.surface_color)

        stroke = getattr(context, "stroke_rect", None)
        if self.border_color is not None and self.border_color.a > 0.0 and stroke is not None:
            stroke(rect, 1.0, self.border_color, radius)


class Box(RenderObjectWidget):
    """一块矩形。`width` / `height` 为 None 时按可用空间撑开。"""

    def __init__(
        self,
        *,
        width: float | None = None,
        height: float | None = None,
        color: Color | None = None,
        radius: float | None = None,
        key: Key | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.color = color
        self.radius = radius
        self.key = key

    def create_render_object(self) -> _BoxRenderObject:
        return _BoxRenderObject(self.width, self.height)

    def update_render_object(self, render_object: RenderBox) -> None:
        assert isinstance(render_object, _BoxRenderObject)
        render_object.width_hint = self.width
        render_object.height_hint = self.height
        render_object.color = self.color
        render_object.radius = self.radius
        render_object.mark_needs_layout()

    def create_element(self) -> Element:
        return _BoxElement(self)


class _BoxElement(LeafRenderObjectElement):
    """把 widget 上的外观字段写进 RenderObject（RenderObjectWidget 拿不到 context）。"""

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self._apply_style()

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self._apply_style()

    def _apply_style(self) -> None:
        widget = self.widget
        assert isinstance(widget, Box)
        render_object = self.render_object
        if render_object is None:
            return
        assert isinstance(render_object, _BoxRenderObject)
        theme = self.theme
        render_object.color = widget.color if widget.color is not None else theme.color("surface")
        render_object.radius = widget.radius if widget.radius is not None else theme.radius("md")
        render_object.mark_needs_paint()


class Card(RenderObjectWidget):
    """卡片：表面色 + 一级阴影 + 内边距，包一个子级。"""

    def __init__(
        self,
        child: Widget | None = None,
        *,
        padding: float | None = None,
        key: Key | None = None,
    ) -> None:
        self.child = child
        # None = 用环境主题的令牌决定，不在这里写死数值
        self.padding = padding
        self.key = key

    def create_render_object(self) -> _CardRenderObject:
        return _CardRenderObject(EdgeInsets())

    def create_element(self) -> Element:
        return _CardElement(self)


class _CardElement(RenderObjectElement):
    """Card 有且只有一个 Widget 子级，所以它不是叶子——这里补上子级同步。"""

    def __init__(self, widget: Card) -> None:
        super().__init__(widget)
        self._child: Element | None = None

    def insert_child_render_object(self, child: RenderBox, slot: object | None) -> None:
        container = self.render_object
        assert isinstance(container, _CardRenderObject)
        container.child = child

    def remove_child_render_object(self, child: RenderBox) -> None:
        container = self.render_object
        if isinstance(container, _CardRenderObject) and container.child is child:
            container.child = None

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self._apply_padding()
        self._sync_child()

    def perform_rebuild(self) -> None:
        self._sync_child()

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self._apply_padding()
        self._sync_child()

    def _apply_padding(self) -> None:
        widget = self.widget
        assert isinstance(widget, Card)
        render_object = self.render_object
        if render_object is None:
            return
        assert isinstance(render_object, _CardRenderObject)
        theme = self.theme
        inset = widget.padding if widget.padding is not None else theme.space("lg")
        render_object.padding = EdgeInsets.all(inset)
        # 外观也来自主题：表面 + 细边框保层级（阴影 e1 的光栅实现属 v1，
        # 令牌照旧在，只是光栅端暂时忽略）
        render_object.surface_color = theme.color("surface")
        render_object.border_color = theme.color("border")
        render_object.radius = theme.radius("md")
        render_object.mark_needs_paint()

    def _sync_child(self) -> None:
        widget = self.widget
        assert isinstance(widget, Card)
        render_object = self.render_object
        assert isinstance(render_object, _CardRenderObject)

        self._child = self.update_child(self._child, widget.child, None)
        child_element = self._child
        render_object.child = child_element.render_object if child_element is not None else None

    def visit_children(self, visitor: Callable[[Element], None]) -> None:
        if self._child is not None:
            visitor(self._child)

    def unmount(self) -> None:
        if self._child is not None:
            self._deactivate_child(self._child)
            self._child = None
        super().unmount()
