"""1つの面図を、実寸のPNGとして書き出す。

画面に映っているものをそのまま撮るのではなく、書き出し用に
視点と表示を整えてから撮り、終わったら元へ戻す。

■ なぜ画面をそのまま撮らないか

実寸で刷るには「画面の1ミリが何ピクセルか」が分かっていないと
いけない。ユーザが自由にズームした状態では決まらないので、
箱が収まる決まった倍率へ合わせてから撮る。

■ 撮ったあとに切り抜く

Blender のビューポート撮影は領域全体を撮るので、余白が入る。
必要な範囲を計算しておき、撮影後に切り抜く。

■ 解像度を PNG に書き込む

pHYs チャンクに 300dpi を入れる。入れないと、印刷側が原寸を
知らず、画像の大きさに合わせて勝手に拡大縮小する。実寸で
刷れることがこの機能の目的なので、ここは外せない。

■ 必ず元へ戻す

視点・表示設定は viewstate.export_view の中でだけ変わり、抜ける
ときに必ず戻る。戻し損ねると、書き出しただけで作業中のビューが
変わってしまう。

■ 段取り

  tsdraft_target_pane       どのペインで撮るか
  tsdraft_aim_pane          箱へ向けて、刷れる見た目に整える
  tsdraft_measure_pane      実寸とピクセルの対応を測る
  tsdraft_clamp_crop_to_pane 切り抜く範囲をペインの中へ収める
  tsdraft_shoot_pane        撮って切り抜く

以前はこれが1つの関数に 737 行あった。途中で何が決まるのかが
追えず、直すときに全部を読む必要があった。
"""

import os

from pathlib import Path
import mathutils
from mathutils import Vector
from bpy_extras import view3d_utils
import bpy

from ... import debug as _pkg_debug
from ...core import units as _units
from ...export import png as _png
from .. import bbox as _bbox
from .. import dimension as _dimension
from .. import keys as _keys
from .. import labels as _labels
from .. import viewstate as _viewstate


def tsdraft_patch_png_dpi(filepath, dpi):
    """撮った PNG へ実寸の解像度を入れ直す。

    Blender に撮らせたファイルには解像度が入っていないので、
    出来上がったものへ後から入れる。書き換えそのものは
    export.png が持つ。以前はここにも同じ処理があり、dpi から
    「メートルあたりのピクセル数」を出す式が2箇所にあった。
    片方だけ直せば、同じ画像が違う大きさで刷られる。
    """
    path = Path(filepath)
    patched = _png.patch_dpi(path.read_bytes(), dpi)

    if patched is None:
        return False

    path.write_bytes(patched)
    return True


def tsdraft_get_last_export_dir():
    path = bpy.app.driver_namespace.get(_keys.LAST_EXPORT_DIR_KEY)

    if path and os.path.isdir(path):
        return path

    # Fall back to current blend directory if available.
    blend_path = bpy.data.filepath
    if blend_path:
        folder = os.path.dirname(blend_path)
        if folder and os.path.isdir(folder):
            return folder

    return ""


def tsdraft_remember_export_dir(path):
    if not path:
        return

    folder = path if os.path.isdir(path) else os.path.dirname(path)

    if folder:
        try:
            folder = os.path.abspath(folder)
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_remember_export_dir")

        bpy.app.driver_namespace[_keys.LAST_EXPORT_DIR_KEY] = folder


def tsdraft_store_export_view_context(context):
    """Remember the actual 3D editor before Blender opens the file browser."""
    if context.area is None or context.area.type != 'VIEW_3D':
        return

    region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return

    bpy.app.driver_namespace[_keys.EXPORT_CONTEXT_KEY] = {
        "window": context.window,
        "screen": context.screen,
        "area": context.area,
        "region": region,
    }


