"""导出应用图标为打包资源（R10.3）。

**图标本身不在仓库里，它是被画出来的**：`inkstone.app.app_icon_rgba` 用框架
自己的显示列表 + 软件光栅渲染图标几何，逐字节确定。这个脚本只是把那份像素
导出成打包工具认的容器（.ico / .png），产物是**构建产物**，不进版本库。

    .venv/Scripts/python.exe tools/build_icon.py                       # build/inkstone.ico
    .venv/Scripts/python.exe tools/build_icon.py --png --out build/    # 附带 PNG 预览

为什么需要 .ico：PyInstaller / Inno Setup / 桌面条目（.desktop）都要一个文件，
而不是一段 Python。运行时窗口图标走 `SDL_SetWindowIcon`，不需要这个文件。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from inkstone.app import ICON_SIZES, app_icon_ico, app_icon_png  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 inkstone 应用图标")
    parser.add_argument("--out", default="build", help="输出目录（默认 build/）")
    parser.add_argument(
        "--sizes",
        default=",".join(str(s) for s in ICON_SIZES),
        help="ICO 内含尺寸，逗号分隔",
    )
    parser.add_argument("--png", action="store_true", help="同时导出 256px PNG 预览")
    args = parser.parse_args(argv)

    sizes = tuple(int(part) for part in args.sizes.split(",") if part.strip())
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ico_path = out_dir / "inkstone.ico"
    ico_path.write_bytes(app_icon_ico(sizes))
    print(f"  {ico_path}  ({ico_path.stat().st_size} 字节, 含 {len(sizes)} 个尺寸)")

    if args.png:
        png_path = out_dir / "inkstone.png"
        png_path.write_bytes(app_icon_png(256))
        print(f"  {png_path}  ({png_path.stat().st_size} 字节, 256×256 预览)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
