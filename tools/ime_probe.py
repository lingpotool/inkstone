"""输入法（IME）现场诊断：把这台机器上真实发生的输入事件打出来。

为什么要有这个工具：IME 是**环境相关**的（系统输入法、中英档位、窗口的
IME 上下文状态、多显示器…），"我这儿能打中文"与"你这儿打不了"之间隔着
这些状态，靠猜没有意义。这个脚本开一个最小窗口，把环境事实与收到的
每一个事件原样打印，用来把问题定位到具体一环。

    .venv/Scripts/python.exe tools/ime_probe.py

跑起来后：**点一下窗口标题为「IME 诊断」的窗口**，切到中文，随便打几个
拼音（如 zhong），再按空格选词。把控制台输出整段贴回来即可。

输出里能直接读出结论：

- `ImeEvent COMPOSE` 出现 → 组合态通了，问题在别处（渲染/焦点）；
- 只有 `KeyEvent`、没有 `ImeEvent` → 系统 IME 根本没在这个窗口组合，
  看 `layout` / `open` / `conversion` 三行判断是"输入法没开"还是"档位在英文"。
"""

from __future__ import annotations

import ctypes
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from inkstone.backend import ImeRect, WindowSpec
from inkstone.backend.sdl2 import SDL2Backend

_DURATION_S = 60.0


def _kb_layout_info() -> dict[str, object]:
    """当前键盘布局是不是 IME、叫什么名字。"""
    out: dict[str, object] = {}
    if sys.platform != "win32":
        return out
    user32 = ctypes.WinDLL("user32")
    imm32 = ctypes.WinDLL("imm32")
    hkl = user32.GetKeyboardLayout(0)
    buf = ctypes.create_unicode_buffer(64)
    user32.GetKeyboardLayoutNameW(buf)
    out["layout"] = buf.value
    imm32.ImmIsIME.restype = ctypes.c_bool
    imm32.ImmIsIME.argtypes = [ctypes.c_void_p]
    out["is_ime"] = bool(imm32.ImmIsIME(ctypes.c_void_p(hkl)))
    desc = ctypes.create_unicode_buffer(256)
    imm32.ImmGetDescriptionW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    n = imm32.ImmGetDescriptionW(ctypes.c_void_p(hkl), desc, 256)
    out["ime_name"] = desc.value if n else "(非 IME / 无描述)"
    return out


def _ime_state(hwnd: int) -> dict[str, object]:
    """该窗口的 IME 上下文：是否已关联、是否打开、转换模式（中/英）。"""
    out: dict[str, object] = {}
    if sys.platform != "win32" or not hwnd:
        return out
    imm32 = ctypes.WinDLL("imm32")
    imm32.ImmGetContext.restype = ctypes.c_void_p
    imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
    imm32.ImmGetOpenStatus.restype = ctypes.c_bool
    imm32.ImmGetOpenStatus.argtypes = [ctypes.c_void_p]
    himc = imm32.ImmGetContext(ctypes.c_void_p(hwnd))
    out["himc"] = hex(himc) if himc else "NULL"
    if not himc:
        return out
    out["open"] = bool(imm32.ImmGetOpenStatus(ctypes.c_void_p(himc)))
    conv = ctypes.c_uint32(0)
    sent = ctypes.c_uint32(0)
    imm32.ImmGetConversionStatus.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    if imm32.ImmGetConversionStatus(ctypes.c_void_p(himc), ctypes.byref(conv), ctypes.byref(sent)):
        out["conversion"] = hex(conv.value)  # bit0=IME_CMODE_NATIVE（中文）
        out["native_mode"] = bool(conv.value & 0x0001)
    return out


def main() -> int:
    backend = SDL2Backend()
    backend.initialize()
    from inkstone.backend import windows_shell

    try:
        from inkstone import __version__
    except ImportError:  # pragma: no cover
        __version__ = "?"
    lib = backend._lib

    class _V(ctypes.Structure):
        _fields_ = [
            ("major", ctypes.c_uint8),
            ("minor", ctypes.c_uint8),
            ("patch", ctypes.c_uint8),
        ]

    lib.SDL_GetVersion.argtypes = [ctypes.c_void_p]
    v = _V()
    lib.SDL_GetVersion(ctypes.byref(v))

    window = backend.create_window(WindowSpec(title="IME 诊断", width=520, height=180))
    hwnd = backend.native_window_handle(window)
    backend.set_window_theme(window, dark=False, background=0xFFFFFF)
    # 不开文本输入，SDL 根本不产生 TEXTINPUT/TEXTEDITING——这是"能不能打中文"
    # 的物理开关，诊断脚本当然也要开。
    backend.start_text_input(window)
    backend.set_ime_rect(window, ImeRect(20.0, 60.0, 2.0, 24.0))

    print("=" * 60)
    print(f"inkstone {__version__} · SDL {v.major}.{v.minor}.{v.patch}")
    print(f"DPI 感知: {windows_shell.dpi_awareness()} (2=per-monitor)")
    print(f"窗口逻辑尺寸: {backend.window_size(window)} · 缩放 {backend.dpi_scale(window)}")
    for key, value in _kb_layout_info().items():
        print(f"布局 {key}: {value}")
    print("-" * 60)
    print("请点一下这个窗口，切到中文，输入拼音（如 zhong）+ 空格选词。")

    started = time.time()
    last_state = None
    reported = 0
    while time.time() - started < _DURATION_S:
        state = _ime_state(hwnd)
        if state != last_state:
            print(f"[状态] {state}")
            last_state = state
        for event in backend.wait_events(200.0):
            name = type(event).__name__
            if name == "KeyEvent":
                print(f"[按键] code={event.code} key={event.key!r} text={event.text!r}")
            elif name == "TextEvent":
                print(f"[上屏] {event.text!r}")
            elif name == "ImeEvent":
                print(f"[组合] {event.kind.value} text={event.text!r}")
            elif name == "FocusEvent":
                print(f"[焦点] focused={event.focused}")
                backend.set_ime_rect(window, ImeRect(20.0, 60.0, 2.0, 24.0))
        reported += 1
    backend.destroy_window(window)
    backend.shutdown()
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
