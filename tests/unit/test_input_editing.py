"""输入框编辑模型（R9.3）。

覆盖 Phase 1 DoD 的那句话：**可输入、可删除、光标位置正确**。外加组合态
（中文输入）的进/出、剪贴板、焦点路由与"点空白失焦"。

测试全部无头：注入 `TextEvent` / `KeyEvent` / `ImeEvent` 与时间戳，
不开窗口、不碰平台。
"""

from __future__ import annotations

import pytest

from inkstone.backend import HeadlessBackend
from inkstone.backend.base import (
    ImeEvent,
    ImeKind,
    KeyEvent,
    KeyKind,
    Modifiers,
    PointerEvent,
    PointerKind,
    TextEvent,
)
from inkstone.core import BuildOwner, FrameError
from inkstone.layout import BoxConstraints
from inkstone.style import ComponentState, Theme
from inkstone.text import TextEngine
from inkstone.widgets import Box, Column, Input

CONSTRAINTS = BoxConstraints(max_width=320, max_height=200)


def _owner() -> BuildOwner:
    return BuildOwner(theme=Theme.light(), text_engine=TextEngine(HeadlessBackend()))


def _mounted(*widgets: object) -> tuple[BuildOwner, object]:
    owner = _owner()
    if len(widgets) == 1:
        element: object = owner.mount(widgets[0])  # type: ignore[arg-type]
    else:
        element = owner.mount(Column(children=tuple(widgets)))  # type: ignore[arg-type]
    owner.begin_frame(CONSTRAINTS)
    return owner, element


def _focus(owner: BuildOwner, *, x: float = 10.0, y: float = 18.0) -> None:
    owner.dispatch_pointer(PointerEvent(kind=PointerKind.DOWN, x=x, y=y, button=1))
    owner.dispatch_pointer(PointerEvent(kind=PointerKind.UP, x=x, y=y, button=1))
    owner.begin_frame(CONSTRAINTS)


def _text(owner: BuildOwner, value: str) -> None:
    owner.dispatch_text(TextEvent(text=value))
    owner.begin_frame(CONSTRAINTS)


def _key(owner: BuildOwner, code: str, *, shift: bool = False, ctrl: bool = False) -> None:
    owner.dispatch_key(
        KeyEvent(
            kind=KeyKind.DOWN,
            code=code,
            modifiers=Modifiers(shift=shift, ctrl=ctrl),
        )
    )
    owner.begin_frame(CONSTRAINTS)


class TestTyping:
    def test_text_event_inserts_at_caret(self) -> None:
        owner, element = _mounted(Input())
        _focus(owner)
        _text(owner, "hello")
        assert element.state.text == "hello"
        assert element.state.selection == (5, 5)

    def test_on_changed_callback_fires(self) -> None:
        seen: list[str] = []
        owner, _ = _mounted(Input(on_changed=seen.append))
        _focus(owner)
        _text(owner, "中文")
        assert seen == ["中文"]

    def test_typing_without_focus_is_ignored(self) -> None:
        owner, element = _mounted(Input())
        owner.dispatch_text(TextEvent(text="x"))
        assert element.state.text == ""

    def test_caret_moves_and_inserts_in_the_middle(self) -> None:
        owner, element = _mounted(Input(value="hello"))
        _focus(owner)
        _key(owner, "Home")
        _text(owner, ">")
        assert element.state.text == ">hello"
        assert element.state.selection == (1, 1)


class TestDeleting:
    def test_backspace_deletes_one_character(self) -> None:
        owner, element = _mounted(Input(value="ab"))
        _focus(owner)
        _key(owner, "End")  # 点击定位在左端，先移到末尾
        _key(owner, "Backspace")
        assert element.state.text == "a"

    def test_backspace_at_start_is_a_noop(self) -> None:
        owner, element = _mounted(Input(value="ab"))
        _focus(owner)
        _key(owner, "Home")
        _key(owner, "Backspace")
        assert element.state.text == "ab"

    def test_delete_forward(self) -> None:
        owner, element = _mounted(Input(value="ab"))
        _focus(owner)
        _key(owner, "Home")
        _key(owner, "Delete")
        assert element.state.text == "b"

    def test_backspace_deletes_a_whole_grapheme_cluster(self) -> None:
        """按字素簇删除：不能把基字符和组合符拆开（铁律 2）。"""
        owner, element = _mounted(Input(value="e\u0301x"))
        _focus(owner)
        _key(owner, "End")
        _key(owner, "Backspace")  # 只删掉 x
        _key(owner, "Backspace")  # 再删掉整个 é（两个码点）
        assert element.state.text == ""

    def test_delete_selection_removes_the_range(self) -> None:
        owner, element = _mounted(Input(value="hello"))
        _focus(owner)
        _key(owner, "Home")
        _key(owner, "ArrowRight", shift=True)
        _key(owner, "ArrowRight", shift=True)
        assert element.state.selection == (0, 2)
        _key(owner, "Backspace")
        assert element.state.text == "llo"


