"""自研 GL 光栅后端（兜底）

零额外依赖，三角化绘制；用于最小化体积与 Skia 不可用场景。

它要实现的是 `raster/base.py` 的 `RasterBackend` 协议（docs/03 §2）：

    begin_frame(size, scale) → execute(display_list, clip)* → end_frame() → screenshot()

对 GL 来说这条形状才成立：正常路径是画进 GPU 表面再交换（`end_frame`），
`execute` 的 `clip` 直接落成 `glScissor`（脏矩形重绘的天然映射），
而 `screenshot()` 是一次显式的 `glReadPixels` 回读——**异常操作**，不是主路径。
`create_image` / `destroy_image` 对应纹理的分配与释放：GL 资源不在 Python 的
GC 管辖内，必须显式释放。

Phase 归属（R3.6 统一口径）：**自研 GL 属 Phase 1 兜底**——它的存在意义是
"没有 Skia 也能出图"，属于能发布的最小条件；Skia 属 Phase 3 的质量升级。

状态：协议已定（R3.1），实现待做（见 ROADMAP.md）。
"""

__all__: list[str] = []
