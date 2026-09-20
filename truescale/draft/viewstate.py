"""3Dビューの見た目を、一時的に作図向けへ変える。

書き出しや作図の間だけ、背景を白にしたりオーバーレイを消したりする。
終わったら必ず元へ戻す。

■ 戻せるように控える

変える前の状態を控えておき、それを使って戻す。控えずに
「既定値へ戻す」とすると、その人が自分で設定していた表示が
勝手に変わる。控えは Scene のカスタムプロパティに置くので、
操作の途中で Blender が落ちても次回に戻せる。

■ ダーク表示は別扱い

作図用の白背景とは目的が違う（見せるための表示）ので、控えも
別のキーに持つ。同じキーを使い回すと、片方を解除したときに
もう片方の控えを上書きしてしまう。

■ 視点の向きから「どの面図か」を決める

正面・側面・上面の判定は、カメラの回転から起こす。ユーザが
少しでも回していたら、どの面図とも見なさない。斜めから見た絵を
「正面図」として書き出すと、寸法が合わない図面になる。
"""

import bpy
import mathutils
from mathutils import Vector

from .. import debug as _debug
from . import keys as _keys


def get_view_key_from_rv3d(rv3d):

    view_dir = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
    view_dir.normalize()

    abs_view = (
        abs(view_dir.x),
        abs(view_dir.y),
        abs(view_dir.z),
    )
    strongest = max(abs_view)

    if strongest >= 0.965:
        if abs_view[1] == strongest:
            return "front", view_dir
        elif abs_view[2] == strongest:
            return "top", view_dir
        else:
            return "side", view_dir

    return "user", view_dir


def get_view3d_override(context):
    area = context.area

    if area is None or area.type != 'VIEW_3D':
        return None

    region = None

    for r in area.regions:
        if r.type == 'WINDOW':
            region = r
            break

    if region is None:
        return None

    return {
        "window": context.window,
        "screen": context.screen,
        "area": area,
        "region": region,
        "space_data": area.spaces.active,
    }


def save_view_state(space):
    namespace = bpy.app.driver_namespace

    # 既に保存済みなら上書きしない
    if namespace.get(_keys.VIEW_STATE_KEY) is not None:
        return

    overlay = space.overlay
    shading = space.shading

    namespace[_keys.VIEW_STATE_KEY] = {
        "shading_type": shading.type,
        "background_type": getattr(shading, "background_type", None),
        "background_color": tuple(getattr(shading, "background_color", (0.05, 0.05, 0.05))),
        "show_axis_x": getattr(overlay, "show_axis_x", True),
        "show_axis_y": getattr(overlay, "show_axis_y", True),
        "show_axis_z": getattr(overlay, "show_axis_z", True),
        "show_cursor": getattr(overlay, "show_cursor", True),
        "show_object_origins": getattr(overlay, "show_object_origins", False),
        "show_object_origins_all": getattr(overlay, "show_object_origins_all", False),
        "show_floor": getattr(overlay, "show_floor", True),
        "show_ortho_grid": getattr(overlay, "show_ortho_grid", True),
        "show_text": getattr(overlay, "show_text", True),
    }


def restore_view_state(space):
    namespace = bpy.app.driver_namespace
    state = namespace.get(_keys.VIEW_STATE_KEY)

    overlay = space.overlay
    shading = space.shading

    if state is None:
        # 保存状態がない時は、ユーザーの現在設定を勝手に変更しない
        return

    shading.type = state["shading_type"]

    if state["background_type"] is not None and hasattr(shading, "background_type"):
        shading.background_type = state["background_type"]

    if hasattr(shading, "background_color"):
        shading.background_color = state["background_color"]

    for attr in (
        "show_axis_x",
        "show_axis_y",
        "show_axis_z",
        "show_cursor",
        "show_object_origins",
        "show_object_origins_all",
        "show_floor",
        "show_ortho_grid",
        "show_text",
    ):
        if hasattr(overlay, attr):
            setattr(overlay, attr, state[attr])

    namespace[_keys.VIEW_STATE_KEY] = None


def tsdraft_background_color(mode, custom_color=(1.0, 1.0, 1.0)):
    """Return viewport/page RGB for the selected background preset."""
    mode = str(mode or 'WHITE').upper()
    if mode == 'BLACK':
        return (0.0, 0.0, 0.0)
    if mode == 'CUSTOM':
        try:
            return tuple(float(v) for v in custom_color[:3])
        except Exception:
            return (1.0, 1.0, 1.0)
    # WHITE and GRID both use a white base.
    return (1.0, 1.0, 1.0)


