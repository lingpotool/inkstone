"""显示列表状态指令的等价性测试（R8.4 地基）。

状态指令（平移/裁剪）存在的理由：让"内容录一次、每帧只改变换"成为可能——
滚动层缓存的前提。但新语义必须与"录制时直接把偏移烘焙进坐标"**逐像素一致**，
否则黄金图会莫名其妙地变，而排查方向会完全错。

所以这一组的核心断言是**等价**：状态版 == 烘焙版（软件光栅逐像素、GL 调用逐参数）。
"""

from __future__ import annotations

import pytest

from inkstone.gfx import (
    DisplayList,
    FillRectOp,
    GLRasterBackend,
    PathData,
    PathFillOp,
    PopOp,
    PositionedGlyph,
    PushClipOp,
    PushTranslateOp,
    SoftwareRasterizer,
    StrokeRectOp,
    TextRunOp,
    resolve_state_ops,
)
from inkstone.gfx.color import Color
from inkstone.layout.types import Offset, Rect, Size

RED = Color(255, 0, 0, 1.0)
BLUE = Color(0, 0, 255, 1.0)


def _raster(width: int, height: int, ops: tuple[object, ...]) -> bytes:
    raster = SoftwareRasterizer()
    raster.begin_frame(Size(float(width), float(height)), 1.0)
    raster.execute(DisplayList(width, height, ops))  # type: ignore[arg-type]
    raster.end_frame()
    return bytes(raster.screenshot().data)


class TestSoftwareEquivalence:
    def test_push_translate_equals_baked_translation(self) -> None:
        baked = (FillRectOp(Rect(10.0, 15.0, 20.0, 20.0), RED),)
        stated = (
            PushTranslateOp(10.0, 15.0),
            FillRectOp(Rect(0.0, 0.0, 20.0, 20.0), RED),
            PopOp(),
        )
        assert _raster(64, 48, baked) == _raster(64, 48, stated)

    def test_nested_translates_accumulate(self) -> None:
        baked = (FillRectOp(Rect(7.0, 9.0, 4.0, 4.0), RED),)
        stated = (
            PushTranslateOp(3.0, 4.0),
            PushTranslateOp(4.0, 5.0),
            FillRectOp(Rect(0.0, 0.0, 4.0, 4.0), RED),
            PopOp(),
            PopOp(),
        )
        assert _raster(32, 32, baked) == _raster(32, 32, stated)

    def test_push_clip_equals_baked_clip(self) -> None:
        baked = (FillRectOp(Rect(0.0, 0.0, 32.0, 32.0), BLUE, clip=Rect(4.0, 4.0, 8.0, 8.0)),)
        stated = (
            PushClipOp(Rect(4.0, 4.0, 8.0, 8.0)),
            FillRectOp(Rect(0.0, 0.0, 32.0, 32.0), BLUE),
            PopOp(),
        )
        assert _raster(32, 32, baked) == _raster(32, 32, stated)

    def test_translate_and_clip_compose(self) -> None:
        baked = (FillRectOp(Rect(10.0, 10.0, 16.0, 16.0), RED, clip=Rect(10.0, 10.0, 8.0, 8.0)),)
        stated = (
            PushClipOp(Rect(10.0, 10.0, 8.0, 8.0)),
            PushTranslateOp(10.0, 10.0),
            # 指令自带的 clip 是**指令局部坐标**，会跟着平移
            FillRectOp(Rect(0.0, 0.0, 16.0, 16.0), RED, clip=Rect(0.0, 0.0, 8.0, 8.0)),
            PopOp(),
            PopOp(),
        )
        assert _raster(32, 32, baked) == _raster(32, 32, stated)

    def test_translated_text_equals_baked_text(self) -> None:
        glyph = PositionedGlyph("A", 0.0, 8.0)
        baked = (
            TextRunOp(
                origin=Offset(6.0, 10.0), baseline=12.0, glyphs=(glyph,), size=14.0, color=RED
            ),
        )
        stated = (
            PushTranslateOp(6.0, 10.0),
            TextRunOp(
                origin=Offset(0.0, 0.0), baseline=12.0, glyphs=(glyph,), size=14.0, color=RED
            ),
            PopOp(),
        )
        assert _raster(48, 40, baked) == _raster(48, 40, stated)

    def test_translated_path_equals_baked_path(self) -> None:
        path: PathData = (("M", 0.0, 0.0), ("L", 6.0, 0.0), ("L", 6.0, 6.0), ("Z",))
        stated = (PushTranslateOp(2.0, 3.0), PathFillOp(path, RED), PopOp())
        resolved = list(resolve_state_ops(stated))  # type: ignore[arg-type]
        assert resolved[0].data == (("M", 2.0, 3.0), ("L", 8.0, 3.0), ("L", 8.0, 9.0), ("Z",))


