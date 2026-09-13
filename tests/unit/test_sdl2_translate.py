"""SDL2 事件翻译层的纯函数测试（R5）。

翻译器全部是不碰 lib 的纯函数，测试直接构造 ctypes 结构体喂进去——
"SDL 事件 → 归一化事件"这条链路因此**不需要真窗口**就能全覆盖。
这里钉住的是 R5 的每一条具体修复：事件自带时间戳、window_id 透传、
scancode/keysym 分离、滚轮方向与精度、双击信息、IME 两通道拆分。
"""

from __future__ import annotations

import ctypes

from inkstone.backend import (
    ImeKind,
    KeyKind,
    PointerKind,
    PointerType,
    TextEvent,
    WindowKind,
)
from inkstone.backend.base import ImeEvent
from inkstone.backend.sdl2 import (
    _KeyboardEvent,
    _MouseButtonEvent,
    _MouseMotionEvent,
    _MouseWheelEvent,
    _TextEditingEvent,
    _TextInputEvent,
    _WindowEvent,
    translate_button,
    translate_keyboard,
    translate_motion,
    translate_text_editing,
    translate_text_input,
    translate_wheel,
    translate_window_event,
)


def _keyboard(*, scancode: int = 4, sym: int = 97, mod: int = 0, repeat: int = 0) -> _KeyboardEvent:
    raw = _KeyboardEvent()
    raw.timestamp = 1234
    raw.window_id = 7
    raw.repeat = repeat
    raw.keysym.scancode = scancode
    raw.keysym.sym = sym
    raw.keysym.mod = mod
    return raw


class TestTranslateKeyboard:
    def test_timestamp_comes_from_the_event_not_the_clock(self):
        """R5.2：时间戳是事件自带的，不是泵时刻的 SDL_GetTicks。"""
        event = translate_keyboard(_keyboard(), down=True, code_name="A", key_name="a")
        assert event.time_ms == 1234.0

    def test_window_id_is_carried(self):
        event = translate_keyboard(_keyboard(), down=True, code_name="A", key_name="a")
        assert event.window_id == 7

    def test_scancode_and_keysym_are_separate(self):
        """R5.6：AZERTY 场景——物理键位是 A 的位置，敲出来的是 q。

        code 必须跟物理键位（快捷键在 AZERTY 上不漂），key 跟布局。
        """
        event = translate_keyboard(_keyboard(), down=True, code_name="A", key_name="q")
        assert event.code == "KeyA"  # 物理位：QWERTY 的 A 键位置
        assert event.key == "q"  # 布局相关：AZERTY 上这个位置是 q

    def test_numpad_enter_is_not_merged_into_enter(self):
        event = translate_keyboard(
            _keyboard(), down=True, code_name="Keypad Enter", key_name="Keypad Enter"
        )
        assert event.code == "NumpadEnter"  # 物理键位分得开
        assert event.key == "Enter"  # 语义上都是确认

    def test_repeat_and_modifiers(self):
        event = translate_keyboard(
            _keyboard(repeat=1, mod=0x0001 | 0x0040), down=True, code_name="A", key_name="a"
        )
        assert event.repeat is True
        assert event.modifiers.shift and event.modifiers.ctrl
        assert not event.modifiers.alt

    def test_up_kind(self):
        event = translate_keyboard(_keyboard(), down=False, code_name="Escape", key_name="Escape")
        assert event.kind is KeyKind.UP


class TestTranslateTextChannels:
    """R5.7：text input 与 IME composition 是两条通道，不再混用 ImeKind.COMMIT。"""

    def test_text_input_is_a_text_event_not_an_ime_commit(self):
        raw = _TextInputEvent()
        raw.timestamp = 50
        raw.window_id = 3
        raw.text = "中".encode()
        event = translate_text_input(raw)
        assert isinstance(event, TextEvent)
        assert event.text == "中"
        assert event.window_id == 3
        assert event.time_ms == 50.0

    def test_text_editing_is_compose_with_cursor_range(self):
        raw = _TextEditingEvent()
        raw.timestamp = 60
        raw.window_id = 3
        raw.text = b"zhongwen"
        raw.start = 2
        raw.length = 5
        event = translate_text_editing(raw)
        assert isinstance(event, ImeEvent)
        assert event.kind is ImeKind.COMPOSE
        assert event.text == "zhongwen"
        assert (event.cursor_start, event.cursor_end) == (2, 7)

    def test_empty_editing_is_cancel(self):
        """CANCEL 的发射路径（R5.7）：组合被清空 = 取消。"""
        raw = _TextEditingEvent()
        raw.timestamp = 61
        raw.window_id = 3
        raw.text = b""
        event = translate_text_editing(raw)
        assert event.kind is ImeKind.CANCEL
        assert event.text == ""