def tsdraft_get_export_view_context(context):
    """
    Return the VIEW_3D context saved at invoke-time.
    Falls back to scanning open Blender windows if needed.
    """
    saved = bpy.app.driver_namespace.get(_keys.EXPORT_CONTEXT_KEY)

    if saved:
        try:
            window = saved.get("window")
            screen = saved.get("screen")
            area = saved.get("area")

            if (
                window is not None
                and screen is not None
                and area is not None
                and area.type == 'VIEW_3D'
                and area in screen.areas
            ):
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region is not None:
                    return window, screen, area, region
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_get_export_view_context")

    # If execute() runs in File Browser context, search every open window.
    try:
        for window in context.window_manager.windows:
            screen = window.screen
            if screen is None:
                continue

            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue

                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region is not None:
                    return window, screen, area, region
    except Exception:
        _pkg_debug.swallowed("draft.tsdraft_get_export_view_context")

    return None


def tsdraft_find_view_region(area, wanted_key):
    """
    Return (region, rv3d) for the requested orthographic pane.
    In Quad View, pair WINDOW regions with region_quadviews and select
    the pane whose actual rotation is top/front/side.
    """
    space = area.spaces.active
    window_regions = [r for r in area.regions if r.type == 'WINDOW']

    try:
        quadviews = list(space.region_quadviews)
    except Exception:
        quadviews = []

    # Quad View: Blender exposes 4 RegionView3D states.
    if quadviews and len(window_regions) >= len(quadviews):
        # Pair by WINDOW-region order. This matches Blender's quad layout.
        pairs = list(zip(window_regions[:len(quadviews)], quadviews))

        for region, rv in pairs:
            key, _ = _viewstate.get_view_key_from_rv3d(rv)
            if key == wanted_key:
                return region, rv

    # Single view fallback.
    rv = space.region_3d
    if rv is not None:
        key, _ = _viewstate.get_view_key_from_rv3d(rv)
        if key == wanted_key:
            region = next((r for r in window_regions if r.type == 'WINDOW'), None)
            if region is not None:
                return region, rv

    return None, None


def tsdraft_frame_export_region(context, window, screen, area, region, rv3d, source_obj, bbox_obj, padding_ratio=0.58):
    """
    Export専用の正規化フレーミング。
    現在のユーザー拡大率が極端でも、まずBlender標準のView Selectedで
    正常な倍率へ戻し、その後BBox基準で最終フィットする。
    """
    if (
        window is None or screen is None or area is None or region is None
        or rv3d is None or source_obj is None or bbox_obj is None
    ):
        return False

    view_layer = context.view_layer
    active_before = view_layer.objects.active
    selected_before = [obj for obj in view_layer.objects if obj.select_get()]

    try:
        for obj in selected_before:
            try:
                obj.select_set(False)
            except Exception:
                _pkg_debug.swallowed("draft.tsdraft_frame_export_region")

        try:
            source_obj.select_set(True)
            view_layer.objects.active = source_obj
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_frame_export_region")

        override = {
            "window": window,
            "screen": screen,
            "area": area,
            "region": region,
            "space_data": area.spaces.active,
            "region_data": rv3d,
        }

        try:
            with context.temp_override(**override):
                bpy.ops.view3d.view_selected(use_all_regions=False)
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_frame_export_region")

        try:
            corners_world = [
                bbox_obj.matrix_world @ Vector(corner)
                for corner in bbox_obj.bound_box
            ]
            if corners_world:
                rv3d.view_location = (
                    sum(corners_world, Vector()) / len(corners_world)
                )
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_frame_export_region")

        tsdraft_force_view_redraw(context, area)
        tsdraft_force_view_redraw(context, area)

        return tsdraft_fit_region_to_bbox(
            context,
            area,
            region,
            rv3d,
            bbox_obj,
            padding_ratio=padding_ratio
        )

    finally:
        try:
            source_obj.select_set(False)
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_frame_export_region")

        for obj in selected_before:
            try:
                obj.select_set(True)
            except Exception:
                _pkg_debug.swallowed("draft.tsdraft_frame_export_region")

        try:
            view_layer.objects.active = active_before
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_frame_export_region")


