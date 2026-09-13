"""样板 App —— 墨记：一个真实的小工具，也是 Phase 1 的验收器（docs/21 R7.4）。

它不是一个"组件演示页"，是一个**会被真实使用**的东西：侧栏导航 + 可滚动
笔记列表 + 表单 + 明暗主题切换。它能跑起来，说明框架已经能拿去写工具了。

    python examples/notes.py                      # 无头出图 notes.png（进 CI 冒烟）
    python examples/notes.py --dark               # 暗色主题
    python examples/notes.py --deterministic      # 确定性字形（跨平台同字节）
    python examples/notes.py --dpi 1.5            # 150% 缩放
    python examples/notes.py --sdl2               # 真窗口交互（需平台 SDL2 库）

**纪律（docs/21 R7.4，比 App 本身重要）**：App 代码不许绕过框架机制——

- 不直接构建显示列表、不自己算布局、不摸光栅器（那是 app 外壳的组装职责）；
- 不读墙上时钟（时间只来自 `backend.now_ms()`，测试可注入）；
- 组件里不写死颜色/尺寸（一律 `context.theme` 的令牌）；
- 交互只经事件路由与手势识别器（`owner.dispatch_pointer`），不自己解析裸事件。

文本编辑（R9）已经落地：搜索框可以聚焦、键入、选中、删除、Ctrl+C/X/V。
真窗口模式（`--sdl2`）还会把应用身份、最小尺寸与明暗主题交给窗口系统——
任务栏认得出这是「墨记」，标题栏跟着主题换色，原生贴靠（Snap Layouts）保留。
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from inkstone import __version__
from inkstone.app import app_icon_rgba
from inkstone.backend import HeadlessBackend, ImeRect, WindowSpec, hbft_font_engine
from inkstone.core import BuildOwner, State, StatefulWidget, ThemeScope, Widget
from inkstone.devtools import render_to_png
from inkstone.layout import BoxConstraints, CrossAxisAlignment, Size
from inkstone.style import ButtonVariant, Theme
from inkstone.text import FontWeight, TextEngine
from inkstone.widgets import Box, Button, Card, Column, Flexible, Input, Row, ScrollView, Text

WIDTH = 760.0
HEIGHT = 520.0

#: 窗口图标边长（物理像素）。256 是 Windows 取大图标的档位，其余由系统缩放。
_ICON_PX = 256

_FILTERS = (("all", "全部"), ("starred", "收藏"), ("archived", "归档"))


@dataclass(frozen=True, slots=True)
class Note:
    """一条笔记。不可变——改动产生新对象，和 Widget 的纪律一致。"""

    title: str
    body: str
    starred: bool = False
    archived: bool = False


def _seed_notes() -> list[Note]:
    return [
        Note("欢迎使用墨记", "纯 Python 自绘界面，中文一等公民。侧栏切换筛选，卡片可收藏或归档。"),
        Note("中文排版", "标点不会跑到行首，英文单词不会被从中间劈开；行距来自设计令牌。"),
        Note(
            "关于缩放",
            "把窗口拖到 150% 的屏幕上，文字会在物理分辨率上重新光栅化，而不是被放大糊掉。",
        ),
        Note(
            "手势",
            "滚动列表和卡片上的按钮在同一个竞技场里竞争：按下后 8px 内抬起是点击，超过才是滚动。",
        ),
        Note("旧草稿", "这条已经归档，只在「归档」筛选下可见。", archived=True),
        Note(
            "收藏的灵感", "换主题时只有读过主题的子树重建——改侧栏不会拖着主区陪葬。", starred=True
        ),
        Note(
            "待办",
            "搜索框可聚焦、可键入、可选中删除；中文输入法直接可用，Tab 在控件间移动焦点。",
        ),
        Note("确定性", "同样的组件树在任何机器上产出同样的显示列表——黄金图与 CI 都建立在这上面。"),
    ]


class NotesApp(StatefulWidget):
    """根组件：持有笔记数据、当前筛选与主题档位。"""

    def __init__(
        self,
        *,
        initial_dark: bool = False,
        on_theme: Callable[[bool], None] | None = None,
    ) -> None:
        self.initial_dark = initial_dark
        #: 主题切换的对外通知（应用外壳拿它同步窗口标题栏配色）。
        #: 组件不知道窗口的存在——它只报告"我换档了"，这是 App↔平台的边界。
        self.on_theme = on_theme

    def create_state(self) -> NotesAppState:
        return NotesAppState()


class NotesAppState(State[NotesApp]):
    def init_state(self) -> None:
        self.notes: list[Note] = _seed_notes()
        self.filter: str = "all"
        self.dark: bool = self.widget.initial_dark
        self._seq: int = len(self.notes) + 1

    # ------------------------------------------------------------ 事件

    def _add_note(self) -> None:
        self.set_state(
            lambda: self.notes.insert(
                0,
                Note(
                    f"新建笔记 {self._seq}",
                    "这是一条新笔记。文本编辑落地后，这里会是你敲进去的内容。",
                ),
            )
        )
        self._seq += 1

    def _toggle_star(self, index: int) -> None:
        def mutate() -> None:
            note = self.notes[index]
            self.notes[index] = replace(note, starred=not note.starred)

        self.set_state(mutate)

    def _toggle_archive(self, index: int) -> None:
        def mutate() -> None:
            note = self.notes[index]
            self.notes[index] = replace(note, archived=not note.archived)

        self.set_state(mutate)

    def _set_filter(self, value: str) -> None:
        if value != self.filter:
            self.set_state(lambda: setattr(self, "filter", value))

    def _toggle_theme(self) -> None:
        def mutate() -> None:
            self.dark = not self.dark
            if self.widget.on_theme is not None:
                self.widget.on_theme(self.dark)

        self.set_state(mutate)

    def _visible_notes(self) -> list[tuple[int, Note]]:
        """当前筛选下可见的 (原始下标, 笔记)——下标用于回写收藏/归档。"""
        indexed = list(enumerate(self.notes))
        if self.filter == "starred":
            return [(i, n) for i, n in indexed if n.starred and not n.archived]
        if self.filter == "archived":
            return [(i, n) for i, n in indexed if n.archived]
        return [(i, n) for i, n in indexed if not n.archived]

    # ------------------------------------------------------------ 构建

    def build(self, context: object) -> Widget:
        theme = Theme.dark() if self.dark else Theme.light()
        return ThemeScope(
            theme,
            Row(
                align=CrossAxisAlignment.STRETCH,
                children=(
                    Flexible(self._sidebar(theme), flex=2),
                    Flexible(self._main(theme), flex=5),
                ),
            ),
        )

    def _sidebar(self, theme: Theme) -> Widget:
        children: list[Widget] = [
            Text("墨记", size="2xl", weight=FontWeight.SEMIBOLD),
            Text("本地笔记 · inkstone", size="xs"),
            Box(height=1.0, color=theme.color("border")),
        ]
        children.extend(
            Button(
                label,
                variant=ButtonVariant.PRIMARY if key == self.filter else ButtonVariant.GHOST,
                on_tap=lambda key=key: self._set_filter(key),
            )
            for key, label in _FILTERS
        )
        children.append(Flexible(Box(height=0.0)))  # 把主题按钮推到底部
        children.append(
            Button(
                "浅色主题" if self.dark else "深色主题",
                variant=ButtonVariant.GHOST,
                on_tap=self._toggle_theme,
            )
        )
        return Card(child=Column(children=tuple(children), gap=8.0))

    def _main(self, theme: Theme) -> Widget:
        visible = self._visible_notes()
        if visible:
            cards: list[Widget] = [self._note_card(index, note, theme) for index, note in visible]
        else:
            cards = [Text("这里还没有笔记。", size="sm")]
        return Card(
            child=Column(
                children=(
                    Row(
                        align=CrossAxisAlignment.CENTER,
                        gap=8.0,
                        children=(
                            Flexible(
                                Input(placeholder="搜索笔记标题（试试中文输入法）"),
                                flex=1,
                            ),
                            Button("新建", on_tap=self._add_note),
                        ),
                    ),
                    Box(height=1.0, color=theme.color("border")),
                    Flexible(ScrollView(Column(children=tuple(cards), gap=10.0))),
                ),
                gap=10.0,
            )
        )

    def _note_card(self, index: int, note: Note, theme: Theme) -> Widget:
        toggle_archive = "恢复" if note.archived else "归档"
        return Card(
            child=Column(
                children=(
                    Row(
                        align=CrossAxisAlignment.CENTER,
                        gap=8.0,
                        children=(
                            Flexible(
                                Text(note.title, size="md", weight=FontWeight.SEMIBOLD), flex=1
                            ),
                            Button(
                                "取消收藏" if note.starred else "收藏",
                                variant=ButtonVariant.GHOST,
                                size="sm",
                                on_tap=lambda index=index: self._toggle_star(index),
                            ),
                        ),
                    ),
                    Text(note.body, size="sm"),
                    Row(
                        align=CrossAxisAlignment.CENTER,
                        children=(
                            Button(
                                toggle_archive,
                                variant=ButtonVariant.GHOST,
                                size="sm",
                                on_tap=lambda index=index: self._toggle_archive(index),
                            ),
                        ),
                    ),
                ),
                gap=6.0,
            )
        )


def build(
    owner: BuildOwner,
    *,
    dark: bool = False,
    on_theme: Callable[[bool], None] | None = None,
) -> None:
    """挂载样板 App。测试与截图都从这里进入。

    主题由 App 自己持有（根上包 `ThemeScope`），所以初始档位从参数进——
    只改 `owner.theme` 是没用的：App 会在自己下面把作用域覆盖掉。
    `on_theme` 是给真窗口路径用的：换档时通知窗口系统。
    """
    owner.mount(NotesApp(initial_dark=dark, on_theme=on_theme))


# ---------------------------------------------------------------- 运行


def _make_owner(*, dark: bool, deterministic: bool) -> tuple[BuildOwner, object]:
    theme = Theme.dark() if dark else Theme.light()
    if deterministic:
        backend = HeadlessBackend()
        return BuildOwner(theme=theme, text_engine=TextEngine(backend)), backend
    metrics = hbft_font_engine()
    return BuildOwner(theme=theme, text_engine=TextEngine(metrics)), metrics


def run_headless(*, out: Path, dark: bool, deterministic: bool, dpi: float) -> int:
    owner, _backend = _make_owner(dark=dark, deterministic=deterministic)
    build(owner, dark=dark)
    png = render_to_png(
        owner,
        BoxConstraints(max_width=WIDTH, max_height=HEIGHT),
        dpi_scale=dpi,
    )
    out.write_bytes(png)
    print(f"inkstone {__version__} · 墨记")
    print(
        f"主题：{'暗色' if dark else '亮色'} · DPI {dpi:g} · 字形：{'确定性' if deterministic else '真字体'}"
    )
    print(f"已写出 {out}（{len(png)} 字节，{WIDTH:g}×{HEIGHT:g} 逻辑像素）")
    return 0


def run_window(*, dark: bool, deterministic: bool) -> int:
    """SDL2 真窗口交互，**GL 上屏**（R8.6）。

    链路：`SDL 建 OPENGL 窗口 → sdl_gl_driver 把光栅器挂到它的上下文 →
    GLRasterBackend 渲染进离屏 FBO → end_frame 时 blit 到默认帧缓冲并换链`。
    事件仍经 `owner.dispatch_pointer` 进竞技场——与无头截图同一套机制。

    每帧**整树重绘**（`force_repaint=True`）：应用外壳的脏区调度还没落地，
    而 GL 下全屏重绘实测 ~4ms，正确性优先。（真窗口路径不在 CI 覆盖内。）
    """
    from inkstone.backend.base import (
        ImeEvent,
        KeyEvent,
        PointerEvent,
        TextEvent,
        WindowEvent,
        WindowKind,
    )
    from inkstone.backend.gl_wgl import GLUnavailableError, sdl_gl_driver
    from inkstone.backend.sdl2 import SDL2Backend
    from inkstone.gfx import DisplayListRecorder, GLRasterBackend
    from inkstone.layout.types import Rect

    metrics = None if deterministic else hbft_font_engine()
    backend = SDL2Backend()
    # 任务栏身份在建窗**之前**设：Windows 认的是注册时刻的身份，
    # 晚设会让窗口先以解释器（python.exe）的身份出现在任务栏上。
    backend.set_app_identity("Inkstone.Notes")
    backend.initialize()
    window = backend.create_window(
        WindowSpec(
            title="墨记 · inkstone",
            width=WIDTH,
            height=HEIGHT,
            resizable=True,
            opengl=True,
        )
    )
    # 最小尺寸：再小布局就装不下侧栏 + 主区了，窗口系统替我们先挡一道。
    backend.set_min_size(window, 480.0, 320.0)
    # 图标由自家渲染器画出来（R10.3）——仓库里没有 .ico，任务栏认的是这份像素。
    backend.set_icon(window, _ICON_PX, _ICON_PX, app_icon_rgba(_ICON_PX))

    dark_now = [dark]

    def apply_window_theme(is_dark: bool) -> None:
        """主题换档 → 窗口系统跟着换（标题栏配色；原生边框保留）。"""
        dark_now[0] = is_dark
        theme = Theme.dark() if is_dark else Theme.light()
        bg = theme.color("bg")
        backend.set_window_theme(window, dark=is_dark, background=(bg.r << 16) | (bg.g << 8) | bg.b)

    apply_window_theme(dark)
    try:
        driver = sdl_gl_driver(backend, window)
    except GLUnavailableError as error:
        backend.destroy_window(window)
        backend.shutdown()
        print(f"GL 上屏不可用：{error}", file=sys.stderr)
        print('安装后端：pip install "inkstone[sdl2]"；或改用无头出图模式。', file=sys.stderr)
        return 1
    raster = GLRasterBackend(driver, glyph_provider=metrics)  # type: ignore[arg-type]

    owner = BuildOwner(
        theme=Theme.dark() if dark else Theme.light(),
        text_engine=TextEngine(metrics if metrics is not None else HeadlessBackend()),
    )
    build(owner, dark=dark, on_theme=apply_window_theme)
    owner.on_text_input = lambda active: (
        backend.start_text_input(window) if active else backend.stop_text_input(window)
    )
    owner.on_ime_rect = lambda rect: backend.set_ime_rect(
        window, ImeRect(rect.left, rect.top, rect.width, rect.height)
    )
    # 剪贴板钩子：编辑模型的 Ctrl+C/X/V 经它落到平台
    owner.clipboard_get = backend.clipboard_get_text
    owner.clipboard_set = backend.clipboard_set_text

    running = True
    while running:
        for event in backend.wait_events(16.0):
            if isinstance(event, PointerEvent):
                owner.dispatch_pointer(event)
            elif isinstance(event, KeyEvent):
                owner.dispatch_key(event)
            elif isinstance(event, TextEvent):
                owner.dispatch_text(event)
            elif isinstance(event, ImeEvent):
                owner.dispatch_ime(event)
            elif isinstance(event, WindowEvent):
                owner.handle_window_event(event)
                if event.kind is WindowKind.CLOSE:
                    running = False

        # 视口尺寸问后端要**逻辑**像素（R11）：缩放换算在后端，
        # 原生边框可拖拽/贴靠/最大化，布局自适应当前尺寸。
        scale = backend.dpi_scale(window)
        logical_w, logical_h = backend.window_size(window)
        logical_w, logical_h = max(1.0, logical_w), max(1.0, logical_h)
        width = math.ceil(logical_w * scale)
        height = math.ceil(logical_h * scale)
        bg = (Theme.dark() if dark_now[0] else Theme.light()).color("bg")
        recorder = DisplayListRecorder()
        recorder.fill_rect(Rect(0.0, 0.0, float(width), float(height)), bg)
        raster.begin_frame(Size(logical_w, logical_h), scale)
        owner.begin_frame(
            BoxConstraints(max_width=logical_w, max_height=logical_h),
            recorder,
            now_ms=backend.now_ms(),
            dpi_scale=scale,
            force_repaint=True,
        )
        raster.execute(recorder.finish(width, height))
        raster.end_frame()  # 离屏 FBO → 默认帧缓冲 → SwapWindow

    driver.close()
    backend.destroy_window(window)
    backend.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inkstone 样板 App：墨记")
    parser.add_argument("--out", default="notes.png", help="无头模式输出 PNG 路径")
    parser.add_argument("--dark", action="store_true", help="暗色主题")
    parser.add_argument("--deterministic", action="store_true", help="用内置确定性字形")
    parser.add_argument("--dpi", type=float, default=1.0, help="设备像素比（1.0 / 1.25 / 1.5）")
    parser.add_argument("--sdl2", action="store_true", help="打开真窗口交互（需平台 SDL2 库）")
    args = parser.parse_args(argv)

    if args.sdl2:
        return run_window(dark=args.dark, deterministic=args.deterministic)
    return run_headless(
        out=Path(args.out),
        dark=args.dark,
        deterministic=args.deterministic,
        dpi=args.dpi,
    )


if __name__ == "__main__":
    sys.exit(main())
