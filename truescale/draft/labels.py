"""寸法の文字を、画面のどこへ置くかを決める。

置く場所を決めるだけで、描かない。描くのは overlay、切り抜きの
範囲を出すのは export.capture。

■ なぜ切り出したか

同じ配置の計算が2箇所にあった。画面へ描く側（overlay）と、
書き出しの切り抜き範囲を出す側（capture）。capture 側には
「Mirror _overlay.draw_size_labels() font fitting exactly.
Otherwise the PNG can crop labels that are correctly visible on
screen.」と書いてあった。手で写して、手で揃え続ける前提だった。

実際にはもう揃っていなかった。

  安全余白    overlay は実際の文字の高さから、capture は設定の
              文字サイズから出していた。capture のほうが広くなる
  自動縮小    その余白を使って「収まらなければ縮める」を判定する
              ので、同じ文字が別の大きさになることがあった
  出す軸      overlay はビュー方向と平行な軸を隠す。capture は
              面図ごとの2軸表から選ぶ。斜めを向いていると食い違う

ずれると、画面では見えている文字が PNG では切れる。刷ってから
でないと分からない。

■ 文字の大きさは測らないと決まらない

blf で測る。置く場所は文字の大きさに依存し（BOXから離す量も、
画面端からの余白も）、文字の大きさは自動縮小で変わる。だから
「測る → 縮める → 測り直す → 置く」の順になっている。
"""

import math

import blf
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from .. import debug as _debug
from ..core import units as _units
from . import dimension as _dimension
from . import keys as _keys
from . import viewstate as _viewstate

# 画面端からの余白の下限（ピクセル）。Nパネルやヘッダへ文字が
# 食い込まないようにする。
MIN_MARGIN_X = 20.0
MIN_MARGIN_Y = 32.0

# 自動縮小の下限。これ以下は読めない。
MIN_FONT_PX = 8

# 寸法軸がビュー方向とどれだけ平行なら隠すか。真正面から見た
# 奥行きの寸法は、線が点になるので書いても読めない。
PARALLEL_LIMIT = 0.965

AXIS_VECTORS = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}

# 三面図シートでは、寸法を図同士の隙間ではなく外周側へ逃がす。
SHEET_LAYOUT = {
    "top": {"X": "TOP", "Y": "LEFT"},
    "front": {"X": "BOTTOM", "Z": "RIGHT"},
    "side": {"Y": "BOTTOM", "Z": "LEFT"},
}

# 単体ビューは上＋左。
SINGLE_LAYOUT = {
    "top": {"X": "TOP", "Y": "LEFT"},
    "front": {"X": "TOP", "Z": "LEFT"},
    "side": {"Y": "TOP", "Z": "LEFT"},
}

SHEET_MODE_KEY = "TSDRAFT_SHEET_LAYOUT_MODE"
SUPPRESS_TEXT_KEY = "TSDRAFT_SHEET_SUPPRESS_DIM_TEXT"


class Placed:
    """置き場所の決まった1つの文字。

    x, y は文字の左下（回転するものは、回した後の見た目の左下）。
    visual_w / visual_h は回した後の見た目の大きさ。切り抜きの
    範囲はこちらで測る。
    """

    __slots__ = (
        "text", "x", "y", "width", "height",
        "rotated", "font_px",
    )

    def __init__(self, text, x, y, width, height, rotated, font_px):
        self.text = text
        self.x = float(x)
        self.y = float(y)
        self.width = float(width)
        self.height = float(height)
        self.rotated = bool(rotated)
        self.font_px = int(font_px)

    @property
    def visual_w(self):
        return self.height if self.rotated else self.width

    @property
    def visual_h(self):
        return self.width if self.rotated else self.height

    def bounds(self):
        """(min_x, min_y, max_x, max_y)。切り抜きの範囲に使う。"""
        return (
            self.x,
            self.y,
            self.x + self.visual_w,
            self.y + self.visual_h,
        )


def sheet_mode():
    """三面図シート用の配置かどうか。"""
    return bool(bpy.app.driver_namespace.get(SHEET_MODE_KEY, False))


def text_suppressed():
    """文字を出さない場面か。

    三面図シートでは、寸法をシートの上で直接描く。ビューポートの
    文字を撮影画像へ焼くと、回転と切り抜きが安定しないため。
    """
    return bool(bpy.app.driver_namespace.get(SUPPRESS_TEXT_KEY, False))


