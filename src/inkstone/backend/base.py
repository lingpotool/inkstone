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

__all__ = [
    "Backend",
    "BackendError",
    "Cursor",
    "Event",
    "FocusEvent",
    "ImeEvent",
    "ImeKind",
    "KeyEvent",
    "KeyKind",
    "Modifiers",
    "PointerEvent",
    "PointerKind",
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


class KeyKind(Enum):
    DOWN = "down"
    UP = "up"


class ImeKind(Enum):
    """输入法事件。中文输入必须走这一套，否则组合态会丢字。"""

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
    """鼠标 / 触控 / 触控笔统一模型。坐标是**窗口内的逻辑像素**。"""

    kind: PointerKind
    x: float
    y: float
    time_ms: float = 0.0
    button: int = 0  # 0=无, 1=左, 2=中, 3=右
    wheel_dx: float = 0.0
    wheel_dy: float = 0.0
    modifiers: Modifiers = field(default_factory=Modifiers.none)


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """键盘事件。`code` 是物理按键的稳定名字，`text` 是它产生的字符。"""

    kind: KeyKind
    code: str  # "KeyA" / "Enter" / "Escape" / "ArrowLeft"
    time_ms: float = 0.0
    text: str | None = None  # 可打印字符；功能键为 None
    repeat: bool = False
    modifiers: Modifiers = field(default_factory=Modifiers.none)


@dataclass(frozen=True, slots=True)
class ImeEvent:
    """输入法事件。`cursor_start/end` 标记组合串里正在编辑的那一段。"""

    kind: ImeKind
    text: str = ""
    time_ms: float = 0.0
    cursor_start: int = 0
    cursor_end: int = 0


@dataclass(frozen=True, slots=True)
class FocusEvent:
    focused: bool
    time_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class WindowEvent:
    kind: WindowKind
    time_ms: float = 0.0
    width: float = 0.0
    height: float = 0.0
    dpi_scale: float = 1.0


# 后端产出的事件联合类型。events/ 层负责消费它做路由与命中测试。
Event = Union[PointerEvent, KeyEvent, ImeEvent, FocusEvent, WindowEvent]  # noqa: UP007


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


# ---------------------------------------------------------------- 协议


class Backend(Protocol):
    """平台后端。实现者：`HeadlessBackend`（测试）、`SDL2Backend`（生产）。"""

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