class TestCaretMovement:
    def test_arrows_move_by_cluster(self) -> None:
        owner, element = _mounted(Input(value="ab"))
        _focus(owner)
        _key(owner, "Home")
        _key(owner, "ArrowRight")
        assert element.state.selection == (1, 1)
        _key(owner, "ArrowLeft")
        assert element.state.selection == (0, 0)

    def test_shift_arrow_extends_selection(self) -> None:
        owner, element = _mounted(Input(value="abc"))
        _focus(owner)
        _key(owner, "Home")
        _key(owner, "ArrowRight", shift=True)
        _key(owner, "ArrowRight", shift=True)
        assert element.state.selection == (0, 2)

    def test_typing_replaces_selection(self) -> None:
        owner, element = _mounted(Input(value="abc"))
        _focus(owner)
        _key(owner, "Home")
        _key(owner, "ArrowRight", shift=True)
        _key(owner, "ArrowRight", shift=True)
        _text(owner, "X")
        assert element.state.text == "Xc"

    def test_home_and_end(self) -> None:
        owner, element = _mounted(Input(value="abc"))
        _focus(owner)
        _key(owner, "Home")
        assert element.state.selection == (0, 0)
        _key(owner, "End")
        assert element.state.selection == (3, 3)


class TestClickPlacesCaret:
    def test_click_near_left_edge_puts_caret_at_start(self) -> None:
        owner, element = _mounted(Input(value="hello world"))
        _focus(owner, x=11.0)  # padding 内一点点
        assert element.state.selection[0] <= 1

    def test_click_near_right_edge_puts_caret_at_end(self) -> None:
        owner, element = _mounted(Input(value="hello world"))
        _focus(owner, x=315.0)
        assert element.state.selection[0] == len("hello world")

    def test_input_shows_focus_border_on_pointer_focus(self) -> None:
        """输入框与按钮不同：鼠标聚焦也要给出可见反馈（马上要打字）。"""
        owner, element = _mounted(Input())
        _focus(owner)
        assert element.state.component_state is ComponentState.FOCUS_VISIBLE


class TestComposition:
    def test_compose_shows_but_does_not_commit(self) -> None:
        owner, element = _mounted(Input())
        _focus(owner)
        owner.dispatch_ime(ImeEvent(kind=ImeKind.COMPOSE, text="ni", cursor_start=0, cursor_end=2))
        owner.begin_frame(CONSTRAINTS)
        assert element.state.text == "", "组合串不该进正文"
        assert element.state.composition == "ni"

    def test_commit_inserts_composition(self) -> None:
        owner, element = _mounted(Input())
        _focus(owner)
        owner.dispatch_ime(ImeEvent(kind=ImeKind.COMPOSE, text="ni"))
        owner.dispatch_ime(ImeEvent(kind=ImeKind.COMMIT, text="你"))
        owner.begin_frame(CONSTRAINTS)
        assert element.state.text == "你"
        assert element.state.composition is None

    def test_cancel_drops_composition(self) -> None:
        owner, element = _mounted(Input())
        _focus(owner)
        owner.dispatch_ime(ImeEvent(kind=ImeKind.COMPOSE, text="ni"))
        owner.dispatch_ime(ImeEvent(kind=ImeKind.CANCEL))
        owner.begin_frame(CONSTRAINTS)
        assert element.state.text == ""
        assert element.state.composition is None

    def test_text_event_terminates_composition(self) -> None:
        owner, element = _mounted(Input())
        _focus(owner)
        owner.dispatch_ime(ImeEvent(kind=ImeKind.COMPOSE, text="ni"))
        _text(owner, "你")
        assert element.state.composition is None and element.state.text == "你"


