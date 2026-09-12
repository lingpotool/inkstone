"""输入法（IME）—— 组合态的统一内部模型（R5.7）。

中文输入在平台上的原生形态是**事件流**：组合中（拼音）、取消、上屏。
输入框要的却是**状态**：`text + selection + composition`（docs/04 §6）。
这个模块就是两者之间的转换器——后端只负责产出归一化事件，
怎么把事件流维护成组合态，是这里的事，而且**与平台无关、纯数据、可测**。

两条通道的分工（R5.7 拆开它们的理由）：

- `TextEvent`：普通文本上屏。按字母键出字、IME 选词上屏都是它——
  对输入框的语义只有一个：插进光标处。
- `ImeEvent`：组合态。COMPOSE 更新组合串，CANCEL 丢弃，COMMIT 上屏
  （SDL2 的 IME 上屏走 TEXTINPUT，COMMIT 留给能区分的平台）。

用法：

    backend.start_text_input(window_id)   # 输入框聚焦时（不开这个 SDL 根本不发事件）
    session = ImeSession()
    backend.set_ime_rect(window_id, ...)  # 候选框跟随光标

    for event in backend.pump_events():
        effect = session.feed(event)
        if effect is not None:
            ...  # 应用到输入框的 text / selection / composition

状态：已实现（R5.7）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..backend.base import Event, ImeEvent, ImeKind, TextEvent

__all__ = ["Composition", "EditEffect", "ImeSession"]


@dataclass(frozen=True, slots=True)
class Composition:
    """进行中的组合态：组合串 + 串内正在编辑的那一段（如拼音里的光标）。"""

    text: str
    cursor_start: int
    cursor_end: int


@dataclass(frozen=True, slots=True)
class EditEffect:
    """一个输入事件对输入框内部表示的作用。

    `insert` 是要合入正文的文本（上屏）；`composition` 是当前组合态的
    完整快照（None = 没有在组合）。输入框每帧画的是
    `text + composition` 的叠加——组合串**不进正文**，这样 CANCEL 才是
    免费的（丢弃叠加层即可，不用从正文里删字）。
    """

    insert: str = ""
    composition: Composition | None = None


class ImeSession:
    """跟踪一个输入框的 IME 组合态。

    事件流 → 状态机的全部规则集中在 `feed`：组合中来新组合是**替换**
    不是追加（拼音重选整个词），上屏与取消都终结组合态。
    """

    def __init__(self) -> None:
        self._composition: Composition | None = None

    @property
    def composing(self) -> bool:
        return self._composition is not None

    @property
    def composition(self) -> Composition | None:
        return self._composition

    def feed(self, event: Event) -> EditEffect | None:
        """喂一个事件，返回对输入框的作用；与本模块无关的事件返回 None。"""
        if isinstance(event, TextEvent):
            # 上屏即终结组合态：SDL 的 IME 提交就是 TEXTINPUT，
            # 没有什么"先 COMMIT 再 TEXTINPUT"的两段式。
            self._composition = None
            return EditEffect(insert=event.text)
        if isinstance(event, ImeEvent):
            if event.kind is ImeKind.COMPOSE:
                self._composition = Composition(
                    text=event.text,
                    cursor_start=event.cursor_start,
                    cursor_end=event.cursor_end,
                )
                return EditEffect(composition=self._composition)
            if event.kind is ImeKind.CANCEL:
                self._composition = None
                return EditEffect()
            if event.kind is ImeKind.COMMIT:
                self._composition = None
                return EditEffect(insert=event.text)
        return None

    def cancel(self) -> None:
        """主动放弃组合（失焦、按 Esc 但不想上屏时）。"""
        self._composition = None
