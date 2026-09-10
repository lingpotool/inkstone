"""表单组件：Button / Input。

这两个组件存在的意义不只是"有个按钮"，更是**把状态轴与令牌系统接起来**：

    ComponentState（八态）→ resolve_button_style() → 具体颜色

按钮自己不知道"primary 是什么颜色"，它只知道"我是 primary 变体、现在处于 hover"，
颜色由变体配方从主题算出来。换主题时组件代码一行都不用改。

**当前的诚实边界**：
- 按钮宽度默认撑满可用空间——**文字度量还没做**，`text/` 落地前无法按标签
  收缩到内容宽度。硬编码一个"每字 14px"的估算是最糟的选择：
  它会在中英混排、字号变化时悄悄算错，而且错得很隐蔽。
- 输入文本的编辑与 IME 组合态属于 Phase 2（ROADMAP item 1），
  这里的 Input 只有值、占位符与状态，不含编辑逻辑。

状态：Button / Input 已实现；TextArea / Select / Checkbox / Radio / Switch /
Slider / Form 待事件系统与表单校验落地后补。
"""

from __future__ import annotations

from ..core import (
    LeafRenderObjectElement,
    RenderObjectWidget,
    State,
    StatefulWidget,
    Widget,
)
from ..core.element import _SLOT_UNCHANGED, Element
from ..core.key import Key
from ..gfx.color import Color
from ..layout import BoxConstraints, RenderBox, Size
from ..layout.types import Rect
from ..style import (
    ButtonStyle,
    ButtonVariant,
    ComponentState,
    InputStyle,
    Theme,
    resolve_button_style,
    resolve_input_style,
)

__all__ = ["Button", "Input"]


# ---------------------------------------------------------------- 渲染对象


class _ControlRenderObject(RenderBox):
    """Button 与 Input 共用的渲染对象：固定高度、宽度撑满可用空间。"""

    def __init__(self) -> None:
        super().__init__()
        self.height_value: float = 36.0
        self.width_value: float | None = None
        self.bg: Color | None = None
        self.fg: Color | None = None
        self.border: Color | None = None
        self.placeholder_color: Color | None = None
        self.border_width: float = 1.0
        self.radius: float = 10.0
        self.focus_ring: Color | None = None
        self.focus_ring_width: float = 0.0

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        width = self.width_value
        if width is None:
            width = constraints.max_width if constraints.has_bounded_width else 0.0
        return constraints.constrain(Size(width, self.height_value))

    def paint(self, context: object) -> None:
        rect = Rect(0.0, 0.0, self.size.width, self.size.height)
        radius = self.radius

        round_rect = getattr(context, "round_rect", None)
        stroke = getattr(context, "stroke_rect", None)

        bg = self.bg
        if bg is not None and bg.a > 0.0 and round_rect is not None:
            round_rect(rect, radius, bg)

        border = self.border
        if self.border_width > 0.0 and border is not None and border.a > 0.0 and stroke is not None:
            stroke(rect, self.border_width, border, radius)

        # 焦点环：2px 环 + 2px 偏移，向外长不裁切（docs/13 §5）
        ring = self.focus_ring
        if self.focus_ring_width > 0.0 and ring is not None and ring.a > 0.0 and stroke is not None:
            from ..style import FOCUS_RING_OFFSET

            ring_rect = Rect(
                -FOCUS_RING_OFFSET,
                -FOCUS_RING_OFFSET,
                self.size.width + 2 * FOCUS_RING_OFFSET,
                self.size.height + 2 * FOCUS_RING_OFFSET,
            )
            stroke(ring_rect, self.focus_ring_width, ring, radius + FOCUS_RING_OFFSET)


