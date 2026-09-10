"""黄金图测试 —— 截图"逐字节相等"。

docs/06 §7 那条最关键的设计纪律在这里落地：

    同样的输入 → 同样的字节

测试流程：

    tests/golden/<name>.png  ←  手动检查过的基线（提交进仓库）
    tests/golden/failures/    ←  实际产物，.gitignore 已配（评审时看）

用法：

    正常跑：拿产物与基线逐字节比对，不一致就挂
    首次/更新基线：set INKSTONE_UPDATE_GOLDEN=1 重跑，把产物写回基线

**为什么黄金图值得这样重锤**：
- 它抓住的不只是"我调的颜色应该没问题"，而是"渲染管线的每一个环节
  都没改（包括布局、显示列表、光栅、PNG 编码）"
- 三台机器、一年后、换 Python 版本——只要它还是绿色，界面就还是一样的
- "AI 能稳定生成界面"这个目标的可执行版本：黄金图 = 黄金 prompt
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from inkstone.core import BuildOwner
from inkstone.devtools import render_to_png
from inkstone.layout import BoxConstraints
from inkstone.style import ButtonVariant, Theme
from inkstone.widgets import Box, Button, Card, Column, Flexible, Input, Row

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
FAILURES_DIR = GOLDEN_DIR / "failures"
UPDATE_ENV = "INKSTONE_UPDATE_GOLDEN"


def _constraints() -> BoxConstraints:
    return BoxConstraints(max_width=320, max_height=260)


def _build_login_form(theme: Theme) -> BuildOwner:
    owner = BuildOwner(theme=theme)
    owner.mount(
        Column(
            children=(
                Card(child=Box(height=64)),
                Input(placeholder="邮箱"),
                Input(placeholder="密码"),
                Row(
                    children=(
                        Button("取消", variant=ButtonVariant.GHOST, width=80),
                        Flexible(Button("登录"), flex=1),
                    ),
                    gap=8,
                ),
            ),
            gap=16,
        )
    )
    return owner


def _assert_or_update_golden(name: str, actual: bytes) -> None:
    baseline = GOLDEN_DIR / f"{name}.png"

    if os.environ.get(UPDATE_ENV) == "1" or not baseline.exists():
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(actual)
        return

    expected = baseline.read_bytes()
    if expected == actual:
        return

    FAILURES_DIR.mkdir(parents=True, exist_ok=True)
    (FAILURES_DIR / f"{name}.actual.png").write_bytes(actual)
    raise AssertionError(
        f"黄金图 {name}.png 不匹配。已把实际产物写到 {FAILURES_DIR}/{name}.actual.png"
        f"，请比对差异后用 INKSTONE_UPDATE_GOLDEN=1 更新基线。"
    )


# ---------------------------------------------------------------- 验收


class TestDeterministicRendering:
    def test_two_renders_produce_identical_bytes(self):
        """同样的输入，渲染两次，PNG 字节必须完全一致。"""
        a = render_to_png(_build_login_form(Theme.light()), _constraints())
        b = render_to_png(_build_login_form(Theme.light()), _constraints())
        assert a == b, "渲染不是确定性的——黄金图测试的前提就垮了"

    def test_light_and_dark_are_genuinely_different(self):
        light = render_to_png(_build_login_form(Theme.light()), _constraints())
        dark = render_to_png(_build_login_form(Theme.dark()), _constraints())
        assert light != dark, "明暗主题应当产生不同的图（颜色至少应有差异）"

    def test_no_surface_swapping_leaks_across_frames(self):
        """同一棵树跑两次帧，渲染结果必须一致（脏标记机制不能污染下一次）。"""
        owner = _build_login_form(Theme.light())
        first = render_to_png(owner, _constraints())
        # 第二次 begin_frame 不重挂载，应当能正常完成且产物一致
        owner.begin_frame(_constraints())
        second = render_to_png(owner, _constraints())
        assert first == second


# ---------------------------------------------------------------- 黄金图


class TestGoldenLoginForm:
    def test_light_theme(self):
        png = render_to_png(_build_login_form(Theme.light()), _constraints())
        _assert_or_update_golden("login_form_light", png)

    def test_dark_theme(self):
        png = render_to_png(_build_login_form(Theme.dark()), _constraints())
        _assert_or_update_golden("login_form_dark", png)


class TestGoldenElementaryShapes:
    def test_button_primary_only(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Button("确定", width=200))
        png = render_to_png(owner, BoxConstraints(max_width=220, max_height=80))
        _assert_or_update_golden("button_primary", png)

    def test_card_standalone(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Card(child=Box(height=80, width=200)))
        png = render_to_png(owner, BoxConstraints(max_width=240, max_height=120))
        _assert_or_update_golden("card_standalone", png)

    def test_input_field(self):
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Input(placeholder="邮箱", width=280))
        png = render_to_png(owner, BoxConstraints(max_width=320, max_height=80))
        _assert_or_update_golden("input_field", png)


@pytest.mark.skipif(os.environ.get(UPDATE_ENV) == "1", reason="更新基线模式下不跑覆盖率检查")
def test_no_orphan_failures_in_fixtures():
    """失败产物应当被审查后清理掉，不该在仓库里堆积。"""
    if not FAILURES_DIR.exists():
        return
    stale = list(FAILURES_DIR.glob("*.actual.png"))
    assert not stale, (
        f"黄金图失败产物没清：{[p.name for p in stale]}。"
        f"确认是预期变化后用 INKSTONE_UPDATE_GOLDEN=1 提交基线，然后清空此目录。"
    )
