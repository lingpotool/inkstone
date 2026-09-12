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


def rasterize_canvas(width: int, height: int, *ops: object) -> FrameBuffer:
    """在任意尺寸的画布上光栅化（背景铺满整幅画布）。

    非方形画布是这里的主角：宽高一旦被混淆，宽扁画布会直接崩，
    竖长画布会静默少画一截——两者都是"测试全用方形画布"掩盖掉的。
    """
    recorder = DisplayListRecorder()
    recorder.fill_rect(Rect(0.0, 0.0, float(width), float(height)), BG)
    for op in ops:
        recorder._ops.append(op)
    return SoftwareRasterizer().rasterize(recorder.finish(width, height))


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

    def test_deep_regions_are_exact_and_edges_are_smooth(self):
        """最强的那条：深区逐像素精确，边缘 1px 过渡带允许混合色。

        外圈 (0,0,40,40) r=10，内圈 (2,2,36,36) r=8，两圆心重合。
        离边缘 ≥1px 的区域颜色必须是精确的；|距离| < 1 的过渡带
        做 SDF 抗锯齿，允许混合。
        """
        fb = self.frame()
        saw_smooth_edge = False
        for y in range(40):
            for x in range(40):
                px, py = x + 0.5, y + 0.5
                d_outer = _dist(px, py, Rect(0, 0, 40, 40), 10.0)
                d_inner = _dist(px, py, Rect(2, 2, 36, 36), 8.0)
                rgb = fb.pixel(x, y)[:3]

                if d_outer <= -1.0 and d_inner >= 1.0:
                    assert rgb == (BORDER.r, BORDER.g, BORDER.b), f"环深区 ({x},{y}) 不是边框色"
                elif d_inner <= -1.0:
                    assert rgb == (FILL.r, FILL.g, FILL.b), f"填充深区 ({x},{y}) 不是填充色"
                elif d_outer >= 1.0:
                    assert rgb == (BG.r, BG.g, BG.b), f"形状外 ({x},{y}) 有东西外溢"
                else:
                    # 过渡带：应当出现真正的混合色，而不是三选一的硬边
                    if rgb not in _PURE:
                        saw_smooth_edge = True
        assert saw_smooth_edge, "没找到任何抗锯齿过渡像素——边缘还是硬的"


_PURE = {
    (BG.r, BG.g, BG.b),
    (FILL.r, FILL.g, FILL.b),
    (BORDER.r, BORDER.g, BORDER.b),
}


def _dist(px: float, py: float, rect: Rect, radius: float) -> float:
    """测试侧的独立 SDF 实现（与光栅器无关，起交叉验证作用）。"""
    cx = (rect.left + rect.right) / 2
    cy = (rect.top + rect.bottom) / 2
    qx = abs(px - cx) - rect.width / 2 + radius
    qy = abs(py - cy) - rect.height / 2 + radius
    ox, oy = max(qx, 0.0), max(qy, 0.0)
    outside = (ox * ox + oy * oy) ** 0.5
    inside = min(max(qx, qy), 0.0)
    return outside + inside - radius

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

    def test_clip_boundary_on_integer_pixels_leaks_nothing(self):
        """裁剪边恰好落在整数像素时，第 10 列/行之外不许有墨迹。

        曾经的 bug：上界写成 `int(right) + 1`，于是 clip=(0,0,10,10) 会
        多放第 10 列与第 10 行进来——像素 10 覆盖 [10,11)，与 [0,10) 无交集。
        """
        fb = rasterize(FillRectOp(Rect(0, 0, 20, 20), FILL, 0.0, Rect(0, 0, 10, 10)))
        assert fb.pixel(9, 9)[:3] == (255, 255, 255), "裁剪区内应当着色"
        for x in range(10, 20):
            assert fb.pixel(x, 5)[:3] == (255, 0, 255), f"第 {x} 列越出了裁剪"
        for y in range(10, 20):
            assert fb.pixel(5, y)[:3] == (255, 0, 255), f"第 {y} 行越出了裁剪"

    def test_clip_boundary_on_integer_pixels_for_stroke(self):
        """描边走的是另一条分派路径，同样不许越界。

        环的右半边落在裁剪边之外：第 6 列/行与裁剪区 [0,6) 无交集，不许有墨迹。
        """
        fb = rasterize(
            StrokeRectOp(Rect(0, 0, 10, 10), FILL, 4.0, 0.0, Rect(0, 0, 6, 6)),
        )
        assert fb.pixel(1, 1)[:3] == (255, 255, 255), "裁剪区内的环应当着色"
        for x in range(6, 10):
            assert fb.pixel(x, 1)[:3] == (255, 0, 255), f"描边越出了裁剪（第 {x} 列）"
        for y in range(6, 10):
            assert fb.pixel(1, y)[:3] == (255, 0, 255), f"描边越出了裁剪（第 {y} 行）"


