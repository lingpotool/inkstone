"""滚动容器：ScrollView。

docs/05 §6 里有一条很容易做错的规则，Scroll 就是它的全部意义所在：

    **Scroll 容器给子级的是无限主轴约束**——这必须显式，
    否则子级的 fill 会爆炸。

也就是说：滚动容器的子级拿到"你想要多长就有多长"的约束，
容器自己则夹在父级给的视口尺寸里。这两件事必须分开，
混在一起就会出现"滚动区域里的 fill 子级把整个窗口撑爆"这种经典 bug。

另外一条取舍：**滚动导致的溢出不算 overflow。**
内容比视口长是设计意图，不是布局错误——所以这里刻意不去调 `note_overflow`，
否则检查器会被滚动区域淹没，真正的问题反而看不见。

状态：已实现（本阶段只做单轴滚动与偏移，滚动条 / 锚点保持 / 过滚动留到 Phase 2）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..backend.base import PointerKind
from ..events.pointer import DispatchPhase, PointerDispatch
from .box import RenderBox
from .protocol import (
    INF,
    Axis,
    constraints_from,
)
from .types import BoxConstraints, Offset, Rect, Size

if TYPE_CHECKING:  # 只为注解：运行期不导入 gfx，避免包导入期的循环依赖
    from ..gfx.color import Color

__all__ = ["RenderScroll", "ScrollDirection", "ScrollbarStyle"]


class ScrollDirection(Enum):
    """滚动方向。`BOTH` 用于表格与代码编辑器。"""

    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class ScrollbarStyle:
    """滚动条外观。几何来自 `tokens.scrollbar`，颜色来自语义色。

    **只有 `can_scroll` 时才画**：内容装得下就不该出现滚动条——那是
    "这里没东西可滚"的诚实表达，也避免给静态页面加视觉噪音。

    `thickness` / `hover_thickness` 是**覆盖层**的两档宽度：悬停/拖拽时变粗
    （Chromium 的 overlay 条就是这么做的），因为不占布局空间，变粗只影响
    画出来的样子与命中区域，不会推动内容。
    """

    thickness: float
    hover_thickness: float
    min_thumb: float
    radius: float
    margin: float
    color: Color
    hover_color: Color
    #: 闲置多久开始淡出、淡出用多久（毫秒）。`prefers-reduced-motion` 时归零。
    hold_ms: float
    fade_ms: float


class RenderScroll(RenderBox):
    """单/双向滚动容器。"""

    def __init__(
        self,
        child: RenderBox | None = None,
        *,
        direction: ScrollDirection = ScrollDirection.VERTICAL,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.direction: ScrollDirection = direction
        #: 滚轮一格滚多少逻辑像素。由元素从主题令牌写进来（L4 不读 L6），
        #: 默认值与 `tokens.gestures["wheel_step"]` 一致，便于裸用渲染对象。
        self.wheel_step: float = 48.0
        #: 滚动条外观；None = 不画（纯渲染对象裸用时保持最小行为）。
        self.scrollbar: ScrollbarStyle | None = None
        #: 指针是否停在拇指上 / 是否正在拖它（拖拽由 R13.2 的识别器驱动）。
        self._thumb_hover: bool = False
        self._thumb_drag: bool = False
        #: 抓取点相对拇指顶端的距离：拖拽时保持它不变，拇指才不会跳。
        self._thumb_grab: float = 0.0
        #: 可见度（覆盖层，R14.2）。滚动/悬停/拖拽时亮起，闲置后淡出——
        #: 这是 Chromium/Flutter 的 overlay 滚动条行为。默认 1.0 是给
        #: 不驱动动效的裸用场景（没有 tick 就一直是可见的经典外观）。
        self.opacity: float = 1.0
        #: 最近一次滚动/拖拽的时刻，闲置计时从它算起。
        self._last_activity_ms: float = 0.0
        #: 动画钩子：由元素接到 `BuildOwner.request_frame`（渲染对象拿不到 owner）。
        self.on_need_frame: Callable[[], None] | None = None
        #: 最近一次拿到的事件时间——`wake` 需要一个时刻，而 `scroll_to` 不带时间。
        self._now_ms: float = 0.0
        self._child: RenderBox | None = None
        self._scroll: Offset = Offset(0.0, 0.0)
        self._content_size: Size = Size(0.0, 0.0)
        # 层缓存（R8.4）：子树内容按"内容局部坐标"录一次，滚动只改变换。
        # 没有它，滚动每帧都要把新偏移重新烘焙进几百条指令的坐标里，
        # 并把整棵子树重排/重绘——这是"滚动做不专业"的根因。
        self._layer_ops: tuple[object, ...] | None = None
        self._layer_dirty: bool = True
        # 层内容的单调代号：重建一次 +1。用它当缓存 key，而不是 id(tuple)——
        # 旧 tuple 被回收后 id 可能被复用，后端会拿旧纹理顶包（内容不同的同键）。
        self._layer_generation: int = 0
        if child is not None:
            self.child = child

    # ------------------------------------------------------------ 树

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
        self._scroll = Offset(0.0, 0.0)
        self.invalidate_layer()

    @property
    def children(self) -> tuple[RenderBox, ...]:
        return () if self._child is None else (self._child,)

    # ------------------------------------------------------------ 滚动状态

    @property
    def content_size(self) -> Size:
        """内容的完整尺寸（不含视口裁剪）。"""
        return self._content_size

    @property
    def viewport(self) -> Size:
        """视口尺寸，即本节点自身的尺寸。"""
        return self._size

    @property
    def scroll_offset(self) -> Offset:
        return self._scroll

    @property
    def max_scroll(self) -> Offset:
        """各方向能滚动的最大距离。"""
        dx = max(0.0, self._content_size.width - self._size.width)
        dy = max(0.0, self._content_size.height - self._size.height)
        if self.direction is ScrollDirection.VERTICAL:
            dx = 0.0
        elif self.direction is ScrollDirection.HORIZONTAL:
            dy = 0.0
        return Offset(dx, dy)

    @property
    def can_scroll(self) -> bool:
        m = self.max_scroll
        return m.dx > 0.0 or m.dy > 0.0

    def invalidate_layer(self) -> None:
        """丢弃层缓存（内容变了 / 子级换了 / 尺寸变了）。"""
        self._layer_ops = None
        self._layer_dirty = True
        self._layer_generation += 1

    def scroll_to(self, dx: float | None = None, dy: float | None = None) -> None:
        """设置滚动偏移，自动夹取到合法范围。

        偏移变化**不重排、不重录内容**：子级尺寸没变，变的只是位置——
        直接改它的 offset 并只把**本节点**标脏，下一帧由 `paint_tree` 重放
        层缓存并换一个平移指令（R8.4）。这就是"滚动只有 O(1) 合成"。
        还没布局过（拿不到视口尺寸）时退回标脏重排，保证正确性优先。
        """
        limit = self.max_scroll
        new = Offset(
            min(max(dx if dx is not None else self._scroll.dx, 0.0), limit.dx),
            min(max(dy if dy is not None else self._scroll.dy, 0.0), limit.dy),
        )
        if new == self._scroll:
            return
        self._scroll = new
        if self._child is not None and self._constraints is not None:
            self.place_child(self._child, Offset(-new.dx, -new.dy))
            self.mark_needs_paint()
        else:
            # 未布局：没有视口/内容尺寸可算，交给正常的布局流程
            self.mark_needs_layout()
        # 偏移真的动了 → 条要亮起来（程序化滚动也算"有人在滚"）
        self.wake(self._now_ms)

    def scroll_by(self, dx: float = 0.0, dy: float = 0.0) -> None:
        self.scroll_to(self._scroll.dx + dx, self._scroll.dy + dy)

    # ------------------------------------------------------------ 滚动条

    @property
    def _bar_active(self) -> bool:
        """指针在条上/正在拖它——此时用加粗档，并把闲置计时重置。"""
        return self._thumb_hover or self._thumb_drag

    def bar_thickness(self) -> float:
        style = self.scrollbar
        if style is None:
            return 0.0
        return style.hover_thickness if self._bar_active else style.thickness

    def thumb_rect(self) -> Rect | None:
        """拇指的矩形（视口局部坐标）；不可滚动或不画滚动条时返回 None。

        几何是纯函数（视口 / 内容 / 偏移 → 矩形），所以能直接单测，
        不需要起渲染管线：

        - 长度 = 视口 × 视口/内容，再夹到 `min_thumb`（长列表的拇指不能细如发丝）；
        - 位置 = 偏移占可滚动距离的比例 × 剩余轨道长度；
        - 横向滚动条贴底边，纵向贴右边，各留 `margin`。

        **覆盖层**：贴的是视口边缘，不占布局空间（R14.2 起不再让槽位）。
        """
        style = self.scrollbar
        if style is None or not self.can_scroll:
            return None
        thickness = self.bar_thickness()
        limit = self.max_scroll
        if self.direction is ScrollDirection.HORIZONTAL:
            track = self._size.width
            visible = self._size.height
            travelled = limit.dx
            offset = self._scroll.dx
            length = max(style.min_thumb, track * (track / self._content_size.width))
            length = min(length, track)
            pos = 0.0 if travelled <= 0.0 else (track - length) * (offset / travelled)
            return Rect(pos, visible - thickness - style.margin, length, thickness)
        track = self._size.height
        visible = self._size.width
        travelled = limit.dy
        offset = self._scroll.dy
        length = max(style.min_thumb, track * (track / self._content_size.height))
        length = min(length, track)
        pos = 0.0 if travelled <= 0.0 else (track - length) * (offset / travelled)
        return Rect(visible - thickness - style.margin, pos, thickness, length)

    # ------------------------------------------------------------ 可见度（R14.2）

    @property
    def last_activity_ms(self) -> float:
        """最近一次滚动/拖拽的时刻——元素层据此算闲置并启动淡出。"""
        return self._last_activity_ms

    def request_fade(self) -> None:
        """请排一帧来推进淡出。

        指针**离开**视口时调它：那一刻不该把条点亮（否则永远不淡出），
        但必须有人继续推帧，否则闲置到点也没人来开淡出。
        """
        if self.on_need_frame is not None:
            self.on_need_frame()

    def wake(self, now_ms: float) -> None:
        """有活动：把条亮起来并重置闲置计时。

        滚动、拖拽、指针进入视口都会调它。**必须请求一帧**——否则淡出动画
        没有帧可推进，条会僵在半透明状态（"动效不动"最常见的成因）。
        """
        self._last_activity_ms = now_ms
        if self.opacity != 1.0:
            self.opacity = 1.0
            self.mark_needs_paint()
        if self.on_need_frame is not None:
            self.on_need_frame()

    def _paint_scrollbar(self, context: object) -> None:
        """把拇指画在**为它让出来的槽位**里（见 ADR-0026）。

        内容已按"视口宽 − 槽位"排版（`_narrow_for_gutter`），所以拇指与条目
        永不重叠；绘制在内容之后，只是保证它盖住的是那条空槽位。

        调用点（`paint_tree` 的两个分支）都在**父级坐标系**里，所以这里自己
        压一次 `self._offset` 的平移——不然拇指会整体偏到左上（实测：画在了
        搜索行上）。`thumb_rect()` 给的是视口局部坐标，两者必须对齐。
        """
        style = self.scrollbar
        rect = self.thumb_rect()
        if style is None or rect is None or self.opacity <= 0.0:
            return
        round_rect = getattr(context, "round_rect", None)
        if not callable(round_rect):
            return
        save = getattr(context, "save", None)
        translate = getattr(context, "translate", None)
        restore = getattr(context, "restore", None)
        base = style.hover_color if self._bar_active else style.color
        # 淡出就是把整体透明度按 opacity 缩放（颜色本身也是半透明的）
        color = base.with_alpha(base.a * self.opacity)
        if callable(save) and callable(translate) and callable(restore):
            save()
            translate(self._offset.dx, self._offset.dy)
            round_rect(rect, style.radius, color)
            restore()
        else:
            round_rect(rect, style.radius, color)

    # ------------------------------------------------------------ 输入

    def handle_pointer_event(self, dispatch: PointerDispatch) -> None:
        """滚轮滚动（R12）。

        滚轮**不是手势**（没有 DOWN/UP 生命周期，不进竞技场），所以走这条
        "非手势输入"的分发缝——`RenderBox.handle_pointer_event` 的文档就是
        为这类输入留的。

        两个关键点：

        1. **只在 TARGET / BUBBLE 阶段处理。** CAPTURE 是根→内，会让最外层
           容器抢先消费；TARGET/BUBBLE 是内→外，正是"内层先滚"该有的顺序。
        2. **只有偏移真的变了才叫停传播。** 夹到边界时偏移不动，事件继续冒泡，
           祖先滚动容器接着处理——**嵌套滚动（内层滚到底、外层接管）因此是
           结构成立的，不写任何特判**。

        方向约定来自 SDL：`wheel_dy > 0` = 向上滚 = 看更上面的内容 = 偏移减小。
        """
        if dispatch.phase is DispatchPhase.CAPTURE:
            return
        event = dispatch.event
        self._now_ms = event.time_ms
        if event.kind is PointerKind.WHEEL:
            dx = -event.wheel_dx * self.wheel_step
            dy = -event.wheel_dy * self.wheel_step
            if self.direction is ScrollDirection.VERTICAL:
                dx = 0.0
            elif self.direction is ScrollDirection.HORIZONTAL:
                dy = 0.0
            before = self._scroll
            self.scroll_by(dx, dy)
            if self._scroll != before:
                self.wake(event.time_ms)
                dispatch.stop_propagation()
            return
        if event.kind is PointerKind.LEAVE:
            self._set_thumb_hover(False)
            self.request_fade()
            return
        if event.kind in (PointerKind.MOVE, PointerKind.ENTER):
            # 能收到 MOVE 就说明指针在本节点命中链上（= 指针在我们身上或
            # 子级上）。**指针离开时不会再收到事件**——那件事由元素每帧
            # 查路由的 hover 链来判断，不能靠这里置位（R14.2 的坑：
            # 靠事件置位的 pointer_inside 永远不会被清掉，条就不淡出了）。
            self.wake(event.time_ms)
            self._set_thumb_hover(self.hit_thumb(dispatch.local_x, dispatch.local_y))

    # ------------------------------------------------------------ 滚动条状态

    def hit_thumb(self, x: float, y: float) -> bool:
        """视口局部坐标是否落在拇指上（把手拖拽的命中判定）。"""
        rect = self.thumb_rect()
        if rect is None:
            return False
        return rect.contains(x, y)

    def begin_thumb_drag(self, x: float, y: float) -> None:
        """抓住拇指：记住"抓住的是拇指上的哪一点"，拖拽时该点跟着指针走。

        为什么要记这一点而不是直接对齐指尖：手指按在拇指下缘时若对齐指尖，
        拇指会"跳"一下。抓取点保持相对位置是拖拽的标准手感。
        """
        rect = self.thumb_rect()
        if rect is None:
            return
        if self.direction is ScrollDirection.HORIZONTAL:
            self._thumb_grab = x - rect.left
        else:
            self._thumb_grab = y - rect.top
        self._thumb_drag = True
        self._set_thumb_hover(True)
        self.wake(self._now_ms)
        self.mark_needs_paint()

    def drag_thumb_to(self, x: float, y: float) -> None:
        """把拇指拖到指针处（绝对定位），换算成滚动偏移。"""
        rect = self.thumb_rect()
        if rect is None:
            return
        if self.direction is ScrollDirection.HORIZONTAL:
            track = self._size.width
            thumb_len = rect.width
            want = x - self._thumb_grab
            limit = self.max_scroll.dx
        else:
            track = self._size.height
            thumb_len = rect.height
            want = y - self._thumb_grab
            limit = self.max_scroll.dy
        travel = track - thumb_len
        if travel <= 0.0 or limit <= 0.0:
            return
        ratio = min(max(want / travel, 0.0), 1.0)
        if self.direction is ScrollDirection.HORIZONTAL:
            self.scroll_to(dx=ratio * limit)
        else:
            self.scroll_to(dy=ratio * limit)

    def end_thumb_drag(self) -> None:
        self._thumb_drag = False
        self.mark_needs_paint()

    def _set_thumb_hover(self, hovered: bool) -> None:
        """悬停态只改颜色，但要**标脏重绘**——否则拇指不会变亮。

        指针移出视口时我们收不到后续 MOVE（不在命中链里），所以悬停会在
        "从拇指上直接移出窗口"这种极端路径上滞留到下次移回；代价只是拇指
        亮着，不足以引入全局 hover 跟踪。
        """
        if hovered != self._thumb_hover:
            self._thumb_hover = hovered
            self.mark_needs_paint()
        if hovered:
            self.wake(self._now_ms)

    # ------------------------------------------------------------ 绘制

    def paint_clip(self) -> Rect | None:
        """视口裁剪：滚出去的内容不许画在视口外。

        `paint_tree` 只做 translate 不做 clip，而滚动容器给子级的是
        **无限主轴约束**——内容天然比视口长，滚出视口的部分必须被挡掉，
        否则它会直接糊在视口下方的组件上（"滚动列表盖住下面的按钮"）。
        """
        return Rect(0.0, 0.0, self._size.width, self._size.height)

    def paint_tree(self, context: object) -> None:
        """重放层缓存：子树内容不重录，只换一个平移。

        这是滚动性能的关键路径（R8.4）。只有当 context 支持状态指令
        （`push_clip` / `push_translate` / `append_ops`，即显示列表录制器）时才走
        缓存；纯 object 上下文退回默认遍历，保证老调用方不受影响。
        """
        if not self._needs_paint:
            return
        push_clip = getattr(context, "push_clip", None)
        push_translate = getattr(context, "push_translate", None)
        append_ops = getattr(context, "append_ops", None)
        pop_state = getattr(context, "pop_state", None)
        push_layer = getattr(context, "push_layer", None)
        pop_layer = getattr(context, "pop_layer", None)
        transform = getattr(context, "transform", None)
        # 缓存里的指令是"内容局部"的，重放时不会被 recorder 的变换折算——
        # 所以只有当前变换是**纯平移**时才能用（祖先偏移可加，缩放/旋转不可加）。
        # 典型会禁用缓存的场景：根上的 DPI 缩放（那本来也不是滚动热路径）。
        if (
            self._child is not None
            and callable(push_clip)
            and callable(push_translate)
            and callable(append_ops)
            and callable(pop_state)
            and transform is not None
            and transform.a == 1.0
            and transform.d == 1.0
            and transform.b == 0.0
            and transform.c == 0.0
        ):
            # 子树内容真的变了才重录；只是滚动的话它一直是干净的
            if self._child.needs_paint:
                self.invalidate_layer()
            if self._layer_ops is None:
                self._rebuild_layer()
            self.paint(context)
            # 重放的坐标 = 祖先已累计的平移 + 本节点 offset + 子级 offset
            base_x = transform.tx + self._offset.dx
            base_y = transform.ty + self._offset.dy
            clip = self.paint_clip()
            push_layer_fn = push_layer if callable(push_layer) else None
            pop_layer_fn = pop_layer if callable(pop_layer) else None
            has_layer_ops = (
                push_layer_fn is not None and pop_layer_fn is not None and clip is not None
            )
            if has_layer_ops and clip is not None and push_layer_fn is not None:
                # 层缓存：内容静态（key=代号），后端可渲染成离屏纹理后每帧只画一个四边形
                push_layer_fn(self._layer_generation, Rect(base_x, base_y, clip.width, clip.height))
            depth = 0
            if clip is not None:
                push_clip(Rect(base_x, base_y, clip.width, clip.height))
                depth += 1
            push_translate(base_x + self._child.offset.dx, base_y + self._child.offset.dy)
            depth += 1
            assert self._layer_ops is not None
            append_ops(self._layer_ops)
            for _ in range(depth):
                pop_state()
            if has_layer_ops and pop_layer_fn is not None:
                pop_layer_fn()
            self._paint_scrollbar(context)
            self._needs_paint = False
            return
        super().paint_tree(context)
        self._paint_scrollbar(context)

    def _rebuild_layer(self) -> None:
        """把子树录成"内容局部坐标"的指令，供后续帧重放。"""
        from ..gfx import DisplayListRecorder  # 局部导入，避免布局层在导入期依赖整个 gfx

        child = self._child
        if child is None:
            self._layer_ops = ()
            self._layer_dirty = False
            return
        recorder = DisplayListRecorder()
        saved = child._offset
        # 归零子级偏移：缓存的是内容自身坐标，平移由重放时的状态指令提供
        child._offset = Offset(0.0, 0.0)
        child.mark_subtree_needs_paint()  # 强制走一遍（否则干净子树会被整棵跳过）
        try:
            child.paint_tree(recorder)
        finally:
            child._offset = saved
        width = max(1, int(self._size.width))
        height = max(1, int(self._size.height))
        self._layer_ops = recorder.finish(width, height).ops
        self._layer_dirty = False

    # ------------------------------------------------------------ 布局

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        inner = self.content_constraints(constraints)

        # 视口：在父级给的范围内尽量取大
        size = constraints.constrain(
            self.wrap(
                Size(
                    inner.max_width if inner.has_bounded_width else 0.0,
                    inner.max_height if inner.has_bounded_height else 0.0,
                )
            )
        )

        if self._child is None:
            self._content_size = Size(0.0, 0.0)
            return size

        # 关键一步：给子级**无限主轴**约束。
        # 这才是"可滚动"的本质——子级想多长就多长，超出的部分由视口裁剪。
        child_constraints = self._child_constraints(inner)
        content = self.layout_child(self._child, child_constraints)
        self._content_size = content

        # 约束/内容尺寸可能变了，层缓存必须作废重录
        self.invalidate_layer()
        self.scroll_to()
        self.place_child(self._child, Offset(-self._scroll.dx, -self._scroll.dy))
        return size

    def _child_constraints(self, inner: BoxConstraints) -> BoxConstraints:
        """按滚动方向决定哪个轴给子级无限空间。"""
        if self.direction is ScrollDirection.VERTICAL:
            return constraints_from(
                Axis.VERTICAL,
                inner.min_height,
                INF,
                inner.min_width,
                inner.max_width,
            )
        if self.direction is ScrollDirection.HORIZONTAL:
            return constraints_from(
                Axis.HORIZONTAL,
                inner.min_width,
                INF,
                inner.min_height,
                inner.max_height,
            )
        return BoxConstraints(
            inner.min_width,
            INF,
            inner.min_height,
            INF,
        )

    def describe(self) -> str:
        base = super().describe()
        return f"{base} content={self._content_size.width:g}×{self._content_size.height:g}"
