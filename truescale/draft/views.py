"""四分割と単一ビューの切り替え。

作図中は四分割（正面・上面・側面・自由）で見て、書き出すときに
1つへ切り替える。

■ 四分割のズームを揃える

Blender は四分割の各ビューのズームを別々に持つ。ばらばらだと、
同じ物が面ごとに違う大きさに見えて、図面として読めない。
切り替えた直後に揃え、タイマーで一度だけ念押しする。

一度だけなのは、毎フレーム揃えるとユーザがズームできなくなる
ため。切り替え直後の1回だけ効かせて、あとは触らない。
"""

from mathutils import Vector
import bpy
from bpy_extras import view3d_utils

from .. import debug as _debug
from . import bbox as _bbox
from .export import capture as _capture
from . import keys as _keys
from . import viewstate as _viewstate


def tsdraft_enforce_quad_zoom_lock_once():
    """
    Global safety pass for drawing-mode Quad Views.
    Top / Front / Side may zoom out freely, but cannot zoom in far enough
    for the Bounding Box to exceed ~72% of any pane.
    """
    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)
    if bbox_obj is None:
        return

    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return

    try:
        windows = list(wm.windows)
    except Exception:
        return

    for window in windows:
        scene = getattr(window, "scene", None)
        screen = getattr(window, "screen", None)

        if scene is None or screen is None:
            continue

        if not getattr(scene, "tsdraft_drawing_mode", False):
            continue

        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue

            space = area.spaces.active
            try:
                if not space.region_quadviews:
                    continue
            except Exception:
                continue

            items = []
            for key in ("top", "front", "side"):
                region, rv3d = _capture.tsdraft_find_view_region(area, key)
                if region is not None and rv3d is not None:
                    items.append((key, region, rv3d))

            if not items:
                continue

            try:
                corners_world = [
                    bbox_obj.matrix_world @ Vector(corner)
                    for corner in bbox_obj.bound_box
                ]
            except Exception:
                continue

            # Find the minimum safe distance required by ALL ortho panes.
            safe_distance = max(float(rv.view_distance) for _, _, rv in items)
            need_change = False

            for _key, region, rv3d in items:
                projected = []
                for world_co in corners_world:
                    p = view3d_utils.location_3d_to_region_2d(
                        region, rv3d, world_co
                    )
                    if p is not None:
                        projected.append(p)

                if len(projected) < 4:
                    continue

                xs = [p.x for p in projected]
                ys = [p.y for p in projected]
                bbox_w = max(xs) - min(xs)
                bbox_h = max(ys) - min(ys)

                usable_w = max(1.0, float(region.width) * 0.56)
                usable_h = max(1.0, float(region.height) * 0.56)

                factor = max(
                    bbox_w / usable_w,
                    bbox_h / usable_h,
                    1.0
                )

                if factor > 1.0005:
                    proposed = float(rv3d.view_distance) * factor * 1.03
                    safe_distance = max(safe_distance, proposed)
                    need_change = True

            if need_change:
                state = {}
                for key, _region, rv3d in items:
                    rv3d.view_distance = safe_distance
                    state[key] = safe_distance

                bpy.app.driver_namespace[_keys.ZOOM_SYNC_STATE_KEY] = state

                try:
                    area.tag_redraw()
                except Exception:
                    _debug.swallowed("draft.tsdraft_enforce_quad_zoom_lock_once")


def tsdraft_quad_zoom_lock_timer():
    try:
        tsdraft_enforce_quad_zoom_lock_once()
    except Exception:
        _debug.swallowed("draft.tsdraft_quad_zoom_lock_timer")

    # Keep watching while add-on is enabled.
    return 0.10


def update_drawing_background(self, context):
    if not getattr(context.scene, "tsdraft_drawing_mode", False):
        # 通常Blender表示では背景やoverlayを勝手に変更しない。
        return

    if context.area and context.area.type == 'VIEW_3D':
        _viewstate.configure_drawing_view(
            context.area.spaces.active,
            getattr(context.scene, "tsdraft_drawing_background", 'WHITE'),
            getattr(context.scene, "tsdraft_drawing_background_color", (1.0, 1.0, 1.0))
        )
        _bbox.redraw_viewports()


def tsdraft_fit_quad_for_drawing(context, area, padding_factor=1.28):
    """
    Quad Viewの上面・前面・側面をBBox中心へ寄せ、
    寸法文字のために少し余白を持たせる。
    任意ビューは触らない。
    """
    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)
    if bbox_obj is None:
        return

    try:
        corners_world = [
            bbox_obj.matrix_world @ Vector(corner)
            for corner in bbox_obj.bound_box
        ]
    except Exception:
        return

    if not corners_world:
        return

    center = sum(corners_world, Vector()) / len(corners_world)

    ortho_items = []
    for key in ("top", "front", "side"):
        region, rv3d = _capture.tsdraft_find_view_region(area, key)
        if region is None or rv3d is None:
            continue
        ortho_items.append((key, region, rv3d))

    if not ortho_items:
        return

    # まず中心を揃える。
    for _key, _region, rv3d in ortho_items:
        rv3d.view_location = center

    # 現在倍率で投影し、BBoxが各paneへ収まるための倍率を求める。
    required_scale = 1.0

    for _key, region, rv3d in ortho_items:
        projected = []
        for world_co in corners_world:
            p = view3d_utils.location_3d_to_region_2d(
                region,
                rv3d,
                world_co
            )
            if p is not None:
                projected.append(p)

        if not projected:
            continue

        xs = [p.x for p in projected]
        ys = [p.y for p in projected]

        bbox_w_px = max(xs) - min(xs)
        bbox_h_px = max(ys) - min(ys)

        usable_w = max(1.0, float(region.width) / padding_factor)
        usable_h = max(1.0, float(region.height) / padding_factor)

        scale_w = bbox_w_px / usable_w
        scale_h = bbox_h_px / usable_h

        required_scale = max(required_scale, scale_w, scale_h)

    if required_scale > 1.0:
        for _key, _region, rv3d in ortho_items:
            rv3d.view_distance *= required_scale

    # 最低限の寸法余白を常に確保。
    for _key, _region, rv3d in ortho_items:
        rv3d.view_distance *= 1.08

    # 3面は同倍率を維持する。
    common_distance = max(rv3d.view_distance for _, _, rv3d in ortho_items)
    for _key, _region, rv3d in ortho_items:
        rv3d.view_distance = common_distance

    bpy.app.driver_namespace[_keys.ZOOM_SYNC_STATE_KEY] = {
        key: common_distance
        for key, _region, _rv3d in ortho_items
    }


def is_quad_view(space):
    try:
        return len(space.region_quadviews) > 0
    except Exception:
        return False


def switch_single_view(context, axis_type=None):
    override = _viewstate.get_view3d_override(context)

    if override is None:
        return False

    space = override["space_data"]

    _viewstate.configure_drawing_view(
        space,
        getattr(context.scene, "tsdraft_drawing_background", 'WHITE'),
        getattr(context.scene, "tsdraft_drawing_background_color", (1.0, 1.0, 1.0))
    )
    context.scene.tsdraft_drawing_mode = True

    if is_quad_view(space):
        with context.temp_override(**override):
            bpy.ops.screen.region_quadview()

        override = _viewstate.get_view3d_override(context)

        if override is None:
            return False

    if axis_type is not None:
        with context.temp_override(**override):
            bpy.ops.view3d.view_axis(
                type=axis_type,
                align_active=False
            )

    _bbox.redraw_viewports()
    return True
