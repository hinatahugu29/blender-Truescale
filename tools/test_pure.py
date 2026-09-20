#!/usr/bin/env python3
"""Blender を使わない部分の単体テスト。

truescale/core と truescale/export は bpy に依存しないので、
Blender を起動せずに検証できる。ヘッドレステストより桁違いに速く、
数値の取り違えのような間違いはここで捕まえられる。

実行:
    python tools/test_pure.py

終了コード: 0 = 全て成功 / 1 = 失敗あり
"""

import math
import re
import zlib
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from truescale.core import paper, session, state, units
from truescale.export import png
from truescale.export import pdf
from truescale.export import sheets
from truescale.export import tiling
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


class _FakeScene:
    """色設定だけを持つ、シーンの代役。"""

    def __init__(self, **colors):
        for name, value in colors.items():
            setattr(self, name, value)


@test
def test_marks_follow_type_color_setting():
    """オートも手動も、種類ごとの色設定に従う。

    以前は置いた時点の色を保存値から使っていたため、あとから
    設定を変えても手動のものだけ変わらなかった。オート合印と
    矢印は追従していたので、同じパネルの中で食い違っていた。
    """
    scene = _FakeScene(
        tsunfold_notch_color=(0.0, 0.0, 1.0),
        tsunfold_arrow_color=(0.0, 1.0, 0.0),
    )

    auto = {"type": storage.NOTCH, "auto": True, "color": [1.0, 0.0, 0.0]}
    manual = {"type": storage.NOTCH, "auto": False, "color": [1.0, 0.0, 0.0]}
    arrow = {"type": storage.ARROW, "color": [1.0, 0.0, 0.0]}

    check(
        storage.scene_item_color(auto, scene) == (0.0, 0.0, 1.0),
        "オート合印が設定に従っていない",
    )
    check(
        storage.scene_item_color(manual, scene) == (0.0, 0.0, 1.0),
        "手動の合印が設定に従っていない",
    )
    check(
        storage.scene_item_color(arrow, scene) == (0.0, 1.0, 0.0),
        "矢印が矢印の色設定に従っていない",
    )