def tsdraft_export_bbox_fill_ratio(region, rv3d, bbox_obj, padding_ratio):
    """BBoxが指定した書き出し占有率にどれくらい近いかを返す。"""
    try:
        projected = []
        for corner in bbox_obj.bound_box:
            world_co = bbox_obj.matrix_world @ Vector(corner)
            p2 = view3d_utils.location_3d_to_region_2d(
                region, rv3d, world_co
            )
            if p2 is not None:
                projected.append(p2)

        if len(projected) < 4:
            return None

        xs = [float(p.x) for p in projected]
        ys = [float(p.y) for p in projected]
        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)

        usable_w = max(1.0, float(region.width) * padding_ratio)
        usable_h = max(1.0, float(region.height) * padding_ratio)

        return max(
            bbox_w / usable_w,
            bbox_h / usable_h
        )
    except Exception:
        return None


def tsdraft_fit_region_to_bbox(context, area, region, rv3d, bbox_obj, padding_ratio=0.52):
    """
    Normalize an orthographic pane to the Bounding Box at a predictable size.

    Important:
    Older versions only zoomed OUT when the BBox was too large.
    If the user happened to be zoomed far out, the export inherited that tiny
    on-screen size and produced a soft / clipped sheet.

    This version actively fits in BOTH directions:
      - BBox too large  -> zoom out
      - BBox too small  -> zoom in

    Therefore export scale is independent from the user's current viewport zoom.
    """
    if region is None or rv3d is None or bbox_obj is None:
        return False

    try:
        corners_world = [
            bbox_obj.matrix_world @ Vector(corner)
            for corner in bbox_obj.bound_box
        ]
    except Exception:
        return False

    if not corners_world:
        return False

    center = sum(corners_world, Vector()) / len(corners_world)
    rv3d.view_location = center
    changed = False

    # Aim slightly inside the requested usable rectangle.
    # This avoids one-pixel edge clipping while keeping the source dense.
    target_fill = 0.96

    for _ in range(10):
        try:
            area.tag_redraw()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_fit_region_to_bbox")

        projected = []
        for world_co in corners_world:
            p = view3d_utils.location_3d_to_region_2d(
                region, rv3d, world_co
            )
            if p is not None:
                projected.append(p)

        if len(projected) < 4:
            break

        xs = [p.x for p in projected]
        ys = [p.y for p in projected]

        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)

        usable_w = max(1.0, float(region.width) * padding_ratio)
        usable_h = max(1.0, float(region.height) * padding_ratio)

        current_fill = max(
            bbox_w / usable_w,
            bbox_h / usable_h
        )

        if current_fill <= 1e-9:
            break

        # In ortho view, projected size is approximately inverse to view_distance.
        # scale < 1 => zoom IN, scale > 1 => zoom OUT.
        distance_scale = current_fill / target_fill

        if abs(current_fill - target_fill) <= 0.003:
            break

        # Protect against wild one-frame jumps, while still converging from
        # extremely zoomed-in / zoomed-out user views.
        distance_scale = min(20.0, max(0.05, distance_scale))
        rv3d.view_distance = max(
            1e-9,
            float(rv3d.view_distance) * distance_scale
        )
        changed = True

    try:
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=5)
    except Exception:
        _pkg_debug.swallowed("draft.tsdraft_fit_region_to_bbox")

    return changed


def tsdraft_force_view_redraw(context, area):
    """Force Blender to finish drawing the newly switched view before screenshot."""
    try:
        area.tag_redraw()
    except Exception:
        _pkg_debug.swallowed("draft.tsdraft_force_view_redraw")

    try:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=10)
    except Exception:
        # Some contexts dislike redraw_timer; area.tag_redraw still helps.
        pass


def tsdraft_crop_png_with_blender(src_path, dst_path, x0, y0, width, height):
    """
    Crop a PNG using Blender's own Image API.
    Coordinates are bottom-left based, matching Blender image pixels.
    """
    img = bpy.data.images.load(src_path, check_existing=False)
    try:
        src_w, src_h = img.size
        x0 = max(0, min(int(x0), src_w - 1))
        y0 = max(0, min(int(y0), src_h - 1))
        width = max(1, min(int(width), src_w - x0))
        height = max(1, min(int(height), src_h - y0))

        src_pixels = list(img.pixels[:])
        dst_pixels = [0.0] * (width * height * 4)

        for row in range(height):
            src_start = ((y0 + row) * src_w + x0) * 4
            src_end = src_start + width * 4
            dst_start = row * width * 4
            dst_pixels[dst_start:dst_start + width * 4] = src_pixels[src_start:src_end]

        out = bpy.data.images.new(
            name="TSDRAFT_Printable_Crop",
            width=width,
            height=height,
            alpha=False
        )
        try:
            out.pixels[:] = dst_pixels
            out.filepath_raw = dst_path
            out.file_format = 'PNG'
            out.save()
        finally:
            bpy.data.images.remove(out)
    finally:
        bpy.data.images.remove(img)


