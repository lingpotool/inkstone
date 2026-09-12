"""PNG 解码与像素级比对 —— 黄金图的**事实源**。

为什么不能拿 PNG 文件字节当比对单位（docs/16 §R2.1）：

    `encode_png` 用 `zlib.compress(raw, level=6)`。deflate 的输出**不跨 zlib 版本
    保证一致**，而各平台 CPython 打包的 zlib 并不固定——"三平台逐字节相同"
    是侥幸，不是保证。一旦哪天某个平台换了 zlib，黄金图会以"渲染变了"的名义
    红一次，把所有人引向错误的排查方向。

所以比对单位是**解码后的 RGBA 像素**，PNG 文件字节只是存储格式。
解码器只用 stdlib（`zlib` + `struct`），**不引 Pillow**：少一个依赖，
而且"拒绝不认识的形态"这件事必须由我们自己决定，不能靠第三方库默默兜住。

只支持自家编码器产出的形态（8-bit RGBA、非交错）；其余形态一律**响亮报错**，
不做"尽力而为"的解码——悄悄解错比解不了危险得多。

本模块同时提供 `GoldenBaseline`：两个黄金图测试模块共用同一份比对/更新实现，
避免"两处副本各自漂移"（docs/16 §R2.1 要求改的就是那两处）。
"""

from __future__ import annotations

import pathlib
import struct
import zlib
from dataclasses import dataclass

__all__ = [
    "PNG_SIGNATURE",
    "DecodedPng",
    "GoldenBaseline",
    "PngFormatError",
    "decode_png",
    "describe_difference",
]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 只认这一种形态：8 位深、颜色类型 6（RGBA）、压缩/滤波方法 0、非交错
_SUPPORTED_BIT_DEPTH = 8
_SUPPORTED_COLOR_TYPE = 6
_CHANNELS = 4


class PngFormatError(ValueError):
    """解码器不认识的 PNG 形态。**不猜、不尽力而为**，直接报清楚。"""


