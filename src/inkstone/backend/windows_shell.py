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

__all__ = ["apply_app_user_model_id", "apply_caption_theme", "is_supported"]

#: DwmSetWindowAttribute 的属性号（Windows 11 SDK）
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35


def is_supported() -> bool:
    return sys.platform == "win32"


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
