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
from .fonts import FontFace, FontSpec, GlyphRun, MetricsProvider, TextMetrics
from .headless_fonts import FontTable, HeadlessMetrics

__all__ = ["HeadlessBackend"]


def _resolve_metrics(
    font_table: FontTable | None,
    system_fonts: bool,
    font_engine: MetricsProvider | None,
) -> MetricsProvider:
    """按优先级挑一个度量提供方。

    显式注入 > 系统真字体（若可用）> 确定性表。

    "系统真字体不可用就静默退回确定性表"是有意为之：CI 上没有 GDI，
    单元测试仍要能跑；而**渲染出真中文**只在真机上才会发生，
    这正是我们要的（黄金图用确定性源，真机预览用系统源）。
    """
    if font_engine is not None:
        return font_engine
    if system_fonts:
        from .gdi_fonts import gdi_font_engine

        engine = gdi_font_engine()
        if engine is not None:
            return engine
    return HeadlessMetrics(font_table)


class HeadlessBackend:
    """无头后端：假窗口、注入式事件、可控时钟、确定性字体度量。

    字体度量用混入而非继承，是为了让**纯文本层测试**可以只拿
    `HeadlessMetrics()` 而不用造一个后端（它不需要窗口和时钟）。
    这里做的是把两件正交的能力拼在一起。
    """

    def __init__(
        self,
        *,
        start_time_ms: float = 0.0,
        font_table: FontTable | None = None,
        system_fonts: bool = False,
        font_engine: MetricsProvider | None = None,
    ) -> None:
        self._time_ms = start_time_ms
        self._pending: list[Event] = []
        self._windows: dict[int, WindowSpec] = {}
        self._next_id = 1
        self._dpi_scale = 1.0
        self._cursor: Cursor = Cursor.DEFAULT
        self._clipboard = ""
        self._initialized = False
        self._redraw_requests = 0
        # 字体度量：默认走确定性表（跨平台一致，黄金图才能逐字节比对）。
        # `font_engine` 允许注入任意 MetricsProvider；`system_fonts=True`
        # 是"尽量用系统真字体"的糖，拿不到引擎时静默退回确定性表。
        self._metrics: MetricsProvider = _resolve_metrics(font_table, system_fonts, font_engine)
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

    # ------------------------------------------------------------ 字体度量
    #
    # 直接委派给内部 HeadlessMetrics。度量是**全库唯一入口**：
    # text/ 断行与 gfx/ 绘制都走这里，两套度量在无头后端上无法出现。

    @property
    def font_metrics(self) -> MetricsProvider:
        """当前的度量提供方。

        可能是确定性的 `HeadlessMetrics`，也可能是系统真字体引擎
        （`GdiFontEngine`）。**它同时也可能是字形提供方**——
        如果它实现了 `mask_for`，光栅层就应当用它取字形，
        这样度量与字形必然同源。`devtools` 就是这么自动配对的。
        """
        return self._metrics

    def has_family(self, family: str) -> bool:
        return self._metrics.has_family(family)

    def resolve_font(self, spec: FontSpec) -> FontFace:
        return self._metrics.resolve_font(spec)

    def measure_text(self, text: str, spec: FontSpec) -> TextMetrics:
        return self._metrics.measure_text(text, spec)

    def shape_line(self, text: str, spec: FontSpec) -> GlyphRun:
        return self._metrics.shape_line(text, spec)

    @property
    def glyph_provider(self) -> object | None:
        """能提供字形的对象；确定性度量表提供不了，返回 None。

        这个方法存在是为了让上层能**自动配对**度量与字形：
        `devtools` 拿到它就直接喂给光栅器，于是"度量用的字体"
        与"字形用的字体"不可能是两个。返回 None 时上层用内置确定性字形。

        为什么不让 `HeadlessBackend` 自己实现 `mask_for` 来伪装成字形提供方：
        那样 `hasattr(backend, "mask_for")` 永远为真，上层就分不清
        "这个后端能画真字形"还是"它只是转发给了一个画不了的东西"。
        显式返回 None 比隐式的能力嗅探可靠。
        """
        if hasattr(self._metrics, "mask_for"):
            return self._metrics
        return None

    def font_stats(self) -> dict[str, int]:
        """度量缓存统计。"""
        return self._metrics.stats()

    # ------------------------------------------------------------ 渲染占位

    def present(self, display_list: DisplayList, clear_color: Color) -> None:
        """记录本帧结果。`clear_color` 在无头后端只作记录，不产生像素。"""
        self.last_frame = display_list
        self.last_pixels = None

    # ------------------------------------------------------------ 内部

    def _require_window(self, window_id: int) -> None:
        if window_id not in self._windows:
            raise BackendError(f"没有 id 为 {window_id} 的窗口")