class TestNonSquareCanvas:
    """非方形画布：宽必须归宽、高必须归高。

    曾经的 bug：`_fill` / `_stroke` 把**画布宽度**当成高度传给了 `_row_range`。
    宽扁画布（宽 > 高）上画超出底部的形状会直接崩；竖长画布（高 > 宽）
    全屏填充会在底部静默少画一截。方形画布恰好掩盖了这两个方向。
    """

    @pytest.mark.parametrize(("width", "height"), [(200, 100), (100, 200)])
    def test_full_canvas_fill_colors_all_four_corners(self, width: int, height: int) -> None:
        fb = rasterize_canvas(width, height, FillRectOp(Rect(0, 0, width, height), FILL, 0.0, None))
        for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            assert fb.pixel(x, y)[:3] == (255, 255, 255), f"({x},{y}) 没被着色"

    @pytest.mark.parametrize(("width", "height"), [(200, 100), (100, 200)])
    def test_full_canvas_fill_colors_every_edge_pixel(self, width: int, height: int) -> None:
        fb = rasterize_canvas(width, height, FillRectOp(Rect(0, 0, width, height), FILL, 0.0, None))
        for x in range(width):
            assert fb.pixel(x, 0)[:3] == (255, 255, 255), f"上边缘 ({x},0) 没着色"
            assert fb.pixel(x, height - 1)[:3] == (255, 255, 255), (
                f"下边缘 ({x},{height - 1}) 没着色"
            )
        for y in range(height):
            assert fb.pixel(0, y)[:3] == (255, 255, 255), f"左边缘 (0,{y}) 没着色"
            assert fb.pixel(width - 1, y)[:3] == (255, 255, 255), f"右边缘 ({width - 1},{y}) 没着色"

    def test_shape_overflowing_bottom_does_not_crash(self):
        """滚动列表每帧都在画"内容比视口长"——这里曾经抛 ValueError。"""
        fb = rasterize_canvas(200, 100, FillRectOp(Rect(0, 0, 200, 150), FILL, 0.0, None))
        for x, y in ((0, 0), (199, 0), (0, 99), (199, 99), (100, 50)):
            assert fb.pixel(x, y)[:3] == (255, 255, 255), f"可见区域 ({x},{y}) 没着色"

    def test_shape_overflowing_right_does_not_crash(self):
        fb = rasterize_canvas(200, 100, FillRectOp(Rect(0, 0, 260, 100), FILL, 0.0, None))
        for x, y in ((0, 0), (199, 0), (0, 99), (199, 99)):
            assert fb.pixel(x, y)[:3] == (255, 255, 255), f"可见区域 ({x},{y}) 没着色"

    def test_shape_overflowing_both_axes_does_not_crash(self):
        fb = rasterize_canvas(200, 100, FillRectOp(Rect(0, 0, 260, 150), FILL, 0.0, None))
        assert fb.pixel(199, 99)[:3] == (255, 255, 255)

    def test_stroke_overflowing_bottom_does_not_crash(self):
        """描边走的是另一条分派路径，宽高同样不许传反。"""
        fb = rasterize_canvas(200, 100, StrokeRectOp(Rect(0, 0, 200, 150), FILL, 2.0, 0.0, None))
        assert fb.pixel(1, 50)[:3] == (255, 255, 255), "可见区域的环应当着色"
        assert fb.pixel(100, 50)[:3] == (255, 0, 255), "环内部不填"

    def test_tall_canvas_fill_reaches_the_bottom(self):
        """100×200 竖向画布全屏填充：曾经底部 100 行静默留空。"""
        fb = rasterize_canvas(100, 200, FillRectOp(Rect(0, 0, 100, 200), FILL, 0.0, None))
        for y in (0, 99, 100, 150, 199):
            assert fb.pixel(50, y)[:3] == (255, 255, 255), f"第 {y} 行没着色"


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
