"""文本与字体（L3）：整形、断行、回退、排版。

管线（docs/04 §1）：

    str → 回退解析 → 整形 → 断行 → 排版 → 段落对象

分层约束（铁律 1）：本包只向下依赖 L0 `backend/`（字体度量契约）
与 L2 `gfx/`、`layout/` 的几何类型。**绝不 import `core/` 或 `widgets/`。**

度量同源（docs/04 §3）：所有测量都经由 `FontResolver` 走到
后端唯一的 `measure_text`。本包内不出现任何平台 API 或宽度估算。

```python
from inkstone.backend import HeadlessBackend
from inkstone.text import FontResolver, Shaper, TextStyle, default_chain, layout_paragraph

backend = HeadlessBackend()
resolver = FontResolver(backend)
style = TextStyle(families=("Inter", "Microsoft YaHei UI"), size=14)
shaper = Shaper(resolver, default_chain(style.families))
para = layout_paragraph("你好，世界", style, resolver, shaper, max_width=200)
para.position_for_point(30, 8)   # 点击落在哪个字符间隙
para.rects_for_range(2, 5)       # 选区的矩形（跨行时多个）
```

状态：文本栈已实现（字体管理 / 回退链 / 整形 / 断行 / 段落排版）。
"""

from .engine import TextEngine
from .fallback import (
    FallbackChain,
    FontScript,
    ScriptRun,
    default_chain,
    script_of,
    split_by_script,
)
from .font import (
    FontRegistry,
    FontResolver,
    FontSlant,
    FontWeight,
    ResolvedTextStyle,
    TextStyle,
)
from .linebreak import (
    BreakOpportunity,
    LineBreakResult,
    break_line,
    can_break_between,
    greedy_wrap,
)
from .paragraph import (
    EllipsisMode,
    Paragraph,
    ParagraphLayout,
    TextAlign,
    layout_paragraph,
)
from .shaping import ShapedCluster, ShapedLine, Shaper, shaped_line_from_text

__all__ = [
    "BreakOpportunity",
    "EllipsisMode",
    "FallbackChain",
    "FontRegistry",
    "FontResolver",
    "FontScript",
    "FontSlant",
    "FontWeight",
    "LineBreakResult",
    "Paragraph",
    "ParagraphLayout",
    "ResolvedTextStyle",
    "ScriptRun",
    "ShapedCluster",
    "ShapedLine",
    "Shaper",
    "TextAlign",
    "TextEngine",
    "TextStyle",
    "break_line",
    "can_break_between",
    "default_chain",
    "greedy_wrap",
    "layout_paragraph",
    "script_of",
    "shaped_line_from_text",
    "split_by_script",
]
