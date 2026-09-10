"""无头后端 —— 不开窗口，供 CI 黄金图测试与自动化测试使用。

它是"只有后端碰平台"这条纪律的收益兑现：

    同样的测试代码，在无头后端上跑得起来、跑得飞快、跑得完全确定。

两个关键设计：

1. **时钟是注入的。** `now_ms()` 返回内部计数，只能用 `advance()` 推进。
   所以"动画播到第 300ms"这类断言在 CI 里和在你机器上是同一个结果——
   渲染层不读墙上时钟，才有这种确定性。
2. **事件是注入的。** 测试用 `inject()` 塞事件，然后 `pump_events()` 取。
   不需要真的去移动鼠标，也不需要系统提供输入设备。

DPI 也可以直接设（`set_dpi_scale`），用来验证 125% / 150% 缩放下的行为，
这在真窗口上很难自动化。

状态：已实现。
"""

from __future__ import annotations

from ..gfx.color import Color
from ..gfx.display_list import DisplayList
from .base import (
    BackendError,
    Cursor,
    Event,
    WindowKind,
    WindowSpec,
)

__all__ = ["HeadlessBackend"]


class HeadlessBackend:
    """无头后端：假窗口、注入式事件、可控时钟。"""

    def __init__(self, *, start_time_ms: float = 0.0) -> None:
        self._time_ms = start_time_ms
        self._pending: list[Event] = []
        self._windows: dict[int, WindowSpec] = {}
        self._next_id = 1
        self._dpi_scale = 1.0
        self._cursor: Cursor = Cursor.DEFAULT
        self._clipboard = ""
        self._initialized = False
        self._redraw_requests = 0
        # 最近一次光栅结果留在内存里，测试可以直接取像素做断言
        self.last_frame: DisplayList | None = None
        self.last_pixels: bytes | None = None

    # ------------------------------------------------------------ 身份

    @property
    def name(self) -> str:
        return "headless"

    # ------------------------------------------------------------ 生命周期

    def initialize(self) -> None:
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False
        self._windows.clear()
        self._pending.clear()

    # ------------------------------------------------------------ 窗口

    def create_window(self, spec: WindowSpec) -> int:
        if not self._initialized:
            raise BackendError("先调用 initialize() 再创建窗口")
        if spec.width <= 0 or spec.height <= 0:
            raise BackendError(f"窗口尺寸必须为正，收到 {spec.width}×{spec.height}")
        window_id = self._next_id
        self._next_id += 1
        self._windows[window_id] = spec
        return window_id

    def destroy_window(self, window_id: int) -> None:
        self._require_window(window_id)
        del self._windows[window_id]

    def window_spec(self, window_id: int) -> WindowSpec:
        self._require_window(window_id)
        return self._windows[window_id]

    def resize_window(self, window_id: int, width: float, height: float) -> None:
        """改变窗口大小，并产生一条 RESIZED 事件（真平台也是这么做的）。"""
        self._require_window(window_id)
        spec = self._windows[window_id]
        self._windows[window_id] = WindowSpec(
            title=spec.title,
            width=width,
            height=height,
            resizable=spec.resizable,
            min_width=spec.min_width,
            min_height=spec.min_height,
        )
        from .base import WindowEvent

        self._pending.append(
            WindowEvent(
                kind=WindowKind.RESIZED,
                time_ms=self.now_ms(),
                width=width,
                height=height,
                dpi_scale=self._dpi_scale,
            )
        )

    # ------------------------------------------------------------ 事件

    def inject(self, event: Event) -> None:
        """塞一个事件进队列。测试驱动输入就靠它。"""
        self._pending.append(event)

    def inject_many(self, *events: Event) -> None:
        self._pending.extend(events)

    def pump_events(self) -> list[Event]:
        events = self._pending
        self._pending = []
        return events

    def wait_events(self, timeout_ms: float) -> list[Event]:
        """无头后端不阻塞——没有真窗口，等也没人来事件。

        语义上等价于"立刻返回当前待处理事件"，这样上层主循环
        在无头和有窗口两种后端上写起来是一样的。
        """
        return self.pump_events()

    def pending_count(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------ 能力

    def dpi_scale(self, window_id: int) -> float:
        self._require_window(window_id)
        return self._dpi_scale

    def set_dpi_scale(self, value: float) -> None:
        """改 DPI 并广播事件——用来测 125% / 150% 缩放。"""
        if value <= 0.0:
            raise BackendError(f"DPI 缩放必须为正，收到 {value}")
        self._dpi_scale = value
        from .base import WindowEvent

        for _window_id, spec in self._windows.items():
            self._pending.append(
                WindowEvent(
                    kind=WindowKind.DPI_CHANGED,
                    time_ms=self.now_ms(),
                    width=spec.width,
                    height=spec.height,
                    dpi_scale=value,
                )
            )

    def set_cursor(self, window_id: int, cursor: Cursor) -> None:
        self._require_window(window_id)
        self._cursor = cursor

    @property
    def cursor(self) -> Cursor:
        return self._cursor

    def clipboard_get_text(self) -> str:
        return self._clipboard

    def clipboard_set_text(self, text: str) -> None:
        self._clipboard = text

    def now_ms(self) -> float:
        return self._time_ms

    def advance(self, ms: float) -> None:
        """手动推进时钟。动画与超时测试的确定性全靠它。"""
        if ms < 0.0:
            raise BackendError(f"时钟不能倒退，收到 {ms}ms")
        self._time_ms += ms

    def request_redraw(self, window_id: int) -> None:
        self._require_window(window_id)
        self._redraw_requests += 1

    @property
    def redraw_requests(self) -> int:
        return self._redraw_requests

    # ------------------------------------------------------------ 渲染占位

    def present(self, display_list: DisplayList, clear_color: Color) -> None:
        """记录本帧结果。`clear_color` 在无头后端只作记录，不产生像素。"""
        self.last_frame = display_list
        self.last_pixels = None

    # ------------------------------------------------------------ 内部

    def _require_window(self, window_id: int) -> None:
        if window_id not in self._windows:
            raise BackendError(f"没有 id 为 {window_id} 的窗口")
