"""IME 组合态模型与 IME 通道的测试（R5.7）。

三层各钉一层：

1. `ImeSession`：事件流 → 组合态状态机（纯数据，与平台无关）；
2. `HeadlessBackend` 的 IME 通道：start/stop/set_ime_rect 被正确记录——
   上层"输入框聚焦时打开 IME"的测试就断言这些；
3. headless 的 window_id 路由（R5.1）：双窗口各自只收到自己的事件。
"""

from __future__ import annotations

import pytest

from inkstone.backend import (
    HeadlessBackend,
    ImeEvent,
    ImeKind,
    ImeRect,
    KeyEvent,
    KeyKind,
    PointerEvent,
    PointerKind,
    TextEvent,
    WindowSpec,
)
from inkstone.events.ime import Composition, ImeSession


class TestImeSession:
    def test_compose_updates_composition_snapshot(self) -> None:
        session = ImeSession()
        effect = session.feed(
            ImeEvent(kind=ImeKind.COMPOSE, text="zhong", cursor_start=0, cursor_end=5)
        )
        assert session.composing
        assert effect is not None
        assert effect.composition == Composition(text="zhong", cursor_start=0, cursor_end=5)
        assert effect.insert == "", "组合中的文字不进正文"

    def test_new_compose_replaces_the_old_one(self) -> None:
        """拼音重选整个词：组合是替换，不是追加。"""
        session = ImeSession()
        session.feed(ImeEvent(kind=ImeKind.COMPOSE, text="zhong"))
        effect = session.feed(ImeEvent(kind=ImeKind.COMPOSE, text="zhongwen"))
        assert effect is not None
        assert effect.composition is not None and effect.composition.text == "zhongwen"

    def test_text_event_commits_and_clears_composition(self) -> None:
        """SDL 的 IME 上屏走 TEXTINPUT：上屏即终结组合态。"""
        session = ImeSession()
        session.feed(ImeEvent(kind=ImeKind.COMPOSE, text="zhongwen"))
        effect = session.feed(TextEvent(text="中文"))
        assert effect is not None
        assert effect.insert == "中文"
        assert effect.composition is None
        assert not session.composing

    def test_cancel_discards_without_inserting(self) -> None:
        """CANCEL 是免费的：组合从没进过正文，丢弃叠加层即可。"""
        session = ImeSession()
        session.feed(ImeEvent(kind=ImeKind.COMPOSE, text="zhongwen"))
        effect = session.feed(ImeEvent(kind=ImeKind.CANCEL))
        assert effect is not None
        assert effect.insert == ""
        assert effect.composition is None
        assert not session.composing

    def test_commit_kind_inserts_text(self) -> None:
        """COMMIT 留给能区分 IME 上屏的平台（Win32 IMM）。"""
        session = ImeSession()
        session.feed(ImeEvent(kind=ImeKind.COMPOSE, text="nihon"))
        effect = session.feed(ImeEvent(kind=ImeKind.COMMIT, text="日本"))
        assert effect is not None and effect.insert == "日本"
        assert not session.composing

    def test_plain_typing_without_composition(self) -> None:
        """英文输入：没有组合态，TextEvent 直接是插入。"""
        session = ImeSession()
        effect = session.feed(TextEvent(text="a"))
        assert effect is not None and effect.insert == "a"
        assert not session.composing

    def test_unrelated_events_pass_through(self) -> None:
        session = ImeSession()
        assert session.feed(KeyEvent(kind=KeyKind.DOWN, code="KeyA")) is None
        assert session.feed(PointerEvent(kind=PointerKind.MOVE, x=1, y=2)) is None


class TestHeadlessImeChannel:
    """R5.7 验收：headless 能断言 IME 三方法被正确调用。"""

    def test_text_input_lifecycle_is_recorded(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())

        assert not backend.text_input_active(window)
        backend.start_text_input(window)
        assert backend.text_input_active(window)
        backend.stop_text_input(window)
        assert not backend.text_input_active(window)

    def test_ime_rect_is_recorded_per_window(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        first = backend.create_window(WindowSpec())
        second = backend.create_window(WindowSpec())

        backend.set_ime_rect(first, ImeRect(x=10, y=20, width=30, height=14))

        assert backend.ime_rect(first) == ImeRect(x=10, y=20, width=30, height=14)
        assert backend.ime_rect(second) is None, "候选框位置是 per-window 的"

    def test_ime_methods_reject_unknown_windows(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        backend.create_window(WindowSpec())
        from inkstone.backend import BackendError

        with pytest.raises(BackendError):
            backend.start_text_input(999)
        with pytest.raises(BackendError):
            backend.set_ime_rect(999, ImeRect(x=0, y=0, width=1, height=1))

    def test_destroying_window_cleans_ime_state(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())
        backend.start_text_input(window)
        backend.set_ime_rect(window, ImeRect(x=1, y=2, width=3, height=4))
        backend.destroy_window(window)
        assert not backend.text_input_active(window)
        assert backend.ime_rect(window) is None


class TestWindowIdRouting:
    """R5.1：事件带 window_id，双窗口各自只收到自己的。"""

    def test_each_window_gets_only_its_own_events(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        first = backend.create_window(WindowSpec(width=100, height=100))
        second = backend.create_window(WindowSpec(width=200, height=200))

        backend.resize_window(first, 300, 300)
        backend.set_dpi_scale(second, 2.0)
        backend.inject(PointerEvent(kind=PointerKind.DOWN, x=1, y=1, window_id=first))

        events = backend.pump_events()
        by_window: dict[int, list[object]] = {first: [], second: []}
        for event in events:
            window_id = getattr(event, "window_id", 0)
            by_window[window_id].append(event)

        assert len(by_window[first]) == 2  # resize + 注入的指针事件
        assert len(by_window[second]) == 1  # 只有 DPI
        assert all(getattr(e, "window_id", 0) == first for e in by_window[first]), (
            "窗口一的事件里不许混入窗口二的"
        )

    def test_injected_events_keep_their_window_id(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())
        backend.inject(TextEvent(text="字", window_id=window))
        events = backend.pump_events()
        assert isinstance(events[0], TextEvent)
        assert events[0].window_id == window


class TestHeadlessFrameSeam:
    """R5.8：帧边界可断言——"这帧上屏了没有"测试要知道。"""

    def test_present_counts_only_presented_frames(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec())

        backend.begin_frame(window)
        backend.end_frame(window, present=True)
        backend.begin_frame(window)
        backend.end_frame(window, present=False)  # 离屏帧不上屏

        assert backend.frames_presented == 1

    def test_frame_requires_a_real_window(self) -> None:
        from inkstone.backend import BackendError

        backend = HeadlessBackend()
        backend.initialize()
        backend.create_window(WindowSpec())
        with pytest.raises(BackendError):
            backend.begin_frame(999)
