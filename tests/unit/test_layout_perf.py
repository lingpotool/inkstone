"""布局引擎的性能与确定性验收测试。

对应 docs/05 §8 的两条硬指标，但**口径必须诚实**（docs/16 §R2.3）：

    - 1000 节点**增量**布局 < 5ms   （只标脏根节点：一次根布局 + 250 次缓存查询）
    - 1000 节点**全量**布局 < 30ms  （整棵树都标脏：每一层都真的重排）
    - 同一棵树重复布局两次，几何完全一致

**为什么必须拆成两个用例**：此前只有一个"1001 节点全量布局 0.89ms"的用例，
但它每轮只调 `root.mark_needs_layout()`——而该方法只**向上冒泡**，
于是第二轮起 250 个 Row 与 750 个叶子全部缓存命中。那个数字测的是
"一次根节点布局 + 250 次缓存查询"，却被当成"全量布局"写进了文档。
审查实测真全量 14.6ms，是它的 16 倍。

这不是"数字不好看"的问题，而是**假保证**：一个建立在假测量上的预算，
会让真正的数量级回归（比如误写成 O(n²)）从门禁底下溜过去。
所以拆成两条并各自改名——名字要说清它到底测了什么。

性能优化本身不在 R2 的范围（记入 docs/20 的后补清单）。
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

from inkstone.layout import (
    BoxConstraints,
    RenderBox,
    RenderColumn,
    RenderRow,
    RenderSized,
    Sizing,
    collect_descendants,
)

# 增量预算：docs/05 §8 的原值。断言的就是文档里那个数，不额外收紧——
# 墙上时钟断言在共享 CI runner 上天生会抖，紧阈值会变成"偶尔红一次"，
# 而一个偶尔红的门禁比没有更糟（大家会学会忽略 CI）。
INCREMENTAL_LAYOUT_BUDGET_MS = float(os.environ.get("INKSTONE_PERF_BUDGET_MS", "5.0"))

# 全量预算：本机实测 17.3ms（R1 之后；审查时为 14.6ms），按 2 倍余量定 30ms。
# 同样**不收紧**：它的作用是抓数量级回归，不是卡毫秒。
FULL_LAYOUT_BUDGET_MS = float(os.environ.get("INKSTONE_PERF_FULL_BUDGET_MS", "30.0"))


def build_tree(rows: int = 250, per_row: int = 3) -> RenderColumn:
    """造一棵 1 + rows + rows*per_row 个节点的树。"""
    root = RenderColumn(gap=2, debug_name="Root")
    for r in range(rows):
        row = RenderRow(gap=4, debug_name=f"Row{r}")
        root.add(row)
        for c in range(per_row):
            row.add(
                RenderSized(width=Sizing.fixed(30), height=Sizing.fixed(20), debug_name=f"C{c}"),
                flex=1,
            )
    return root


def mark_tree_dirty(root: RenderBox) -> None:
    """整棵树标脏——这才是"全量布局"的前提。

    `mark_needs_layout()` 只向上冒泡（父级需要重排），子级约束没变时会走缓存。
    要真全量就得用向下的 `mark_subtree_needs_layout()`。
    """
    root.mark_subtree_needs_layout()


def measure_layout(root: RenderBox, *, full: bool, runs: int = 20) -> tuple[float, float]:
    """跑 `runs` 轮，返回 (最快一次, 中位数)，单位毫秒。

    **断言用最快一次，不用平均。** 这不是为了让数字好看，而是因为
    墙上时钟的噪声是**单向的**：别的进程抢 CPU、GC、调度抖动只会让某一次
    变慢，绝不会让它比真实成本更快。所以 N 次里的最小值是对真实成本最稳的
    估计，也把"偶尔被干扰"这个失败模式从根上消掉。

    用平均会怎样（R2 收尾时实测到的）：200 次采样 p50=16.2ms、p99=30.8ms、
    max=35.4ms，**有 2 次越过 30ms 预算**。20 次取平均虽然把单次抖动摊薄了
    20 倍，但一次上百毫秒的抖动照样能把平均拉过线——于是门禁"偶尔红一次"。
    而一个偶尔红的门禁比没有更糟：它会训练所有人忽略 CI（AGENT.md 的既有结论）。

    代价要说清楚：min 口径下典型值是 13ms，对 30ms 预算有 2.3 倍余量，
    所以它抓的是 **≥2.3 倍**的回归，不是 2 倍。这正是性能门禁该干的事
    （抓数量级回归），文档也是这么定的。

    中位数照常返回并打印：趋势比门槛有价值。
    """
    for _ in range(3):  # 预热：避开首次执行的导入与分支预测开销
        if full:
            mark_tree_dirty(root)
        else:
            root.mark_needs_layout()
        root.layout(CONSTRAINTS)

    samples: list[float] = []
    for _ in range(runs):
        if full:
            mark_tree_dirty(root)
        else:
            root.mark_needs_layout()
        start = time.perf_counter()
        root.layout(CONSTRAINTS)
        samples.append((time.perf_counter() - start) * 1000)
    return min(samples), statistics.median(samples)


CONSTRAINTS = BoxConstraints(max_width=1200, max_height=800)


def test_tree_has_expected_node_count():
    assert len(collect_descendants(build_tree())) == 1001


def test_mark_tree_dirty_actually_dirty_every_node():
    """先证明"全量"这个前提成立——否则下面那条预算又是在测缓存。"""
    root = build_tree(rows=20, per_row=3)
    root.layout(CONSTRAINTS)
    assert not any(node.needs_layout for node in collect_descendants(root))

    mark_tree_dirty(root)
    assert all(node.needs_layout for node in collect_descendants(root)), (
        "整树标脏必须覆盖每一个节点，不然「全量布局」名不副实"
    )


@pytest.mark.slow
def test_layout_perf_incremental():
    """增量布局预算：只标脏根节点。

    这是真实界面里最常见的帧：一个属性变了 → 冒泡到根 → 根重排，
    子级约束没变所以全部命中缓存。**它不等于"全量布局"**。
    """
    root = build_tree()
    fastest, median = measure_layout(root, full=False)

    print(
        f"\n[perf] 1001 节点增量布局（只标根）：最快 {fastest:.2f}ms / 中位 {median:.2f}ms"
        f"（预算 {INCREMENTAL_LAYOUT_BUDGET_MS}ms）"
    )

    assert fastest < INCREMENTAL_LAYOUT_BUDGET_MS, (
        f"1001 节点增量布局最快一次 {fastest:.2f}ms，超出 {INCREMENTAL_LAYOUT_BUDGET_MS}ms 预算"
        f"（docs/05 §8 的指标是 5ms；可用 INKSTONE_PERF_BUDGET_MS 调整）"
    )


@pytest.mark.slow
def test_layout_perf_full():
    """真全量布局预算：整棵树都标脏，每一层都真的重排。

    触发场景是真实存在的：换主题、DPI 缩放变化、检查器强制重算、
    窗口尺寸变化导致约束全体变化。这条用例的存在本身就是"假保证"的补丁。
    """
    root = build_tree()
    fastest, median = measure_layout(root, full=True)

    print(
        f"\n[perf] 1001 节点全量布局（整树标脏）：最快 {fastest:.2f}ms / 中位 {median:.2f}ms"
        f"（预算 {FULL_LAYOUT_BUDGET_MS}ms）"
    )

    assert fastest < FULL_LAYOUT_BUDGET_MS, (
        f"1001 节点全量布局最快一次 {fastest:.2f}ms，超出 {FULL_LAYOUT_BUDGET_MS}ms 预算。"
        f"这是真全量（整树标脏），不是增量——别把它和增量预算搞混。"
        f"（可用 INKSTONE_PERF_FULL_BUDGET_MS 调整）"
    )


@pytest.mark.slow
def test_cached_layout_is_effectively_free():
    """没标脏就重排时，应当只走缓存——这是"只重排脏子树"的收益证明。"""
    root = build_tree()
    root.layout(CONSTRAINTS)

    runs = 200
    start = time.perf_counter()
    for _ in range(runs):
        root.layout(CONSTRAINTS)
    cached_ms = (time.perf_counter() - start) / runs * 1000

    mark_tree_dirty(root)
    start = time.perf_counter()
    root.layout(CONSTRAINTS)
    full_ms = (time.perf_counter() - start) * 1000

    assert cached_ms * 50 < full_ms, (
        f"缓存路径没有明显更快：cached={cached_ms:.4f}ms full={full_ms:.4f}ms"
    )


def test_repeated_layout_is_bit_for_bit_identical():
    root = build_tree(rows=20, per_row=3)

    def snapshot() -> list[tuple[float, float, float, float]]:
        mark_tree_dirty(root)
        root.layout(CONSTRAINTS)
        return [
            (n.offset.dx, n.offset.dy, n.size.width, n.size.height)
            for n in collect_descendants(root)
        ]

    first = snapshot()
    for _ in range(5):
        assert snapshot() == first
