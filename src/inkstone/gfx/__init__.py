"""渲染管线（L2）：显示列表 + 可插拔光栅后端。

一句话记住这条管线：

    RenderObject.paint() → DisplayList（指令）→ RasterBackend → 像素

只有这一层接触"画"的概念。上层（布局 / 组件树 / 组件）只负责把
**绘制意图**写进显示列表；画到哪、用什么画，是后端的事。

```python
recorder = DisplayListRecorder()
owner.begin_frame(constraints, recorder)
display_list = recorder.finish(320, 600)

raster = SoftwareRasterizer()
raster.begin_frame(Size(320, 600), 1.0)
raster.execute(display_list)          # clip=None 表示整幅
raster.end_frame()
frame = raster.screenshot()           # 读回是显式操作
png = encode_png(frame.width, frame.height, bytes(frame.data))
```

**帧生命周期是 R3.1 重塑过的对外契口**（docs/03 §2）。旧的
`rasterize(display_list) -> FrameBuffer` 已删除：它把"读回内存"当成后端的唯一出口，
GL/Skia 在结构上无法实现它。现在帧缓冲归后端所有，`screenshot()` 只是读回，
脏矩形是 `execute` 的参数（显示列表保持"纯图纸"，重绘策略不污染它的比对语义）。

状态：显示列表 / 录制器 / 软件光栅 / PNG 编码 / 文本绘制已实现；
**自研 GL 后端属 Phase 1 兜底**（保证「不装 Skia 也能跑」），
Skia 属 Phase 3 的质量升级——两句话以 docs/03 §3 的选型表为准。

关于文本：显示列表的文本指令是 `TextRunOp`——它承载**已定位的字形**
而不是字符串（docs/03 的 `text_run`）。整形与断行在 L3 文本层完成，
本层只负责把字形画成像素，所以渲染层不需要知道换行与回退的任何规则。
字形形状来自可替换的 `GlyphProvider`：默认是内置确定性字形
（无字体文件，ASCII 真位图 + 非拉丁占位块），服务黄金图与无头 CI；
平台真字形由后端提供，替换 provider 即可，光栅与组件一行不改。
"""

from .color import Color
from .display_list import (
    CLOSE,
    CUBIC,
    LINE,
    MOVE,
    PATH_VERBS,
    QUAD,
    DisplayList,
    FillRectOp,
    Op,
    PathCommand,
    PathData,
    PathFillOp,
    PathStrokeOp,
    PositionedGlyph,
    StrokeRectOp,
    TextRunOp,
)
from .glyphs import BuiltinGlyphProvider, GlyphMask, GlyphProvider, rect_of_mask
from .paint import DisplayListRecorder
from .raster.base import FrameBuffer, RasterBackend, RasterError
from .raster.software import SoftwareRasterizer, encode_png
from .transform import IDENTITY, Affine

__all__ = [
    "CLOSE",
    "CUBIC",
    "IDENTITY",
    "LINE",
    "MOVE",
    "PATH_VERBS",
    "QUAD",
    "Affine",
    "BuiltinGlyphProvider",
    "Color",
    "DisplayList",
    "DisplayListRecorder",
    "FillRectOp",
    "FrameBuffer",
    "GlyphMask",
    "GlyphProvider",
    "Op",
    "PathCommand",
    "PathData",
    "PathFillOp",
    "PathStrokeOp",
    "PositionedGlyph",
    "RasterBackend",
    "RasterError",
    "SoftwareRasterizer",
    "StrokeRectOp",
    "TextRunOp",
    "encode_png",
    "rect_of_mask",
]
