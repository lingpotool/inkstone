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

from collections.abc import Callable

from ..backend.base import PointerKind
from ..core import (
    LeafRenderObjectElement,
    RenderObjectWidget,
    State,
    StatefulWidget,
    Widget,
)
from ..core.element import _SLOT_UNCHANGED, Element
from ..core.key import Key
from ..events.gestures import (
    DoubleTapGestureRecognizer,
    GestureRecognizer,
    LongPressGestureRecognizer,
    TapGestureRecognizer,
)
from ..events.pointer import PointerDispatch
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
        # ---- 输入（R7.1） ----
        #: 指针回调，由 Element 从 State 写进来（RenderObject 拿不到 State/BuildOwner）
        self.on_pointer: Callable[[PointerDispatch], None] | None = None
        #: 是否处于聚焦态（由 Element 从 State 同步，供 IME 上报使用）
        self.focused: bool = False

    # ------------------------------------------------------------ 指针输入

    def handle_pointer_event(self, dispatch: PointerDispatch) -> None:
        """把事件转交给组件层写进来的回调。

        渲染对象不认识 Button/Input 的状态机，只负责"事件到了"——
        状态机住在 State 里，这正是 docs/06 的"元素写、渲染对象读"。
        """
        if self.on_pointer is not None:
            self.on_pointer(dispatch)

    def caret_rect(self) -> Rect:
        """光标矩形（本节点局部坐标，零宽）。

        输入框聚焦时上报给 IME 的候选框锚点。当前还没有文本编辑，
        光标固定在内容区左端、纵向居中——等编辑模型落地后，
        这里换成 `Paragraph.rects_for_range(caret, caret)`。
        """
        line_height = 0.0
        if self.text_style is not None:
            line_height = self.text_style.size * self.text_style.line_height
        height = min(line_height, self.size.height) if line_height > 0.0 else self.size.height
        return Rect(self.padding_h, (self.size.height - height) / 2.0, 0.0, height)

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
        on_pointer: Callable[[PointerDispatch], None] | None = None,
        focused: bool = False,
        wants_text_input: bool = False,
        recognizers: list[GestureRecognizer] | None = None,
    ) -> None:
        self.style = style
        self.width = width
        self.label = label
        self.placeholder = placeholder
        self.center_text = center_text
        self.on_pointer = on_pointer
        self.focused = focused
        self.wants_text_input = wants_text_input
        self.recognizers: list[GestureRecognizer] = recognizers if recognizers is not None else []

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
        render_object.on_pointer = self.on_pointer
        render_object.focused = self.focused
        # 识别器由组件层（知道主题令牌）创建，元素写进渲染对象（R7.2）
        render_object.recognizers = list(self.recognizers)
        if isinstance(self.style, InputStyle):
            render_object.placeholder_color = self.style.placeholder
        render_object.mark_needs_layout()

    def create_element(self) -> Element:
        return _ControlElement(self)


class _ControlElement(LeafRenderObjectElement):
    def __init__(self, widget: _ControlBox) -> None:
        super().__init__(widget)
        self._last_focused = False

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
        if widget.wants_text_input:
            self._sync_text_input(render_object, widget.focused)

    def _sync_text_input(self, render_object: _ControlRenderObject, focused: bool) -> None:
        """输入框聚焦态变化时通知后端：开/关 IME 通道 + 上报候选框位置（R5.7/R7.1）。

        为什么要经 owner 的钩子而不是直接拿 backend：core/widgets 不认识平台对象，
        组装层（App）把 `backend.start_text_input` / `set_ime_rect` 接进来。
        """
        if focused == self._last_focused:
            return
        self._last_focused = focused
        owner = self.owner
        if owner is None:
            return
        if owner.on_text_input is not None:
            owner.on_text_input(focused)
        if focused and owner.on_ime_rect is not None:
            caret = render_object.caret_rect()
            origin = render_object.local_to_global(caret.top_left)
            owner.on_ime_rect(Rect(origin.dx, origin.dy, caret.width, caret.height))


# ---------------------------------------------------------------- Button


class Button(StatefulWidget):
    """按钮。

    状态由 `ComponentState` 驱动：组件不关心"鼠标在哪"，只关心"我现在是什么状态"。
    点击/双击/长按由**手势识别器**在竞技场里裁决（R7.2）——事件不直达组件，
    组件只收到识别器回调（按下/抬起/取消/命中手势）。
    """

    def __init__(
        self,
        label: str = "",
        *,
        on_tap: Callable[[], None] | None = None,
        on_double_tap: Callable[[], None] | None = None,
        on_long_press: Callable[[], None] | None = None,
        variant: ButtonVariant = ButtonVariant.PRIMARY,
        size: str = "md",
        width: float | None = None,
        disabled: bool = False,
        key: Key | None = None,
    ) -> None:
        self.label = label
        self.on_tap = on_tap
        self.on_double_tap = on_double_tap
        self.on_long_press = on_long_press
        self.variant = variant
        self.size = size
        self.width = width
        self.disabled = disabled
        self.key = key

    def create_state(self) -> ButtonState:
        return ButtonState()


