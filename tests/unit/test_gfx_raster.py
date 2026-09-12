"""光栅器与显示列表的单元测试。

最重要的一组是 `TestRoundedCornerRing`——它守着一个真实渲染 bug：
描边曾经用"四条矩形条"拼，圆角处会露出底色，肉眼看到就是
"按钮四角有空白、边框和圆角之间裂开了"。圆环法修复后由这组测试守住。

顺带覆盖：圆角填充、矩形描边、裁剪、alpha 混合、PNG 编码确定性。
"""

import pytest

from inkstone.gfx import (
    CLOSE,
    CUBIC,
    IDENTITY,
    LINE,
    MOVE,
    PATH_VERBS,
    QUAD,
    Affine,
    DisplayList,
    DisplayListRecorder,
    FillRectOp,
    FrameBuffer,
    PathData,
    PathFillOp,
    PathStrokeOp,
    RasterBackend,
    RasterError,
    SoftwareRasterizer,
    StrokeRectOp,
    encode_png,
)
from inkstone.gfx.color import Color
from inkstone.layout.types import Rect, Size
from perf_support import measure_best

BG = Color.from_hex("#FF00FF")
FILL = Color.from_hex("#FFFFFF")
BORDER = Color.from_hex("#E2E8F0")


def rasterize_display_list(display_list: DisplayList, **kwargs: object) -> FrameBuffer:
    """走一遍帧生命周期：begin → execute → end → screenshot（R3.1 之后的唯一入口）。"""
    raster = SoftwareRasterizer(**kwargs)  # type: ignore[arg-type]
    raster.begin_frame(Size(float(display_list.width), float(display_list.height)), 1.0)
    raster.execute(display_list)
    raster.end_frame()
    return raster.screenshot()


def rasterize(*ops: object, size: int = 40) -> FrameBuffer:
    recorder = DisplayListRecorder()
    recorder.fill_rect(Rect(0.0, 0.0, float(size), float(size)), BG)
    for op in ops:
        recorder._ops.append(op)  # 直接塞指令：测光栅，不测录制
    return rasterize_display_list(recorder.finish(size, size))


def rasterize_canvas(width: int, height: int, *ops: object) -> FrameBuffer:
    """在任意尺寸的画布上光栅化（背景铺满整幅画布）。

    非方形画布是这里的主角：宽高一旦被混淆，宽扁画布会直接崩，
    竖长画布会静默少画一截——两者都是"测试全用方形画布"掩盖掉的。
    """
    recorder = DisplayListRecorder()
    recorder.fill_rect(Rect(0.0, 0.0, float(width), float(height)), BG)
    for op in ops:
        recorder._ops.append(op)
    return rasterize_display_list(recorder.finish(width, height))


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