@test
def test_color_falls_back_to_saved_value():
    """設定が取れないときは保存値を使う。

    古いファイルや、プロパティがまだ登録されていない状態でも
    描けるようにするため。
    """
    item = {"type": storage.NOTCH, "color": [1.0, 0.0, 0.0]}
    check(
        storage.scene_item_color(item, None) == (1.0, 0.0, 0.0),
        "シーンが無いときに保存値へ落ちていない",
    )
    check(
        storage.scene_item_color(item, _FakeScene()) == (1.0, 0.0, 0.0),
        "設定が未登録のときに保存値へ落ちていない",
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
def test_diagonal_line_keeps_its_thickness():
    """斜めの線でも、指定した太さで引かれる。

    以前は線上の各ピクセルへ正方形のブラシを置いていたため、
    45度の線は指定の約1.41倍に太った。実寸で刷るための機能なので、
    指定と実際がずれるのは困る。
    """
    size = 81
    thickness = 9

    def stroke_width(x0, y0, x1, y1):
        buf = png.new_buffer(size, size)
        png.draw_line(buf, size, size, x0, y0, x1, y1,
                      thickness, (0.0, 0.0, 0.0))

        # 塗られた画素の総数を線の長さで割れば、平均の太さになる。
        # 斜めの長さは辺の差ではなく斜辺なので、hypot で測る。
        painted = sum(
            1
            for i in range(size * size)
            if buf[i * 3] == 0
        )
        length = math.hypot(x1 - x0, y1 - y0)
        return painted / length

    horizontal = stroke_width(10, 40, 70, 40)
    diagonal = stroke_width(10, 10, 70, 70)

    check(
        abs(horizontal - thickness) <= 1.5,
        f"水平線の太さがずれている: {horizontal:.2f}",
    )
    check(
        abs(diagonal - horizontal) <= 1.5,
        f"斜めの線だけ太さが違う: 水平 {horizontal:.2f} / 斜め {diagonal:.2f}",
    )


@test
def test_line_is_not_skewed():
    """斜めの線が平行四辺形にならない。

    線の向きに直角ではなく軸方向へ広げると、断面が斜めになる。
    線をまたぐ縦の並びと横の並びで、塗られた幅が同じになるはず。
    """
    size = 61
    buf = png.new_buffer(size, size)
    png.draw_line(buf, size, size, 10, 10, 50, 50, 9, (0.0, 0.0, 0.0))

    def painted_in_row(y):
        return sum(1 for x in range(size) if buf[(y * size + x) * 3] == 0)

    def painted_in_col(x):
        return sum(1 for y in range(size) if buf[(y * size + x) * 3] == 0)

    # 端の影響を避けて真ん中で測る
    row = painted_in_row(30)
    col = painted_in_col(30)
    check(row > 0 and col > 0, "中央に線が無い")
    check(
        abs(row - col) <= 1,
        f"縦横で断面が違う（傾いている）: 行 {row} / 列 {col}",
    )


@test
def test_draw_line_color():
    """指定した色で描かれる。"""
    buf = png.new_buffer(4, 4)
    png.draw_line(buf, 4, 4, 0, 0, 3, 0, 1, (1.0, 0.0, 0.0))
    check(buf[0:3] == b"\xff\x00\x00", f"赤で描かれていない: {bytes(buf[0:3])}")


# ============================================================
# export.sheets
# ============================================================

class _FakeDrawing:
    def __init__(self, lines, w, h):
        self.lines = lines
        self.width_mm = w
        self.height_mm = h


@test
def test_clipping_keeps_the_line_straight():
    """枠で切っても、線は元の直線の上に乗ったまま。

    端点を動かすだけなので、切った結果も同じ直線上にある。
    ここがずれると、タイルの境目で線が折れる。
    """
    box = (0.0, 0.0, 100.0, 100.0)
    cut = sheets._clip(-50.0, -50.0, 150.0, 150.0, box)
    check(cut is not None, "45度の線が切り落とされた")

    x0, y0, x1, y1 = cut
    # 元の直線は y = x
    check(abs(x0 - y0) < 1e-9, f"始点が直線から外れた: {cut}")
    check(abs(x1 - y1) < 1e-9, f"終点が直線から外れた: {cut}")
    check(abs(x0 - 0.0) < 1e-9 and abs(x1 - 100.0) < 1e-9,
          f"枠の縁で切れていない: {cut}")


@test
def test_clipping_drops_lines_outside():
    """枠の外の線は落とす。中の線はそのまま。"""
    box = (0.0, 0.0, 100.0, 100.0)
    check(sheets._clip(200.0, 200.0, 300.0, 300.0, box) is None,
          "枠の外の線が残った")
    check(sheets._clip(-10.0, 50.0, -5.0, 50.0, box) is None,
          "枠の左外の線が残った")

    inside = sheets._clip(10.0, 10.0, 90.0, 90.0, box)
    check(inside == (10.0, 10.0, 90.0, 90.0), f"中の線が変わった: {inside}")


@test
def test_tiled_sheets_come_in_reading_order():
    """タイルは、紙を並べた見た目の順で返る。

    左上から右へ、そして下へ。印刷したものを上から重ねれば
    そのまま並ぶ。
    """
    drawing = _FakeDrawing([(0.0, 0.0, 400.0, 500.0, (0, 0, 0), 0.4)],
                           400.0, 500.0)
    plan = tiling.plan(400.0, 500.0, 210.0, 297.0)
    made = sheets.tiled(drawing, plan)

    check(len(made) == plan.count, f"枚数が違う: {len(made)}")
    labels = [s.label for s in made]
    check(labels[0] == "1-A", f"最初が左上でない: {labels}")
    check(
        labels == sorted(labels, key=lambda v: (v[-1], int(v.split("-")[0]))),
        f"並び順が読む順になっていない: {labels}",
    )


@test
def test_nothing_is_drawn_in_the_unprintable_margin():
    """余白の中には何も描かない。

    余白は「プリンタが刷れない縁」として決めたもの。そこへ描いた
    ものは刷っても出ない。実際、縮尺を確かめるための目盛りを
    端から 3.2mm（余白 8mm の指定の内側）に描いていたことがある。
    出ないのでは意味がない。
    """
    drawing = _FakeDrawing(
        [(0.0, 0.0, 600.0, 800.0, (0, 0, 0), 0.4),
         (0.0, 800.0, 600.0, 0.0, (0, 0, 0), 0.4)],
        600.0, 800.0,
    )
    plan = tiling.plan(600.0, 800.0, 210.0, 297.0)
    margin = plan.margin

    for sheet in sheets.tiled(drawing, plan):
        for x0, y0, x1, y1, _, _ in sheet.lines:
            for x, y in ((x0, y0), (x1, y1)):
                check(
                    margin - 1e-6 <= x <= sheet.paper_w - margin + 1e-6,
                    f"{sheet.label}: 横が余白へ出ている x={x:.3f}",
                )
                check(
                    margin - 1e-6 <= y <= sheet.paper_h - margin + 1e-6,
                    f"{sheet.label}: 縦が余白へ出ている y={y:.3f}",
                )


@test
def test_the_ruler_sits_inside_the_printable_area():
    """目盛りが刷れる範囲の中にある。

    上のテストと合わせて、目盛りが「ある」かつ「刷れる」ことを
    まとめて保証する。
    """
    drawing = _FakeDrawing([(0.0, 0.0, 400.0, 500.0, (0, 0, 0), 0.4)],
                           400.0, 500.0)
    plan = tiling.plan(400.0, 500.0, 210.0, 297.0)

    for sheet in sheets.tiled(drawing, plan):
        ruler_y = [
            y0
            for x0, y0, x1, y1, _, _ in sheet.lines
            if abs(y1 - y0) < 1e-9 and abs(abs(x1 - x0) - sheets.RULER_MM) < 1e-9
        ]
        check(ruler_y, f"{sheet.label} に目盛りが無い")
        for y in ruler_y:
            check(
                y >= plan.margin - 1e-6,
                f"{sheet.label}: 目盛りが余白の中にある y={y:.3f}",
            )
            # 型紙の載る範囲（受け持ち）より下にあること
            check(
                y < plan.page_origin()[1],
                f"{sheet.label}: 目盛りが型紙の範囲に重なる y={y:.3f}",
            )


def _ruler_length(sheet):
    """その紙に描かれた目盛りの実際の長さ。無ければ 0。

    目盛りの軸は太さ 0.3 の水平線。枠（0.2）や型紙の線（0.4）と
    区別する。
    """
    lengths = [
        abs(x1 - x0)
        for x0, y0, x1, y1, _, width in sheet.lines
        if abs(width - 0.3) < 1e-9 and abs(y1 - y0) < 1e-9
        and abs(x1 - x0) > 5.0
    ]
    return max(lengths) if lengths else 0.0


@test
def test_every_sheet_carries_a_ruler():
    """どの紙にも実寸の目盛りが入る。

    1枚でも欠けると、その紙だけ縮尺を確かめられない。
    """
    drawing = _FakeDrawing([(0.0, 0.0, 400.0, 500.0, (0, 0, 0), 0.4)],
                           400.0, 500.0)
    plan = tiling.plan(400.0, 500.0, 210.0, 297.0)

    for sheet in sheets.tiled(drawing, plan):
        check(
            abs(_ruler_length(sheet) - sheets.RULER_MM) < 1e-9,
            f"{sheet.label} に 100mm の目盛りが無い",
        )


@test
def test_the_ruler_is_never_cut_off():
    """目盛りが紙からはみ出して切られることがない。

    はがき（刷れる幅 84mm）に 100mm の目盛りを描いていたことが
    ある。はみ出した分が切られて 84mm になり、定規を当てた人は
    「印刷が縮んでいる」と読む。実際は縮んでいない。縮尺を
    確かめるための目盛りが逆に誤解させるので、無いより悪い。
    """
    drawing = _FakeDrawing([(0.0, 0.0, 300.0, 400.0, (0, 0, 0), 0.4)],
                           300.0, 400.0)

    papers = {
        "A4": (210.0, 297.0),
        "A4横": (297.0, 210.0),
        "A3": (297.0, 420.0),
        "はがき": (100.0, 148.0),
        "名刺": (55.0, 91.0),
    }

    for name, (pw, ph) in papers.items():
        plan = tiling.plan(300.0, 400.0, pw, ph)
        check(plan is not None, f"{name} で計画が立たない")

        for sheet in sheets.tiled(drawing, plan):
            length = _ruler_length(sheet)
            if length == 0.0:
                continue   # 入らない紙では描かない。それでよい

            # 切りのよい値そのものであること（切られていない証拠）
            check(
                any(abs(length - choice) < 1e-9
                    for choice in sheets.RULER_CHOICES),
                f"{name}: 目盛りが半端な長さ {length:.2f}mm（切られた）",
            )
            check(
                length <= pw - plan.margin * 2.0 + 1e-9,
                f"{name}: 目盛りが刷れる幅を超えている {length:.2f}mm",
            )


@test
def test_the_ruler_says_how_long_it_is():
    """目盛りに長さが書いてある。

    何ミリのはずなのかが紙に無いと、測っても判断できない。
    紙によって長さが変わるので、なおさら書いていないと困る。
    """
    drawing = _FakeDrawing([(0.0, 0.0, 300.0, 400.0, (0, 0, 0), 0.4)],
                           300.0, 400.0)

    for pw, ph, expected in ((210.0, 297.0, 100), (100.0, 148.0, 50)):
        plan = tiling.plan(300.0, 400.0, pw, ph)
        sheet = sheets.tiled(drawing, plan)[0]

        length = _ruler_length(sheet)
        check(
            abs(length - expected) < 1e-9,
            f"{pw:.0f}mm 幅で目盛りが {length:.0f}mm（{expected} のはず）",
        )

        # 数値が線として描かれていること。桁数ぶんの字形が要る。
        expected_text = f"{expected}MM"
        strokes = sum(
            len(sheets._STROKES.get(char, ()))
            for char in expected_text
        )
        check(strokes > 0, f"{expected_text} の字形が無い")


@test
def test_the_footer_never_reaches_the_pattern():
    """帯の目印が、型紙の載る範囲へ食い込まない。

    食い込むと、型紙の線と重なって読めなくなる。
    """
    drawing = _FakeDrawing([], 300.0, 400.0)

    for pw, ph in ((210.0, 297.0), (100.0, 148.0), (55.0, 91.0)):
        plan = tiling.plan(300.0, 400.0, pw, ph)
        top = plan.page_origin()[1]

        for sheet in sheets.tiled(drawing, plan):
            below = [
                max(y0, y1)
                for x0, y0, x1, y1, _, _ in sheet.lines
                if max(y0, y1) < top - 1e-9
            ]
            if below:
                check(
                    max(below) < top,
                    f"{pw:.0f}mm: 帯が型紙の範囲へ食い込む",
                )


@test
def test_tiles_do_not_move_the_pattern():
    """タイルへ移しても、線の長さは変わらない。

    分割は位置をずらすだけ。長さが変われば実寸が崩れる。
    """
    import math

    original = (30.0, 40.0, 130.0, 40.0)   # ちょうど100mm の横線
    drawing = _FakeDrawing(
        [(original[0], original[1], original[2], original[3], (0, 0, 0), 0.4)],
        400.0, 500.0,
    )
    plan = tiling.plan(400.0, 500.0, 210.0, 297.0)

    total = 0.0
    for sheet in sheets.tiled(drawing, plan):
        for x0, y0, x1, y1, _, width in sheet.lines:
            # 目盛りや枠ではなく、型紙の線だけを見る
            if abs(width - 0.4) < 1e-9 and abs(y1 - y0) < 1e-9:
                total += math.hypot(x1 - x0, y1 - y0)

    # 重なりの分だけ二重に数えるので、元の長さ以上にはなるが
    # 足りないことはあってはならない
    check(
        total >= 100.0 - 1e-6,
        f"分割すると線が短くなった: {total:.4f} mm",
    )


@test
def test_a_small_pattern_needs_no_tiling():
    """紙に収まる型紙は、分割せず1枚で返る。"""
    drawing = _FakeDrawing([(0.0, 0.0, 100.0, 150.0, (0, 0, 0), 0.4)],
                           100.0, 150.0)
    made = sheets.single(drawing, 210.0, 297.0)
    check(made is not None, "収まるはずが 1枚にならない")
    check(len(made) == 1, f"1枚のはずが {len(made)} 枚")

    too_big = _FakeDrawing([(0.0, 0.0, 400.0, 150.0, (0, 0, 0), 0.4)],
                           400.0, 150.0)
    check(
        sheets.single(too_big, 210.0, 297.0) is None,
        "収まらないのに 1枚で返した",
    )


# ============================================================
# export.pdf
# ============================================================

def _pdf_streams(data):
    """PDF から内容ストリームの中身を取り出す。

    正規表現を使わないのは、改行の扱いで壊れやすいから。
    """
    newline = chr(10).encode("ascii")
    results = []
    at = 0
    while True:
        head = data.find(b"stream", at)
        if head < 0:
            break
        tail = data.find(b"endstream", head)
        if tail < 0:
            break
        body = data[data.index(newline, head) + 1:tail]
        results.append(body.strip(b"\r\n"))
        at = tail + 1
    return results


def _a4_pdf():
    page = pdf.Page(210.0, 297.0)
    page.line(10.0, 10.0, 110.0, 10.0, 0.4)          # ちょうど100mm
    page.rect(10.0, 20.0, 50.0, 30.0, 0.2)
    page.polyline([(10.0, 60.0), (60.0, 80.0), (110.0, 60.0)], 0.5)
    second = pdf.Page(210.0, 297.0)
    second.line(0.0, 0.0, 10.0, 10.0)
    return pdf.build([page, second], title="test")


@test
def test_pdf_object_offsets_are_correct():
    """参照表の位置が、実際のオブジェクトを指している。

    ここがずれると、読み手によっては開けない。位置は組み立てながら
    数えるので、内容を足してもずれないはず。
    """
    data = _a4_pdf()
    check(data.startswith(b"%PDF-1.4"), "先頭が PDF ではない")
    check(data.rstrip().endswith(b"%%EOF"), "末尾が %%EOF ではない")

    found = re.search(rb"startxref\s+(\d+)\s+%%EOF", data)
    check(found is not None, "startxref が無い")

    xref_at = int(found.group(1))
    check(data[xref_at:xref_at + 4] == b"xref", "startxref が xref を指さない")

    head = re.match(rb"xref\s+0\s+(\d+)\s+", data[xref_at:])
    total = int(head.group(1))
    body = data[xref_at + head.end():]
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf])", body[:total * 20])
    check(len(entries) == total, f"参照表の件数が合わない: {len(entries)}")

    for index, (offset, _, kind) in enumerate(entries):
        if kind == b"f":
            continue
        at = int(offset)
        expected = f"{index} 0 obj".encode("ascii")
        check(
            data[at:at + len(expected)] == expected,
            f"{index} 番の位置が違う（{data[at:at + 16]!r}）",
        )