class _ControlBox(RenderObjectWidget):
    """承载解析好样式的渲染组件。样式在这里已经是具体值了。"""

    def __init__(self, *, style: ButtonStyle | InputStyle, width: float | None = None) -> None:
        self.style = style
        self.width = width

    def create_render_object(self) -> _ControlRenderObject:
        return _ControlRenderObject()

    def update_render_object(self, render_object: RenderBox) -> None:
        self._apply(render_object)

    def _apply(self, render_object: RenderBox) -> None:
        assert isinstance(render_object, _ControlRenderObject)
        render_object.width_value = self.width
        render_object.height_value = self.style.height
        render_object.bg = self.style.bg
        render_object.fg = self.style.fg
        render_object.border = self.style.border
        render_object.border_width = self.style.border_width
        render_object.radius = self.style.radius
        render_object.focus_ring = self.style.focus_ring
        render_object.focus_ring_width = self.style.focus_ring_width
        if isinstance(self.style, InputStyle):
            render_object.placeholder_color = self.style.placeholder
        render_object.mark_needs_layout()

    def create_element(self) -> Element:
        return _ControlElement(self)


class _ControlElement(LeafRenderObjectElement):
    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self._apply()

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self._apply()

    def _apply(self) -> None:
        widget = self.widget
        assert isinstance(widget, _ControlBox)
        if self.render_object is not None:
            widget._apply(self.render_object)


# ---------------------------------------------------------------- Button


class Button(StatefulWidget):
    """按钮。

    状态由 `ComponentState` 驱动：组件不关心"鼠标在哪"，只关心"我现在是什么状态"。
    事件系统落地后，pointer 事件只负责调 `set_component_state()`，
    样式解析这条链完全不用动。
    """

    def __init__(
        self,
        label: str = "",
        *,
        on_pressed: object | None = None,
        variant: ButtonVariant = ButtonVariant.PRIMARY,
        size: str = "md",
        width: float | None = None,
        disabled: bool = False,
        key: Key | None = None,
    ) -> None:
        self.label = label
        self.on_pressed = on_pressed
        self.variant = variant
        self.size = size
        self.width = width
        self.disabled = disabled
        self.key = key

    def create_state(self) -> ButtonState:
        return ButtonState()


class ButtonState(State["Button"]):
    def init_state(self) -> None:
        self.component_state = (
            ComponentState.DISABLED if self.widget.disabled else ComponentState.DEFAULT
        )

    def build(self, context: object) -> Widget:
        assert isinstance(context, Element)
        style = resolve_button_style(
            context.theme,
            variant=self.widget.variant,
            size=self.widget.size,
            state=self.component_state,
        )
        return _ControlBox(style=style, width=self.widget.width)

    def set_component_state(self, state: ComponentState) -> None:
        """切换交互状态。真正接上事件系统后，这会由 pointer / focus 事件调用。"""
        if self.component_state is state:
            return
        self.component_state = state
        self.set_state()

    @property
    def enabled(self) -> bool:
        return not self.component_state.is_interactive_blocked


# ---------------------------------------------------------------- Input


class Input(StatefulWidget):
    """输入框。当前只有值、占位符与状态——文本编辑与 IME 属 Phase 2。"""

    def __init__(
        self,
        value: str = "",
        *,
        placeholder: str = "",
        on_changed: object | None = None,
        size: str = "md",
        width: float | None = None,
        error: bool = False,
        key: Key | None = None,
    ) -> None:
        self.value = value
        self.placeholder = placeholder
        self.on_changed = on_changed
        self.size = size
        self.width = width
        self.error = error
        self.key = key

    def create_state(self) -> InputState:
        return InputState()


class InputState(State["Input"]):
    def init_state(self) -> None:
        self.component_state = ComponentState.ERROR if self.widget.error else ComponentState.DEFAULT

    def build(self, context: object) -> Widget:
        assert isinstance(context, Element)
        style = resolve_input_style(
            context.theme,
            size=self.widget.size,
            state=self.component_state,
        )
        return _ControlBox(style=style, width=self.widget.width)

    def set_component_state(self, state: ComponentState) -> None:
        if self.component_state is state:
            return
        self.component_state = state
        self.set_state()


# 供外部读取解析结果的便捷入口（检查器与测试用）
def button_style_for(theme: Theme, widget: Button, state: ComponentState) -> ButtonStyle:
    return resolve_button_style(theme, variant=widget.variant, size=widget.size, state=state)


def input_style_for(theme: Theme, widget: Input, state: ComponentState) -> InputStyle:
    return resolve_input_style(theme, size=widget.size, state=state)