@dataclass(frozen=True, slots=True)
class DecodedPng:
    """解码结果：宽高 + 逐像素 RGBA（行优先，每像素 4 字节）。"""

    width: int
    height: int
    rgba: bytes

    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        """取一个像素的 (r, g, b, a)。测试与失败报告用。"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"像素坐标越界：({x}, {y})，画布 {self.width}×{self.height}")
        base = (y * self.width + x) * _CHANNELS
        d = self.rgba
        return d[base], d[base + 1], d[base + 2], d[base + 3]


def decode_png(data: bytes) -> DecodedPng:
    """把 PNG 字节解码成 RGBA 像素。形态不支持时抛 `PngFormatError`。"""
    if data[:8] != PNG_SIGNATURE:
        raise PngFormatError("不是 PNG：签名不匹配（前 8 字节应当是 \\x89PNG\\r\\n\\x1a\\n）")

    width = height = 0
    header_seen = False
    idat = bytearray()
    pos = 8
    while pos < len(data):
        if pos + 8 > len(data):
            raise PngFormatError(f"PNG 在第 {pos} 字节处截断（chunk 头不完整）")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        if len(payload) != length:
            raise PngFormatError(f"chunk {kind!r} 声明长度 {length}，实际只有 {len(payload)} 字节")
        _check_crc(kind, payload, data[pos + 8 + length : pos + 12 + length])
        pos += 12 + length

        if kind == b"IHDR":
            width, height = _parse_ihdr(payload)
            header_seen = True
        elif kind == b"IDAT":
            if not header_seen:
                raise PngFormatError("IDAT 出现在 IHDR 之前")
            idat += payload
        elif kind == b"IEND":
            break

    if not header_seen:
        raise PngFormatError("PNG 里没有 IHDR")
    if width == 0 or height == 0:
        raise PngFormatError(f"画布尺寸必须为正，收到 {width}×{height}")
    if not idat:
        raise PngFormatError("PNG 里没有 IDAT（没有像素数据）")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:  # pragma: no cover - 只在基线被破坏时触发
        raise PngFormatError(f"IDAT 解压失败：{exc}") from exc

    return DecodedPng(width, height, _unfilter(raw, width, height))


def _parse_ihdr(payload: bytes) -> tuple[int, int]:
    if len(payload) != 13:
        raise PngFormatError(f"IHDR 应当是 13 字节，收到 {len(payload)}")
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", payload
    )
    if depth != _SUPPORTED_BIT_DEPTH:
        raise PngFormatError(f"只支持 {_SUPPORTED_BIT_DEPTH} 位深，收到 {depth}")
    if color_type != _SUPPORTED_COLOR_TYPE:
        raise PngFormatError(
            f"只支持颜色类型 {_SUPPORTED_COLOR_TYPE}（RGBA），收到 {color_type}"
            "（0=灰度 / 2=RGB / 3=索引 / 4=灰度+A / 6=RGBA）"
        )
    if compression != 0:
        raise PngFormatError(f"只支持压缩方法 0，收到 {compression}")
    if filter_method != 0:
        raise PngFormatError(f"只支持滤波方法 0，收到 {filter_method}")
    if interlace != 0:
        raise PngFormatError(f"不支持交错（Adam7）PNG，收到 interlace={interlace}")
    return width, height


def _check_crc(kind: bytes, payload: bytes, crc_bytes: bytes) -> None:
    if len(crc_bytes) != 4:
        raise PngFormatError(f"chunk {kind!r} 缺 CRC")
    (expected,) = struct.unpack(">I", crc_bytes)
    actual = zlib.crc32(kind + payload) & 0xFFFFFFFF
    if expected != actual:
        raise PngFormatError(
            f"chunk {kind!r} 的 CRC 不匹配（声明 {expected:#010x}，实算 {actual:#010x}）"
        )


def _unfilter(raw: bytes, width: int, height: int) -> bytes:
    """把 PNG 的行滤波还原成原始像素。

    5 种滤波器都要实现（None/Sub/Up/Average/Paeth）：自家编码器只写 None，
    但**只实现 None 的解码器是假解码器**——它会在遇到别人的 PNG 时静默出错。
    """
    stride = width * _CHANNELS
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise PngFormatError(
            f"解压后应有 {expected} 字节（含每行 1 字节滤波类型），收到 {len(raw)}"
        )

    out = bytearray(stride * height)
    prev = bytes(stride)  # 第 0 行上方视作全 0
    pos = 0
    for y in range(height):
        filter_type = raw[pos]
        line = raw[pos + 1 : pos + 1 + stride]
        pos += 1 + stride

        if filter_type == 0:  # None —— 自家编码器走的就是这条（整行切片，最快）
            row = line
        elif filter_type == 1:  # Sub
            row = _unfilter_sub(line, prev)
        elif filter_type == 2:  # Up
            row = _unfilter_up(line, prev)
        elif filter_type == 3:  # Average
            row = _unfilter_average(line, prev)
        elif filter_type == 4:  # Paeth
            row = _unfilter_paeth(line, prev)
        else:
            raise PngFormatError(f"第 {y} 行的滤波类型非法：{filter_type}（合法值 0–4）")

        start = y * stride
        out[start : start + stride] = row
        prev = row
    return bytes(out)


def _unfilter_sub(line: bytes, prev: bytes) -> bytes:
    """Recon(x) = Filt(x) + Recon(x − bpp)。"""
    acc = bytearray(len(line))
    for i, value in enumerate(line):
        left = acc[i - _CHANNELS] if i >= _CHANNELS else 0
        acc[i] = (value + left) & 0xFF
    return bytes(acc)


def _unfilter_up(line: bytes, prev: bytes) -> bytes:
    """Recon(x) = Filt(x) + Recon(上一行同列)。"""
    return bytes((value + up) & 0xFF for value, up in zip(line, prev, strict=True))


def _unfilter_average(line: bytes, prev: bytes) -> bytes:
    """Recon(x) = Filt(x) + floor((Recon(左) + Recon(上)) / 2)。"""
    acc = bytearray(len(line))
    for i, value in enumerate(line):
        left = acc[i - _CHANNELS] if i >= _CHANNELS else 0
        acc[i] = (value + ((left + prev[i]) >> 1)) & 0xFF
    return bytes(acc)


def _unfilter_paeth(line: bytes, prev: bytes) -> bytes:
    """Recon(x) = Filt(x) + PaethPredictor(左, 上, 左上)。"""
    acc = bytearray(len(line))
    for i, value in enumerate(line):
        if i >= _CHANNELS:
            left = acc[i - _CHANNELS]
            up_left = prev[i - _CHANNELS]
        else:
            left = 0
            up_left = 0
        acc[i] = (value + _paeth(left, prev[i], up_left)) & 0xFF
    return bytes(acc)


def _paeth(left: int, up: int, up_left: int) -> int:
    """PNG 规范里的 Paeth 预测器（取三者中最接近 left+up−up_left 的那个）。"""
    p = left + up - up_left
    pa = abs(p - left)
    pb = abs(p - up)
    pc = abs(p - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


# ---------------------------------------------------------------- 差异描述


def describe_difference(
    expected: DecodedPng, actual: DecodedPng, *, max_samples: int = 6
) -> str | None:
    """逐像素比对。完全相同返回 `None`，否则返回人类可读的差异报告。"""
    if (expected.width, expected.height) != (actual.width, actual.height):
        return (
            f"尺寸不同：基线 {expected.width}×{expected.height}，"
            f"实际 {actual.width}×{actual.height}"
        )
    if expected.rgba == actual.rgba:
        return None

    differing: list[tuple[int, int, tuple[int, int, int, int], tuple[int, int, int, int]]] = []
    count = 0
    min_x = expected.width
    max_x = -1
    min_y = expected.height
    max_y = -1
    for y in range(expected.height):
        for x in range(expected.width):
            a = expected.pixel(x, y)
            b = actual.pixel(x, y)
            if a == b:
                continue
            count += 1
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            if len(differing) < max_samples:
                differing.append((x, y, a, b))

    lines = [
        f"{count} / {expected.pixel_count} 个像素不同",
        f"  差异范围 x {min_x}..{max_x}，y {min_y}..{max_y}",
        "  样例（基线 -> 实际）:",
    ]
    lines.extend(f"    ({x},{y}) {a} -> {b}" for x, y, a, b in differing)
    if count > len(differing):
        lines.append(f"    …… 还有 {count - len(differing)} 个")
    return "\n".join(lines)


# ---------------------------------------------------------------- 黄金图基线


class GoldenBaseline:
    """黄金图基线的读取 / 比对 / 更新。两个测试模块共用同一份实现。

    两条刻意的纪律（docs/16 §R2.1、§R2.2）：

    1. **比对单位是解码后的像素**，不是 PNG 文件字节——zlib 输出不跨版本保证一致。
    2. **基线缺失 = 失败**。此前"缺失就顺手写一份新基线然后绿灯"，
       等于把门禁做成了自动橡皮图章：新增场景与"基线被误删"完全无法区分。
       要生成基线必须显式走 `INKSTONE_UPDATE_GOLDEN=1`。
    """

    def __init__(self, golden_dir: pathlib.Path, *, update: bool) -> None:
        self.golden_dir = golden_dir
        self.failures_dir = golden_dir / "failures"
        self.update = update

    def check(self, name: str, png: bytes) -> None:
        baseline = self.golden_dir / f"{name}.png"

        if self.update:
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_bytes(png)
            return

        if not baseline.exists():
            raise AssertionError(
                f"黄金图基线缺失：{baseline}\n"
                f"  这**不算通过**——基线缺失与'基线被误删'无法区分。\n"
                f"  若这是新场景：先跑 `INKSTONE_UPDATE_GOLDEN=1 pytest "
                f"{_module_hint()}` 生成基线，再**逐像素确认**产物符合预期，"
                f"然后把基线提交进仓库。"
            )

        difference = describe_difference(decode_png(baseline.read_bytes()), decode_png(png))
        if difference is None:
            return

        self.failures_dir.mkdir(parents=True, exist_ok=True)
        (self.failures_dir / f"{name}.actual.png").write_bytes(png)
        raise AssertionError(
            f"黄金图 {name}.png 像素不一致：\n{difference}\n"
            f"  实际产物已写到 {self.failures_dir / f'{name}.actual.png'}。\n"
            f"  确认差异符合预期后，用 `INKSTONE_UPDATE_GOLDEN=1 pytest "
            f"{_module_hint()}` 更新基线。"
        )


def _module_hint() -> str:
    return "tests/unit/test_golden_form.py tests/unit/test_text_render.py"