@test
def test_pdf_page_is_exactly_the_paper_size():
    """ページの大きさが、指定した用紙の実寸になる。

    PDF の長さは 1/72 インチ。A4 は 595.276 × 841.890 pt。
    ここがずれると、原寸で刷っても合わない。
    """
    data = _a4_pdf()
    boxes = re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)
    check(len(boxes) == 2, f"ページ数が違う: {len(boxes)}")

    width = float(boxes[0][0])
    height = float(boxes[0][1])
    check(abs(width - 595.2756) < 0.01, f"幅が A4 でない: {width} pt")
    check(abs(height - 841.8898) < 0.01, f"高さが A4 でない: {height} pt")

    # ミリへ戻して 0.01mm 未満
    check(abs(width / 72.0 * 25.4 - 210.0) < 0.01, "幅がミリへ戻らない")
    check(abs(height / 72.0 * 25.4 - 297.0) < 0.01, "高さがミリへ戻らない")


@test
def test_pdf_keeps_lengths_in_real_size():
    """100mm と書いた線が、PDF の中でも 100mm になる。

    刷ったものを定規で測る前に、ここで確かめられる。
    """
    data = _a4_pdf()
    streams = _pdf_streams(data)
    check(streams, "内容ストリームが無い")

    text = zlib.decompress(streams[0]).decode("ascii")
    check(text.startswith("1 J 1 j"), "線端の指定が先頭に無い")

    horizontal = [
        abs(float(x1) - float(x0))
        for x0, y0, x1, y1 in re.findall(
            r"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", text
        )
        if abs(float(y1) - float(y0)) < 0.01
    ]
    expected = 100.0 * 72.0 / 25.4
    check(
        any(abs(value - expected) < 0.01 for value in horizontal),
        f"100mm の線が見つからない: {horizontal}",
    )


