"""Backend 协议符合性测试（R5.9）。

`SDL2Backend` 曾经自称实现了 `Backend` 协议、其实缺了全部字体度量方法——
`runtime_checkable` 对这种"缺方法"的摆设检查是放行的，所以这里**显式**
遍历协议的每个成员，逐一断言实现存在且可调用。

红绿可控的验证也在本文件：`assert_implements` 对缺方法的假类必须报出来——
门禁自身如果不能红，它的绿就没有意义。
"""

from __future__ import annotations

from inkstone.backend import Backend, HeadlessBackend, MetricsProvider, SDL2Backend
from inkstone.gfx import RasterFrameRenderer, SoftwareRasterizer


def _protocol_members(protocol: type) -> dict[str, object]:
    """协议自己声明的成员（不含继承来的、不含私有的）。"""
    return {name: value for name, value in vars(protocol).items() if not name.startswith("_")}


def assert_implements(impl: type, protocol: type) -> list[str]:
    """返回 impl 缺失/不可调用的协议成员名列表。空列表 = 符合。"""
    missing: list[str] = []
    for name in _protocol_members(protocol):
        member = getattr(impl, name, None)
        if member is None:
            missing.append(name)
        elif not (callable(member) or isinstance(member, property)):
            missing.append(f"{name}（存在但不可调用）")
    return missing


class TestBackendProtocolConformance:
    def test_headless_implements_backend(self) -> None:
        assert assert_implements(HeadlessBackend, Backend) == []

    def test_sdl2_implements_backend(self) -> None:
        """R5.9 的核心断言：SDL2 后端真的满足它自称的协议。"""
        assert assert_implements(SDL2Backend, Backend) == []

    def test_backend_no_longer_includes_metrics(self) -> None:
        """R5.9：度量从 Backend 拆出——协议里不该再有度量方法。"""
        members = _protocol_members(Backend)
        for name in ("resolve_font", "measure_text", "shape_line", "has_family", "has_glyph"):
            assert name not in members, f"{name} 不该还在 Backend 协议里"

    def test_backend_includes_ime_and_frame_seam(self) -> None:
        """R5.7 / R5.8：IME 三方法与帧边界是协议的一部分。"""
        members = _protocol_members(Backend)
        for name in (
            "start_text_input",
            "stop_text_input",
            "set_ime_rect",
            "begin_frame",
            "end_frame",
        ):
            assert name in members, f"{name} 应在 Backend 协议里"

    def test_headless_still_implements_metrics_provider(self) -> None:
        """拆分不等于丢掉：headless 依然是度量提供方（测试靠它注入字体）。"""
        assert assert_implements(HeadlessBackend, MetricsProvider) == []

    def test_the_checker_itself_can_go_red(self) -> None:
        """门禁自身必须能红：缺方法的类必须被指名报出来。"""

        class Broken:
            @property
            def name(self) -> str:
                return "broken"

        missing = assert_implements(Broken, Backend)
        assert "initialize" in missing
        assert "start_text_input" in missing
        assert len(missing) == len(_protocol_members(Backend)) - 1

    def test_sdl2_is_not_a_metrics_provider(self) -> None:
        """窗口后端不再假装会量字。"""
        assert assert_implements(SDL2Backend, MetricsProvider) != []


class TestFrameRendererSeam:
    """R5.8：RasterFrameRenderer 把光栅器适配进后端的帧接缝。"""

    def test_software_rasterizer_adapts(self) -> None:
        from inkstone.backend import FrameRenderer

        renderer = RasterFrameRenderer(SoftwareRasterizer())
        assert assert_implements(RasterFrameRenderer, FrameRenderer) == []
        renderer.begin_frame(100.0, 50.0, 2.0)
        renderer.end_frame()
        frame = renderer.raster.screenshot()
        # 100×50 逻辑像素 × 2.0 缩放 = 200×100 物理像素
        assert (frame.width, frame.height) == (200, 100)

    def test_sdl2_holds_renderer_by_composition(self) -> None:
        renderer = RasterFrameRenderer(SoftwareRasterizer())
        backend = SDL2Backend(renderer=renderer)
        assert backend._renderer is renderer
