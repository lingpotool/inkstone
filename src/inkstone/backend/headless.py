"""无头后端 —— 不开窗口，供 CI 黄金图测试与自动化测试使用。

它是"只有后端碰平台"这条纪律的收益兑现：

    同样的测试代码，在无头后端上跑得起来、跑得飞快、跑得完全确定。

两个关键设计：

1. **时钟是注入的。** `now_ms()` 返回内部计数，只能用 `advance()` 推进。
   所以"动画播到第 300ms"这类断言在 CI 里和在你机器上是同一个结果——
   渲染层不读墙上时钟，才有这种确定性。
2. **事件是注入的。** 测试用 `inject()` 塞事件，然后 `pump_events()` 取。
   不需要真的去移动鼠标，也不需要系统提供输入设备。

DPI 按窗口设置（`set_dpi_scale(window_id, value)`），用来验证 125% / 150%
缩放下单个窗口的行为——真机上把窗口拖到另一块显示器就是这种语义。

状态：已实现。
"""

from __future__ import annotations

from .base import (
    BackendError,
    Cursor,
    Event,
    ImeRect,
    WindowEvent,
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

    显式注入 > 系统真字体（HarfBuzz + FreeType，若可用）> 确定性表。

    "系统真字体不可用就静默退回确定性表"是有意为之：uharfbuzz / freetype-py
    是运行时依赖，理论上一定在，但极简环境（嵌入解释器、裁剪过的部署）
    可能缺 wheel——这时单元测试仍要能跑，而不是直接起不来。
    """
    if font_engine is not None:
        return font_engine
    if system_fonts:
        try:
            from .hbft_fonts import hbft_font_engine

            return hbft_font_engine()
        except ImportError:
            pass
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
        # DPI 是 per-window 的（R5.10）：真机上每个显示器缩放可以不同，
        # 拖到另一块屏只该影响那一个窗口。
        self._dpi_scales: dict[int, float] = {}
        self._cursor: Cursor = Cursor.DEFAULT
        self._clipboard = ""
        self._initialized = False
        self._redraw_requests = 0
        # IME 状态（R5.7）：记录哪些窗口开着文本输入、候选框在哪。
        # 上层测试靠这两个断言"输入框聚焦时真的打开了 IME 通道"。
        self._text_input_windows: set[int] = set()
        self._ime_rects: dict[int, ImeRect] = {}
        # 帧接缝（R5.8）：记录帧边界，测试可以断言"这帧真的上过屏"。
        self._frames_presented = 0
        self._in_frame = False
        # 窗口能力（R10）：记录调用，供应用外壳测试断言
        self._titles: dict[int, str] = {}
        self._min_sizes: dict[int, tuple[float, float]] = {}
        self._icons: dict[int, tuple[int, int, bytes]] = {}
        self._maximized: dict[int, bool] = {}
        self._fullscreen: dict[int, bool] = {}
        self._window_themes: dict[int, tuple[bool, int | None]] = {}
        self.app_id: str = ""
        # 字体度量：默认走确定性表（跨平台一致，黄金图才能逐字节比对）。
        # `font_engine` 允许注入任意 MetricsProvider；`system_fonts=True`
        # 是"尽量用系统真字体"的糖，拿不到引擎时静默退回确定性表。
        self._metrics: MetricsProvider = _resolve_metrics(font_table, system_fonts, font_engine)

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
        self._dpi_scales.clear()
        self._text_input_windows.clear()
        self._ime_rects.clear()
        self._pending.clear()
        self._icons.clear()

    # ------------------------------------------------------------ 窗口

    def create_window(self, spec: WindowSpec) -> int:
        if not self._initialized:
            raise BackendError("先调用 initialize() 再创建窗口")
        if spec.width <= 0 or spec.height <= 0:
            raise BackendError(f"窗口尺寸必须为正，收到 {spec.width}×{spec.height}")
        window_id = self._next_id
        self._next_id += 1
        self._windows[window_id] = spec
        self._dpi_scales[window_id] = 1.0
        return window_id

    def destroy_window(self, window_id: int) -> None:
        self._require_window(window_id)
        del self._windows[window_id]
        self._dpi_scales.pop(window_id, None)
        self._text_input_windows.discard(window_id)
        self._ime_rects.pop(window_id, None)
        self._icons.pop(window_id, None)

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
        self._pending.append(
            WindowEvent(
                kind=WindowKind.RESIZED,
                window_id=window_id,
                time_ms=self.now_ms(),
                width=width,
                height=height,
                dpi_scale=self._dpi_scales[window_id],
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
        return self._dpi_scales[window_id]

    def set_dpi_scale(self, window_id: int, value: float) -> None:
        """改某个窗口的 DPI 并给它发事件——用来测 125% / 150% 缩放。

        R5.10 起是 per-window 的：真机上把窗口拖到另一块显示器，
        只影响那个窗口（SDL 那边是 `WINDOWEVENT_MOVED` 触发轮询）。
        """
        self._require_window(window_id)
        if value <= 0.0:
            raise BackendError(f"DPI 缩放必须为正，收到 {value}")
        self._dpi_scales[window_id] = value
        spec = self._windows[window_id]
        self._pending.append(
            WindowEvent(
                kind=WindowKind.DPI_CHANGED,
                window_id=window_id,
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

    # ------------------------------------------------------------ 窗口能力（R10）
    #
    # 无头后端把它们**记录下来**而不是丢弃：应用外壳的行为（标题、最小尺寸、
    # 主题跟随）因此在 CI 里可断言，不需要真窗口。

    def set_app_identity(self, app_id: str) -> None:
        self.app_id = app_id

    def set_title(self, window_id: int, title: str) -> None:
        self._require_window(window_id)
        self._titles[window_id] = title

    def set_min_size(self, window_id: int, width: float, height: float) -> None:
        self._require_window(window_id)
        self._min_sizes[window_id] = (width, height)

    def set_icon(self, window_id: int, width: int, height: int, rgba: bytes) -> None:
        self._require_window(window_id)
        if len(rgba) != width * height * 4:
            raise BackendError(f"图标像素长度应为 {width * height * 4}，收到 {len(rgba)}")
        self._icons[window_id] = (width, height, rgba)

    def set_maximized(self, window_id: int, maximized: bool) -> None:
        self._require_window(window_id)
        self._maximized[window_id] = maximized

    def set_fullscreen(self, window_id: int, enabled: bool) -> None:
        self._require_window(window_id)
        self._fullscreen[window_id] = enabled

    def set_window_theme(
        self, window_id: int, *, dark: bool, background: int | None = None
    ) -> None:
        self._require_window(window_id)
        self._window_themes[window_id] = (dark, background)

    def window_title(self, window_id: int) -> str | None:
        return self._titles.get(window_id)

    def window_min_size(self, window_id: int) -> tuple[float, float] | None:
        return self._min_sizes.get(window_id)

    def window_icon(self, window_id: int) -> tuple[int, int, bytes] | None:
        """窗口图标 (宽, 高, RGBA)——测试断言"图标真的交到了后端"。"""
        return self._icons.get(window_id)

    def window_theme(self, window_id: int) -> tuple[bool, int | None] | None:
        return self._window_themes.get(window_id)

    def is_maximized(self, window_id: int) -> bool:
        return self._maximized.get(window_id, False)

    def is_fullscreen(self, window_id: int) -> bool:
        return self._fullscreen.get(window_id, False)

    @property
    def redraw_requests(self) -> int:
        return self._redraw_requests

    # ------------------------------------------------------------ 文本输入 / IME（R5.7）

    def start_text_input(self, window_id: int) -> None:
        self._require_window(window_id)
        self._text_input_windows.add(window_id)

    def stop_text_input(self, window_id: int) -> None:
        self._require_window(window_id)
        self._text_input_windows.discard(window_id)
        self._ime_rects.pop(window_id, None)

    def set_ime_rect(self, window_id: int, rect: ImeRect) -> None:
        self._require_window(window_id)
        self._ime_rects[window_id] = rect

    def text_input_active(self, window_id: int) -> bool:
        """该窗口的文本输入是否开着——上层测试断言 IME 通道就靠它。"""
        return window_id in self._text_input_windows

    def ime_rect(self, window_id: int) -> ImeRect | None:
        return self._ime_rects.get(window_id)

    # ------------------------------------------------------------ 呈现接缝（R5.8）

    def begin_frame(self, window_id: int) -> None:
        self._require_window(window_id)
        self._in_frame = True

    def end_frame(self, window_id: int, *, present: bool = True) -> None:
        self._require_window(window_id)
        self._in_frame = False
        if present:
            self._frames_presented += 1

    @property
    def frames_presented(self) -> int:
        """上过屏的帧数。测试断言"这一帧真的 present 了"就靠它。"""
        return self._frames_presented

    # ------------------------------------------------------------ 字体度量
    #
    # 直接委派给内部 HeadlessMetrics。度量是**全库唯一入口**：
    # text/ 断行与 gfx/ 绘制都走这里，两套度量在无头后端上无法出现。

    @property
    def font_metrics(self) -> MetricsProvider:
        """当前的度量提供方。

        可能是确定性的 `HeadlessMetrics`，也可能是真字体引擎
        （`HbFtFontEngine`）。**它同时也可能是字形提供方**——
        如果它实现了 `mask_for`，光栅层就应当用它取字形，
        这样度量与字形必然同源。`devtools` 就是这么自动配对的。
        """
        return self._metrics

    def has_family(self, family: str) -> bool:
        return self._metrics.has_family(family)

    def has_glyph(self, family: str, char: str) -> bool:
        return self._metrics.has_glyph(family, char)

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

    def stats(self) -> dict[str, int]:
        """度量缓存统计（`MetricsProvider` 协议成员）。"""
        return self._metrics.stats()

    def font_stats(self) -> dict[str, int]:
        """`stats()` 的别名（沿用旧名）。"""
        return self._metrics.stats()

    # ------------------------------------------------------------ 内部

    def _require_window(self, window_id: int) -> None:
        if window_id not in self._windows:
            raise BackendError(f"没有 id 为 {window_id} 的窗口")