# 面図ごとに、単一ビューで向ける Blender の視点。
VIEW_AXIS = {
    "top": "TOP",
    "front": "FRONT",
    "side": "RIGHT",
}


def tsdraft_target_pane(context, window, screen, area, space, view_key):
    """撮るペインを決める。(リージョン, 視点) を返す。

    四分割なら、その面図のペインをそのまま使う。単一ビューなら、
    メインのビューをその向きへ変える（向きは export_view の中で
    元へ戻る）。

    四分割のときに一度描き直してから取り直しているのは、切り替えた
    直後はリージョンの大きさが古いままのことがあるため。古い大きさで
    切り抜き位置を計算すると、画像がずれる。
    """
    region, rv3d = tsdraft_find_view_region(area, view_key)

    if region is not None:
        tsdraft_force_view_redraw(context, area)
        fresh_region, fresh_rv3d = tsdraft_find_view_region(area, view_key)
        if fresh_region is not None and fresh_rv3d is not None:
            return fresh_region, fresh_rv3d
        return region, rv3d

    # 単一ビュー、または想定外の配置。メインのビューを使う。
    main_region = next(
        (r for r in area.regions if r.type == 'WINDOW'), None
    )
    if main_region is None:
        raise RuntimeError("3DビューのWINDOW領域が見つかりませんでした")

    rv3d = space.region_3d
    if rv3d is None:
        raise RuntimeError("3Dビューの情報を取得できませんでした")

    if view_key == "user":
        # 任意ビューは、いま向いている方向をそのまま使う。
        return main_region, rv3d

    with context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=main_region,
        space_data=space,
        region_data=rv3d,
    ):
        bpy.ops.view3d.view_axis(
            type=VIEW_AXIS.get(view_key, "FRONT"),
            align_active=False,
        )

    tsdraft_force_view_redraw(context, area)
    return main_region, rv3d


# 箱がペインをどれだけ占めていれば良しとするか。ここへ収まるまで
# 最大3回まで合わせ直す。狭すぎると図が小さくなり、広すぎると
# 端が切れる。
FILL_MIN = 0.90
FILL_MAX = 1.03
FILL_TRIES = 3


def tsdraft_aim_pane(context, window, screen, area, space,
                     region, rv3d, source_obj, bbox_obj, world_corners,
                     scene, is_user_view=False,
                     common_view_distance=None, fit_padding_ratio=0.58):
    """ペインを箱へ向けて、刷れる見た目に整える。

    やることは4つ。

      共通の倍率を合わせる（三面で縮尺を揃えるため）
      箱の中心へ寄せて、平行投影にする
      背景・グリッドなどを刷れる見た目へ変える
      箱がペインを占める割合を確かめ、外れていれば合わせ直す

    最後の確認をするのは、利用者が極端にズームした状態から書き出す
    ことがあるため。1回合わせただけでは、リージョンの大きさが
    描き直しの後で変わって外れることがある。
    """
    if common_view_distance is not None and not is_user_view:
        rv3d.view_distance = common_view_distance

    if not is_user_view:
        rv3d.view_location = (
            sum(world_corners, mathutils.Vector()) / len(world_corners)
        )
        rv3d.view_perspective = 'ORTHO'

        # 利用者が図が切れるほどズームしていても、撮る前に必ず
        # 箱をペインの中へ戻す。
        try:
            tsdraft_frame_export_region(
                context, window, screen, area, region, rv3d,
                source_obj, bbox_obj, padding_ratio=fit_padding_ratio,
            )
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_aim_pane")

    # 刷れる見た目へ。軸・原点・3Dカーソルは configure_drawing_view が
    # 常に消す。背景は白 / グリッド / 黒 / カスタムから選ぶ。
    _viewstate.configure_drawing_view(
        space,
        getattr(scene, "tsdraft_export_background", 'WHITE'),
        getattr(scene, "tsdraft_export_background_color", (1.0, 1.0, 1.0)),
    )
    scene.tsdraft_drawing_mode = True

    tsdraft_force_view_redraw(context, area)

    if is_user_view:
        return

    for _try in range(FILL_TRIES):
        fill = tsdraft_export_bbox_fill_ratio(
            region, rv3d, bbox_obj, fit_padding_ratio
        )
        if fill is not None and FILL_MIN <= fill <= FILL_MAX:
            return

        try:
            tsdraft_frame_export_region(
                context, window, screen, area, region, rv3d,
                source_obj, bbox_obj, padding_ratio=fit_padding_ratio,
            )
        except Exception:
            return

        tsdraft_force_view_redraw(context, area)


