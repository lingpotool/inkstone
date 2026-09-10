"""光栅器与显示列表的单元测试。

最重要的一组是 `TestRoundedCornerRing`——它守着一个真实渲染 bug：
描边曾经用"四条矩形条"拼，圆角处会露出底色，肉眼看到就是
"按钮四角有空白、边框和圆角之间裂开了"。圆环法修复后由这组测试守住。

顺带覆盖：圆角填充、矩形描边、裁剪、alpha 混合、PNG 编码确定性。
"""

import pytest

from inkstone.gfx import (
    DisplayList,
    DisplayListRecorder,
    FillRectOp,
    FrameBuffer,
    SoftwareRasterizer,
    StrokeRectOp,
    encode_png,
)
from inkstone.gfx.color import Color
from inkstone.layout.types import Rect

BG = Color.from_hex("#FF00FF")
FILL = Color.from_hex("#FFFFFF")
BORDER = Color.from_hex("#E2E8F0")


def rasterize(*ops: object, size: int = 40) -> FrameBuffer:
    recorder = DisplayListRecorder()
    recorder.fill_rect(Rect(0.0, 0.0, float(size), float(size)), BG)
    for op in ops:
        recorder._ops.append(op)  # 直接塞指令：测光栅，不测录制
    return SoftwareRasterizer().rasterize(recorder.finish(size, size))


class TestRecorder:
    def test_translate_and_save_restore(self):
        r = DisplayListRecorder()
        r.fill_rect(Rect(0, 0, 10, 10), FILL)
        r.save()
        r.translate(5.0, 20.0)
        r.fill_rect(Rect(0, 0, 10, 10), FILL)
        r.restore()
        r.fill_rect(Rect(0, 0, 10, 10), FILL)
        dl = r.finish(40, 40)

        assert [op.rect.left for op in dl.ops] == pytest.approx([0, 5, 0])
        assert [op.rect.top for op in dl.ops] == pytest.approx([0, 20, 0])

    def test_restore_without_save_is_rejected(self):
        r = DisplayListRecorder()
        with pytest.raises(RuntimeError):
            r.restore()

    def test_unbalanced_save_is_rejected(self):
        r = DisplayListRecorder()
        r.save()
        with pytest.raises(RuntimeError):
            r.finish(10, 10)

    def test_clip_is_recorded_and_narrows(self):
        r = DisplayListRecorder()
        r.clip_rect(Rect(0, 0, 20, 20))
        r.save()
        r.clip_rect(Rect(0, 0, 10, 10))
        r.fill_rect(Rect(0, 0, 40, 40), FILL)
        r.restore()
        r.fill_rect(Rect(0, 0, 40, 40), FILL)
        dl = r.finish(40, 40)
        assert dl.ops[0].clip == Rect(0, 0, 10, 10)
        assert dl.ops[1].clip == Rect(0, 0, 20, 20), "restore 应当把裁剪也还原"

    def test_radius_is_kept_on_op(self):
        r = DisplayListRecorder()
        r.round_rect(Rect(0, 0, 20, 20), 6.0, FILL)
        dl = r.finish(40, 40)
        op = dl.ops[0]
        assert isinstance(op, FillRectOp)
        assert op.radius == pytest.approx(6.0)

    def test_display_list_is_immutable_and_describable(self):
        r = DisplayListRecorder()
        r.fill_rect(Rect(0, 0, 10, 10), FILL)
        dl = r.finish(20, 20)
        assert isinstance(dl, DisplayList)
        assert len(dl) == 1
        assert "DisplayList(20×20, 1 ops)" in dl.describe()


class TestRoundedFill:
    def test_corner_pixel_is_outside_the_rounded_shape(self):
        fb = rasterize(FillRectOp(Rect(0, 0, 40, 40), FILL, 10.0, None))
        assert fb.pixel(0, 0)[:3] == (255, 0, 255)  # 圆角外 = 底色

    def test_center_is_filled(self):
        fb = rasterize(FillRectOp(Rect(0, 0, 40, 40), FILL, 10.0, None))
        assert fb.pixel(20, 20)[:3] == (255, 255, 255)

    def test_square_corner_is_filled_when_radius_zero(self):
        fb = rasterize(FillRectOp(Rect(0, 0, 40, 40), FILL, 0.0, None))
        assert fb.pixel(0, 0)[:3] == (255, 255, 255)


