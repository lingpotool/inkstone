"""布局引擎的性能与确定性验收测试。

对应 docs/05 §8 的两条硬指标：

    - 1000 节点全量布局 < 5ms
    - 同一棵树重复布局两次，几何完全一致

这两条一旦破了，界面要么卡、要么"每次打开长得不一样"，都属于回归。
用测试钉住，比靠人记得跑 benchmark 可靠。
"""

from __future__ import annotations

import time

import pytest

from inkstone.layout import (
    BoxConstraints,
    RenderColumn,
    RenderRow,
    RenderSized,
    Sizing,
    collect_descendants,
)

# 预算放宽到 2ms：CI 机器慢、且这里测的是"数量级"而不是绝对值。
# 真正的预算（5ms）写在 docs/05；留 2.5 倍余量是为了避免偶发抖动造成假红。
FULL_LAYOUT_BUDGET_MS = 2.0


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


CONSTRAINTS = BoxConstraints(max_width=1200, max_height=800)


def test_tree_has_expected_node_count():
    assert len(collect_descendants(build_tree())) == 1001


@pytest.mark.slow
def test_full_layout_of_1000_nodes_stays_within_budget():
    root = build_tree()
    for _ in range(3):  # 预热，避开首次执行的导入与分支预测开销
        root.mark_needs_layout()
        root.layout(CONSTRAINTS)

    runs = 20
    start = time.perf_counter()
    for _ in range(runs):
        root.mark_needs_layout()
        root.layout(CONSTRAINTS)
    elapsed_ms = (time.perf_counter() - start) / runs * 1000

    assert elapsed_ms < FULL_LAYOUT_BUDGET_MS, (
        f"1001 节点全量布局耗时 {elapsed_ms:.2f}ms，超出 {FULL_LAYOUT_BUDGET_MS}ms 预算"
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

    root.mark_needs_layout()
    start = time.perf_counter()
    root.layout(CONSTRAINTS)
    full_ms = (time.perf_counter() - start) * 1000

    assert cached_ms * 50 < full_ms, (
        f"缓存路径没有明显更快：cached={cached_ms:.4f}ms full={full_ms:.4f}ms"
    )


def test_repeated_layout_is_bit_for_bit_identical():
    root = build_tree(rows=20, per_row=3)

    def snapshot() -> list[tuple[float, float, float, float]]:
        root.mark_needs_layout()
        root.layout(CONSTRAINTS)
        return [
            (n.offset.dx, n.offset.dy, n.size.width, n.size.height)
            for n in collect_descendants(root)
        ]

    first = snapshot()
    for _ in range(5):
        assert snapshot() == first