class ButtonState(State["Button"]):
    """按钮的交互状态机。

    - hover / 焦点：命中链的 ENTER/LEAVE 直接送达（不是手势竞争）；
    - 点击 / 双击 / 长按：识别器在竞技场里赢下后回调，输了（如被滚动抢走）
      收到 cancel —— ACTIVE 必须退回去。

    状态优先级：ACTIVE（按下）> FOCUS_VISIBLE（聚焦）> HOVER（悬停）> DEFAULT。
    """

    def init_state(self) -> None:
        self.component_state = (
            ComponentState.DISABLED if self.widget.disabled else ComponentState.DEFAULT
        )
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._tap: TapGestureRecognizer | None = None
        self._double: DoubleTapGestureRecognizer | None = None
        self._long: LongPressGestureRecognizer | None = None

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
            on_pointer=self._handle_pointer,
            recognizers=self._build_recognizers(context.theme),
        )

    # ------------------------------------------------------------ 识别器

    def _build_recognizers(self, theme: object) -> list[GestureRecognizer]:
        """按当前 widget 配置组装识别器，阈值一律来自令牌（不许字面量）。"""
        from ..style import Theme  # 局部导入避免循环；Theme 是 L6，widgets 是 L7

        assert isinstance(theme, Theme)
        if self.widget.disabled:
            return []
        slop = theme.gesture("tap_slop")

        recognizers: list[GestureRecognizer] = []
        if self.widget.on_double_tap is not None:
            # 双击识别器顺带负责单击（两者必须在同一个状态机里裁决，见 gestures.py）
            if self._double is None:
                self._double = DoubleTapGestureRecognizer(
                    slop=slop,
                    double_tap_ms=theme.gesture("double_tap_ms"),
                    on_tap=self._fire_tap,
                    on_double_tap=self._fire_double_tap,
                    on_tap_down=self._press_down,
                    on_tap_up=self._press_up,
                    on_cancel=self._press_cancel,
                )
            else:
                self._double.slop = slop
                self._double.double_tap_ms = theme.gesture("double_tap_ms")
            recognizers.append(self._double)
        else:
            if self._tap is None:
                self._tap = TapGestureRecognizer(
                    slop=slop,
                    on_tap=self._fire_tap,
                    on_tap_down=self._press_down,
                    on_tap_up=self._press_up,
                    on_cancel=self._press_cancel,
                )
            else:
                self._tap.slop = slop
            recognizers.append(self._tap)

        if self.widget.on_long_press is not None:
            if self._long is None:
                self._long = LongPressGestureRecognizer(
                    slop=slop,
                    duration_ms=theme.gesture("long_press_ms"),
                    on_long_press=self._fire_long_press,
                )
            else:
                self._long.slop = slop
                self._long.duration_ms = theme.gesture("long_press_ms")
            recognizers.append(self._long)
        return recognizers

    # ------------------------------------------------------------ 识别器回调

    def _press_down(self) -> None:
        self._pressed = True
        self._focused = True
        self._refresh_state()

    def _press_up(self) -> None:
        self._pressed = False
        self._refresh_state()

    def _press_cancel(self) -> None:
        # 竞技场判给别人（滚动获胜）时必须退回按压态
        self._pressed = False
        self._refresh_state()

    def _fire_tap(self) -> None:
        callback = self.widget.on_tap
        if callback is not None:
            callback()

    def _fire_double_tap(self) -> None:
        callback = self.widget.on_double_tap
        if callback is not None:
            callback()

    def _fire_long_press(self) -> None:
        callback = self.widget.on_long_press
        if callback is not None:
            callback()

    # ------------------------------------------------------------ hover / 焦点

    def _handle_pointer(self, dispatch: PointerDispatch) -> None:
        if self.widget.disabled:
            return
        kind = dispatch.event.kind
        if kind is PointerKind.ENTER:
            self._hovered = True
        elif kind is PointerKind.LEAVE:
            self._hovered = False
            self._pressed = False
        else:
            return
        self._refresh_state()

    def _refresh_state(self) -> None:
        if self._pressed:
            state = ComponentState.ACTIVE
        elif self._focused:
            state = ComponentState.FOCUS_VISIBLE
        elif self._hovered:
            state = ComponentState.HOVER
        else:
            state = ComponentState.DEFAULT
        self.set_component_state(state)

    def set_component_state(self, state: ComponentState) -> None:
        """切换交互状态（测试的直接入口）。"""
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
    """输入框的交互状态机（R7.1）。

    点击获焦并通知 IME 通道（`_ControlElement` 观察 `focused` 变化后上报）。
    文本编辑与组合态渲染仍是 Phase 2——本轮只闭合"点击能聚焦"。
    """

    def init_state(self) -> None:
        self.component_state = ComponentState.ERROR if self.widget.error else ComponentState.DEFAULT
        self._focused = False
        self._tap: TapGestureRecognizer | None = None

    @property
    def focused(self) -> bool:
        return self._focused

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
            focused=self._focused,
            wants_text_input=True,
            recognizers=self._build_recognizers(context.theme),
        )

    def _build_recognizers(self, theme: object) -> list[GestureRecognizer]:
        """点击即聚焦。走单击识别器而不是裸 DOWN——这样"点击落在输入框上"
        与滚动/拖拽的竞争语义和按钮完全一致（R7.2）。"""
        from ..style import Theme

        assert isinstance(theme, Theme)
        slop = theme.gesture("tap_slop")
        if self._tap is None:
            self._tap = TapGestureRecognizer(slop=slop, on_tap_down=self._focus)
        else:
            self._tap.slop = slop
        return [self._tap]

    def _focus(self) -> None:
        if self._focused:
            return
        self._focused = True
        self._refresh_state()

    def unfocus(self) -> None:
        """主动失焦（点击别处、Esc）。焦点管理器属后续工作，先留显式入口。"""
        if self._focused:
            self._focused = False
            self._refresh_state()

    def _refresh_state(self) -> None:
        base = ComponentState.ERROR if self.widget.error else ComponentState.DEFAULT
        self.set_component_state(ComponentState.FOCUS_VISIBLE if self._focused else base)

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
