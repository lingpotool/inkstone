"""R7.3 DPI 缩放接线的回归测试（docs/21）。

两头早就通了（后端的 per-window DPI 变化事件 R5.10、录制器的仿射缩放 R3.4），
缺的是中间那根线。这一包把线接上：

    后端档位 → BuildOwner.begin_frame(dpi_scale) → flush_paint 在根上压缩放
             → 显示列表是设备像素 → 光栅缓冲 逻辑尺寸 × scale

关键约束（docs/02 §6）：**布局永远在逻辑像素里算**，物理 = 逻辑 × dpi_scale，
换算只发生在录制/光栅那一层；事件坐标也已经是逻辑像素，保持一致。
"""

from __future__ import annotations

import pytest

from inkstone.backend import HeadlessBackend, WindowSpec
from inkstone.backend.base import WindowKind
from inkstone.core import BuildOwner, FrameError
from inkstone.devtools import render_to_display_list, render_to_framebuffer, render_to_png
from inkstone.gfx import TextRunOp
from inkstone.layout import BoxConstraints
from inkstone.style import Theme
from inkstone.text import TextEngine
from inkstone.widgets import Box, Text

SMALL = BoxConstraints(max_width=100.0, max_height=50.0)


def _owner(*, text_engine: TextEngine | None = None) -> BuildOwner:
    owner = BuildOwner(theme=Theme.light(), text_engine=text_engine)
    # radius=0.0：直角方块才能用角像素断言"铺满了缩放后的画布"
    owner.mount(Box(width=100.0, height=50.0, color=Theme.light().color("primary"), radius=0.0))
    return owner


class TestFramebufferScales:
    def test_150_percent_triples_the_buffer_geometry(self) -> None:
        frame = render_to_framebuffer(_owner(), SMALL, dpi_scale=1.5)
        assert (frame.width, frame.height) == (150, 75)

    def test_125_percent_rounds_up(self) -> None:
        frame = render_to_framebuffer(_owner(), SMALL, dpi_scale=1.25)
        assert (frame.width, frame.height) == (125, 63)

    def test_content_fills_the_scaled_canvas(self) -> None:
        """100×50 的逻辑盒子在 1.5 档下要铺满 150×75 的设备像素。"""
        theme = Theme.light()
        frame = render_to_framebuffer(_owner(), SMALL, dpi_scale=1.5)
        expected = theme.color("primary")
        rgba = (expected.r, expected.g, expected.b, 255)
        assert frame.pixel(149, 74) == rgba, "右下角应当是盒子，而不是背景"
        assert frame.pixel(0, 0) == rgba

    def test_default_uses_owner_scale(self) -> None:
        owner = _owner()
        owner.dpi_scale = 1.5
        frame = render_to_framebuffer(owner, SMALL)
        assert (frame.width, frame.height) == (150, 75)

    def test_invalid_scale_is_rejected(self) -> None:
        owner = _owner()
        with pytest.raises(FrameError):
            owner.dpi_scale = 0.0


class TestLayoutStaysLogical:
    def test_layout_size_is_unaffected_by_scale(self) -> None:
        owner = _owner()
        size = owner.begin_frame(SMALL, dpi_scale=1.5)
        assert size is not None
        assert (size.width, size.height) == pytest.approx((100.0, 50.0)), (
            "布局必须是逻辑像素——缩放只影响光栅，不该改变几何"
        )

    def test_display_list_is_in_device_pixels(self) -> None:
        owner = _owner()
        display_list = render_to_display_list(owner, SMALL, dpi_scale=1.5)
        assert (display_list.width, display_list.height) == (150, 75)


class TestTextRasterizesAtPhysicalResolution:
    """文字要在物理分辨率上光栅化才不模糊——不能把 1.0 的画面整体拉伸。"""

    @staticmethod
    def _text_size(dpi_scale: float) -> float:
        backend = HeadlessBackend()
        owner = BuildOwner(theme=Theme.light(), text_engine=TextEngine(backend))
        owner.mount(Text("inkstone 中文", size="md"))
        display_list = render_to_display_list(
            owner,
            BoxConstraints(max_width=300.0, max_height=80.0),
            background=False,
            dpi_scale=dpi_scale,
        )
        op = next(op for op in display_list.ops if isinstance(op, TextRunOp))
        return op.size

    def test_glyph_em_scales_with_dpi(self) -> None:
        base = self._text_size(1.0)
        scaled = self._text_size(1.5)
        assert scaled == pytest.approx(base * 1.5), (
            "字形 em 必须随档位缩放——否则掩码还是低分辨率的，文字发虚"
        )


class TestDpiChangedEvent:
    def test_dpi_changed_event_updates_the_next_frame(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec(width=100.0, height=50.0))
        owner = _owner()

        assert owner.dpi_scale == pytest.approx(1.0)
        backend.set_dpi_scale(window, 1.5)
        for event in backend.pump_events():
            owner.handle_window_event(event)

        assert owner.dpi_scale == pytest.approx(1.5)
        frame = render_to_framebuffer(owner, SMALL)
        assert (frame.width, frame.height) == (150, 75), "下一帧必须用新档位"

    def test_non_dpi_window_events_are_ignored(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec(width=100.0, height=50.0))
        owner = _owner()

        backend.resize_window(window, 200.0, 100.0)
        for event in backend.pump_events():
            assert event.kind is WindowKind.RESIZED
            owner.handle_window_event(event)

        assert owner.dpi_scale == pytest.approx(1.0)


class TestPngOutputScales:
    def test_png_bytes_are_larger_at_150_percent(self) -> None:
        small = render_to_png(_owner(), SMALL, dpi_scale=1.0)
        large = render_to_png(_owner(), SMALL, dpi_scale=1.5)
        assert len(large) > len(small)
