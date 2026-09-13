"""平台后端协议 —— 整个系统里唯一允许碰窗口和显卡的地方。

一句话原则（docs/03 反复强调）：

    **只有后端碰平台，其余全是纯 Python、无窗口可测。**

这条纪律换来的是：布局、组件树、样式、显示列表全都能在 CI 里
不开窗口地测；换平台只需要换一个后端实现。

本模块定义三样东西：

1. **归一化事件** —— 后端把各平台的原生事件翻译成同一套数据类。
   键码用 W3C 风格的稳定名字（`"KeyA"` / `"Enter"` / `"ArrowLeft"`），
   不带任何平台痕迹；翻译发生在后端，`events/` 只负责路由与命中测试。
2. **窗口规格与能力** —— 尺寸、DPI、光标、剪贴板。
3. **Backend 协议** —— 后端必须实现的那几个方法。

关于时钟：

    `now_ms()` 由后端提供，**渲染层不许读墙上时钟**。
    无头后端用注入的时钟（可手动推进），所以动画与超时在测试里完全确定。

状态：已实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Union

from .fonts import (
    FontFace,
    FontMetricsError,
    FontSlant,
    FontSpec,
    FontWeight,
    GlyphPlacement,
    GlyphRun,
    MetricsProvider,
    TextMetrics,
)

__all__ = [
    "Backend",
    "BackendError",
    "Cursor",
    "Event",
    "FocusEvent",
    "FontFace",
    "FontMetricsError",
    "FontSlant",
    "FontSpec",
    "FontWeight",
    "FrameRenderer",
    "GlyphPlacement",
    "GlyphRun",
    "ImeEvent",
    "ImeKind",
    "ImeRect",
    "KeyEvent",
    "KeyKind",
    "MetricsProvider",
    "Modifiers",
    "PointerEvent",
    "PointerKind",
    "PointerType",
    "TextEvent",
    "TextMetrics",
    "WindowEvent",
    "WindowKind",
    "WindowSpec",
]


class BackendError(RuntimeError):
    """后端不可用或使用不当。报错信息必须说清怎么修。"""


class Cursor(Enum):
    """光标形状。枚举而不是字符串——拼错当场报错。"""

    DEFAULT = "default"
    TEXT = "text"
    HAND = "hand"
    CROSSHAIR = "crosshair"
    MOVE = "move"
    RESIZE_ROW = "resize_row"
    RESIZE_COL = "resize_col"
    NOT_ALLOWED = "not_allowed"
    WAIT = "wait"


@dataclass(frozen=True, slots=True)
class Modifiers:
    """修饰键状态。四个布尔，简单直白，不做位运算魔法。"""

    shift: bool = False
    ctrl: bool = False
    alt: bool = False
    meta: bool = False  # Windows 键 / Command 键

    @classmethod
    def none(cls) -> Modifiers:
        return cls()


# ---------------------------------------------------------------- 事件


class PointerKind(Enum):
    DOWN = "down"
    UP = "up"
    MOVE = "move"
    WHEEL = "wheel"
    ENTER = "enter"
    LEAVE = "leave"


class PointerType(Enum):
    """指针设备类型。触屏与鼠标的交互模型不同（没有悬停），必须分得开。"""

    MOUSE = "mouse"
    TOUCH = "touch"
    PEN = "pen"


class KeyKind(Enum):
    DOWN = "down"
    UP = "up"


class ImeKind(Enum):
    """输入法**组合态**事件。中文输入必须走这一套，否则组合态会丢字。

    注意与普通文本上屏（`TextEvent`）的区别（R5.7 拆开的两条通道）：
    按字母键出字走 `TextEvent`；拼音组合中/取消走这里。
    `COMMIT` 留给能区分"IME 上屏"与"普通上屏"的平台（如 Win32 IMM），
    SDL2 的上屏统一走 `TextEvent`。
    """

    COMPOSE = "compose"  # 组合中（拼音还没上屏）
    COMMIT = "commit"  # 上屏
    CANCEL = "cancel"  # 取消组合


class WindowKind(Enum):
    CLOSE = "close"
    RESIZED = "resized"
    DPI_CHANGED = "dpi_changed"
    EXPOSED = "exposed"  # 需要重绘
    FOCUS = "focus"


@dataclass(frozen=True, slots=True)
class PointerEvent:
    """鼠标 / 触控 / 触控笔统一模型。坐标是**窗口内的逻辑像素**。

    `window_id` 标明事件属于哪个窗口（R5.1）——多窗口是 docs/02 §2 的承诺，
    没有它事件到手后无法路由。`clicks` 是双击信息（1=单击，2=双击）；
    `pressure` 为压感预留（无压感设备恒为 1.0）。
    """

    kind: PointerKind
    x: float
    y: float
    window_id: int = 0
    time_ms: float = 0.0
    button: int = 0  # 0=无, 1=左, 2=中, 3=右
    wheel_dx: float = 0.0
    wheel_dy: float = 0.0
    pointer_type: PointerType = PointerType.MOUSE
    pointer_id: int = 0
    clicks: int = 0
    pressure: float = 1.0
    modifiers: Modifiers = field(default_factory=Modifiers.none)


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """键盘事件（R5.6：scancode 与 keysym 分离）。

    `code` 是**物理键位**的稳定名字（W3C code，布局无关）——快捷键用它，
    AZERTY 上也不漂。`key` 是**布局相关**的键值（W3C key），文本语义用它。
    """

    kind: KeyKind
    code: str  # "KeyA" / "Enter" / "NumpadEnter" / "ArrowLeft"
    window_id: int = 0
    key: str = ""  # "a" / "Enter" / "Shift"（布局相关）
    time_ms: float = 0.0
    text: str | None = None  # 可打印字符；功能键为 None
    repeat: bool = False
    modifiers: Modifiers = field(default_factory=Modifiers.none)


@dataclass(frozen=True, slots=True)
class ImeEvent:
    """输入法**组合态**事件。`cursor_start/end` 标记组合串里正在编辑的那一段。

    只承载组合态（COMPOSE/COMMIT/CANCEL）；普通字符上屏是 `TextEvent`（R5.7）。
    """

    kind: ImeKind
    text: str = ""
    window_id: int = 0
    time_ms: float = 0.0
    cursor_start: int = 0
    cursor_end: int = 0


@dataclass(frozen=True, slots=True)
class TextEvent:
    """普通文本上屏（R5.7 拆出的独立通道）。

    按字母键直接出字、IME 选词后上屏，都是这个事件——对输入框来说
    语义只有一个：把这段文字插进光标处。组合态的中间过程与它无关。
    """

    text: str
    window_id: int = 0
    time_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class FocusEvent:
    focused: bool
    window_id: int = 0
    time_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class WindowEvent:
    kind: WindowKind
    window_id: int = 0
    time_ms: float = 0.0
    width: float = 0.0
    height: float = 0.0
    dpi_scale: float = 1.0


# 后端产出的事件联合类型。events/ 层负责消费它做路由与命中测试。
Event = Union[PointerEvent, KeyEvent, ImeEvent, TextEvent, FocusEvent, WindowEvent]  # noqa: UP007


# ---------------------------------------------------------------- 窗口


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """窗口创建参数。

    尺寸是**逻辑像素**。物理像素 = 逻辑 × dpi_scale，
    这个换算是后端的事——上层永远只跟逻辑像素打交道，
    否则 125%/150% 缩放下的错位就无从谈起。
    """

    title: str = "inkstone"
    width: float = 800.0
    height: float = 600.0
    resizable: bool = True
    min_width: float = 0.0
    min_height: float = 0.0
    #: 需要 GL 上屏时置真：后端用 OpenGL 标志建窗口，应用再经
    #: `gl_wgl.sdl_gl_driver` 把光栅器接到这个窗口的上下文上（R8.6）。
    #: 无头后端忽略此字段（它没有真窗口）。
    opengl: bool = False


@dataclass(frozen=True, slots=True)
class ImeRect:
    """IME 候选框的跟随位置（窗口内逻辑像素）。

    为什么不用 `layout.types.Rect`：backend 是 L0，layout 是 L4，
    import 它会是反向依赖（架构测试挡着）。这里只需要一份哑数据。
    """

    x: float
    y: float
    width: float
    height: float


# ---------------------------------------------------------------- 协议


class FrameRenderer(Protocol):
    """后端持有的呈现器接缝（R5.8）。

    为什么签名里没有 `DisplayList`：显示列表类型住在 gfx（L2），
    backend 是 L0，import 它会是反向依赖（架构测试拦着）。
    所以帧的**内容**（`execute(display_list)`）由应用组装层直接交给
    光栅器，后端只经手帧的**边界**——begin 时的平台准备（切换渲染目标）、
    end 时的上屏动作（换链）。gfx 侧用 `RasterFrameRenderer` 适配进来。
    """

    def begin_frame(self, width: float, height: float, scale: float) -> None:
        """开始一帧。尺寸是**逻辑像素**，`scale` 是设备像素比——
        与 `RasterBackend.begin_frame(Size, scale)` 同一套语义。"""

    def end_frame(self) -> None:
        """结束一帧（光栅器侧收尾；swap 与否由后端按 present 决定）。"""


class Backend(Protocol):
    """平台后端。实现者：`HeadlessBackend`（测试）、`SDL2Backend`（生产）。

    **R5.9 起它不再是 MetricsProvider**：度量引擎（R4 的 HB+FT，三平台
    同一个）由 App 组装时注入，窗口后端不再假装自己会量字。
    协议符合性由 `tests/unit/test_backend_protocol.py` 显式断言——
    不靠 `runtime_checkable` 的装饰器摆设。
    """

    @property
    def name(self) -> str:
        """后端名字，用于日志与检查器。"""

    def initialize(self) -> None:
        """初始化（SDL2 之类需要）。必须可重复调用而不出错。"""

    def shutdown(self) -> None:
        """释放资源。"""

    def create_window(self, spec: WindowSpec) -> int:
        """创建窗口，返回窗口 id。"""

    def destroy_window(self, window_id: int) -> None:
        """销毁窗口。"""

    def pump_events(self) -> list[Event]:
        """取出当前待处理的事件（不阻塞）。"""

    def wait_events(self, timeout_ms: float) -> list[Event]:
        """阻塞等待事件，超时返回空列表。空闲时主循环用它省电。"""

    def dpi_scale(self, window_id: int) -> float:
        """该窗口的 DPI 缩放（1.0 / 1.25 / 1.5 …）。"""

    def set_cursor(self, window_id: int, cursor: Cursor) -> None:
        """设置光标形状。"""

    def clipboard_get_text(self) -> str:
        """读剪贴板文本；读不到返回空串。"""

    def clipboard_set_text(self, text: str) -> None:
        """写剪贴板文本。"""

    def now_ms(self) -> float:
        """当前时间（毫秒）。

        **这是全系统唯一的时间来源。** 渲染层不许读 `time.time()`——
        无头后端注入的是可控时钟，动画与超时才能在测试里完全确定。
        """

    def request_redraw(self, window_id: int) -> None:
        """请求下一帧重绘。"""

    # -------------------------------------------------------- 窗口能力（R10）

    # 这些是"应用外壳"要用的能力：标题、最小尺寸、最大化/全屏、标题栏外观。
    # 它们都进协议而不是让 App 直接摸平台——上层代码里不该出现 Win32 调用。

    def set_app_identity(self, app_id: str) -> None:
        """设置应用身份（任务栏分组 / 图标归属）。**建窗口之前**调用。

        Windows 上映射到 AppUserModelID；不设的话任务栏显示解释器（`python.exe`）
        的名字与图标——那是"脚本"而不是"应用"。
        """

    def set_title(self, window_id: int, title: str) -> None:
        """更新窗口标题。"""

    def set_min_size(self, window_id: int, width: float, height: float) -> None:
        """设置窗口最小尺寸（逻辑像素）。"""

    def set_icon(self, window_id: int, width: int, height: int, rgba: bytes) -> None:
        """设置窗口/任务栏图标。

        `rgba` 是 `width × height × 4` 字节的**原始像素**——图标由应用外壳
        用自己的渲染器画出来（`inkstone.app.app_icon_rgba`），后端只负责把它
        交给窗口系统。这样后端不必认识显示列表、字体或主题令牌（L0 纪律），
        图标也不必以二进制形式进仓库。
        """

    def set_maximized(self, window_id: int, maximized: bool) -> None:
        """最大化 / 还原。"""

    def set_fullscreen(self, window_id: int, enabled: bool) -> None:
        """进入 / 退出全屏。"""

    def set_window_theme(
        self, window_id: int, *, dark: bool, background: int | None = None
    ) -> None:
        """把应用主题告诉窗口系统（标题栏配色）。

        `background` 是 `0xRRGGBB`（L0 不认识 gfx 的 Color 类型）；None = 用
        平台默认。Windows 上映射到 DWM 的深色标题栏 + 标题栏底色——**保留
        原生边框**（Snap Layouts / 贴靠 / 无障碍全都还在），只是把它染成
        与应用一致的颜色。
        """

    # -------------------------------------------------------- 文本输入 / IME

    # 中文输入的三条命脉（R5.7）。SDL2 的 TEXTINPUT/TEXTEDITING 事件
    # 在调用 start_text_input 之前**根本不产生**——不实现这三个方法，
    # "中文 IME 深度控制"就是物理不通的。

    def start_text_input(self, window_id: int) -> None:
        """开始接收文本输入（打开 IME 组合事件）。"""

    def stop_text_input(self, window_id: int) -> None:
        """停止接收文本输入。"""

    def set_ime_rect(self, window_id: int, rect: ImeRect) -> None:
        """设置 IME 候选框位置（跟随光标，不遮住正在输入的文字）。"""

    # -------------------------------------------------------- 呈现接缝（R5.8）

    # "帧怎么上屏"是后端最核心的职责之一。帧的**内容**由应用层直接交给
    # 光栅器（`RasterBackend.execute`）；这里只管帧的**边界**。

    def begin_frame(self, window_id: int) -> None:
        """开始一帧（平台侧准备：切换渲染目标、记录帧边界）。"""

    def end_frame(self, window_id: int, *, present: bool = True) -> None:
        """结束一帧。`present=False` 只算不画（离屏/测试），不触发上屏。"""
