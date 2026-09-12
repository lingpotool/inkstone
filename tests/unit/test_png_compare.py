"""PNG 解码器与黄金图基线的自测（docs/16 §R2.1 / §R2.2）。

这一组测试守的是**门禁本身**：黄金图比对从"PNG 字节"改成"解码后像素"之后，
必须证明三件事——

1. 解码器能还原自家编码器的产物（往返全等）；
2. 5 种行滤波器都能解（只实现 None 的解码器是假解码器）；
3. **字节不同但像素相同的两张图必须判等**——这正是换掉比对单位的原因：
   zlib 的输出不跨版本保证一致，"三平台逐字节相同"曾经是侥幸。

以及 R2.2：基线缺失必须失败，不许顺手写一份新基线然后绿灯。
"""

from __future__ import annotations

import pathlib
import struct
import zlib

import pytest

from inkstone.gfx import encode_png
from png_compare import (
    PNG_SIGNATURE,
    DecodedPng,
    GoldenBaseline,
    PngFormatError,
    decode_png,
    describe_difference,
)

# ---------------------------------------------------------------- 工具


def _rgba(width: int, height: int, seed: int = 0) -> bytes:
    """造一段确定性的 RGBA 像素（不用随机数——测试也要确定性）。"""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes(
                (
                    (x * 37 + y * 11 + seed) & 0xFF,
                    (x * 5 + y * 61 + seed * 3) & 0xFF,
                    (x * 97 + y * 23 + seed * 7) & 0xFF,
                    255,
                )
            )
    return bytes(out)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_with_rows(width: int, height: int, rows: list[tuple[int, bytes]], level: int = 6) -> bytes:
    """按给定的 (滤波类型, 滤波后字节) 组装 PNG。用来构造非 None 滤波的样本。"""
    raw = b"".join(bytes((ftype,)) + line for ftype, line in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, level))
        + _chunk(b"IEND", b"")
    )


def _forward_filter(row: bytes, prev: bytes, ftype: int) -> bytes:
    """测试侧独立实现的 PNG 正向滤波（与解码器互逆，用来造样本）。"""
    bpp = 4
    out = bytearray(len(row))
    for i, value in enumerate(row):
        left = row[i - bpp] if i >= bpp else 0
        up = prev[i]
        up_left = prev[i - bpp] if i >= bpp else 0
        if ftype == 0:
            predict = 0
        elif ftype == 1:
            predict = left
        elif ftype == 2:
            predict = up
        elif ftype == 3:
            predict = (left + up) >> 1
        else:
            p = left + up - up_left
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
            predict = left if (pa <= pb and pa <= pc) else (up if pb <= pc else up_left)
        out[i] = (value - predict) & 0xFF
    return bytes(out)


# ---------------------------------------------------------------- 往返


class TestRoundTrip:
    def test_encode_then_decode_is_identical(self) -> None:
        pixels = _rgba(17, 9)
        decoded = decode_png(encode_png(17, 9, pixels))
        assert decoded.width == 17
        assert decoded.height == 9
        assert decoded.rgba == pixels

    def test_single_pixel_image(self) -> None:
        pixels = bytes((1, 2, 3, 4))
        decoded = decode_png(encode_png(1, 1, pixels))
        assert decoded.pixel(0, 0) == (1, 2, 3, 4)

    def test_pixel_helper_rejects_out_of_range(self) -> None:
        decoded = decode_png(encode_png(2, 2, _rgba(2, 2)))
        with pytest.raises(IndexError):
            decoded.pixel(2, 0)


