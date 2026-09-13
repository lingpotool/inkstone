"""真窗口 GL 上屏（R8.6）。

链路：SDL 建 OPENGL 窗口 → `sdl_gl_driver` 挂到它的上下文 → GLRasterBackend
渲染进离屏 FBO → `end_frame` blit 到默认帧缓冲并换链。

这是**真机测试**：需要 SDL2（`pip install "inkstone[sdl2]"`）与可用 GL。
缺任一条件明确 skip——CI 上就是这种情形，"没跑"与"过了"必须分得开。
"""

from __future__ import annotations

import pytest

from inkstone.backend.base import BackendError, WindowSpec
from inkstone.gfx import DisplayList, FillRectOp, GLRasterBackend
from inkstone.gfx.color import Color
from inkstone.layout.types import Rect, Size

BG = Color(20, 24, 40, 1.0)
RED = Color(255, 0, 0, 1.0)


@pytest.fixture
def sdl_window():
    from inkstone.backend.sdl2 import SDL2Backend

    backend = SDL2Backend()
    try:
        backend.initialize()
    except BackendError as error:
        pytest.skip(f"没有可用的 SDL2：{error}")
    window = backend.create_window(
        WindowSpec(
            title="inkstone present test", width=160, height=120, resizable=False, opengl=True
        )
    )
    yield backend, window
    backend.destroy_window(window)
    backend.shutdown()


class TestWindowSpecOpenGL:
    def test_opengl_flag_is_part_of_the_window_spec(self) -> None:
        """能力声明在数据里，而不是靠后端偷偷嗅探（与 R5.9 的协议口径一致）。"""
        assert WindowSpec().opengl is False
        assert WindowSpec(opengl=True).opengl is True


class TestSDLGLPresent:
    def test_render_and_present_to_a_real_window(self, sdl_window) -> None:
        from inkstone.backend.gl_wgl import GLUnavailableError, sdl_gl_driver

        backend, window = sdl_window
        try:
            driver = sdl_gl_driver(backend, window)
        except GLUnavailableError as error:
            pytest.skip(f"本机 GL 不可用：{error}")

        raster = GLRasterBackend(driver)
        raster.begin_frame(Size(160.0, 120.0), 1.0)
        raster.execute(
            DisplayList(
                160,
                120,
                (
                    FillRectOp(Rect(0.0, 0.0, 160.0, 120.0), BG),
                    FillRectOp(Rect(30.0, 20.0, 60.0, 40.0), RED),
                ),
            )
        )
        raster.end_frame()  # blit + SwapWindow：真的上屏了

        # 从离屏 FBO 读回断言图形正确（换链后默认帧缓冲内容不再保证）
        frame = raster.screenshot()
        assert frame.pixel(60, 40)[:3] == (255, 0, 0)
        assert frame.pixel(5, 5)[:3] == (20, 24, 40)
        assert driver._gl.glGetError() == 0
        driver.close()

    def test_frame_can_be_presented_repeatedly(self, sdl_window) -> None:
        backend, window = sdl_window
        from inkstone.backend.gl_wgl import GLUnavailableError, sdl_gl_driver

        try:
            driver = sdl_gl_driver(backend, window)
        except GLUnavailableError as error:
            pytest.skip(f"本机 GL 不可用：{error}")
        raster = GLRasterBackend(driver)
        for offset in (0.0, 20.0):
            raster.begin_frame(Size(160.0, 120.0), 1.0)
            raster.execute(
                DisplayList(
                    160,
                    120,
                    (FillRectOp(Rect(offset, offset, 40.0, 40.0), RED),),
                )
            )
            raster.end_frame()
        assert driver._gl.glGetError() == 0
        driver.close()