def tsdraft_shoot_pane(context, window, screen, area, space,
                       region, temp_path, out_path, crop_box,
                       extra_redraw=False):
    """エリア全体を1枚撮り、ペインの必要な範囲だけを切り出す。

    crop_box は (min_x, min_y, max_x, max_y)。ペインの中の座標。

    Nパネルやツールバーの表示はここで切らない。切るとリージョンの
    大きさが変わり、計算し終えた切り抜き位置がずれる。実際それで
    UIが写り込んでいた。WINDOW リージョンの中だけを切り出せば、
    そもそも入らない。

    ギズモだけは消す。これは画の上に重なるもので、リージョンの
    大きさを変えない。撮ったら必ず戻す。
    """
    min_x, min_y, max_x, max_y = crop_box

    old_gizmo = getattr(space, "show_gizmo", None)

    try:
        if hasattr(space, "show_gizmo"):
            space.show_gizmo = False

        # 倍率も文字の抑止も、画面へ届いてから撮る。
        tsdraft_force_view_redraw(context, area)
        tsdraft_force_view_redraw(context, area)
        if extra_redraw:
            tsdraft_force_view_redraw(context, area)

        with context.temp_override(window=window, screen=screen, area=area):
            bpy.ops.screen.screenshot_area(
                filepath=temp_path, hide_props_region=False
            )
    finally:
        if old_gizmo is not None and hasattr(space, "show_gizmo"):
            space.show_gizmo = old_gizmo
        tsdraft_force_view_redraw(context, area)

    if not Path(temp_path).exists():
        raise RuntimeError("画面のスクリーンショットを書き出せませんでした")

    # エリアの座標へ直してから切る。
    tsdraft_crop_png_with_blender(
        temp_path,
        out_path,
        round(region.x - area.x + min_x),
        round(region.y - area.y + min_y),
        round(max_x - min_x),
        round(max_y - min_y),
    )


class PaneMeasure:
    """撮るペインの中で、箱がどこに何ピクセルで写っているか。

    切り抜きの範囲も、PNG へ書き込む解像度も、ここから決まる。
    実寸で刷れるかどうかはこの値にかかっているので、撮る処理から
    切り離して、値だけを見られるようにしてある。
    """

    __slots__ = (
        "min_x", "min_y", "max_x", "max_y",
        "px_w", "px_h", "mm_w", "mm_h",
        "px_per_mm", "dpi",
    )

    def __init__(self, min_x, min_y, max_x, max_y,
                 mm_w, mm_h, px_per_mm, dpi):
        self.min_x = float(min_x)
        self.min_y = float(min_y)
        self.max_x = float(max_x)
        self.max_y = float(max_y)
        self.px_w = self.max_x - self.min_x
        self.px_h = self.max_y - self.min_y
        self.mm_w = float(mm_w)
        self.mm_h = float(mm_h)
        self.px_per_mm = float(px_per_mm)
        self.dpi = float(dpi)


