"""布局组件：Row / Column / Flexible / ScrollView。

`Flexible` 是个有意思的东西：**它自己不产生 RenderObject**，
只给父级携带"这个子级要参与剩余空间分配，权重是多少"这条信息。
所以它在 Element 树里不留节点——少了这一层，diff 与热重载都更省。

```python
Row(children=[
    Box(width=40, height=20),
    Flexible(Input(), flex=1),   # 吃掉剩余宽度
])
```

`ScrollView` 包住 `RenderScroll`，并在挂载时注册**拖拽识别器**（R7.2）——
滚动不是"容器自己看滚动事件"，而是和按钮的 tap 在同一竞技场里竞争：
8px 内抬起是点击，超过则滚动获胜。

状态：Row / Column / Flexible / ScrollView 已实现。
Grid / Stack 的 Widget 包装待各自有真实用例时补（渲染对象已就绪，包一层很快）。
"""

from __future__ import annotations

from collections.abc import Callable

from ..core import (
    MultiChildRenderObjectElement,
    RenderObjectElement,
    RenderObjectWidget,
    Widget,
)
from ..core.element import _SLOT_UNCHANGED, Element
from ..core.key import Key
from ..events.gestures import DragGestureRecognizer, HandleDragRecognizer
from ..layout import (
    Axis,
    RenderBox,
    RenderColumn,
    RenderFlex,
    RenderRow,
    RenderScroll,
    ScrollbarStyle,
    ScrollDirection,
)
from ..layout.protocol import CrossAxisAlignment, MainAxisAlignment, MainAxisSize
from ..layout.types import EdgeInsets, Offset
from ..motion import AnimatedValue, reduced_motion
from ..motion.ticker import Tickable

__all__ = ["Column", "Flex", "Flexible", "Row", "ScrollView"]


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


class ScrollView(RenderObjectWidget):
    """可滚动容器。

    子级拿到无限主轴约束（想多长就多长），视口由父级约束决定；
    滚出视口的内容由 `RenderScroll.paint_clip` 裁掉，命中测试也点不到。
    """

    def __init__(
        self,
        child: Widget,
        *,
        direction: ScrollDirection = ScrollDirection.VERTICAL,
        key: Key | None = None,
    ) -> None:
        self.child = child
        self.direction = direction
        self.key = key

    def create_render_object(self) -> RenderScroll:
        return RenderScroll(direction=self.direction)

    def update_render_object(self, render_object: RenderBox) -> None:
        assert isinstance(render_object, RenderScroll)
        render_object.direction = self.direction
        render_object.mark_needs_layout()

    def create_element(self) -> Element:
        return _ScrollElement(self)


