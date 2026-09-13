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

from ..backend.base import ImeEvent, KeyEvent, KeyKind, PointerKind, TextEvent
from ..backend.headless_fonts import grapheme_clusters
from ..core import (
    LeafRenderObjectElement,
    RenderObjectWidget,
    State,
    StatefulWidget,
    Widget,
)
from ..core.element import _SLOT_UNCHANGED, Element
from ..core.key import Key
from ..events.focus import FocusNode, FocusSource
from ..events.gestures import (
    DoubleTapGestureRecognizer,
    GestureRecognizer,
    LongPressGestureRecognizer,
    TapGestureRecognizer,
)
from ..events.ime import ImeSession
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


# ---------------------------------------------------------------- 字素簇辅助
#
# 编辑必须按**字素簇**走，不按码点（AGENT 铁律 2）：否则删一个键会删掉
# emoji 的半张脸、或把 "é"（基字符 + 组合符）拆成两半。


def _prev_index(text: str, index: int) -> int:
    """`index` 左边**一个簇**的起点。"""
    if index <= 0:
        return 0
    for start, end in grapheme_clusters(text):
        if start < index <= end:
            return start
    return 0


def _next_index(text: str, index: int) -> int:
    """`index` 右边**一个簇**的终点。"""
    if index >= len(text):
        return len(text)
    for start, end in grapheme_clusters(text):
        if start <= index < end:
            return end
    return len(text)


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
        # ---- 编辑态几何（R9.3）：全部由 Element 从 State 写入 ----
        #: 光标在**显示文本**里的下标（显示文本 = 正文 + 组合串）；None = 不画
        self.caret: int | None = None
        #: 选区 `[start, end)`（显示文本下标）；start == end 表示无选区
        self.selection: tuple[int, int] | None = None
        #: 组合串在显示文本里的区间 `[start, end)`（画下划线）
        self.composition: tuple[int, int] | None = None
        #: 选区底色（令牌：primary-soft）
        self.selection_color: Color | None = None
        #: 是否处于编辑中（决定要不要省略号、要不要裁剪内容区）
        self.editing: bool = False
        #: 编辑装饰尺寸（元素从主题令牌写入；组件里不许写字面量）
        self.caret_width: float = 0.0
        self.underline_width: float = 0.0
        self.underline_offset: float = 0.0

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

        编辑中由 `Paragraph.rects_for_range(caret, caret)` 给出真实位置——
        IME 候选框要跟着光标走，用固定位置会在中文输入时把候选框钉在左边。
        还没排版（或没有编辑态）时退回"内容区左端 + 纵向居中"。
        """
        paragraph = self.painted_paragraph
        if paragraph is not None and paragraph.lines and self.caret is not None:
            rects = paragraph.rects_for_range(self.caret, self.caret)
            if rects:
                leading = max(0.0, (self.size.height - paragraph.height) / 2.0)
                rect = rects[0]
                extent = max(min(rect.height, paragraph.height), self.caret_width)
                return Rect(
                    self.padding_h + rect.left,
                    leading + rect.top + max(0.0, (rect.height - extent) / 2.0),
                    0.0,
                    extent,
                )
        line_height = 0.0
        if self.text_style is not None:
            line_height = self.text_style.size * self.text_style.line_height
        extent = min(line_height, self.size.height) if line_height > 0.0 else self.size.height
        return Rect(self.padding_h, (self.size.height - extent) / 2.0, 0.0, extent)

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
        # 编辑中**不能加省略号**：`rects_for_range` 的下标要对应到显示文本，
        # 被省略号替换掉的内容会让光标/选区映射整体错位。超出部分靠裁剪挡掉。
        ellipsis = EllipsisMode.NONE if self.editing else EllipsisMode.END
        return self.engine.paragraph(
            text,
            self.text_style,
            max_width=available,
            align=TextAlign.CENTER if self.center_text else TextAlign.START,
            max_lines=1,
            ellipsis=ellipsis,
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

        if self.editing:
            # 编辑中不省略号，内容可能超宽——裁到内边距里（裁剪只影响内容）
            save = getattr(context, "save", None)
            restore = getattr(context, "restore", None)
            clip_rect = getattr(context, "clip_rect", None)
            if callable(save) and callable(restore) and callable(clip_rect):
                save()
                clip_rect(
                    Rect(
                        self.padding_h,
                        0.0,
                        max(0.0, self.size.width - 2.0 * self.padding_h),
                        self.size.height,
                    )
                )
                self._paint_content(context)
                restore()
            else:
                self._paint_content(context)
        else:
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
        origin_x = self.padding_h
        origin_y = leading

        # 选区底色先画（在文字下面）
        fill_rect = getattr(context, "fill_rect", None)
        selection = self.selection
        selection_color = self.selection_color
        if (
            fill_rect is not None
            and selection is not None
            and selection[0] < selection[1]
            and selection_color is not None
        ):
            for rect in paragraph.rects_for_range(selection[0], selection[1]):
                fill_rect(
                    Rect(
                        rect.left + origin_x,
                        rect.top + origin_y,
                        rect.width,
                        rect.height,
                    ),
                    selection_color,
                )

        for layout in paragraph.lines:
            glyphs = glyphs_of_layout(layout)
            if not glyphs:
                continue
            text_run(
                Offset(layout.x + origin_x, layout.origin_y + origin_y),
                layout.line.baseline,
                glyphs,
                self.text_style.size if self.text_style else 0.0,
                color,
            )

        # IME 组合态下划线：按字符区间画一条细线（组合串还没上屏，需要可见反馈）
        composition = self.composition
        if (
            fill_rect is not None
            and composition is not None
            and composition[0] < composition[1]
            and selection_color is not None
        ):
            for rect in paragraph.rects_for_range(composition[0], composition[1]):
                fill_rect(
                    Rect(
                        rect.left + origin_x,
                        rect.top + origin_y + rect.height - self.underline_offset,
                        rect.width,
                        self.underline_width,
                    ),
                    selection_color,
                )

        # 光标：零宽矩形 → 画成一条竖线，纵向按行高居中
        caret = self.caret
        if fill_rect is not None and caret is not None and color is not None:
            for rect in paragraph.rects_for_range(caret, caret):
                caret_extent = max(min(rect.height, paragraph.height), self.caret_width)
                fill_rect(
                    Rect(
                        rect.left + origin_x,
                        rect.top + origin_y + max(0.0, (rect.height - caret_extent) / 2.0),
                        self.caret_width,
                        caret_extent,
                    ),
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
        editing: bool = False,
        caret: int | None = None,
        selection: tuple[int, int] | None = None,
        composition: tuple[int, int] | None = None,
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
        self.editing = editing
        self.caret = caret
        self.selection = selection
        self.composition = composition

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
        render_object.editing = self.editing
        render_object.caret = self.caret
        render_object.selection = self.selection
        render_object.composition = self.composition
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
        self._last_ime_key: tuple[object, ...] | None = None

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
        if isinstance(widget.style, InputStyle):
            # 选区底色与编辑装饰都来自令牌（render object 读不到主题，只能元素写）
            render_object.selection_color = theme.color("primary-soft")
            render_object.caret_width = theme.decoration("caret_width")
            render_object.underline_width = theme.decoration("underline_width")
            render_object.underline_offset = theme.decoration("underline_offset")
        # 环境文本引擎由元素注入（RenderObject 拿不到 BuildOwner）
        render_object.engine = self.text_engine
        render_object.mark_needs_layout()
        render_object.mark_needs_paint()
        if widget.wants_text_input:
            self._sync_text_input(render_object, widget.focused)
            if widget.editing and widget.caret is not None:
                # 候选框跟随光标：在**构建时**上报，此时段落几何是上一帧布局的、
                # 光标是本次构建的新值——移动光标时段落没变，所以位置是准的。
                # 若等 State 在事件处理里上报，渲染对象还停在旧光标上，锚点不动。
                self._report_ime_rect(render_object)

    def _report_ime_rect(self, render_object: _ControlRenderObject) -> None:
        owner = self.owner
        if owner is None or owner.on_ime_rect is None:
            return
        key = (render_object.caret, render_object.label)
        if key == self._last_ime_key:
            return
        self._last_ime_key = key
        caret = render_object.caret_rect()
        origin = render_object.local_to_global(caret.top_left)
        owner.on_ime_rect(Rect(origin.dx, origin.dy, caret.width, caret.height))

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
        #: 键盘来源的聚焦才画环（focus-visible，docs/13 §5）
        self._focus_visible = False
        self._tap: TapGestureRecognizer | None = None
        self._double: DoubleTapGestureRecognizer | None = None
        self._long: LongPressGestureRecognizer | None = None
        self._focus_node = FocusNode(on_change=self._on_focus_change, debug_name="Button")

    def build(self, context: object) -> Widget:
        assert isinstance(context, Element)
        manager = context.owner.focus_manager if context.owner is not None else None
        if manager is not None and not self._focus_node.attached:
            manager.attach(self._focus_node)
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

    def _on_focus_change(self, focused: bool, visible: bool) -> None:
        # 鼠标点击给的焦点不算 focus-visible：按钮上不该出现焦点环
        del focused
        self._focus_visible = visible
        self._refresh_state()

    def dispose(self) -> None:
        self._focus_node.unfocus()
        manager = self._focus_node._manager
        if manager is not None:
            manager.detach(self._focus_node)

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
        # 点击让按钮获得焦点（键盘从这里接着走），但**不是** focus-visible：
        # 环只有 Tab 导航才显示。焦点系统同时负责"点别处自动失焦"。
        self._focus_node.request_focus(FocusSource.POINTER)
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
        elif self._focus_visible:
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
    """输入框：可输入、可删除、光标位置正确（Phase 1 DoD，R9.3）。

    编辑模型是 `text + selection(anchor, focus) + composition`（docs/04 §6）：

    - **正文**（text）与**组合串**（composition）分开存——组合态不上屏，
      CANCEL 因此是免费的（丢掉叠加层即可，不用从正文里删字）；
    - 选区用 `anchor/focus` 两个下标（焦点是"活动端"），移动/删除**按字素簇**
      走（不劈 emoji）；
    - 键盘/文本/IME 事件由**焦点系统**送到这里（`FocusNode.on_event`），
      组件不自己去嗅探全局事件。
    """

    def __init__(
        self,
        value: str = "",
        *,
        placeholder: str = "",
        on_changed: Callable[[str], None] | None = None,
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
    """输入框的编辑状态机（R9.3）。"""

    def init_state(self) -> None:
        self.component_state = ComponentState.ERROR if self.widget.error else ComponentState.DEFAULT
        self._text: str = self.widget.value
        end = len(self._text)
        #: 选区两端（焦点是活动端）；`anchor == focus` 即无选区
        self._anchor: int = end
        self._caret: int = end
        self._ime = ImeSession()
        self._composition: str | None = None
        self._focused = False
        self._focus_visible = False
        self._last_pointer_x: float = 0.0
        self._tap: TapGestureRecognizer | None = None
        self._focus_node = FocusNode(
            on_event=self._handle_event, on_change=self._on_focus_change, debug_name="Input"
        )
        self._manager: object | None = None

    # ------------------------------------------------------------ 查询

    @property
    def focused(self) -> bool:
        return self._focused

    @property
    def text(self) -> str:
        """当前正文（不含未上屏的组合串）。"""
        return self._text

    @property
    def selection(self) -> tuple[int, int]:
        return self._selection_range()

    @property
    def composition(self) -> str | None:
        return self._composition

    # ------------------------------------------------------------ 构建

    def build(self, context: object) -> Widget:
        assert isinstance(context, Element)
        manager = context.owner.focus_manager if context.owner is not None else None
        if manager is not None and not self._focus_node.attached:
            manager.attach(self._focus_node)
            self._manager = manager
        style = resolve_input_style(
            context.theme,
            size=self.widget.size,
            state=self.component_state,
        )
        display, caret_value, selection_value, composition_value = self._display_state()
        # 未聚焦不画光标/选区/组合线（否则黄金图里每个输入框都挂着一条竖线）
        caret: int | None = caret_value if self._focused else None
        selection: tuple[int, int] | None = selection_value if self._focused else None
        composition: tuple[int, int] | None = composition_value if self._focused else None
        return _ControlBox(
            style=style,
            width=self.widget.width,
            label=display,
            placeholder=self.widget.placeholder,
            center_text=False,
            focused=self._focused,
            wants_text_input=True,
            recognizers=self._build_recognizers(context.theme),
            editing=self._focused,
            caret=caret,
            selection=selection,
            composition=composition,
            on_pointer=self._handle_pointer,
        )

    def _display_state(
        self,
    ) -> tuple[str, int, tuple[int, int], tuple[int, int] | None]:
        """渲染用的显示文本 + 光标 + 选区 + 组合区间（都是**显示文本**下标）。

        组合串插在选区处（选区被组合串临时"顶替"）；组合中不显示选区——
        这与浏览器一致，也是唯一能让组合态下划线范围无歧义的做法。
        """
        start, end = self._selection_range()
        composition = self._composition
        if composition:
            display = self._text[:start] + composition + self._text[end:]
            caret = start + len(composition)
            return display, caret, (caret, caret), (start, caret)
        return self._text, self._caret, (start, end), None

    def dispose(self) -> None:
        self._focus_node.unfocus()
        manager = self._focus_node._manager
        if manager is not None:
            manager.detach(self._focus_node)

    def did_update_widget(self, old_widget: Input) -> None:
        # 外部换了 value（受控用法）：采纳新值，光标移到末尾
        if self.widget.value != old_widget.value and self.widget.value != self._text:
            self._text = self.widget.value
            self._caret = self._anchor = len(self._text)
            self._composition = None
            self._ime.cancel()

    # ------------------------------------------------------------ 识别器 / 指针

    def _build_recognizers(self, theme: object) -> list[GestureRecognizer]:
        """点击即聚焦。走单击识别器而不是裸 DOWN——这样"点击落在输入框上"
        与滚动/拖拽的竞争语义和按钮完全一致（R7.2）。"""
        from ..style import Theme

        assert isinstance(theme, Theme)
        slop = theme.gesture("tap_slop")
        if self._tap is None:
            self._tap = TapGestureRecognizer(slop=slop, on_tap_down=self._focus_from_pointer)
        else:
            self._tap.slop = slop
        return [self._tap]

    def _handle_pointer(self, dispatch: PointerDispatch) -> None:
        """记住按下的水平位置：抬手判定聚焦时据此把光标插到点击处。"""
        if dispatch.event.kind is PointerKind.DOWN:
            self._last_pointer_x = dispatch.local_x

    def _focus_from_pointer(self) -> None:
        self._focus_node.request_focus(FocusSource.POINTER)
        if not self._focused:
            return
        # 点击定位光标：不做这一下，用户点哪儿都只能跑到末尾——不像专业输入框
        render_object = self.context.find_render_object()
        paragraph = getattr(render_object, "painted_paragraph", None)
        padding = getattr(render_object, "padding_h", 0.0)
        if paragraph is None:
            return
        index = paragraph.position_for_point(
            self._last_pointer_x - padding, max(1.0, paragraph.height / 2.0)
        )
        index = max(0, min(index, len(self._text)))
        self._caret = self._anchor = index
        self._composition = None
        self._ime.cancel()
        self._after_edit()

    def _on_focus_change(self, focused: bool, visible: bool) -> None:
        self._focused = focused
        self._focus_visible = visible
        if not focused:
            # 失焦即丢弃未上屏的组合态（不上屏是 IME 的标准行为）
            self._composition = None
            self._ime.cancel()
        self._refresh_state()

    def unfocus(self) -> None:
        """主动失焦（点击别处由焦点管理器负责；这里留显式入口）。"""
        self._focus_node.unfocus()

    def _refresh_state(self) -> None:
        base = ComponentState.ERROR if self.widget.error else ComponentState.DEFAULT
        # 输入框与按钮不同：鼠标点击聚焦也要显示焦点边框（与浏览器 :focus-visible
        # 对文本输入的行为一致——你马上要打字，反馈必须可见）
        self.set_component_state(ComponentState.FOCUS_VISIBLE if self._focused else base)

    def set_component_state(self, state: ComponentState) -> None:
        if self.component_state is state:
            return
        self.component_state = state
        self.set_state()

    # ------------------------------------------------------------ 事件处理（焦点系统送达）

    def _handle_event(self, event: object) -> bool:
        if isinstance(event, (TextEvent, ImeEvent)):
            self._apply_effect(self._ime.feed(event))
            return True
        if isinstance(event, KeyEvent) and event.kind is KeyKind.DOWN:
            return self._handle_key(event)
        return False

    def _handle_key(self, event: KeyEvent) -> bool:
        ctrl = event.modifiers.ctrl or event.modifiers.meta
        code = event.code

        if code == "Backspace":
            self._delete_backward()
        elif code == "Delete":
            self._delete_forward()
        elif code == "ArrowLeft":
            self._move_caret(-1, extend=event.modifiers.shift)
        elif code == "ArrowRight":
            self._move_caret(1, extend=event.modifiers.shift)
        elif code == "Home":
            self._set_caret(0, extend=event.modifiers.shift)
        elif code == "End":
            self._set_caret(len(self._text), extend=event.modifiers.shift)
        elif code == "KeyA" and ctrl:
            self._anchor, self._caret = 0, len(self._text)
            self._after_edit()
        elif code == "KeyC" and ctrl:
            self._copy()
        elif code == "KeyX" and ctrl:
            self._cut()
        elif code == "KeyV" and ctrl:
            self._paste()
        elif code == "Escape" and self._composition is not None:
            self._ime.cancel()
            self._composition = None
            self._after_edit()
        else:
            return False
        return True

    # ------------------------------------------------------------ 编辑操作

    def _selection_range(self) -> tuple[int, int]:
        return (min(self._anchor, self._caret), max(self._anchor, self._caret))

    def _replace_selection(self, insert: str) -> None:
        start, end = self._selection_range()
        self._text = self._text[:start] + insert + self._text[end:]
        self._caret = self._anchor = start + len(insert)

    def _insert(self, text: str) -> None:
        if not text:
            return
        self._composition = None
        self._replace_selection(text)
        self._after_edit()

    def _delete_backward(self) -> None:
        start, end = self._selection_range()
        if start != end:
            self._replace_selection("")
        elif self._caret > 0:
            prev = _prev_index(self._text, self._caret)
            self._text = self._text[:prev] + self._text[self._caret :]
            self._caret = self._anchor = prev
        else:
            return
        self._composition = None
        self._after_edit()

    def _delete_forward(self) -> None:
        start, end = self._selection_range()
        if start != end:
            self._replace_selection("")
        elif self._caret < len(self._text):
            nxt = _next_index(self._text, self._caret)
            self._text = self._text[: self._caret] + self._text[nxt:]
        else:
            return
        self._composition = None
        self._after_edit()

    def _move_caret(self, step: int, *, extend: bool) -> None:
        start, end = self._selection_range()
        if not extend and start != end:
            # 无 Shift 时先把选区收成一点（向左收左端、向右收右端）
            self._caret = start if step < 0 else end
        else:
            target = (
                _prev_index(self._text, self._caret)
                if step < 0
                else _next_index(self._text, self._caret)
            )
            self._caret = target
        if not extend:
            self._anchor = self._caret
        self._composition = None
        self._after_edit()

    def _set_caret(self, index: int, *, extend: bool) -> None:
        self._caret = max(0, min(index, len(self._text)))
        if not extend:
            self._anchor = self._caret
        self._composition = None
        self._after_edit()

    def _copy(self) -> None:
        start, end = self._selection_range()
        if start == end:
            return
        writer = self._clipboard_set()
        if writer is not None:
            writer(self._text[start:end])

    def _cut(self) -> None:
        start, end = self._selection_range()
        if start == end:
            return
        writer = self._clipboard_set()
        if writer is not None:
            writer(self._text[start:end])
        self._replace_selection("")
        self._composition = None
        self._after_edit()

    def _paste(self) -> None:
        reader = self._clipboard_get()
        if reader is None:
            return
        text = reader()
        if text:
            self._insert(text)

    def _clipboard_get(self) -> Callable[[], str] | None:
        owner = self.context.owner
        return owner.clipboard_get if owner is not None else None

    def _clipboard_set(self) -> Callable[[str], None] | None:
        owner = self.context.owner
        return owner.clipboard_set if owner is not None else None

    # ------------------------------------------------------------ 收尾

    def _apply_effect(self, effect: object) -> None:
        """把 `ImeSession` 的作用落到编辑模型上。"""
        insert = getattr(effect, "insert", "")
        composition = getattr(effect, "composition", None)
        if insert:
            self._insert(insert)
            return
        self._composition = None if composition is None else composition.text
        self._after_edit()

    def _after_edit(self) -> None:
        """编辑后的统一收尾：请求重建，回调外部。

        候选框位置**不在这里上报**——此刻渲染对象还停在旧光标/旧段落上，
        要等本次构建把新光标写进去；上报由 `_ControlElement._apply` 做。
        """
        self.set_state()
        callback = self.widget.on_changed
        if callback is not None:
            callback(self._text)


# 供外部读取解析结果的便捷入口（检查器与测试用）
def button_style_for(theme: Theme, widget: Button, state: ComponentState) -> ButtonStyle:
    return resolve_button_style(theme, variant=widget.variant, size=widget.size, state=state)


def input_style_for(theme: Theme, widget: Input, state: ComponentState) -> InputStyle:
    return resolve_input_style(theme, size=widget.size, state=state)