class TestRasterProtocol:
    """R3.1：协议形状与帧生命周期（docs/03 §2）。

    旧协议只有一个 `rasterize() -> FrameBuffer`，把"读回内存"当成后端的唯一出口——
    GL/Skia 在结构上无法实现它。这里钉住新形状，免得它悄悄退回去。
    """

    @staticmethod
    def _dl(width: int = 4, height: int = 4, color: Color = FILL) -> DisplayList:
        recorder = DisplayListRecorder()
        recorder.fill_rect(Rect(0.0, 0.0, float(width), float(height)), color)
        return recorder.finish(width, height)

    def test_software_rasterizer_satisfies_the_protocol(self):
        """结构性检查：少了任何一个方法都算不满足协议。"""
        assert isinstance(SoftwareRasterizer(), RasterBackend)

    def test_old_single_shot_rasterize_is_gone(self):
        """旧接口必须**删掉**而不是留着——两个入口会让"帧所有权在后端"变成空话。"""
        assert not hasattr(SoftwareRasterizer(), "rasterize")

    def test_full_lifecycle(self):
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 4.0), 1.0)
        raster.execute(self._dl())
        raster.end_frame()
        fb = raster.screenshot()
        assert (fb.width, fb.height) == (4, 4)
        assert fb.pixel(2, 2)[:3] == (FILL.r, FILL.g, FILL.b)

    def test_scale_scales_the_framebuffer(self):
        """设备像素比：size 是逻辑尺寸，帧缓冲是 size × scale。"""
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 3.0), 2.0)
        raster.execute(self._dl(8, 6))
        raster.end_frame()
        assert (raster.screenshot().width, raster.screenshot().height) == (8, 6)

    def test_fractional_scale_rounds_up(self):
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(3.0, 3.0), 1.25)
        raster.execute(self._dl(4, 4))
        raster.end_frame()
        assert raster.screenshot().width == 4  # ceil(3 × 1.25) = 4

    @pytest.mark.parametrize("scale", [0.0, -1.0])
    def test_non_positive_scale_is_rejected(self, scale: float):
        with pytest.raises(ValueError, match="设备像素比"):
            SoftwareRasterizer().begin_frame(Size(4.0, 4.0), scale)

    def test_execute_before_begin_frame_is_rejected(self):
        with pytest.raises(RasterError, match="execute 必须在 begin_frame"):
            SoftwareRasterizer().execute(self._dl())

    def test_end_frame_without_begin_frame_is_rejected(self):
        with pytest.raises(RasterError, match="end_frame 没有配对"):
            SoftwareRasterizer().end_frame()

    def test_screenshot_before_end_frame_is_rejected(self):
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 4.0), 1.0)
        raster.execute(self._dl())
        with pytest.raises(RasterError, match="screenshot 必须在 end_frame"):
            raster.screenshot()

    def test_begin_frame_twice_is_rejected(self):
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 4.0), 1.0)
        with pytest.raises(RasterError, match="上一帧还没结束"):
            raster.begin_frame(Size(4.0, 4.0), 1.0)

    def test_multiple_executes_in_one_frame_are_allowed(self):
        """一帧里可以多次 execute，各自只提交自己那块区域（脏矩形分片）。

        注意"分层"不靠多次 execute 叠加：一个显示列表就是一段完整的绘制程序
        （录制器把该画的都录进去了），`clip` 管的是"这一趟负责更新哪块区域"。
        """
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 4.0), 1.0)
        raster.execute(self._dl(4, 4, BG), Rect(0, 0, 2, 2))
        raster.execute(self._dl(4, 4, FILL), Rect(2, 2, 2, 2))
        raster.end_frame()

        fb = raster.screenshot()
        assert fb.pixel(0, 0)[:3] == (BG.r, BG.g, BG.b)
        assert fb.pixel(3, 3)[:3] == (FILL.r, FILL.g, FILL.b)

    def test_display_list_size_must_match_the_frame(self):
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 4.0), 1.0)
        with pytest.raises(ValueError, match="不一致"):
            raster.execute(self._dl(8, 8))

    def test_screenshot_returns_a_copy(self):
        """帧缓冲归后端所有：改拷贝不许影响下一帧。"""
        raster = SoftwareRasterizer()
        raster.begin_frame(Size(4.0, 4.0), 1.0)
        raster.execute(self._dl())
        raster.end_frame()
        fb = raster.screenshot()
        fb.data[0] = 0
        assert raster.screenshot().data[0] != 0

    def test_image_handles_are_not_implemented_yet(self):
        """位图生命周期进了协议，但软件端还没有消费者——要**响亮**地未实现。"""
        raster = SoftwareRasterizer()
        with pytest.raises(NotImplementedError, match="位图上传"):
            raster.create_image(1, 1, bytes(4))
        with pytest.raises(NotImplementedError, match="位图上传"):
            raster.destroy_image(1)


