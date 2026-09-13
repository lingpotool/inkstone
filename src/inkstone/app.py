"""应用外壳（L8）：把框架能力组合成"一个真正的应用程序"。

窗口生命周期、帧调度入口、应用级服务（路由/主题/持久化）的组合点。

本模块当前的落地部分是**应用图标**（R10.3）：

    python -c "from inkstone.app import app_icon_ico; open('inkstone.ico','wb').write(app_icon_ico())"

**为什么图标是"画"出来的，不是仓库里的 .ico**：

- 二进制资产进仓库就没有 diff、没有评审、没有来源，改一次颜色要靠设计软件；
- 这里的图标用自家的显示列表 + 软件光栅生成，**同一份代码在任何机器上
  产出逐字节相同的 RGBA**，因此可以进黄金图测试；
- 需要分发时（打包 exe / 桌面条目）用 `tools/build_icon.py` 导出多尺寸 .ico，
  导出物是构建产物，不进版本库。

图标几何：圆角方石（砚）+ 环形砚池 + 中心墨点。纯几何、不依赖任何字体文件，
所以在最小部署里也画得出来。
"""

from __future__ import annotations

import struct

from .gfx import Color, DisplayListRecorder, SoftwareRasterizer
from .gfx.raster.software import encode_png
from .layout.types import Rect, Size

__all__ = ["ICON_SIZES", "app_icon_ico", "app_icon_png", "app_icon_rgba"]

#: 打包用的标准尺寸档（16 任务栏小图 → 256 高 DPI / 商店大图）。
ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# 品牌色写在应用外壳层，而不是主题令牌里：图标不该随明暗主题变色
# （任务栏与桌面快捷方式没有"当前主题"这回事）。
_ICON_STONE = Color.from_hex("#2F3B52")  # 砚石（墨蓝）
_ICON_POOL = Color.from_hex("#F2ECDD")  # 砚池边缘（宣纸白）
_ICON_INK = Color.from_hex("#F2ECDD")  # 墨点

#: 圆角半径 / 尺寸。22% 是桌面图标里"明显是圆角但不至于像药丸"的档位。
_RADIUS_RATIO = 0.22


def app_icon_rgba(size: int) -> bytes:
    """生成 `size × size` 的图标 RGBA 像素（逐字节确定）。

    画法（自下而上）：圆角方石 → 环形砚池 → 中心墨点。
    """
    if size < 8:
        raise ValueError(f"图标尺寸至少 8px（再小笔画会糊成一团），收到 {size}")
    side = float(size)
    recorder = DisplayListRecorder()
    recorder.round_rect(Rect(0.0, 0.0, side, side), side * _RADIUS_RATIO, _ICON_STONE)

    # 砚池：一个圆环。用圆角描边的矩形表示——半径取边长一半就是正圆。
    inset = side * 0.24
    pool_side = side - inset * 2.0
    recorder.stroke_rect(
        Rect(inset, inset, pool_side, pool_side),
        width=side * 0.075,
        color=_ICON_POOL,
        radius=pool_side / 2.0,
    )

    # 墨点：中心的实心圆。
    dot = side * 0.2
    recorder.round_rect(
        Rect((side - dot) / 2.0, (side - dot) / 2.0, dot, dot), dot / 2.0, _ICON_INK
    )

    raster = SoftwareRasterizer()
    raster.begin_frame(Size(side, side), 1.0)
    raster.execute(recorder.finish(size, size))
    raster.end_frame()
    return bytes(raster.screenshot().data)


def app_icon_png(size: int = 256) -> bytes:
    """单尺寸 PNG（检查器预览 / 打包资源用）。"""
    return encode_png(size, size, app_icon_rgba(size))


def app_icon_ico(sizes: tuple[int, ...] = ICON_SIZES) -> bytes:
    """多尺寸 .ico（每个条目内嵌 PNG）。

    ICO 从 Vista 起支持 PNG 负载，所以这里不需要再写一遍 BMP 编码器；
    256px 也必须用 PNG（BMP 条目在 256 上有尺寸字段溢出的历史坑）。
    """
    images = [(size, app_icon_png(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type=icon, count
    offset = len(header) + 16 * len(images)
    entries = bytearray()
    for size, png in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 0 表示 256
            0 if size >= 256 else size,
            0,  # 调色板数（无）
            0,  # 保留
            1,  # 色彩平面
            32,  # 每像素位数
            len(png),
            offset,
        )
        offset += len(png)
    return bytes(header + entries + b"".join(png for _, png in images))
