"""SDL2 后端 —— 生产用的主后端。

选 SDL2 而不是 GLFW 的理由（docs/03 已经论证过）：原生 Wayland 支持、
完整的 IME 组合事件、三平台一致的窗口与输入模型。

**关于测试覆盖，说清楚**：这个模块的大部分（创建窗口、轮询事件）
需要真实窗口与输入设备，CI 里跑不了。所以：

- 能在无环境下测的部分（键名归一化、SDL2 缺失时的报错）**拆成了纯函数并测了**
- 需要真窗口的部分标注了 `# 未在 CI 覆盖`，且失败时报的错都带修复指引

宁可写明"这块没测"，也不要假装它被测过。

状态：已实现（事件解码部分未在 CI 覆盖，需真窗口验证）。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Any

from .base import (
    BackendError,
    Cursor,
    Event,
    FocusEvent,
    ImeEvent,
    ImeKind,
    KeyEvent,
    KeyKind,
    Modifiers,
    PointerEvent,
    PointerKind,
    WindowEvent,
    WindowKind,
    WindowSpec,
)

__all__ = ["SDL2Backend", "normalize_key_name", "sdl2_library_name"]

# ---------------------------------------------------------------- 常量

_SDL_INIT_VIDEO = 0x00000020

_SDL_WINDOW_RESIZABLE = 0x00000020
_SDL_WINDOW_ALLOW_HIGHDPI = 0x00002000

_SDL_QUIT = 0x100

_SDL_WINDOWEVENT = 0x200
_SDL_WINDOWEVENT_RESIZED = 5
_SDL_WINDOWEVENT_EXPOSED = 3
_SDL_WINDOWEVENT_CLOSE = 2  # 仅作记录，关闭一般走 SDL_QUIT

_SDL_KEYDOWN = 0x300
_SDL_KEYUP = 0x301
_SDL_TEXTEDITING = 0x302
_SDL_TEXTINPUT = 0x303

_SDL_MOUSEMOTION = 0x400
_SDL_MOUSEBUTTONDOWN = 0x401
_SDL_MOUSEBUTTONUP = 0x402
_SDL_MOUSEWHEEL = 0x403

_SDL_WINDOW_INPUT_FOCUS = 1  # SDL_HINT 之外：焦点靠 SDL_WINDOWEVENT 的 FOCUS_GAINED
_SDL_WINDOWEVENT_FOCUS_GAINED = 12
_SDL_WINDOWEVENT_FOCUS_LOST = 13

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


_KEY_NAME_TO_CODE = {
    "Escape": "Escape",
    "Return": "Enter",
    "Keypad Enter": "Enter",
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


def normalize_key_name(name: str) -> str:
    """SDL 的按键名 → W3C 风格的稳定键码。

    这是纯函数，因此在无 SDL2 的环境下也能测——
    键码归一化写错了很难发现，所以单独拎出来覆盖。
    """
    if not name:
        return "Unidentified"
    if name in _KEY_NAME_TO_CODE:
        return _KEY_NAME_TO_CODE[name]
    if len(name) == 1:
        if name.isalpha():
            return "Key" + name.upper()
        if name.isdigit():
            return "Digit" + name
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
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint32),
        ("window_id", ctypes.c_uint32),
        ("which", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("direction", ctypes.c_uint32),
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


# ---------------------------------------------------------------- 后端


class SDL2Backend:
    """SDL2 后端。构造时不会加载库，`initialize()` 才加载——导入保持安全。"""

    def __init__(self) -> None:
        self._lib: Any = None
        self._windows: dict[int, Any] = {}
        self._next_id = 1
        self._cursors: dict[Cursor, Any] = {}
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
        self._initialized = True

    def shutdown(self) -> None:
        if not self._initialized:
            return
        for window in list(self._windows.values()):
            self._lib.SDL_DestroyWindow(window)
        self._windows.clear()
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
            -1,  # SDL_WINDOWPOS_CENTERED 的简化写法：由 SDL 决定
            -1,
            int(spec.width),
            int(spec.height),
            flags,
        )
        if not handle:
            raise BackendError(f"SDL_CreateWindow 失败：{self._last_error()}")
        window_id = self._next_id
        self._next_id += 1
        self._windows[window_id] = handle
        return window_id

    def destroy_window(self, window_id: int) -> None:
        self._require_initialized()
        handle = self._handle(window_id)
        self._lib.SDL_DestroyWindow(handle)
        del self._windows[window_id]

    # ------------------------------------------------------------ 事件

    def pump_events(self) -> list[Event]:
        """取出当前待处理事件（不阻塞）。未在 CI 覆盖：需要真窗口。"""
        self._require_initialized()
        buffer = ctypes.create_string_buffer(_EVENT_BUFFER_SIZE)
        events: list[Event] = []
        while self._lib.SDL_PollEvent(buffer) != 0:
            translated = self._translate(buffer)
            if translated is not None:
                events.append(translated)
        return events

    def wait_events(self, timeout_ms: float) -> list[Event]:
        """阻塞等待事件，超时返回。空闲时主循环用它省电（而非忙等）。"""
        self._require_initialized()
        timeout = max(0, int(timeout_ms))
        self._lib.SDL_WaitEventTimeout(ctypes.create_string_buffer(_EVENT_BUFFER_SIZE), timeout)
        return self.pump_events()

    def _translate(self, buffer: Any) -> Event | None:
        """把一个 SDL 事件翻译成归一化事件。未在 CI 覆盖。"""
        kind = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        now = float(self._lib.SDL_GetTicks())

        if kind in (_SDL_KEYDOWN, _SDL_KEYUP):
            raw = ctypes.cast(buffer, ctypes.POINTER(_KeyboardEvent)).contents
            name = self._lib.SDL_GetKeyName(raw.keysym.sym) or b""
            return KeyEvent(
                kind=KeyKind.DOWN if kind == _SDL_KEYDOWN else KeyKind.UP,
                code=normalize_key_name(name.decode("utf-8", "replace")),
                time_ms=now,
                repeat=bool(raw.repeat),
                modifiers=_modifiers_from(raw.keysym.mod),
            )

        if kind == _SDL_TEXTINPUT:
            text_input = ctypes.cast(buffer, ctypes.POINTER(_TextInputEvent)).contents
            return ImeEvent(
                kind=ImeKind.COMMIT,
                text=text_input.text.decode("utf-8", "replace"),
                time_ms=now,
            )

        if kind == _SDL_TEXTEDITING:
            editing = ctypes.cast(buffer, ctypes.POINTER(_TextEditingEvent)).contents
            return ImeEvent(
                kind=ImeKind.COMPOSE,
                text=editing.text.decode("utf-8", "replace"),
                time_ms=now,
                cursor_start=editing.start,
                cursor_end=editing.start + editing.length,
            )

        if kind == _SDL_MOUSEMOTION:
            motion = ctypes.cast(buffer, ctypes.POINTER(_MouseMotionEvent)).contents
            return PointerEvent(kind=PointerKind.MOVE, x=motion.x, y=motion.y, time_ms=now)

        if kind in (_SDL_MOUSEBUTTONDOWN, _SDL_MOUSEBUTTONUP):
            button = ctypes.cast(buffer, ctypes.POINTER(_MouseButtonEvent)).contents
            pointer_kind = PointerKind.DOWN if kind == _SDL_MOUSEBUTTONDOWN else PointerKind.UP
            return PointerEvent(
                kind=pointer_kind, x=button.x, y=button.y, button=button.button, time_ms=now
            )

        if kind == _SDL_MOUSEWHEEL:
            wheel = ctypes.cast(buffer, ctypes.POINTER(_MouseWheelEvent)).contents
            return PointerEvent(
                kind=PointerKind.WHEEL,
                x=0.0,
                y=0.0,
                wheel_dx=float(wheel.x),
                wheel_dy=float(wheel.y),
                time_ms=now,
            )

        if kind == _SDL_WINDOWEVENT:
            window_event = ctypes.cast(buffer, ctypes.POINTER(_WindowEvent)).contents
            return self._translate_window_event(window_event, now)

        if kind == _SDL_QUIT:
            return WindowEvent(kind=WindowKind.CLOSE, time_ms=now)

        return None

    def _translate_window_event(self, raw: Any, now: float) -> Event | None:
        if raw.event == _SDL_WINDOWEVENT_RESIZED:
            return WindowEvent(
                kind=WindowKind.RESIZED,
                time_ms=now,
                width=float(raw.data1),
                height=float(raw.data2),
            )
        if raw.event == _SDL_WINDOWEVENT_EXPOSED:
            return WindowEvent(kind=WindowKind.EXPOSED, time_ms=now)
        if raw.event == _SDL_WINDOWEVENT_FOCUS_GAINED:
            return FocusEvent(focused=True, time_ms=now)
        if raw.event == _SDL_WINDOWEVENT_FOCUS_LOST:
            return FocusEvent(focused=False, time_ms=now)
        return None

    # ------------------------------------------------------------ 能力

    def dpi_scale(self, window_id: int) -> float:
        """DPI 缩放。优先用 SDL 2.24+ 的 SDL_GetWindowDisplayScale。"""
        self._require_initialized()
        handle = self._handle(window_id)
        getter = getattr(self._lib, "SDL_GetWindowDisplayScale", None)
        if getter is not None:
            return float(getter(handle))
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
        self._lib.SDL_GetClipboardText.restype = ctypes.c_char_p
        value = self._lib.SDL_GetClipboardText()
        return value.decode("utf-8", "replace") if value else ""

    def clipboard_set_text(self, text: str) -> None:
        self._require_initialized()
        self._lib.SDL_SetClipboardText(text.encode("utf-8"))

    def now_ms(self) -> float:
        self._require_initialized()
        return float(self._lib.SDL_GetTicks())

    def request_redraw(self, window_id: int) -> None:
        """SDL2 没有"请求重绘"的概念——下一帧由主循环驱动。"""
        self._handle(window_id)

    # ------------------------------------------------------------ 内部

    def _load_error(self) -> str:
        return self._last_error()

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

    # 下面这个属性只为让 `SDL2Backend` 与 Backend 协议的结构保持一致
    _window_input_focus = _SDL_WINDOW_INPUT_FOCUS


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