def screen_bbox(region, rv3d, bbox_obj, view_key):
    """箱を画面へ投影した矩形。(min_x, max_x, min_y, max_y)。

    面図でなければ None。文字は画面上で「箱の上中央」「箱の左中央」
    に置くので、箱が画面のどこにあるかが要る。
    """
    if view_key not in {"top", "front", "side"}:
        return None

    points = []
    try:
        for corner in bbox_obj.bound_box:
            world = bbox_obj.matrix_world @ Vector(corner)
            flat = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
            if flat is not None:
                points.append(flat)
    except Exception:
        _debug.swallowed("draft.labels.screen_bbox")
        return None

    if not points:
        return None

    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return (min(xs), max(xs), min(ys), max(ys))


def _shown(scene, view_key, axis_name, explicit_user_mode):
    """その軸の寸法を出すか。"""
    if explicit_user_mode:
        return bool(getattr(scene, "tsdraft_show_dimensions_user", True))

    if view_key == "user":
        return True

    if not getattr(scene, f"tsdraft_show_dimensions_{view_key}", True):
        return False

    return _dimension.tsdraft_dimension_axis_enabled(scene, view_key, axis_name)


def _edge_on(bbox_obj, axis_name, view_dir):
    """その寸法軸が、ビュー方向とほぼ平行か。

    真正面から見た奥行きの寸法は、線が点になるので書いても読めない。
    """
    if axis_name not in AXIS_VECTORS:
        return False

    axis_world = bbox_obj.matrix_world.to_3x3() @ AXIS_VECTORS[axis_name]
    if axis_world.length <= 0.0:
        return False

    axis_world.normalize()
    return abs(axis_world.dot(view_dir)) >= PARALLEL_LIMIT


def view_px_per_mm(region, rv3d, bbox_obj, view_key, unit_scale):
    """画面の1ミリが何ピクセルか。

    箱を画面へ投影した大きさと、箱の実寸の比。手での微調整を
    ミリで受け取るので、それをピクセルへ直すのに要る。
    """
    if bbox_obj is None:
        return 1.0

    corners = [bbox_obj.matrix_world @ v.co for v in bbox_obj.data.vertices]
    projected = []
    for co in corners:
        p2 = view3d_utils.location_3d_to_region_2d(region, rv3d, co)
        if p2 is not None:
            projected.append((float(p2.x), float(p2.y)))

    if len(projected) < 4:
        return 1.0

    xs = [p[0] for p in projected]
    ys = [p[1] for p in projected]
    px_w = max(xs) - min(xs)
    px_h = max(ys) - min(ys)

    if view_key == "top":
        vals_u = [co.x for co in corners]
        vals_v = [co.y for co in corners]
    elif view_key == "front":
        vals_u = [co.x for co in corners]
        vals_v = [co.z for co in corners]
    elif view_key == "side":
        vals_u = [co.y for co in corners]
        vals_v = [co.z for co in corners]
    else:
        vals_u = [co.x for co in corners]
        vals_v = [co.z for co in corners]

    mm_w = (max(vals_u) - min(vals_u)) * unit_scale * 1000.0
    mm_h = (max(vals_v) - min(vals_v)) * unit_scale * 1000.0

    vals = []
    if mm_w > 1e-9 and px_w > 1:
        vals.append(px_w / mm_w)
    if mm_h > 1e-9 and px_h > 1:
        vals.append(px_h / mm_h)

    return sum(vals) / len(vals) if vals else 1.0


def _measure(font_id, text, requested_px, region):
    """文字を測る。入らなければ縮めて測り直す。

    余白は実際の文字の高さから出す。設定の文字サイズから出すと、
    フォントによって実際の高さが違うぶんだけずれる。以前は
    書き出し側がそうなっていた。
    """
    blf.size(font_id, requested_px)
    width, height = blf.dimensions(font_id, text)

    margin_x = max(MIN_MARGIN_X, float(height) * 0.75)
    margin_y = max(MIN_MARGIN_Y, float(height) * 1.15)

    available_w = max(1.0, float(region.width) - margin_x * 2.0)
    available_h = max(1.0, float(region.height) - margin_y * 2.0)

    font_px = requested_px

    if width > available_w or height > available_h:
        scale = min(
            available_w / max(1.0, float(width)),
            available_h / max(1.0, float(height)),
            1.0,
        )
        font_px = max(MIN_FONT_PX, int(requested_px * scale))
        blf.size(font_id, font_px)
        width, height = blf.dimensions(font_id, text)

        margin_x = max(MIN_MARGIN_X, float(height) * 0.75)
        margin_y = max(MIN_MARGIN_Y, float(height) * 1.15)

    return width, height, font_px, margin_x, margin_y