def configure_drawing_view(space, background_mode='WHITE', custom_color=(1.0, 1.0, 1.0)):
    save_view_state(space)

    mode = str(background_mode or 'WHITE').upper()
    show_grid = (mode == 'GRID')
    bg_color = tsdraft_background_color(mode, custom_color)

    shading = space.shading
    shading.type = 'SOLID'

    if hasattr(shading, "background_type"):
        shading.background_type = 'VIEWPORT'

    if hasattr(shading, "background_color"):
        shading.background_color = bg_color

    overlay = space.overlay

    if hasattr(overlay, "show_axis_x"):
        overlay.show_axis_x = False

    if hasattr(overlay, "show_axis_y"):
        overlay.show_axis_y = False

    if hasattr(overlay, "show_axis_z"):
        overlay.show_axis_z = False

    if hasattr(overlay, "show_cursor"):
        overlay.show_cursor = False

    if hasattr(overlay, "show_object_origins"):
        overlay.show_object_origins = False

    if hasattr(overlay, "show_object_origins_all"):
        overlay.show_object_origins_all = False

    if hasattr(overlay, "show_floor"):
        overlay.show_floor = show_grid

    if hasattr(overlay, "show_ortho_grid"):
        overlay.show_ortho_grid = show_grid

    # Blender標準の「上」「前」等を隠し、独自の大きいビュー名を使う
    if hasattr(overlay, "show_text"):
        overlay.show_text = False


def save_dark_view_state(space):
    namespace = bpy.app.driver_namespace
    if namespace.get(_keys.DARK_VIEW_STATE_KEY) is not None:
        return

    shading = space.shading

    theme_gradients = None
    try:
        theme_gradients = bpy.context.preferences.themes[0].view_3d.space.gradients
    except Exception:
        _debug.swallowed("draft.save_dark_view_state")

    overlay = space.overlay
    scene = getattr(bpy.context, "scene", None)

    state = {
        "background_type": getattr(shading, "background_type", None),
        "background_color": tuple(getattr(shading, "background_color", (0.05, 0.05, 0.05))),
        "show_floor": getattr(overlay, "show_floor", None),
        "show_ortho_grid": getattr(overlay, "show_ortho_grid", None),
        "show_axis_x": getattr(overlay, "show_axis_x", None),
        "show_axis_y": getattr(overlay, "show_axis_y", None),
        "scene_show_grid": (
            bool(getattr(scene, "tsdraft_show_grid", False))
            if scene is not None
            else False
        ),
    }

    if theme_gradients is not None:
        state["theme_background_type"] = theme_gradients.background_type
        state["theme_gradient"] = tuple(theme_gradients.gradient)
        state["theme_high_gradient"] = tuple(theme_gradients.high_gradient)

    namespace[_keys.DARK_VIEW_STATE_KEY] = state


def apply_dark_place_view(space):
    save_dark_view_state(space)

    shading = space.shading

    # Blender標準のテーマグラデーションを深海色に一時変更
    try:
        gradients = bpy.context.preferences.themes[0].view_3d.space.gradients
        gradients.background_type = 'LINEAR'
        gradients.gradient = _keys.DARK_GRAD_BOTTOM
        gradients.high_gradient = _keys.DARK_GRAD_TOP

        if hasattr(shading, "background_type"):
            shading.background_type = 'THEME'
    except Exception:
        # 万一テーマへ触れない環境では従来の単色へフォールバック
        if hasattr(shading, "background_type"):
            shading.background_type = 'VIEWPORT'
        if hasattr(shading, "background_color"):
            shading.background_color = _keys.DARK_BG