class TestTranslatePointer:
    def test_motion_carries_window_and_timestamp(self):
        raw = _MouseMotionEvent()
        raw.timestamp = 99
        raw.window_id = 5
        raw.which = 0
        raw.x = 10
        raw.y = 20
        event = translate_motion(raw)
        assert event.kind is PointerKind.MOVE
        assert (event.x, event.y) == (10.0, 20.0)
        assert event.window_id == 5
        assert event.time_ms == 99.0
        assert event.pointer_type is PointerType.MOUSE

    def test_touch_synthesized_motion_is_marked_touch(self):
        """R5.5：which == SDL_TOUCH_MOUSEID 的是触摸合成事件。"""
        raw = _MouseMotionEvent()
        raw.which = 0xFFFFFFFF
        event = translate_motion(raw)
        assert event.pointer_type is PointerType.TOUCH
        assert event.pointer_id == 0

    def test_down_move_up_share_one_pointer_id(self):
        """**回归**：同一次拖拽的 DOWN / MOVE / UP 必须带同一个 pointer_id。

        竞技场按 pointer_id 找"这次按下的局"，对不上就整条拖拽链失效。
        历史 bug：motion 用 `raw.which`、button 却留默认 0——真机上鼠标拖拽
        （滚动列表）完全没反应，而单元测试手写 pointer_id=0 所以一直是绿的。
        """
        motion = _MouseMotionEvent()
        motion.which = 1
        button = _MouseButtonEvent()
        button.which = 1

        down = translate_button(button, down=True)
        move = translate_motion(motion)
        up = translate_button(button, down=False)

        assert down.pointer_id == move.pointer_id == up.pointer_id != 0
        assert translate_motion(motion).pointer_type is PointerType.MOUSE

    def test_button_carries_clicks(self):
        """R5.5：双击信息（SDL 算好的 clicks）必须进事件。"""
        raw = _MouseButtonEvent()
        raw.timestamp = 100
        raw.window_id = 2
        raw.button = 1
        raw.clicks = 2
        raw.x = 30
        raw.y = 40
        event = translate_button(raw, down=True)
        assert event.kind is PointerKind.DOWN
        assert event.button == 1
        assert event.clicks == 2
        assert (event.x, event.y) == (30.0, 40.0)


class TestTranslateWheel:
    def _wheel(self, **kw: int | float) -> _MouseWheelEvent:
        raw = _MouseWheelEvent()
        raw.timestamp = 200
        raw.window_id = 9
        for name, value in kw.items():
            setattr(raw, name, value)
        return raw

    def test_precise_deltas_win_over_integers(self):
        """R5.4：触摸板平滑滚动的浮点增量优先。"""
        raw = self._wheel(x=1, y=1, precise_x=0.5, precise_y=-0.25)
        event = translate_wheel(raw, 0.0, 0.0)
        assert event.wheel_dx == 0.5
        assert event.wheel_dy == -0.25

    def test_integer_fallback_for_old_sdl(self):
        raw = self._wheel(x=0, y=3)  # precise 为 0 = 旧 SDL 没写这个字段
        event = translate_wheel(raw, 0.0, 0.0)
        assert event.wheel_dy == 3.0

    def test_flipped_direction_is_normalized(self):
        """macOS 自然滚动：FLIPPED 标记在这里消化，上层不用知道。"""
        raw = self._wheel(y=2, direction=1)  # SDL_MOUSEWHEEL_FLIPPED
        event = translate_wheel(raw, 0.0, 0.0)
        assert event.wheel_dy == -2.0

    def test_coordinates_come_from_the_event(self):
        """R5.4：滚轮事件的坐标是真实指针位置，不是硬编码 0,0。"""
        raw = self._wheel(y=1, mouse_x=300, mouse_y=200)
        event = translate_wheel(raw, 0.0, 0.0)
        assert (event.x, event.y) == (300.0, 200.0)

    def test_old_sdl_falls_back_to_mouse_state(self):
        raw = self._wheel(y=1)  # mouse_x/mouse_y 为 0 = SDL < 2.26
        event = translate_wheel(raw, 123.0, 456.0)
        assert (event.x, event.y) == (123.0, 456.0)