class _ScrollElement(RenderObjectElement):
    """ScrollView 的单子级同步 + 拖拽识别器注入（与 Card 同一套路）。"""

    def __init__(self, widget: ScrollView) -> None:
        super().__init__(widget)
        self._child: Element | None = None
        self._drag: DragGestureRecognizer | None = None
        #: 滚动条淡出：动画对象归元素所有（见 `_pump_fade` 的分层说明）
        self._fade: AnimatedValue | None = None
        self._fade_runner: _ScrollbarFade | None = None

    def insert_child_render_object(self, child: RenderBox, slot: object | None) -> None:
        scroll = self.render_object
        assert isinstance(scroll, RenderScroll)
        scroll.child = child

    def remove_child_render_object(self, child: RenderBox) -> None:
        scroll = self.render_object
        if isinstance(scroll, RenderScroll) and scroll.child is child:
            scroll.child = None

    def mount(self, parent: Element | None, slot: object | None) -> None:
        super().mount(parent, slot)
        self._apply()
        self._sync_child()

    def perform_rebuild(self) -> None:
        self._sync_child()

    def update(self, new_widget: Widget, slot: object = _SLOT_UNCHANGED) -> None:
        super().update(new_widget, slot)
        self._apply()
        self._sync_child()

    def _apply(self) -> None:
        widget = self.widget
        assert isinstance(widget, ScrollView)
        scroll = self.render_object
        if not isinstance(scroll, RenderScroll):
            return
        theme = self.theme
        slop = theme.gesture("tap_slop")
        vertical = widget.direction is not ScrollDirection.HORIZONTAL
        horizontal = widget.direction is not ScrollDirection.VERTICAL
        if self._drag is None:
            self._drag = DragGestureRecognizer(
                slop=slop,
                horizontal=horizontal,
                vertical=vertical,
                on_update=lambda dx, dy: scroll.scroll_by(dx, dy),
            )
        else:
            self._drag.slop = slop
            self._drag.horizontal = horizontal
            self._drag.vertical = vertical
        # 识别器由元素写进渲染对象（RenderObject 拿不到主题令牌，R7.2）
        # **顺序有意为之**：内容拖拽在前、把手拖拽在后。竞技场按注册顺序投递
        # 抬起事件，拇指那个要在内容拖拽自决之后再退出，收敛时机才和以前一致。
        scroll.recognizers = [self._drag, self._thumb_drag(scroll)]
        # 滚轮步长同理：布局层不读主题，令牌值由元素送进去（R12）
        scroll.wheel_step = theme.gesture("wheel_step")
        # 滚动条外观：几何来自 tokens.scrollbar，颜色来自语义色（R13.1）；
        # 时长过一遍"减少动效"（R14.1）——系统要求减少动画时归零 = 瞬时到位。
        scroll.scrollbar = ScrollbarStyle(
            thickness=theme.scrollbar("thickness"),
            hover_thickness=theme.scrollbar("hover_thickness"),
            min_thumb=theme.scrollbar("min_thumb"),
            radius=theme.scrollbar("radius"),
            margin=theme.scrollbar("margin"),
            color=theme.color("scrollbar"),
            hover_color=theme.color("scrollbar-hover"),
            hold_ms=theme.scrollbar("hold_ms"),
            fade_ms=reduced_motion.duration(theme.scrollbar("fade_ms")),
        )
        # 淡出动画由**元素**持有：布局层(L4)不能依赖动效层(L4.5)。
        # 渲染对象只管"活动/可见"，元素把 visible → opacity 的过渡跑出来。
        scroll.on_need_frame = self._pump_fade

    def _thumb_drag(self, scroll: RenderScroll) -> HandleDragRecognizer:
        """滚动条拇指的拖拽识别器（R13.2）。

        识别器住在 L1，不认识布局树，所以：
        - `to_local` 把窗口坐标换算成滚动容器的局部坐标（沿父链取原点）；
        - `hit_test` / `on_start` / `on_drag` / `on_end` 全部转交给渲染对象，
          那里有拇指几何与偏移映射。
        """

        def to_local(x: float, y: float) -> tuple[float, float]:
            origin = scroll.local_to_global(Offset(0.0, 0.0))
            return (x - origin.dx, y - origin.dy)

        return HandleDragRecognizer(
            to_local=to_local,
            hit_test=scroll.hit_thumb,
            on_start=scroll.begin_thumb_drag,
            on_drag=scroll.drag_thumb_to,
            on_end=scroll.end_thumb_drag,
        )

    # ------------------------------------------------------------ 滚动条淡出（R14.2）

    def _pump_fade(self) -> None:
        """把淡出任务挂进帧循环并请求一帧。

        由渲染对象在"有活动"时回调（`RenderScroll.on_need_frame`）。动画对象
        住在元素里是**分层要求**：`layout`(L4) 在 `motion`(L4.5) 之下，
        布局层不能 import 动效层；组件层两边都能用，所以过渡放在这里。
        """
        owner = self.owner
        if owner is None:
            return
        owner.ticker.add(self._fade_task())
        owner.request_frame()

    def _fade_task(self) -> _ScrollbarFade:
        if self._fade_runner is None:
            self._fade = AnimatedValue(1.0, duration_ms=0.0)
            self._fade_runner = _ScrollbarFade(self)
        return self._fade_runner

    def _sync_child(self) -> None:
        widget = self.widget
        assert isinstance(widget, ScrollView)
        scroll = self.render_object
        assert isinstance(scroll, RenderScroll)
        self._child = self.update_child(self._child, widget.child, None)
        child_element = self._child
        new_child = child_element.render_object if child_element is not None else None
        # 只在渲染树子级真的换了才重设 child——`RenderScroll.child` 的 setter
        # 会把滚动偏移清零，每次重建都设一遍等于"一换主题就跳回顶部"。
        if scroll.child is not new_child:
            scroll.child = new_child

    def visit_children(self, visitor: Callable[[Element], None]) -> None:
        if self._child is not None:
            visitor(self._child)

    def unmount(self) -> None:
        owner = self.owner
        if owner is not None and self._fade_runner is not None:
            owner.ticker.remove(self._fade_runner)
        if self._child is not None:
            self._deactivate_child(self._child)
            self._child = None
        super().unmount()


class _ScrollbarFade(Tickable):
    """把"此刻想不想看见滚动条"翻译成 opacity 过渡。

    返回 True 的条件很关键：**只有"还在淡出过程中"或"有一个待办的淡出"**
    才继续排帧。指针停在视口里时返回 False——否则鼠标一停，应用就为了一个
    不再变化的透明值空转 60fps。
    """

    def __init__(self, element: _ScrollElement) -> None:
        self._element = element

    def tick(self, now_ms: float) -> bool:
        element = self._element
        scroll = element.render_object
        if not isinstance(scroll, RenderScroll) or element._fade is None:
            return False
        style = scroll.scrollbar
        if style is None or not scroll.can_scroll:
            scroll.opacity = 1.0
            return False
        value = element._fade
        deadline = scroll.last_activity_ms + style.hold_ms
        idle = now_ms - scroll.last_activity_ms
        # 指针是否还在滚动容器上：**每帧问路由**，而不是靠事件置位——
        # 指针移出去之后我们收不到任何事件，置位的标志永远清不掉。
        owner = element.owner
        hovered = owner is not None and scroll in owner.pointer_router.hover_chain
        # 指针在容器上/正在拖 → 一直亮着；否则先按住 hold_ms，再淡出
        want = 1.0 if (hovered or scroll._bar_active or idle < style.hold_ms) else 0.0
        # 淡出从**截止时刻**起算，而不是从"哪一帧发现它到点了"起算——
        # 否则掉帧会让淡出顺延，动画时长变得取决于帧率（不可复现）。
        start = now_ms if want >= 1.0 else min(now_ms, deadline)
        value.set_target(want, now_ms=start, duration_ms=style.fade_ms)
        more = value.tick(now_ms)
        if scroll.opacity != value.value:
            scroll.opacity = value.value
            scroll.mark_needs_paint()
        # 待办淡出（hold 窗口）也要继续推进，否则永远等不到那一刻
        pending_out = not hovered and not scroll._bar_active and scroll.opacity > 0.0
        return more or pending_out
