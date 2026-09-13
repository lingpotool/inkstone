"""窗口身份与外观能力（R10）。

三块断言：

1. **无头后端如实记录**——标题、最小尺寸、最大化/全屏、标题栏主题、应用身份。
   应用外壳的行为因此在 CI 里可断言，不需要真窗口。
2. **平台外观函数永不抛异常**——"这条能力在这台机器上做不到"必须表现为
   返回 False，而不是让上层去 catch。"只有后端碰平台"靠的就是这层包边。
3. **真机冒烟**——装了 SDL2 才跑：这些调用落到真实窗口上不能崩；CI 上 skip。
"""

from __future__ import annotations

import sys

import pytest

from inkstone.backend import BackendError, HeadlessBackend
from inkstone.backend.base import WindowSpec
from inkstone.backend.windows_shell import (
    apply_app_user_model_id,
    apply_caption_theme,
    dpi_awareness,
    ensure_per_monitor_awareness,
    is_supported,
)


@pytest.fixture
def backend() -> HeadlessBackend:
    made = HeadlessBackend()
    made.initialize()
    return made


@pytest.fixture
def window(backend: HeadlessBackend) -> int:
    return backend.create_window(WindowSpec(title="inkstone", width=320, height=200))


class TestHeadlessWindowCapabilities:
    """无头后端把窗口能力记录下来：上层行为可测。"""

    def test_app_identity_is_recorded(self, backend: HeadlessBackend) -> None:
        backend.set_app_identity("Inkstone.Notes")
        assert backend.app_id == "Inkstone.Notes"

    def test_title_round_trips(self, backend: HeadlessBackend, window: int) -> None:
        assert backend.window_title(window) is None
        backend.set_title(window, "墨记 — 未命名")
        assert backend.window_title(window) == "墨记 — 未命名"

    def test_min_size_round_trips(self, backend: HeadlessBackend, window: int) -> None:
        backend.set_min_size(window, 480.0, 320.0)
        assert backend.window_min_size(window) == (480.0, 320.0)

    def test_maximize_and_fullscreen_default_off(
        self, backend: HeadlessBackend, window: int
    ) -> None:
        assert backend.is_maximized(window) is False
        assert backend.is_fullscreen(window) is False

    def test_maximize_toggles(self, backend: HeadlessBackend, window: int) -> None:
        backend.set_maximized(window, True)
        assert backend.is_maximized(window) is True
        backend.set_maximized(window, False)
        assert backend.is_maximized(window) is False

    def test_fullscreen_toggles(self, backend: HeadlessBackend, window: int) -> None:
        backend.set_fullscreen(window, True)
        assert backend.is_fullscreen(window) is True
        backend.set_fullscreen(window, False)
        assert backend.is_fullscreen(window) is False

    def test_window_theme_records_dark_and_background(
        self, backend: HeadlessBackend, window: int
    ) -> None:
        backend.set_window_theme(window, dark=True, background=0x1E1E1E)
        assert backend.window_theme(window) == (True, 0x1E1E1E)

    def test_window_theme_background_defaults_to_none(
        self, backend: HeadlessBackend, window: int
    ) -> None:
        """不给底色 = 用平台默认，这是**有意的 None**而不是漏传。"""
        backend.set_window_theme(window, dark=False)
        assert backend.window_theme(window) == (False, None)

    def test_capabilities_require_a_real_window(self, backend: HeadlessBackend) -> None:
        """对着不存在的窗口调能力必须当场报错，而不是静默丢弃。"""
        for call in (
            lambda: backend.set_title(999, "x"),
            lambda: backend.set_min_size(999, 1.0, 1.0),
            lambda: backend.set_maximized(999, True),
            lambda: backend.set_fullscreen(999, True),
            lambda: backend.set_window_theme(999, dark=True),
        ):
            with pytest.raises(BackendError):
                call()

    def test_theme_can_follow_a_toggle(self, backend: HeadlessBackend, window: int) -> None:
        """浅色 → 深色切换后，窗口系统拿到的必须是**新**主题。"""
        backend.set_window_theme(window, dark=False, background=0xFFFFFF)
        backend.set_window_theme(window, dark=True, background=0x101010)
        assert backend.window_theme(window) == (True, 0x101010)

    def test_window_size_is_the_logical_spec(self, backend: HeadlessBackend, window: int) -> None:
        """视口尺寸是**逻辑**像素——应用外壳据此布局，不碰物理像素。"""
        assert backend.window_size(window) == (320.0, 200.0)
        backend.resize_window(window, 500.0, 400.0)
        assert backend.window_size(window) == (500.0, 400.0)