# 面図ごとに、画面の横・縦へ対応する世界の軸。
PANE_AXES = {
    "top": ("x", "y"),
    "front": ("x", "z"),
    "side": ("y", "z"),
}


def tsdraft_measure_pane(region, rv3d, world_corners, view_key,
                         unit_scale, is_user_view=False):
    """箱をペインへ投影して、実寸とピクセルの対応を測る。

    任意ビューは実寸を保証しない、ただの画面撮影なので、
    ミリの対応は持たせない。
    """
    projected = []
    for co in world_corners:
        flat = view3d_utils.location_3d_to_region_2d(region, rv3d, co)
        if flat is not None:
            projected.append((float(flat.x), float(flat.y)))

    if len(projected) < 4:
        raise RuntimeError(
            f"{_dimension.tsdraft_svg_view_label(view_key)}"
            "のBounding Boxを投影できませんでした"
        )

    xs = [p[0] for p in projected]
    ys = [p[1] for p in projected]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    if is_user_view:
        return PaneMeasure(min_x, min_y, max_x, max_y, 0.0, 0.0, 1.0, 96.0)

    axis_u, axis_v = PANE_AXES.get(view_key, ("y", "z"))
    vals_u = [getattr(co, axis_u) for co in world_corners]
    vals_v = [getattr(co, axis_v) for co in world_corners]

    mm_w = (max(vals_u) - min(vals_u)) * unit_scale * 1000.0
    mm_h = (max(vals_v) - min(vals_v)) * unit_scale * 1000.0

    px_w = max_x - min_x
    px_h = max_y - min_y

    if px_w <= 1 or px_h <= 1 or mm_w <= 0 or mm_h <= 0:
        raise RuntimeError(
            f"{_dimension.tsdraft_svg_view_label(view_key)}"
            "の実寸対応を取得できませんでした"
        )

    px_per_mm = (px_w / mm_w + px_h / mm_h) * 0.5
    return PaneMeasure(
        min_x, min_y, max_x, max_y,
        mm_w, mm_h, px_per_mm, px_per_mm * 25.4,
    )


def tsdraft_clamp_crop_to_pane(area, target_region, box, font_size,
                               fixed_top=False, whole_pane=False):
    """切り抜きの範囲を、3Dビューの描画部分の中へ収める。

    box は (min_x, min_y, max_x, max_y)。戻り値も同じ形。

    Nパネルや左ツールバーが出ていると、撮った画像にその帯が写る。
    表示を切ってから撮る手もあるが、切るとリージョンの大きさが
    変わり、計算し終えた切り抜き位置がずれる。実際それでUIが
    写り込んでいた。触らずに、画像側で避ける。

    上端はエリアのUI帯を拾いやすいので、明示的に厚めに取る。

    whole_pane が真なら、図面としてではなくペインそのものを普通の
    画像として保存する（任意ビュー）。左上にBlenderのUIの端が
    写り込むことがあるので、少し内側で切る。
    """
    min_x, min_y, max_x, max_y = box

    safe_left = 14.0
    safe_right = 14.0
    safe_bottom = 18.0
    safe_top = 30.0 if fixed_top else max(30.0, float(font_size) * 0.95)

    target_x0 = float(target_region.x)
    target_x1 = float(target_region.x + target_region.width)

    for ui_region in area.regions:
        if ui_region == target_region:
            continue

        rx0 = float(ui_region.x)
        rx1 = float(ui_region.x + ui_region.width)
        if not (rx0 < target_x1 and rx1 > target_x0):
            continue

        if ui_region.type == 'UI':
            safe_right = max(safe_right, max(0.0, target_x1 - rx0) + 6.0)
        elif ui_region.type == 'TOOLS':
            safe_left = max(safe_left, max(0.0, rx1 - target_x0) + 6.0)

    if whole_pane:
        return (
            max(safe_left, 34.0),
            max(safe_bottom, 20.0),
            float(target_region.width) - max(safe_right, 18.0),
            float(target_region.height) - max(safe_top, 52.0),
        )

    return (
        max(safe_left, min_x),
        max(safe_bottom, min_y),
        min(float(target_region.width) - safe_right, max_x),
        min(float(target_region.height) - safe_top, max_y),
    )


