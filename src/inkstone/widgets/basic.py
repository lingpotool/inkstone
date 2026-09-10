"""基础组件：Box / Card / Text。

Box 是"一块有尺寸、有颜色、有圆角的矩形"——卡片、占位、分隔都从它来。
Card 是 Box 的语义化封装：默认带上内边距，让"卡片"在代码里读起来就是卡片，
而不是一堆 Box 参数。

**零硬编码**：这里没有出现任何字面量颜色、字号或间距。
Card 的默认内边距来自 `context.theme`（环境主题挂在 BuildOwner 上），
而不是写死 16——否则换主题时它就换不动了。Text 同理：字号与行高都来自
主题的 `type_scale` 令牌，字体链来自 `font_sans`。

关于 Text 与度量（docs/04 §3）：

    Text 的尺寸**完全来自真实字体度量**，经由 `context.text_engine`。
    `perform_layout` 返回的尺寸就是渲染时实际画出来的尺寸——
    它们是同一次排版的产物，不存在"量出来高、画出来矮"的可能。

    它刻意**不**提供"每字 N 像素"的估算路径。中英混排、字号变化、
    字体回退都会让估算悄悄算错，而且错得极难排查。

状态：Box / Card / Text 已实现。
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
from ..gfx.display_list import PositionedGlyph
from ..layout import BoxConstraints, RenderBox, Size
from ..layout.types import EdgeInsets, Offset, Rect
from ..text import EllipsisMode, FontWeight, Paragraph, TextAlign, TextEngine, TextStyle

__all__ = ["Box", "Card", "Text"]


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


# ---------------------------------------------------------------- Text


class _TextRenderObject(RenderBox):
    """文本的渲染对象：只做两件事——按约束排版、把排版结果画出去。

    尺寸规则（`perform_layout`）体现文本组件的典型语义：

    - 给定有限宽 → 按该宽度换行，高度是**行数 × 行高**；
    - 给定无限宽 → 不换行，宽度等于整段文字的度量宽度
      （这是 Row 里放一个 Text 的正常情况：文本不该被硬塞成多行）；
    - 给定有限高且内容更高 → 标记溢出（铁律 5：不许静默裁切）。

    `painted_paragraph` 记下本次布局用的段落对象，绘制时直接复用——
    这样"量出来的几何"与"画出来的几何"必然是同一份数据，
    不存在两套度量对不上的可能（docs/04 §3）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.text: str = ""
        self.style: TextStyle | None = None
        self.color: Color | None = None
        self.align: TextAlign = TextAlign.START
        self.max_lines: int | None = None
        self.ellipsis: EllipsisMode = EllipsisMode.END
        self.soft_wrap: bool = True
        # 由 Element 注入：RenderObject 拿不到 BuildOwner（只有 Element 能），
        # 所以环境服务一律"元素写、渲染对象读"，与 style/color 同一套路。
        self.engine: TextEngine | None = None
        self.painted_paragraph: Paragraph | None = None

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        engine = self.engine
        style = self.style
        if engine is None or style is None or not self.text:
            # 没有文本引擎（纯布局测试）或空文本：零尺寸，不抛异常。
            # 组件在缺少环境服务时应当退化，而不是让整棵树崩掉。
            self.painted_paragraph = None
            return constraints.constrain(Size(0.0, 0.0))

        # 可用宽度：不允许换行时按"无限宽"处理，让文本保持单行
        if not self.soft_wrap or not constraints.has_bounded_width:
            available = 0.0
        else:
            available = constraints.max_width

        paragraph = engine.paragraph(
            self.text,
            style,
            max_width=available,
            align=self.align,
            max_lines=self.max_lines,
            ellipsis=self.ellipsis,
        )
        self.painted_paragraph = paragraph

        size = constraints.constrain(Size(paragraph.width, paragraph.height))
        # 内容比盒子大 → 标记溢出给检查器看，绝不悄悄裁掉（铁律 5）
        self.note_overflow(max(paragraph.height - size.height, paragraph.width - size.width))
        return size

    def paint(self, context: object) -> None:
        paragraph = self.painted_paragraph
        color = self.color
        style = self.style
        if paragraph is None or color is None or style is None or color.a == 0.0:
            return
        text_run = getattr(context, "text_run", None)
        if text_run is None:
            return

        for layout in paragraph.lines:
            glyphs = _glyphs_of(layout)
            if not glyphs:
                continue
            # 逐行提交。注意两个坐标：`origin` 是**行框左上角**，
            # `baseline` 是基线相对行框顶的距离——两者都来自本次排版结果，
            # 绘制层不需要知道行高、对齐、换行的任何规则。
            text_run(
                Offset(layout.x, layout.origin_y),
                layout.line.baseline,
                glyphs,
                style.size,
                color,
            )