class TestLayerMarkersAreTransparentToPixels:
    """层标记只对"关心缓存的后端"有意义；软件光栅必须当它不存在。"""

    def test_layer_markers_do_not_change_pixels(self) -> None:
        from inkstone.gfx import PopLayerOp, PushLayerOp

        plain = (FillRectOp(Rect(4.0, 4.0, 8.0, 8.0), RED),)
        layered = (
            PushLayerOp(1, Rect(0.0, 0.0, 32.0, 32.0)),
            FillRectOp(Rect(4.0, 4.0, 8.0, 8.0), RED),
            PopLayerOp(),
        )
        assert _raster(32, 32, plain) == _raster(32, 32, layered)


class TestStateValidation:
    def test_pop_without_push_raises(self) -> None:
        with pytest.raises(ValueError, match="配对"):
            list(resolve_state_ops((PopOp(),)))

    def test_unbalanced_push_raises(self) -> None:
        ops = (PushTranslateOp(1.0, 1.0), FillRectOp(Rect(0, 0, 4, 4), RED))
        with pytest.raises(ValueError, match="配对"):
            list(resolve_state_ops(ops))  # type: ignore[arg-type]

    def test_op_outside_the_clip_leaves_no_ink(self) -> None:
        """裁剪不搬家：被裁到区域外的指令最终不留墨迹。

        指令本身仍会产出、由光栅端的裁剪边界挡掉——这里刻意不在状态展开时
        做"裁剪与指令是否相交"的几何早退：那会让状态路径与烘焙路径在边界情形
        分叉，而黄金图恰恰靠"两条路径逐像素一致"来守。
        """
        blank = _raster(32, 32, ())
        clipped = _raster(
            32,
            32,
            (
                PushClipOp(Rect(100.0, 100.0, 8.0, 8.0)),
                FillRectOp(Rect(0.0, 0.0, 8.0, 8.0), RED),
                PopOp(),
            ),
        )
        assert clipped == blank

    def test_stroke_rect_is_translated(self) -> None:
        resolved = list(
            resolve_state_ops(
                (PushTranslateOp(5.0, 0.0), StrokeRectOp(Rect(0, 0, 4, 4), RED, width=1.0), PopOp())
            )
        )  # type: ignore[arg-type]
        assert resolved[0].rect == Rect(5.0, 0.0, 4.0, 4.0)


class TestGLBackendStateOps:
    """GL 后端也走同一份 `resolve_state_ops`——调用参数必须已被平移。"""

    def test_gl_receives_translated_geometry(self) -> None:
        from test_gl_backend import FakeDriver

        driver = FakeDriver()
        backend = GLRasterBackend(driver)
        backend.begin_frame(Size(32.0, 32.0), 1.0)
        backend.execute(
            DisplayList(
                32,
                32,
                (
                    PushTranslateOp(4.0, 6.0),
                    FillRectOp(Rect(0.0, 0.0, 10.0, 10.0), RED),
                    PopOp(),
                ),
            )
        )
        assert driver.fills()[0][1] == Rect(4.0, 6.0, 10.0, 10.0)
