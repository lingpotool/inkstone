"""焦点系统（R9.1）。

这组测试守的是"键盘输入能正确落到唯一的那个控件"以及"焦点环只在键盘
导航时出现"两条。真机上暴露的两个问题（点过的控件全都一直带环、输入框
打不进字）根因都在这里。
"""

from __future__ import annotations

from inkstone.events.focus import FocusManager, FocusNode, FocusSource


def _node(name: str = "n", **kwargs: object) -> FocusNode:
    return FocusNode(debug_name=name, **kwargs)  # type: ignore[arg-type]


class TestUniqueFocus:
    def test_request_moves_focus_and_blurs_the_previous(self) -> None:
        manager = FocusManager()
        first, second = _node("a"), _node("b")
        manager.attach(first)
        manager.attach(second)

        manager.request(first, FocusSource.POINTER)
        assert manager.current is first and first.focused
        manager.request(second, FocusSource.POINTER)
        assert manager.current is second
        assert not first.focused, "同一时刻只能有一个焦点"

    def test_clear_blurs_current(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.attach(node)
        manager.request(node)
        manager.clear()
        assert manager.current is None and not node.focused

    def test_detach_current_clears_focus(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.attach(node)
        manager.request(node)
        manager.detach(node)
        assert manager.current is None and not node.focused and not node.attached

    def test_disabled_node_cannot_take_focus(self) -> None:
        manager = FocusManager()
        node = _node()
        node.enabled = False
        manager.attach(node)
        manager.request(node)
        assert manager.current is None

    def test_unattached_node_request_is_ignored(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.request(node)
        assert manager.current is None


class TestFocusVisible:
    """focus-visible 语义（docs/13 §5）：环只在键盘来源时显示。"""

    def test_pointer_focus_is_not_visible(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.attach(node)
        manager.request(node, FocusSource.POINTER)
        assert node.focused and not node.focus_visible, "鼠标点击不该画出焦点环"

    def test_keyboard_focus_is_visible(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.attach(node)
        manager.request(node, FocusSource.KEYBOARD)
        assert node.focused and node.focus_visible

    def test_switching_from_pointer_to_keyboard_becomes_visible(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.attach(node)
        manager.request(node, FocusSource.POINTER)
        manager.request(node, FocusSource.KEYBOARD)
        assert node.focus_visible, "同一个节点再次由键盘聚焦时应显示环"

    def test_change_callback_reports_both_flags(self) -> None:
        events: list[tuple[bool, bool]] = []
        manager = FocusManager()
        node = _node(on_change=lambda f, v: events.append((f, v)))
        manager.attach(node)
        manager.request(node, FocusSource.KEYBOARD)
        manager.clear()
        assert events == [(True, True), (False, False)]


class TestTraversal:
    def test_tab_follows_attach_order(self) -> None:
        manager = FocusManager()
        nodes = [_node(f"n{i}") for i in range(3)]
        for node in nodes:
            manager.attach(node)
        manager.move(1)
        assert manager.current is nodes[0]
        manager.move(1)
        assert manager.current is nodes[1]
        assert manager.focus_visible, "键盘导航必须显示焦点环"

    def test_tab_wraps_around(self) -> None:
        manager = FocusManager()
        nodes = [_node(f"n{i}") for i in range(2)]
        for node in nodes:
            manager.attach(node)
        manager.move(1)
        manager.move(1)
        manager.move(1)
        assert manager.current is nodes[0]

    def test_shift_tab_goes_backwards(self) -> None:
        manager = FocusManager()
        nodes = [_node(f"n{i}") for i in range(3)]
        for node in nodes:
            manager.attach(node)
        manager.move(1)  # n0
        manager.move(-1)
        assert manager.current is nodes[2], "从第一个往前应回绕到最后一个"

    def test_explicit_order_overrides_attach_order(self) -> None:
        manager = FocusManager()
        late = _node("late", order=1)
        early = _node("early", order=2)
        manager.attach(late)
        manager.attach(early)
        manager.move(1)
        assert manager.current is late
        manager.move(1)
        assert manager.current is early

    def test_disabled_nodes_are_skipped(self) -> None:
        manager = FocusManager()
        first, middle, last = _node("a"), _node("b"), _node("c")
        middle.enabled = False
        for node in (first, middle, last):
            manager.attach(node)
        manager.move(1)
        assert manager.current is first
        manager.move(1)
        assert manager.current is last, "禁用的节点不该拿到焦点"


class TestEventDispatch:
    def test_event_goes_to_the_focused_node_only(self) -> None:
        received: list[str] = []
        manager = FocusManager()
        first = _node("a", on_event=lambda e: received.append("a") or True)
        second = _node("b", on_event=lambda e: received.append("b") or True)
        manager.attach(first)
        manager.attach(second)
        manager.request(second, FocusSource.POINTER)
        assert manager.dispatch(object()) is True
        assert received == ["b"]

    def test_no_focus_means_unhandled(self) -> None:
        manager = FocusManager()
        assert manager.dispatch(object()) is False


class TestPointerDispatchBlur:
    """点空白处要失焦——否则点过的控件会一直"聚焦"（真机上的那圈边框）。"""

    def test_focus_is_cleared_when_nothing_requested_it(self) -> None:
        manager = FocusManager()
        node = _node()
        manager.attach(node)
        manager.request(node, FocusSource.POINTER)

        manager.begin_pointer_dispatch()
        blurred = manager.end_pointer_dispatch()
        assert blurred is True
        assert manager.current is None

    def test_focus_stays_when_a_node_requested_it(self) -> None:
        manager = FocusManager()
        first, second = _node("a"), _node("b")
        manager.attach(first)
        manager.attach(second)
        manager.request(first, FocusSource.POINTER)

        manager.begin_pointer_dispatch()
        manager.request(second, FocusSource.POINTER)  # 模拟点中另一个输入框
        assert manager.end_pointer_dispatch() is False
        assert manager.current is second

    def test_no_focus_to_clear_is_reported(self) -> None:
        manager = FocusManager()
        manager.begin_pointer_dispatch()
        assert manager.end_pointer_dispatch() is False
