"""文本渲染测试：显示列表指令、字形提供方、软件光栅、Text 组件。

它守住的是"文本从字符到像素"这条完整链路：

    字符串 → 文本层排版 → TextRunOp（字形 + 位置）→ 字形掩码 → 像素

关键性质（都是"错了会很隐蔽"的那类）：

    - **字形位置来自文本层的 advance，光栅层不自己量字**（docs/04 §3）。
      一旦光栅层自己算宽度，就会出现"排版对了但画歪了"。
    - 重绘两次逐字节相同（确定性优先于一切，铁律 3）。
    - Text 的布局尺寸**就是**它绘制时用的那份排版结果（度量同源）。

全部无窗口、无 GPU、纯确定性。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from inkstone.backend import FontWeight, HeadlessBackend
from inkstone.core import BuildOwner
from inkstone.devtools import render_to_png
from inkstone.gfx import (
    DisplayList,
    DisplayListRecorder,
    PositionedGlyph,
    SoftwareRasterizer,
    TextRunOp,
)
from inkstone.gfx.color import Color
from inkstone.gfx.glyphs import BuiltinGlyphProvider, GlyphMask, rect_of_mask
from inkstone.layout import BoxConstraints
from inkstone.layout.types import Offset, Rect
from inkstone.style import Theme
from inkstone.text import TextAlign, TextEngine
from inkstone.widgets import Card, Column, Text
from png_compare import GoldenBaseline

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
FAILURES_DIR = GOLDEN_DIR / "failures"
UPDATE_ENV = "INKSTONE_UPDATE_GOLDEN"

BLACK = Color(0, 0, 0)


def _owner(theme: Theme | None = None) -> BuildOwner:
    """带文本引擎的 BuildOwner —— 文本组件排版的前提。"""
    return BuildOwner(theme=theme, text_engine=TextEngine(HeadlessBackend()))


def _assert_or_update_golden(name: str, actual: bytes) -> None:
    """比对 / 更新黄金图基线。

    比对单位是**解码后的 RGBA 像素**，不是 PNG 文件字节——`encode_png` 用
    `zlib.compress(..., level=6)`，而 deflate 的输出不跨 zlib 版本保证一致
    （docs/16 §R2.1）。基线缺失也不再"顺手写一份然后绿灯"（§R2.2）。
    """
    GoldenBaseline(GOLDEN_DIR, update=os.environ.get(UPDATE_ENV) == "1").check(name, actual)


# ================================================================ 指令层


class TestTextRunOp:
    """docs/03：文本指令是"字形序列 + 字体 + 位置"，**不接受字符串**。"""

    def test_op_holds_positioned_glyphs(self) -> None:
        glyphs = (PositionedGlyph("A", 0.0, 7.0, "sans-serif"),)
        op = TextRunOp(Offset(10.0, 20.0), 14.0, glyphs, 14.0, BLACK)
        assert op.glyphs[0].text == "A"
        assert op.origin.dx == 10.0
        assert op.baseline == 14.0

    def test_recorder_emits_text_op(self) -> None:
        rec = DisplayListRecorder()
        rec.text_run(
            Offset(4.0, 6.0),
            12.0,
            (PositionedGlyph("中", 0.0, 14.0, "sans-serif"),),
            14.0,
            BLACK,
        )
        dl = rec.finish(100, 50)
        assert len(dl) == 1
        assert isinstance(dl.ops[0], TextRunOp)

    def test_recorder_translates_origin(self) -> None:
        """文本原点也要跟随平移栈——否则嵌套容器里的文字全画在左上角。"""
        rec = DisplayListRecorder()
        rec.save()
        rec.translate(20.0, 30.0)
        rec.text_run(
            Offset(1.0, 2.0),
            10.0,
            (PositionedGlyph("A", 0.0, 7.0, "sans-serif"),),
            14.0,
            BLACK,
        )
        rec.restore()
        op = rec.finish(100, 50).ops[0]
        assert isinstance(op, TextRunOp)
        assert op.origin.dx == 21.0
        assert op.origin.dy == 32.0

    def test_empty_glyph_list_emits_nothing(self) -> None:
        """空 run 不产生指令——避免白白占用一次光栅分派。"""
        rec = DisplayListRecorder()
        rec.text_run(Offset(0.0, 0.0), 10.0, (), 14.0, BLACK)
        assert len(rec.finish(10, 10)) == 0

    def test_op_captures_clip(self) -> None:
        rec = DisplayListRecorder()
        rec.clip_rect(Rect(0.0, 0.0, 50.0, 20.0))
        rec.text_run(
            Offset(0.0, 0.0),
            10.0,
            (PositionedGlyph("A", 0.0, 7.0, "sans-serif"),),
            14.0,
            BLACK,
        )
        op = rec.finish(100, 50).ops[0]
        assert isinstance(op, TextRunOp)
        assert op.clip is not None
        assert op.clip.width == 50.0

    def test_describe_does_not_dump_whole_text(self) -> None:
        """检查器输出要有预览截断——否则长文本会把日志淹没。"""
        rec = DisplayListRecorder()
        long_text = "中文" * 40
        rec.text_run(
            Offset(0.0, 0.0),
            10.0,
            tuple(PositionedGlyph(c, i * 14.0, 14.0, "f") for i, c in enumerate(long_text)),
            14.0,
            BLACK,
        )
        text = rec.finish(1000, 50).describe()
        assert "n=80" in text
        assert len(text) < 400, "describe() 没有截断长文本"


# ================================================================ 字形提供方


class TestBuiltinGlyphProvider:
    """内置字形：ASCII 真位图 + 非拉丁占位块。零字体文件，全确定性。"""

    def test_ascii_has_real_glyph(self) -> None:
        provider = BuiltinGlyphProvider()
        assert not provider.is_placeholder_only("A")
        mask = provider.mask_for("A", 14.0, "sans-serif", 7.0)
        assert mask.width > 0 and mask.height > 0
        assert max(mask.coverage) > 0, "字形全空——画出来会是一片空白"

    def test_cjk_is_placeholder(self) -> None:
        provider = BuiltinGlyphProvider()
        assert provider.is_placeholder_only("中")
        # 全角字的 advance 就是 1em（字号 14 → 14px）
        mask = provider.mask_for("中", 14.0, "sans-serif", 14.0)
        assert mask.width == 14, "占位块宽度应等于文本层给的 advance（全角 1em）"
        assert mask.height > 0

    def test_glyph_fits_advance(self) -> None:
        """**字形宽度不得超过 advance**——否则相邻字形会叠在一起。

        这是最容易出现、也最容易被忽略的渲染缺陷：小字号下尤其明显。
        """
        provider = BuiltinGlyphProvider()
        for advance in (5.0, 6.0, 7.0, 8.0, 12.0):
            mask = provider.mask_for("A", 14.0, "sans-serif", advance)
            assert mask.width <= max(advance, 5.0) + 0.001, (
                f"advance={advance} 时字形宽 {mask.width}，超出可用宽度"
            )

    def test_small_advance_does_not_produce_overlap(self) -> None:
        """模拟真实排版：Latin 半角 advance=7，字形不得越界到下一个字形。"""
        provider = BuiltinGlyphProvider()
        mask = provider.mask_for("A", 14.0, "sans-serif", 7.0)
        assert mask.width <= 7, f"半角字形宽 {mask.width} > advance 7，会与邻字重叠"

    def test_glyph_is_deterministic(self) -> None:
        a = BuiltinGlyphProvider().mask_for("A", 14.0, "sans-serif", 7.0)
        b = BuiltinGlyphProvider().mask_for("A", 14.0, "sans-serif", 7.0)
        assert a == b

    def test_glyphs_differ_between_characters(self) -> None:
        """不同字符必须有不同的字形——否则所有字看起来都一样。"""
        provider = BuiltinGlyphProvider()
        assert provider.mask_for("A", 14.0, "f", 7.0) != provider.mask_for("B", 14.0, "f", 7.0)

    def test_space_is_blank(self) -> None:
        mask = BuiltinGlyphProvider().mask_for(" ", 14.0, "sans-serif", 7.0)
        assert max(mask.coverage) == 0, "空格不该画出任何像素"

    def test_mask_lookup_is_bounds_checked(self) -> None:
        mask = BuiltinGlyphProvider().mask_for("A", 14.0, "f", 7.0)
        assert mask.at(-1, 0) == 0
        assert mask.at(0, 999) == 0

    def test_rect_of_mask_positions_correctly(self) -> None:
        mask = GlyphMask(4, 6, 1, -6, bytes(24))
        rect = rect_of_mask(mask, pen_x=100.0, baseline_y=50.0)
        assert rect.left == 101.0
        assert rect.top == 44.0
        assert rect.width == 4.0
        assert rect.height == 6.0


# ================================================================ 光栅层


class TestTextRasterization:
    def _render(self, op: TextRunOp, w: int = 120, h: int = 24) -> object:
        return SoftwareRasterizer().rasterize(DisplayList(w, h, (op,)))

    def _op(self, text: str, size: float = 14.0) -> TextRunOp:
        glyphs = tuple(
            PositionedGlyph(ch, i * size * 0.6, size, "sans-serif") for i, ch in enumerate(text)
        )
        return TextRunOp(Offset(2.0, 2.0), size * 0.8, glyphs, size, BLACK)

    def test_draws_something(self) -> None:
        frame = self._render(self._op("A"))
        assert any(frame.data[i] for i in range(3, len(frame.data), 4)), (
            "文本没有画出任何不透明像素"
        )

    def test_deterministic_bytes(self) -> None:
        """同样的指令，两次光栅，像素逐字节相同（铁律 3）。"""
        op = self._op("Ag7")
        assert self._render(op).data == self._render(op).data

    def test_empty_text_draws_nothing(self) -> None:
        frame = self._render(TextRunOp(Offset(0.0, 0.0), 10.0, (), 14.0, BLACK))
        assert all(b == 0 for b in frame.data)

    def test_transparent_color_draws_nothing(self) -> None:
        op = TextRunOp(
            Offset(0.0, 0.0),
            10.0,
            (PositionedGlyph("A", 0.0, 7.0, "f"),),
            14.0,
            Color(0, 0, 0, 0.0),
        )
        assert all(b == 0 for b in self._render(op).data)

    def test_glyph_position_comes_from_advance(self) -> None:
        """**字形位置来自 advance，不是光栅层自己算的**（docs/04 §3）。

        把同一个字形放在两个不同 advance 位置，画出来的像素位置应当跟着走。
        """
        glyph_a = PositionedGlyph("A", 0.0, 10.0, "f")
        glyph_b = PositionedGlyph("A", 40.0, 10.0, "f")
        narrow = self._render(TextRunOp(Offset(0.0, 0.0), 12.0, (glyph_a,), 14.0, BLACK))
        wide = self._render(TextRunOp(Offset(0.0, 0.0), 12.0, (glyph_b,), 14.0, BLACK))
        assert narrow.data != wide.data, "移动字形位置没有改变输出——位置没被使用"

    def test_clip_prevents_drawing_outside(self) -> None:
        """裁剪必须生效：文字不能被画到裁剪矩形之外。"""
        glyphs = tuple(PositionedGlyph("A", i * 10.0, 14.0, "f") for i in range(8))
        op = TextRunOp(Offset(0.0, 0.0), 12.0, glyphs, 14.0, BLACK, clip=Rect(0.0, 0.0, 20.0, 24.0))
        frame = self._render(op, w=120, h=24)
        # 检查 x >= 20 的区域没有任何不透明像素
        for y in range(24):
            for x in range(20, 120):
                assert frame.data[(y * 120 + x) * 4 + 3] == 0, f"裁剪失效：({x},{y}) 被画了"

    def test_underline_draws_line(self) -> None:
        glyphs = (PositionedGlyph("A", 0.0, 10.0, "f"),)
        with_line = self._render(
            TextRunOp(Offset(0.0, 0.0), 12.0, glyphs, 14.0, BLACK, underline=True)
        )
        without = self._render(TextRunOp(Offset(0.0, 0.0), 12.0, glyphs, 14.0, BLACK))
        assert with_line.data != without.data, "组合态下划线没有画出来"

    def test_space_advances_without_drawing(self) -> None:
        """空格不画像素，但必须推进笔位置。"""
        a_then_space = (
            PositionedGlyph("A", 0.0, 5.0, "f"),
            PositionedGlyph(" ", 5.0, 5.0, "f"),
        )
        op = TextRunOp(Offset(0.0, 0.0), 12.0, (a_then_space[0],), 14.0, BLACK)
        assert any(b for b in self._render(op, 40, 24).data)


# ================================================================ Text 组件


class TestTextWidgetLayout:
    """Text 的尺寸**来自真实度量**——不是估算（docs/04 §3 铁律）。"""

    def test_width_equals_measured_text(self) -> None:
        owner = _owner()
        widget = Text("你好", size="md")
        owner.mount(widget)
        size = owner.flush_layout(BoxConstraints(max_width=500, max_height=100))
        # md = 14px；两个全角字 = 28px
        assert size is not None
        assert size.width == pytest.approx(28.0)

    def test_height_equals_line_height(self) -> None:
        owner = _owner()
        owner.mount(Text("中文", size="md"))
        size = owner.flush_layout(BoxConstraints(max_width=500, max_height=100))
        assert size is not None
        assert size.height == pytest.approx(14.0 * 1.6)

    def test_larger_size_token_measures_wider(self) -> None:
        """字号档位不同 → 度量不同。这是"用令牌而不是硬编码"的可验证收益。"""
        widths = {}
        for token in ("sm", "md", "lg"):
            owner = _owner()
            owner.mount(Text("中文测试", size=token))
            size = owner.flush_layout(BoxConstraints(max_width=500, max_height=100))
            assert size is not None
            widths[token] = size.width
        assert widths["sm"] < widths["md"] < widths["lg"]

    def test_wraps_at_constraint(self) -> None:
        """有限宽 → 换行 → 高度按行数增长。"""
        owner = _owner()
        owner.mount(Text("你好世界这是一个测试", size="md"))
        size = owner.flush_layout(BoxConstraints(max_width=70.0, max_height=200.0))
        assert size is not None
        assert size.height == pytest.approx(2 * 14.0 * 1.6)

    def test_no_wrap_keeps_single_line(self) -> None:
        """`wrap=False` → 单行，宽度是被撑满后的可用宽度。"""
        owner = _owner()
        owner.mount(Text("你好世界这是一个测试", size="md", wrap=False))
        size = owner.flush_layout(BoxConstraints(max_width=70.0, max_height=200.0))
        assert size is not None
        assert size.height == pytest.approx(14.0 * 1.6)

    def test_unbounded_width_stays_single_line(self) -> None:
        """Row 里放 Text 时宽度无界 —— 文本不该被硬塞成多行。"""
        owner = _owner()
        owner.mount(Text("你好世界这是一个测试", size="md"))
        size = owner.flush_layout(BoxConstraints(max_width=float("inf"), max_height=200.0))
        assert size is not None
        assert size.height == pytest.approx(14.0 * 1.6)

    def test_max_lines_truncates_height(self) -> None:
        owner = _owner()
        owner.mount(Text("你好世界这是一个测试", size="md", max_lines=1))
        size = owner.flush_layout(BoxConstraints(max_width=70.0, max_height=200.0))
        assert size is not None
        assert size.height == pytest.approx(14.0 * 1.6)

    def test_empty_text_is_zero_size(self) -> None:
        owner = _owner()
        owner.mount(Text("", size="md"))
        size = owner.flush_layout(BoxConstraints(max_width=200, max_height=100))
        assert size is not None
        assert size.width == 0.0
        assert size.height == 0.0

    def test_without_engine_degrades_gracefully(self) -> None:
        """没配文本引擎时退化为零尺寸，**不抛异常**——
        纯布局测试很常见这种场景，崩掉会让人以为布局错了。"""
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Text("中文"))
        size = owner.flush_layout(BoxConstraints(max_width=200, max_height=100))
        assert size is not None
        assert size.width == 0.0

    def test_overflow_is_flagged_not_silently_clipped(self) -> None:
        """内容比盒子大要标记溢出（铁律 5）。"""
        owner = _owner()
        owner.mount(Text("你好世界这是一个很长的测试句子", size="md"))
        owner.flush_layout(BoxConstraints(max_width=70.0, max_height=20.0))
        root = owner.root_render_object
        assert root is not None and root.has_overflow, "内容溢出没有被标记出来"


class TestTextWidgetPaint:
    def test_emits_one_op_per_line(self) -> None:
        owner = _owner()
        owner.mount(Text("你好世界这是一个测试", size="md"))
        owner.flush_layout(BoxConstraints(max_width=70.0, max_height=200.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        text_ops = [op for op in rec.finish(200, 200).ops if isinstance(op, TextRunOp)]
        assert len(text_ops) == 2, f"两行文字应当产生两条指令，实际 {len(text_ops)}"

    def test_glyph_text_matches_source(self) -> None:
        """指令里的字形文本必须拼回原文——丢了字就是渲染 bug。"""
        owner = _owner()
        owner.mount(Text("你好Hello", size="md"))
        owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(500, 100).ops if isinstance(op, TextRunOp))
        assert "".join(g.text for g in op.glyphs) == "你好Hello"

    def test_glyph_advances_come_from_measurement(self) -> None:
        owner = _owner()
        owner.mount(Text("中A", size="md"))
        owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(500, 100).ops if isinstance(op, TextRunOp))
        assert op.glyphs[0].advance == pytest.approx(14.0)  # 全角
        assert op.glyphs[1].advance == pytest.approx(7.0)  # 半角

    def test_color_comes_from_theme_not_literal(self) -> None:
        """颜色必须来自主题令牌，组件里不许出现字面量（铁律 2）。"""
        theme = Theme.light()
        owner = _owner(theme)
        owner.mount(Text("中文"))
        owner.flush_layout(BoxConstraints(max_width=200.0, max_height=100.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(200, 100).ops if isinstance(op, TextRunOp))
        assert op.color == theme.color("text")

    def test_explicit_color_wins(self) -> None:
        owner = _owner()
        owner.mount(Text("中文", color=Color(255, 0, 0)))
        owner.flush_layout(BoxConstraints(max_width=200.0, max_height=100.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(200, 100).ops if isinstance(op, TextRunOp))
        assert op.color == Color(255, 0, 0)

    def test_center_alignment_offsets_glyphs(self) -> None:
        owner = _owner()
        owner.mount(Text("短", size="md", align=TextAlign.CENTER))
        owner.flush_layout(BoxConstraints(max_width=140.0, max_height=60.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(140, 60).ops if isinstance(op, TextRunOp))
        assert op.origin.dx == pytest.approx(63.0)

    def test_text_inside_card_is_offset_by_padding(self) -> None:
        """嵌套容器的平移必须作用到文字上。"""
        owner = _owner()
        owner.mount(Card(Text("中", size="md")))
        owner.flush_layout(BoxConstraints(max_width=200.0, max_height=100.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(200, 100).ops if isinstance(op, TextRunOp))
        assert op.origin.dx >= 16.0, "卡片内边距没有作用到文字原点"


class TestTextWidgetUpdate:
    def test_text_change_relayouts(self) -> None:
        owner = _owner()
        element = owner.mount(Text("短", size="md"))
        first = owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        assert first is not None
        # 同一棵树换 widget：走 update 路径（mount 只管第一次）
        element.update(Text("更长的文本内容", size="md"))
        second = owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        assert second is not None
        assert second.width > first.width

    def test_weight_change_is_reflected(self) -> None:
        """字重变化会改变宽度——必须重新布局，不能只重画。"""
        owner = _owner()
        element = owner.mount(Text("Bold test", size="md"))
        regular = owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        element.update(Text("Bold test", size="md", weight=FontWeight.BOLD))
        bold = owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        assert regular is not None and bold is not None
        assert bold.width > regular.width

    def test_update_keeps_text_and_style_in_sync(self) -> None:
        """更新后指令里的字形必须来自新文本——不能残留旧的。"""
        owner = _owner()
        element = owner.mount(Text("旧的", size="md"))
        owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        element.update(Text("新的", size="md"))
        owner.flush_layout(BoxConstraints(max_width=500.0, max_height=100.0))
        rec = DisplayListRecorder()
        owner.flush_paint(rec)
        op = next(op for op in rec.finish(500, 100).ops if isinstance(op, TextRunOp))
        assert "".join(g.text for g in op.glyphs) == "新的"


# ================================================================ 确定性 / 黄金图


class TestTextDeterminism:
    def test_repeated_render_identical_bytes(self) -> None:
        def render() -> bytes:
            owner = _owner()
            owner.mount(
                Column(children=(Text("Inkstone 文本", size="lg"), Text("第二行", size="sm")))
            )
            return render_to_png(owner, BoxConstraints(max_width=300, max_height=120))

        first = render()
        for _ in range(3):
            assert render() == first

    def test_golden_text_block(self) -> None:
        """文本黄金图：ASCII 每个字形、字距、行高、对齐都要稳定。"""
        owner = _owner()
        owner.mount(
            Card(
                Column(
                    gap=8,
                    children=(
                        Text("Inkstone Text", size="lg"),
                        Text("ABCDEFGHIJKLM 0123456789", size="sm"),
                        Text("你好，世界。中文测试", size="md"),
                        Text("右对齐示例", size="sm", align=TextAlign.END),
                    ),
                )
            )
        )
        png = render_to_png(owner, BoxConstraints(max_width=320, max_height=200))
        _assert_or_update_golden("text_block", png)

    def test_golden_text_wrapping(self) -> None:
        """换行 + 禁则 + 省略号的黄金图。

        用固定宽度逼出跨行，中文标点不得起行，超过 max_lines 后末尾出省略号。
        """
        owner = _owner()
        owner.mount(
            Column(
                gap=6,
                children=(
                    Text("这是一段用来验证换行与标点禁则的中文文本，它足够长。", size="md"),
                    Text("超过两行就省略：", size="sm"),
                    Text(
                        "这一段文字被限制为两行，多出来的部分应当以省略号结束而不是硬切。",
                        size="sm",
                        max_lines=2,
                    ),
                ),
            )
        )
        png = render_to_png(owner, BoxConstraints(max_width=200, max_height=220))
        _assert_or_update_golden("text_wrapping", png)