class TestAllFiveFilters:
    """5 种行滤波器都要能解——只实现 None 会在别人的 PNG 上静默出错。"""

    WIDTH = 3
    HEIGHT = 2

    @pytest.mark.parametrize("ftype", [0, 1, 2, 3, 4])
    def test_filter_round_trips(self, ftype: int) -> None:
        stride = self.WIDTH * 4
        pixels = _rgba(self.WIDTH, self.HEIGHT)
        raw_rows = [pixels[y * stride : (y + 1) * stride] for y in range(self.HEIGHT)]

        prev = bytes(stride)
        filtered: list[tuple[int, bytes]] = []
        for row in raw_rows:
            filtered.append((ftype, _forward_filter(row, prev, ftype)))
            prev = row

        decoded = decode_png(_png_with_rows(self.WIDTH, self.HEIGHT, filtered))
        assert decoded.rgba == pixels, f"滤波器 {ftype} 解码结果不对"

    def test_all_filters_in_one_image(self) -> None:
        """一张图里混用不同滤波器（真实编码器的常见行为）。"""
        stride = self.WIDTH * 4
        pixels = _rgba(self.WIDTH, 5)
        raw_rows = [pixels[y * stride : (y + 1) * stride] for y in range(5)]

        prev = bytes(stride)
        filtered: list[tuple[int, bytes]] = []
        for y, row in enumerate(raw_rows):
            ftype = y % 5
            filtered.append((ftype, _forward_filter(row, prev, ftype)))
            prev = row

        decoded = decode_png(_png_with_rows(self.WIDTH, 5, filtered))
        assert decoded.rgba == pixels

    def test_illegal_filter_type_is_rejected(self) -> None:
        with pytest.raises(PngFormatError, match="滤波类型非法"):
            decode_png(_png_with_rows(1, 1, [(9, bytes(4))]))


class TestRejectsUnsupportedForms:
    """不认识的形态一律响亮报错——悄悄解错比解不了危险得多。"""

    def test_bad_signature(self) -> None:
        with pytest.raises(PngFormatError, match="不是 PNG"):
            decode_png(b"GIF89a" + bytes(32))

    def test_sixteen_bit_depth(self) -> None:
        ihdr = struct.pack(">IIBBBBB", 1, 1, 16, 6, 0, 0, 0)
        data = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
        with pytest.raises(PngFormatError, match="位深"):
            decode_png(data)

    def test_rgb_without_alpha(self) -> None:
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        data = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
        with pytest.raises(PngFormatError, match="颜色类型"):
            decode_png(data)

    def test_interlaced_is_rejected(self) -> None:
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 1)
        data = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
        with pytest.raises(PngFormatError, match="交错"):
            decode_png(data)

    def test_missing_ihdr(self) -> None:
        data = PNG_SIGNATURE + _chunk(b"IDAT", b"x") + _chunk(b"IEND", b"")
        with pytest.raises(PngFormatError, match="IDAT 出现在 IHDR 之前"):
            decode_png(data)

    def test_missing_idat(self) -> None:
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        data = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
        with pytest.raises(PngFormatError, match="没有 IDAT"):
            decode_png(data)

    def test_crc_mismatch(self) -> None:
        good = encode_png(1, 1, bytes(4))
        broken = good[: len(good) - 8] + b"\x00\x00\x00\x00" + good[len(good) - 4 :]
        with pytest.raises(PngFormatError, match="CRC"):
            decode_png(broken)

    def test_truncated(self) -> None:
        """从中间截断（切进 IDAT 载荷）：声明的长度与实际不符，必须报清楚。"""
        with pytest.raises(PngFormatError, match="声明长度"):
            decode_png(encode_png(4, 4, _rgba(4, 4))[:-20])

    def test_truncated_tail(self) -> None:
        """尾巴被切掉（CRC 不完整）也要报错，不能静默当作解完了。"""
        with pytest.raises(PngFormatError, match="缺 CRC"):
            decode_png(encode_png(4, 4, _rgba(4, 4))[:-3])

    def test_corrupt_idat(self) -> None:
        good = encode_png(4, 4, _rgba(4, 4))
        pos = good.index(b"IDAT")
        broken = good[: pos + 4] + b"\xff\xff\xff\xff" + good[pos + 8 :]
        with pytest.raises(PngFormatError):
            decode_png(broken)


