"""测试用的**合成字体** —— 现造，不依赖系统上装了什么。

为什么要现造而不是用系统字体：`tests/` 要在三平台 CI 上跑，
而"这台机器恰好有 Microsoft YaHei"不该是测试通过的前提。
合成字体每个 1KB 上下，行为完全确定，还能精确控制**我们想测的那些量**
（字重、字宽、kern 对、连字规则）——真字体反而做不到这一点。

`build_font` 造的是一个最小可用字体：`.notdef` 加若干真轮廓字形，
带 OS/2 与 name 表，可选加传统 `kern` 表与 GSUB 连字规则。
"""

from __future__ import annotations

import pathlib

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import newTable
from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0

__all__ = ["build_font", "build_kerned_font"]

#: 字形名 → 它对应的字符（测试只用到这几个）
DEFAULT_CHARS: dict[str, int] = {"A": 0x41, "B": 0x42, "f": 0x66, "i": 0x69}


def _triangle(pen: TTGlyphPen, width: int = 600, height: int = 700) -> None:
    pen.moveTo((0, 0))
    pen.lineTo((width // 2, height))
    pen.lineTo((width, 0))
    pen.closePath()


def build_font(
    path: pathlib.Path,
    *,
    family: str = "Test Sans",
    subfamily: str = "Regular",
    weight: int = 400,
    italic: bool = False,
    advance: int = 600,
    typographic_family: str | None = None,
    has_os2: bool = True,
    units_per_em: int = 1000,
) -> pathlib.Path:
    """造一个最小可用的字体文件（有真轮廓，能被 FreeType 加载）。

    `advance` 是每个字形的推进宽度（字体单位）：把它调大就能区分字重——
    测试靠这个观察"到底选了哪个面"，不必去比较渲染结果。
    """
    builder = FontBuilder(units_per_em, isTTF=True)
    order = [".notdef", *DEFAULT_CHARS]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({code: name for name, code in DEFAULT_CHARS.items()})
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name != ".notdef":
            _triangle(pen, advance, 700)
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (advance, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    names = {
        "familyName": family,
        "styleName": subfamily,
        "fullName": f"{family} {subfamily}",
        "psName": f"{family}-{subfamily}".replace(" ", ""),
    }
    if typographic_family is not None:
        names["typographicFamily"] = typographic_family
        names["typographicSubfamily"] = subfamily
    builder.setupNameTable(names)
    if has_os2:
        builder.setupOS2(
            usWeightClass=weight,
            fsSelection=1 if italic else 0,
            sTypoAscender=800,
            sTypoDescender=-200,
        )
    builder.setupPost()
    builder.save(str(path))
    return path


def build_kerned_font(path: pathlib.Path, *, family: str = "Kern Test") -> pathlib.Path:
    """造一个**带 kerning 与连字**的字体。

    两张表都是手写进去的，因为 `FontBuilder` 不管这两个：

    - 传统 `kern` 表：A-A 收紧 200/1000 em。HarfBuzz 在没有 GPOS 时读它，
      所以这个合成字体能真实地触发 kerning —— 测试因此不必依赖系统字体。
    - GSUB `liga`：`f f i` → `f_f_i` 一个字形三个字符。这是测"连字的簇跨度"
      的唯一办法：没有连字规则，HarfBuzz 永远一字一形，
      而"一个 hb 簇覆盖多个字素簇"这条路径就永远跑不到。
    """
    base = build_font(path, family=family)
    from fontTools.ttLib import TTFont

    font = TTFont(str(base))
    subtable = KernTable_format_0()
    subtable.coverage, subtable.format, subtable.version = 1, 0, 0
    subtable.kernTable = {("A", "A"): -200}
    kern = newTable("kern")
    kern.version = 0
    kern.kernTables = [subtable]
    font["kern"] = kern

    # 连字需要 f_f_i 这个字形存在，先补上再写 GSUB
    pen = TTGlyphPen(None)
    _triangle(pen)
    font.setGlyphOrder([*font.getGlyphOrder(), "f_f_i"])
    font["glyf"]["f_f_i"] = pen.glyph()
    font["hmtx"]["f_f_i"] = (600, 0)
    addOpenTypeFeaturesFromString(font, "feature liga { sub f f i by f_f_i; } liga;")
    font.save(str(path))
    return path
