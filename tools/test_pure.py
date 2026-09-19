#!/usr/bin/env python3
"""Blender を使わない部分の単体テスト。

truescale/core と truescale/export は bpy に依存しないので、
Blender を起動せずに検証できる。ヘッドレステストより桁違いに速く、
数値の取り違えのような間違いはここで捕まえられる。

実行:
    python tools/test_pure.py

終了コード: 0 = 全て成功 / 1 = 失敗あり
"""

import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from truescale.core import paper, units
from truescale.export import png

_tests = []


def test(func):
    _tests.append(func)
    return func


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, tolerance, message):
    if abs(actual - expected) > tolerance:
        raise AssertionError(
            f"{message}: 期待 {expected} / 実際 {actual}"
        )


# ============================================================
# units
# ============================================================

@test
def test_mm_bu_roundtrip():
    """mm と BU の往復で元に戻る。"""
    for mm_per_bu in (1.0, 10.0, 1000.0, 0.001):
        for mm in (0.0, 1.0, 123.456, 9999.0):
            bu = units.mm_to_bu(mm, mm_per_bu)
            close(units.bu_to_mm(bu, mm_per_bu), mm, 1e-9, "往復")


@test
def test_mm_per_bu_known_values():
    """よく使う換算が合っている。"""
    close(units.mm_to_bu(1000.0, 1000.0), 1.0, 1e-12, "1 BU = 1000 mm")
    close(units.mm_to_bu(1.0, 1.0), 1.0, 1e-12, "1 BU = 1 mm")
    close(units.bu_to_mm(2.5, 150.0), 375.0, 1e-9, "2.5 BU × 150 mm")


@test
def test_unit_scale_conversion():
    """Unit Scale と mm/BU の相互変換。"""
    close(units.meters_to_mm_per_bu(1.0), 1000.0, 1e-9, "Unit Scale 1")
    close(units.meters_to_mm_per_bu(0.001), 1.0, 1e-9, "Unit Scale 0.001")
    close(units.mm_per_bu_to_meters(1000.0), 1.0, 1e-12, "逆変換")
    close(units.mm_per_bu_to_meters(1.0), 0.001, 1e-12, "逆変換")


@test
def test_invalid_scale_falls_back():
    """0 や負の換算値でゼロ除算しない。"""
    for bad in (0.0, -1.0):
        result = units.mm_to_bu(100.0, bad)
        check(result > 0.0, f"不正な換算値 {bad} で破綻している: {result}")


# ============================================================
# paper
# ============================================================

@test
def test_paper_sizes_are_portrait():
    """定義は縦向き（幅 <= 高さ）で揃っている。"""
    for name, (width, height) in paper.SIZES_MM.items():
        check(width <= height, f"{name} が縦向きでない: {width}×{height}")


@test
def test_a_series_halving():
    """A判は1つ下のサイズの2倍の面積になる。"""
    order = ["A0", "A1", "A2", "A3", "A4", "A5"]
    for larger, smaller in zip(order, order[1:]):
        big = paper.SIZES_MM[larger]
        small = paper.SIZES_MM[smaller]
        ratio = (big[0] * big[1]) / (small[0] * small[1])
        close(ratio, 2.0, 0.02, f"{larger} と {smaller} の面積比")


@test
def test_fits_both_orientations():
    """縦横どちらでも収まれば収まる判定になる。"""
    check(paper.fits(200.0, 290.0, 210.0, 297.0), "A4縦に収まるはず")
    check(paper.fits(290.0, 200.0, 210.0, 297.0), "A4横向きでも収まるはず")
    check(not paper.fits(300.0, 400.0, 210.0, 297.0), "A4に収まらないはず")


@test
def test_smallest_fitting():
    """収まる最小の用紙を選ぶ。"""
    check(paper.smallest_fitting(100.0, 140.0) == "A5", "A5のはず")
    check(paper.smallest_fitting(200.0, 290.0) == "A4", "A4のはず")
    check(paper.smallest_fitting(5000.0, 5000.0) is None, "収まらないはず")


@test
def test_orientation_swap():
    """向きの指定で幅と高さが入れ替わる。"""
    check(paper.oriented(210.0, 297.0, "LANDSCAPE") == (297.0, 210.0), "横向き")
    check(paper.oriented(297.0, 210.0, "PORTRAIT") == (210.0, 297.0), "縦向き")
    check(paper.oriented(210.0, 297.0, "AUTO") == (210.0, 297.0), "自動は素通し")


# ============================================================
# png
# ============================================================

@test
def test_mm_pixel_conversion():
    """300dpi で 25.4mm = 300px。"""
    close(png.mm_to_pixels(25.4, 300), 300.0, 1e-9, "mm→px")
    close(png.pixels_to_mm(300.0, 300), 25.4, 1e-9, "px→mm")
    close(png.mm_to_pixels(210.0, 300), 2480.31, 0.01, "A4の幅")


@test
def test_png_structure():
    """出力が PNG として最低限の形をしている。"""
    buf = png.new_buffer(4, 3)
    data = png.encode_rgb(4, 3, buf, dpi=300)

    check(data[:8] == b"\x89PNG\r\n\x1a\n", "シグネチャが違う")
    for name in (b"IHDR", b"pHYs", b"IDAT", b"IEND"):
        check(name in data, f"{name.decode()} チャンクが無い")


@test
def test_png_records_dpi():
    """pHYs に解像度が入る。実寸印刷の要。"""
    buf = png.new_buffer(2, 2)
    data = png.encode_rgb(2, 2, buf, dpi=300)

    index = data.index(b"pHYs")
    payload = data[index + 4:index + 13]
    ppm_x = int.from_bytes(payload[0:4], "big")
    unit = payload[8]

    # 300 dpi = 11811 ピクセル/メートル
    check(abs(ppm_x - 11811) <= 1, f"解像度が違う: {ppm_x}")
    check(unit == 1, f"単位指定が違う: {unit}")


@test
def test_draw_line_writes_pixels():
    """線を引くとバッファが変わり、範囲外は触らない。"""
    width = height = 8
    buf = png.new_buffer(width, height)
    before = bytes(buf)

    png.draw_line(buf, width, height, 0, 0, 7, 7, 1, (0.0, 0.0, 0.0))
    check(bytes(buf) != before, "線が描かれていない")

    # 対角線上は黒
    pos = (3 * width + 3) * 3
    check(buf[pos:pos + 3] == b"\x00\x00\x00", "対角線が黒くない")

    # 範囲外を指定しても落ちない
    png.draw_line(buf, width, height, -50, -50, 100, 100, 3, (1.0, 0.0, 0.0))


@test
def test_draw_line_color():
    """指定した色で描かれる。"""
    buf = png.new_buffer(4, 4)
    png.draw_line(buf, 4, 4, 0, 0, 3, 0, 1, (1.0, 0.0, 0.0))
    check(buf[0:3] == b"\xff\x00\x00", f"赤で描かれていない: {bytes(buf[0:3])}")


# ============================================================
# 実行
# ============================================================

def main():
    print("=" * 66)
    print("Blender非依存モジュールの単体テスト")
    print("=" * 66)

    passed = failed = 0
    for func in _tests:
        doc = (func.__doc__ or "").strip().split("\n")[0]
        try:
            func()
        except Exception:
            failed += 1
            print(f"  FAIL  {func.__name__}  — {doc}")
            for line in traceback.format_exc().rstrip().split("\n")[-3:]:
                print(f"        {line}")
        else:
            passed += 1
            print(f"  ok    {func.__name__}  — {doc}")

    print("-" * 66)
    print(f"  成功 {passed} / 失敗 {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