class TestDpiAwareness:
    """R11.1：进程必须是 DPI 感知的，否则 Windows 会把整窗位图拉伸（界面发虚）。"""

    def test_awareness_probe_is_safe_everywhere(self) -> None:
        awareness = dpi_awareness()
        assert awareness in (0, 1, 2)
        if not is_supported():
            assert awareness == 2, "非 Windows 平台没有'不感知'这回事"

    def test_ensure_is_idempotent_and_reports_success(self) -> None:
        # 首次调用会真的设置；再次调用必须直接返回 True 而不重复设置。
        assert ensure_per_monitor_awareness() is True
        assert ensure_per_monitor_awareness() is True

    def test_ensure_is_callable_and_idempotent(self) -> None:
        """兜底函数随时可调、幂等、返回 bool。

        **故意不在这里断言"调用后变感知"**：那会走先设 Win32 的路径，实测会
        让同进程后续的 SDL IME 组合事件失效（R11.1）。真正的感知不变量由
        `TestSDL2WindowCapabilities` 在真后端上断言（那条走 SDL 自己的 hint）。
        """
        assert isinstance(ensure_per_monitor_awareness(), bool)


class TestWindowsShellIsSafeEverywhere:
    """平台外观函数的契约：返回 bool，永不抛。"""

    def test_support_matches_platform(self) -> None:
        assert is_supported() == (sys.platform == "win32")

    def test_app_id_returns_bool(self) -> None:
        result = apply_app_user_model_id("Inkstone.Test")
        assert isinstance(result, bool)

    def test_caption_theme_without_handle_is_false(self) -> None:
        """没有 HWND（无头 / 非 Windows）时明确返回 False，不抛。"""
        assert apply_caption_theme(0, dark=True) is False

    def test_caption_theme_accepts_garbage_handle(self) -> None:
        """非法句柄由 DWM 拒绝，函数只该返回 False——上层不需要 try。"""
        result = apply_caption_theme(1, dark=True, background=0x123456)
        assert isinstance(result, bool)
        assert result is False  # 句柄 1 不可能是真窗口

    def test_app_id_accepts_odd_input(self) -> None:
        assert isinstance(apply_app_user_model_id(""), bool)


class TestSDL2WindowCapabilities:
    """真机冒烟：这些调用必须真的落到 SDL 上而不崩。装不到 SDL2 就 skip。"""

    @pytest.fixture
    def backend(self):
        from inkstone.backend import SDL2Backend
        from inkstone.backend.sdl2 import load_sdl2

        try:
            load_sdl2()
        except BackendError:
            pytest.skip("本机没有 SDL2（未装 inkstone[sdl2] 或系统库）")
        made = SDL2Backend()
        try:
            made.initialize()
        except BackendError as error:
            pytest.skip(f"SDL2 初始化失败（可能是无显示环境）：{error}")
        yield made
        made.shutdown()

    def test_identity_before_window_does_not_raise(self, backend) -> None:
        backend.set_app_identity("Inkstone.Notes")

    @pytest.mark.skipif(
        sys.platform != "win32", reason="DPI 虚拟化是 Windows 的行为（macOS/Linux 恒为感知）"
    )
    def test_initializing_declares_per_monitor_awareness(self, backend) -> None:
        """界面发虚的根因是进程不感知（Windows 拉伸整窗）——初始化后必须已感知。"""
        assert dpi_awareness() == 2

    def test_window_capability_calls_do_not_raise(self, backend) -> None:
        window = backend.create_window(WindowSpec(title="inkstone test", width=200, height=160))
        try:
            backend.set_title(window, "墨记")
            backend.set_min_size(window, 240.0, 180.0)
            from inkstone.app import app_icon_rgba

            backend.set_icon(window, 32, 32, app_icon_rgba(32))
            backend.set_window_theme(window, dark=True, background=0x1E1E1E)
            backend.set_window_theme(window, dark=False, background=0xFFFFFF)
            backend.set_maximized(window, True)
            backend.set_maximized(window, False)
            backend.pump_events()  # 最大化会产出 RESIZED，泵一下不崩
        finally:
            backend.destroy_window(window)

    def test_window_opens_at_the_requested_logical_size(self, backend) -> None:
        """125% 屏上要 400×300 逻辑像素，拿到的就该是 400×300（×1.25 物理）。

        这条防两个退化：忘了把逻辑换算成物理（窗口小一圈），
        或忘了把物理换算回逻辑（布局按 1.25 倍算，右下角被切掉）。
        """
        window = backend.create_window(WindowSpec(title="size", width=400, height=300))
        try:
            width, height = backend.window_size(window)
            assert abs(width - 400.0) <= 1.0
            assert abs(height - 300.0) <= 1.0
            assert backend.dpi_scale(window) > 0.0
        finally:
            backend.destroy_window(window)

    def test_native_handle_is_hwnd_on_windows(self, backend) -> None:
        window = backend.create_window(WindowSpec(title="inkstone test", width=160, height=120))
        try:
            handle = backend.native_window_handle(window)
            if sys.platform == "win32":
                assert handle != 0, "Windows 上必须拿得到 HWND，否则 DWM 配色是空转"
            else:
                assert handle == 0, "非 Windows 平台明确返回 0"
        finally:
            backend.destroy_window(window)