# ---------------------------------------------------------------- 差异描述


class TestDescribeDifference:
    def test_identical_returns_none(self) -> None:
        a = decode_png(encode_png(8, 8, _rgba(8, 8)))
        b = decode_png(encode_png(8, 8, _rgba(8, 8)))
        assert describe_difference(a, b) is None

    def test_one_pixel_difference_is_found(self) -> None:
        pixels = bytearray(_rgba(8, 8))
        base = decode_png(encode_png(8, 8, bytes(pixels)))

        index = (2 * 8 + 3) * 4  # 第 3 列、第 2 行
        pixels[index] = (pixels[index] + 1) & 0xFF
        changed = decode_png(encode_png(8, 8, bytes(pixels)))

        report = describe_difference(base, changed)
        assert report is not None
        assert "1 / 64 个像素不同" in report
        assert "(3,2)" in report
        assert "x 3..3" in report and "y 2..2" in report

    def test_size_mismatch_is_reported(self) -> None:
        a = decode_png(encode_png(4, 4, _rgba(4, 4)))
        b = decode_png(encode_png(4, 5, _rgba(4, 5)))
        report = describe_difference(a, b)
        assert report is not None
        assert "尺寸不同" in report


# ---------------------------------------------------------------- 黄金图基线


class TestGoldenBaseline:
    def test_bytes_differ_but_pixels_match_passes(self, tmp_path: pathlib.Path) -> None:
        """**R2.1 的核心**：zlib 压缩级别不同 → 文件字节不同、像素相同。

        旧实现比对 PNG 字节，这种情形会红——而它其实什么都没坏。
        """
        pixels = _rgba(12, 7)
        level_six = encode_png(12, 7, pixels)
        level_nine = _png_with_rows(
            12, 7, [(0, pixels[y * 48 : (y + 1) * 48]) for y in range(7)], level=9
        )
        assert level_six != level_nine, "前提：两种压缩级别应当产出不同字节"
        assert decode_png(level_six).rgba == decode_png(level_nine).rgba

        baseline = GoldenBaseline(tmp_path, update=False)
        (tmp_path / "sample.png").write_bytes(level_six)
        baseline.check("sample", level_nine)  # 不该抛

    def test_missing_baseline_fails_with_guidance(self, tmp_path: pathlib.Path) -> None:
        baseline = GoldenBaseline(tmp_path, update=False)
        with pytest.raises(AssertionError) as exc:
            baseline.check("nope", encode_png(2, 2, _rgba(2, 2)))
        message = str(exc.value)
        assert "基线缺失" in message
        assert "INKSTONE_UPDATE_GOLDEN=1" in message
        assert not (tmp_path / "nope.png").exists(), "失败路径不许顺手写基线"

    def test_update_mode_writes_the_baseline(self, tmp_path: pathlib.Path) -> None:
        png = encode_png(3, 3, _rgba(3, 3))
        GoldenBaseline(tmp_path, update=True).check("fresh", png)
        assert (tmp_path / "fresh.png").read_bytes() == png

    def test_pixel_difference_fails_and_saves_the_artifact(self, tmp_path: pathlib.Path) -> None:
        baseline = GoldenBaseline(tmp_path, update=False)
        (tmp_path / "pic.png").write_bytes(encode_png(4, 4, _rgba(4, 4)))

        changed = bytearray(_rgba(4, 4))
        changed[0] = (changed[0] + 9) & 0xFF
        with pytest.raises(AssertionError, match="像素不一致"):
            baseline.check("pic", encode_png(4, 4, bytes(changed)))

        artifact = tmp_path / "failures" / "pic.actual.png"
        assert artifact.exists(), "失败产物要落盘，供人比对"

    def test_decoded_png_is_the_comparison_unit(self) -> None:
        decoded: DecodedPng = decode_png(encode_png(2, 3, _rgba(2, 3)))
        assert decoded.pixel_count == 6
        assert len(decoded.rgba) == 2 * 3 * 4
