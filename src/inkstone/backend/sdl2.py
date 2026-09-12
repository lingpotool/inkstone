"""SDL2 后端 —— 生产用的主后端。

选 SDL2 而不是 GLFW 的理由（docs/03 已经论证过）：原生 Wayland 支持、
完整的 IME 组合事件、三平台一致的窗口与输入模型。

**关于测试覆盖，说清楚**：创建窗口、泵事件这些需要真实窗口与输入设备，
CI 里跑不了。但**事件翻译拆成了纯函数**（`translate_*`，直接构造 ctypes
结构体就能测，R5），所以"SDL 事件 → 归一化事件"这条链路是全覆盖的。
仍需真窗口的部分（`create_window` / `pump_events` 的泵循环本体）
失败时报的错都带修复指引。

宁可写明"这块没测"，也不要假装它被测过。

状态：已实现（事件翻译纯函数覆盖；窗口生命周期需实机验证）。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from dataclasses import replace
from typing import Any

from .base import (
    BackendError,
    Cursor,
    Event,
    FocusEvent,
    FrameRenderer,
    ImeEvent,
    ImeKind,
    ImeRect,
    KeyEvent,
    KeyKind,
    Modifiers,
    PointerEvent,
    PointerKind,
    PointerType,
    TextEvent,
    WindowEvent,
    WindowKind,
    WindowSpec,
)

__all__ = ["SDL2Backend", "normalize_key_name", "normalize_key_value", "sdl2_library_name"]

# ---------------------------------------------------------------- 常量

_SDL_INIT_VIDEO = 0x00000020

_SDL_WINDOW_RESIZABLE = 0x00000020
_SDL_WINDOW_ALLOW_HIGHDPI = 0x00002000

#: SDL_WINDOWPOS_CENTERED 是带掩码的特值（0x2FFF0000），不是 -1。
#: -1 恰好在数值上等于 SDL_WINDOWPOS_UNDEFINED——"能跑"纯属巧合。
_SDL_WINDOWPOS_CENTERED = 0x2FFF0000

_SDL_QUIT = 0x100

_SDL_WINDOWEVENT = 0x200
_SDL_WINDOWEVENT_MOVED = 4
_SDL_WINDOWEVENT_RESIZED = 5
_SDL_WINDOWEVENT_EXPOSED = 3
_SDL_WINDOWEVENT_ENTER = 10
_SDL_WINDOWEVENT_LEAVE = 11
_SDL_WINDOWEVENT_FOCUS_GAINED = 12
_SDL_WINDOWEVENT_FOCUS_LOST = 13
_SDL_WINDOWEVENT_CLOSE = 14

_SDL_KEYDOWN = 0x300
_SDL_KEYUP = 0x301
_SDL_TEXTEDITING = 0x302
_SDL_TEXTINPUT = 0x303

_SDL_MOUSEMOTION = 0x400
_SDL_MOUSEBUTTONDOWN = 0x401
_SDL_MOUSEBUTTONUP = 0x402
_SDL_MOUSEWHEEL = 0x403

#: 触摸产生的合成鼠标事件，which 字段是这个特值（Uint32 的 -1）
_SDL_TOUCH_MOUSEID = 0xFFFFFFFF

#: macOS 自然滚动：滚轮方向翻转（SDL 2.0.4+）
_SDL_MOUSEWHEEL_FLIPPED = 1

_SDL_BUTTON_LEFT = 1
_SDL_BUTTON_MIDDLE = 2
_SDL_BUTTON_RIGHT = 3

_SDL_CURSOR_ARROW = 0
_SDL_CURSOR_IBEAM = 1
_SDL_CURSOR_HAND = 11
_SDL_CURSOR_CROSSHAIR = 3
_SDL_CURSOR_SIZEALL = 9
_SDL_CURSOR_SIZEWE = 5
_SDL_CURSOR_SIZENS = 6
_SDL_CURSOR_NO = 10
_SDL_CURSOR_WAIT = 7

_CURSOR_TO_SDL = {
    Cursor.DEFAULT: _SDL_CURSOR_ARROW,
    Cursor.TEXT: _SDL_CURSOR_IBEAM,
    Cursor.HAND: _SDL_CURSOR_HAND,
    Cursor.CROSSHAIR: _SDL_CURSOR_CROSSHAIR,
    Cursor.MOVE: _SDL_CURSOR_SIZEALL,
    Cursor.RESIZE_COL: _SDL_CURSOR_SIZEWE,
    Cursor.RESIZE_ROW: _SDL_CURSOR_SIZENS,
    Cursor.NOT_ALLOWED: _SDL_CURSOR_NO,
    Cursor.WAIT: _SDL_CURSOR_WAIT,
}


# 按平台列候选库名。用查表而不是 if 链：mypy 会按当前平台收窄 sys.platform，
# if 链会被判成"不可达"。
_LIBRARY_CANDIDATES: dict[str, tuple[str, ...]] = {
    "win32": ("SDL2.dll",),
    "darwin": ("libSDL2-2.0.0.dylib", "libSDL2.dylib", "SDL2.framework/SDL2"),
}
_DEFAULT_CANDIDATES: tuple[str, ...] = ("libSDL2-2.0.so.0", "libSDL2.so")


def sdl2_library_name() -> str:
    """各平台上 SDL2 动态库的首选名字。找不着时报错信息里会带上它。"""
    return _LIBRARY_CANDIDATES.get(sys.platform, _DEFAULT_CANDIDATES)[0]


# ---------------------------------------------------------------- 键名归一化
#
# R5.6：scancode（物理键位）与 keysym（布局相关）是两套名字，分开归一化：
#   code ← SDL_GetScancodeName(scancode)  —— 快捷键用它，AZERTY 上不漂
#   key  ← SDL_GetKeyName(keysym)         —— 文本语义用它（布局相关）

_SCANCODE_TO_CODE = {
    "Escape": "Escape",
    "Return": "Enter",
    # 物理键位上小键盘回车是**另一个键**（W3C code: NumpadEnter），
    # 不与主键盘回车合并——“回车确认 vs 小键盘回车”有应用要区分。
    "Keypad Enter": "NumpadEnter",
    "Space": "Space",
    "Backspace": "Backspace",
    "Tab": "Tab",
    "Delete": "Delete",
    "Insert": "Insert",
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
    "Up": "ArrowUp",
    "Down": "ArrowDown",
    "Home": "Home",
    "End": "End",
    "PageUp": "PageUp",
    "PageDown": "PageDown",
    "Left Shift": "ShiftLeft",
    "Right Shift": "ShiftRight",
    "Left Ctrl": "ControlLeft",
    "Right Ctrl": "ControlRight",
    "Left Alt": "AltLeft",
    "Right Alt": "AltRight",
    "Left GUI": "MetaLeft",
    "Right GUI": "MetaRight",
    "CapsLock": "CapsLock",
}

#: keysym 名 → W3C key 值。布局相关：AZERTY 的 A 键 keysym 是 "q"，
#: key 值就是 "q"（这正是 key 与 code 分工的意义）。
_KEYSYM_TO_KEY = {
    "Return": "Enter",
    "Keypad Enter": "Enter",  # 语义上都是"确认"，key 层不区分
    "Escape": "Escape",
    "Space": " ",
    "Backspace": "Backspace",
    "Tab": "Tab",
    "Delete": "Delete",
    "Insert": "Insert",
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
    "Up": "ArrowUp",
    "Down": "ArrowDown",
    "Home": "Home",
    "End": "End",
    "PageUp": "PageUp",
    "PageDown": "PageDown",
    "Left Shift": "Shift",
    "Right Shift": "Shift",
    "Left Ctrl": "Control",
    "Right Ctrl": "Control",
    "Left Alt": "Alt",
    "Right Alt": "Alt",
    "Left GUI": "Meta",
    "Right GUI": "Meta",
    "CapsLock": "CapsLock",
}


def normalize_key_name(name: str) -> str:
    """SDL 的 **scancode** 名 → W3C 风格的物理键码（`KeyEvent.code`）。

    这是纯函数，因此在无 SDL2 的环境下也能测——
    键码归一化写错了很难发现，所以单独拎出来覆盖。
    """
    if not name:
        return "Unidentified"
    if name in _SCANCODE_TO_CODE:
        return _SCANCODE_TO_CODE[name]
    if len(name) == 1:
        if name.isalpha():
            return "Key" + name.upper()
        if name.isdigit():
            return "Digit" + name
    return name


def normalize_key_value(name: str) -> str:
    """SDL 的 **keysym** 名 → W3C `key` 值（`KeyEvent.key`，布局相关）。

    可打印字符原样保留（"a" 就是 "a"——AZERTY 上会自然是 "q"）。
    """
    if not name:
        return "Unidentified"
    if name in _KEYSYM_TO_KEY:
        return _KEYSYM_TO_KEY[name]
    if len(name) == 1:
        return name
    return name


# ---------------------------------------------------------------- 结构体


class _SDL_Keysym(ctypes.Structure):
    _fields_ = [
        ("scancode", ctypes.c_int32),
        ("sym", ctypes.c_int32),
        ("mod", ctypes.c_uint16),
        ("unused", ctypes.c_uint32),
    ]


class _KeyboardEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("state", ctypes.c_uint8),
        ("repeat", ctypes.c_uint8),
        ("padding2", ctypes.c_uint8),
        ("padding3", ctypes.c_uint8),
        ("keysym", _SDL_Keysym),
    ]


class _MouseMotionEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("which", ctypes.c_uint32),
        ("state", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
    ]


class _MouseButtonEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("which", ctypes.c_uint32),
        ("button", ctypes.c_uint8),
        ("state", ctypes.c_uint8),
        ("clicks", ctypes.c_uint8),
        ("padding", ctypes.c_uint8),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
    ]


class _MouseWheelEvent(ctypes.Structure):
    # preciseX/preciseY 是 SDL 2.0.18+ 加的浮点增量（触摸板平滑滚动）；
    # mouseX/mouseY 是 2.26+ 加的真实指针位置。旧 SDL 的结构体更短——
    # 我们的缓冲区比它大且清零过，多读的字段恒为 0，翻译函数按 0 兜底。
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("which", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("direction", ctypes.c_uint32),
        ("precise_x", ctypes.c_float),
        ("precise_y", ctypes.c_float),
        ("mouse_x", ctypes.c_int32),
        ("mouse_y", ctypes.c_int32),
    ]


class _SDLRect(ctypes.Structure):
    """SDL_Rect —— SDL_SetTextInputRect 的参数。"""

    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("w", ctypes.c_int),
        ("h", ctypes.c_int),
    ]


class _TextInputEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("text", ctypes.c_char * 32),
    ]


class _TextEditingEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("text", ctypes.c_char * 32),
        ("start", ctypes.c_int32),
        ("length", ctypes.c_int32),
    ]


class _WindowEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("event", ctypes.c_uint8),
        ("padding1", ctypes.c_uint8),
        ("padding2", ctypes.c_uint8),
        ("padding3", ctypes.c_uint8),
        ("data1", ctypes.c_int32),
        ("data2", ctypes.c_int32),
    ]


# SDL_Event 是个 union，最大 56 字节。用一个足够大的缓冲区承载它。
_EVENT_BUFFER_SIZE = 64


# ---------------------------------------------------------------- 事件翻译（纯函数）
#
# SDL 事件结构体 → 归一化事件，全部拆成**不碰 lib** 的纯函数：
# 测试可以直接构造 ctypes 结构体喂进来，不需要真窗口（R5 的测试策略）。
# 时间戳一律用事件自带的 `timestamp`（R5.2）——泵时刻的时间戳会让
# 一帧内积压的 20 个事件拿到几乎相同的时间，双击判定就废在这种事上。


def _pointer_type_of(which: int) -> PointerType:
    """`which == SDL_TOUCH_MOUSEID` 表示这是触摸合成的鼠标事件。"""
    return PointerType.TOUCH if which == _SDL_TOUCH_MOUSEID else PointerType.MOUSE


def translate_keyboard(raw: Any, *, down: bool, code_name: str, key_name: str) -> KeyEvent:
    """键盘事件。`code_name` 来自 scancode（物理位），`key_name` 来自 keysym（布局）。"""
    return KeyEvent(
        kind=KeyKind.DOWN if down else KeyKind.UP,
        code=normalize_key_name(code_name),
        window_id=int(raw.window_id),
        key=normalize_key_value(key_name),
        time_ms=float(raw.timestamp),
        repeat=bool(raw.repeat),
        modifiers=_modifiers_from(raw.keysym.mod),
    )


def translate_text_input(raw: Any) -> TextEvent:
    """普通文本上屏（R5.7）：字母键出字与 IME 选词上屏都走这里。"""
    return TextEvent(
        text=raw.text.decode("utf-8", "replace"),
        window_id=int(raw.window_id),
        time_ms=float(raw.timestamp),
    )


def translate_text_editing(raw: Any) -> ImeEvent:
    """IME 组合态。空串的组合事件 = 组合被取消（CANCEL 的发射路径，R5.7）。"""
    text = raw.text.decode("utf-8", "replace")
    if not text:
        return ImeEvent(
            kind=ImeKind.CANCEL, window_id=int(raw.window_id), time_ms=float(raw.timestamp)
        )
    return ImeEvent(
        kind=ImeKind.COMPOSE,
        text=text,
        window_id=int(raw.window_id),
        time_ms=float(raw.timestamp),
        cursor_start=int(raw.start),
        cursor_end=int(raw.start) + int(raw.length),
    )


def translate_motion(raw: Any) -> PointerEvent:
    return PointerEvent(
        kind=PointerKind.MOVE,
        x=float(raw.x),
        y=float(raw.y),
        window_id=int(raw.window_id),
        time_ms=float(raw.timestamp),
        pointer_type=_pointer_type_of(int(raw.which)),
        pointer_id=0 if int(raw.which) == _SDL_TOUCH_MOUSEID else int(raw.which),
    )


def translate_button(raw: Any, *, down: bool) -> PointerEvent:
    """鼠标按键。`clicks` 是 SDL 算好的击键次数（1=单击，2=双击），直接透传。"""
    return PointerEvent(
        kind=PointerKind.DOWN if down else PointerKind.UP,
        x=float(raw.x),
        y=float(raw.y),
        window_id=int(raw.window_id),
        time_ms=float(raw.timestamp),
        button=int(raw.button),
        clicks=int(raw.clicks),
        pointer_type=_pointer_type_of(int(raw.which)),
    )


def translate_wheel(raw: Any, fallback_x: float, fallback_y: float) -> PointerEvent:
    """滚轮：精度、方向、坐标三件套（R5.4）。

    - **精度**：优先用 2.0.18+ 的浮点增量（触摸板平滑滚动）；为 0 说明
      旧 SDL 没写这个字段，退回整数值。
    - **方向**：macOS 自然滚动会带 FLIPPED 标记，语义统一为"内容跟着手指走"
      之外的标准方向——翻转在这里做掉，上层不用知道平台差异。
    - **坐标**：2.26+ 的事件自带指针位置；旧 SDL 由调用方查
      `SDL_GetMouseState` 传进来兜底。
    """
    dx = float(raw.precise_x) if raw.precise_x != 0 else float(raw.x)
    dy = float(raw.precise_y) if raw.precise_y != 0 else float(raw.y)
    if int(raw.direction) == _SDL_MOUSEWHEEL_FLIPPED:
        dx, dy = -dx, -dy
    if raw.mouse_x or raw.mouse_y:
        x, y = float(raw.mouse_x), float(raw.mouse_y)
    else:
        x, y = fallback_x, fallback_y
    return PointerEvent(
        kind=PointerKind.WHEEL,
        x=x,
        y=y,
        window_id=int(raw.window_id),
        time_ms=float(raw.timestamp),
        wheel_dx=dx,
        wheel_dy=dy,
        pointer_type=_pointer_type_of(int(raw.which)),
    )


def translate_window_event(raw: Any) -> Event | None:
    """窗口事件。MOVED 不在这里处理——它要查 DPI（需要 lib），由后端自己管。"""
    window_id = int(raw.window_id)
    time_ms = float(raw.timestamp)
    if raw.event == _SDL_WINDOWEVENT_RESIZED:
        return WindowEvent(
            kind=WindowKind.RESIZED,
            window_id=window_id,
            time_ms=time_ms,
            width=float(raw.data1),
            height=float(raw.data2),
        )
    if raw.event == _SDL_WINDOWEVENT_EXPOSED:
        return WindowEvent(kind=WindowKind.EXPOSED, window_id=window_id, time_ms=time_ms)
    if raw.event == _SDL_WINDOWEVENT_CLOSE:
        return WindowEvent(kind=WindowKind.CLOSE, window_id=window_id, time_ms=time_ms)
    if raw.event == _SDL_WINDOWEVENT_FOCUS_GAINED:
        return FocusEvent(focused=True, window_id=window_id, time_ms=time_ms)
    if raw.event == _SDL_WINDOWEVENT_FOCUS_LOST:
        return FocusEvent(focused=False, window_id=window_id, time_ms=time_ms)
    if raw.event == _SDL_WINDOWEVENT_ENTER:
        return PointerEvent(
            kind=PointerKind.ENTER, x=0.0, y=0.0, window_id=window_id, time_ms=time_ms
        )
    if raw.event == _SDL_WINDOWEVENT_LEAVE:
        return PointerEvent(
            kind=PointerKind.LEAVE, x=0.0, y=0.0, window_id=window_id, time_ms=time_ms
        )
    return None


# ---------------------------------------------------------------- 后端


class SDL2Backend:
    """SDL2 后端。构造时不会加载库，`initialize()` 才加载——导入保持安全。

    `renderer`（R5.8）：呈现器以**组合**方式注入（如
    `gfx.raster.RasterFrameRenderer(SoftwareRasterizer())`）。
    帧的内容由应用层直接交给光栅器；后端只在帧边界上经手它
    （begin 时告诉它物理尺寸，end 时收尾并决定上不上屏）。
    """

    def __init__(self, *, renderer: FrameRenderer | None = None) -> None:
        self._lib: Any = None
        self._windows: dict[int, Any] = {}
        self._next_id = 1
        # SDL 事件里带的是 SDL 自己的 windowID，必须映射回我们的窗口 id，
        # 否则事件到手不知道属于哪个窗口（R5.1 的另一半）。
        self._by_sdl_id: dict[int, int] = {}
        # 每个窗口最近一次上报的 DPI 缩放——MOVED 事件触发轮询，
        # 变了才发 DPI_CHANGED（R5.10）。
        self._window_scales: dict[int, float] = {}
        self._cursors: dict[Cursor, Any] = {}
        self._renderer = renderer
        self._initialized = False

    # ------------------------------------------------------------ 身份

    @property
    def name(self) -> str:
        return "sdl2"

    # ------------------------------------------------------------ 生命周期

    def initialize(self) -> None:
        if self._initialized:
            return
        self._lib = _load_sdl2()
        if self._lib.SDL_Init(_SDL_INIT_VIDEO) != 0:
            raise BackendError(f"SDL_Init 失败：{self._last_error()}")
        self._lib.SDL_GetKeyName.restype = ctypes.c_char_p
        self._lib.SDL_GetKeyName.argtypes = [ctypes.c_int32]
        self._lib.SDL_GetScancodeName.restype = ctypes.c_char_p
        self._lib.SDL_GetScancodeName.argtypes = [ctypes.c_int32]
        # 返回的是 SDL 拥有的缓冲区，必须自己 string_at 之后 SDL_free（R5.10）；
        # 声明成 c_char_p 会让 ctypes 转换后丢掉原指针，每次读剪贴板泄漏一次。
        self._lib.SDL_GetClipboardText.restype = ctypes.c_void_p
        self._lib.SDL_GetWindowID.restype = ctypes.c_uint32
        self._lib.SDL_GetWindowID.argtypes = [ctypes.c_void_p]
        self._initialized = True

    def shutdown(self) -> None:
        if not self._initialized:
            return
        for window in list(self._windows.values()):
            self._lib.SDL_DestroyWindow(window)
        self._windows.clear()
        self._by_sdl_id.clear()
        self._window_scales.clear()
        self._cursors.clear()
        self._lib.SDL_Quit()
        self._initialized = False

    # ------------------------------------------------------------ 窗口

    def create_window(self, spec: WindowSpec) -> int:
        self._require_initialized()
        flags = _SDL_WINDOW_ALLOW_HIGHDPI
        if spec.resizable:
            flags |= _SDL_WINDOW_RESIZABLE
        handle = self._lib.SDL_CreateWindow(
            spec.title.encode("utf-8"),
            _SDL_WINDOWPOS_CENTERED,
            _SDL_WINDOWPOS_CENTERED,
            int(spec.width),
            int(spec.height),
            flags,
        )
        if not handle:
            raise BackendError(f"SDL_CreateWindow 失败：{self._last_error()}")
        window_id = self._next_id
        self._next_id += 1
        self._windows[window_id] = handle
        self._by_sdl_id[int(self._lib.SDL_GetWindowID(handle))] = window_id
        self._window_scales[window_id] = self.dpi_scale(window_id)
        return window_id

    def destroy_window(self, window_id: int) -> None:
        self._require_initialized()
        handle = self._handle(window_id)
        self._by_sdl_id.pop(int(self._lib.SDL_GetWindowID(handle)), None)
        self._lib.SDL_DestroyWindow(handle)
        del self._windows[window_id]
        self._window_scales.pop(window_id, None)

    # ------------------------------------------------------------ 事件

    def pump_events(self) -> list[Event]:
        """取出当前待处理事件（不阻塞）。翻译本身是纯函数，有单测覆盖。"""
        self._require_initialized()
        buffer = ctypes.create_string_buffer(_EVENT_BUFFER_SIZE)
        events: list[Event] = []
        while self._lib.SDL_PollEvent(buffer) != 0:
            translated = self._translate(buffer)
            if translated is not None:
                events.append(translated)
        return events

    def wait_events(self, timeout_ms: float) -> list[Event]:
        """阻塞等待事件，超时返回。空闲时主循环用它省电（而非忙等）。

        R5.3：`SDL_WaitEventTimeout` 会把**唤醒它的那个事件**直接取走——
        这个事件必须进入本批翻译（先翻它，再泵干剩下的），
        否则每次唤醒稳定丢一个事件（比如用户按下的第一下键）。
        """
        self._require_initialized()
        timeout = max(0, int(timeout_ms))
        buffer = ctypes.create_string_buffer(_EVENT_BUFFER_SIZE)
        events: list[Event] = []
        if self._lib.SDL_WaitEventTimeout(buffer, timeout) != 0:
            translated = self._translate(buffer)
            if translated is not None:
                events.append(translated)
        events.extend(self.pump_events())
        return events

    def _translate(self, buffer: Any) -> Event | None:
        """把一个 SDL 事件分发给对应的纯函数翻译器，并重映射窗口 id。"""
        kind = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        event = self._translate_kind(kind, buffer)
        return self._remap_window_id(event) if event is not None else None

    def _translate_kind(self, kind: int, buffer: Any) -> Event | None:
        if kind in (_SDL_KEYDOWN, _SDL_KEYUP):
            raw = ctypes.cast(buffer, ctypes.POINTER(_KeyboardEvent)).contents
            # code ← scancode（物理键位，R5.6）；key ← keysym（布局相关）
            code_raw = self._lib.SDL_GetScancodeName(raw.keysym.scancode) or b""
            key_raw = self._lib.SDL_GetKeyName(raw.keysym.sym) or b""
            return translate_keyboard(
                raw,
                down=kind == _SDL_KEYDOWN,
                code_name=code_raw.decode("utf-8", "replace"),
                key_name=key_raw.decode("utf-8", "replace"),
            )

        if kind == _SDL_TEXTINPUT:
            return translate_text_input(
                ctypes.cast(buffer, ctypes.POINTER(_TextInputEvent)).contents
            )

        if kind == _SDL_TEXTEDITING:
            return translate_text_editing(
                ctypes.cast(buffer, ctypes.POINTER(_TextEditingEvent)).contents
            )

        if kind == _SDL_MOUSEMOTION:
            return translate_motion(ctypes.cast(buffer, ctypes.POINTER(_MouseMotionEvent)).contents)

        if kind in (_SDL_MOUSEBUTTONDOWN, _SDL_MOUSEBUTTONUP):
            button = ctypes.cast(buffer, ctypes.POINTER(_MouseButtonEvent)).contents
            return translate_button(button, down=kind == _SDL_MOUSEBUTTONDOWN)

        if kind == _SDL_MOUSEWHEEL:
            wheel = ctypes.cast(buffer, ctypes.POINTER(_MouseWheelEvent)).contents
            mouse_x, mouse_y = self._mouse_position()
            return translate_wheel(wheel, mouse_x, mouse_y)

        if kind == _SDL_WINDOWEVENT:
            window = ctypes.cast(buffer, ctypes.POINTER(_WindowEvent)).contents
            if window.event == _SDL_WINDOWEVENT_MOVED:
                # 跨屏拖动：轮询显示缩放，变了才发 DPI_CHANGED（R5.10）
                return self._track_dpi_change(int(window.window_id), float(window.timestamp))
            return translate_window_event(window)

        if kind == _SDL_QUIT:
            # 应用级退出，不属于任何窗口（window_id=0）
            timestamp = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32 * 2)).contents[1]
            return WindowEvent(kind=WindowKind.CLOSE, time_ms=float(timestamp))

        return None

    def _remap_window_id(self, event: Event) -> Event:
        """事件里带的是 SDL 的 windowID，换成我们自己的窗口 id（R5.1）。

        查不到映射（极少见：窗口销毁后滞留的事件）时置 0——
        一个"不属于任何已知窗口"的 id 比一个陌生数字更不容易被误路由。
        """
        window_id = getattr(event, "window_id", 0)
        if not window_id:
            return event
        mapped = self._by_sdl_id.get(window_id, 0)
        if mapped == window_id:
            return event
        return replace(event, window_id=mapped)

    def _track_dpi_change(self, sdl_window_id: int, time_ms: float) -> Event | None:
        """窗口移动后检查 DPI 缩放是否变化（拖到了另一块显示器）。"""
        window_id = self._by_sdl_id.get(sdl_window_id)
        if window_id is None:
            return None
        scale = self.dpi_scale(window_id)
        if scale == self._window_scales.get(window_id):
            return None
        self._window_scales[window_id] = scale
        spec_width, spec_height = self._window_size(window_id)
        # 事件里先放 SDL 的 id，由 _translate 出口统一重映射成我们的窗口 id
        return WindowEvent(
            kind=WindowKind.DPI_CHANGED,
            window_id=sdl_window_id,
            time_ms=time_ms,
            width=spec_width,
            height=spec_height,
            dpi_scale=scale,
        )

    def _mouse_position(self) -> tuple[float, float]:
        """SDL_GetMouseState —— 滚轮事件的指针位置兜底（SDL < 2.26）。"""
        x, y = ctypes.c_int32(0), ctypes.c_int32(0)
        self._lib.SDL_GetMouseState(ctypes.byref(x), ctypes.byref(y))
        return float(x.value), float(y.value)

    def _window_size(self, window_id: int) -> tuple[float, float]:
        w, h = ctypes.c_int(0), ctypes.c_int(0)
        self._lib.SDL_GetWindowSize(self._handle(window_id), ctypes.byref(w), ctypes.byref(h))
        return float(w.value), float(h.value)

    # ------------------------------------------------------------ 能力

    def dpi_scale(self, window_id: int) -> float:
        """DPI 缩放。优先用 SDL 2.24+ 的 SDL_GetWindowDisplayScale。"""
        self._require_initialized()
        handle = self._handle(window_id)
        getter = getattr(self._lib, "SDL_GetWindowDisplayScale", None)
        if getter is not None:
            return float(getter(handle))
        # SDL < 2.24 的兜底：查窗口所在显示器的 DPI 估算（R5.10）
        display_dpi = getattr(self._lib, "SDL_GetDisplayDPI", None)
        display_index = getattr(self._lib, "SDL_GetWindowDisplayIndex", None)
        if display_dpi is not None and display_index is not None:
            ddpi = ctypes.c_float(0.0)
            if (
                display_dpi(display_index(handle), ctypes.byref(ddpi), None, None) == 0
                and ddpi.value > 0
            ):
                return float(ddpi.value) / 96.0
        return 1.0

    def set_cursor(self, window_id: int, cursor: Cursor) -> None:
        self._require_initialized()
        if not self._cursors:
            self._lib.SDL_CreateSystemCursor.restype = ctypes.c_void_p
            self._lib.SDL_CreateSystemCursor.argtypes = [ctypes.c_int]
        sdl_cursor = _CURSOR_TO_SDL[cursor]
        if cursor not in self._cursors:
            self._cursors[cursor] = self._lib.SDL_CreateSystemCursor(sdl_cursor)
        self._lib.SDL_SetCursor(self._cursors[cursor])

    def clipboard_get_text(self) -> str:
        self._require_initialized()
        # restype 在 initialize() 里声明为 c_void_p：SDL 的契约要求
        # 调用方 SDL_free 返回值（R5.10）——ctypes 自动转 bytes 会丢掉
        # 原指针，每次读剪贴板泄漏一块。
        pointer = self._lib.SDL_GetClipboardText()
        if not pointer:
            return ""
        try:
            return ctypes.string_at(pointer).decode("utf-8", "replace")
        finally:
            self._lib.SDL_free(ctypes.c_void_p(pointer))

    def clipboard_set_text(self, text: str) -> None:
        self._require_initialized()
        self._lib.SDL_SetClipboardText(text.encode("utf-8"))

    def now_ms(self) -> float:
        self._require_initialized()
        return float(self._lib.SDL_GetTicks())

    def request_redraw(self, window_id: int) -> None:
        """SDL2 没有"请求重绘"的概念——下一帧由主循环驱动。"""
        self._handle(window_id)

    # ------------------------------------------------------------ 文本输入 / IME（R5.7）
    #
    # 不调 SDL_StartTextInput，SDL 就不产生 TEXTINPUT/TEXTEDITING 事件——
    # 这三个方法是"中文能输入"的物理前提，不是可选项。

    def start_text_input(self, window_id: int) -> None:
        self._require_initialized()
        self._handle(window_id)
        self._lib.SDL_StartTextInput()

    def stop_text_input(self, window_id: int) -> None:
        self._require_initialized()
        self._handle(window_id)
        self._lib.SDL_StopTextInput()

    def set_ime_rect(self, window_id: int, rect: ImeRect) -> None:
        """候选框跟随光标。SDL 的 rect 是窗口内坐标，单位与事件坐标一致。"""
        self._require_initialized()
        self._handle(window_id)
        sdl_rect = _SDLRect(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
        self._lib.SDL_SetTextInputRect(ctypes.byref(sdl_rect))

    # ------------------------------------------------------------ 呈现接缝（R5.8）

    def begin_frame(self, window_id: int) -> None:
        """开始一帧：把物理尺寸告诉呈现器（逻辑 × DPI）。

        将来 GL 后端落地时，这里也是切渲染目标 / 清屏的位置。
        """
        self._require_initialized()
        self._handle(window_id)
        if self._renderer is None:
            return
        scale = self.dpi_scale(window_id)
        width, height = self._window_size(window_id)
        self._renderer.begin_frame(width, height, scale)

    def end_frame(self, window_id: int, *, present: bool = True) -> None:
        """结束一帧。`present=True` 时上屏（GL 落地后是 SDL_GL_SwapWindow）。

        软件光栅没有 swap 链——"上屏"目前是呈现器侧的事，这里管帧边界。
        """
        self._require_initialized()
        self._handle(window_id)
        if self._renderer is not None:
            self._renderer.end_frame()
        # SDL_GL_SwapWindow 属 GL 后端落地时的事——现在没有 GL 上下文。

    # ------------------------------------------------------------ 内部

    def _last_error(self) -> str:
        getter = getattr(self._lib, "SDL_GetError", None)
        if getter is None:
            return ""
        getter.restype = ctypes.c_char_p
        value = getter()
        return value.decode("utf-8", "replace") if value else ""

    def _handle(self, window_id: int) -> Any:
        if window_id not in self._windows:
            raise BackendError(f"没有 id 为 {window_id} 的窗口")
        return self._windows[window_id]

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise BackendError("先调用 initialize()")


# ---------------------------------------------------------------- 加载


def _load_sdl2() -> Any:
    """加载 SDL2 动态库。找不到时给出带修复指引的报错。"""
    candidates = list(_LIBRARY_CANDIDATES.get(sys.platform, _DEFAULT_CANDIDATES))
    if sdl2_library_name() not in candidates:
        candidates.insert(0, sdl2_library_name())

    found = ctypes.util.find_library("SDL2")
    if found:
        candidates.insert(0, found)

    last_error: OSError | None = None
    for candidate in candidates:
        try:
            return ctypes.CDLL(candidate)
        except OSError as error:  # pragma: no cover - 取决于机器
            last_error = error

    raise BackendError(
        f"找不到 SDL2 动态库（试过：{', '.join(candidates)}）。\n"
        f"  安装方式：Windows `winget install libsdl-org.SDL2`；"
        f"macOS `brew install sdl2`；Ubuntu `sudo apt install libsdl2-2.0-0`。\n"
        f"  也可以改用 HeadlessBackend（无需 SDL2，供测试与 CI 使用）。\n"
        f"  原始错误：{last_error}"
    )


def _modifiers_from(sdl_mod: int) -> Modifiers:
    """SDL 修饰键位掩码 → Modifiers。"""
    return Modifiers(
        shift=bool(sdl_mod & 0x0001 or sdl_mod & 0x0002),
        ctrl=bool(sdl_mod & 0x0040 or sdl_mod & 0x0080),
        alt=bool(sdl_mod & 0x0100 or sdl_mod & 0x0200),
        meta=bool(sdl_mod & 0x0400 or sdl_mod & 0x0800),
    )