class TestDirtyRectCompositing:
    """`clip` 是脏矩形：区域外保留上一帧（docs/14 §4 的决策）。"""

    def _frame(self, raster: SoftwareRasterizer, color: Color, clip: Rect | None) -> None:
        recorder = DisplayListRecorder()
        recorder.fill_rect(Rect(0.0, 0.0, 8.0, 8.0), color)
        raster.begin_frame(Size(8.0, 8.0), 1.0)
        raster.execute(recorder.finish(8, 8), clip)
        raster.end_frame()

    def test_clip_none_replaces_the_whole_frame(self):
        raster = SoftwareRasterizer()
        self._frame(raster, FILL, None)
        assert raster.screenshot().pixel(0, 0)[:3] == (FILL.r, FILL.g, FILL.b)

    def test_outside_the_clip_keeps_the_previous_frame(self):
        raster = SoftwareRasterizer()
        self._frame(raster, FILL, None)  # 第 1 帧：整幅白
        self._frame(raster, BG, Rect(0, 0, 4, 4))  # 第 2 帧：只更新左上 4×4

        fb = raster.screenshot()
        assert fb.pixel(1, 1)[:3] == (BG.r, BG.g, BG.b), "裁剪区内应当是第 2 帧的颜色"
        assert fb.pixel(6, 6)[:3] == (FILL.r, FILL.g, FILL.b), "裁剪区外必须保留第 1 帧"

    def test_clip_is_clamped_to_the_canvas(self):
        raster = SoftwareRasterizer()
        self._frame(raster, BG, Rect(-100, -100, 400, 400))
        assert raster.screenshot().pixel(7, 7)[:3] == (BG.r, BG.g, BG.b)

    def test_empty_clip_updates_nothing(self):
        raster = SoftwareRasterizer()
        self._frame(raster, FILL, None)
        self._frame(raster, BG, Rect(10, 10, 2, 2))  # 完全在画布外
        assert raster.screenshot().pixel(0, 0)[:3] == (FILL.r, FILL.g, FILL.b)

    def test_fractional_clip_covers_partially_touched_pixels(self):
        """裁剪边界落在像素中间时按"需要更新"取大——部分覆盖的像素也要更新。"""
        raster = SoftwareRasterizer()
        self._frame(raster, FILL, None)
        self._frame(raster, BG, Rect(0.5, 0.5, 3.0, 3.0))
        fb = raster.screenshot()
        assert fb.pixel(0, 0)[:3] == (BG.r, BG.g, BG.b)
        assert fb.pixel(3, 3)[:3] == (BG.r, BG.g, BG.b), "ceil(3.5)=4，第 3 列也要更新"
        assert fb.pixel(4, 4)[:3] == (FILL.r, FILL.g, FILL.b)


class TestAffineTransform:
    """R3.3：仿射矩阵本身。

    矩阵约定与 SVG / CSS 的 `matrix(a,b,c,d,tx,ty)` 一致：
    `x' = a·x + c·y + tx`，`y' = b·x + d·y + ty`。
    """

    def test_identity_maps_nothing(self):
        assert IDENTITY.is_identity
        assert IDENTITY.map_point(3.0, 4.0) == (3.0, 4.0)

    def test_translation(self):
        assert Affine.translation(2.0, -1.0).map_point(3.0, 4.0) == (5.0, 3.0)

    def test_scaling_about_the_origin(self):
        assert Affine.scaling(2.0).map_point(3.0, 4.0) == (6.0, 8.0)

    def test_scaling_about_a_point_keeps_that_point_fixed(self):
        """绕某点缩放：那个点必须不动。否则就是"一边缩放一边跑掉"。"""
        s = Affine.scaling(2.0, origin_x=10.0, origin_y=20.0)
        assert s.map_point(10.0, 20.0) == (10.0, 20.0)
        assert s.map_point(11.0, 20.0) == (12.0, 20.0)

    def test_non_uniform_scaling(self):
        assert Affine.scaling(2.0, 3.0).map_point(1.0, 1.0) == (2.0, 3.0)

    def test_composition_order_is_explicit(self):
        """`A.then(B)` = 先 A 后 B。复合顺序不能含糊，否则缩放会"带跑"元素。"""
        scale_then_move = Affine.scaling(2.0).then(Affine.translation(10.0, 0.0))
        move_then_scale = Affine.translation(10.0, 0.0).then(Affine.scaling(2.0))
        assert scale_then_move.map_point(1.0, 0.0) == (12.0, 0.0)  # 2×1 + 10
        assert move_then_scale.map_point(1.0, 0.0) == (22.0, 0.0)  # 2×(1 + 10)
        assert scale_then_move != move_then_scale

    def test_uniform_scale_detection(self):
        assert Affine.scaling(2.0).uniform_scale() == 2.0
        assert Affine.translation(5.0, 0.0).uniform_scale() == 1.0
        assert Affine.scaling(2.0, 3.0).uniform_scale() is None
        assert Affine(a=0.0, b=1.0, c=-1.0, d=0.0).uniform_scale() is None

    def test_map_rect_is_exact_for_translate_and_scale(self):
        r = Rect(0.0, 0.0, 10.0, 4.0)
        assert Affine.translation(1.0, 2.0).map_rect(r) == Rect(1.0, 2.0, 10.0, 4.0)
        assert Affine.scaling(2.0).map_rect(r) == Rect(0.0, 0.0, 20.0, 8.0)

    def test_map_rect_of_a_rotated_rect_is_a_bounding_box(self):
        """旋转下矩形的像不是轴对齐矩形，只能给包围盒——这是唯一诚实的答案。"""
        rot90 = Affine(a=0.0, b=1.0, c=-1.0, d=0.0)
        box = rot90.map_rect(Rect(0.0, 0.0, 10.0, 4.0))
        assert box.width == pytest.approx(4.0)
        assert box.height == pytest.approx(10.0)

    def test_as_tuple_is_serialisable(self):
        assert Affine.scaling(2.0).as_tuple() == (2.0, 0.0, 0.0, 2.0, 0.0, 0.0)

    def test_affine_is_immutable(self):
        with pytest.raises(AttributeError):
            Affine().a = 5.0  # type: ignore[misc]