class TestTranslateWindowEvent:
    def _window(self, event_id: int, data1: int = 0, data2: int = 0) -> _WindowEvent:
        raw = _WindowEvent()
        raw.timestamp = 500
        raw.window_id = 4
        raw.event = event_id
        raw.data1 = data1
        raw.data2 = data2
        return raw

    def test_resized(self):
        event = translate_window_event(self._window(5, 1024, 768))
        assert event is not None and event.kind is WindowKind.RESIZED
        assert (event.width, event.height) == (1024.0, 768.0)
        assert event.window_id == 4
        assert event.time_ms == 500.0

    def test_close_is_14_not_2(self):
        """R5.10：CLOSE 是 14（2 是 HIDDEN——写错了关窗事件永远收不到）。"""
        event = translate_window_event(self._window(14))
        assert event is not None and event.kind is WindowKind.CLOSE
        assert translate_window_event(self._window(2)) is None

    def test_focus_pair(self):
        gained = translate_window_event(self._window(12))
        lost = translate_window_event(self._window(13))
        assert gained is not None and getattr(gained, "focused", None) is True
        assert lost is not None and getattr(lost, "focused", None) is False
        assert gained is not None and gained.window_id == 4

    def test_enter_leave_become_pointer_events(self):
        """R5.5：ENTER/LEAVE 进指针模型（悬停态的前提）。"""
        enter = translate_window_event(self._window(10))
        leave = translate_window_event(self._window(11))
        assert enter is not None and getattr(enter, "kind", None) is PointerKind.ENTER
        assert leave is not None and getattr(leave, "kind", None) is PointerKind.LEAVE
        assert enter is not None and enter.window_id == 4

    def test_moved_is_not_translated_here(self):
        """MOVED 要查 DPI（需要 lib），由后端自己处理——纯函数不管。"""
        assert translate_window_event(self._window(4)) is None


# ------------------------------------------------------------ R5.3：wait_events 丢事件


class _FakeLib:
    """最小 SDL 假身：只为测 wait_events 不丢唤醒事件（R5.3 的回归）。

    `WaitEventTimeout` 会把唤醒它的事件**取走**——旧实现把这个 buffer 丢了，
    每次唤醒稳定丢一个事件（比如用户按下的第一下键）。
    """

    def __init__(self) -> None:
        self.queued: list[int] = []

    def SDL_WaitEventTimeout(self, buffer: ctypes.Array[ctypes.c_char], timeout: int) -> int:
        # 唤醒事件：一个 KeyDown（type=0x300, timestamp=42, window_id=1）
        raw = _KeyboardEvent()
        raw.type = 0x300
        raw.timestamp = 42
        raw.window_id = 1
        raw.keysym.scancode = 4
        raw.keysym.sym = 97
        ctypes.memmove(buffer, ctypes.byref(raw), ctypes.sizeof(raw))
        return 1

    def SDL_PollEvent(self, buffer: ctypes.Array[ctypes.c_char]) -> int:
        return 0

    def SDL_GetScancodeName(self, scancode: int) -> bytes:
        return b"A"

    def SDL_GetKeyName(self, sym: int) -> bytes:
        return b"a"


class TestWaitEventsKeepsTheWakeEvent:
    def test_the_event_that_woke_us_up_is_translated(self):
        from inkstone.backend import SDL2Backend

        backend = SDL2Backend()
        backend._lib = _FakeLib()
        backend._initialized = True

        events = backend.wait_events(1000.0)

        assert len(events) == 1, "唤醒 wait 的那个事件不许丢"
        assert events[0].time_ms == 42.0
        assert events[0].window_id == 0  # SDL 的 1 号窗口不在映射表里 → 置 0

    def test_timeout_returns_empty(self):
        from inkstone.backend import SDL2Backend

        class TimeoutLib(_FakeLib):
            def SDL_WaitEventTimeout(self, buffer: object, timeout: int) -> int:
                return 0

        backend = SDL2Backend()
        backend._lib = TimeoutLib()
        backend._initialized = True
        assert backend.wait_events(10.0) == []
