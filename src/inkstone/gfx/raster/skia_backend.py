"""Skia 光栅后端（首选）

Chrome/Flutter 同款，路径与文字质量最好，支持 CPU/GPU 双模。

它要实现的是 `raster/base.py` 的 `RasterBackend` 协议（docs/03 §2）：
`begin_frame → execute* → end_frame → screenshot`。

Skia 落这条协议几乎是直译：`begin_frame` 建/复用 `SkSurface`，
`execute` 把显示列表翻译成 `SkCanvas` 调用（`clip` 落成 `canvas.clipRect`），
`end_frame` 提交，`screenshot` 走 `SkImage.readPixels`。
`create_image` / `destroy_image` 对应 `SkImage` 的生命周期。

注意黄金图的角色分工（docs/03 §3 的后端选型表）：Skia 上线后，**事实源仍是软件光栅**——
Skia 的输出与它比对（容忍抗锯齿差异），而不是反过来。所以这个后端
不需要追求与软件光栅逐比特相同，它追求的是"看起来对、跑得快"。

Phase 归属（R3.6 统一口径）：**Skia 属 Phase 3**；自研 GL 属 Phase 1 兜底。

状态：协议已定（R3.1），实现待做（见 ROADMAP.md）。
"""

__all__: list[str] = []