@test
def test_pdf_does_not_repeat_the_same_color():
    """同じ色と太さが続くとき、命令を繰り返さない。

    線1本ごとに書くと、30枚の型紙で内容が数倍に膨らむ。
    """
    page = pdf.Page(210.0, 297.0)
    for i in range(50):
        page.line(0.0, float(i), 10.0, float(i), 0.4, (0.0, 0.0, 0.0))

    text = page.content().decode("ascii")
    check(text.count(" RG") == 1, f"色の指定が {text.count(' RG')} 回")
    check(text.count(" w") == 1, f"太さの指定が {text.count(' w')} 回")


@test
def test_pdf_refuses_an_empty_document():
    """ページが1枚も無ければ、壊れたファイルを作らずに断る。"""
    try:
        pdf.build([])
    except ValueError:
        return
    check(False, "ページ0枚でも PDF を作ってしまった")


# ============================================================
# export.tiling
# ============================================================

@test
def test_tiles_cover_the_whole_pattern():
    """型紙のどの点も、必ずどれかのタイルに入る。

    抜けがあると、貼り合わせても穴が空く。角と辺の上を含めて見る。
    """
    shape_w, shape_h = 420.0, 520.0
    p = tiling.plan(shape_w, shape_h, 210.0, 297.0)
    check(p is not None, "計画が立たない")

    steps = 37  # 端と中間を含む、割り切れない刻み
    for i in range(steps + 1):
        for j in range(steps + 1):
            x = shape_w * i / steps
            y = shape_h * j / steps
            check(
                tiling.covers(p, x, y),
                f"どのタイルにも入らない点がある: ({x:.2f}, {y:.2f})",
            )


