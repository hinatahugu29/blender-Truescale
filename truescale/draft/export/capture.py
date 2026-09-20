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

視点・表示設定・選択状態は、撮る前に控えて finally で戻す。
戻し損ねると、書き出しただけで作業中のビューが変わる。
"""

import os

import struct
import zlib
from pathlib import Path
import blf
import mathutils
from mathutils import Vector
from bpy_extras import view3d_utils
import bpy

from ... import debug as _pkg_debug
from .. import bbox as _bbox
from .. import dimension as _dimension
from .. import keys as _keys
from .. import viewstate as _viewstate


def tsdraft_patch_png_dpi(filepath, dpi):
    """Insert/replace PNG pHYs chunk so Illustrator reads the intended physical size."""
    path = Path(filepath)
    data = path.read_bytes()

    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False

    ppm = int(round(float(dpi) / 0.0254))  # pixels per meter

    out = bytearray(data[:8])
    pos = 8
    inserted = False

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            break

        chunk = data[pos:end]

        # Drop old pHYs and replace it once.
        if ctype == b"pHYs":
            if not inserted:
                payload = struct.pack(">IIB", ppm, ppm, 1)
                crc = zlib.crc32(b"pHYs" + payload) & 0xffffffff
                out += struct.pack(">I", len(payload)) + b"pHYs" + payload + struct.pack(">I", crc)
                inserted = True
        else:
            out += chunk

            # Put pHYs immediately after IHDR when absent.
            if ctype == b"IHDR" and not inserted:
                payload = struct.pack(">IIB", ppm, ppm, 1)
                crc = zlib.crc32(b"pHYs" + payload) & 0xffffffff
                out += struct.pack(">I", len(payload)) + b"pHYs" + payload + struct.pack(">I", crc)
                inserted = True

        pos = end

    path.write_bytes(out)
    return inserted


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


def tsdraft_export_viewport_exact_png(context, filepath, view_key, common_view_distance=None, fit_padding_ratio=0.58, suppress_dimension_text=False):
    """
    Export the requested view by directly using its matching Quad View pane.
    No fake view switching when Quad View already contains top/front/side.
    """
    view_ctx = tsdraft_get_export_view_context(context)
    if view_ctx is None:
        raise RuntimeError("書き出し元の3Dビューが見つからんかったンゴ")

    export_window, export_screen, area, _ = view_ctx
    space = area.spaces.active

    # 書き出しは一時的にビュー方向・背景・オーバーレイを変更するため、
    # 開始時の状態を丸ごと退避して最後に必ず戻す。
    original_display_state = _viewstate.tsdraft_capture_export_display_state(
        space,
        context.scene
    )

    # 寸法表示は作業中のON/OFFとは別扱い。
    # 三面図PNGでは事故防止のため必ず表示して撮影し、
    # 最後に元の状態へ戻す。
    original_dimension_state = {
        "all": bool(getattr(context.scene, "tsdraft_show_dimensions", True)),
        "top": bool(getattr(context.scene, "tsdraft_show_dimensions_top", True)),
        "front": bool(getattr(context.scene, "tsdraft_show_dimensions_front", True)),
        "side": bool(getattr(context.scene, "tsdraft_show_dimensions_side", True)),
        "user": bool(getattr(context.scene, "tsdraft_show_dimensions_user", True)),
    }

    # 三面図シートの一時キャプチャでは、寸法文字を最終シート側で描く。
    # Scene側もOFFにして、描画タイミングのズレによる二重焼き込みを防ぐ。
    hard_suppress_dimension_text = bool(suppress_dimension_text)

    original_user_view_mode = bool(
        getattr(context.scene, "tsdraft_user_view_mode", False)
    )

    original_bbox_state = {
        "all": bool(getattr(context.scene, "tsdraft_show_bbox", True)),
        "top": bool(getattr(context.scene, "tsdraft_show_bbox_top", True)),
        "front": bool(getattr(context.scene, "tsdraft_show_bbox_front", True)),
        "side": bool(getattr(context.scene, "tsdraft_show_bbox_side", True)),
        "user": bool(getattr(context.scene, "tsdraft_show_bbox_user", True)),
    }

    original_main_rv3d_state = _viewstate.tsdraft_capture_rv3d_state(
        getattr(space, "region_3d", None)
    )

    original_quad_states = []
    try:
        for rv in list(space.region_quadviews):
            original_quad_states.append(
                (rv, _viewstate.tsdraft_capture_rv3d_state(rv))
            )
    except Exception:
        _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

    source_obj = _bbox.tsdraft_resolve_source_object(context)
    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)

    if source_obj is None:
        raise RuntimeError("元オブジェクトを選択してクレメンス")
    if bbox_obj is None:
        raise RuntimeError("先にBOX＋寸法を作成してクレメンス")

    scene = context.scene
    unit_scale = scene.unit_settings.scale_length or 1.0
    is_user_view = (view_key == "user")

    # 三面図はグローバル/ビュー単位の寸法表示を撮影中だけ有効化。
    # 各ビューの軸別ON/OFFはユーザー指定を尊重する。
    # 任意ビューはイメージ用途があるため現在設定を尊重。
    if not is_user_view:
        scene.tsdraft_user_view_mode = False

        # 三面図はクイック非表示中でも、書き出しだけは
        # BOX＋寸法を必ず有効化して事故を防ぐ。
        scene.tsdraft_show_bbox = True
        scene.tsdraft_show_bbox_top = True
        scene.tsdraft_show_bbox_front = True
        scene.tsdraft_show_bbox_side = True

        scene.tsdraft_show_dimensions = True
        scene.tsdraft_show_dimensions_top = True
        scene.tsdraft_show_dimensions_front = True
        scene.tsdraft_show_dimensions_side = True

        if hard_suppress_dimension_text:
            scene.tsdraft_show_dimensions = False

    temp_full = str(Path(filepath).with_name(Path(filepath).stem + "_TEMP_AREA.png"))

    try:
        # Nパネル/ツールバーは実際には触らない。
        # 画像側のクロップでUI領域を除外するので、撮影後も表示状態が変わらない。
        tsdraft_force_view_redraw(context, area)

        # -------------------------------------------------
        # Quad View: directly grab the requested pane.
        # Single View: switch that one view to the requested axis.
        # -------------------------------------------------
        target_region, target_rv3d = tsdraft_find_view_region(area, view_key)

        # Quad Viewの最新リージョン情報を使う。
        if target_region is not None:
            tsdraft_force_view_redraw(context, area)
            refreshed_region, refreshed_rv3d = tsdraft_find_view_region(area, view_key)
            if refreshed_region is not None and refreshed_rv3d is not None:
                target_region, target_rv3d = refreshed_region, refreshed_rv3d

        if target_region is None or target_rv3d is None:
            # Single-view or unusual layout fallback: switch the main view.
            main_region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if main_region is None:
                raise RuntimeError("3DビューのWINDOW領域が見つからんかったンゴ")

            target_rv3d = space.region_3d
            if target_rv3d is None:
                raise RuntimeError("3Dビュー情報が取れんかったンゴ")

            override = {
                "window": export_window,
                "screen": export_screen,
                "area": area,
                "region": main_region,
                "space_data": space,
                "region_data": target_rv3d,
            }

            if view_key == "user":
                # 任意ビューは現在の向きをそのまま使う。
                target_region = main_region
                target_rv3d = space.region_3d
            else:
                axis_map = {
                    "top": "TOP",
                    "front": "FRONT",
                    "side": "RIGHT",
                }

                with context.temp_override(**override):
                    bpy.ops.view3d.view_axis(
                        type=axis_map.get(view_key, "FRONT"),
                        align_active=False
                    )

                tsdraft_force_view_redraw(context, area)
                target_region = main_region

        # Keep common orthographic zoom if supplied.
        if common_view_distance is not None and not is_user_view:
            target_rv3d.view_distance = common_view_distance

        # Center the requested pane on the Bounding Box.
        world_corners = [bbox_obj.matrix_world @ v.co for v in bbox_obj.data.vertices]

        if not is_user_view:
            center = sum(world_corners, mathutils.Vector()) / len(world_corners)
            target_rv3d.view_location = center
            target_rv3d.view_perspective = 'ORTHO'

            # Export safety net:
            # even if the user managed to zoom until the object is clipped,
            # force the bbox back inside this pane before screenshot.
            try:
                tsdraft_frame_export_region(
                    context,
                    export_window,
                    export_screen,
                    area,
                    target_region,
                    target_rv3d,
                    source_obj,
                    bbox_obj,
                    padding_ratio=fit_padding_ratio
                )
            except Exception:
                _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # Printable styling.
        # 書き出し背景は白 / グリッド / 黒 / カスタムから選択。
        # 軸・原点・3Dカーソルはconfigure_drawing_view側で常に非表示。
        export_background = getattr(scene, "tsdraft_export_background", 'WHITE')
        export_custom_color = getattr(
            scene,
            "tsdraft_export_background_color",
            (1.0, 1.0, 1.0)
        )
        _viewstate.configure_drawing_view(
            space,
            export_background,
            export_custom_color
        )
        scene.tsdraft_drawing_mode = True

        tsdraft_force_view_redraw(context, area)

        # 最終スクショ直前でも倍率を検証。
        # 初期画面が極端なズーム状態でも、ここで必ず一定のBBox占有率へ戻す。
        if not is_user_view:
            for _verify in range(3):
                fill = tsdraft_export_bbox_fill_ratio(
                    target_region,
                    target_rv3d,
                    bbox_obj,
                    fit_padding_ratio
                )

                if fill is not None and 0.90 <= fill <= 1.03:
                    break

                try:
                    tsdraft_frame_export_region(
                        context,
                        export_window,
                        export_screen,
                        area,
                        target_region,
                        target_rv3d,
                        source_obj,
                        bbox_obj,
                        padding_ratio=fit_padding_ratio
                    )
                except Exception:
                    break

                tsdraft_force_view_redraw(context, area)

        # -------------------------------------------------
        # Project BBox into the target pane only.
        # -------------------------------------------------
        projected = []
        for co in world_corners:
            p2 = view3d_utils.location_3d_to_region_2d(
                target_region,
                target_rv3d,
                co
            )
            if p2 is not None:
                projected.append((float(p2.x), float(p2.y)))

        if len(projected) < 4:
            raise RuntimeError(f"{_dimension.tsdraft_svg_view_label(view_key)}のBounding Boxを投影できんかったンゴ")

        xs = [p[0] for p in projected]
        ys = [p[1] for p in projected]
        bbox_min_x = min(xs)
        bbox_max_x = max(xs)
        bbox_min_y = min(ys)
        bbox_max_y = max(ys)

        bbox_px_w = bbox_max_x - bbox_min_x
        bbox_px_h = bbox_max_y - bbox_min_y

        if is_user_view:
            # 任意ビューは実寸を保証しない普通のスクショPNG。
            bbox_mm_w = 0.0
            bbox_mm_h = 0.0
            px_per_mm = 1.0
            dpi = 96.0
        else:
            if view_key == "top":
                vals_u = [co.x for co in world_corners]
                vals_v = [co.y for co in world_corners]
            elif view_key == "front":
                vals_u = [co.x for co in world_corners]
                vals_v = [co.z for co in world_corners]
            else:
                vals_u = [co.y for co in world_corners]
                vals_v = [co.z for co in world_corners]

            bbox_mm_w = (max(vals_u) - min(vals_u)) * unit_scale * 1000.0
            bbox_mm_h = (max(vals_v) - min(vals_v)) * unit_scale * 1000.0

            if bbox_px_w <= 1 or bbox_px_h <= 1 or bbox_mm_w <= 0 or bbox_mm_h <= 0:
                raise RuntimeError(f"{_dimension.tsdraft_svg_view_label(view_key)}の実寸対応が取れんかったンゴ")

            px_per_mm_x = bbox_px_w / bbox_mm_w
            px_per_mm_y = bbox_px_h / bbox_mm_h
            px_per_mm = (px_per_mm_x + px_per_mm_y) * 0.5
            dpi = px_per_mm * 25.4

        # -------------------------------------------------
        # Crop bounds: BBox + dimension labels.
        # -------------------------------------------------
        crop_min_x = bbox_min_x
        crop_max_x = bbox_max_x
        crop_min_y = bbox_min_y
        crop_max_y = bbox_max_y

        data = bpy.app.driver_namespace.get(_keys.DATA_KEY, [])
        suppress_sheet_dim_text = bool(
            bpy.app.driver_namespace.get("TSDRAFT_SHEET_SUPPRESS_DIM_TEXT", False)
        )
        visible_axes = set() if (is_user_view or suppress_sheet_dim_text) else {
            axis for axis in _dimension.tsdraft_svg_dimension_axes(view_key)
            if _dimension.tsdraft_dimension_axis_enabled(scene, view_key, axis)
        }

        # Export crop must use the SAME automatic label layout as the viewport.
        # Otherwise the PNG can crop labels that are correctly visible on screen.
        sheet_layout_mode = bool(
            bpy.app.driver_namespace.get("TSDRAFT_SHEET_LAYOUT_MODE", False)
        )
        if sheet_layout_mode:
            auto_axis_layout = {
                "top": {
                    "X": "TOP",
                    "Y": "LEFT",
                },
                "front": {
                    "X": "BOTTOM",
                    "Z": "RIGHT",
                },
                "side": {
                    "Y": "BOTTOM",
                    "Z": "LEFT",
                },
            }
        else:
            auto_axis_layout = {
                "top": {
                    "X": "TOP",
                    "Y": "LEFT",
                },
                "front": {
                    "X": "TOP",
                    "Z": "LEFT",
                },
                "side": {
                    "Y": "TOP",
                    "Z": "LEFT",
                },
            }

        requested_font_size = max(1, int(scene.tsdraft_font_size))

        # text_heightは各ラベルを測るまで存在しないため、
        # ここでは文字サイズから安全余白の初期値を作る。
        base_margin_x = max(
            20.0,
            float(requested_font_size) * 0.75
        )
        base_margin_y = max(
            32.0,
            float(requested_font_size) * 1.15
        )

        available_w = max(
            1.0,
            float(target_region.width) - base_margin_x * 2.0
        )
        available_h = max(
            1.0,
            float(target_region.height) - base_margin_y * 2.0
        )

        for item in data:
            axis = item.get("axis", "X")
            if axis not in visible_axes:
                continue

            label = _dimension.get_dimension_text(scene, item)

            # Mirror _overlay.draw_size_labels() font fitting exactly.
            blf.size(0, requested_font_size)
            text_width, text_height = blf.dimensions(0, label)

            if text_width > available_w or text_height > available_h:
                fit_scale = min(
                    available_w / max(1.0, float(text_width)),
                    available_h / max(1.0, float(text_height)),
                    1.0
                )
                effective_size = max(
                    8,
                    int(requested_font_size * fit_scale)
                )
                blf.size(0, effective_size)
                text_width, text_height = blf.dimensions(0, label)

            axis_lower = axis.lower()
            off_x_mm = getattr(
                scene,
                f"tsdraft_{view_key}_{axis_lower}_offset_x_mm",
                0.0
            )
            off_y_mm = getattr(
                scene,
                f"tsdraft_{view_key}_{axis_lower}_offset_y_mm",
                0.0
            )

            off_x_px = off_x_mm * px_per_mm
            off_y_px = off_y_mm * px_per_mm

            layout_type = auto_axis_layout.get(view_key, {}).get(axis)

            rotate_vertical_text = layout_type in {"LEFT", "RIGHT"}

            if layout_type == "TOP":
                gap = max(10.0, float(text_height) * 0.35)
                text_x = (
                    (bbox_min_x + bbox_max_x) * 0.5
                    - float(text_width) * 0.5
                    + off_x_px
                )
                text_y = bbox_max_y + gap + off_y_px

            elif layout_type == "BOTTOM":
                gap = max(10.0, float(text_height) * 0.35)
                text_x = (
                    (bbox_min_x + bbox_max_x) * 0.5
                    - float(text_width) * 0.5
                    + off_x_px
                )
                text_y = (
                    bbox_min_y
                    - gap
                    - float(text_height)
                    + off_y_px
                )

            elif layout_type == "RIGHT":
                gap = max(10.0, float(text_height) * 0.35)
                text_x = bbox_max_x + gap + off_x_px
                text_y = (
                    (bbox_min_y + bbox_max_y) * 0.5
                    - float(text_width) * 0.5
                    + off_y_px
                )

            elif layout_type == "LEFT":
                gap = max(10.0, float(text_height) * 0.35)
                text_x = (
                    bbox_min_x
                    - gap
                    - float(text_height)
                    + off_x_px
                )
                text_y = (
                    (bbox_min_y + bbox_max_y) * 0.5
                    - float(text_width) * 0.5
                    + off_y_px
                )

            else:
                # Fallback for unusual layouts.
                loc = item.get("location")
                if loc is None:
                    continue

                p2 = view3d_utils.location_3d_to_region_2d(
                    target_region,
                    target_rv3d,
                    loc
                )
                if p2 is None:
                    continue

                text_x = (
                    float(p2.x)
                    - float(text_width) * 0.5
                    + off_x_px
                )
                text_y = (
                    float(p2.y)
                    - float(text_height) * 0.5
                    + off_y_px
                )

            # Mirror viewport clamping as well.
            # ここでは実際に測定済みのtext_heightから余白を決める。
            safe_margin_x = max(
                base_margin_x,
                float(text_height) * 0.75
            )
            safe_margin_y = max(
                base_margin_y,
                float(text_height) * 1.15
            )

            visual_w = float(text_height) if rotate_vertical_text else float(text_width)
            visual_h = float(text_width) if rotate_vertical_text else float(text_height)

            max_text_x = max(
                safe_margin_x,
                float(target_region.width)
                - visual_w
                - safe_margin_x
            )
            max_text_y = max(
                safe_margin_y,
                float(target_region.height)
                - visual_h
                - safe_margin_y
            )

            text_x = min(
                max(text_x, safe_margin_x),
                max_text_x
            )
            text_y = min(
                max(text_y, safe_margin_y),
                max_text_y
            )

            crop_min_x = min(crop_min_x, text_x)
            crop_max_x = max(
                crop_max_x,
                text_x + visual_w
            )
            crop_min_y = min(crop_min_y, text_y)
            crop_max_y = max(
                crop_max_y,
                text_y + visual_h
            )

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

        # クロップ範囲は3Dビューの描画部分だけに限定する。
        # Nパネル(UI)や左ツールバー(TOOLS)が表示中でも画像には入れない。
        safe_left = 14.0
        safe_right = 14.0
        safe_bottom = 18.0

        # screenshot_areaはエリア上端のUI帯を拾う場合があるため、
        # 上端は明示的に大きめの侵入禁止帯を設ける。
        if suppress_sheet_dim_text or hard_suppress_dimension_text:
            safe_top = 30.0
        else:
            safe_top = max(
                30.0,
                float(scene.tsdraft_font_size) * 0.95
            )

        target_x0 = float(target_region.x)
        target_x1 = float(target_region.x + target_region.width)

        for ui_region in area.regions:
            if ui_region == target_region:
                continue

            rx0 = float(ui_region.x)
            rx1 = float(ui_region.x + ui_region.width)

            # Nパネル/右サイドバーがターゲットリージョン右端へ重なる場合
            if ui_region.type == 'UI':
                if rx0 < target_x1 and rx1 > target_x0:
                    overlap = max(0.0, target_x1 - rx0)
                    safe_right = max(safe_right, overlap + 6.0)

            # 左ツールバーがターゲットリージョン左端へ重なる場合
            elif ui_region.type == 'TOOLS':
                if rx0 < target_x1 and rx1 > target_x0:
                    overlap = max(0.0, rx1 - target_x0)
                    safe_left = max(safe_left, overlap + 6.0)

        crop_min_x = max(safe_left, crop_min_x)
        crop_min_y = max(safe_bottom, crop_min_y)
        crop_max_x = min(float(target_region.width) - safe_right, crop_max_x)
        crop_max_y = min(float(target_region.height) - safe_top, crop_max_y)

        if crop_max_x <= crop_min_x or crop_max_y <= crop_min_y:
            raise RuntimeError("書き出し範囲を計算できんかったンゴ")

        if is_user_view:
            # 任意ビューは図面クロップではなく、任意ペインそのものを普通の画像として保存。
            # 左上にBlenderのUI端が写り込むことがあるため、
            # 任意ビューだけ少し内側へクロップする。
            user_safe_left = max(safe_left, 34.0)
            user_safe_top = max(safe_top, 52.0)
            user_safe_right = max(safe_right, 18.0)
            user_safe_bottom = max(safe_bottom, 20.0)

            crop_min_x = user_safe_left
            crop_min_y = user_safe_bottom
            crop_max_x = float(target_region.width) - user_safe_right
            crop_max_y = float(target_region.height) - user_safe_top

        # -------------------------------------------------
        # Screenshot entire editor area once; crop requested pane coordinates.
        # Temporarily hide viewport chrome that can bleed into the PNG,
        # then restore every state directly afterwards.
        # -------------------------------------------------
        # IMPORTANT:
        # Do not toggle N-panel / toolbar here because that changes region geometry
        # after crop coordinates were calculated. That was causing header/UI bleed.
        # WINDOW-region cropping already excludes those regions.
        old_show_gizmo = getattr(space, "show_gizmo", None)

        try:
            if hasattr(space, "show_gizmo"):
                space.show_gizmo = False

            # Make absolutely sure zoom/layout and text-suppression changes
            # have reached the screen before the screenshot.
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
            if hard_suppress_dimension_text:
                tsdraft_force_view_redraw(context, area)

            with context.temp_override(
                window=export_window,
                screen=export_screen,
                area=area
            ):
                bpy.ops.screen.screenshot_area(
                    filepath=temp_full,
                    hide_props_region=False
                )
        finally:
            if old_show_gizmo is not None and hasattr(space, "show_gizmo"):
                space.show_gizmo = old_show_gizmo

            tsdraft_force_view_redraw(context, area)

        if not Path(temp_full).exists():
            raise RuntimeError("画面スクショを書き出せんかったンゴ")

        region_offset_x = target_region.x - area.x
        region_offset_y = target_region.y - area.y

        crop_x = region_offset_x + crop_min_x
        crop_y = region_offset_y + crop_min_y
        crop_w = crop_max_x - crop_min_x
        crop_h = crop_max_y - crop_min_y

        tsdraft_crop_png_with_blender(
            temp_full,
            filepath,
            round(crop_x),
            round(crop_y),
            round(crop_w),
            round(crop_h)
        )

        if not Path(filepath).exists():
            raise RuntimeError("クロップ済みPNGを書き出せんかったンゴ")

        if not tsdraft_patch_png_dpi(filepath, dpi):
            raise RuntimeError("PNGへ実寸dpi情報を書き込めんかったンゴ")

        return {
            "bbox_width_mm": bbox_mm_w,
            "bbox_height_mm": bbox_mm_h,
            "bbox_px_w": bbox_px_w,
            "bbox_px_h": bbox_px_h,
            "dpi": dpi,
            "image_w_px": round(crop_w),
            "image_h_px": round(crop_h),
            "bbox_left_px": float(bbox_min_x - crop_min_x),
            "bbox_right_px": float(crop_max_x - bbox_max_x),
            "bbox_bottom_px": float(bbox_min_y - crop_min_y),
            "bbox_top_px": float(crop_max_y - bbox_max_y),
            "view_distance": target_rv3d.view_distance,
        }

    finally:

        # 撮影前の任意ビューモードへ戻す。
        try:
            scene.tsdraft_user_view_mode = original_user_view_mode
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # 撮影前のBOX＋寸法表示状態へ戻す。
        try:
            scene.tsdraft_show_bbox = original_bbox_state["all"]
            scene.tsdraft_show_bbox_top = original_bbox_state["top"]
            scene.tsdraft_show_bbox_front = original_bbox_state["front"]
            scene.tsdraft_show_bbox_side = original_bbox_state["side"]
            scene.tsdraft_show_bbox_user = original_bbox_state["user"]

            scene.tsdraft_show_dimensions = original_dimension_state["all"]
            scene.tsdraft_show_dimensions_top = original_dimension_state["top"]
            scene.tsdraft_show_dimensions_front = original_dimension_state["front"]
            scene.tsdraft_show_dimensions_side = original_dimension_state["side"]
            scene.tsdraft_show_dimensions_user = original_dimension_state["user"]
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # 撮影前のビュー方向・位置・倍率・背景・グリッド等へ完全復帰。
        try:
            _viewstate.tsdraft_restore_rv3d_state(
                getattr(space, "region_3d", None),
                original_main_rv3d_state
            )

            for rv, rv_state in original_quad_states:
                _viewstate.tsdraft_restore_rv3d_state(rv, rv_state)

            _viewstate.tsdraft_restore_export_display_state(
                space,
                scene,
                original_display_state
            )
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        try:
            if Path(temp_full).exists():
                Path(temp_full).unlink()
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # 描画状態を更新
        try:
            area.tag_redraw()
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        try:
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
        except Exception:
            _bbox.redraw_viewports()
