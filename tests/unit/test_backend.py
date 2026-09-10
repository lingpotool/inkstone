"""平台抽象层的单元测试。

分组说明：

- `TestHeadlessBackend`：无头后端是**完整测试**的——它存在的意义就是让
  上层逻辑能在没有窗口、没有输入设备、没有真实时钟的情况下被验证。
- `TestKeyNameNormalization` / `TestSdl2WithoutLibrary`：SDL2 后端里
  **不需要真窗口就能测**的部分。真窗口那部分（pump_events / create_window）
  在 CI 里跑不了，代码里已标注，这里不假装覆盖了它。
"""

import pytest

from inkstone.backend import (
    BackendError,
    Cursor,
    FocusEvent,
    HeadlessBackend,
    ImeEvent,
    ImeKind,
    KeyEvent,
    KeyKind,
    Modifiers,
    PointerEvent,
    PointerKind,
    SDL2Backend,
    WindowEvent,
    WindowKind,
    WindowSpec,
    normalize_key_name,
    sdl2_library_name,
)


class TestHeadlessBackend:
    def test_name_and_lifecycle(self):
        backend = HeadlessBackend()
        assert backend.name == "headless"
        backend.initialize()
        backend.shutdown()
        # shutdown 之后窗口应当都清掉了
        with pytest.raises(BackendError):
            backend.window_spec(1)

    def test_create_window_before_initialize_is_rejected(self):
        backend = HeadlessBackend()
        with pytest.raises(BackendError):
            backend.create_window(WindowSpec())

    def test_create_and_destroy_window(self):
        backend = HeadlessBackend()
        backend.initialize()
        spec = WindowSpec(title="测试", width=640, height=480)
        window = backend.create_window(spec)

        assert backend.window_spec(window).title == "测试"
        backend.destroy_window(window)
        with pytest.raises(BackendError):
            backend.window_spec(window)

    def test_rejects_bad_window_size(self):
        backend = HeadlessBackend()
        backend.initialize()
        with pytest.raises(BackendError):
            backend.create_window(WindowSpec(width=0, height=100))

    # ------------------------------------------------------------ 事件

    def test_inject_then_pump(self):
        backend = HeadlessBackend()
        backend.initialize()
        backend.create_window(WindowSpec())

        backend.inject(PointerEvent(kind=PointerKind.DOWN, x=10, y=20, button=1))
        backend.inject(KeyEvent(kind=KeyKind.DOWN, code="KeyA", text="a"))

        assert backend.pending_count() == 2
        events = backend.pump_events()
        assert len(events) == 2
        assert backend.pending_count() == 0, "取完就应当清空，否则会重复处理"

    def test_wait_events_does_not_block(self):
        """无头后端不该阻塞——没有真窗口，等也没人来事件。"""
        backend = HeadlessBackend()
        backend.initialize()
        backend.inject(FocusEvent(focused=True))
        assert len(backend.wait_events(1000.0)) == 1

    def test_resize_emits_event(self):
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec(width=800, height=600))

        backend.resize_window(window, 1024, 768)
        events = backend.pump_events()

        assert len(events) == 1
        assert isinstance(events[0], WindowEvent)
        assert events[0].kind is WindowKind.RESIZED
        assert (events[0].width, events[0].height) == pytest.approx((1024, 768))

    # ------------------------------------------------------------ 时钟

    def test_clock_only_moves_when_advanced(self):
        """确定性之本：时钟不自己走。"""
        backend = HeadlessBackend()
        assert backend.now_ms() == 0.0
        backend.advance(300.0)
        assert backend.now_ms() == 300.0
        assert backend.now_ms() == 300.0, "不推进就不该变"

    def test_clock_cannot_go_backwards(self):
        backend = HeadlessBackend()
        with pytest.raises(BackendError):
            backend.advance(-1.0)

    def test_start_time_is_configurable(self):
        assert HeadlessBackend(start_time_ms=1000.0).now_ms() == 1000.0

    # ------------------------------------------------------------ DPI

    def test_dpi_scale_defaults_to_one(self):
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())
        assert backend.dpi_scale(window) == pytest.approx(1.0)

    def test_changing_dpi_broadcasts_event(self):
        """125% / 150% 缩放的自动化验证靠这个——真窗口上很难测。"""
        backend = HeadlessBackend()
        backend.initialize()
        backend.create_window(WindowSpec())
        backend.create_window(WindowSpec())

        backend.set_dpi_scale(1.5)
        events = backend.pump_events()

        assert len(events) == 2, "每个窗口都该收到 DPI 变化"
        assert all(isinstance(e, WindowEvent) for e in events)
        assert all(e.kind is WindowKind.DPI_CHANGED for e in events)
        assert all(e.dpi_scale == pytest.approx(1.5) for e in events)

    def test_rejects_bad_dpi(self):
        backend = HeadlessBackend()
        with pytest.raises(BackendError):
            backend.set_dpi_scale(0.0)

    # ------------------------------------------------------------ 杂项

    def test_cursor_is_recorded(self):
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())
        backend.set_cursor(window, Cursor.TEXT)
        assert backend.cursor is Cursor.TEXT

    def test_clipboard_round_trip(self):
        backend = HeadlessBackend()
        backend.clipboard_set_text("中文 + ascii")
        assert backend.clipboard_get_text() == "中文 + ascii"

    def test_redraw_requests_are_counted(self):
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())
        backend.request_redraw(window)
        backend.request_redraw(window)
        assert backend.redraw_requests == 2

    def test_ime_event_carries_composition_range(self):
        """中文输入的关键：组合态要带上正在编辑的那一段。"""
        event = ImeEvent(kind=ImeKind.COMPOSE, text="zhongwen", cursor_start=0, cursor_end=8)
        assert event.kind is ImeKind.COMPOSE
        assert (event.cursor_start, event.cursor_end) == (0, 8)


