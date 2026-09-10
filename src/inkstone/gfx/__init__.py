"""渲染管线（L2）：显示列表 + 可插拔光栅后端。

一句话记住这条管线：

    RenderObject.paint() → DisplayList（指令）→ RasterBackend → 像素

只有这一层接触"画"的概念。上层（布局 / 组件树 / 组件）只负责把
**绘制意图**写进显示列表；画到哪、用什么画，是后端的事。

```python
recorder = DisplayListRecorder()
owner.begin_frame(constraints, recorder)
display_list = recorder.finish(320, 600)
frame = SoftwareRasterizer().rasterize(display_list)
png = encode_png(frame.width, frame.height, bytes(frame.data))
```

状态：显示列表 / 录制器 / 软件光栅 / PNG 编码已实现；
GL + Skia 后端属 Phase 3（docs/03）。
"""

from .color import Color
from .display_list import DisplayList, FillRectOp, Op, StrokeRectOp
from .paint import DisplayListRecorder
from .raster.base import FrameBuffer, RasterBackend
from .raster.software import SoftwareRasterizer, encode_png

__all__ = [
    "Color",
    "DisplayList",
    "DisplayListRecorder",
    "FillRectOp",
    "FrameBuffer",
    "Op",
    "RasterBackend",
    "SoftwareRasterizer",
    "StrokeRectOp",
    "encode_png",
]