def _anchor(layout_type, box, width, height):
    """箱に対する置き場所。(x, y)。

    回すもの（LEFT / RIGHT）は、回した後の横幅が文字の高さになる。
    """
    min_x, max_x, min_y, max_y = box
    gap = max(10.0, float(height) * 0.35)

    if layout_type == "TOP":
        return ((min_x + max_x) * 0.5 - width * 0.5, max_y + gap)

    if layout_type == "BOTTOM":
        return ((min_x + max_x) * 0.5 - width * 0.5, min_y - gap - height)

    if layout_type == "RIGHT":
        return (max_x + gap, (min_y + max_y) * 0.5 - width * 0.5)

    # LEFT
    return (min_x - gap - height, (min_y + max_y) * 0.5 - width * 0.5)


def layout(scene, region, rv3d, bbox_obj, data, view_key, view_dir,
           explicit_user_mode=False, font_id=0):
    """寸法の文字を置く場所を決める。Placed の一覧を返す。

    描かない。画面へ描く側と、切り抜きの範囲を出す側の両方が
    これを呼ぶ。同じ入力から同じ場所が出るので、画面で見えている
    文字が PNG で切れることはなくなる。
    """
    if region is None or rv3d is None or bbox_obj is None or not data:
        return []

    table = SHEET_LAYOUT if sheet_mode() else SINGLE_LAYOUT
    box = screen_bbox(region, rv3d, bbox_obj, view_key)
    requested_px = max(1, int(getattr(scene, "tsdraft_font_size", 16)))

    placed = []

    for item in data:
        axis_name = item.get("axis", "X")

        if _edge_on(bbox_obj, axis_name, view_dir):
            continue
        if not _shown(scene, view_key, axis_name, explicit_user_mode):
            continue

        text = _dimension.get_dimension_text(scene, item)
        width, height, font_px, margin_x, margin_y = _measure(
            font_id, text, requested_px, region
        )

        layout_type = table.get(view_key, {}).get(axis_name)
        rotated = layout_type in {"LEFT", "RIGHT"}

        if box is not None and layout_type in {"TOP", "BOTTOM", "LEFT", "RIGHT"}:
            x, y = _anchor(layout_type, box, width, height)
        else:
            # 任意ビューなどは、3D の位置をそのまま画面へ落とす。
            flat = view3d_utils.location_3d_to_region_2d(
                region, rv3d, item.get("location")
            ) if item.get("location") is not None else None

            if flat is None:
                continue

            x = flat.x - width * 0.5
            y = flat.y - height * 0.5

        # 手での微調整は、自動で決めた位置からのずらし量として足す。
        axis = axis_name.lower()
        px_per_mm = view_px_per_mm(
            region, rv3d, bbox_obj, view_key,
            _units.scene_scale_to_meters(scene),
        )
        x += getattr(scene, f"tsdraft_{view_key}_{axis}_offset_x_mm", 0.0) * px_per_mm
        y += getattr(scene, f"tsdraft_{view_key}_{axis}_offset_y_mm", 0.0) * px_per_mm

        visual_w = height if rotated else width
        visual_h = width if rotated else height

        max_x = max(margin_x, float(region.width) - visual_w - margin_x)
        max_y = max(margin_y, float(region.height) - visual_h - margin_y)

        x = min(max(x, margin_x), max_x)
        y = min(max(y, margin_y), max_y)

        placed.append(Placed(text, x, y, width, height, rotated, font_px))

    return placed


def draw(placed, font_id=0):
    """決めた場所へ文字を描く。

    回すものは blf の回転を使う。blf の状態はフォント番号ごとに
    共有なので、戻し忘れると Blender 本体のUIの文字まで傾く。
    描いたら必ず戻す。
    """
    for item in placed:
        blf.size(font_id, item.font_px)

        if not item.rotated:
            blf.position(font_id, item.x, item.y, 0)
            blf.draw(font_id, item.text)
            continue

        try:
            blf.enable(font_id, blf.ROTATION)
            blf.rotation(font_id, math.radians(90.0))
            # blf は指定位置を基準に反時計回りへ回すので、見た目の
            # 左下が x, y に来るよう X を右へずらす。
            blf.position(font_id, item.x + item.height, item.y, 0)
            blf.draw(font_id, item.text)
        finally:
            try:
                blf.rotation(font_id, 0.0)
                blf.disable(font_id, blf.ROTATION)
            except Exception:
                _debug.swallowed("draft.labels.draw")


def current_view_key(scene, rv3d):
    """いま向いている面図の名前と、ビュー方向。

    「任意」を押したときだけ user 個別設定を使う。斜めを向いて
    いるだけの既定のビューは、全体の寸法表示の設定に従う。
    """
    view_key, view_dir = _viewstate.get_view_key_from_rv3d(rv3d)

    explicit = bool(getattr(scene, "tsdraft_user_view_mode", False))
    if explicit:
        view_key = "user"

    return view_key, view_dir, explicit


def label_data():
    """置く対象の寸法。無ければ空。"""
    return bpy.app.driver_namespace.get(_keys.DATA_KEY, []) or []
