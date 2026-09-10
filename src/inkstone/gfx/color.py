"""Color —— 不可变的 RGBA 颜色。

为什么颜色放在 gfx（L2）而不是 style（L6）：光栅后端画一个矩形就要用它，
它是渲染管线的原子类型；样式层只是"往里填什么值"的规则。

实现了 docs/13 §9 验收标准里最关键的那个函数：**WCAG 对比度**。
"明暗两版全部语义令牌对比度 ≥ AA"这条要求，靠它才能变成自动化检查。

状态：已实现。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Color"]


@dataclass(frozen=True, slots=True)
class Color:
    """sRGB 颜色。`r/g/b` 取 0–255，`a` 取 0.0–1.0。"""

    r: int
    g: int
    b: int
    a: float = 1.0

    def __post_init__(self) -> None:
        for name in ("r", "g", "b"):
            value = getattr(self, name)
            if not 0 <= value <= 255:
                raise ValueError(f"{name} 必须在 0–255 之间，收到 {value}")
        if not 0.0 <= self.a <= 1.0:
            raise ValueError(f"alpha 必须在 0.0–1.0 之间，收到 {self.a}")

    # ------------------------------------------------------------ 构造

    @classmethod
    def from_hex(cls, hex_string: str) -> Color:
        """解析 `#RRGGBB` / `#RRGGBBAA` / `RRGGBB`。"""
        text = hex_string.strip().lstrip("#")
        if len(text) == 6:
            return cls(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        if len(text) == 8:
            return cls(
                int(text[0:2], 16),
                int(text[2:4], 16),
                int(text[4:6], 16),
                int(text[6:8], 16) / 255.0,
            )
        raise ValueError(f"颜色必须是 #RRGGBB 或 #RRGGBBAA，收到 {hex_string!r}")

    @classmethod
    def from_rgba(cls, r: int, g: int, b: int, a: float = 1.0) -> Color:
        return cls(r, g, b, a)

    # ------------------------------------------------------------ 输出

    def to_hex(self, include_alpha: bool = False) -> str:
        base = f"#{self.r:02X}{self.g:02X}{self.b:02X}"
        if include_alpha:
            return f"{base}{round(self.a * 255):02X}"
        return base

    def __str__(self) -> str:
        return self.to_hex(include_alpha=self.a < 1.0)

    # ------------------------------------------------------------ 运算

    def with_alpha(self, alpha: float) -> Color:
        return Color(self.r, self.g, self.b, alpha)

    def lerp(self, other: Color, t: float) -> Color:
        """线性插值。`t=0` 是自己，`t=1` 是 other。"""
        t = min(max(t, 0.0), 1.0)
        return Color(
            round(self.r + (other.r - self.r) * t),
            round(self.g + (other.g - self.g) * t),
            round(self.b + (other.b - self.b) * t),
            self.a + (other.a - self.a) * t,
        )

    # ------------------------------------------------------------ WCAG

    def relative_luminance(self) -> float:
        """WCAG 2.x 相对亮度，0（纯黑）到 1（纯白）。"""
        return (
            0.2126 * _linearize(self.r) + 0.7152 * _linearize(self.g) + 0.0722 * _linearize(self.b)
        )

    def contrast_ratio(self, other: Color) -> float:
        """WCAG 2.x 对比度，1.0（无差别）到 21.0（黑白）。

        AA 要求：正文 ≥ 4.5，大字（≥18pt 或 ≥14pt 粗体）≥ 3.0。
        """
        lighter = max(self.relative_luminance(), other.relative_luminance())
        darker = min(self.relative_luminance(), other.relative_luminance())
        return (lighter + 0.05) / (darker + 0.05)

    def meets_aa(self, background: Color, large_text: bool = False) -> bool:
        threshold = 3.0 if large_text else 4.5
        return self.contrast_ratio(background) >= threshold


def _linearize(channel: int) -> float:
    """sRGB 通道值 → 线性亮度（WCAG 公式）。"""
    c = channel / 255.0
    if c <= 0.03928:
        return c / 12.92
    return float(((c + 0.055) / 1.055) ** 2.4)
