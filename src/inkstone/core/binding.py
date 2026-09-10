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

from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum

from ..layout import BoxConstraints, RenderBox, Size
from ..style import Theme, default_theme
from ..text import TextEngine
from .element import Element
from .render_object import PaintContext
from .widget import Widget

__all__ = ["MAX_BUILD_ROUNDS", "BuildOwner", "FrameError", "FramePhase"]

# 一帧内允许的最大重建轮数。正常情况 1 轮就收敛；
# 超过说明 build 里有"无条件 set_state"这类死循环。
MAX_BUILD_ROUNDS = 20


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
        self.theme: Theme = theme if theme is not None else default_theme()
        # 文本引擎：Text / Input / Button 排版时通过 `context.text_engine` 取。
        # 与 theme 并列放在这里，理由相同——它是整棵树共享的**有状态**服务
        # （带度量缓存与整形缓存），每帧重建会让排版性能垮掉。
        self.text_engine: TextEngine | None = text_engine
        # 计数器：测试用它验证"批处理"与"帧数"，调试时也能看出有没有过度重建
        self.build_count: int = 0
        self.frame_count: int = 0

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
        self._dirty[id(element)] = element

    def flush_build(self) -> None:
        """重建所有脏节点，按 depth 从浅到深，保证父级先于子级。"""
        with self._phase_scope(FramePhase.BUILD):
            rounds = 0
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
                    if element.active and element.dirty:
                        element.rebuild()
                        self.build_count += 1

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
        self.flush_build()
        size = self.flush_layout(constraints)
        if context is not None:
            if force_repaint:
                root = self.root_render_object
                if root is not None:
                    _force_paint_all(root)
            self.flush_paint(context)
        return size

    # ------------------------------------------------------------ 内部

    @contextmanager
    def _phase_scope(self, phase: FramePhase) -> Iterator[None]:
        previous = self._phase
        self._phase = phase
        try:
            yield
        finally:
            self._phase = previous


def _force_paint_all(root: RenderBox) -> None:
    """把整棵渲染子树标脏。`mark_needs_paint` 只标自身与冒泡到根，
    但"整树重画"需要把每个节点都标记——否则已清过的子级会被 paint_tree 跳过。
    """
    pending: list[RenderBox] = [root]
    while pending:
        node = pending.pop()
        node._needs_paint = True
        pending.extend(node.children)