def tsdraft_export_viewport_exact_png(context, filepath, view_key, common_view_distance=None, fit_padding_ratio=0.58, suppress_dimension_text=False):
    """1つの面図を、実寸のPNGとして書き出す。

    段取りは5つ。

      1. 撮るペインを決める    tsdraft_target_pane
      2. 箱へ向けて見た目を整える  tsdraft_aim_pane
      3. 実寸とピクセルの対応を測る tsdraft_measure_pane
      4. 切り抜く範囲を決める   draft.labels + clamp_crop_to_pane
      5. 撮って切り抜く      tsdraft_shoot_pane

    四分割にその面図のペインがあれば、そのまま使う。無いときだけ
    メインのビューを向け直す。向きも表示も export_view の中でだけ
    変わり、抜けるときに必ず戻る。

    戻り値は、この画像の実寸と、箱が画像のどこにあるかの一覧。
    三面図シートを組むときに使う。
    """
    view_ctx = tsdraft_get_export_view_context(context)
    if view_ctx is None:
        raise RuntimeError("書き出し元の3Dビューが見つかりませんでした")

    export_window, export_screen, area, _ = view_ctx
    space = area.spaces.active

    source_obj = _bbox.tsdraft_resolve_source_object(context)
    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)

    if source_obj is None:
        raise RuntimeError("元オブジェクトを選択してください")
    if bbox_obj is None:
        raise RuntimeError("先にBOX＋寸法を作成してください")

    scene = context.scene
    # 1 BU が何メートルか。シーンの Unit Scale を直に読んではいけない。
    # このアドオンには「シーンを見ず、アドオンの中だけで基準を決める」
    # モードがある。直読みすると、型紙側が 1 BU = 40mm で計算して
    # いるのに、こちらは 1000mm で測る、ということが起きる。
    # 実際にそうなっていた（同じファイルで 25 倍の食い違い）。
    unit_scale = _units.scene_scale_to_meters(scene)
    is_user_view = (view_key == "user")
    hard_suppress_dimension_text = bool(suppress_dimension_text)

    temp_full = str(Path(filepath).with_name(Path(filepath).stem + "_TEMP_AREA.png"))

    # 視点も表示もこの中でだけ変わり、抜けるときに必ず戻る。
    # 控えと戻しが同じ場所にあるので、片方にだけ項目を足して
    # 黙って戻らなくなる、ということが起きない。
    try:
        with _viewstate.export_view(
            space, scene,
            axis_view=not is_user_view,
            suppress_text=hard_suppress_dimension_text,
        ):
            # Nパネル/ツールバーは実際には触らない。
            # 画像側のクロップでUI領域を除外するので、撮影後も表示状態が変わらない。
            tsdraft_force_view_redraw(context, area)

            # 撮るペインを決める。四分割ならそのペイン、単一ビュー
            # ならメインのビューをその向きへ変える。
            target_region, target_rv3d = tsdraft_target_pane(
                context, export_window, export_screen, area, space, view_key
            )

            # ペインを箱へ向けて、刷れる見た目に整える。
            world_corners = [
                bbox_obj.matrix_world @ v.co
                for v in bbox_obj.data.vertices
            ]
            tsdraft_aim_pane(
                context, export_window, export_screen, area, space,
                target_region, target_rv3d,
                source_obj, bbox_obj, world_corners, scene,
                is_user_view=is_user_view,
                common_view_distance=common_view_distance,
                fit_padding_ratio=fit_padding_ratio,
            )

            # 箱をペインへ投影して、実寸とピクセルの対応を測る。
            measured = tsdraft_measure_pane(
                target_region, target_rv3d, world_corners,
                view_key, unit_scale, is_user_view,
            )

            # 切り抜きの範囲。箱に、寸法の文字のぶんを足していく。
            crop_min_x = measured.min_x
            crop_max_x = measured.max_x
            crop_min_y = measured.min_y
            crop_max_y = measured.max_y

            # 切り抜きの範囲は、寸法の文字まで含める。文字が入る場所は
            # draft.labels が決める。画面へ描く側と同じ計算なので、
            # 画面で見えている文字が PNG で切れることはない。
            #
            # 以前はここに overlay の計算を手で写していた。写した時点で
            # すでにずれていて（安全余白の出し方と、出す軸の選び方）、
            # ずれると刷ってみるまで分からない。
            suppress_sheet_dim_text = _labels.text_suppressed()

            if is_user_view or suppress_sheet_dim_text:
                data = []
            else:
                data = _labels.label_data()

            _, view_dir = _viewstate.get_view_key_from_rv3d(target_rv3d)

            for item in _labels.layout(
                scene, target_region, target_rv3d, bbox_obj, data,
                view_key, view_dir,
            ):
                left, bottom, right, top = item.bounds()
                crop_min_x = min(crop_min_x, left)
                crop_max_x = max(crop_max_x, right)
                crop_min_y = min(crop_min_y, bottom)
                crop_max_y = max(crop_max_y, top)

            # Small breathing room around the real rendered bounds.
            # If dimension text is suppressed (sheet / batch source capture),
            # font size must not affect crop size or apparent object magnification.
            if suppress_sheet_dim_text or hard_suppress_dimension_text:
                # 三面図シート / 3面まとめての元画像は、文字サイズと完全分離した固定余白。
                # BBoxぎりぎりで切らず、モデル本体も確実に残す。
                pad_px = 72.0
            else:
                pad_px = max(56.0, scene.tsdraft_font_size * 1.45)
            crop_min_x -= pad_px
            crop_max_x += pad_px
            crop_min_y -= pad_px
            crop_max_y += pad_px

            # 切り抜きは3Dビューの描画部分の中へ収める。Nパネルや
            # ツールバーが出ていても、画像には入れない。
            (
                crop_min_x, crop_min_y, crop_max_x, crop_max_y,
            ) = tsdraft_clamp_crop_to_pane(
                area,
                target_region,
                (crop_min_x, crop_min_y, crop_max_x, crop_max_y),
                scene.tsdraft_font_size,
                fixed_top=(suppress_sheet_dim_text
                           or hard_suppress_dimension_text),
                whole_pane=is_user_view,
            )

            if crop_max_x <= crop_min_x or crop_max_y <= crop_min_y:
                raise RuntimeError("書き出し範囲を計算できませんでした")

            # エリアを1枚撮って、ペインの必要な範囲だけ切り出す。
            tsdraft_shoot_pane(
                context, export_window, export_screen, area, space,
                target_region, temp_full, filepath,
                (crop_min_x, crop_min_y, crop_max_x, crop_max_y),
                extra_redraw=hard_suppress_dimension_text,
            )

            crop_w = crop_max_x - crop_min_x
            crop_h = crop_max_y - crop_min_y

            if not Path(filepath).exists():
                raise RuntimeError("切り抜いたPNGを書き出せませんでした")

            if not tsdraft_patch_png_dpi(filepath, measured.dpi):
                raise RuntimeError("PNGへ実寸のdpi情報を書き込めませんでした")

            # 箱が画像のどこにあるか。四方の余白をピクセルで返す。
            # 三面図シートは、これを使って三面の位置を揃える。
            return {
                "bbox_width_mm": measured.mm_w,
                "bbox_height_mm": measured.mm_h,
                "bbox_px_w": measured.px_w,
                "bbox_px_h": measured.px_h,
                "dpi": measured.dpi,
                "image_w_px": round(crop_w),
                "image_h_px": round(crop_h),
                "bbox_left_px": float(measured.min_x - crop_min_x),
                "bbox_right_px": float(crop_max_x - measured.max_x),
                "bbox_bottom_px": float(measured.min_y - crop_min_y),
                "bbox_top_px": float(crop_max_y - measured.max_y),
                "view_distance": target_rv3d.view_distance,
            }

    finally:
        # ここでやるのは後始末だけ。表示と視点は export_view が戻す。
        try:
            if Path(temp_full).exists():
                Path(temp_full).unlink()
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        try:
            area.tag_redraw()
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
        except Exception:
            _bbox.redraw_viewports()
