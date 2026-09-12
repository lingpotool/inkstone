"""真字体黄金图助手（R4.5）—— 黄金图用**内嵌字体引擎**，不再用确定性字形表。

## 为什么黄金图要切到真字体

确定性字形表（`headless_fonts.FontTable`）的字形是手画的方块矩形——
它验证的是"管线各环节不变"，但验证不了"用户看到的字好不好看"。
R4 把真字体（HarfBuzz + FreeType + 内嵌 Inkstone Sans）铺进产品路径之后，
黄金图再守着方块字形就是**守错了东西**：

- 按钮、输入框、文本的基线不再是"看起来像字的方块"，而是真字形
- kerning、中英混排、小数字号这些"真字体才暴露的问题"有了回归闸门
- 跨平台逐比特一致的前提仍在：**只装内嵌字体**（`FontLibrary(directories=())`），
  字体文件随包发布，三平台同一份字节

## 边界：什么不该切

`test_text_render.py` 里非黄金图的断言（advance、行高、断行位置）是
**按确定性字体表的数值写的**，那里的 `_owner()` 保留原样——
换引擎意味着重写约二十处断言，而这些断言测的是逻辑不是字体。
"""

from __future__ import annotations

from inkstone.backend import FontLibrary, HbFtFontEngine, HeadlessBackend
from inkstone.core import BuildOwner
from inkstone.style import Theme
from inkstone.text import TextEngine

__all__ = ["embedded_engine", "golden_owner"]


def embedded_engine() -> HbFtFontEngine:
    """只装内嵌字体的 HB+FT 引擎——三平台逐比特一致的前提。

    `FontLibrary(directories=())` 不扫任何系统目录：
    Windows 上有雅黑、Linux 上没有，这种差异不许渗进黄金图。
    """
    return HbFtFontEngine(FontLibrary(directories=()))


def golden_owner(theme: Theme) -> BuildOwner:
    """用真字体引擎的 BuildOwner。

    `render_to_framebuffer` 会从度量引擎自动取 `glyph_provider`
    （HB+FT 引擎自己就是），所以**度量与字形必然出自同一份字体**——
    这是截图基建的设计，不是这里的巧合。
    """
    backend = HeadlessBackend(font_engine=embedded_engine())
    return BuildOwner(theme=theme, text_engine=TextEngine(backend))