def _inked_extent(fb: FrameBuffer) -> tuple[int, int]:
    """墨迹的宽高（像素）。"""
    xs = [x for x in range(fb.width) if any(fb.pixel(x, y)[3] for y in range(fb.height))]
    ys = [y for y in range(fb.height) if any(fb.pixel(x, y)[3] for x in range(fb.width))]
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


class TestRecorderTransform:
    """R3.3：变换栈端到端（录制 → 光栅）。

    此前录制器只有"累计平移"，整个 gfx 层没有缩放的概念，
    于是 125%/150% DPI 缩放与 `motion/` 的缩放动画都无处可挂。
    """

    def test_scale_doubles_a_rect(self):
        recorder = DisplayListRecorder()
        recorder.scale(2.0)
        recorder.fill_rect(Rect(0.0, 0.0, 10.0, 10.0), FILL)
        fb = rasterize_display_list(recorder.finish(40, 40))
        assert _inked_extent(fb) == (20, 20)

    def test_translate_then_scale_keeps_the_element_in_place(self):
        """先定位、再缩放 = 绕**自己**的原点放大（Skia 的语义）。

        如果反过来（缩放把位置也乘进去），元素会一边变大一边往右下跑——
        这正是"复合顺序必须定死"的原因。
        """
        recorder = DisplayListRecorder()
        recorder.translate(10.0, 0.0)
        recorder.scale(2.0)
        recorder.fill_rect(Rect(0.0, 0.0, 5.0, 5.0), FILL)
        op = recorder.finish(40, 40).ops[0]
        assert isinstance(op, FillRectOp)
        assert op.rect == Rect(10.0, 0.0, 10.0, 10.0)

    def test_scale_then_translate_moves_within_the_scaled_space(self):
        """先缩放、再平移 = 位移量也被放大（进入放大后的坐标系）。"""
        recorder = DisplayListRecorder()
        recorder.scale(2.0)
        recorder.translate(5.0, 0.0)
        recorder.fill_rect(Rect(0.0, 0.0, 5.0, 5.0), FILL)
        op = recorder.finish(40, 40).ops[0]
        assert isinstance(op, FillRectOp)
        assert op.rect == Rect(10.0, 0.0, 10.0, 10.0)

    def test_save_restore_restores_the_transform(self):
        recorder = DisplayListRecorder()
        recorder.save()
        recorder.scale(3.0)
        recorder.restore()
        recorder.fill_rect(Rect(0.0, 0.0, 10.0, 10.0), FILL)
        op = recorder.finish(40, 40).ops[0]
        assert isinstance(op, FillRectOp)
        assert op.rect == Rect(0.0, 0.0, 10.0, 10.0), "restore 之后应当回到未缩放的状态"

    def test_clip_is_mapped_through_the_transform(self):
        recorder = DisplayListRecorder()
        recorder.scale(2.0)
        recorder.clip_rect(Rect(0.0, 0.0, 5.0, 5.0))
        recorder.fill_rect(Rect(0.0, 0.0, 20.0, 20.0), FILL)
        op = recorder.finish(40, 40).ops[0]
        assert isinstance(op, FillRectOp)
        assert op.clip == Rect(0.0, 0.0, 10.0, 10.0)

    def test_scale_one_is_pixel_identical_to_no_scale(self):
        """scale=1 必须逐像素相同——否则黄金图会因为"什么都没改"而变。"""
        plain = DisplayListRecorder()
        plain.fill_rect(Rect(2.0, 3.0, 10.0, 6.0), FILL)
        scaled = DisplayListRecorder()
        scaled.scale(1.0)
        scaled.fill_rect(Rect(2.0, 3.0, 10.0, 6.0), FILL)
        assert (
            rasterize_display_list(plain.finish(20, 20)).data
            == rasterize_display_list(scaled.finish(20, 20)).data
        )

    def test_zero_scale_is_rejected(self):
        with pytest.raises(ValueError, match="不能为 0"):
            DisplayListRecorder().scale(0.0)

    def test_scaling_scales_the_stroke_width_and_radius_too(self):
        """线宽与圆角是局部长度：不跟着缩放的话，放大两倍后边框细得像没有。"""
        recorder = DisplayListRecorder()
        recorder.scale(2.0)
        recorder.stroke_rect(Rect(0.0, 0.0, 10.0, 10.0), 1.0, FILL, 3.0)
        op = recorder.finish(40, 40).ops[0]
        assert isinstance(op, StrokeRectOp)
        assert op.rect == Rect(0.0, 0.0, 20.0, 20.0)
        assert op.width == pytest.approx(2.0)
        assert op.radius == pytest.approx(6.0)


