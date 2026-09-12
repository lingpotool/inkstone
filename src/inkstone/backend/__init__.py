"""平台抽象层：把 Windows / macOS / Linux 的差异关在门外。

一句话原则：**只有后端碰平台，其余全是纯 Python、无窗口可测。**

```python
backend = HeadlessBackend()          # 测试 / CI：不开窗口，时钟与事件都是注入的
backend.initialize()
window = backend.create_window(WindowSpec(title="我的应用", width=800, height=600))
for event in backend.pump_events():
    ...                              # 归一化事件，不带任何平台痕迹

backend = SDL2Backend()              # 生产：三平台窗口与输入，需装 SDL2
```

上层（布局 / 组件树 / 样式 / 显示列表）永远只认识这套协议，
所以同一个应用在无头后端上跑测试、在 SDL2 后端上跑桌面，代码一模一样。

状态：协议 + 无头后端已实现并完整测试；
SDL2 后端的真窗口部分需实机验证（已标注未覆盖范围）。
"""

from .base import (
    Backend,
    BackendError,
    Cursor,
    Event,
    FocusEvent,
    FontFace,
    FontMetricsError,
    FontSlant,
    FontSpec,
    FontWeight,
    GlyphPlacement,
    GlyphRun,
    ImeEvent,
    ImeKind,
    KeyEvent,
    KeyKind,
    MetricsProvider,
    Modifiers,
    PointerEvent,
    PointerKind,
    TextMetrics,
    WindowEvent,
    WindowKind,
    WindowSpec,
)
from .fontfiles import (
    DEFAULT_FALLBACK_FAMILY,
    FontLibrary,
    FontRecord,
    default_font_directories,
    default_font_library,
    generic_candidates,
    order_weights,
)
from .gdi_fonts import GdiFontEngine, gdi_font_engine
from .hbft_fonts import HbFtFontEngine, hbft_font_engine
from .headless import HeadlessBackend
from .headless_fonts import FontTable, HeadlessMetrics, default_font_table
from .sdl2 import SDL2Backend, normalize_key_name, sdl2_library_name

__all__ = [
    "DEFAULT_FALLBACK_FAMILY",
    "Backend",
    "BackendError",
    "Cursor",
    "Event",
    "FocusEvent",
    "FontFace",
    "FontLibrary",
    "FontMetricsError",
    "FontRecord",
    "FontSlant",
    "FontSpec",
    "FontTable",
    "FontWeight",
    "GdiFontEngine",
    "GlyphPlacement",
    "GlyphRun",
    "HbFtFontEngine",
    "HeadlessBackend",
    "HeadlessMetrics",
    "ImeEvent",
    "ImeKind",
    "KeyEvent",
    "KeyKind",
    "MetricsProvider",
    "Modifiers",
    "PointerEvent",
    "PointerKind",
    "SDL2Backend",
    "TextMetrics",
    "WindowEvent",
    "WindowKind",
    "WindowSpec",
    "default_font_directories",
    "default_font_library",
    "default_font_table",
    "gdi_font_engine",
    "generic_candidates",
    "hbft_font_engine",
    "normalize_key_name",
    "order_weights",
    "sdl2_library_name",
]
