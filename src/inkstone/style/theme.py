"""主题 —— 令牌的具名组合。

一个 Theme = 一份基础令牌 + 一份语义映射。内置明暗两版；
自定义主题只需给出自己的 `SemanticTokens`（比如换成西方涨绿跌红），
基础层可以整个复用。

使用方式（组件层）：

    ```python
    theme = Theme.dark()
    card_bg = theme.color("surface")        # 语义色
    padding = theme.space("lg")             # 16
    radius = theme.radius("md")             # 10
    ```

所有取值方法对未知令牌名抛 `TokenError`——**找不到就响亮地失败**。
静默回退到某个默认值，会让"拼写错令牌名"变成一个界面上的小瑕疵，
而不是一条清晰的报错。

未实现（按 ROADMAP 属后续阶段）：密度档位、字号缩放、高对比主题、
prefers-reduced-motion（docs/13 §6.3，动效引擎落地时一起做）。

状态：明暗两版已实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from functools import cache
from typing import TypeVar

from ..gfx.color import Color
from .tokens import (
    DARK_SEMANTIC,
    DEFAULT_RAW,
    LIGHT_SEMANTIC,
    CubicBezier,
    FontSize,
    RawTokens,
    SemanticTokens,
    ShadowSpec,
    SpringSpec,
    contrast_issues,
)

__all__ = ["Theme", "ThemeMode", "TokenError"]


class ThemeMode(Enum):
    LIGHT = "light"
    DARK = "dark"


class TokenError(KeyError):
    """引用了不存在的令牌。多半是拼写错误，报错信息里带上可用名单。"""


_V = TypeVar("_V")


@dataclass(frozen=True, slots=True)
class Theme:
    """一份可用的设计系统实例。"""

    name: str
    mode: ThemeMode
    raw: RawTokens
    semantic: SemanticTokens

    # ------------------------------------------------------------ 构造

    @classmethod
    def light(cls, *, name: str = "light", raw: RawTokens = DEFAULT_RAW) -> Theme:
        return cls(name=name, mode=ThemeMode.LIGHT, raw=raw, semantic=LIGHT_SEMANTIC)

    @classmethod
    def dark(cls, *, name: str = "dark", raw: RawTokens = DEFAULT_RAW) -> Theme:
        return cls(name=name, mode=ThemeMode.DARK, raw=raw, semantic=DARK_SEMANTIC)

    def with_semantic(self, semantic: SemanticTokens, *, name: str | None = None) -> Theme:
        """换一套语义映射（自定义品牌色、西方涨跌色等），基础层原样复用。"""
        return replace(self, semantic=semantic, name=name if name is not None else self.name)

    # ------------------------------------------------------------ 查询

    @property
    def is_dark(self) -> bool:
        return self.mode is ThemeMode.DARK

    def contrast_issues(self) -> list[str]:
        """WCAG 2.2 AA 检查。空列表 = 通过。CI 里必须为空。"""
        return contrast_issues(self.semantic)

    # ------------------------------------------------------------ 取值

    def color(self, name: str) -> Color:
        """语义色。`"bg" / "surface" / "primary" / "text-dim" …`（下划线即连字符）。"""
        return _lookup("semantic", name.replace("_", "-"), _semantic_map(self.semantic))

    def space(self, name: str) -> float:
        """间距（px）。`"xs"=4 … "4xl"=64`，8pt 网格。"""
        return _lookup("spacing", name, self.raw.spacing)

    def radius(self, name: str) -> float:
        """圆角（px）。`"sm"=6 / "md"=10 / "lg"=14 / "xl"=20 / "pill"`。"""
        return _lookup("radius", name, self.raw.radius)

    def font_size(self, name: str) -> FontSize:
        """字号阶梯。`"xs" … "3xl"`。"""
        return _lookup("type_scale", name, self.raw.type_scale)

    def control_height(self, name: str) -> float:
        """控件高度（px）。`"xs"=24 … "xl"=52`，表单对齐靠它。"""
        return _lookup("control_heights", name, self.raw.control_heights)

    def border_width(self, name: str) -> float:
        """描边宽度（px）。`"hairline"=1 / "thick"=2`。

        边框、分隔线、焦点环都从这里取——"1px 还是 2px"是设计决定，
        散落在组件里当字面量就没人能一次改全。
        """
        return _lookup("border_widths", name, self.raw.border_widths)

    def shadow(self, name: str) -> ShadowSpec | None:
        """阴影。`"e0"` 无阴影，`"e1"–"e4"` 逐级加深。"""
        return _lookup("shadows", name, self.raw.shadows)

    def duration(self, name: str) -> float:
        """动效时长（ms）。超过 500 的值不许出现。"""
        return _lookup("motion_durations", name, self.raw.motion_durations)

    def easing(self, name: str) -> CubicBezier | SpringSpec:
        """缓动曲线或弹簧参数。"""
        return _lookup("easings", name, self.raw.easings)

    def gesture(self, name: str) -> float:
        """手势参数。`"tap_slop"=8 / "long_press_ms"=500 / "double_tap_ms"=300`。

        识别器住在 `events/`（L1），读不到主题（L6）——所以由组件在构造
        识别器时把令牌值传进去，识别器本身不含字面量。
        """
        return _lookup("gestures", name, self.raw.gestures)

    def decoration(self, name: str) -> float:
        """文本编辑装饰。`"caret_width" / "underline_width" / "underline_offset"`。"""
        return _lookup("decorations", name, self.raw.decorations)

    @property
    def font_sans(self) -> tuple[str, ...]:
        return self.raw.font_sans

    @property
    def font_mono(self) -> tuple[str, ...]:
        return self.raw.font_mono


_DEFAULT_THEME: Theme | None = None


def default_theme() -> Theme:
    """环境主题缺失时的兜底（组件没挂到 BuildOwner 上时会用到）。

    缓存一份复用：Theme 是不可变的，没必要每次都造新的。
    """
    global _DEFAULT_THEME
    if _DEFAULT_THEME is None:
        _DEFAULT_THEME = Theme.light()
    return _DEFAULT_THEME


def _lookup(group: str, name: str, table: Mapping[str, _V]) -> _V:
    try:
        return table[name]
    except KeyError:
        available = ", ".join(sorted(table))
        raise TokenError(f"没有 {group}[{name!r}] 这个令牌。可用：{available}") from None


@cache
def _semantic_map(semantic: SemanticTokens) -> dict[str, Color]:
    """把 SemanticTokens 摊平成 dict，供 color() 按名取用。

    缓存（R6.3）：`Theme.color()` 是热路径（每帧每控件多次），
    每次重建 26 键 dict 纯属浪费。SemanticTokens 是 frozen 可哈希的，
    直接按实例缓存。返回的 dict 是共享的——**只读，别改**。
    """
    return {
        "bg": semantic.bg,
        "surface": semantic.surface,
        "surface-alt": semantic.surface_alt,
        "border": semantic.border,
        "border-strong": semantic.border_strong,
        "text": semantic.text,
        "text-dim": semantic.text_dim,
        "text-faint": semantic.text_faint,
        "primary": semantic.primary,
        "primary-hover": semantic.primary_hover,
        "primary-press": semantic.primary_press,
        "primary-soft": semantic.primary_soft,
        "on-primary": semantic.on_primary,
        "focus-ring": semantic.focus_ring,
        "success-bg": semantic.success_bg,
        "success-text": semantic.success_text,
        "warning-bg": semantic.warning_bg,
        "warning-text": semantic.warning_text,
        "danger-bg": semantic.danger_bg,
        "danger-text": semantic.danger_text,
        "info-bg": semantic.info_bg,
        "info-text": semantic.info_text,
        "market-up": semantic.market_up,
        "market-down": semantic.market_down,
    }
