"""光栅后端实现：软件光栅（确定性事实源）+ GL 后端（帧率路径，R8）+ Skia（Phase 3）。

三者实现同一个协议（`base.RasterBackend`），所以上层不需要知道用的是哪个。
"""

from .base import FrameBuffer, RasterBackend, RasterError, RasterFrameRenderer
from .gl_backend import GLRasterBackend
from .gl_driver import GLDriver
from .software import SoftwareRasterizer, encode_png

__all__ = [
    "FrameBuffer",
    "GLDriver",
    "GLRasterBackend",
    "RasterBackend",
    "RasterError",
    "RasterFrameRenderer",
    "SoftwareRasterizer",
    "encode_png",
]
