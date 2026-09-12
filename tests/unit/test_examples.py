"""示例冒烟测试 —— 保证 examples/ 里的代码真的能跑。

为什么值得单独测：`examples/` 是最容易腐烂的地方。API 一改，示例就悄悄
失效，而它恰恰是新人第一眼看到的东西——示例跑不起来，等于告诉用户
"这个库别碰"。所以**示例必须进 CI**。

`examples/hello.py` 用确定性字形渲染时是逐字节确定的，
所以这里可以直接断言"两次渲染的字节相同"，而不只是"没崩"。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str) -> ModuleType:
    path = EXAMPLES / f"{name}.py"
    assert path.exists(), f"示例文件不存在：{path}"
    spec = importlib.util.spec_from_file_location(f"examples_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestHelloExample:
    def test_runs_and_writes_png(self, tmp_path: Path) -> None:
        module = _load("hello")
        out = tmp_path / "hello.png"
        code = module.main(["--out", str(out), "--deterministic"])
        assert code == 0
        assert out.exists()
        # PNG magic number——确认真的是张图，而不是错误页面
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_deterministic_mode_is_byte_stable(self, tmp_path: Path) -> None:
        """确定性字形下两次渲染必须逐字节相同（这就是黄金图的基础）。"""
        module = _load("hello")
        first = tmp_path / "a.png"
        second = tmp_path / "b.png"
        module.main(["--out", str(first), "--deterministic"])
        module.main(["--out", str(second), "--deterministic"])
        assert first.read_bytes() == second.read_bytes()

    def test_dark_theme_differs(self, tmp_path: Path) -> None:
        module = _load("hello")
        light = tmp_path / "l.png"
        dark = tmp_path / "d.png"
        module.main(["--out", str(light), "--deterministic"])
        module.main(["--out", str(dark), "--deterministic", "--dark"])
        assert light.read_bytes() != dark.read_bytes()

    def test_layout_has_no_overflow(self) -> None:
        """示例的界面不该溢出——宽度都是算出来的，不是猜的。

        这一条如果红了，通常意味着某个组件的尺寸规则坏了。
        """
        from inkstone.backend import HeadlessBackend
        from inkstone.core import BuildOwner
        from inkstone.layout import BoxConstraints
        from inkstone.style import Theme
        from inkstone.text import TextEngine

        module = _load("hello")
        owner = BuildOwner(theme=Theme.light(), text_engine=TextEngine(HeadlessBackend()))
        module.build(owner)
        owner.flush_layout(BoxConstraints(max_width=module.WIDTH, max_height=module.HEIGHT))
        root = owner.root_render_object
        assert root is not None
        assert not root.has_overflow, f"示例界面溢出了 {root.overflow}px"


class TestNotesExample:
    """R7.4 样板 App：侧栏 + 滚动列表 + 表单 + 主题切换的真实小工具。"""

    @staticmethod
    def _mounted(module: ModuleType):
        from inkstone.backend import HeadlessBackend
        from inkstone.core import BuildOwner
        from inkstone.layout import BoxConstraints
        from inkstone.style import Theme
        from inkstone.text import TextEngine

        owner = BuildOwner(theme=Theme.light(), text_engine=TextEngine(HeadlessBackend()))
        element = owner.mount(module.NotesApp())
        owner.begin_frame(BoxConstraints(max_width=module.WIDTH, max_height=module.HEIGHT))
        return owner, element

    def test_runs_and_writes_png(self, tmp_path: Path) -> None:
        module = _load("notes")
        out = tmp_path / "notes.png"
        assert module.main(["--out", str(out), "--deterministic"]) == 0
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_deterministic_mode_is_byte_stable(self, tmp_path: Path) -> None:
        module = _load("notes")
        first = tmp_path / "a.png"
        second = tmp_path / "b.png"
        module.main(["--out", str(first), "--deterministic"])
        module.main(["--out", str(second), "--deterministic"])
        assert first.read_bytes() == second.read_bytes()

    def test_dark_theme_differs(self, tmp_path: Path) -> None:
        module = _load("notes")
        light = tmp_path / "l.png"
        dark = tmp_path / "d.png"
        module.main(["--out", str(light), "--deterministic"])
        module.main(["--out", str(dark), "--deterministic", "--dark"])
        assert light.read_bytes() != dark.read_bytes()

    def test_150_percent_changes_canvas_size(self, tmp_path: Path) -> None:
        from png_compare import decode_png

        module = _load("notes")
        out = tmp_path / "big.png"
        module.main(["--out", str(out), "--deterministic", "--dpi", "1.5"])
        frame = decode_png(out.read_bytes())
        assert (frame.width, frame.height) == (
            int(module.WIDTH * 1.5),
            int(module.HEIGHT * 1.5),
        )

    def test_layout_has_no_overflow(self) -> None:
        from inkstone.layout import BoxConstraints

        module = _load("notes")
        owner, _ = self._mounted(module)
        root = owner.root_render_object
        assert root is not None
        assert not root.has_overflow, f"样板 App 溢出了 {root.overflow}px"
        assert (
            root.layout(BoxConstraints(max_width=module.WIDTH, max_height=module.HEIGHT))
            is not None
        )

    def test_filter_and_add_change_the_state(self) -> None:
        module = _load("notes")
        _, element = self._mounted(module)
        state = element.state  # type: ignore[attr-defined]

        assert len(state._visible_notes()) == 7  # 8 条里 1 条已归档
        state._set_filter("starred")
        assert [n.title for _, n in state._visible_notes()] == ["收藏的灵感"]
        state._set_filter("archived")
        assert [n.title for _, n in state._visible_notes()] == ["旧草稿"]

        state._set_filter("all")
        before = len(state.notes)
        state._add_note()
        assert len(state.notes) == before + 1
        assert state.notes[0].title.startswith("新建笔记")

    def test_theme_toggle_reaches_the_subtree(self) -> None:
        from inkstone.layout import BoxConstraints

        module = _load("notes")
        owner, element = self._mounted(module)
        # App 自己 build 出 ThemeScope，所以它本身看不到那层作用域——
        # 读**子树**（ThemeScope 那个 Element）才反映实际生效的主题。
        scope = []
        element.visit_children(scope.append)
        assert not scope[0].theme.is_dark

        element.state._toggle_theme()  # type: ignore[attr-defined]
        owner.begin_frame(BoxConstraints(max_width=module.WIDTH, max_height=module.HEIGHT))
        scope = []
        element.visit_children(scope.append)
        assert scope[0].theme.is_dark, "切换后子树必须读到暗色主题（ThemeScope）"
