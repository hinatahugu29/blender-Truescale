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

from truescale.core import paper, session, state, units
from truescale.export import png
from truescale.marking import storage

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
# state
# ============================================================

@test
def test_invalidate_advances_epoch():
    """無効化すると epoch が進み、キャッシュが空になる。"""
    state.invalidate()
    state.store(("x", 1), "値")
    before = state.epoch

    state.invalidate()

    check(state.epoch == before + 1, f"epoch が進んでいない: {state.epoch}")
    check(state.get(("x", 1)) is None, "キャッシュが残っている")


@test
def test_store_evicts_oldest():
    """上限を超えたら古いものから捨てる。

    全消しにすると、ある設定のキャッシュが埋まったときに
    無関係なキャッシュまで巻き添えで消える。
    """
    state.invalidate()
    limit = state.DRAW_CACHE_LIMIT

    for index in range(limit):
        state.store(("k", index), index)
    check(len(state.draw_cache) == limit, f"件数が違う: {len(state.draw_cache)}")

    # 1つ足すと、最も古いものだけが消える
    state.store(("k", limit), limit)
    check(len(state.draw_cache) == limit, f"上限を超えた: {len(state.draw_cache)}")
    check(state.get(("k", 0)) is None, "最も古いものが残っている")
    check(state.get(("k", 1)) == 1, "2番目まで消えている")
    check(state.get(("k", limit)) == limit, "新しいものが入っていない")


@test
def test_store_never_exceeds_limit():
    """大量に入れても上限を超えない。"""
    state.invalidate()
    for index in range(state.DRAW_CACHE_LIMIT * 5):
        state.store(("many", index), index)
    check(
        len(state.draw_cache) <= state.DRAW_CACHE_LIMIT,
        f"上限を超えている: {len(state.draw_cache)}",
    )


# ============================================================
# session
# ============================================================

@test
def test_session_keys_are_declared():
    """scene["..."] で使うキーが全て session に宣言されている。

    キー名を直書きすると、接頭辞の一括置換などで壊れたときに
    気づけない。宣言と使用が一致していることを機械的に確かめる。
    """
    import re

    declared = set(session.all_keys())

    # 登録プロパティなので session の管轄外。
    # 属性アクセスが正で、辞書アクセスは登録前に呼ばれた場合の保険。
    registered_fallbacks = {"tsunfold_active_tool"}

    source = (REPO_ROOT / "truescale" / "unfold" / "__init__.py").read_text(
        encoding="utf-8"
    )

    used = set()
    for match in re.finditer(r'scene(?:\.get\(|\[)"(tsunfold_\w+)"', source):
        used.add(match.group(1))

    undeclared = used - declared - registered_fallbacks
    check(
        not undeclared,
        f"session に宣言されていないキーを使っている: {sorted(undeclared)}",
    )


@test
def test_session_reset_covers_work_state():
    """ファイルを開いたときの初期化が、作業状態を網羅している。"""
    reset = session.RESET_ON_LOAD

    must_reset = (
        session.MODAL_RUNNING,
        session.MARKING_SESSION_ACTIVE,
        session.MANUAL_LAYOUT_ACTIVE,
        session.SEAM_SOURCE,
    )
    for key in must_reset:
        check(key in reset, f"初期化対象に含まれていない: {key}")

    # 退避用の値まで消すと、プレビュー復帰ができなくなる
    check(
        session.PREVIEW_SOURCE_NAME not in reset,
        "プレビューの退避情報まで初期化している",
    )


# ============================================================
# marking.storage
# ============================================================

@test
def test_item_color_clamps():
    """描画用の色は 0.0〜1.0 に収まる。"""
    result = storage.item_color({"type": "text", "color": [2.0, -1.0, 0.5]})
    check(result == (1.0, 0.0, 0.5), f"クランプされていない: {result}")


@test
def test_item_color_survives_broken_data():
    """保存値が壊れていても描画を止めない。"""
    for broken in ({"color": None}, {"color": []}, {"color": "赤"}, {}):
        result = storage.item_color(broken)
        check(len(result) == 3, f"3要素で返らない: {result}")


@test
def test_auto_notch_uses_scene_color():
    """オート合印はシーンの色に従い、手動は保存値を使う。"""
    auto = {"type": storage.NOTCH, "auto": True, "color": [1.0, 0.0, 0.0]}
    manual = {"type": storage.NOTCH, "auto": False, "color": [1.0, 0.0, 0.0]}

    scene_color = (0.0, 0.0, 1.0)
    check(
        storage.item_color(auto, scene_color) == (0.0, 0.0, 1.0),
        "オートがシーンの色に従っていない",
    )
    check(
        storage.item_color(manual, scene_color) == (1.0, 0.0, 0.0),
        "手動の色が上書きされている",
    )


@test
def test_normalize_color_rounds():
    """保存用の色は桁を丸める。

    同じ色なのに文字列が変わってキャッシュキーがずれるのを避ける。
    """
    result = storage.normalize_color([0.1234567891, 0.5, 1.0])
    check(result == [0.12346, 0.5, 1.0], f"丸められていない: {result}")


@test
def test_without_notches():
    """合印だけを取り除ける。オートのみも選べる。"""
    items = [
        {"type": storage.NOTCH, "auto": True},
        {"type": storage.NOTCH, "auto": False},
        {"type": "text", "value": "残る"},
    ]

    all_removed = storage.without_notches(items)
    check(len(all_removed) == 1, f"合印が残っている: {all_removed}")
    check(all_removed[0]["type"] == "text", "他の注記まで消えた")

    auto_only = storage.without_notches(items, auto_only=True)
    check(len(auto_only) == 2, f"手動まで消えている: {auto_only}")


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