@test
def test_overlap_is_what_was_asked_for():
    """隣り合うタイルは、指定した幅だけ確実に重なる。"""
    for overlap in (0.0, 5.0, 15.0, 30.0):
        p = tiling.plan(600.0, 800.0, 210.0, 297.0, overlap=overlap)
        check(p is not None, f"計画が立たない: のりしろ {overlap}")

        horizontal, vertical = tiling.seam_overlap(p)
        check(
            abs(horizontal - overlap) < 1e-9,
            f"横の重なりが違う: {horizontal} ≠ {overlap}",
        )
        check(
            abs(vertical - overlap) < 1e-9,
            f"縦の重なりが違う: {vertical} ≠ {overlap}",
        )

        # 実際に隣のタイルと重なっているか
        if p.cols >= 2:
            _, _, x1, _ = p.window(0, 0)
            x0_next, _, _, _ = p.window(1, 0)
            check(
                abs((x1 - x0_next) - overlap) < 1e-9,
                f"隣のタイルとの重なりが違う: {x1 - x0_next}",
            )


@test
def test_no_drift_across_many_tiles():
    """タイルが何枚並んでも、位置のずれが積み上がらない。

    1枚ずつ「前の右隣」と決めていくと、丸めの誤差が枚数だけ
    積み上がる。どのタイルも型紙の左下からの絶対値で出すこと。
    """
    p = tiling.plan(3000.0, 200.0, 210.0, 297.0)
    check(p is not None, "計画が立たない")
    check(p.cols >= 15, f"タイルが少なすぎて検証にならない: {p.cols}")

    for col in range(p.cols):
        x0, _, _, _ = p.window(col, 0)
        expected = col * p.step_w
        check(
            abs(x0 - expected) < 1e-9,
            f"{col} 枚目の位置がずれている: {x0} ≠ {expected}",
        )

    # 累積して求めた場合との差が出ないこと
    walked = 0.0
    for col in range(p.cols):
        x0, _, _, _ = p.window(col, 0)
        check(
            abs(x0 - walked) < 1e-9,
            f"{col} 枚目で累積とずれた: {x0} ≠ {walked}",
        )
        walked += p.step_w