class TestPathInstructions:
    """R3.4：path 指令的数据形态（只定形状，光栅暂不实现）。

    决策（docs/17 §R3.4 已定）：扁平 verb 数组，**不接受 SVG path 字符串**。
    解析器进了渲染层，"同样的路径"就有两种写法（相对/绝对、隐含重复、
    科学计数法），显示列表的逐指令等值比对立刻失效——而黄金图与
    "AI 能稳定生成界面"都建立在这条比对之上。
    """

    TRIANGLE: PathData = ((MOVE, 0.0, 0.0), (LINE, 10.0, 0.0), (LINE, 0.0, 10.0), (CLOSE,))

    def test_path_fill_op_holds_flat_verbs(self):
        op = PathFillOp(self.TRIANGLE, FILL)
        assert op.data[0] == (MOVE, 0.0, 0.0)
        assert op.clip is None

    def test_path_stroke_op_carries_the_width(self):
        op = PathStrokeOp(self.TRIANGLE, FILL, 2.0)
        assert op.width == pytest.approx(2.0)

    def test_all_verbs_are_accepted(self):
        data: PathData = (
            (MOVE, 0.0, 0.0),
            (LINE, 1.0, 1.0),
            (QUAD, 2.0, 2.0, 3.0, 3.0),
            (CUBIC, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0),
            (CLOSE,),
        )
        assert set(PATH_VERBS) == {MOVE, LINE, QUAD, CUBIC, CLOSE}
        PathFillOp(data, FILL)  # 不该抛

    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            ((("X", 0.0, 0.0),), "动词非法"),
            (((MOVE, 0.0),), "坐标个数不对（M 要两个）"),
            (((CLOSE, 1.0),), "Z 不吃坐标"),
            (((MOVE, 0.0, 0.0, 0.0),), "坐标个数不对（多给了）"),
            ((), "空路径"),
            ((((),),), "空命令"),
        ],
    )
    def test_malformed_paths_fail_at_construction(self, data: object, reason: str):
        """**在构造时炸，不在光栅时炸**。

        光栅端拿到非法路径只能"画一半然后放弃"，那是静默失败；
        构造时抛异常则把错误定位到写出这条路径的那一行代码。
        """
        with pytest.raises(ValueError):
            PathFillOp(data, FILL)  # type: ignore[arg-type]

    def test_non_numeric_coordinates_are_rejected(self):
        with pytest.raises(ValueError, match="必须是数字"):
            PathFillOp(((MOVE, "0", 0.0),), FILL)  # type: ignore[arg-type]

    def test_describe_does_not_dump_all_coordinates(self):
        """路径可能很长：检查器只报命令数与动词序列。"""
        text = DisplayList(20, 20, (PathFillOp(self.TRIANGLE, FILL),)).describe()
        assert "path-fill" in text
        assert "MLLZ" in text
        assert "0.0" not in text.replace("20×20", "")

    def test_rasterizing_a_path_raises_loudly(self):
        """**响亮地未实现**，不静默跳过——静默跳过会让"图标没画出来"变成要查半天的问题。"""
        display_list = DisplayList(20, 20, (PathFillOp(self.TRIANGLE, FILL),))
        with pytest.raises(NotImplementedError, match="路径指令"):
            rasterize_display_list(display_list)

    def test_rasterizing_a_path_stroke_raises_loudly(self):
        display_list = DisplayList(20, 20, (PathStrokeOp(self.TRIANGLE, FILL, 1.0),))
        with pytest.raises(NotImplementedError, match="PathStrokeOp"):
            rasterize_display_list(display_list)

    def test_path_ops_are_immutable_and_hashable(self):
        """指令是不可变数据类：要能进集合、能当字典键、能逐指令比对。"""
        a = PathFillOp(self.TRIANGLE, FILL)
        b = PathFillOp(self.TRIANGLE, FILL)
        assert a == b
        assert len({a, b}) == 1


