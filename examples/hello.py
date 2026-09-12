"""inkstone 示例 —— 真的能跑，真的能出图。

    python examples/hello.py                   # 渲染成 hello.png
    python examples/hello.py --out x.png       # 指定输出路径
    python examples/hello.py --dark            # 暗色主题
    python examples/hello.py --deterministic   # 内置确定性字形（跨平台同字节）

默认走**真字体引擎**（HarfBuzz 整形 + FreeType 光栅化，三平台同一套）：
中英文都渲染成真字形，系统字体缺失时由内嵌兜底字体接住（中文永不出豆腐块）。
加 `--deterministic` 换成内置确定性字形（5×7 位图 + 非拉丁占位块），
用于在任何机器上得到逐字节相同的结果。

这个示例要证明三件事：

1. **API 好用**：搭一个真实界面只要一个 Column 加几个组件。
2. **中文是一等公民**：中英混排、折行、标点禁则都对。
3. **尺寸不用猜**：按钮宽度由标签文字自己算出来，没有一处硬编码宽度。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from inkstone import __version__
from inkstone.backend import HeadlessBackend, hbft_font_engine
from inkstone.core import BuildOwner
from inkstone.devtools import render_to_png
from inkstone.layout import BoxConstraints
from inkstone.style import ButtonVariant, Theme
from inkstone.text import FontWeight, TextAlign, TextEngine
from inkstone.widgets import Box, Button, Column, Input, Row, Text

WIDTH = 420
HEIGHT = 470


def build(owner: BuildOwner) -> None:
    """搭一棵组件树。

    **没有任何一处写死宽度**——按钮宽度由标签的真实度量算出来。
    文本栈落地之前这里只能写 `width=80` 之类的估值，那正是 docs/04
    反复警告的坑（中英混排、字号变化时会悄悄算错）。
    """
    theme = owner.theme
    owner.mount(
        Column(
            gap=14,
            children=(
                Text("inkstone", size="2xl", weight=FontWeight.SEMIBOLD),
                Text(f"版本 {__version__} · 跨平台 Python 原生 UI", size="sm"),
                Box(height=1, color=theme.color("border")),
                Text(
                    "中文与 English 混排、折行、标点禁则都由文本栈处理："
                    "标点不会被留在行首，英文单词也不会被从中间劈开。",
                    size="md",
                ),
                Text("下面这段限制两行，超出以省略号收尾：", size="sm"),
                Text(
                    "这一段文字被限制为两行，多出来的内容应当以省略号结束，"
                    "而不是把半个字硬切掉或者溢出到容器外面。",
                    size="sm",
                    max_lines=2,
                ),
                Text("右对齐示例 · right aligned", size="sm", align=TextAlign.END),
                Box(height=1, color=theme.color("border")),
                Input(placeholder="你的邮箱 / Email"),
                Input(value="hello@inkstone.dev"),
                Row(
                    gap=8,
                    children=(
                        Button("稍后再说", variant=ButtonVariant.GHOST),
                        Button("开始使用"),
                    ),
                ),
            ),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inkstone 示例：渲染一张界面图")
    parser.add_argument("--out", default="hello.png", help="输出 PNG 路径")
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="用内置确定性字形（跨平台逐字节一致），而不是真字体引擎",
    )
    parser.add_argument("--dark", action="store_true", help="用暗色主题")
    args = parser.parse_args(argv)

    theme = Theme.dark() if args.dark else Theme.light()

    # 字体来源：默认真字体引擎（HB+FT，三平台同一套；uharfbuzz / freetype-py
    # 是运行时依赖，随包安装）。系统字体缺失时由内嵌兜底字体接住。
    # 光栅的字形会**自动跟随**这里的度量来源（devtools 负责配对），
    # 所以字距与字形必然出自同一份字体，不会出现"排得对但画歪了"。
    if args.deterministic:
        backend = HeadlessBackend()
        font_note = "内置确定性字形"
    else:
        backend = HeadlessBackend(font_engine=hbft_font_engine())
        font_note = "真字体（HarfBuzz + FreeType）"

    owner = BuildOwner(theme=theme, text_engine=TextEngine(backend))
    build(owner)

    png = render_to_png(owner, BoxConstraints(max_width=WIDTH, max_height=HEIGHT))
    out = Path(args.out)
    out.write_bytes(png)

    print(f"inkstone {__version__}")
    print(f"字体来源：{font_note}")
    print(f"主题：{'暗色' if args.dark else '亮色'}")
    print(f"已写出 {out}（{len(png)} 字节，{WIDTH}×{HEIGHT}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
