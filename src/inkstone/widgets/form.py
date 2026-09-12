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
from ..layout.types import Offset, Rect
from ..style import (
    ButtonStyle,
    ButtonVariant,
    ComponentState,
    InputStyle,
    Theme,
    resolve_button_style,
    resolve_input_style,
)
from ..text import EllipsisMode, Paragraph, TextAlign, TextEngine, TextStyle
from .basic import glyphs_of_layout

__all__ = ["Button", "Input"]


# ---------------------------------------------------------------- 渲染对象


class _ControlRenderObject(RenderBox):
    """Button 与 Input 共用的渲染对象。

    尺寸规则：

        - 显式给了 `width` → 用它；
        - 没给且约束是紧的（比如 Flex 里 `flex=1` 分到的份额）→ 撑满，
          否则"要它撑满"的意图会落空；
        - 没给且是 Button → **按标签文字的真实度量收缩**（加上两侧内边距）；
        - 没给且是 Input → 撑满可用宽度（输入框应当占满一行）。

    第 3 条是文本栈落地后才敢做的：早先只能写死宽度或撑满，
    因为**文字宽度不许估算**（docs/04 §3）。
    """

    def __init__(self) -> None:
        super().__init__()
        # 下面这些字段**全部由 Element 从主题令牌填入**（`_ControlBox._apply`）：
        # RenderObject 拿不到 BuildOwner，所以外观一律"元素写、渲染对象读"。
        # 初值刻意取中性值而不是像 36.0 / 10.0 / 12.0 这样的**设计值**——
        # 后者会让"漏填"表现为一个看起来还挺正常的控件，而不是明显画错。
        self.height_value: float = 0.0
        self.width_value: float | None = None
        self.bg: Color | None = None
        self.fg: Color | None = None
        self.border: Color | None = None
        self.placeholder_color: Color | None = None
        self.border_width: float = 0.0
        self.radius: float = 0.0
        self.focus_ring: Color | None = None
        self.focus_ring_width: float = 0.0
        # ---- 文本 ----
        self.label: str = ""
        self.placeholder: str = ""
        self.text_style: TextStyle | None = None
        self.padding_h: float = 0.0
        #: 按钮居中、输入框左对齐
        self.center_text: bool = True
        #: 由 Element 注入的环境文本引擎（RenderObject 拿不到 BuildOwner）
        self.engine: TextEngine | None = None
        self.painted_paragraph: Paragraph | None = None

    # ------------------------------------------------------------ 内容文字

    def _content_text(self) -> tuple[str, bool]:
        """要显示的文字 + 是否是占位符。

        输入框有值时显示值，没值时显示占位符（用占位符色）。
        """
        if self.label:
            return self.label, False
        if self.placeholder:
            return self.placeholder, True
        return "", False

    def _measure_content_width(self) -> float:
        """内容文字的真实宽度。没有引擎时返回 0（退化为最小宽度）。"""
        text, _ = self._content_text()
        if not text or self.engine is None or self.text_style is None:
            return 0.0
        return self.engine.measure_width(text, self.text_style)

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        height = self.height_value
        width = self.width_value
        if width is None:
            if constraints.is_tight:
                # 紧约束意味着父级已经决定了尺寸（例如 flex 份额）——
                # 必须照办，否则 Button 在 Flexible 里不会撑满。
                width = constraints.max_width
            elif not self.center_text:
                # 输入框：撑满可用宽度
                width = constraints.max_width if constraints.has_bounded_width else 0.0
            else:
                # 按钮：按标签的真实度量收缩
                width = self._measure_content_width() + 2.0 * self.padding_h
                if constraints.has_bounded_width:
                    width = min(width, constraints.max_width)

        size = constraints.constrain(Size(width, height))

        # 排版内容文字（宽度取内边距内的可用空间，超出则省略号收尾）
        self.painted_paragraph = self._layout_content(size)
        return size

    def _layout_content(self, size: Size) -> Paragraph | None:
        text, _ = self._content_text()
        if not text or self.engine is None or self.text_style is None:
            return None
        available = max(0.0, size.width - 2.0 * self.padding_h)
        return self.engine.paragraph(
            text,
            self.text_style,
            max_width=available,
            align=TextAlign.CENTER if self.center_text else TextAlign.START,
            max_lines=1,
            ellipsis=EllipsisMode.END,
        )

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

        self._paint_content(context)

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

    def _paint_content(self, context: object) -> None:
        """绘制标签 / 值 / 占位符。

        纵向居中：控件高度通常大于行高，文字要垂直居中而不是贴顶——
        按钮上文字贴顶是肉眼一眼就能看出的"没做完"。
        """
        paragraph = self.painted_paragraph
        text_run = getattr(context, "text_run", None)
        if paragraph is None or text_run is None or not paragraph.lines:
            return

        _, is_placeholder = self._content_text()
        color = self.placeholder_color if is_placeholder else self.fg
        if color is None or color.a == 0.0:
            return

        leading = max(0.0, (self.size.height - paragraph.height) / 2.0)
        for layout in paragraph.lines:
            glyphs = glyphs_of_layout(layout)
            if not glyphs:
                continue
            text_run(
                Offset(layout.x + self.padding_h, layout.origin_y + leading),
                layout.line.baseline,
                glyphs,
                self.text_style.size if self.text_style else 0.0,
                color,
            )


class _ControlBox(RenderObjectWidget):
    """承载解析好样式的渲染组件。样式在这里已经是具体值了。"""

    def __init__(
        self,
        *,
        style: ButtonStyle | InputStyle,
        width: float | None = None,
        label: str = "",
        placeholder: str = "",
        center_text: bool = True,
    ) -> None:
        self.style = style
        self.width = width
        self.label = label
        self.placeholder = placeholder
        self.center_text = center_text

    def create_render_object(self) -> _ControlRenderObject:
        render_object = _ControlRenderObject()
        self._apply(render_object)
        return render_object

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
        render_object.padding_h = self.style.padding_h
        render_object.label = self.label
        render_object.placeholder = self.placeholder
        render_object.center_text = self.center_text
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
        render_object = self.render_object
        if render_object is None:
            return
        assert isinstance(render_object, _ControlRenderObject)
        widget._apply(render_object)
        # 文字样式从主题令牌翻译（组件层负责"md 是多少像素"这类知识）
        theme = self.theme
        font = widget.style.font
        render_object.text_style = TextStyle(
            families=theme.font_sans,
            size=float(font.px),
            line_height=font.line_height,
        )
        # 环境文本引擎由元素注入（RenderObject 拿不到 BuildOwner）
        render_object.engine = self.text_engine
        render_object.mark_needs_layout()
        render_object.mark_needs_paint()


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
        # 标签交给渲染对象去量——**不在这里估算宽度**（docs/04 §3）
        return _ControlBox(
            style=style,
            width=self.widget.width,
            label=self.widget.label,
            center_text=True,
        )

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
        return _ControlBox(
            style=style,
            width=self.widget.width,
            label=self.widget.value,
            placeholder=self.widget.placeholder,
            center_text=False,
        )

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
