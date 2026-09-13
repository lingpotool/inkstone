"""Windows 平台外观：DWM 标题栏配色 + 任务栏身份（R10，L0）。

**为什么保留原生边框、只做配色**：Win11 的 Snap Layouts、贴靠、最大化动画、
无障碍与高对比主题都是 DWM 提供的能力。自绘标题栏会把它们全部弄丢
（Chrome / VS Code 那样做，要自己实现 hit-test 与 snap）。这里走的是
Flutter / Electron 的默认路线——原生边框 + 平台化外观。

非 Windows 平台上这些函数是**安全的空操作**（返回 False），调用方无需分支：
"这条外观能力在这台机器上做不到"不等于"程序错了"。
"""

from __future__ import annotations

import ctypes
import sys

__all__ = [
    "animations_enabled",
    "apply_app_user_model_id",
    "apply_caption_theme",
    "dpi_awareness",
    "ensure_per_monitor_awareness",
    "is_supported",
]

#: SystemParametersInfo 的动作码：客户端区域动画是否开启（"辅助功能 → 视觉效果"）
_SPI_GETCLIENTAREAANIMATION = 0x1042

#: DwmSetWindowAttribute 的属性号（Windows 11 SDK）
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35

#: DPI_AWARENESS 枚举（GetAwarenessFromDpiAwarenessContext）
_DPI_AWARENESS_UNAWARE = 0
_DPI_AWARENESS_SYSTEM = 1
_DPI_AWARENESS_PER_MONITOR = 2

#: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2（伪句柄 -4）
_PER_MONITOR_AWARE_V2 = -4


def is_supported() -> bool:
    return sys.platform == "win32"


def animations_enabled() -> bool:
    """系统是否允许动画（"辅助功能 → 视觉效果 → 动画效果"）。

    查不到就当**允许**：把"读不到设置"当成"用户要求关动画"会让所有机器上的
    动效静默消失，那比多播一个淡出更糟。非 Windows 也返回 True（各平台的
    等价开关在跨平台工作流里补）。
    """
    if not is_supported():
        return True
    try:
        user32 = ctypes.WinDLL("user32")
        user32.SystemParametersInfoW.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        enabled = ctypes.c_int(1)
        ok = user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        return bool(enabled.value) if ok else True
    except OSError:  # pragma: no cover - 取决于机器
        return True


def dpi_awareness() -> int:
    """当前进程的 DPI 感知级别：0=unaware / 1=system / 2=per-monitor。

    非 Windows 返回 2（"感知，不需要特殊处理"）。

    为什么需要这个探针：**感知级别决定我们渲染的像素算不算数**。进程若是
    unaware，Windows 会把整个窗口按显示器缩放位图拉伸——渲染得再清晰，
    用户看到的也是糊的（这正是本项目实测到的"界面整体发虚"根因）。
    """
    if not is_supported():
        return _DPI_AWARENESS_PER_MONITOR
    try:
        user32 = ctypes.WinDLL("user32")
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        return int(
            user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext())
        )
    except (OSError, AttributeError):  # pragma: no cover - 老系统
        return _DPI_AWARENESS_UNAWARE


def ensure_per_monitor_awareness() -> bool:
    """把进程声明为 per-monitor-v2 DPI 感知。返回最终是否"感知"。

    **这是兜底，不是首选路径。** 正常流程是让 SDL 用
    `SDL_WINDOWS_DPI_AWARENESS=permonitorv2` 在建窗前设置；只有老 SDL 不认识
    那个 hint 时才由这里补。

    为什么不能自己抢先设（实测 SDL 2.32 / Win11）：先调本函数再 `SDL_Init`，
    **IME 的组合事件会被扣到上屏那一刻才吐出来**——输入法不出候选框，中文
    打不了；同样的 per-monitor-v2 由 SDL 自己设则正常。所以调用点放在
    `SDL_Init` 之后，且 SDL 已经设上时这里只是一次纯读取。

    已有感知声明（清单/别处设过）时直接返回 True，不重复设置；老系统
    （Win10 1703 之前）没有这个 API，退回 shcore 的 `SetProcessDpiAwareness`。
    """
    if not is_supported():
        return True
    if dpi_awareness() != _DPI_AWARENESS_UNAWARE:
        return True
    try:
        user32 = ctypes.WinDLL("user32")
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_int
        if set_context(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)):
            return True
    except (OSError, AttributeError):  # pragma: no cover - 老系统
        pass
    try:  # pragma: no cover - 只在 Win10 1703 之前走到
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return True
    except (OSError, AttributeError):
        return dpi_awareness() != _DPI_AWARENESS_UNAWARE


def apply_app_user_model_id(app_id: str) -> bool:
    """设置进程的 AppUserModelID：任务栏把窗口归到这个身份下。

    不设的话任务栏显示的是 `python.exe` 的图标与名字——专业软件不会这样。
    必须在创建窗口**之前**调用（Windows 的既有约定）。
    """
    if not is_supported():
        return False
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        return bool(shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0)
    except OSError:  # pragma: no cover - 取决于机器
        return False


def apply_caption_theme(hwnd: int, *, dark: bool, background: int | None = None) -> bool:
    """把窗口标题栏染成与主题一致：深色模式 + 标题栏底色。

    `hwnd` 来自 SDL 的 `SDL_GetWindowWMInfo`；`background` 是 `0xRRGGBB`。
    老系统（Win10 早期）不支持这两个属性时会失败——返回 False，不抛。
    """
    if not is_supported() or not hwnd:
        return False
    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        value = ctypes.c_int(1 if dark else 0)
        ok = bool(
            dwm.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                _DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            == 0
        )
        if background is not None:
            # COLORREF 是 0x00BBGGRR，不是 RGB
            red, green, blue = (
                (background >> 16) & 0xFF,
                (background >> 8) & 0xFF,
                background & 0xFF,
            )
            color = ctypes.c_uint32(red | (green << 8) | (blue << 16))
            ok = (
                bool(
                    dwm.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd),
                        _DWMWA_CAPTION_COLOR,
                        ctypes.byref(color),
                        ctypes.sizeof(color),
                    )
                    == 0
                )
                and ok
            )
        return ok
    except OSError:  # pragma: no cover - 取决于机器
        return False