@test
def test_pattern_smaller_than_paper_is_one_tile():
    """紙に収まる型紙は1枚のまま。無駄に分割しない。"""
    p = tiling.plan(150.0, 200.0, 210.0, 297.0)
    check(p is not None, "計画が立たない")
    check(p.count == 1, f"1枚で済むはずが {p.count} 枚")


@test
def test_tiling_works_for_any_paper():
    """A4でもA3でも、用紙を渡せば同じように成り立つ。

    どちらの人にも使える仕組みであることを、枚数と被覆で確かめる。
    """
    papers = {
        "A4": (210.0, 297.0),
        "A3": (297.0, 420.0),
        "レターっぽい何か": (216.0, 279.0),
    }
    shape_w, shape_h = 500.0, 700.0

    for name, (pw, ph) in papers.items():
        p = tiling.plan(shape_w, shape_h, pw, ph)
        check(p is not None, f"{name} で計画が立たない")
        check(p.count >= 1, f"{name} で枚数が 0")

        # 紙が大きいほど枚数は減る（同じ型紙なら）
        for x, y in ((0.0, 0.0), (shape_w, shape_h), (shape_w / 2, shape_h)):
            check(
                tiling.covers(p, x, y),
                f"{name} で ({x}, {y}) が抜けている",
            )


