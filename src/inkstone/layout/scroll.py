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

from enum import Enum

from ..backend.base import PointerKind
from ..events.pointer import DispatchPhase, PointerDispatch
from .box import RenderBox
from .protocol import (
    INF,
    Axis,
    constraints_from,
)
from .types import BoxConstraints, Offset, Rect, Size

__all__ = ["RenderScroll", "ScrollDirection"]


class ScrollDirection(Enum):
    """滚动方向。`BOTH` 用于表格与代码编辑器。"""

    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    BOTH = "both"


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

    def scroll_by(self, dx: float = 0.0, dy: float = 0.0) -> None:
        self.scroll_to(self._scroll.dx + dx, self._scroll.dy + dy)

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
        if event.kind is not PointerKind.WHEEL:
            return
        dx = -event.wheel_dx * self.wheel_step
        dy = -event.wheel_dy * self.wheel_step
        if self.direction is ScrollDirection.VERTICAL:
            dx = 0.0
        elif self.direction is ScrollDirection.HORIZONTAL:
            dy = 0.0
        before = self._scroll
        self.scroll_by(dx, dy)
        if self._scroll != before:
            dispatch.stop_propagation()

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
            self._needs_paint = False
            return
        super().paint_tree(context)

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
        self._content_size = self.layout_child(self._child, child_constraints)

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
