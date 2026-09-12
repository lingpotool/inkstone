"""帧调度 BuildOwner（docs/06 §4）。

一帧的生命周期：

    输入事件 → 状态变更（收集脏集合）
      → build（重建脏子树）
      → layout（重排脏几何）
      → semantics（同步无障碍语义树，Phase 3）
      → paint（重放脏段显示列表）
      → composite（光栅与合成）

这个模块负责的两条硬规则：

1. **同一帧内的多次 set_state 只重建一次。** 脏集合是 dict，
   重复标脏只是覆盖同一个键；flush 时按 depth 排序一次性重建。
2. **在 layout / paint 阶段改状态要抛错。** 那是时序 bug 的高发区——
   改了但本帧已经错过了，表现是"界面要下一帧才动"，极难排查。
   框架宁可响亮地失败，也不留下这种诡异行为。

时钟由后端注入、渲染不读墙上时钟（确定性），这条由 gfx 层保证，这里只留接口。

状态：已实现（semantics 阶段留到 Phase 3 无障碍）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from ..backend.base import PointerEvent
from ..events.pointer import HitTestResult, PointerRouter
from ..layout import BoxConstraints, Offset, Rect, RenderBox, Size
from ..style import Theme, default_theme
from ..text import TextEngine
from .element import Element
from .render_object import PaintContext
from .signals import Effect
from .widget import Widget

__all__ = [
    "MAX_BUILD_FAILURES_PER_FRAME",
    "MAX_BUILD_ROUNDS",
    "BuildError",
    "BuildFailure",
    "BuildOwner",
    "FrameError",
    "FramePhase",
]

# 一帧内允许的最大重建轮数。正常情况 1 轮就收敛；
# 超过说明 build 里有"无条件 set_state"这类死循环。
MAX_BUILD_ROUNDS = 20

# 同一帧内一个节点连续失败多少次就本帧不再重试（下一帧接着试）。
# 不设上限的话，"每次 build 都抛"的节点会把一帧拖成死循环——
# 那就把"响亮失败"变成了"卡死"，比静默更糟。
MAX_BUILD_FAILURES_PER_FRAME = 3


class FramePhase(Enum):
    """当前处于一帧的哪个阶段。"""

    IDLE = "idle"
    BUILD = "build"
    LAYOUT = "layout"
    PAINT = "paint"


class FrameError(RuntimeError):
    """帧时序错误——在不允许改状态的阶段改了状态。"""

    def __init__(self, message: str, *, phase: str = "", path: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.phase = phase
        self.path = path

    def __str__(self) -> str:
        lines = [f"FrameError: {self.message}"]
        if self.phase:
            lines.append(f"  阶段: {self.phase}")
        if self.path:
            lines.append(f"  路径: {self.path}")
        lines.append("  建议: 把状态变更移到事件回调里，不要在 build/layout/paint 中触发")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class BuildFailure:
    """一个节点在 build 阶段抛出的异常，连同它的节点路径。"""

    path: str
    exception: BaseException

    def describe(self) -> str:
        return f"{self.path}: {type(self.exception).__name__}: {self.exception}"


class BuildError(RuntimeError):
    """一帧内一个或多个节点的 build 失败——聚合上报，一个都不掩盖。

    为什么要聚合而不是"遇到第一个就抛"：那样后面所有脏节点都会停在
    "widget 已换、子树没更新"的不一致态，而且下一帧也不会重试。
    这里的语义是：**其余节点照常完成本帧**，失败的节点保持脏等下一帧，
    最后再把这批错误一起响亮地报出来（铁律 5：不许静默）。
    """

    def __init__(self, failures: Sequence[BuildFailure]) -> None:
        self.failures: tuple[BuildFailure, ...] = tuple(failures)
        lines = [f"BuildError: 一帧内有 {len(self.failures)} 处 build 抛出异常"]
        lines.extend(f"  {failure.describe()}" for failure in self.failures)
        lines.append("  说明: 其余节点已完成本帧；失败节点保持脏，下一帧会重试")
        super().__init__("\n".join(lines))


class BuildOwner:
    """脏集合与帧调度。整棵组件树共享一个。"""

    def __init__(
        self,
        *,
        theme: Theme | None = None,
        text_engine: TextEngine | None = None,
    ) -> None:
        self._dirty: dict[int, Element] = {}
        self._phase: FramePhase = FramePhase.IDLE
        self._root: Element | None = None
        # 环境主题：组件通过 `context.theme` 拿到它，不必层层透传。
        # 放在 BuildOwner 是因为它本来就是整棵树的上下文根。
        # 存 `_theme` 而不是直接 `theme`：`theme` 是 property，setter 要触发全树重建。
        self._theme: Theme = theme if theme is not None else default_theme()
        # 文本引擎：Text / Input / Button 排版时通过 `context.text_engine` 取。
        # 与 theme 并列放在这里，理由相同——它是整棵树共享的**有状态**服务
        # （带度量缓存与整形缓存），每帧重建会让排版性能垮掉。
        self.text_engine: TextEngine | None = text_engine
        # 计数器：测试用它验证"批处理"与"帧数"，调试时也能看出有没有过度重建
        self.build_count: int = 0
        self.frame_count: int = 0
        # 本帧 build 阶段的错误（每帧开头清空）。失败节点会保持脏并在下一帧重试，
        # 但错误不会消失——`flush_build` 结束时若非空就抛聚合的 `BuildError`。
        self.errors: list[BuildFailure] = []
        # 帧调度出口："需要一帧"时回调一次。app 层、后端 vsync、motion 包
        # 都接在这里，所以签名保持最简单形态（无参数、无返回值）。
        #
        # 它补上的是闭环里缺的那一环：docs/06 §4 承诺"时钟由后端注入、vsync 对齐"，
        # 但此前 `schedule_build_for` 只入队，没有任何"该画下一帧了"的出口，
        # 于是 set_state → 屏幕刷新这条闭环压根不存在。
        self.on_frame_scheduled: Callable[[], None] | None = None
        self._frame_scheduled = False
        # 失效的 Effect 队列：下一帧 build 之前统一重跑（docs/20 R6.2）。
        # 信号写 → effect 失效 → 入队 → 同帧结算，UI 与副作用不差一拍。
        self._pending_effects: list[Effect] = []
        # 指针路由（R7.1）：命中链的三阶段分发与 hover 差分。
        # 一个 owner 一棵树，所以 router 的 hover 状态也归它。
        self.pointer_router = PointerRouter()
        # 文本输入通道：输入框获焦时经这两个钩子通知后端（R5.7 的协议已就位）。
        # 为什么不直接调 backend：core 不应该认识平台对象；App 组装时把
        # `backend.start_text_input` / `set_ime_rect` 接进来即可。
        self.on_text_input: Callable[[bool], None] | None = None
        self.on_ime_rect: Callable[[Rect], None] | None = None

    # ------------------------------------------------------------ 查询

    @property
    def phase(self) -> FramePhase:
        return self._phase

    @property
    def root(self) -> Element | None:
        return self._root

    @property
    def dirty_count(self) -> int:
        return len(self._dirty)

    # ------------------------------------------------------------ 环境主题

    @property
    def theme(self) -> Theme:
        """当前环境主题。组件通过 `context.theme` 活读它。"""
        return self._theme

    @theme.setter
    def theme(self, value: Theme) -> None:
        """换主题：把整棵树标脏，下一帧重新解析所有样式。

        正式机制（InheritedElement / ThemeScope，R6.1）落地后，本属性就是
        "根作用域"：没被子树 ThemeScope 覆盖的节点都读它，所以换根主题
        仍然是全树标脏——那是"根作用域变了"的特例，不是另一套机制。
        子树级覆盖请用 `ThemeScope`，它能定向标脏（docs/20）。

        在 layout / paint 阶段换主题会抛 `FrameError`（与其它状态变更一致）。
        """
        if value is self._theme:
            return
        self._theme = value
        self._mark_all_needs_build()

    def _mark_all_needs_build(self) -> None:
        """整棵树标脏重建。主题是整棵树的上下文，没有局部生效的余地。"""
        root = self._root
        if root is None:
            return
        pending: list[Element] = [root]
        while pending:
            node = pending.pop()
            node.mark_needs_build()
            node.visit_children(pending.append)

    @property
    def root_render_object(self) -> RenderBox | None:
        """广度优先找第一个 RenderBox——它就是渲染树的根。"""
        if self._root is None:
            return None
        queue: list[Element] = [self._root]
        while queue:
            node = queue.pop(0)
            render_object = node.render_object
            if render_object is not None:
                return render_object
            node.visit_children(queue.append)
        return None

    # ------------------------------------------------------------ 挂载

    def mount(self, widget: Widget) -> Element:
        """建立三棵树的根。只能调用一次。"""
        if self._root is not None:
            raise FrameError("BuildOwner 已经挂载过根节点，一个 owner 只管一棵树")
        with self._phase_scope(FramePhase.BUILD):
            element = widget.create_element()
            element.assign_owner(self)
            element.mount(None, None)
            self._root = element
        return element

    # ------------------------------------------------------------ 调度

    def schedule_build_for(self, element: Element) -> None:
        """把一个 Element 排入重建队列。

        layout / paint 阶段调用会抛 FrameError——这是本模块最重要的守卫。
        """
        if self._phase in (FramePhase.LAYOUT, FramePhase.PAINT):
            raise FrameError(
                f"在 {self._phase.value} 阶段不允许修改状态",
                phase=self._phase.value,
                path=element.describe_path(),
            )
        was_empty = not self._dirty
        self._dirty[id(element)] = element
        if was_empty:
            self.request_frame()

    def request_frame(self) -> None:
        """声明"需要一帧"，同一帧内只会通知一次。

        脏集合从空变非空时触发一次 `on_frame_scheduled`；`begin_frame` 末尾
        重置"已通知"标志，所以下一帧的标脏还能再触发（注册一次、帧末注销）。
        没有回调时静默通过——headless 测试与纯布局场景不需要它。

        纯绘制脏（不经过 `mark_needs_build`）的场景由 app 层显式调它：
        `RenderObject` 拿不到 `BuildOwner`，那条路只能由知道 owner 的那一层来接。
        """
        if self._frame_scheduled:
            return
        self._frame_scheduled = True
        callback = self.on_frame_scheduled
        if callback is not None:
            callback()

    def schedule_effect(self, effect: Effect) -> None:
        """把一个失效的 Effect 排入队列，下一帧 build 之前统一重跑。

        与标脏共用同一个帧出口：队列一进东西就是"需要一帧"。
        """
        if self._phase in (FramePhase.LAYOUT, FramePhase.PAINT):
            raise FrameError(
                f"在 {self._phase.value} 阶段不允许修改状态",
                phase=self._phase.value,
            )
        self._pending_effects.append(effect)
        self.request_frame()

    # ------------------------------------------------------------ 输入分发（R7.1）

    def dispatch_pointer(self, event: PointerEvent) -> bool:
        """把一个指针事件路由到组件树。返回是否命中。

        做两件事：从根渲染对象算命中链（layout 的 `hit_test`），
        再把链交给 router 做三阶段分发与 hover 差分。
        事件回调在 IDLE 阶段执行，所以回调里 `set_state` 是合法的；
        但**在 layout / paint 阶段派发事件会抛 `FrameError`**——
        那意味着某个绘制/布局代码在偷偷制造输入，属于时序 bug
        （与 `schedule_build_for` 的阶段守卫同一套标准，不开洞）。
        """
        if self._phase in (FramePhase.LAYOUT, FramePhase.PAINT):
            raise FrameError(
                f"在 {self._phase.value} 阶段不允许派发指针事件",
                phase=self._phase.value,
            )
        root = self.root_render_object
        if root is None:
            return False
        result = HitTestResult()
        root.hit_test(Offset(event.x, event.y), result)
        return self.pointer_router.dispatch(result, event)

    def flush_effects(self) -> None:
        """在 build 之前重跑失效的 Effect。

        时序刻意放在 build 之前：effect 里 set_state 产生的脏，
        由紧随其后的 `flush_build` 在同一帧收掉。
        在 BUILD 阶段运行，所以 effect 里允许 set_state / 写信号。
        """
        if not self._pending_effects:
            return
        with self._phase_scope(FramePhase.BUILD):
            pending = self._pending_effects
            self._pending_effects = []
            for effect in pending:
                effect.flush()

    def flush_build(self) -> None:
        """重建所有脏节点，按 depth 从浅到深，保证父级先于子级。

        **单个节点 build 抛异常不会打断整批。** 异常被记进 `self.errors`，
        该节点保持脏（下一帧重试），其余节点照常完成本帧；全部处理完之后
        如果 `self.errors` 非空，抛聚合的 `BuildError`。

        为什么不能"遇到第一个就抛"：`rebuild()` 是先清 `_dirty` 再执行
        `perform_rebuild()`，队列也已经整批取空——异常一冒泡，该节点就停在
        "widget 已换、子树没更新"的不一致态，而且下一帧不重试、没有任何可见错误。
        响亮失败是对的，但"响亮"不等于"让半棵树停在旧状态"。
        """
        with self._phase_scope(FramePhase.BUILD):
            self.errors.clear()
            rounds = 0
            # 本帧内各节点的失败次数。跨帧重置——"连续失败"是按帧算的。
            failures: dict[int, int] = {}
            # 本帧放弃重试、但要留给下一帧的节点
            retry_next_frame: list[Element] = []
            while self._dirty:
                rounds += 1
                if rounds > MAX_BUILD_ROUNDS:
                    raise FrameError(
                        f"一帧内重建了 {rounds} 轮仍未收敛，多半是 build 里有不带条件的 set_state",
                        phase=FramePhase.BUILD.value,
                    )
                # 先取出并清空：rebuild 中新标脏的节点进下一轮，不会被吞掉
                batch = sorted(self._dirty.values(), key=lambda e: e.depth)
                self._dirty.clear()
                for element in batch:
                    if not (element.active and element.dirty):
                        continue
                    key = id(element)
                    if failures.get(key, 0) >= MAX_BUILD_FAILURES_PER_FRAME:
                        # 本帧不再试，但保持脏——下一帧从头再来
                        element._dirty = True
                        retry_next_frame.append(element)
                        continue
                    try:
                        element.rebuild()
                    except Exception as exc:  # 错误边界必须接住一切，不能让单个节点掀翻整批
                        failures[key] = failures.get(key, 0) + 1
                        # rebuild 已经清了脏标记，这里必须按回去：
                        # 失败节点要保持脏，否则下一帧它永远不会被重试。
                        element._dirty = True
                        self.errors.append(
                            BuildFailure(path=element.describe_path(), exception=exc)
                        )
                        if failures[key] >= MAX_BUILD_FAILURES_PER_FRAME:
                            retry_next_frame.append(element)
                        else:
                            self._dirty[key] = element  # 本帧内再试一次
                        continue
                    self.build_count += 1
            # 帧末把"本帧放弃重试但仍脏"的节点放回队列，下一帧接着试。
            # 注意是在 while 循环之后放回，所以不会把本帧拖成死循环。
            for element in retry_next_frame:
                self._dirty[id(element)] = element
            if self.errors:
                raise BuildError(self.errors)

    def flush_layout(self, constraints: BoxConstraints) -> Size | None:
        if self.root_render_object is None:
            return None
        with self._phase_scope(FramePhase.LAYOUT):
            return self.root_render_object.layout(constraints)

    def flush_paint(self, context: PaintContext) -> None:
        if self.root_render_object is None:
            return
        with self._phase_scope(FramePhase.PAINT):
            self.root_render_object.paint_tree(context)

    def begin_frame(
        self,
        constraints: BoxConstraints,
        context: PaintContext | None = None,
        *,
        force_repaint: bool = False,
    ) -> Size | None:
        """跑完整的一帧。

        `force_repaint=True` 时强制把渲染树全部标脏——动画场景下默认会跳过
        没动的子树（省 99% 工作量），黄金图测试与截图工具要的是"每帧完整画面"，
        所以 devtools 用这个标志。
        """
        self.frame_count += 1
        try:
            self.flush_effects()
            self.flush_build()
            size = self.flush_layout(constraints)
            if context is not None:
                if force_repaint:
                    root = self.root_render_object
                    if root is not None:
                        # 整树重画：`mark_needs_paint` 只标自身并冒泡到根，
                        # 已清过的子级会被 paint_tree 跳过——所以要一路标到叶子。
                        root.mark_subtree_needs_paint()
                self.flush_paint(context)
            return size
        finally:
            # 帧末注销"已通知"标志：下一帧再标脏还能再通知一次。
            # 放在 finally 里，帧中途抛错（如 BuildError）也不会把标志卡住——
            # 卡住的话"需要一帧"就再也通知不出去了，那是更糟的静默失败。
            self._frame_scheduled = False

    # ------------------------------------------------------------ 内部

    @contextmanager
    def _phase_scope(self, phase: FramePhase) -> Iterator[None]:
        previous = self._phase
        self._phase = phase
        try:
            yield
        finally:
            self._phase = previous