@pytest.mark.slow
def test_full_screen_opaque_fill_perf():
    """1280×800 全屏不透明填充 < 50ms（R3.5 的验收）。

    审查实测（R3 之前）**749ms**：`_accumulate` / `_paint_row` / `_clear`
    全是逐像素 Python 循环，1280×800 = 102 万次"逐像素垂直采样"。
    对齐的不透明直角矩形每行覆盖度恒为 1.0，那些采样全是白算的，
    所以走整行切片直通即可——降一个数量级，仍留 CI 余量。

    预算 50ms 而不是"实测值的两倍"：它的作用是抓**数量级回归**
    （比如谁把快路径的条件改坏了，749ms 会立刻回来），不是卡毫秒。
    """
    width, height = 1280, 800
    recorder = DisplayListRecorder()
    recorder.fill_rect(Rect(0.0, 0.0, float(width), float(height)), FILL)
    display_list = recorder.finish(width, height)

    def one_frame() -> None:
        rasterize_display_list(display_list)

    fastest, median = measure_best(one_frame, runs=5)
    print(
        f"\n[perf] 1280×800 全屏不透明填充：最快 {fastest:.2f}ms / 中位 {median:.2f}ms（预算 50ms）"
    )
    assert fastest < 50.0, (
        f"全屏不透明填充最快一次 {fastest:.2f}ms，超出 50ms 预算。"
        f"多半是 `_fill_aligned_opaque` 的快路径条件被改坏了（审查实测慢路径 749ms）"
    )