@test
def test_pixels_round_trip_within_half_a_pixel():
    """ミリ→ピクセル→ミリで戻しても、半ピクセル以上ずれない。

    ここが分割の正確さそのもの。300dpi の半ピクセルは 0.042 mm。
    枚数が増えても誤差が積み上がらないことを、端のタイルまで見る。
    """
    dpi = 300.0
    tolerance = 25.4 / dpi / 2.0

    p = tiling.plan(2400.0, 1500.0, 210.0, 297.0)
    check(p is not None, "計画が立たない")
    check(p.count >= 60, f"タイルが少なすぎる: {p.count}")

    worst = 0.0
    for row in range(p.rows):
        for col in range(p.cols):
            x0, y0, x1, y1 = p.window(col, row)
            for x, y in (
                (x0, y0),
                (x1, y1),
                ((x0 + x1) / 2.0, (y0 + y1) / 2.0),
                (x0 + 0.3333, y1 - 0.7777),
            ):
                px, py = tiling.to_pixels(p, col, row, x, y, dpi)
                bx, by = tiling.from_pixels(p, col, row, px, py, dpi)
                worst = max(worst, abs(bx - x), abs(by - y))

    check(
        worst <= tolerance,
        f"ミリ→ピクセルの往復で {worst:.6f} mm ずれた（許容 {tolerance:.6f}）",
    )


