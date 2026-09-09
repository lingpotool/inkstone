"""设计令牌：所有颜色、间距、圆角、字号的唯一来源。改这里，全盘生效。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple           # 应用底色
    surface: tuple      # 卡片/面板
    surface_alt: tuple  # 次级面板、输入框底
    border: tuple
    border_strong: tuple
    text: tuple
    text_dim: tuple
    text_faint: tuple
    primary: tuple
    primary_hover: tuple
    primary_press: tuple
    on_primary: tuple   # 主色按钮上的文字
    danger: tuple
    success: tuple
    primary_soft: tuple


LIGHT = Theme(
    name="light",
    bg=(244, 245, 247),
    surface=(255, 255, 255),
    surface_alt=(249, 250, 251),
    border=(229, 231, 235),
    border_strong=(209, 213, 219),
    text=(31, 35, 40),
    text_dim=(107, 114, 128),
    text_faint=(156, 163, 175),
    primary=(79, 70, 229),
    primary_hover=(67, 56, 202),
    primary_press=(55, 48, 163),
    on_primary=(255, 255, 255),
    danger=(220, 38, 38),
    success=(15, 118, 110),
    primary_soft=(238, 240, 255),
)

DARK = Theme(
    name="dark",
    bg=(17, 19, 23),
    surface=(28, 31, 37),
    surface_alt=(34, 38, 45),
    border=(48, 53, 62),
    border_strong=(68, 74, 85),
    text=(233, 236, 241),
    text_dim=(154, 162, 175),
    text_faint=(110, 118, 132),
    primary=(129, 122, 255),
    primary_hover=(149, 143, 255),
    primary_press=(108, 100, 245),
    on_primary=(20, 20, 26),
    danger=(248, 113, 113),
    success=(45, 212, 191),
    primary_soft=(44, 42, 84),
)

THEMES = {"light": LIGHT, "dark": DARK}


class S:  # 间距 (spacing)
    s1 = 4
    s2 = 8
    s3 = 12
    s4 = 16
    s5 = 24
    s6 = 32


class R:  # 圆角 (radius)
    sm = 6
    md = 10
    lg = 14
    pill = 999


class F:  # 字号 (font size)
    sm = 12
    md = 14
    lg = 17
    xl = 22
    xxl = 28


# 中文字体优先，回退到系统可用的第一个
FONT_CANDIDATES = [
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "SimHei",
]