def restore_dark_place_view(space):
    namespace = bpy.app.driver_namespace
    state = namespace.get(_keys.DARK_VIEW_STATE_KEY)
    shading = space.shading

    if state is not None:
        # テーマのグラデーション設定を元へ戻す
        try:
            gradients = bpy.context.preferences.themes[0].view_3d.space.gradients

            if "theme_background_type" in state:
                gradients.background_type = state["theme_background_type"]
            if "theme_gradient" in state:
                gradients.gradient = state["theme_gradient"]
            if "theme_high_gradient" in state:
                gradients.high_gradient = state["theme_high_gradient"]
        except Exception:
            _debug.swallowed("draft.restore_dark_place_view")

        if state.get("background_type") is not None and hasattr(shading, "background_type"):
            shading.background_type = state["background_type"]

        if hasattr(shading, "background_color"):
            shading.background_color = state["background_color"]

        overlay = space.overlay

        if state.get("show_floor") is not None and hasattr(overlay, "show_floor"):
            overlay.show_floor = state["show_floor"]

        if state.get("show_ortho_grid") is not None and hasattr(overlay, "show_ortho_grid"):
            overlay.show_ortho_grid = state["show_ortho_grid"]

        if state.get("show_axis_x") is not None and hasattr(overlay, "show_axis_x"):
            overlay.show_axis_x = state["show_axis_x"]

        if state.get("show_axis_y") is not None and hasattr(overlay, "show_axis_y"):
            overlay.show_axis_y = state["show_axis_y"]

        # tsdraft_show_grid は暗所中に変更していないため、ここでは触らない。
        # Sceneプロパティへ代入すると update_grid() が走り、
        # 図面用の白背景を再適用してしまうため、実際のoverlayだけ復元する。

    namespace[_keys.DARK_VIEW_STATE_KEY] = None


def tsdraft_capture_rv3d_state(rv3d):
    if rv3d is None:
        return None

    return {
        "view_location": rv3d.view_location.copy(),
        "view_rotation": rv3d.view_rotation.copy(),
        "view_distance": float(rv3d.view_distance),
        "view_perspective": rv3d.view_perspective,
        "view_camera_zoom": getattr(rv3d, "view_camera_zoom", None),
        "view_camera_offset": (
            tuple(rv3d.view_camera_offset)
            if hasattr(rv3d, "view_camera_offset")
            else None
        ),
    }


def tsdraft_restore_rv3d_state(rv3d, state):
    if rv3d is None or not state:
        return

    rv3d.view_location = state["view_location"]
    rv3d.view_rotation = state["view_rotation"]
    rv3d.view_distance = state["view_distance"]
    rv3d.view_perspective = state["view_perspective"]

    if (
        state.get("view_camera_zoom") is not None
        and hasattr(rv3d, "view_camera_zoom")
    ):
        rv3d.view_camera_zoom = state["view_camera_zoom"]

    if (
        state.get("view_camera_offset") is not None
        and hasattr(rv3d, "view_camera_offset")
    ):
        rv3d.view_camera_offset = state["view_camera_offset"]


def tsdraft_capture_export_display_state(space, scene):
    shading = space.shading
    overlay = space.overlay

    overlay_attrs = (
        "show_axis_x",
        "show_axis_y",
        "show_axis_z",
        "show_cursor",
        "show_object_origins",
        "show_object_origins_all",
        "show_floor",
        "show_ortho_grid",
        "show_text",
    )

    return {
        "shading_type": shading.type,
        "background_type": getattr(shading, "background_type", None),
        "background_color": tuple(
            getattr(shading, "background_color", (0.05, 0.05, 0.05))
        ),
        "overlay": {
            attr: getattr(overlay, attr, None)
            for attr in overlay_attrs
        },
        "drawing_mode": bool(
            getattr(scene, "tsdraft_drawing_mode", False)
        ),
        "driver_view_state": bpy.app.driver_namespace.get(
            _keys.VIEW_STATE_KEY,
            None
        ),
    }


def tsdraft_restore_export_display_state(space, scene, state):
    if not state:
        return

    shading = space.shading
    overlay = space.overlay

    try:
        shading.type = state["shading_type"]
    except Exception:
        _debug.swallowed("draft.tsdraft_restore_export_display_state")

    if (
        state.get("background_type") is not None
        and hasattr(shading, "background_type")
    ):
        try:
            shading.background_type = state["background_type"]
        except Exception:
            _debug.swallowed("draft.tsdraft_restore_export_display_state")

    if hasattr(shading, "background_color"):
        try:
            shading.background_color = state["background_color"]
        except Exception:
            _debug.swallowed("draft.tsdraft_restore_export_display_state")

    for attr, value in state.get("overlay", {}).items():
        if value is not None and hasattr(overlay, attr):
            try:
                setattr(overlay, attr, value)
            except Exception:
                _debug.swallowed("draft.tsdraft_restore_export_display_state")

    scene.tsdraft_drawing_mode = state.get("drawing_mode", False)

    # configure_drawing_view() が書き出し中に作った退避状態を残さない。
    bpy.app.driver_namespace[_keys.VIEW_STATE_KEY] = state.get(
        "driver_view_state",
        None
    )
