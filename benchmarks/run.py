"""R7.5 性能基准 —— 三个真实场景，出帧耗时 p50/p95，驱动 GL 后端决策（docs/21）。

    python benchmarks/run.py                 # 跑全部场景，打印报告
    python benchmarks/run.py --scene scroll  # 只跑一个
    python benchmarks/run.py --frames 200 --json out.json

**为什么要自己写而不是用 pytest-benchmark**：我们不看平均值，要看**分布**
（p50/p95）。一帧偶尔卡到 40ms 比"平均 8ms"重要得多——用户感知的是卡顿，
不是均值。stdlib 的 `statistics` + 精确计时就够了，不必为一个报告引依赖。

**决策规则先写死再量**（docs/21 R7.5）：`P95_BUDGET_MS = 10.0`
（60fps 预算 16.6ms 的六成）。量完再调阈值等于给结论找理由。

场景都是**无头**的：软件光栅是当前唯一的真实后端，测的就是这条链的
build → layout → paint → 录制 → 光栅。度量用确定性字形表，跨机器可比。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from inkstone.backend import HeadlessBackend
from inkstone.core import BuildOwner
from inkstone.gfx import DisplayListRecorder, SoftwareRasterizer
from inkstone.layout import BoxConstraints, RenderScroll, Size
from inkstone.layout.types import Rect
from inkstone.style import Theme
from inkstone.text import TextEngine
from inkstone.widgets import Card, Column, ScrollView, Text

#: 决策阈值（先写死）：p95 超过它就启动 GL 后端子包，否则 GL 让位于 Phase 2。
P95_BUDGET_MS = 10.0

WIDTH = 800.0
HEIGHT = 600.0


@dataclass(frozen=True, slots=True)
class Scene:
    name: str
    description: str
    build: Callable[[BuildOwner], None]
    mutate: Callable[[BuildOwner, int], None]
    force_repaint: bool


def _owner() -> BuildOwner:
    return BuildOwner(theme=Theme.light(), text_engine=TextEngine(HeadlessBackend()))


# ---------------------------------------------------------------- 场景


def _scroll_build(owner: BuildOwner) -> None:
    """长列表：150 张卡片装进可滚动视口。"""
    cards = tuple(
        Card(
            child=Column(
                gap=4.0,
                children=(
                    Text(f"笔记 {i}", size="md"),
                    Text("滚动列表里的每一帧都要重排脏子树——这是真实 App 的主路径。", size="sm"),
                ),
            )
        )
        for i in range(150)
    )
    owner.mount(ScrollView(Column(children=cards, gap=8.0)))


def _scroll_mutate(owner: BuildOwner, frame: int) -> None:
    root = owner.root_render_object
    assert isinstance(root, RenderScroll)
    limit = root.max_scroll.dy
    # 循环滚动：到底了回到顶，保证每帧都有真实位移
    offset = (frame * 40.0) % (limit + 40.0) if limit > 0 else 0.0
    root.scroll_to(dy=offset)


def _fullscreen_build(owner: BuildOwner) -> None:
    """全屏重绘：1280×800 上一棵混合形状与文本的树，每帧强制全部重画。"""
    rows = tuple(
        Card(
            child=Column(
                gap=6.0,
                children=(
                    Text(f"面板 {i}", size="lg"),
                    Text("整帧重绘的场景：窗口 resize、主题切换、动画都会走这条路。", size="sm"),
                ),
            )
        )
        for i in range(24)
    )
    owner.mount(Column(children=rows, gap=10.0, padding=None))


def _reflow(owner: BuildOwner, frame: int) -> None:
    """每帧整树重排：文本密集页的真实成本在断行与排版，不只是重画。"""
    root = owner.root_render_object
    if root is not None:
        root.mark_subtree_needs_layout()


def _dense_build(owner: BuildOwner) -> None:
    """文本密集页：40 段中文，考的是断行 + 排版 + 字形光栅。"""
    paragraph = (
        "中文排版要处理标点禁则、字素簇与回退链；这一段会被反复断行与光栅化，"
        "用来观察文本密集页面的每帧成本。"
    )
    owner.mount(
        Column(
            gap=6.0,
            children=tuple(Text(paragraph, size="sm", max_lines=3) for _ in range(40)),
        )
    )


SCENES: dict[str, Scene] = {
    "scroll": Scene(
        name="scroll",
        description="长列表滚动（150 卡，仅脏子树重排/重绘）",
        build=_scroll_build,
        mutate=_scroll_mutate,
        force_repaint=False,
    ),
    "fullscreen": Scene(
        name="fullscreen",
        description="全屏重绘（1280×800 混合树，每帧强制全量）",
        build=_fullscreen_build,
        mutate=lambda owner, frame: None,
        force_repaint=True,
    ),
    "text": Scene(
        name="text",
        description="文本密集页（40 段中文，断行 + 排版 + 光栅）",
        build=_dense_build,
        mutate=_reflow,
        force_repaint=True,
    ),
}


# ---------------------------------------------------------------- 测量


def _make_raster(backend: str) -> object:
    """建光栅后端。`gl` 走真机 WGL（Windows + 有 GL 驱动才可用）。"""
    if backend == "software":
        return SoftwareRasterizer()
    try:
        from inkstone.backend.gl_wgl import windows_gl_driver
        from inkstone.gfx import GLRasterBackend
    except ImportError as exc:  # 非 Windows：ctypes.wintypes 不可用
        raise SystemExit(f"当前平台没有 GL 驱动实现（{exc}）") from exc
    driver = windows_gl_driver()
    if driver is None:
        raise SystemExit("本机没有可用的 OpenGL 驱动，无法跑 --backend gl")
    return GLRasterBackend(driver)


def measure(
    scene: Scene, *, frames: int, warmup: int = 10, backend: str = "software"
) -> list[float]:
    """跑 `warmup + frames` 帧，返回每帧耗时（ms）。"""
    owner = _owner()
    scene.build(owner)
    constraints = BoxConstraints(
        max_width=WIDTH * 1.6 if scene.name == "fullscreen" else WIDTH,
        max_height=(HEIGHT * 1.33) if scene.name == "fullscreen" else HEIGHT,
    )
    width = int(constraints.max_width)
    height = int(constraints.max_height)
    raster = _make_raster(backend)

    durations: list[float] = []
    try:
        for frame in range(warmup + frames):
            scene.mutate(owner, frame)
            start = time.perf_counter()
            recorder = DisplayListRecorder()
            recorder.fill_rect(Rect(0.0, 0.0, float(width), float(height)), owner.theme.color("bg"))
            owner.begin_frame(constraints, recorder, force_repaint=scene.force_repaint)
            display_list = recorder.finish(width, height)
            raster.begin_frame(Size(float(width), float(height)), 1.0)  # type: ignore[attr-defined]
            raster.execute(display_list)  # type: ignore[attr-defined]
            raster.end_frame()  # type: ignore[attr-defined]
            elapsed = (time.perf_counter() - start) * 1000.0
            if frame >= warmup:
                durations.append(elapsed)
    finally:
        close = getattr(raster, "close", None)
        if close is not None:
            close()
        else:
            driver = getattr(raster, "_driver", None)
            if driver is not None and hasattr(driver, "close"):
                driver.close()
    return durations


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def report(results: dict[str, list[float]]) -> tuple[dict[str, dict[str, float]], bool]:
    summary: dict[str, dict[str, float]] = {}
    needs_gl = False
    for name, durations in results.items():
        stats = {
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
            "max_ms": max(durations),
            "mean_ms": statistics.fmean(durations),
        }
        summary[name] = stats
        if stats["p95_ms"] > P95_BUDGET_MS:
            needs_gl = True
    return summary, needs_gl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inkstone R7.5 性能基准")
    parser.add_argument("--scene", choices=[*SCENES, "all"], default="all")
    parser.add_argument("--frames", type=int, default=120, help="每场景测量帧数（不含预热）")
    parser.add_argument(
        "--backend",
        choices=["software", "gl"],
        default="software",
        help="光栅后端（gl 走真机 WGL，仅 Windows + 有 GL 驱动时可用）",
    )
    parser.add_argument("--json", type=Path, default=None, help="把结果写成 JSON")
    args = parser.parse_args(argv)

    names = list(SCENES) if args.scene == "all" else [args.scene]
    results = {
        name: measure(SCENES[name], frames=args.frames, backend=args.backend) for name in names
    }
    summary, needs_gl = report(results)

    print(
        f"inkstone 性能基准 · 预算 p95 ≤ {P95_BUDGET_MS:g}ms（60fps 的六成） · 后端 {args.backend}"
    )
    print(f"{'场景':<12}{'p50':>9}{'p95':>9}{'max':>9}{'mean':>9}  说明")
    for name in names:
        stats = summary[name]
        print(
            f"{name:<12}{stats['p50_ms']:>8.2f}ms{stats['p95_ms']:>8.2f}ms"
            f"{stats['max_ms']:>8.2f}ms{stats['mean_ms']:>8.2f}ms  {SCENES[name].description}"
        )

    print()
    if needs_gl:
        print("结论：有场景 p95 超预算 → 启动 GL 后端子包（另立分包文档，不在 R7 scope）")
    else:
        print("结论：全部场景 p95 在预算内 → GL 后端让位于 Phase 2 铺组件，记入 docs/20 §R6.4")

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {"budget_p95_ms": P95_BUDGET_MS, "needs_gl": needs_gl, "scenes": summary},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