class TestRoundedCornerRing:
    """边框与圆角之间不许有缝隙（真实 bug 的回归测试）。"""

    @staticmethod
    def frame() -> FrameBuffer:
        return rasterize(
            FillRectOp(Rect(0, 0, 40, 40), FILL, 10.0, None),
            StrokeRectOp(Rect(0, 0, 40, 40), BORDER, 2.0, 10.0, None),
        )

    def test_corner_arc_is_border_not_fill(self):
        """修复前 (3,3) 是填充、边框在圆角处断开，看过去就是"四角有空白"。"""
        fb = self.frame()
        assert fb.pixel(3, 3)[:3] == (226, 232, 240)

    def test_nothing_sticks_out_of_the_rounded_corner(self):
        """修复前边框是方的，会一路画到包围盒的角上。"""
        fb = self.frame()
        assert fb.pixel(0, 0)[:3] == (255, 0, 255)
        assert fb.pixel(1, 1)[:3] == (255, 0, 255)

    def test_every_pixel_matches_the_rounded_ring_spec(self):
        """最强的那条：逐像素对照"外圈圆角矩形 − 内圈圆角矩形"的解析解。

        外圈 (0,0,40,40) r=10，内圈 (2,2,36,36) r=8——两个圆心重合，
        所以判据简化成"到 (10,10) 的距离落在 8 与 10 之间即为边框"。
        任何缝隙、任何溢出都会被这一条抓到。
        """
        fb = self.frame()
        for y in range(40):
            for x in range(40):
                px, py = x + 0.5, y + 0.5
                cx, cy = min(max(px, 10.0), 30.0), min(max(py, 10.0), 30.0)
                dist_sq = (px - cx) ** 2 + (py - cy) ** 2
                if dist_sq <= 64.0:
                    expected = FILL
                elif dist_sq <= 100.0:
                    expected = BORDER
                else:
                    expected = BG
                assert fb.pixel(x, y)[:3] == (expected.r, expected.g, expected.b), (
                    f"({x},{y}) 期望 {expected.to_hex()}，实际不符"
                )

    def test_interior_is_untouched_by_the_ring(self):
        fb = self.frame()
        assert fb.pixel(20, 20)[:3] == (255, 255, 255)

    def test_square_stroke_draws_a_full_ring(self):
        fb = rasterize(StrokeRectOp(Rect(0, 0, 40, 40), BORDER, 2.0, 0.0, None))
        assert fb.pixel(1, 1)[:3] == (226, 232, 240)
        assert fb.pixel(1, 20)[:3] == (226, 232, 240)
        assert fb.pixel(38, 20)[:3] == (226, 232, 240)
        assert fb.pixel(20, 20)[:3] == (255, 0, 255)  # 内部不填


class TestClipping:
    def test_fill_is_cut_by_clip(self):
        fb = rasterize(FillRectOp(Rect(0, 0, 40, 40), FILL, 0.0, Rect(0, 0, 10, 10)))
        assert fb.pixel(5, 5)[:3] == (255, 255, 255)
        assert fb.pixel(20, 20)[:3] == (255, 0, 255)


class TestAlphaBlending:
    def test_half_alpha_blends(self):
        """白 50% 叠在洋红 #FF00FF 上：R 仍是满，G 涨到一半，B 不变。"""
        half = Color(255, 255, 255, 0.5)
        fb = rasterize(FillRectOp(Rect(0, 0, 40, 40), half, 0.0, None))
        r, g, b, a = fb.pixel(20, 20)
        assert r == 255  # 255×0.5 + 255×0.5
        assert g in (127, 128)  # 0×0.5 + 255×0.5
        assert b == 255
        assert a == 255  # 底色不透明，合成后也不透明

    def test_zero_alpha_is_invisible(self):
        fb = rasterize(FillRectOp(Rect(0, 0, 40, 40), Color(255, 255, 255, 0.0), 0.0, None))
        assert fb.pixel(20, 20)[:3] == (255, 0, 255)


class TestFrameBuffer:
    def test_pixel_out_of_range_is_rejected(self):
        fb = SoftwareRasterizer().rasterize(DisplayList(4, 4, ()))
        fb.pixel(3, 3)
        with pytest.raises(IndexError):
            fb.pixel(4, 0)

    def test_size_mismatch_is_rejected(self):
        with pytest.raises(ValueError):
            FrameBuffer(2, 2, bytearray(4))


class TestPngEncoding:
    def test_signature_and_header(self):
        png = encode_png(2, 2, bytes(2 * 2 * 4))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert b"IHDR" in png and b"IEND" in png

    def test_same_pixels_give_same_bytes(self):
        px = bytes(8 * 8 * 4)
        assert encode_png(8, 8, px) == encode_png(8, 8, px)

    def test_length_mismatch_is_rejected(self):
        with pytest.raises(ValueError):
            encode_png(2, 2, bytes(10))
