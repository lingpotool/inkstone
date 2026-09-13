"""R8.2 Windows WGL 真机 GL 驱动测试（docs/22）。

这些测试**真的建 GL 上下文、真的渲染、真的回读像素**——它们只在 Windows +
有 OpenGL 驱动的机器上运行（本机验证环境：NVIDIA RTX 3060 / GL 4.6）。
其余平台自动跳过：GL 是可选的加速路径，软件光栅永远是兜底。

跑不到时**必须显示为 skip**，不能悄悄绿——"没跑"与"过了"是两件事。
"""

from __future__ import annotations

import sys

import pytest

from inkstone.gfx import (
    DisplayList,
    FillRectOp,
    FrameBuffer,
    GLRasterBackend,
    PositionedGlyph,
    RasterBackend,
    StrokeRectOp,
    TextRunOp,
    encode_png,
)
from inkstone.gfx.color import Color
from inkstone.layout.types import Offset, Rect, Size

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="WGL 是 Windows 专用驱动；Linux GLX/EGL、macOS CGL 属后续",
)

if sys.platform == "win32":  # 只在 Windows 上导入（ctypes.wintypes 在别处不可用）
    from inkstone.backend.gl_wgl import WglGLDriver, windows_gl_driver

RED = Color(255, 0, 0, 1.0)
BLUE = Color(0, 128, 255, 1.0)


@pytest.fixture
def driver():
    if sys.platform != "win32":
        pytest.skip("非 Windows")
    if not WglGLDriver.is_available():
        pytest.skip("本机没有可用的 OpenGL 驱动")
    made = windows_gl_driver()
    assert made is not None
    yield made
    made.close()


def _render(driver, width: int, height: int, *ops: object) -> FrameBuffer:
    backend = GLRasterBackend(driver)
    backend.begin_frame(Size(float(width), float(height)), 1.0)
    backend.execute(DisplayList(width, height, tuple(ops)))  # type: ignore[arg-type]
    backend.end_frame()
    return backend.screenshot()


class TestRealGL:
    def test_driver_reports_a_real_context(self) -> None:
        driver = windows_gl_driver()
        if driver is None:
            pytest.skip("本机没有可用的 OpenGL 驱动")
        version = driver._gl.glGetString(0x1F02)
        try:
            assert version and version.startswith(b"4."), f"意外的 GL 版本：{version!r}"
        finally:
            driver.close()

    def test_fill_rect_lands_at_top_left_after_y_flip(self, driver) -> None:
        """显示列表是左上原点；GL 是左下原点。翻转错了图形会上下颠倒。"""
        frame = _render(driver, 64, 48, FillRectOp(Rect(8, 8, 24, 20), RED))
        assert frame.pixel(20, 18) == (255, 0, 0, 255), "矩形内部应当是红色"
        assert frame.pixel(20, 6) == (0, 0, 0, 0), "矩形上方应当是透明"
        assert frame.pixel(20, 40) == (0, 0, 0, 0), "矩形下方应当是透明"
        assert frame.pixel(0, 0) == (0, 0, 0, 0)

    def test_stroke_ring_has_transparent_center(self, driver) -> None:
        """描边向内侧生长：外圈内、内圈外，中心必须透（否则画成了实心）。"""
        frame = _render(
            driver, 64, 48, StrokeRectOp(Rect(8, 8, 40, 32), BLUE, width=4.0, radius=0.0)
        )
        assert frame.pixel(10, 20)[3] > 200, "左边框应当有色"
        assert frame.pixel(28, 20)[3] == 0, "中心必须透明"
        assert frame.pixel(8, 8)[3] > 200, "外角应当有色"

    def test_glyph_draws_ink(self, driver) -> None:
        op = TextRunOp(
            origin=Offset(8.0, 8.0),
            baseline=12.0,
            glyphs=(PositionedGlyph("A", 0.0, 8.0),),
            size=14.0,
            color=Color(0, 0, 0, 1.0),
        )
        frame = _render(driver, 48, 40, op)
        inked = sum(1 for y in range(40) for x in range(48) if frame.pixel(x, y)[3] > 0)
        assert inked > 10, f"字形几乎没有墨迹（{inked} 像素）——文本路径没画出来"

    def test_op_clip_scissors_the_drawing(self, driver) -> None:
        """op.clip 必须真的裁掉区域外的绘制（GL 侧是 scissor）。"""
        frame = _render(
            driver, 64, 48, FillRectOp(Rect(0, 0, 64, 48), RED, clip=Rect(0, 0, 16, 16))
        )
        assert frame.pixel(8, 8) == (255, 0, 0, 255), "裁剪区内应当有色"
        assert frame.pixel(32, 24)[3] == 0, "裁剪区外必须透明"

    def test_no_gl_error_after_a_frame(self, driver) -> None:
        """一帧跑完 GL 错误状态必须是 0——驱动里的 ctypes 签名/状态错误靠这条兜底。"""
        _render(driver, 32, 32, FillRectOp(Rect(0, 0, 10, 10), RED))
        error = driver._gl.glGetError()
        assert error == 0, f"GL 错误 0x{error:04X}"

    def test_texture_lifecycle(self, driver) -> None:
        handle = driver.create_texture(2, 2, bytes([255, 0, 0, 255]) * 4)
        assert handle > 0
        driver.destroy_texture(handle)
        driver.destroy_texture(handle)  # 幂等

    def test_backend_satisfies_raster_protocol(self, driver) -> None:
        assert isinstance(GLRasterBackend(driver), RasterBackend)

    def test_readback_roundtrips_through_png(self, driver) -> None:
        frame = _render(driver, 8, 6, FillRectOp(Rect(0, 0, 8, 6), RED))
        png = encode_png(frame.width, frame.height, bytes(frame.data))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
