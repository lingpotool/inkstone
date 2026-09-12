"""把 Noto Sans SC 子集化成内嵌兜底字体（R4.4）。

## 为什么要有这个脚本

内嵌字体是**包的一部分**（1.8MB 二进制进仓库），而"覆盖哪些语言、多少汉字"
是一个会变的决策。手工跑一次 `fonttools subset` 然后提交产物的做法，
会让"这字体是怎么来的"变成一个没人能回答的问题——改口径时也无从下手。

所以口径写在下面，产物由脚本生成：

    .venv/Scripts/python.exe tools/build_embedded_font.py

## 三档口径（改口径改这里）

| 档 | 覆盖 | 汉字 | 实测体积 |
|---|---|---|---|
| t1 | ASCII + 中文标点 | GB2312 一级 3755 | 0.88 MB |
| t2 | t1 + 拉丁扩展（中欧/越南）+ 希腊 + 西里尔 + 符号 | GB2312 6763 | 1.71 MB |
| **t3（默认）** | t2 + 日文假名 | GB2312 6763 | **1.83 MB** |
| t4 | t3 + 汉字基本区全部（含生僻字） | 20992 | 7.31 MB |

选 t3 的理由：**多语言基本盘几乎免费**——从 t1 加到 t3 只多 0.17MB，
因为拉丁/希腊/西里尔/假名加起来才 640 个字形，而 6763 个汉字是体积的全部来源。
"专业软件只有中文"没有任何理由。

生僻字（龘、𠮷、㐀）要 t4（+5.5MB，全在汉字上）；韩文不在 Noto Sans SC 里
（实测 0 个音节），要覆盖得另加一份 Noto Sans KR——两者都是文档化的后补项。

## 为什么改字体名

子集化是**修改**字体。OFL 允许修改与再分发（Noto 没有声明保留字体名），
但改个名更诚实：这份文件已经不是原版 Noto Sans SC 了。
`nameID 0` 的版权声明与 OFL 许可原文**原样保留**（OFL 的硬要求）。
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "src" / "inkstone" / "backend" / "fonts_data"

#: 上游字体。SubsetOTF 是 Noto 官方按语言切好的版本，比全量 CJK 小得多。
SOURCE_URL = (
    "https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf"
)
SOURCE_NAME = "NotoSansSC-Regular.otf"
LICENSE_URL = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE"

#: 子集化之后的名字。`Inkstone Sans` 与 `fontfiles.DEFAULT_FALLBACK_FAMILY` 必须一致。
EMBEDDED_FAMILY = "Inkstone Sans"
EMBEDDED_FILE = "InkstoneSans-Regular.otf"


def _codepoint_range(cmap: set[int], start: int, end: int) -> set[int]:
    """区间里**这份字体真有**的码点。字体没收录的不必强求（也不该报错）。"""
    return {code for code in range(start, end + 1) if code in cmap}


def _gb2312_hanzi() -> str:
    """按 GB2312 编码空间枚举出全部汉字（6763 个）。

    不写死一份字符表：GB2312 是编码标准，枚举它的合法双字节空间就能得到
    确定的字符集——而抄一份 6763 字的表进仓库，迟早会和标准对不上。
    """
    out: list[str] = []
    for high in range(0xA1, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                char = bytes((high, low)).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= char <= "\u9fff":
                out.append(char)
    return "".join(out)


def charset_for(tier: str, cmap: set[int]) -> set[int]:
    """按档位算出要保留的码点集合。"""
    ascii_range = _codepoint_range(cmap, 0x20, 0x7E)
    latin_supplement = _codepoint_range(cmap, 0xA0, 0x24F)
    latin_extended_additional = _codepoint_range(cmap, 0x1E00, 0x1EFF)  # 越南语
    greek_cyrillic = _codepoint_range(cmap, 0x370, 0x4FF)
    symbols = (
        _codepoint_range(cmap, 0x2000, 0x206F)  # 通用标点
        | _codepoint_range(cmap, 0x20A0, 0x20CF)  # 货币
        | _codepoint_range(cmap, 0x2100, 0x22FF)  # 字母式符号 + 箭头 + 数学
        | _codepoint_range(cmap, 0x2300, 0x23FF)  # 技术符号
        | _codepoint_range(cmap, 0x2500, 0x257F)  # 制表符
        | _codepoint_range(cmap, 0x25A0, 0x27BF)  # 几何图形 + 杂项符号 + 装饰
        | _codepoint_range(cmap, 0x3000, 0x303F)  # CJK 标点（禁则相关）
        | _codepoint_range(cmap, 0xFF00, 0xFFEF)  # 全角形式
    )
    kana = _codepoint_range(cmap, 0x3040, 0x30FF)
    hanzi = {ord(char) for char in _gb2312_hanzi()}

    if tier == "t1":
        return ascii_range | symbols | hanzi
    if tier == "t2":
        return ascii_range | latin_supplement | latin_extended_additional | greek_cyrillic | symbols | hanzi
    if tier == "t3":
        return (
            ascii_range
            | latin_supplement
            | latin_extended_additional
            | greek_cyrillic
            | symbols
            | kana
            | hanzi
        )
    if tier == "t4":
        cjk_basic = _codepoint_range(cmap, 0x3400, 0x4DBF) | _codepoint_range(cmap, 0x4E00, 0x9FFF)
        return (
            ascii_range
            | latin_supplement
            | latin_extended_additional
            | greek_cyrillic
            | symbols
            | kana
            | cjk_basic
        )
    raise SystemExit(f"未知档位：{tier}（可用：t1 / t2 / t3 / t4）")


def _fetch(url: str, target: pathlib.Path) -> pathlib.Path:
    if target.exists():
        print(f"已存在，跳过下载：{target.name}（{target.stat().st_size / 1e6:.1f} MB）")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {url}")
    urllib.request.urlretrieve(url, target)  # noqa: S310 - 固定 URL，非用户输入
    print(f"  完成：{target.stat().st_size / 1e6:.1f} MB")
    return target


def _rename(font_path: pathlib.Path, family: str) -> None:
    """改掉族名，保留版权声明与许可描述（OFL 的硬要求）。

    **对每条 nameID 显式写两个平台记录**（Windows/UTF-16 与 Mac/Roman），
    而不是只改已有的记录：源字体没有 nameID 16（排印族名）这条记录，
    "只改已有的"就写不进去——而 `fontfiles._read_faces` 优先读它。
    靠退回 nameID 1 也能工作，但那是**碰巧**，不是设计。
    """
    from fontTools.ttLib import TTFont

    font = TTFont(str(font_path))
    name = font["name"]
    revision = font["head"].fontRevision
    # nameID: 1=family 2=subfamily 3=唯一标识 4=全名 6=PS 名 16/17=排印族名
    replacements = {
        1: family,
        2: "Regular",
        3: f"{family.replace(' ', '')}-Regular;{revision}",
        4: f"{family} Regular",
        6: f"{family.replace(' ', '')}-Regular",
        16: family,
        17: "Regular",
    }
    for name_id, value in replacements.items():
        name.setName(value, name_id, 3, 1, 0x409)  # Windows / UTF-16 / en-US
        name.setName(value, name_id, 1, 0, 0)  # Mac / Roman / en
    font.save(str(font_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成内嵌兜底字体")
    parser.add_argument("--tier", default="t3", choices=["t1", "t2", "t3", "t4"])
    parser.add_argument("--source", type=pathlib.Path, default=None, help="本地源字体（默认自动下载）")
    parser.add_argument("--cache", type=pathlib.Path, default=ROOT / ".private" / "fonts")
    args = parser.parse_args()

    from fontTools.ttLib import TTFont

    source = args.source or _fetch(SOURCE_URL, args.cache / SOURCE_NAME)
    cmap = set(TTFont(str(source), lazy=True).getBestCmap())
    wanted = charset_for(args.tier, cmap)

    text_file = args.cache / f"charset-{args.tier}.txt"
    text_file.parent.mkdir(parents=True, exist_ok=True)
    text_file.write_text("".join(chr(code) for code in sorted(wanted)), encoding="utf-8")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / EMBEDDED_FILE
    subprocess.run(  # noqa: S603 - 固定参数
        [
            sys.executable,
            "-m",
            "fontTools.subset",
            str(source),
            f"--text-file={text_file}",
            f"--output-file={target}",
            # 保留全部 layout 特性：kerning 与连字是换文本栈的**收益**，
            # 为了省几百 KB 把它们裁掉，等于白换
            "--layout-features=*",
            # 不保留 hinting：我们的光栅化走 FreeType 的灰度抗锯齿，
            # 而 hinting 是给整数像素网格时代的显示器用的，还占体积
            "--no-hinting",
            # 去子程序化：让每个字形的轮廓自包含，便于体积统计与后续处理
            "--desubroutinize",
            "--name-IDs=*",
        ],
        check=True,
    )
    _rename(target, EMBEDDED_FAMILY)

    size_mb = target.stat().st_size / 1e6
    print(f"\n档位 {args.tier}：{len(wanted)} 个码点 → {target.relative_to(ROOT)}（{size_mb:.2f} MB）")
    if size_mb > 5.0:
        print(f"  ⚠ 超过 5MB 目标（docs/18 §R4.4）；要么换低档，要么改文档里的口径")
    _write_license()
    return 0


def _write_license() -> None:
    """把 OFL 原文放进包内。**OFL 要求随字体一起分发许可**。"""
    target = OUT_DIR / "OFL.txt"
    if not target.exists():
        _fetch(LICENSE_URL, target)
    print(f"许可：{target.relative_to(ROOT)}（{target.stat().st_size} 字节）")


if __name__ == "__main__":
    raise SystemExit(main())