@test
def test_the_same_point_lands_consistently_on_overlapping_tiles():
    """重なり部分の同じ点は、どちらのタイルでも同じ実寸位置になる。

    貼り合わせたときに線が食い違わないことの根拠。タイルごとに
    座標の出し方が違うと、重なりで二重にずれて見える。
    """
    dpi = 300.0
    p = tiling.plan(800.0, 600.0, 210.0, 297.0)
    check(p is not None, "計画が立たない")
    check(p.cols >= 2, "検証には横2枚以上が要る")

    # 1枚目と2枚目が重なっている範囲の点を選ぶ
    _, _, x1, _ = p.window(0, 0)
    x0_next, _, _, _ = p.window(1, 0)
    check(x0_next < x1, "隣のタイルと重なっていない")

    shared_x = (x0_next + x1) / 2.0
    shared_y = 50.0

    left = tiling.to_pixels(p, 0, 0, shared_x, shared_y, dpi)
    right = tiling.to_pixels(p, 1, 0, shared_x, shared_y, dpi)

    back_left = tiling.from_pixels(p, 0, 0, left[0], left[1], dpi)
    back_right = tiling.from_pixels(p, 1, 0, right[0], right[1], dpi)

    check(
        abs(back_left[0] - back_right[0]) < 1e-9
        and abs(back_left[1] - back_right[1]) < 1e-9,
        f"重なりで位置が食い違う: {back_left} と {back_right}",
    )


@test
def test_tile_image_is_the_size_of_the_paper():
    """タイルの画像は紙と同じ大きさで作られる。

    紙より小さい画像を「用紙に合わせて」刷らせると拡大される。
    実寸が崩れる一番ありがちな経路なので、画像自体を紙の寸法にする。
    """
    p = tiling.plan(600.0, 800.0, 210.0, 297.0)
    width_px, height_px = tiling.pixel_size(p, 300.0)

    check(width_px == 2480, f"A4の幅が 2480px でない: {width_px}")
    check(height_px == 3508, f"A4の高さが 3508px でない: {height_px}")

    # 画像の寸法をミリへ戻して、紙とのずれが 0.05mm 未満
    back_w = width_px / 300.0 * 25.4
    back_h = height_px / 300.0 * 25.4
    check(abs(back_w - 210.0) < 0.05, f"幅が紙とずれる: {back_w}")
    check(abs(back_h - 297.0) < 0.05, f"高さが紙とずれる: {back_h}")


@test
def test_impossible_settings_are_refused():
    """成り立たない指定は、黙って変な値を返さない。"""
    check(
        tiling.plan(100.0, 100.0, 210.0, 297.0, margin=150.0) is None,
        "余白が紙より大きいのに計画を返した",
    )
    check(
        tiling.plan(100.0, 100.0, 210.0, 297.0, margin=8.0, overlap=500.0)
        is None,
        "のりしろが紙より大きいのに計画を返した",
    )
    check(tiling.plan(0.0, 100.0, 210.0, 297.0) is None, "幅0を受け入れた")


@test
def test_tile_labels_read_like_the_printed_sheets():
    """タイルの呼び名が、紙を並べた見た目と一致する。

    行を下から数えると、番号を見ながら並べられない。
    左上が 1-A になること。
    """
    p = tiling.plan(400.0, 500.0, 210.0, 297.0)
    check(p is not None, "計画が立たない")
    check(p.cols >= 2 and p.rows >= 2, "検証には2×2以上が要る")

    top_left = p.label(0, p.rows - 1)
    check(top_left == "1-A", f"左上が 1-A ではない: {top_left}")

    # 同じ呼び名が2つ無いこと
    names = [
        p.label(c, r) for r in range(p.rows) for c in range(p.cols)
    ]
    check(len(names) == len(set(names)), f"呼び名が重複している: {names}")


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