def _glyphs_of(layout: object) -> tuple[PositionedGlyph, ...]:
    """把一行的整形结果转成显示列表要的字形序列。

    放在这里（而不是 gfx）是因为它是**跨层适配**：把 L3 的 `ShapedLine`
    翻译成 L2 的指令数据。适配代码属于上层，gfx 不该认识 text 的类型。

    簇文本由 `ShapedCluster.start/end` 切平面字符串得到——簇本来就记着
    自己在源文本里的下标，这里只是把它取出来。
    """
    from ..text import ParagraphLayout

    if not isinstance(layout, ParagraphLayout):
        return ()
    line = layout.line
    return tuple(
        PositionedGlyph(
            text=line.text[cluster.start : cluster.end],
            x=cluster.x,
            advance=cluster.advance,
            family=cluster.family,
        )
        for cluster in line.clusters
    )


class Text(RenderObjectWidget):
    """一段文本。

    尺寸**来自真实字体度量**，不含任何估算——中英混排、字号变化、
    字体回退都不会算错（docs/04 §3 的铁律）。

    `size` 是主题 `type_scale` 里的**档位名**（`"sm"` / `"md"` / `"lg"`…），
    不是像素值。想换字号请用档位，这样行高会跟着一起变——
    直接写像素会绕过行高令牌，中文行距立刻失准。
    """

    def __init__(
        self,
        text: str,
        *,
        size: str = "md",
        weight: FontWeight = FontWeight.REGULAR,
        color: Color | None = None,
        align: TextAlign = TextAlign.START,
        max_lines: int | None = None,
        ellipsis: EllipsisMode = EllipsisMode.END,
        wrap: bool = True,
        key: Key | None = None,
    ) -> None:
        self.text = text
        self.size = size
        self.weight = weight
        self.color = color
        self.align = align
        self.max_lines = max_lines
        self.ellipsis = ellipsis
        self.wrap = wrap
        self.key = key

    def create_render_object(self) -> _TextRenderObject:
        # 注意：挂载时框架**只调 create_render_object**，不调 update。
        # 所以这里必须把字段全部填满——只建一个空对象会让首次布局量到空串，
        # 表现为"文字没画出来但也不报错"，极其难查。
        render_object = _TextRenderObject()
        self._configure(render_object)
        return render_object

    def update_render_object(self, render_object: RenderBox) -> None:
        assert isinstance(render_object, _TextRenderObject)
        self._configure(render_object)
        render_object.mark_needs_layout()

    def _configure(self, render_object: _TextRenderObject) -> None:
        """把 widget 上的字段写进 RenderObject。挂载与更新共用同一份逻辑，
        免得两条路径漏字段（漏掉的那个字段只在更新后生效，是经典陷阱）。"""
        render_object.text = self.text
        render_object.align = self.align
        render_object.max_lines = self.max_lines
        render_object.ellipsis = self.ellipsis
        render_object.soft_wrap = self.wrap

    def create_element(self) -> Element:
        return _TextElement(self)


class _TextElement(LeafRenderObjectElement):
    """把主题令牌翻译成 `TextStyle` 写进 RenderObject。

    翻译放在这里（L7）而不是 `text/`（L3），是分层的要求：
    `text/` 不认识"主题令牌"这个概念，它只认识 `TextStyle`。
    哪一层知道"md 是多少像素"，哪一层就负责翻译。
    """

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self._apply_style()

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self._apply_style()

    def _apply_style(self) -> None:
        widget = self.widget
        assert isinstance(widget, Text)
        render_object = self.render_object
        if render_object is None:
            return
        assert isinstance(render_object, _TextRenderObject)

        theme = self.theme
        font_size = theme.font_size(widget.size)
        render_object.style = TextStyle(
            families=theme.font_sans,
            size=float(font_size.px),
            weight=widget.weight,
            line_height=font_size.line_height,
        )
        # 文本色若未指定，用主题的正文色令牌——组件里不许出现字面量颜色
        render_object.color = widget.color if widget.color is not None else theme.color("text")
        # 环境文本引擎由元素注入（RenderObject 拿不到 BuildOwner）
        render_object.engine = self.text_engine
        render_object.mark_needs_layout()
        render_object.mark_needs_paint()
