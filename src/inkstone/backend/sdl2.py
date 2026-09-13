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
import importlib
import os
import sys
import warnings
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

__all__ = [
    "SDL2Backend",
    "load_sdl2",
    "normalize_key_name",
    "normalize_key_value",
    "sdl2_library_name",
]

# ---------------------------------------------------------------- 常量

_SDL_INIT_VIDEO = 0x00000020

_SDL_WINDOW_RESIZABLE = 0x00000020
_SDL_WINDOW_OPENGL = 0x00000002
_SDL_WINDOW_ALLOW_HIGHDPI = 0x00002000

# SDL_GL_SetAttribute 的属性号
_SDL_GL_DOUBLEBUFFER = 5
_SDL_GL_RED_SIZE = 0
_SDL_GL_GREEN_SIZE = 1
_SDL_GL_BLUE_SIZE = 2
_SDL_GL_ALPHA_SIZE = 3
_SDL_GL_DEPTH_SIZE = 6

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


class _SDL_version(ctypes.Structure):
    """SDL_version：三个 uint8。`SDL_GetWindowWMInfo` 要求调用方填好版本号。"""

    _fields_ = [
        ("major", ctypes.c_uint8),
        ("minor", ctypes.c_uint8),
        ("patch", ctypes.c_uint8),
    ]


class _SysWMinfoWin(ctypes.Structure):
    """`SDL_SysWMinfo.info.win`：句柄、设备上下文、实例句柄。"""

    _fields_ = [
        ("window", ctypes.c_void_p),
        ("hdc", ctypes.c_void_p),
        ("hinstance", ctypes.c_void_p),
    ]


