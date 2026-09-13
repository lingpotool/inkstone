"""文本热路径缓存（R8.3）。

滚动 / resize / 重排场景里，绝大多数段落的输入根本没变，但断行 + 整形
会被完整重跑——实测文本密集页每帧 37ms 的纯 Python 开销大头在这里。
这两个缓存把"输入没变"变成一次 dict 查询。

缓存是**行为等价**的（Paragraph 不可变，相同输入必得相同结果），
所以这里的断言是："同输入必须复用""不同输入必须区分""清缓存后必须重算"。
"""

from __future__ import annotations

from inkstone.backend import HeadlessBackend
from inkstone.text import TextAlign, TextEngine, TextStyle
from inkstone.text.fallback import script_of

STYLE = TextStyle(families=("sans-serif",), size=14.0)
TEXT = "中文段落缓存测试：标点禁则与断行都要走同一条路径。"


class TestParagraphCache:
    @staticmethod
    def _engine() -> TextEngine:
        return TextEngine(HeadlessBackend())

    def test_identical_inputs_reuse_the_same_paragraph(self) -> None:
        engine = self._engine()
        first = engine.paragraph(TEXT, STYLE, max_width=200.0)
        second = engine.paragraph(TEXT, STYLE, max_width=200.0)
        assert first is second, "相同输入必须复用同一个 Paragraph（热路径的关键）"

    def test_every_input_participates_in_the_key(self) -> None:
        engine = self._engine()
        base = engine.paragraph(TEXT, STYLE, max_width=200.0)
        assert engine.paragraph(TEXT, STYLE, max_width=201.0) is not base
        assert engine.paragraph(TEXT, STYLE, max_width=200.0, align=TextAlign.CENTER) is not base
        assert engine.paragraph(TEXT, STYLE, max_width=200.0, max_lines=1) is not base
        # 文本本身当然也算
        assert engine.paragraph(TEXT + "。", STYLE, max_width=200.0) is not base

    def test_clear_caches_forces_recomputation(self) -> None:
        engine = self._engine()
        first = engine.paragraph(TEXT, STYLE, max_width=200.0)
        engine.clear_caches()
        assert engine.paragraph(TEXT, STYLE, max_width=200.0) is not first


class TestScriptOfCache:
    def test_repeated_lookup_hits_the_cache(self) -> None:
        script_of.cache_clear()
        for _ in range(5):
            script_of("中")
        assert script_of.cache_info().hits >= 4

    def test_result_is_stable(self) -> None:
        assert script_of("A") is script_of("A")
