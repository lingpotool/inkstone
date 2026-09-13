"""R8.1 GL 后端骨架的回归测试（docs/22）。

本机与 CI 都没有可用的 GL 上下文，所以**逻辑层用假驱动测**：
`FakeDriver` 只记录被调用的方法，不碰 GPU。这样帧状态机、裁剪语义、
圆角钳制、文本取掩码规则、资源生命周期、读回校验全都能被钉住——
真机驱动（R8.2）只需要把同样的调用搬给 GL，本模块一行不用改。

测试失败时先问一句：错的是后端逻辑，还是假驱动记录的调用序列？
两者都是"会红"的正当理由，但修法不同。
"""

from __future__ import annotations

import pytest

from inkstone.gfx import (
    DisplayList,
    FillRectOp,
    FrameBuffer,
    GLRasterBackend,
    GlyphProvider,
    PathData,
    PositionedGlyph,
    RasterError,
    StrokeRectOp,
    TextRunOp,
    encode_png,
    glyph_mask_plan,
)
from inkstone.gfx.color import Color
from inkstone.gfx.display_list import PATH_VERBS, PathFillOp
from inkstone.layout.types import Offset, Rect, Size

RED = Color(255, 0, 0)
SIZE = Size(100.0, 50.0)


class FakeDriver:
    """只记录调用，不画任何东西。像素全 0，长度按 begin 的物理尺寸给足。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self._width = 0
        self._height = 0
        self._textures: dict[int, tuple[int, int, bytes]] = {}
        self._next_texture = 1
        self.pixel_override: bytes | None = None
        self.began = 0
        self.ended = 0

    def begin(self, width: int, height: int, scale: float) -> None:
        self.calls.append(("begin", width, height, scale))
        self._width, self._height = width, height
        self.began += 1

    def clear(self, color: Color) -> None:
        self.calls.append(("clear", color))

    def end(self) -> None:
        self.calls.append(("end",))
        self.ended += 1

    def set_scissor(self, rect: Rect | None) -> None:
        self.calls.append(("scissor", rect))

    def fill_rect(self, rect: Rect, radius: float, color: Color) -> None:
        self.calls.append(("fill", rect, radius, color))

    def stroke_rect(self, rect: Rect, radius: float, width: float, color: Color) -> None:
        self.calls.append(("stroke", rect, radius, width, color))

    def draw_glyph(self, mask: object, pen_x: float, baseline_y: float, color: Color) -> None:
        self.calls.append(("glyph", pen_x, baseline_y, color))

    def create_texture(self, width: int, height: int, pixels: bytes) -> int:
        handle = self._next_texture
        self._next_texture += 1
        self._textures[handle] = (width, height, pixels)
        self.calls.append(("create_texture", handle, width, height))
        return handle

    def destroy_texture(self, handle: int) -> None:
        self._textures.pop(handle, None)
        self.calls.append(("destroy_texture", handle))

    def read_pixels(self) -> bytes:
        if self.pixel_override is not None:
            return self.pixel_override
        return bytes(self._width * self._height * 4)

    # 便捷查询
    def kinds(self) -> list[str]:
        return [str(call[0]) for call in self.calls]

    def scissor_rects(self) -> list[Rect | None]:
        return [call[1] for call in self.calls if call[0] == "scissor"]

    def fills(self) -> list[tuple[object, ...]]:
        return [call for call in self.calls if call[0] == "fill"]


def _dl(width: int, height: int, *ops: object) -> DisplayList:
    return DisplayList(width, height, tuple(ops))  # type: ignore[arg-type]


def _glyph(text: str, x: float = 0.0, advance: float = 6.0, **kwargs: object) -> PositionedGlyph:
    return PositionedGlyph(text=text, x=x, advance=advance, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 帧生命周期


class TestFrameLifecycle:
    def test_execute_requires_begin(self) -> None:
        backend = GLRasterBackend(FakeDriver())
        with pytest.raises(RasterError):
            backend.execute(_dl(100, 50))

    def test_begin_twice_is_an_error(self) -> None:
        backend = GLRasterBackend(FakeDriver())
        backend.begin_frame(SIZE, 1.0)
        with pytest.raises(RasterError):
            backend.begin_frame(SIZE, 1.0)

    def test_screenshot_requires_end(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        with pytest.raises(RasterError):
            backend.screenshot()

    def test_scale_drives_physical_size(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.5)
        assert ("begin", 150, 75, 1.5) in driver.calls

    def test_invalid_scale_is_rejected(self) -> None:
        backend = GLRasterBackend(FakeDriver())
        with pytest.raises(ValueError):
            backend.begin_frame(SIZE, 0.0)

    def test_display_list_size_must_match_frame(self) -> None:
        backend = GLRasterBackend(FakeDriver())
        backend.begin_frame(SIZE, 1.0)
        with pytest.raises(ValueError, match="设备像素"):
            backend.execute(_dl(99, 50))

    def test_frame_runs_begin_execute_end_screenshot(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(_dl(100, 50, FillRectOp(Rect(0, 0, 10, 10), RED)))
        backend.end_frame()
        frame = backend.screenshot()
        assert isinstance(frame, FrameBuffer)
        assert (frame.width, frame.height) == (100, 50)
        assert driver.kinds().count("begin") == 1
        assert driver.kinds().count("end") == 1

    def test_identical_frame_skips_clear_and_draws(self) -> None:
        """内容和上一帧完全一致 → 复用帧缓冲，连 clear 都不做（帧去重）。"""
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        display_list = _dl(100, 50, FillRectOp(Rect(0, 0, 10, 10), RED))

        backend.begin_frame(SIZE, 1.0)
        backend.execute(display_list)
        backend.end_frame()
        first_draws = len(driver.fills())
        first_clears = driver.kinds().count("clear")
        assert first_draws == 1 and first_clears == 1

        backend.begin_frame(SIZE, 1.0)
        backend.execute(display_list)
        backend.end_frame()
        assert len(driver.fills()) == first_draws, "相同内容不该重画"
        assert driver.kinds().count("clear") == first_clears, "相同内容不该重清屏"

    def test_changed_frame_redraws(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(_dl(100, 50, FillRectOp(Rect(0, 0, 10, 10), RED)))
        backend.end_frame()

        backend.begin_frame(SIZE, 1.0)
        backend.execute(_dl(100, 50, FillRectOp(Rect(0, 0, 20, 20), RED)))
        backend.end_frame()
        assert len(driver.fills()) == 2, "内容变了必须重画"
        assert driver.kinds().count("clear") == 2

    def test_resize_invalidates_frame_dedup(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        display_list = _dl(100, 50, FillRectOp(Rect(0, 0, 10, 10), RED))
        backend.begin_frame(SIZE, 1.0)
        backend.execute(display_list)
        backend.end_frame()

        # 换了物理尺寸：帧目标重建，上一帧内容不再有效
        backend.begin_frame(SIZE, 2.0)
        backend.execute(_dl(200, 100, FillRectOp(Rect(0, 0, 10, 10), RED)))
        backend.end_frame()
        assert len(driver.fills()) == 2

    def test_short_readback_is_an_error(self) -> None:
        driver = FakeDriver()
        driver.pixel_override = b"\x00" * 8
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.end_frame()
        with pytest.raises(RasterError, match="读回"):
            backend.screenshot()


# ---------------------------------------------------------------- 指令与裁剪


class TestDrawCommands:
    def test_fill_rect_forwards_device_coordinates(self) -> None:
        """显示列表坐标已是设备像素（录制时乘过 DPI），后端不得再乘 scale。"""
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 2.0)
        backend.execute(_dl(200, 100, FillRectOp(Rect(10, 20, 30, 40), RED)))
        fills = driver.fills()
        assert len(fills) == 1
        assert fills[0][1] == Rect(10, 20, 30, 40)

    def test_radius_is_clamped_to_half_short_side(self) -> None:
        """重矩形给 50 半径 → 夹到 20；与软件光栅同一规则（golden 才能对齐）。"""
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(_dl(100, 50, FillRectOp(Rect(0, 0, 100, 40), RED, radius=50.0)))
        assert driver.fills()[0][2] == pytest.approx(20.0)

    def test_stroke_forwards_width_and_color(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(_dl(100, 50, StrokeRectOp(Rect(0, 0, 40, 20), RED, width=2.0, radius=4.0)))
        stroke = next(call for call in driver.calls if call[0] == "stroke")
        assert stroke[1] == Rect(0, 0, 40, 20)
        assert stroke[3] == pytest.approx(2.0)
        assert stroke[2] == pytest.approx(4.0)

    def test_execute_clip_becomes_scissor(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        clip = Rect(5, 5, 20, 20)
        backend.execute(_dl(100, 50, FillRectOp(Rect(0, 0, 100, 50), RED)), clip)
        assert clip in driver.scissor_rects()

    def test_op_clip_and_dirty_rect_intersect(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(
            _dl(100, 50, FillRectOp(Rect(0, 0, 100, 50), RED, clip=Rect(0, 0, 40, 40))),
            Rect(20, 20, 100, 100),
        )
        assert Rect(20, 20, 20, 20) in driver.scissor_rects()

    def test_empty_intersection_skips_the_op(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(
            _dl(100, 50, FillRectOp(Rect(0, 0, 10, 10), RED, clip=Rect(0, 0, 10, 10))),
            Rect(50, 50, 10, 10),
        )
        assert driver.fills() == [], "无交集的指令不该产生任何绘制调用"

    def test_scissor_is_cleared_after_execute(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        backend.execute(
            _dl(100, 50, FillRectOp(Rect(0, 0, 10, 10), RED, clip=Rect(0, 0, 5, 5))), None
        )
        assert driver.scissor_rects()[-1] is None, "帧末必须复位 scissor，别留给下一个调用者"

    def test_paths_are_not_implemented_yet(self) -> None:
        path: PathData = (("M", 0.0, 0.0), ("L", 10.0, 0.0), ("L", 10.0, 10.0), ("Z",))
        assert PATH_VERBS  # 形状常量存在
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        with pytest.raises(NotImplementedError, match="PathFillOp"):
            backend.execute(_dl(100, 50, PathFillOp(path, RED)))


# ---------------------------------------------------------------- 文本


class TestText:
    def test_single_glyph_draws_once_at_baseline(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        op = TextRunOp(
            origin=Offset(10.0, 5.0),
            baseline=20.0,
            glyphs=(_glyph("A"),),
            size=14.0,
            color=RED,
        )
        backend.execute(_dl(100, 50, op))
        glyphs = [call for call in driver.calls if call[0] == "glyph"]
        assert len(glyphs) == 1
        # baseline_y = origin.y + baseline = 25；字形的 x 相对 origin
        assert glyphs[0][1] == pytest.approx(10.0)
        assert glyphs[0][2] == pytest.approx(25.0)

    def test_space_has_no_mask(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        op = TextRunOp(
            origin=Offset(0.0, 0.0),
            baseline=10.0,
            glyphs=(_glyph(" "),),
            size=14.0,
            color=RED,
        )
        backend.execute(_dl(100, 50, op))
        assert not [call for call in driver.calls if call[0] == "glyph"]

    def test_empty_run_produces_nothing(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        op = TextRunOp(origin=Offset(0.0, 0.0), baseline=10.0, glyphs=(), size=14.0, color=RED)
        backend.execute(_dl(100, 50, op))
        assert driver.kinds().count("scissor") == 1  # 只有帧末复位

    def test_ligature_covered_cluster_is_not_drawn_twice(self) -> None:
        """连字 id 只落在第一个簇；后两个簇必须被跳过（否则叠字）。"""
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        op = TextRunOp(
            origin=Offset(0.0, 0.0),
            baseline=10.0,
            glyphs=(
                _glyph("f", 0.0, glyph_ids=(7,)),
                _glyph("f", 4.0),
                _glyph("i", 8.0),
            ),
            size=14.0,
            color=RED,
        )
        backend.execute(_dl(100, 50, op))
        glyphs = [call for call in driver.calls if call[0] == "glyph"]
        assert len(glyphs) == 1, "被连字覆盖的簇只该画一次"

    def test_underline_draws_one_line(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(SIZE, 1.0)
        op = TextRunOp(
            origin=Offset(5.0, 0.0),
            baseline=10.0,
            glyphs=(_glyph("A", 0.0, advance=6.0), _glyph("B", 6.0, advance=6.0)),
            size=14.0,
            color=RED,
            underline=True,
        )
        backend.execute(_dl(100, 50, op))
        fills = driver.fills()
        assert len(fills) == 1, "下划线应当产生一条填充指令"
        assert fills[0][1].left == pytest.approx(5.0)
        assert fills[0][1].width == pytest.approx(12.0)


# ---------------------------------------------------------------- 资源


class TestResources:
    def test_texture_lifecycle_forwarded(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        handle = backend.create_image(2, 2, b"\x00" * 16)
        assert handle in driver._textures
        backend.destroy_image(handle)
        assert handle not in driver._textures

    def test_destroy_unknown_texture_is_idempotent(self) -> None:
        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.destroy_image(999)  # 不抛


# ---------------------------------------------------------------- 共享规则


class TestGlyphMaskPlan:
    """`glyph_mask_plan` 是软件光栅与 GL 共用的取字形规则——单独钉住。"""

    def test_builtin_provider_without_ids_uses_text_path(self) -> None:
        assert glyph_mask_plan(_glyph("A"), run_has_ids=False) == ()

    def test_single_id_uses_id_path(self) -> None:
        assert glyph_mask_plan(_glyph("A", glyph_ids=(5,)), run_has_ids=True) == (5,)

    def test_multi_id_combining_mark_uses_text_path(self) -> None:
        assert glyph_mask_plan(_glyph("a\u0301", glyph_ids=(1, 2)), run_has_ids=True) == ()

    def test_empty_id_after_ligature_is_skipped(self) -> None:
        assert glyph_mask_plan(_glyph("i"), run_has_ids=True) is None


def test_gl_backend_satisfies_raster_protocol_shape() -> None:
    """协议符合性：RasterBackend 是运行期可查的协议（R5.9 的口径）。"""
    from inkstone.gfx import RasterBackend

    backend = GLRasterBackend(FakeDriver())
    assert isinstance(backend, RasterBackend)


def test_gl_screenshot_roundtrips_through_png() -> None:
    """读回的像素能走通用 PNG 编码——证明与软件后端产出的数据面同构。"""
    driver = FakeDriver()
    backend = GLRasterBackend(driver)
    backend.begin_frame(Size(4.0, 3.0), 1.0)
    backend.end_frame()
    frame = backend.screenshot()
    png = encode_png(frame.width, frame.height, bytes(frame.data))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_glyph_provider_is_injectable() -> None:
    """真机注入 HbFtFontEngine 时后端不做任何特判——类型只是协议。"""
    provider: GlyphProvider = __import__(
        "inkstone.gfx", fromlist=["BuiltinGlyphProvider"]
    ).BuiltinGlyphProvider()
    backend = GLRasterBackend(FakeDriver(), glyph_provider=provider)
    assert backend is not None