class TestClipboard:
    def test_copy_cut_paste_round_trip(self) -> None:
        owner, element = _mounted(Input(value="hello"))
        clipboard = {"text": ""}
        owner.clipboard_get = lambda: clipboard["text"]
        owner.clipboard_set = lambda value: clipboard.__setitem__("text", value)
        _focus(owner)
        _key(owner, "KeyA", ctrl=True)
        _key(owner, "KeyC", ctrl=True)
        assert clipboard["text"] == "hello"
        _key(owner, "KeyX", ctrl=True)
        assert element.state.text == ""
        _key(owner, "KeyV", ctrl=True)
        assert element.state.text == "hello"


class TestFocusIntegration:
    def test_clicking_blank_blurs_the_input(self) -> None:
        owner, column = _mounted(Input(), Box(height=60.0, color=Theme.light().color("surface")))
        element = column.children[0].state  # type: ignore[attr-defined]
        _focus(owner)
        assert element.focused
        # 点在输入框下方的空白 Box 上：没人请求焦点 → 失焦
        owner.dispatch_pointer(PointerEvent(kind=PointerKind.DOWN, x=10.0, y=80.0, button=1))
        owner.begin_frame(CONSTRAINTS)
        assert not element.focused

    def test_tab_moves_focus_between_inputs(self) -> None:
        owner, column = _mounted(Input(), Input())
        first = column.children[0].state  # type: ignore[attr-defined]
        second = column.children[1].state  # type: ignore[attr-defined]
        owner.dispatch_pointer(PointerEvent(kind=PointerKind.DOWN, x=10.0, y=18.0, button=1))
        owner.begin_frame(CONSTRAINTS)
        assert first.focused
        owner.dispatch_key(KeyEvent(kind=KeyKind.DOWN, code="Tab"))
        owner.begin_frame(CONSTRAINTS)
        assert second.focused and not first.focused

    def test_composition_is_dropped_on_blur(self) -> None:
        owner, column = _mounted(Input(), Box(height=60.0, color=Theme.light().color("surface")))
        element = column.children[0].state  # type: ignore[attr-defined]
        _focus(owner)
        owner.dispatch_ime(ImeEvent(kind=ImeKind.COMPOSE, text="ni"))
        owner.begin_frame(CONSTRAINTS)
        owner.dispatch_pointer(PointerEvent(kind=PointerKind.DOWN, x=10.0, y=80.0, button=1))
        owner.begin_frame(CONSTRAINTS)
        assert element.composition is None


class TestCaretRectReporting:
    def test_caret_rect_is_reported_to_ime(self) -> None:
        rects: list[object] = []
        owner = _owner()
        owner.on_ime_rect = rects.append
        owner.mount(Input(value="abc"))
        owner.begin_frame(CONSTRAINTS)
        owner.dispatch_pointer(PointerEvent(kind=PointerKind.DOWN, x=10.0, y=18.0, button=1))
        owner.begin_frame(CONSTRAINTS)
        assert rects, "聚焦后必须上报候选框位置"

    def test_caret_rect_moves_with_the_caret(self) -> None:
        rects: list[object] = []
        owner = _owner()
        owner.on_ime_rect = rects.append
        owner.mount(Input(value="hello world"))
        owner.begin_frame(CONSTRAINTS)
        owner.dispatch_pointer(PointerEvent(kind=PointerKind.DOWN, x=11.0, y=18.0, button=1))
        owner.begin_frame(CONSTRAINTS)
        left_x = rects[-1].left  # type: ignore[attr-defined]
        owner.dispatch_key(KeyEvent(kind=KeyKind.DOWN, code="End"))
        owner.begin_frame(CONSTRAINTS)
        right_x = rects[-1].left  # type: ignore[attr-defined]
        assert right_x > left_x, "光标右移后候选框锚点必须跟着走"


class TestPhaseGuard:
    def test_dispatch_text_during_layout_raises(self) -> None:
        from inkstone.core import LeafRenderObjectElement, RenderObjectWidget
        from inkstone.layout import RenderBox, Size

        owner = _owner()

        class _HookBox(RenderBox):
            def perform_layout(self, constraints: BoxConstraints) -> Size:
                owner.dispatch_text(TextEvent(text="x"))
                return constraints.constrain(Size(4.0, 4.0))

        class _HookWidget(RenderObjectWidget):
            def create_render_object(self) -> _HookBox:
                return _HookBox()

            def create_element(self) -> LeafRenderObjectElement:
                return LeafRenderObjectElement(self)

        owner.mount(_HookWidget())
        with pytest.raises(FrameError, match="派发文本事件"):
            owner.flush_layout(CONSTRAINTS)
