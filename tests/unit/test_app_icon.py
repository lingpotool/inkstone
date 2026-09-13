"""应用图标管线（R10.3）。

图标不是仓库里的二进制，而是**画出来的**：显示列表 → 软件光栅 → RGBA。
所以这里断言三件事：几何对（圆角透明、中心有墨点）、逐字节确定、ICO 容器合法。
"""

from __future__ import annotations

import struct

import pytest

from inkstone.app import ICON_SIZES, app_icon_ico, app_icon_png, app_icon_rgba
from inkstone.backend import BackendError, HeadlessBackend
from inkstone.backend.base import WindowSpec
from inkstone.gfx.raster import FrameBuffer

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _frame(size: int) -> FrameBuffer:
    return FrameBuffer(size, size, bytearray(app_icon_rgba(size)))


class TestIconPixels:
    def test_size_is_exact(self) -> None:
        assert len(app_icon_rgba(32)) == 32 * 32 * 4

    def test_corner_is_transparent(self) -> None:
        """圆角外必须真正透明——否则任务栏会出现一个方角底色块。"""
        assert _frame(64).pixel(0, 0) == (0, 0, 0, 0)

    def test_center_is_the_ink_dot(self) -> None:
        r, g, b, a = _frame(64).pixel(32, 32)
        assert a == 255
        assert (r, g, b) == (0xF2, 0xEC, 0xDD)

    def test_stone_background_is_painted(self) -> None:
        """四边中点附近应是实心砚石，不是透明。"""
        r, g, b, a = _frame(64).pixel(32, 4)
        assert a == 255
        assert (r, g, b) == (0x2F, 0x3B, 0x52)

    def test_pool_ring_has_light_pixels(self) -> None:
        """环上某点必须是宣纸白——证明描边真的画出来了。"""
        frame = _frame(64)
        light = sum(
            1 for y in range(64) for x in range(64) if frame.pixel(x, y)[:3] == (0xF2, 0xEC, 0xDD)
        )
        # 环 + 中心点，远多于中心那一个小圆
        assert light > 100

    def test_too_small_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="至少 8px"):
            app_icon_rgba(4)

    def test_generation_is_deterministic(self) -> None:
        """黄金图与"换台机器同字节"都建立在这条上。"""
        assert app_icon_rgba(48) == app_icon_rgba(48)


class TestIconContainers:
    def test_png_signature(self) -> None:
        assert app_icon_png(32)[:8] == _PNG_SIGNATURE

    def test_ico_header(self) -> None:
        for sizes in (ICON_SIZES, (16, 32)):
            data = app_icon_ico(sizes)
            reserved, kind, count = struct.unpack_from("<HHH", data, 0)
            assert (reserved, kind, count) == (0, 1, len(sizes))

    def test_ico_entries_point_at_pngs(self) -> None:
        sizes = (16, 32, 256)
        data = app_icon_ico(sizes)
        for index, size in enumerate(sizes):
            entry = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
            width, height, _palette, _res, planes, depth, length, offset = entry
            assert width == (0 if size >= 256 else size)
            assert height == (0 if size >= 256 else size)
            assert (planes, depth) == (1, 32)
            assert data[offset : offset + 8] == _PNG_SIGNATURE
            assert length > 8

    def test_ico_is_deterministic(self) -> None:
        assert app_icon_ico((16, 32)) == app_icon_ico((16, 32))


class TestBackendReceivesIcon:
    def test_headless_records_icon(self) -> None:
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec(width=100, height=100))
        rgba = app_icon_rgba(16)
        backend.set_icon(window, 16, 16, rgba)
        assert backend.window_icon(window) == (16, 16, rgba)

    def test_headless_rejects_wrong_pixel_count(self) -> None:
        """长度不对是调用方的 bug，必须当场报错而不是画出一张歪图。"""
        backend = HeadlessBackend()
        backend.initialize()
        window = backend.create_window(WindowSpec(width=100, height=100))
        with pytest.raises(BackendError, match="图标像素长度"):
            backend.set_icon(window, 16, 16, b"\x00" * 10)