class TestEventTypes:
    def test_events_are_immutable(self):
        """事件必须是不可变的——它们在队列里传递，被谁改了都很难查。"""
        event = PointerEvent(kind=PointerKind.MOVE, x=1, y=2)
        with pytest.raises(AttributeError):
            event.x = 5  # type: ignore[misc]

    def test_modifiers_default_to_none(self):
        modifiers = Modifiers.none()
        assert not (modifiers.shift or modifiers.ctrl or modifiers.alt or modifiers.meta)


class TestKeyNameNormalization:
    """纯函数，无 SDL2 也能测——键码写错很难发现，所以单独覆盖。"""

    @pytest.mark.parametrize(
        ("sdl_name", "expected"),
        [
            ("Escape", "Escape"),
            ("Return", "Enter"),
            ("Keypad Enter", "Enter"),
            ("Left", "ArrowLeft"),
            ("Right", "ArrowRight"),
            ("Up", "ArrowUp"),
            ("Down", "ArrowDown"),
            ("Left Shift", "ShiftLeft"),
            ("Right Ctrl", "ControlRight"),
            ("Left Alt", "AltLeft"),
            ("Left GUI", "MetaLeft"),
        ],
    )
    def test_special_keys(self, sdl_name: str, expected: str):
        assert normalize_key_name(sdl_name) == expected

    @pytest.mark.parametrize(
        ("sdl_name", "expected"),
        [("a", "KeyA"), ("z", "KeyZ"), ("5", "Digit5"), ("0", "Digit0")],
    )
    def test_printable_keys(self, sdl_name: str, expected: str):
        assert normalize_key_name(sdl_name) == expected

    def test_unknown_names_pass_through(self):
        assert normalize_key_name("F13") == "F13"

    def test_empty_name_is_explicit(self):
        assert normalize_key_name("") == "Unidentified"


class TestSdl2WithoutLibrary:
    """装没装 SDL2 都能跑的测试。"""

    def test_library_name_is_platform_specific(self):
        name = sdl2_library_name()
        assert name.endswith((".dll", ".dylib", ".so", ".so.0"))

    def test_construction_is_safe_without_sdl2(self):
        """构造不该加载库——否则导入这个模块就要装 SDL2。"""
        backend = SDL2Backend()
        assert backend.name == "sdl2"

    def test_using_it_before_initialize_gives_clear_error(self):
        backend = SDL2Backend()
        with pytest.raises(BackendError) as exc:
            backend.now_ms()
        assert "initialize" in str(exc.value)

    def test_initialize_reports_how_to_install_when_missing(self):
        backend = SDL2Backend()
        try:
            backend.initialize()
        except BackendError as error:
            message = str(error)
            # 报错必须能照着修，而不是只说"找不到"
            assert "SDL2" in message
            assert any(word in message for word in ("winget", "brew", "apt"))
            assert "HeadlessBackend" in message, "要告诉用户有不需要 SDL2 的替代方案"
