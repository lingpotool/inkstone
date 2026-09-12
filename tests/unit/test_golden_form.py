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
import sys
import zlib
from pathlib import Path
from unittest import mock

import pytest

from inkstone.core import BuildOwner
from inkstone.devtools import render_to_png
from inkstone.gfx import encode_png
from inkstone.layout import BoxConstraints
from inkstone.style import ButtonVariant, Theme
from inkstone.widgets import Box, Button, Card, Column, Flexible, Input, Row
from png_compare import GoldenBaseline, decode_png
from real_font import golden_owner

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
FAILURES_DIR = GOLDEN_DIR / "failures"
UPDATE_ENV = "INKSTONE_UPDATE_GOLDEN"


def _constraints() -> BoxConstraints:
    return BoxConstraints(max_width=320, max_height=260)


def _owner(theme: Theme) -> BuildOwner:
    """带真字体引擎的 BuildOwner（R4.5：黄金图用内嵌字体，见 `tests/real_font.py`）。

    **必须带**：不带的话按钮标签、输入框占位符都画不出来，
    黄金图就成了"没有字的表单"——看着像渲染坏了，其实只是没配引擎。
    文本引擎是整棵树共享的有状态服务（带度量缓存），所以要复用一个实例。
    """
    return golden_owner(theme)


def _build_login_form(theme: Theme) -> BuildOwner:
    owner = _owner(theme)
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
    """比对 / 更新黄金图基线。

    比对单位是**解码后的 RGBA 像素**，不是 PNG 文件字节——`encode_png` 用
    `zlib.compress(..., level=6)`，而 deflate 的输出不跨 zlib 版本保证一致
    （docs/16 §R2.1）。基线缺失也不再"顺手写一份然后绿灯"（§R2.2）。
    """
    GoldenBaseline(GOLDEN_DIR, update=os.environ.get(UPDATE_ENV) == "1").check(name, actual)


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


def _load_notes_module() -> object:
    """加载 `examples/notes.py`（样板 App）。黄金图跑它 = App 的视觉回归门禁。"""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[2] / "examples" / "notes.py"
    spec = importlib.util.spec_from_file_location("examples_notes_golden", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestGoldenNotesApp:
    """R7.4：样板 App 的黄金图（侧栏 + 滚动列表 + 表单 + 明暗主题）。"""

    def test_notes_light(self) -> None:
        module = _load_notes_module()
        owner = _owner(Theme.light())
        module.build(owner)  # type: ignore[attr-defined]
        png = render_to_png(
            owner,
            BoxConstraints(max_width=module.WIDTH, max_height=module.HEIGHT),  # type: ignore[attr-defined]
        )
        _assert_or_update_golden("notes_light", png)

    def test_notes_dark(self) -> None:
        module = _load_notes_module()
        owner = _owner(Theme.dark())
        module.build(owner, dark=True)  # type: ignore[attr-defined]
        png = render_to_png(
            owner,
            BoxConstraints(max_width=module.WIDTH, max_height=module.HEIGHT),  # type: ignore[attr-defined]
        )
        _assert_or_update_golden("notes_dark", png)


class TestGoldenDpiScale:
    """R7.3：同一棵树在 150% 档位下按设备像素出图（文字在物理分辨率上光栅化）。"""

    def test_login_form_at_150_percent(self):
        owner = _build_login_form(Theme.light())
        png = render_to_png(owner, _constraints(), dpi_scale=1.5)
        _assert_or_update_golden("login_form_light_150", png)

    def test_150_percent_is_not_a_stretch_of_100_percent(self):
        """150% 不是把 100% 的图放大——布局相同、光栅分辨率更高。

        判据：两者的物理尺寸不同（480×390 vs 320×260），
        但 150% 的画布确实是 1.5 倍（由解码器给出）。
        """
        frame_100 = decode_png(render_to_png(_build_login_form(Theme.light()), _constraints()))
        frame_150 = decode_png(
            render_to_png(_build_login_form(Theme.light()), _constraints(), dpi_scale=1.5)
        )
        assert (frame_100.width, frame_100.height) == (320, 260)
        assert (frame_150.width, frame_150.height) == (480, 390)


class TestGoldenElementaryShapes:
    def test_button_primary_only(self):
        owner = _owner(Theme.light())
        owner.mount(Button("确定", width=200))
        png = render_to_png(owner, BoxConstraints(max_width=220, max_height=80))
        _assert_or_update_golden("button_primary", png)

    def test_card_standalone(self):
        owner = _owner(Theme.light())
        owner.mount(Card(child=Box(height=80, width=200)))
        png = render_to_png(owner, BoxConstraints(max_width=240, max_height=120))
        _assert_or_update_golden("card_standalone", png)

    def test_input_field(self):
        owner = _owner(Theme.light())
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


@pytest.mark.skipif(
    os.environ.get(UPDATE_ENV) == "1", reason="更新基线模式下门禁语义不成立（缺失即写入）"
)
class TestGoldenGateIsReal:
    """门禁本身要能红——这是 docs/16 §R2.2 的验收，也是 R2 整包验收的第一条。

    全部用 `tmp_path` 隔离，**不动真基线**。
    """

    @staticmethod
    def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys.modules[__name__], "GOLDEN_DIR", tmp_path)

    def test_missing_baseline_fails_with_guidance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """基线缺失不许"顺手写一份然后绿灯"。"""
        self._isolate(tmp_path, monkeypatch)
        png = render_to_png(_build_login_form(Theme.light()), _constraints())
        with pytest.raises(AssertionError) as exc:
            _assert_or_update_golden("login_form_light", png)
        assert "基线缺失" in str(exc.value)
        assert "INKSTONE_UPDATE_GOLDEN=1" in str(exc.value)
        assert not (tmp_path / "login_form_light.png").exists()

    def test_one_changed_pixel_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """故意改坏一个像素 → 必须红（改坏在**基线**上，模拟基线悄悄漂移）。"""
        self._isolate(tmp_path, monkeypatch)
        png = render_to_png(_build_login_form(Theme.light()), _constraints())
        frame = decode_png(png)

        broken = bytearray(frame.rgba)
        broken[0] = (broken[0] + 1) & 0xFF
        (tmp_path / "login_form_light.png").write_bytes(
            encode_png(frame.width, frame.height, bytes(broken))
        )

        with pytest.raises(AssertionError, match="像素不一致"):
            _assert_or_update_golden("login_form_light", png)

    def test_identical_pixels_in_differently_compressed_png_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R2.1 的核心：PNG 字节不同、像素相同 → 必须通过。

        用不同的 zlib 压缩级别重编一遍同一份像素来制造"字节不同"。
        旧实现比对 PNG 文件字节，这种情形会红——而它其实什么都没坏。
        """
        self._isolate(tmp_path, monkeypatch)
        png = render_to_png(_build_login_form(Theme.light()), _constraints())
        frame = decode_png(png)

        # 只在这一小段里换压缩级别；**不要用 monkeypatch.undo()**——
        # 那会把上面的 GOLDEN_DIR 隔离一起撤销，测试会悄悄跑回真基线。
        real_compress = zlib.compress
        with mock.patch.object(zlib, "compress", lambda data, level=6: real_compress(data, 9)):
            recompressed = encode_png(frame.width, frame.height, frame.rgba)

        assert recompressed != png, "前提：不同压缩级别应当产出不同字节"
        (tmp_path / "login_form_light.png").write_bytes(recompressed)

        _assert_or_update_golden("login_form_light", png)  # 不该抛