class _SysWMinfo(ctypes.Structure):
    """`SDL_SysWMinfo` 的 Windows 布局：version + subsystem + info union。

    SDL 会**整段写入**这个结构（union 按最大成员算大小），所以按"只声明
    Windows 需要的字段"来定义会写入越界、踩坏调用方的栈。这里照实声明
    Win32 成员，并留出足够余量：多要一点内存换来的是不会内存越界。

    取不到（非 Windows / SDL 版本不匹配）时 `SDL_GetWindowWMInfo` 返回 0，
    上层拿到 0 并跳过平台外观调用。
    """

    _fields_ = [
        ("version", _SDL_version),
        ("subsystem", ctypes.c_uint32),
        ("win", _SysWMinfoWin),
        # 余量：不同 SDL 构建的 union 可能有更大的成员（WinRT 等）。
        ("_reserve", ctypes.c_byte * 64),
    ]


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

    @property
    def library(self) -> Any:
        """已加载的 SDL2 动态库（GL 上下文集成需要它，见 `gl_wgl.sdl_gl_driver`）。"""
        return self._lib

    def native_window(self, window_id: int) -> Any:
        """窗口的 SDL 句柄。给 GL 上下文创建用——平台句柄只该在 L0 流动。"""
        self._require_initialized()
        return self._handle(window_id)

    # ------------------------------------------------------------ 窗口能力（R10）

    def set_app_identity(self, app_id: str) -> None:
        """任务栏身份。Windows 上必须在建窗之前设，否则会先以解释器身份注册。"""
        if sys.platform == "win32":
            from . import windows_shell

            windows_shell.apply_app_user_model_id(app_id)

    def set_title(self, window_id: int, title: str) -> None:
        self._require_initialized()
        self._lib.SDL_SetWindowTitle(self._handle(window_id), title.encode("utf-8"))

    def set_min_size(self, window_id: int, width: float, height: float) -> None:
        """最小尺寸按**逻辑像素**收，转成窗口坐标单位交给系统（见 window_size）。"""
        self._require_initialized()
        scale = self._window_unit_scale(window_id)
        self._lib.SDL_SetWindowMinimumSize(
            self._handle(window_id), max(1, round(width * scale)), max(1, round(height * scale))
        )

    def set_icon(self, window_id: int, width: int, height: int, rgba: bytes) -> None:
        """把应用外壳画好的 RGBA 交给 SDL（`SDL_SetWindowIcon` 会拷贝）。

        用 `SDL_CreateRGBSurfaceFrom` 包住这段像素，调用完立刻释放——
        缓冲区是 Python 的，`SDL_FreeSurface` 不会去 free 它（From 系列不接管内存）。
        """
        self._require_initialized()
        if len(rgba) != width * height * 4:
            raise BackendError(f"图标像素长度应为 {width * height * 4}，收到 {len(rgba)}")
        buf = (ctypes.c_ubyte * len(rgba)).from_buffer_copy(rgba)
        # 小端机器上内存序即 R,G,B,A；SDL 的 mask 描述的是整数的位段。
        surface = self._lib.SDL_CreateRGBSurfaceFrom(
            ctypes.byref(buf),
            width,
            height,
            32,
            width * 4,
            0x000000FF,
            0x0000FF00,
            0x00FF0000,
            0xFF000000,
        )
        if not surface:
            return
        try:
            self._lib.SDL_SetWindowIcon(self._handle(window_id), surface)
        finally:
            self._lib.SDL_FreeSurface(surface)

    def set_maximized(self, window_id: int, maximized: bool) -> None:
        self._require_initialized()
        handle = self._handle(window_id)
        if maximized:
            self._lib.SDL_MaximizeWindow(handle)
        else:
            self._lib.SDL_RestoreWindow(handle)

    def set_fullscreen(self, window_id: int, enabled: bool) -> None:
        self._require_initialized()
        # SDL_WINDOW_FULLSCREEN_DESKTOP：无模式切换，比真改分辨率更适合桌面 App
        flag = 0x00001001 if enabled else 0
        self._lib.SDL_SetWindowFullscreen(self._handle(window_id), flag)

    def set_window_theme(
        self, window_id: int, *, dark: bool, background: int | None = None
    ) -> None:
        """把主题告诉窗口系统：Windows 上染 DWM 标题栏（原生边框保留）。"""
        if sys.platform != "win32":
            return
        from . import windows_shell

        windows_shell.apply_caption_theme(
            self.native_window_handle(window_id), dark=dark, background=background
        )

    def native_window_handle(self, window_id: int) -> int:
        """平台原生窗口句柄（Windows 上是 HWND；其他平台返回 0）。

        只有需要调用平台外观 API（DWM）时才用它——句柄不往 L0 之外流。
        """
        if sys.platform != "win32":
            return 0
        info = _SysWMinfo()
        self._lib.SDL_GetVersion(ctypes.byref(info.version))
        if not self._lib.SDL_GetWindowWMInfo(self._handle(window_id), ctypes.byref(info)):
            return 0
        return int(info.win.window or 0)

    # ------------------------------------------------------------ 生命周期

    def initialize(self) -> None:
        if self._initialized:
            return
        self._lib = load_sdl2()
        self._bind_signatures()
        # DPI 感知必须在**任何窗口之前**声明（SDL_Init 会建隐藏辅助窗口）。
        # 不声明的话 Windows 会把整窗按显示器缩放位图拉伸——渲染得再清晰，
        # 用户看到的也是糊的（R11.1）。
        #
        # **交给 SDL 设，不要自己先调 Win32**：实测（SDL 2.32 / Win11）先调
        # `SetProcessDpiAwarenessContext` 再 SDL_Init，IME 组合事件会被扣住，
        # 直到上屏才一次性吐出来——输入法没有候选框，中文等于打不了。
        # 同样的 per-monitor-v2 由 SDL 的 hint 设置则一切正常（A/B 实测）。
        if sys.platform == "win32":
            self._lib.SDL_SetHint(b"SDL_WINDOWS_DPI_AWARENESS", b"permonitorv2")
            # 我们自己按 dpi_scale 缩放坐标（ADR-0015），不要 SDL 再缩一遍。
            self._lib.SDL_SetHint(b"SDL_WINDOWS_DPI_SCALING", b"0")
        if self._lib.SDL_Init(_SDL_INIT_VIDEO) != 0:
            raise BackendError(f"SDL_Init 失败：{self._last_error()}")
        # 老 SDL 不认识上面那个 hint 时兜底（对 IME 有副作用，所以只在真没设上
        # 时才动手；SDL 设上了这里就是一个纯读取）。
        if sys.platform == "win32":
            from . import windows_shell

            windows_shell.ensure_per_monitor_awareness()
        self._initialized = True

    def _bind_signatures(self) -> None:
        """集中声明 ctypes 签名。

        **指针返回值必须显式声明 `restype`**：ctypes 默认按 `c_int` 处理返回值，
        在 64 位平台会把 `SDL_Window*` 截断成 32 位——句柄作废，
        再传给 `SDL_GetWindowID` 就是一次访问违例（R8.5 真机验证时撞到的）。
        声明集中在这里，避免"哪个函数忘了声明"散落各处。
        """
        lib = self._lib
        c, p = ctypes, ctypes.POINTER
        lib.SDL_Init.restype = c.c_int
        lib.SDL_Init.argtypes = [c.c_uint32]
        lib.SDL_Quit.restype = None
        lib.SDL_CreateWindow.restype = c.c_void_p
        lib.SDL_CreateWindow.argtypes = [c.c_char_p, c.c_int, c.c_int, c.c_int, c.c_int, c.c_uint32]
        lib.SDL_DestroyWindow.argtypes = [c.c_void_p]
        lib.SDL_GetWindowID.restype = c.c_uint32
        lib.SDL_GetWindowID.argtypes = [c.c_void_p]
        lib.SDL_GetWindowSize.argtypes = [c.c_void_p, p(c.c_int), p(c.c_int)]
        lib.SDL_SetHint.restype = c.c_int
        lib.SDL_SetHint.argtypes = [c.c_char_p, c.c_char_p]
        # 窗口能力（R10）
        lib.SDL_SetWindowTitle.argtypes = [c.c_void_p, c.c_char_p]
        lib.SDL_SetWindowMinimumSize.argtypes = [c.c_void_p, c.c_int, c.c_int]
        lib.SDL_MaximizeWindow.argtypes = [c.c_void_p]
        lib.SDL_RestoreWindow.argtypes = [c.c_void_p]
        lib.SDL_SetWindowFullscreen.restype = c.c_int
        lib.SDL_SetWindowFullscreen.argtypes = [c.c_void_p, c.c_uint32]
        lib.SDL_GetVersion.argtypes = [c.c_void_p]
        lib.SDL_GetWindowWMInfo.restype = c.c_int
        lib.SDL_GetWindowWMInfo.argtypes = [c.c_void_p, c.c_void_p]
        lib.SDL_SetWindowIcon.argtypes = [c.c_void_p, c.c_void_p]
        lib.SDL_CreateRGBSurfaceFrom.restype = c.c_void_p
        lib.SDL_CreateRGBSurfaceFrom.argtypes = [
            c.c_void_p,
            c.c_int,
            c.c_int,
            c.c_int,
            c.c_int,
            c.c_uint32,
            c.c_uint32,
            c.c_uint32,
            c.c_uint32,
        ]
        lib.SDL_FreeSurface.argtypes = [c.c_void_p]
        # DPI：SDL 2.24+ 的 scale 返回 float（不声明 restype 会被当 int 解读）；
        # 老版本兜底走 display index + display DPI。这三个按版本可选，缺失就跳过。
        for name, restype, argtypes in (
            ("SDL_GetWindowDisplayScale", c.c_float, [c.c_void_p]),
            ("SDL_GetWindowDisplayIndex", c.c_int, [c.c_void_p]),
            ("SDL_GetDisplayDPI", c.c_int, [c.c_int, p(c.c_float), p(c.c_float), p(c.c_float)]),
            # GL 上屏（R8.6）：上下文创建/切换/换链 + 取函数地址。
            # SDL_GL_GetProcAddress 返回函数指针——同样是**必须声明 restype**
            # 的那类（默认 c_int 会在 64 位截断）。
            ("SDL_GL_SetAttribute", c.c_int, [c.c_int, c.c_int]),
            ("SDL_GL_CreateContext", c.c_void_p, [c.c_void_p]),
            ("SDL_GL_MakeCurrent", c.c_int, [c.c_void_p, c.c_void_p]),
            ("SDL_GL_GetProcAddress", c.c_void_p, [c.c_char_p]),
            ("SDL_GL_SwapWindow", None, [c.c_void_p]),
            ("SDL_GL_DeleteContext", None, [c.c_void_p]),
        ):
            function = getattr(lib, name, None)
            if function is None:
                continue
            function.restype = restype
            function.argtypes = argtypes
        lib.SDL_PollEvent.restype = c.c_int
        lib.SDL_PollEvent.argtypes = [c.c_void_p]
        lib.SDL_WaitEventTimeout.restype = c.c_int
        lib.SDL_WaitEventTimeout.argtypes = [c.c_void_p, c.c_int]
        lib.SDL_GetTicks.restype = c.c_uint32
        lib.SDL_GetMouseState.restype = c.c_uint32
        lib.SDL_GetMouseState.argtypes = [p(c.c_int), p(c.c_int)]
        lib.SDL_CreateSystemCursor.restype = c.c_void_p
        lib.SDL_CreateSystemCursor.argtypes = [c.c_int]
        lib.SDL_SetCursor.argtypes = [c.c_void_p]
        lib.SDL_StartTextInput.restype = None
        lib.SDL_StopTextInput.restype = None
        lib.SDL_SetTextInputRect.argtypes = [c.c_void_p]
        lib.SDL_SetClipboardText.restype = c.c_int
        lib.SDL_SetClipboardText.argtypes = [c.c_char_p]
        # 返回的是 SDL 拥有的缓冲区，必须自己 string_at 之后 SDL_free（R5.10）；
        # 声明成 c_char_p 会让 ctypes 转换后丢掉原指针，每次读剪贴板泄漏一次。
        lib.SDL_GetClipboardText.restype = c.c_void_p
        lib.SDL_free.argtypes = [c.c_void_p]
        lib.SDL_GetKeyName.restype = c.c_char_p
        lib.SDL_GetKeyName.argtypes = [c.c_int32]
        lib.SDL_GetScancodeName.restype = c.c_char_p
        lib.SDL_GetScancodeName.argtypes = [c.c_int32]

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
        if spec.opengl:
            # GL 上屏：先声明想要的像素格式（8/8/8/8 + 双缓冲），再带 OPENGL 建窗。
            # 属性要在建窗**之前**设，否则 SDL 会用默认格式建好上下文。
            for attribute, value in (
                (_SDL_GL_RED_SIZE, 8),
                (_SDL_GL_GREEN_SIZE, 8),
                (_SDL_GL_BLUE_SIZE, 8),
                (_SDL_GL_ALPHA_SIZE, 8),
                (_SDL_GL_DEPTH_SIZE, 0),
                (_SDL_GL_DOUBLEBUFFER, 1),
            ):
                self._lib.SDL_GL_SetAttribute(attribute, value)
            flags |= _SDL_WINDOW_OPENGL
        # 规范里的尺寸是**逻辑像素**；Windows 上要换成物理像素，
        # 否则 125% 屏上开出来的窗口会小一圈（其他平台本就是逻辑单位）。
        scale = self._window_unit_scale(None)
        handle = self._lib.SDL_CreateWindow(
            spec.title.encode("utf-8"),
            _SDL_WINDOWPOS_CENTERED,
            _SDL_WINDOWPOS_CENTERED,
            max(1, round(spec.width * scale)),
            max(1, round(spec.height * scale)),
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
        """把一个 SDL 事件分发给对应的纯函数翻译器，并重映射窗口 id / 坐标单位。"""
        kind = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        event = self._translate_kind(kind, buffer)
        if event is None:
            return None
        return self._to_logical(self._remap_window_id(event))

    def _to_logical(self, event: Event) -> Event:
        """把事件里的**窗口坐标**换成逻辑像素。

        SDL 的鼠标/窗口尺寸在 Windows 感知进程里是物理像素，而 `events/` 的
        命中测试、手势阈值、布局都在逻辑像素上（`PointerEvent` 的文档也是这么
        承诺的）。不换算的话 125% 屏上点击位置整体偏 25%——用户实测反馈的
        "点按钮位置偏移"就是这里（R11.1）。
        滚轮增量不换：它是滚动量（逻辑距离），不是窗口坐标。
        """
        window_id = getattr(event, "window_id", 0)
        if not window_id:
            return event
        scale = self._window_scales.get(window_id, 1.0)
        if scale == 1.0 or scale <= 0.0:
            return event
        if isinstance(event, PointerEvent):
            return replace(event, x=event.x / scale, y=event.y / scale)
        if isinstance(event, WindowEvent) and event.kind is WindowKind.RESIZED:
            return replace(event, width=event.width / scale, height=event.height / scale)
        return event

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
        spec_width, spec_height = self.window_size(window_id)
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

    def _physical_window_size(self, window_id: int) -> tuple[float, float]:
        """`SDL_GetWindowSize` 的原始值——Windows 上是**物理像素**，别直接给上层。"""
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
        display_index = getattr(self._lib, "SDL_GetWindowDisplayIndex", None)
        if display_index is not None:
            return self._display_scale(int(display_index(handle)))
        return 1.0

    def _display_scale(self, display_index: int) -> float:
        """某块显示器的缩放（1.0 / 1.25 / 1.5 …）。拿不到就按 1.0。

        建窗**之前**也要用（把逻辑尺寸换成物理像素），所以不能依赖窗口句柄。
        """
        display_dpi = getattr(self._lib, "SDL_GetDisplayDPI", None)
        if display_dpi is None:
            return 1.0
        ddpi = ctypes.c_float(0.0)
        if display_dpi(display_index, ctypes.byref(ddpi), None, None) == 0 and ddpi.value > 0:
            return float(ddpi.value) / 96.0
        return 1.0

    def _window_unit_scale(self, window_id: int | None = None) -> float:
        """逻辑像素 → SDL 窗口坐标单位的系数。

        Windows 上感知进程的窗口坐标是**物理像素**，所以要乘/除 DPI 缩放；
        macOS 的 `SDL_GetWindowSize` 本身就是点（逻辑），X11 上 SDL 不做缩放
        ——这两处系数是 1.0。把这层差异收在后端内部，上层永远只见逻辑像素
        （与 ADR-0015 同一条纪律：平台口径不出 L0）。
        """
        if sys.platform != "win32":
            return 1.0
        if window_id is None:
            return self._display_scale(0)
        scale = self.dpi_scale(window_id)
        return scale if scale > 0.0 else 1.0

    def window_size(self, window_id: int) -> tuple[float, float]:
        """窗口客户区的**逻辑**尺寸（后端负责物理→逻辑换算）。

        上层（应用外壳）只需要"我能画多大"，不需要知道这台显示器缩放到几倍。
        """
        self._require_initialized()
        width, height = self._physical_window_size(window_id)
        scale = self._window_unit_scale(window_id)
        return (width / scale, height / scale)

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
        """候选框跟随光标。`rect` 是**逻辑像素**（与布局同一口径）。

        Windows 上要换成物理像素——不换算的话 125% 屏上候选框会偏到光标
        左上方，用户会以为"输入法坏了"。
        """
        self._require_initialized()
        self._handle(window_id)
        scale = self._window_unit_scale(window_id)
        sdl_rect = _SDLRect(
            round(rect.x * scale),
            round(rect.y * scale),
            round(rect.width * scale),
            round(rect.height * scale),
        )
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
        # 呈现器要的是**逻辑**尺寸 + 缩放因子（RasterFrameRenderer 的契约），
        # 它自己算物理像素；这里给物理值会让 125% 屏上被放大两次（R11.1）。
        width, height = self.window_size(window_id)
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


def _library_candidates() -> list[str]:
    """系统库候选名（按平台），`find_library` 的结果插到最前。"""
    names = list(_LIBRARY_CANDIDATES.get(sys.platform, _DEFAULT_CANDIDATES))
    preferred = sdl2_library_name()
    if preferred not in names:
        names.insert(0, preferred)
    found = ctypes.util.find_library("SDL2")
    if found and found not in names:
        names.insert(0, found)
    return names


def load_sdl2() -> Any:
    """按**优先级**加载 SDL2 动态库；全失败时抛带修复指引的 `BackendError`。

    跨平台获取二进制不该靠"往仓库里塞 DLL"。这里是三层策略（R8.5）：

    1. `INKSTONE_SDL2` 环境变量显式指定路径（打包/调试/私有部署用）；
    2. **可选依赖** `inkstone[sdl2]`（`pysdl2-dll` 提供三平台预编译二进制，
       `pysdl2` 负责按平台定位：Windows 的 `SDL2.dll`、macOS 的
       `libSDL2-2.0.0.dylib`、Linux 的 `libSDL2-2.0.so.0`）；
    3. 系统已装的 SDL2（`winget` / `brew` / `apt`）。

    顺序即策略：显式覆盖 > 声明式依赖 > 系统库。核心包仍是**零依赖**——
    SDL2 只在开真窗口时需要，测试/CI/黄金图/基准全走无头后端。
    """
    errors: list[str] = []

    override = os.environ.get("INKSTONE_SDL2")
    if override:
        try:
            return ctypes.CDLL(override)
        except OSError as error:
            errors.append(f"INKSTONE_SDL2={override}: {error}")

    from_package = _load_from_pysdl2(errors)
    if from_package is not None:
        return from_package

    names = _library_candidates()
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError as error:
            errors.append(f"{name}: {error}")

    raise BackendError(
        "找不到 SDL2 动态库。按优先级试过：\n"
        "  1) 环境变量 INKSTONE_SDL2（未设置或无效）\n"
        "  2) 可选依赖包 pysdl2-dll（未安装或加载失败）\n"
        f"  3) 系统库（试过：{', '.join(names)}）\n"
        '推荐：pip install "inkstone[sdl2]"（= pysdl2 + pysdl2-dll，'
        "自带 Windows/macOS/Linux 预编译二进制，不需编译器）。\n"
        "  或系统安装：Windows `winget install libsdl-org.SDL2`；"
        "macOS `brew install sdl2`；Ubuntu `sudo apt install libsdl2-2.0-0`。\n"
        "  测试与 CI 请用 HeadlessBackend（无需 SDL2）。\n"
        "  详细错误：\n  " + "\n  ".join(errors)
    )


def _load_from_pysdl2(errors: list[str]) -> Any | None:
    """从可选依赖 `pysdl2` 的加载器拿库对象（它已按平台找到正确的二进制）。

    `sdl2.dll` 在导入时就会尝试加载二进制并可能发 UserWarning；这里吞掉那条
    警告——它不是给最终用户看的，加载失败会走我们自己的报错。
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # 用 importlib 而不是 import 语句：pysdl2 是可选依赖且没有类型存根，
            # 写死 import 会让"没装这个 extra"的环境连 mypy 都过不去。
            sdl2_dll = importlib.import_module("sdl2.dll")
    except Exception as error:  # pragma: no cover - 取决于环境
        errors.append(f"pysdl2: {type(error).__name__}: {error}")
        return None
    # pysdl2 的加载器对象不是可直接调用的 CDLL（它只暴露加载 API），
    # 但能给出**已按平台定位到的二进制路径**——拿路径自己 CDLL，
    # 我们的绑定方式与错误处理保持不变。
    get_path = getattr(sdl2_dll, "get_dll_file", None)
    if not callable(get_path):  # pragma: no cover - 上游结构变化时才会走到
        errors.append("pysdl2: 加载器没有 get_dll_file()")
        return None
    path = get_path()
    try:
        return ctypes.CDLL(path)
    except OSError as error:  # pragma: no cover - 取决于环境
        errors.append(f"pysdl2 路径 {path}: {error}")
        return None


def _modifiers_from(sdl_mod: int) -> Modifiers:
    """SDL 修饰键位掩码 → Modifiers。"""
    return Modifiers(
        shift=bool(sdl_mod & 0x0001 or sdl_mod & 0x0002),
        ctrl=bool(sdl_mod & 0x0040 or sdl_mod & 0x0080),
        alt=bool(sdl_mod & 0x0100 or sdl_mod & 0x0200),
        meta=bool(sdl_mod & 0x0400 or sdl_mod & 0x0800),
    )