class TestFastPathEquivalence:
    """快路径必须与通用路径**逐比特相同**——否则"优化"就是在改画面。

    手法是把快路径关掉跑一遍再比对（`SoftwareRasterizer(fast_paths=False)`），
    而不是靠构造"刚好绕开快路径"的几何去猜。这条等价性正是快路径敢默认开的前提。
    """

    @staticmethod
    def _raster(
        rect: Rect,
        color: Color,
        *,
        clip: Rect | None = None,
        radius: float = 0.0,
        fast: bool = True,
    ) -> FrameBuffer:
        recorder = DisplayListRecorder()
        if clip is not None:
            recorder.clip_rect(clip)
        if radius > 0.0:
            recorder.round_rect(rect, radius, color)
        else:
            recorder.fill_rect(rect, color)
        raster = SoftwareRasterizer(fast_paths=fast)
        raster.begin_frame(Size(24.0, 24.0), 1.0)
        raster.execute(recorder.finish(24, 24))
        raster.end_frame()
        return raster.screenshot()

    @pytest.mark.parametrize(
        "rect",
        [
            Rect(0.0, 0.0, 24.0, 24.0),  # 全画布
            Rect(4.0, 4.0, 10.0, 10.0),  # 对齐的内部矩形
            Rect(0.0, 0.0, 3.0, 24.0),  # 贴边
            Rect(20.0, 20.0, 10.0, 10.0),  # 溢出画布
            Rect(-5.0, -5.0, 10.0, 10.0),  # 负坐标
        ],
    )
    def test_aligned_rect_matches_the_general_path(self, rect: Rect):
        assert self._raster(rect, FILL, fast=True).data == self._raster(rect, FILL, fast=False).data

    def test_fractional_rect_matches_and_has_partial_coverage(self):
        """边缘落在像素中间 → 必须走通用路径；顺便证明那里真有部分覆盖。"""
        rect = Rect(4.5, 4.5, 10.0, 10.0)
        assert self._raster(rect, FILL, fast=True).data == self._raster(rect, FILL, fast=False).data
        fb = self._raster(rect, FILL, fast=True)
        assert fb.pixel(4, 8)[:3] != (FILL.r, FILL.g, FILL.b), "半像素边界上应当是部分覆盖"

    def test_translucent_rect_matches(self):
        """半透明要混合，切片直通给不出混合结果。"""
        half = Color(FILL.r, FILL.g, FILL.b, 0.5)
        rect = Rect(4.0, 4.0, 10.0, 10.0)
        assert self._raster(rect, half, fast=True).data == self._raster(rect, half, fast=False).data

    def test_rounded_rect_matches(self):
        rect = Rect(4.0, 4.0, 12.0, 12.0)
        assert (
            self._raster(rect, FILL, radius=4.0, fast=True).data
            == self._raster(rect, FILL, radius=4.0, fast=False).data
        )

    def test_clipped_aligned_rect_matches(self):
        rect = Rect(0.0, 0.0, 24.0, 24.0)
        clip = Rect(4.0, 4.0, 8.0, 8.0)
        assert (
            self._raster(rect, FILL, clip=clip, fast=True).data
            == self._raster(rect, FILL, clip=clip, fast=False).data
        )

    def test_fractional_clip_matches(self):
        """裁剪边是小数时，被切出来的边界需要部分覆盖 → 通用路径。"""
        rect = Rect(0.0, 0.0, 24.0, 24.0)
        clip = Rect(4.5, 4.5, 8.0, 8.0)
        assert (
            self._raster(rect, FILL, clip=clip, fast=True).data
            == self._raster(rect, FILL, clip=clip, fast=False).data
        )

    def test_fast_path_actually_engaged(self):
        """别让"等价"变成"两边都走了通用路径"——那样测试什么也没证明。"""
        rect = Rect(0.0, 0.0, 512.0, 512.0)

        def one_frame(fast: bool) -> None:
            recorder = DisplayListRecorder()
            recorder.fill_rect(rect, FILL)
            raster = SoftwareRasterizer(fast_paths=fast)
            raster.begin_frame(Size(512.0, 512.0), 1.0)
            raster.execute(recorder.finish(512, 512))
            raster.end_frame()

        fast_ms, _ = measure_best(lambda: one_frame(True), runs=5)
        slow_ms, _ = measure_best(lambda: one_frame(False), runs=5)
        assert fast_ms * 5 < slow_ms, (
            f"快路径没有明显更快：fast={fast_ms:.2f}ms slow={slow_ms:.2f}ms"
            f"——多半是条件写坏了，根本没走到"
        )


class TestFrameBuffer:
    def test_pixel_out_of_range_is_rejected(self):
        fb = rasterize_display_list(DisplayList(4, 4, ()))
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
