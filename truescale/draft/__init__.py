import bpy

from .. import debug as _debug
import traceback
import os
import html
import math
import struct
import zlib
import base64
from pathlib import Path
import blf
import gpu
import mathutils
from mathutils import Vector
from gpu_extras.batch import batch_for_shader
from bpy.app.handlers import persistent
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper


HANDLER_KEY = "TSDRAFT_SIZE_LABEL_HANDLER"
BBOX_HANDLER_KEY = "TSDRAFT_BBOX_DRAW_HANDLER"
VIEW_LABEL_HANDLER_KEY = "TSDRAFT_VIEW_LABEL_HANDLER"
DATA_KEY = "TSDRAFT_SIZE_LABEL_DATA"
SOURCE_KEY = "TSDRAFT_SIZE_SOURCE_NAME"
VIEW_STATE_KEY = "TSDRAFT_VIEW_STATE"
BBOX_NAME = "サイズ用バウンディングボックス"

# 旧バージョンが driver_namespace に残したキー。後始末のためだけに持つ。
# ここは「過去に実際に使われていた文字列」でなければ意味がないので、
# 接頭辞の一括置換の対象にしてはいけない。
#   MHS_ … 最初期の接頭辞
#   MHP_ … Truescale へ改称する前の接頭辞
LEGACY_HANDLER_KEYS = (
    "MHS_SIZE_LABEL_HANDLER",
    "MHS_BBOX_DRAW_HANDLER",
    "MHS_VIEW_LABEL_HANDLER",
    "MHP_SIZE_LABEL_HANDLER",
    "MHP_BBOX_DRAW_HANDLER",
    "MHP_VIEW_LABEL_HANDLER",
)
LEGACY_DATA_KEY = "MHP_SIZE_LABEL_DATA"
LEGACY_SOURCE_KEY = "MHP_SIZE_SOURCE_NAME"
EXPORT_CONTEXT_KEY = "TSDRAFT_EXPORT_VIEW_CONTEXT"
ZOOM_SYNC_STATE_KEY = "TSDRAFT_ORTHO_ZOOM_SYNC_STATE"
AUTO_FOLLOW_SIGNATURE_KEY = "TSDRAFT_AUTO_FOLLOW_SIGNATURE"
AUTO_FOLLOW_GUARD_KEY = "TSDRAFT_AUTO_FOLLOW_GUARD"
LAST_EXPORT_DIR_KEY = "TSDRAFT_LAST_EXPORT_DIR"
DARK_VIEW_STATE_KEY = "TSDRAFT_DARK_VIEW_STATE"
DARK_BG = (0.004, 0.012, 0.028)
DARK_GRAD_TOP = (0.045, 0.145, 0.220)
DARK_GRAD_BOTTOM = (0.006, 0.018, 0.032)
DARK_LINE = (0.32, 0.34, 0.37, 1.0)
DARK_TEXT = (0.72, 0.88, 0.96, 1.0)



# AddonPreferences の bl_idname は、サブパッケージ名ではなく
# アドオン本体のパッケージ名でなければならない。
# このモジュールは <アドオン>.draft として読み込まれるので、
# 末尾の ".draft" を落としたものが本体のパッケージ名になる。
ADDON_PACKAGE = __package__.rpartition(".")[0]


def get_addon_preferences(context=None):
    context = context or bpy.context
    try:
        addon = context.preferences.addons.get(ADDON_PACKAGE)
        if addon is not None:
            return addon.preferences
    except Exception:
        _debug.swallowed("draft.get_addon_preferences")
    return None


class TSDRAFT_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    show_dark_place_button: bpy.props.BoolProperty(
        name="「なんかずっと暗いとこ」を表示",
        description="お遊び機能のボタンを造形ヘルパーに表示します",
        default=True
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="お遊び")
        layout.prop(self, "show_dark_place_button")


def save_dark_view_state(space):
    namespace = bpy.app.driver_namespace
    if namespace.get(DARK_VIEW_STATE_KEY) is not None:
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

    namespace[DARK_VIEW_STATE_KEY] = state


def apply_dark_place_view(space):
    save_dark_view_state(space)

    shading = space.shading

    # Blender標準のテーマグラデーションを深海色に一時変更
    try:
        gradients = bpy.context.preferences.themes[0].view_3d.space.gradients
        gradients.background_type = 'LINEAR'
        gradients.gradient = DARK_GRAD_BOTTOM
        gradients.high_gradient = DARK_GRAD_TOP

        if hasattr(shading, "background_type"):
            shading.background_type = 'THEME'
    except Exception:
        # 万一テーマへ触れない環境では従来の単色へフォールバック
        if hasattr(shading, "background_type"):
            shading.background_type = 'VIEWPORT'
        if hasattr(shading, "background_color"):
            shading.background_color = DARK_BG


def restore_dark_place_view(space):
    namespace = bpy.app.driver_namespace
    state = namespace.get(DARK_VIEW_STATE_KEY)
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

    namespace[DARK_VIEW_STATE_KEY] = None


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


def draw_bbox_overlay():
    context = bpy.context

    if context.area is None or context.area.type != 'VIEW_3D':
        return

    rv3d = context.region_data
    if rv3d is None:
        return

    scene = context.scene
    dark_place = getattr(scene, "tsdraft_dark_place", False)

    # クイック非表示時は、各ビュー設定より先にBOX全体を止める。
    if not getattr(scene, "tsdraft_show_bbox", True) and not dark_place:
        return

    mode = scene.tsdraft_frame_mode
    if mode == 'NONE' and not dark_place:
        return

    view_key, view_dir = get_view_key_from_rv3d(rv3d)

    explicit_user_mode = bool(
        getattr(scene, "tsdraft_user_view_mode", False)
    )

    # 普通の斜めデフォルトビューは内部判定がuserでも、
    # BOX＋寸法作成直後はグローバル表示を優先して見せる。
    # 「任意」ボタンを明示的に押した時だけuser個別設定を使う。
    if explicit_user_mode:
        view_key = "user"
        if (
            not getattr(scene, "tsdraft_show_bbox_user", True)
            and not dark_place
        ):
            return
    elif view_key != "user":
        if (
            not getattr(scene, f"tsdraft_show_bbox_{view_key}", True)
            and not dark_place
        ):
            return

    bbox_obj = bpy.data.objects.get(BBOX_NAME)
    if bbox_obj is None:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()

    if dark_place:
        shader.uniform_float("color", DARK_LINE)
        gpu.state.line_width_set(2.5)
    else:
        frame_color = scene.tsdraft_frame_color
        shader.uniform_float(
            "color",
            (
                frame_color[0],
                frame_color[1],
                frame_color[2],
                frame_color[3]
            )
        )
        gpu.state.line_width_set(scene.tsdraft_frame_width)

    # -----------------------------------------------------
    # お遊び：「なんかずっと暗いとこ」檻表示
    # -----------------------------------------------------
    if dark_place:
        verts_local = [v.co.copy() for v in bbox_obj.data.vertices]
        if len(verts_local) >= 8:
            xs = [v.x for v in verts_local]
            ys = [v.y for v in verts_local]
            zs = [v.z for v in verts_local]

            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            zmin, zmax = min(zs), max(zs)

            segments_local = []

            # 上下の外周
            rects = (
                (zmin, ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))),
                (zmax, ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))),
            )
            for z, pts in rects:
                for i in range(4):
                    x1, y1 = pts[i]
                    x2, y2 = pts[(i + 1) % 4]
                    segments_local.append(((x1, y1, z), (x2, y2, z)))

            # 四隅の縦柱
            for x in (xmin, xmax):
                for y in (ymin, ymax):
                    segments_local.append(((x, y, zmin), (x, y, zmax)))

            # 前後面の縦格子
            bar_count_x = 8
            for i in range(1, bar_count_x):
                t = i / bar_count_x
                x = xmin + (xmax - xmin) * t
                segments_local.append(((x, ymin, zmin), (x, ymin, zmax)))
                segments_local.append(((x, ymax, zmin), (x, ymax, zmax)))

            # 左右面の縦格子
            bar_count_y = 5
            for i in range(1, bar_count_y):
                t = i / bar_count_y
                y = ymin + (ymax - ymin) * t
                segments_local.append(((xmin, y, zmin), (xmin, y, zmax)))
                segments_local.append(((xmax, y, zmin), (xmax, y, zmax)))

            coords = []
            indices = []

            for a_local, b_local in segments_local:
                a = bbox_obj.matrix_world @ mathutils.Vector(a_local)
                b = bbox_obj.matrix_world @ mathutils.Vector(b_local)
                base = len(coords)
                coords.extend((tuple(a), tuple(b)))
                indices.append((base, base + 1))

            if coords:
                batch = batch_for_shader(
                    shader,
                    'LINES',
                    {"pos": coords},
                    indices=indices
                )
                batch.draw(shader)

        gpu.state.line_width_set(1.0)
        return

    # -----------------------------------------------------
    # 通常の枠表示
    # -----------------------------------------------------
    if mode == 'BOX':
        # BBoxメッシュ自身が持つ12本の辺をそのまま描画する。
        # ワールド座標差から「1軸だけ違う頂点」を推測すると、
        # オブジェクト回転時に本来の辺でもXYZすべてが変化して
        # 辺判定から漏れ、縦線などが欠けることがある。
        verts = [bbox_obj.matrix_world @ v.co for v in bbox_obj.data.vertices]
        edges = [tuple(edge.vertices) for edge in bbox_obj.data.edges]

        if verts and edges:
            coords = [(v.x, v.y, v.z) for v in verts]
            batch = batch_for_shader(
                shader,
                'LINES',
                {"pos": coords},
                indices=edges
            )
            batch.draw(shader)

    elif mode == 'LINES':
        # 「寸法線のみ」は文字の表示ON/OFFとは独立して描画する。
        # 以前は tsdraft_show_dimensions_* がOFFだと線まで消えていたため、
        # 任意ビューや通常の斜めビューで「寸法線のみ」を選ぶと
        # 何も表示されない状態になっていた。
        data = bpy.app.driver_namespace.get(DATA_KEY, [])
        if not data:
            gpu.state.line_width_set(1.0)
            return

    
        axis_vectors = {
            "X": Vector((1.0, 0.0, 0.0)),
            "Y": Vector((0.0, 1.0, 0.0)),
            "Z": Vector((0.0, 0.0, 1.0)),
        }

        segments = []

        for item in data:
            axis_name = item.get("axis", "X")

            if axis_name in axis_vectors:
                axis_world = bbox_obj.matrix_world.to_3x3() @ axis_vectors[axis_name]
                if axis_world.length > 0:
                    axis_world.normalize()

                    if abs(axis_world.dot(view_dir)) >= 0.965:
                        continue

            a = item.get("world_a")
            b = item.get("world_b")

            if a is None or b is None:
                continue

            segments.append((tuple(a), tuple(b)))

        if segments:
            coords = []
            indices = []

            for a, b in segments:
                base = len(coords)
                coords.extend((a, b))
                indices.append((base, base + 1))

            batch = batch_for_shader(
                shader,
                'LINES',
                {"pos": coords},
                indices=indices
            )
            batch.draw(shader)

    gpu.state.line_width_set(1.0)



def ensure_bbox_draw_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(BBOX_HANDLER_KEY)

    if handler is None:
        handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_bbox_overlay,
            (),
            'WINDOW',
            'POST_VIEW'
        )
        namespace[BBOX_HANDLER_KEY] = handler


def remove_bbox_draw_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(BBOX_HANDLER_KEY)

    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        except Exception:
            _debug.swallowed("draft.remove_bbox_draw_handler")

    namespace[BBOX_HANDLER_KEY] = None


def draw_view_label():
    context = bpy.context

    if context.area is None or context.area.type != 'VIEW_3D':
        return

    rv3d = context.region_data
    region = context.region
    if rv3d is None or region is None:
        return

    scene = context.scene

    # 図面表示中だけ独自ビュー名を表示
    if not getattr(scene, "tsdraft_drawing_mode", False):
        return

    view_key, _ = get_view_key_from_rv3d(rv3d)

    # -----------------------------------------------------
    # HARD ZOOM LOCK
    # 現在描画中のpane自身でBBoxを投影し、
    # 画面の80%を超えるほどズームインされたら即座に戻す。
    # 任意ビューは対象外。
    # -----------------------------------------------------
    if view_key in {"top", "front", "side"}:
        bbox_obj = bpy.data.objects.get(BBOX_NAME)

        if bbox_obj is not None:
            try:
                corners_world = [
                    bbox_obj.matrix_world @ Vector(corner)
                    for corner in bbox_obj.bound_box
                ]

                projected = []
                for world_co in corners_world:
                    p = view3d_utils.location_3d_to_region_2d(
                        region,
                        rv3d,
                        world_co
                    )
                    if p is not None:
                        projected.append(p)

                if len(projected) >= 4:
                    xs = [p.x for p in projected]
                    ys = [p.y for p in projected]

                    bbox_w = max(xs) - min(xs)
                    bbox_h = max(ys) - min(ys)

                    # 10% margin on every side.
                    usable_w = max(1.0, float(region.width) * 0.74)
                    usable_h = max(1.0, float(region.height) * 0.74)

                    factor = max(
                        bbox_w / usable_w,
                        bbox_h / usable_h,
                        1.0
                    )

                    if factor > 1.0005:
                        rv3d.view_distance *= factor

                        # New safe zoom becomes the master value for the 3 ortho panes.
                        safe_distance = float(rv3d.view_distance)

                        try:
                            quadviews = list(context.area.spaces.active.region_quadviews)
                        except Exception:
                            quadviews = []

                        sync_state = {}
                        for other_rv in quadviews:
                            other_key, _ = get_view_key_from_rv3d(other_rv)
                            if other_key in {"top", "front", "side"}:
                                other_rv.view_distance = safe_distance
                                sync_state[other_key] = safe_distance

                        if sync_state:
                            bpy.app.driver_namespace[ZOOM_SYNC_STATE_KEY] = sync_state

            except Exception:
                _debug.swallowed("draft.draw_view_label")

    # Normal 3-view zoom synchronization after the hard clamp.
    try:
        tsdraft_sync_ortho_zoom(context.area.spaces.active)
    except Exception:
        _debug.swallowed("draft.draw_view_label")

    labels = {
        "top": "上面",
        "front": "前面",
        "side": "側面",
        "user": "任意",
    }

    label = labels.get(view_key, "任意")

    font_id = 0
    size = 28

    blf.size(font_id, size)
    blf.color(font_id, 0.18, 0.18, 0.18, 1.0)
    blf.position(font_id, 28, region.height - size - 24, 0)
    blf.draw(font_id, label)



def ensure_view_label_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(VIEW_LABEL_HANDLER_KEY)

    if handler is None:
        handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_view_label,
            (),
            'WINDOW',
            'POST_PIXEL'
        )
        namespace[VIEW_LABEL_HANDLER_KEY] = handler


def remove_view_label_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(VIEW_LABEL_HANDLER_KEY)

    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        except Exception:
            _debug.swallowed("draft.remove_view_label_handler")

    namespace[VIEW_LABEL_HANDLER_KEY] = None




SVG_PX_TO_MM = 25.4 / 96.0




def tsdraft_import_legacy_state():
    namespace = bpy.app.driver_namespace

    if not namespace.get(DATA_KEY):
        legacy_data = namespace.get(LEGACY_DATA_KEY)
        if legacy_data:
            namespace[DATA_KEY] = legacy_data

    if not namespace.get(SOURCE_KEY):
        legacy_source = namespace.get(LEGACY_SOURCE_KEY)
        if legacy_source:
            namespace[SOURCE_KEY] = legacy_source


def tsdraft_resolve_source_object(context):
    tsdraft_import_legacy_state()
    namespace = bpy.app.driver_namespace

    # 1. 従来の一時記憶
    source_name = namespace.get(SOURCE_KEY)
    if source_name:
        obj = bpy.data.objects.get(source_name)
        if obj is not None:
            return obj

    # 2. Bounding Box自身に保存した永続情報
    bbox_obj = bpy.data.objects.get(BBOX_NAME)
    if bbox_obj is not None:
        source_name = bbox_obj.get("tsdraft_source_name")
        if source_name:
            obj = bpy.data.objects.get(source_name)
            if obj is not None:
                namespace[SOURCE_KEY] = obj.name
                return obj

    # 3. 現在のアクティブメッシュから復旧
    active = getattr(context, "active_object", None)
    if (
        active is not None
        and active.type == 'MESH'
        and active.name != BBOX_NAME
    ):
        namespace[SOURCE_KEY] = active.name
        if bbox_obj is not None:
            bbox_obj["tsdraft_source_name"] = active.name
        return active

    # 4. 選択中のメッシュが1個だけならそれを採用
    selected_meshes = [
        obj for obj in getattr(context, "selected_objects", [])
        if obj.type == 'MESH' and obj.name != BBOX_NAME
    ]
    if len(selected_meshes) == 1:
        obj = selected_meshes[0]
        namespace[SOURCE_KEY] = obj.name
        if bbox_obj is not None:
            bbox_obj["tsdraft_source_name"] = obj.name
        return obj

    # 5. シーン内に候補メッシュが1個だけなら最後の救済
    candidates = [
        obj for obj in context.scene.objects
        if obj.type == 'MESH' and obj.name != BBOX_NAME
    ]
    if len(candidates) == 1:
        obj = candidates[0]
        namespace[SOURCE_KEY] = obj.name
        if bbox_obj is not None:
            bbox_obj["tsdraft_source_name"] = obj.name
        return obj

    return None


def tsdraft_svg_view_label(view_key):
    return {
        "top": "上面",
        "front": "前面",
        "side": "側面",
        "user": "任意",
    }.get(view_key, view_key)


def tsdraft_svg_dimension_axes(view_key):
    return {
        "top": {"X", "Y"},
        "front": {"X", "Z"},
        "side": {"Y", "Z"},
    }.get(view_key, set())


def tsdraft_dimension_axis_enabled(scene, view_key, axis_name):
    """Return whether a dimension axis should be shown for a drawing view."""
    axis_name = str(axis_name).upper()
    valid_axes = tsdraft_svg_dimension_axes(view_key)
    if not valid_axes:
        return True
    if axis_name not in valid_axes:
        return False
    return bool(getattr(
        scene,
        f"tsdraft_show_dimension_{view_key}_{axis_name.lower()}",
        True
    ))


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
    path = bpy.app.driver_namespace.get(LAST_EXPORT_DIR_KEY)

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
            _debug.swallowed("draft.tsdraft_remember_export_dir")

        bpy.app.driver_namespace[LAST_EXPORT_DIR_KEY] = folder


def tsdraft_store_export_view_context(context):
    """Remember the actual 3D editor before Blender opens the file browser."""
    if context.area is None or context.area.type != 'VIEW_3D':
        return

    region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return

    bpy.app.driver_namespace[EXPORT_CONTEXT_KEY] = {
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
    saved = bpy.app.driver_namespace.get(EXPORT_CONTEXT_KEY)

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
            _debug.swallowed("draft.tsdraft_get_export_view_context")

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
        _debug.swallowed("draft.tsdraft_get_export_view_context")

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
            key, _ = get_view_key_from_rv3d(rv)
            if key == wanted_key:
                return region, rv

    # Single view fallback.
    rv = space.region_3d
    if rv is not None:
        key, _ = get_view_key_from_rv3d(rv)
        if key == wanted_key:
            region = next((r for r in window_regions if r.type == 'WINDOW'), None)
            if region is not None:
                return region, rv

    return None, None


def tsdraft_sync_ortho_zoom(space):
    """
    Keep Top / Front / Side view_distance synchronized in Quad View.
    User view stays independent.
    """
    try:
        quadviews = list(space.region_quadviews)
    except Exception:
        return

    if not quadviews:
        return

    orthos = []
    for rv in quadviews:
        key, _ = get_view_key_from_rv3d(rv)
        if key in {"top", "front", "side"}:
            orthos.append((key, rv))

    if len(orthos) < 2:
        return

    ns = bpy.app.driver_namespace
    prev = ns.get(ZOOM_SYNC_STATE_KEY)

    current = {key: float(rv.view_distance) for key, rv in orthos}

    # First run after entering Quad View:
    # use the front view as master if available, otherwise first ortho view.
    if not isinstance(prev, dict) or not prev:
        master_value = None

        for key, rv in orthos:
            if key == "front":
                master_value = float(rv.view_distance)
                break

        if master_value is None:
            master_value = float(orthos[0][1].view_distance)

        for _, rv in orthos:
            rv.view_distance = master_value

        ns[ZOOM_SYNC_STATE_KEY] = {
            key: master_value
            for key, _ in orthos
        }
        return

    # Detect which pane changed compared with previous draw.
    changed = []
    for key, rv in orthos:
        value = float(rv.view_distance)
        old = prev.get(key)

        if old is not None and abs(value - float(old)) > 1e-6:
            changed.append((key, value))

    if changed:
        # Last changed pane becomes master.
        master_value = changed[-1][1]

        for _, rv in orthos:
            rv.view_distance = master_value

        ns[ZOOM_SYNC_STATE_KEY] = {
            key: master_value
            for key, _ in orthos
        }
    else:
        ns[ZOOM_SYNC_STATE_KEY] = current



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
                _debug.swallowed("draft.tsdraft_frame_export_region")

        try:
            source_obj.select_set(True)
            view_layer.objects.active = source_obj
        except Exception:
            _debug.swallowed("draft.tsdraft_frame_export_region")

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
            _debug.swallowed("draft.tsdraft_frame_export_region")

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
            _debug.swallowed("draft.tsdraft_frame_export_region")

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
            _debug.swallowed("draft.tsdraft_frame_export_region")

        for obj in selected_before:
            try:
                obj.select_set(True)
            except Exception:
                _debug.swallowed("draft.tsdraft_frame_export_region")

        try:
            view_layer.objects.active = active_before
        except Exception:
            _debug.swallowed("draft.tsdraft_frame_export_region")


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
            _debug.swallowed("draft.tsdraft_fit_region_to_bbox")

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
        _debug.swallowed("draft.tsdraft_fit_region_to_bbox")

    return changed


def tsdraft_enforce_quad_zoom_lock_once():
    """
    Global safety pass for drawing-mode Quad Views.
    Top / Front / Side may zoom out freely, but cannot zoom in far enough
    for the Bounding Box to exceed ~72% of any pane.
    """
    bbox_obj = bpy.data.objects.get(BBOX_NAME)
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
                region, rv3d = tsdraft_find_view_region(area, key)
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

                bpy.app.driver_namespace[ZOOM_SYNC_STATE_KEY] = state

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



def tsdraft_force_view_redraw(context, area):
    """Force Blender to finish drawing the newly switched view before screenshot."""
    try:
        area.tag_redraw()
    except Exception:
        _debug.swallowed("draft.tsdraft_force_view_redraw")

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
            VIEW_STATE_KEY,
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
    bpy.app.driver_namespace[VIEW_STATE_KEY] = state.get(
        "driver_view_state",
        None
    )



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
    original_display_state = tsdraft_capture_export_display_state(
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

    original_main_rv3d_state = tsdraft_capture_rv3d_state(
        getattr(space, "region_3d", None)
    )

    original_quad_states = []
    try:
        for rv in list(space.region_quadviews):
            original_quad_states.append(
                (rv, tsdraft_capture_rv3d_state(rv))
            )
    except Exception:
        _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

    source_obj = tsdraft_resolve_source_object(context)
    bbox_obj = bpy.data.objects.get(BBOX_NAME)

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
                _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # Printable styling.
        # 書き出し背景は白 / グリッド / 黒 / カスタムから選択。
        # 軸・原点・3Dカーソルはconfigure_drawing_view側で常に非表示。
        export_background = getattr(scene, "tsdraft_export_background", 'WHITE')
        export_custom_color = getattr(
            scene,
            "tsdraft_export_background_color",
            (1.0, 1.0, 1.0)
        )
        configure_drawing_view(
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
            raise RuntimeError(f"{tsdraft_svg_view_label(view_key)}のBounding Boxを投影できんかったンゴ")

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
                raise RuntimeError(f"{tsdraft_svg_view_label(view_key)}の実寸対応が取れんかったンゴ")

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

        data = bpy.app.driver_namespace.get(DATA_KEY, [])
        suppress_sheet_dim_text = bool(
            bpy.app.driver_namespace.get("TSDRAFT_SHEET_SUPPRESS_DIM_TEXT", False)
        )
        visible_axes = set() if (is_user_view or suppress_sheet_dim_text) else {
            axis for axis in tsdraft_svg_dimension_axes(view_key)
            if tsdraft_dimension_axis_enabled(scene, view_key, axis)
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

            label = get_dimension_text(scene, item)

            # Mirror draw_size_labels() font fitting exactly.
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
            _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

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
            _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # 撮影前のビュー方向・位置・倍率・背景・グリッド等へ完全復帰。
        try:
            tsdraft_restore_rv3d_state(
                getattr(space, "region_3d", None),
                original_main_rv3d_state
            )

            for rv, rv_state in original_quad_states:
                tsdraft_restore_rv3d_state(rv, rv_state)

            tsdraft_restore_export_display_state(
                space,
                scene,
                original_display_state
            )
        except Exception:
            _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        try:
            if Path(temp_full).exists():
                Path(temp_full).unlink()
        except Exception:
            _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        # 描画状態を更新
        try:
            area.tag_redraw()
        except Exception:
            _debug.swallowed("draft.tsdraft_export_viewport_exact_png")

        try:
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
            tsdraft_force_view_redraw(context, area)
        except Exception:
            redraw_viewports()




def dimension_length_mm(item):
    """Return stored dimension length in millimeters, including older data."""
    if "length_mm" in item:
        return float(item["length_mm"])

    # Backward compatibility for 2.4.x data stored only as "12.3 mm"
    text = str(item.get("text", "")).strip()
    try:
        return float(text.replace("mm", "").strip())
    except Exception:
        return 0.0


def format_dimension_value(length_mm, unit):
    if unit == 'M':
        return f"{length_mm / 1000.0:.3f} m"
    if unit == 'CM':
        return f"{length_mm / 10.0:.2f} cm"
    return f"{length_mm:.1f} mm"


def get_dimension_text(scene, item):
    return format_dimension_value(
        dimension_length_mm(item),
        scene.tsdraft_dimension_unit
    )


def get_axis_dimension_text(scene, axis, fallback=None):
    data = bpy.app.driver_namespace.get(DATA_KEY, [])
    for item in data:
        if item.get("axis") == axis:
            return get_dimension_text(scene, item)

    return fallback if fallback is not None else axis


# =========================================================
# 共通：再描画
# =========================================================

def update_bbox_visibility(self=None, context=None):
    bbox = bpy.data.objects.get(BBOX_NAME)

    # 実オブジェクトは常時隠し、ビュー別表示はGPU描画へ任せる
    if bbox is not None:
        bbox.hide_set(True)

    ensure_bbox_draw_handler()
    redraw_viewports()


def redraw_viewports(self=None, context=None):
    wm = bpy.context.window_manager
    if wm is None:
        return

    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# =========================================================
# サイズ計測：Draw Handler
# =========================================================


def tsdraft_view_px_per_mm(region, rv3d, bbox_obj, view_key, unit_scale):
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


def draw_size_labels():
    tsdraft_import_legacy_state()
    context = bpy.context

    if context.area is None or context.area.type != 'VIEW_3D':
        return

    region = context.region
    rv3d = context.region_data

    if region is None or rv3d is None:
        return

    scene = context.scene

    if not scene.tsdraft_show_dimensions:
        return

    namespace = bpy.app.driver_namespace

    # 三面図シートでは、寸法文字を最終シート上で直接描画する。
    # ビューポート文字をスクショへ焼くと回転・クロップが不安定なので、
    # シート用一時PNGでは文字だけ抑止する。
    if namespace.get("TSDRAFT_SHEET_SUPPRESS_DIM_TEXT", False):
        return

    source_name = namespace.get(SOURCE_KEY)
    data = namespace.get(DATA_KEY, [])

    if source_name and bpy.data.objects.get(source_name) is None:
        return

    bbox_obj = bpy.data.objects.get(BBOX_NAME)
    if bbox_obj is None or not data:
        return

    font_id = 0
    font_size = scene.tsdraft_font_size
    font_color = scene.tsdraft_font_color

    if getattr(scene, "tsdraft_dark_place", False):
        draw_color = DARK_TEXT
    else:
        draw_color = font_color

    blf.size(font_id, font_size)
    blf.color(
        font_id,
        draw_color[0],
        draw_color[1],
        draw_color[2],
        draw_color[3]
    )

    # Blenderの現在ビュー方向（画面から奥へ向かう方向）
    view_key, view_dir = get_view_key_from_rv3d(rv3d)

    explicit_user_mode = bool(
        getattr(scene, "tsdraft_user_view_mode", False)
    )

    # 「任意」ボタンを押した時だけuser個別設定を使用。
    # 通常の斜めデフォルトビューはグローバル寸法表示を使う。
    if explicit_user_mode:
        view_key = "user"

    axis_vectors = {
        "X": Vector((1.0, 0.0, 0.0)),
        "Y": Vector((0.0, 1.0, 0.0)),
        "Z": Vector((0.0, 0.0, 1.0)),
    }

    # -----------------------------------------------------
    # 三面図では、BBoxを毎回画面へ投影して寸法文字の基準位置を作る。
    # これにより、オブジェクト寸法変更・BBox追従・ビュー倍率変更があっても
    # 「上中央」「左中央」という画面上の関係を維持できる。
    # -----------------------------------------------------
    projected_bbox = []
    if view_key in {"top", "front", "side"}:
        try:
            bbox_world = [
                bbox_obj.matrix_world @ Vector(corner)
                for corner in bbox_obj.bound_box
            ]
            for world_co in bbox_world:
                p = view3d_utils.location_3d_to_region_2d(
                    region,
                    rv3d,
                    world_co
                )
                if p is not None:
                    projected_bbox.append(p)
        except Exception:
            projected_bbox = []

    bbox_screen = None
    if projected_bbox:
        bx = [p.x for p in projected_bbox]
        by = [p.y for p in projected_bbox]
        bbox_screen = (
            min(bx),
            max(bx),
            min(by),
            max(by),
        )

    # 各三面図で、画面横方向 / 縦方向に対応する寸法軸。
    # 横寸法はBOX上中央、縦寸法はBOX左中央へ自動配置。
    sheet_layout_mode = bool(
        bpy.app.driver_namespace.get("TSDRAFT_SHEET_LAYOUT_MODE", False)
    )

    if sheet_layout_mode:
        # 三面図シートでは、寸法を図同士の隙間ではなく外周側へ逃がす。
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
        # 単体ビューは従来どおり上＋左。
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

    for item in data:
        axis_name = item.get("axis", "X")

        if axis_name in axis_vectors:
            axis_world = bbox_obj.matrix_world.to_3x3() @ axis_vectors[axis_name]
            if axis_world.length > 0:
                axis_world.normalize()

                # ビュー方向と寸法軸がほぼ平行なら、その寸法は奥行きなので隠す。
                if abs(axis_world.dot(view_dir)) >= 0.965:
                    continue

        axis = axis_name.lower()

        # ビューごとの寸法表示ON/OFF。
        # 明示的な任意ビューだけ user 個別設定を使う。
        # 普通の斜めデフォルトビューはグローバル表示を優先する。
        if explicit_user_mode:
            if not getattr(scene, "tsdraft_show_dimensions_user", True):
                continue
        elif view_key != "user":
            if not getattr(
                scene,
                f"tsdraft_show_dimensions_{view_key}",
                True
            ):
                continue
            if not tsdraft_dimension_axis_enabled(scene, view_key, axis_name):
                continue

        unit_scale = scene.unit_settings.scale_length or 1.0
        px_per_mm = tsdraft_view_px_per_mm(
            region, rv3d, bbox_obj, view_key, unit_scale
        )

        # 既存の細かい位置調整は「自動配置位置からの追加オフセット」として残す。
        vx_mm = getattr(scene, f"tsdraft_{view_key}_{axis}_offset_x_mm", 0.0)
        vy_mm = getattr(scene, f"tsdraft_{view_key}_{axis}_offset_y_mm", 0.0)
        vx = vx_mm * px_per_mm
        vy = vy_mm * px_per_mm

        display_text = get_dimension_text(scene, item)

        requested_font_size = max(1, int(font_size))
        blf.size(font_id, requested_font_size)
        text_width, text_height = blf.dimensions(font_id, display_text)

        # pane端やBlender UI帯へ文字が食い込まないよう、
        # 実際の文字高さに応じて安全余白を広げる。
        safe_margin_x = max(
            20.0,
            float(text_height) * 0.75
        )
        safe_margin_y = max(
            32.0,
            float(text_height) * 1.15
        )

        available_w = max(
            1.0,
            float(region.width) - safe_margin_x * 2.0
        )
        available_h = max(
            1.0,
            float(region.height) - safe_margin_y * 2.0
        )

        # 極端な文字サイズだけ、paneに収まる範囲まで自動縮小。
        if text_width > available_w or text_height > available_h:
            scale = min(
                available_w / max(1.0, float(text_width)),
                available_h / max(1.0, float(text_height)),
                1.0
            )
            effective_size = max(8, int(requested_font_size * scale))
            blf.size(font_id, effective_size)
            text_width, text_height = blf.dimensions(font_id, display_text)

        layout_type = auto_axis_layout.get(view_key, {}).get(axis_name)

        rotate_vertical_text = layout_type in {"LEFT", "RIGHT"}

        if bbox_screen is not None and layout_type in {"TOP", "BOTTOM", "LEFT", "RIGHT"}:
            bbox_min_x, bbox_max_x, bbox_min_y, bbox_max_y = bbox_screen

            # 文字サイズに応じてBOXから少し離す。
            gap = max(10.0, float(text_height) * 0.35)

            if layout_type == "TOP":
                text_x = ((bbox_min_x + bbox_max_x) * 0.5) - (text_width * 0.5)
                text_y = bbox_max_y + gap

            elif layout_type == "BOTTOM":
                text_x = ((bbox_min_x + bbox_max_x) * 0.5) - (text_width * 0.5)
                text_y = bbox_min_y - gap - text_height

            elif layout_type == "RIGHT":
                # 90°回転後の見た目:
                # 横幅=text_height / 高さ=text_width
                text_x = bbox_max_x + gap
                text_y = ((bbox_min_y + bbox_max_y) * 0.5) - (text_width * 0.5)

            else:  # LEFT
                # 90°回転後の横幅は text_height。
                text_x = bbox_min_x - gap - text_height
                text_y = ((bbox_min_y + bbox_max_y) * 0.5) - (text_width * 0.5)

            text_x += vx
            text_y += vy

        else:
            # 任意ビューなどは従来の3Dアンカー方式を維持。
            pos_2d = view3d_utils.location_3d_to_region_2d(
                region,
                rv3d,
                item["location"]
            )

            if pos_2d is None:
                continue

            text_x = pos_2d.x - (text_width * 0.5) + vx
            text_y = pos_2d.y - (text_height * 0.5) + vy

        # 最後に画面内へクランプ。
        # 縦寸法は90°回転後の見た目サイズで判定する。
        visual_w = float(text_height) if rotate_vertical_text else float(text_width)
        visual_h = float(text_width) if rotate_vertical_text else float(text_height)

        max_x = max(
            safe_margin_x,
            float(region.width) - visual_w - safe_margin_x
        )
        max_y = max(
            safe_margin_y,
            float(region.height) - visual_h - safe_margin_y
        )

        text_x = min(max(text_x, safe_margin_x), max_x)
        text_y = min(max(text_y, safe_margin_y), max_y)

        if rotate_vertical_text:
            # BLFは指定位置を基準に反時計回りへ回転するため、
            # 見た目の左下が text_x/text_y に来るようXを右へずらす。
            try:
                blf.enable(font_id, blf.ROTATION)
                blf.rotation(font_id, math.radians(90.0))
                blf.position(
                    font_id,
                    text_x + float(text_height),
                    text_y,
                    0
                )
                blf.draw(font_id, display_text)
            finally:
                try:
                    blf.rotation(font_id, 0.0)
                    blf.disable(font_id, blf.ROTATION)
                except Exception:
                    _debug.swallowed("draft.draw_size_labels")
        else:
            blf.position(
                font_id,
                text_x,
                text_y,
                0
            )
            blf.draw(font_id, display_text)


def tsdraft_remove_legacy_draw_handlers():
    """Remove stale pre-Printable/old-version handlers that can double-draw labels."""
    namespace = bpy.app.driver_namespace

    for key in LEGACY_HANDLER_KEYS:
        handler = namespace.get(key)
        if handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
            except Exception:
                _debug.swallowed("draft.tsdraft_remove_legacy_draw_handlers")
            namespace[key] = None


def ensure_draw_handler():
    namespace = bpy.app.driver_namespace

    # First kill known stale handlers from older series/versions.
    tsdraft_remove_legacy_draw_handlers()

    old_handler = namespace.get(HANDLER_KEY)
    if old_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(old_handler, 'WINDOW')
        except Exception:
            _debug.swallowed("draft.ensure_draw_handler")

    handler = bpy.types.SpaceView3D.draw_handler_add(
        draw_size_labels,
        (),
        'WINDOW',
        'POST_PIXEL'
    )
    namespace[HANDLER_KEY] = handler


def remove_draw_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(HANDLER_KEY)

    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                handler,
                'WINDOW'
            )
        except Exception:
            _debug.swallowed("draft.remove_draw_handler")

    namespace[HANDLER_KEY] = None



def tsdraft_get_source_bounds_local(src, depsgraph):
    """Return evaluated local-space min/max corners for the source object."""
    obj_eval = src.evaluated_get(depsgraph)

    try:
        corners = [mathutils.Vector(corner) for corner in obj_eval.bound_box]
    except Exception:
        corners = []

    if not corners:
        return None

    # Invalid Blender bound_box can be all -1 values.
    xs = [v.x for v in corners]
    ys = [v.y for v in corners]
    zs = [v.z for v in corners]

    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = min(zs), max(zs)

    if (
        abs(xmax - xmin) < 1e-12
        and abs(ymax - ymin) < 1e-12
        and abs(zmax - zmin) < 1e-12
    ):
        return None

    return xmin, xmax, ymin, ymax, zmin, zmax


def tsdraft_auto_follow_signature(src, bounds):
    matrix_values = tuple(
        round(float(v), 8)
        for row in src.matrix_world
        for v in row
    )
    bounds_values = tuple(round(float(v), 8) for v in bounds)
    return bounds_values + matrix_values


def tsdraft_update_bbox_from_source(scene, depsgraph, force=False, request_redraw=True):
    """Update the existing BBox mesh + dimension data from its source.

    request_redraw=False is used by the depsgraph auto-follow handler because
    Blender is already redrawing for the source-object update. Avoiding an
    extra all-viewport tag_redraw here prevents feedback-like redraw storms
    while keeping the original, robust full BBox rebuild path intact.
    """
    namespace = bpy.app.driver_namespace

    if namespace.get(AUTO_FOLLOW_GUARD_KEY):
        return False

    if not getattr(scene, "tsdraft_auto_follow", True):
        return False

    source_name = namespace.get(SOURCE_KEY)
    if not source_name:
        # Fall back to the persistent name stored on the BBox object.
        bbox_existing = bpy.data.objects.get(BBOX_NAME)
        if bbox_existing is not None:
            source_name = bbox_existing.get("tsdraft_source_name")
            if source_name:
                namespace[SOURCE_KEY] = source_name

    if not source_name:
        return False

    src = bpy.data.objects.get(source_name)
    bbox_obj = bpy.data.objects.get(BBOX_NAME)

    if src is None or bbox_obj is None or src.type != 'MESH':
        return False

    bounds = tsdraft_get_source_bounds_local(src, depsgraph)
    if bounds is None:
        return False

    signature = tsdraft_auto_follow_signature(src, bounds)
    if not force and namespace.get(AUTO_FOLLOW_SIGNATURE_KEY) == signature:
        return False

    xmin, xmax, ymin, ymax, zmin, zmax = bounds

    verts = [
        (xmin, ymin, zmin),
        (xmax, ymin, zmin),
        (xmax, ymax, zmin),
        (xmin, ymax, zmin),
        (xmin, ymin, zmax),
        (xmax, ymin, zmax),
        (xmax, ymax, zmax),
        (xmin, ymax, zmax),
    ]

    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]

    namespace[AUTO_FOLLOW_GUARD_KEY] = True

    try:
        mesh = bbox_obj.data

        # Rebuild only the tiny 8-vertex box mesh.
        mesh.clear_geometry()
        mesh.from_pydata(verts, edges, [])
        mesh.update()

        bbox_obj.matrix_world = src.matrix_world.copy()
        bbox_obj["tsdraft_source_name"] = src.name

        unit_scale = scene.unit_settings.scale_length or 1.0

        # Match the original anchor convention:
        # min X, max Y, max Z
        anchor_local = mathutils.Vector((xmin, ymax, zmax))

        neighbors = (
            ("X", mathutils.Vector((xmax, ymax, zmax))),
            ("Y", mathutils.Vector((xmin, ymin, zmax))),
            ("Z", mathutils.Vector((xmin, ymax, zmin))),
        )

        dimension_data = []

        for axis, other_local in neighbors:
            world_a = bbox_obj.matrix_world @ anchor_local
            world_b = bbox_obj.matrix_world @ other_local

            length_world = (world_b - world_a).length
            length_mm = length_world * unit_scale * 1000.0
            midpoint = (world_a + world_b) * 0.5

            dimension_data.append({
                "location": midpoint,
                "text": f"{length_mm:.1f} mm",
                "length_mm": length_mm,
                "axis": axis,
                "world_a": tuple(world_a),
                "world_b": tuple(world_b),
            })

        namespace[DATA_KEY] = dimension_data
        namespace[AUTO_FOLLOW_SIGNATURE_KEY] = signature

    finally:
        namespace[AUTO_FOLLOW_GUARD_KEY] = False

    if request_redraw:
        redraw_viewports()
    return True


@persistent
def size_bbox_auto_follow_handler(scene, depsgraph):
    try:
        # The depsgraph event itself already schedules viewport redraws.
        # Do not force-redraw every VIEW_3D area again from inside the handler.
        tsdraft_update_bbox_from_source(scene, depsgraph, force=False, request_redraw=False)
    except Exception:
        # Never let the measurement helper break Blender's depsgraph.
        pass


# =========================================================
# サイズ計測：元オブジェクト削除監視
# =========================================================

@persistent
def size_bbox_cleanup_handler(scene, depsgraph):
    namespace = bpy.app.driver_namespace
    source_name = namespace.get(SOURCE_KEY)

    if not source_name:
        return

    source_exists = bpy.data.objects.get(source_name) is not None
    bbox_exists = bpy.data.objects.get(BBOX_NAME) is not None

    if not source_exists:
        bbox = bpy.data.objects.get(BBOX_NAME)

        if bbox is not None:
            bpy.data.objects.remove(bbox, do_unlink=True)

        namespace[DATA_KEY] = []
        namespace[SOURCE_KEY] = None
        redraw_viewports()
        return

    if not bbox_exists:
        namespace[DATA_KEY] = []
        namespace[SOURCE_KEY] = None
        redraw_viewports()


def ensure_cleanup_handler():
    if size_bbox_cleanup_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(
            size_bbox_cleanup_handler
        )

    if size_bbox_auto_follow_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(
            size_bbox_auto_follow_handler
        )


def remove_cleanup_handler():
    if size_bbox_cleanup_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(
            size_bbox_cleanup_handler
        )

    if size_bbox_auto_follow_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(
            size_bbox_auto_follow_handler
        )


# =========================================================
# サイズ計測：BOX＋寸法作成
# =========================================================


class TSDRAFT_OT_toggle_size_overlay(bpy.types.Operator):
    bl_idname = "truescale_draft.toggle_size_overlay"
    bl_label = "BOX＋寸法 表示切替"
    bl_description = "BOXと寸法を削除せず、一時的に表示／非表示を切り替えます"

    def execute(self, context):
        scene = context.scene
        bbox_obj = bpy.data.objects.get(BBOX_NAME)

        if bbox_obj is None:
            self.report({'WARNING'}, "先にBOX＋寸法を作成してクレメンス")
            return {'CANCELLED'}

        # 両方表示中なら隠す。それ以外ならまとめて表示。
        currently_visible = (
            bool(getattr(scene, "tsdraft_show_bbox", True))
            and bool(getattr(scene, "tsdraft_show_dimensions", True))
        )

        new_state = not currently_visible

        if new_state:
            # 再表示時はグローバル表示を先にONにしてから、
            # 各ビュー個別フラグも確実にONへ戻す。
            scene.tsdraft_show_bbox = True
            scene.tsdraft_show_dimensions = True

            scene.tsdraft_show_bbox_top = True
            scene.tsdraft_show_bbox_front = True
            scene.tsdraft_show_bbox_side = True
            scene.tsdraft_show_bbox_user = True

            scene.tsdraft_show_dimensions_top = True
            scene.tsdraft_show_dimensions_front = True
            scene.tsdraft_show_dimensions_side = True
            scene.tsdraft_show_dimensions_user = True
        else:
            scene.tsdraft_show_bbox = False
            scene.tsdraft_show_dimensions = False

        redraw_viewports()
        return {'FINISHED'}



class TSDRAFT_OT_make_size_bbox(bpy.types.Operator):
    bl_idname = "truescale_draft.make_size_bbox"
    bl_label = "BOX＋寸法を作成"
    bl_description = "選択オブジェクトからサイズ用Bounding Boxを作成し、3辺の寸法を表示します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        src = getattr(context, "active_object", None)

        # 選択オブジェクトが無い、または既に削除済みなら安全に中止
        if src is None:
            self.report({'ERROR'}, "オブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        # Blender側の参照が途中で無効化されていないかも確認
        if bpy.data.objects.get(src.name) is None:
            self.report({'ERROR'}, "選択オブジェクトが見つからんかったンゴ")
            return {'CANCELLED'}

        if src.type != 'MESH':
            self.report({'ERROR'}, "メッシュオブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        if src.name == BBOX_NAME:
            self.report({'ERROR'}, "元オブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        # mode属性へ触る前に参照を再確認
        src = bpy.data.objects.get(src.name)
        if src is None:
            self.report({'ERROR'}, "選択オブジェクトが途中で消えたンゴ")
            return {'CANCELLED'}

        src_mode = getattr(src, "mode", 'OBJECT')
        if src_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                self.report({'ERROR'}, "Object Modeへ切り替えられんかったンゴ")
                return {'CANCELLED'}

        namespace = bpy.app.driver_namespace
        namespace[SOURCE_KEY] = src.name

        old_bbox = bpy.data.objects.get(BBOX_NAME)
        if old_bbox is not None:
            bpy.data.objects.remove(old_bbox, do_unlink=True)

        namespace[DATA_KEY] = []

        bbox_obj = src.copy()

        if src.data:
            bbox_obj.data = src.data.copy()

        if src.users_collection:
            src.users_collection[0].objects.link(bbox_obj)
        else:
            context.collection.objects.link(bbox_obj)

        bpy.ops.object.select_all(action='DESELECT')
        bbox_obj.select_set(True)
        context.view_layer.objects.active = bbox_obj

        node_group = bpy.data.node_groups.new(
            name="AUTO_BoundingBox",
            type="GeometryNodeTree"
        )

        node_group.interface.new_socket(
            name="Geometry",
            in_out='INPUT',
            socket_type='NodeSocketGeometry'
        )

        node_group.interface.new_socket(
            name="Geometry",
            in_out='OUTPUT',
            socket_type='NodeSocketGeometry'
        )

        nodes = node_group.nodes
        links = node_group.links

        input_node = nodes.new("NodeGroupInput")
        bbox_node = nodes.new("GeometryNodeBoundBox")
        output_node = nodes.new("NodeGroupOutput")

        links.new(
            input_node.outputs["Geometry"],
            bbox_node.inputs["Geometry"]
        )

        links.new(
            bbox_node.outputs["Bounding Box"],
            output_node.inputs["Geometry"]
        )

        modifier = bbox_obj.modifiers.new(
            name="AUTO Bounding Box",
            type='NODES'
        )

        modifier.node_group = node_group

        bpy.ops.object.modifier_apply(
            modifier=modifier.name
        )

        if node_group.users == 0:
            bpy.data.node_groups.remove(node_group)

        bbox_obj.name = BBOX_NAME
        bbox_obj["tsdraft_source_name"] = src.name
        bbox_obj.display_type = 'BOUNDS'
        bbox_obj.display_bounds_type = 'BOX'

        # ビュー別表示に対応するため、実オブジェクトは常時非表示
        bbox_obj.hide_set(True)
        ensure_bbox_draw_handler()

        mesh = bbox_obj.data

        anchor = min(
            mesh.vertices,
            key=lambda v: (
                v.co.x,
                -v.co.y,
                -v.co.z
            )
        )

        neighbor_indices = []

        for edge in mesh.edges:
            if anchor.index in edge.vertices:
                for vertex_index in edge.vertices:
                    if vertex_index != anchor.index:
                        neighbor_indices.append(vertex_index)

        neighbor_indices = list(dict.fromkeys(neighbor_indices))

        if len(neighbor_indices) != 3:
            self.report({'ERROR'}, "隣接頂点が3個にならんかったンゴ")
            return {'CANCELLED'}

        dimension_data = []
        anchor_co = mesh.vertices[anchor.index].co.copy()

        unit_scale = context.scene.unit_settings.scale_length
        if unit_scale == 0:
            unit_scale = 1.0

        for index in neighbor_indices:
            other_co = mesh.vertices[index].co.copy()

            world_a = bbox_obj.matrix_world @ anchor_co
            world_b = bbox_obj.matrix_world @ other_co

            length_world = (world_b - world_a).length
            length_mm = length_world * unit_scale * 1000.0

            midpoint = (world_a + world_b) * 0.5

            local_delta = other_co - anchor_co
            values = (abs(local_delta.x), abs(local_delta.y), abs(local_delta.z))
            axis = ("X", "Y", "Z")[values.index(max(values))]

            dimension_data.append({
                "location": midpoint,
                "text": f"{length_mm:.1f} mm",
                "length_mm": length_mm,
                "axis": axis,
                "world_a": tuple(world_a),
                "world_b": tuple(world_b)
            })

        namespace[DATA_KEY] = dimension_data

        # 作成直後は「出た！」が分かるよう、通常斜めビューで必ず表示。
        # 「任意」専用モードはいったん解除する。
        context.scene.tsdraft_user_view_mode = False

        # BOXと寸法を必ず表示へリセット
        context.scene.tsdraft_show_dimensions = True
        context.scene.tsdraft_show_dimensions_top = True
        context.scene.tsdraft_show_dimensions_front = True
        context.scene.tsdraft_show_dimensions_side = True
        context.scene.tsdraft_show_dimensions_user = False

        context.scene.tsdraft_show_bbox = True
        context.scene.tsdraft_frame_mode = 'BOX'
        context.scene.tsdraft_show_bbox_top = True
        context.scene.tsdraft_show_bbox_front = True
        context.scene.tsdraft_show_bbox_side = True
        context.scene.tsdraft_show_bbox_user = False

        ensure_draw_handler()
        ensure_cleanup_handler()

        # 作成ボタンを押した瞬間は、必ずBOX＋寸法が見える状態へ。
        # 三面は表示ON、任意ビューだけはパース確認用としてOFF。



        # 枠そのものが「なし」になっていた場合も作成時はBOXへ戻す。

        ensure_bbox_draw_handler()
        redraw_viewports()

        # 自動追従の初期署名をここで作る
        try:
            tsdraft_update_bbox_from_source(
                context.scene,
                context.evaluated_depsgraph_get(),
                force=True
            )
        except Exception:
            _debug.swallowed("draft.TSDRAFT_OT_make_size_bbox.execute")

        bpy.ops.object.select_all(action='DESELECT')
        bbox_obj.select_set(True)
        context.view_layer.objects.active = bbox_obj

        redraw_viewports()

        self.report({'INFO'}, "Bounding Box＋寸法表示、完成や！")
        return {'FINISHED'}


class TSDRAFT_OT_delete_bbox(bpy.types.Operator):
    bl_idname = "truescale_draft.delete_bbox"
    bl_label = "BOX＋寸法を削除"
    bl_description = "サイズ用Bounding Boxと寸法表示を削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        namespace = bpy.app.driver_namespace
        namespace[DATA_KEY] = []
        namespace[SOURCE_KEY] = None
        namespace[AUTO_FOLLOW_SIGNATURE_KEY] = None

        bbox = bpy.data.objects.get(BBOX_NAME)
        if bbox is not None:
            bpy.data.objects.remove(bbox, do_unlink=True)

        # BOX＋寸法を消したら、そのまま通常表示へ帰還
        if context.area is not None and context.area.type == 'VIEW_3D':
            try:
                bpy.ops.truescale_draft.restore_view()
            except Exception:
                _debug.swallowed("draft.TSDRAFT_OT_delete_bbox.execute")

        redraw_viewports()
        return {'FINISHED'}





def tsdraft_sheet_scale_denominator(scene):
    scale_key = getattr(scene, 'tsdraft_sheet_scale', '1_1')
    fixed = {
        '1_1': 1.0,
        '1_2': 2.0,
        '1_5': 5.0,
        '1_10': 10.0,
    }
    if scale_key == 'CUSTOM':
        return max(1.0, float(getattr(scene, 'tsdraft_sheet_custom_scale', 1.0)))
    return fixed.get(scale_key, 1.0)


def tsdraft_sheet_layout_aligned(view_data, page_w, page_h, margin=12.0, gutter=10.0, footer=12.0):
    """
    Conventional three-view layout using BBox FRAME positions as the alignment reference:
      TOP directly above FRONT, same frame left/right.
      SIDE directly left of FRONT, same frame top/bottom.
    Cropped PNG margins are allowed to differ without breaking projection alignment.
    """
    top = view_data['top']
    front = view_data['front']
    side = view_data['side']

    # Frame-space origin: front frame top-left = (0, top frame height + gutter).
    front_frame_x = 0.0
    front_frame_y = top['frame_h_mm'] + gutter
    top_frame_x = 0.0
    top_frame_y = 0.0
    side_frame_x = -(side['frame_w_mm'] + gutter)
    side_frame_y = front_frame_y

    frame_origins = {
        'top': (top_frame_x, top_frame_y),
        'front': (front_frame_x, front_frame_y),
        'side': (side_frame_x, side_frame_y),
    }

    # Convert frame origins to image top-left positions using each image's crop margins.
    raw = {}
    for key, item in view_data.items():
        fx, fy = frame_origins[key]
        raw[key] = (
            fx - item['left_mm'],
            fy - item['top_mm'],
        )

    min_x = min(raw[k][0] for k in raw)
    min_y = min(raw[k][1] for k in raw)
    max_x = max(raw[k][0] + view_data[k]['image_w_mm'] for k in raw)
    max_y = max(raw[k][1] + view_data[k]['image_h_mm'] for k in raw)

    content_w = max_x - min_x
    content_h = max_y - min_y

    avail_w = page_w - margin * 2.0
    avail_h = page_h - margin * 2.0 - footer
    if content_w > avail_w + 1e-6 or content_h > avail_h + 1e-6:
        return None, (content_w, content_h + footer)

    offset_x = (page_w - content_w) * 0.5 - min_x
    offset_y = margin + (avail_h - content_h) * 0.5 - min_y

    positions = {
        key: (raw[key][0] + offset_x, raw[key][1] + offset_y)
        for key in raw
    }
    return positions, (content_w, content_h + footer)


_TSDRAFT_BITMAP_FONT = {
    'S': ("11111","10000","10000","11111","00001","00001","11111"),
    'C': ("11111","10000","10000","10000","10000","10000","11111"),
    'A': ("01110","10001","10001","11111","10001","10001","10001"),
    'L': ("10000","10000","10000","10000","10000","10000","11111"),
    'E': ("11111","10000","10000","11110","10000","10000","11111"),
    '0': ("01110","10001","10011","10101","11001","10001","01110"),
    '1': ("00100","01100","00100","00100","00100","00100","01110"),
    '2': ("01110","10001","00001","00010","00100","01000","11111"),
    '3': ("11110","00001","00001","01110","00001","00001","11110"),
    '4': ("00010","00110","01010","10010","11111","00010","00010"),
    '5': ("11111","10000","10000","11110","00001","00001","11110"),
    '6': ("01110","10000","10000","11110","10001","10001","01110"),
    '7': ("11111","00001","00010","00100","01000","01000","01000"),
    '8': ("01110","10001","10001","01110","10001","10001","01110"),
    '9': ("01110","10001","10001","01111","00001","00001","01110"),
    ':': ("00000","00100","00100","00000","00100","00100","00000"),
    '.': ("00000","00000","00000","00000","00000","00100","00100"),
    ' ': ("00000","00000","00000","00000","00000","00000","00000"),
}


def tsdraft_draw_bitmap_text(canvas, text, x, y_top, scale=3):
    """Tiny dependency-free black bitmap label, top-left coordinate."""
    h, w, _ = canvas.shape
    x = int(x)
    y_top = int(y_top)
    cursor = x
    for ch in text.upper():
        glyph = _TSDRAFT_BITMAP_FONT.get(ch, _TSDRAFT_BITMAP_FONT[' '])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != '1':
                    continue
                x0 = cursor + gx * scale
                y0_top = y_top + gy * scale
                # Canvas uses Blender bottom-up rows.
                y0 = h - (y0_top + scale)
                x1 = min(w, x0 + scale)
                y1 = min(h, y0 + scale)
                if x1 > x0 and y1 > y0 and x0 >= 0 and y0 >= 0:
                    canvas[y0:y1, x0:x1, 0:3] = 0.0
                    canvas[y0:y1, x0:x1, 3] = 1.0
        cursor += 6 * scale



def tsdraft_sheet_text_rgba(text, font_size_px, color_rgba):
    """Render one text label to an RGBA numpy array using Blender's default font."""
    import numpy as np
    import imbuf

    font_id = 0
    font_size_px = max(8, int(round(font_size_px)))
    blf.size(font_id, font_size_px)
    text_w, text_h = blf.dimensions(font_id, text)

    pad = max(4, int(round(font_size_px * 0.28)))
    width = max(1, int(math.ceil(text_w)) + pad * 2)
    height = max(1, int(math.ceil(text_h)) + pad * 2)

    ib = imbuf.new((width, height), planes=32, buffer_type='BYTE')
    try:
        # Explicit transparent background.
        with ib.with_buffer(write=True) as buf:
            flat = buf.cast('B')
            flat[:] = bytes(len(flat))

        blf.size(font_id, font_size_px)
        blf.color(
            font_id,
            float(color_rgba[0]),
            float(color_rgba[1]),
            float(color_rgba[2]),
            float(color_rgba[3]),
        )
        blf.position(font_id, pad, pad, 0)

        with blf.bind_imbuf(font_id, ib, display_name="sRGB"):
            blf.draw_buffer(font_id, text)

        with ib.with_buffer() as buf:
            arr = np.asarray(buf, dtype=np.uint8).copy()
        return arr.astype(np.float32) / 255.0
    finally:
        try:
            ib.free()
        except Exception:
            _debug.swallowed("draft.tsdraft_sheet_text_rgba")


def tsdraft_sheet_alpha_blit(canvas, rgba, x0, y0):
    """Alpha-composite RGBA onto Blender-style bottom-up float canvas."""
    x0 = int(round(x0))
    y0 = int(round(y0))
    h, w = rgba.shape[0], rgba.shape[1]
    ch, cw = canvas.shape[0], canvas.shape[1]

    sx0 = 0
    sy0 = 0
    sx1 = w
    sy1 = h

    if x0 < 0:
        sx0 = -x0
        x0 = 0
    if y0 < 0:
        sy0 = -y0
        y0 = 0

    x1 = min(cw, x0 + (sx1 - sx0))
    y1 = min(ch, y0 + (sy1 - sy0))
    if x1 <= x0 or y1 <= y0:
        return

    sx1 = sx0 + (x1 - x0)
    sy1 = sy0 + (y1 - y0)

    src = rgba[sy0:sy1, sx0:sx1, :]
    dst = canvas[y0:y1, x0:x1, :]

    alpha = src[..., 3:4]
    dst[..., :3] = src[..., :3] * alpha + dst[..., :3] * (1.0 - alpha)
    dst[..., 3:4] = 1.0


def tsdraft_export_canvas_rgba(scene):
    mode = getattr(scene, 'tsdraft_export_background', 'WHITE')
    custom = getattr(scene, 'tsdraft_export_background_color', (1.0, 1.0, 1.0))
    rgb = tsdraft_background_color(mode, custom)
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0)


def tsdraft_new_background_canvas(np, height, width, rgba):
    canvas = np.empty((int(height), int(width), 4), dtype=np.float32)
    canvas[..., 0] = rgba[0]
    canvas[..., 1] = rgba[1]
    canvas[..., 2] = rgba[2]
    canvas[..., 3] = rgba[3]
    return canvas


def tsdraft_sheet_draw_dimension_labels(canvas, scene, positions, view_data, sheet_dpi, page_h_mm):
    """
    Draw sheet dimensions directly on the final raster.
    Horizontal dimensions remain horizontal.
    Vertical dimensions are raster-rotated 90 degrees, so no viewport BLF
    rotation/cropping ambiguity can occur.
    """
    import numpy as np

    namespace = bpy.app.driver_namespace
    data = namespace.get(DATA_KEY, [])
    if not data:
        return

    axis_text = {}
    for item in data:
        axis = str(item.get("axis", "")).upper()
        if axis in {"X", "Y", "Z"}:
            axis_text[axis] = get_dimension_text(scene, item)

    if not axis_text:
        return

    # 図面シート文字は物理mm基準で描くが、UIの「文字サイズ」に素直に追従させる。
    # 20px -> 約3.0mm を基準に比例。極端値だけ広めに安全制限する。
    design_px = max(4.0, float(getattr(scene, "tsdraft_font_size", 20)))
    font_mm = min(12.0, max(1.2, design_px * 0.15))
    font_px = max(6.0, font_mm / 25.4 * float(sheet_dpi))

    color = getattr(scene, "tsdraft_font_color", (0.05, 0.05, 0.05, 1.0))
    gap_mm = max(1.2, font_mm * 0.42)

    # Same outside-edge convention as the sheet layout.
    specs = {
        "top":   (("X", "TOP"),    ("Y", "LEFT")),
        "front": (("X", "BOTTOM"), ("Z", "RIGHT")),
        "side":  (("Y", "BOTTOM"), ("Z", "LEFT")),
    }

    for view_key, dim_specs in specs.items():
        if view_key not in positions or view_key not in view_data:
            continue

        image_x_mm, image_y_top_mm = positions[view_key]
        vd = view_data[view_key]

        frame_left_mm = image_x_mm + vd["left_mm"]
        frame_top_mm = image_y_top_mm + vd["top_mm"]
        frame_right_mm = frame_left_mm + vd["frame_w_mm"]
        frame_bottom_mm = frame_top_mm + vd["frame_h_mm"]

        for axis, side in dim_specs:
            text = axis_text.get(axis)
            if not text:
                continue

            label = tsdraft_sheet_text_rgba(text, font_px, color)

            if side in {"LEFT", "RIGHT"}:
                # np.rot90 rotates the actual glyph raster. This is deterministic
                # even if viewport BLF rotation behaves differently by version.
                label = np.rot90(label, k=1).copy()

            label_h, label_w = label.shape[0], label.shape[1]
            label_w_mm = label_w / sheet_dpi * 25.4
            label_h_mm = label_h / sheet_dpi * 25.4

            if side == "TOP":
                x_mm = (frame_left_mm + frame_right_mm) * 0.5 - label_w_mm * 0.5
                y_top_mm = frame_top_mm - gap_mm - label_h_mm

            elif side == "BOTTOM":
                x_mm = (frame_left_mm + frame_right_mm) * 0.5 - label_w_mm * 0.5
                y_top_mm = frame_bottom_mm + gap_mm

            elif side == "LEFT":
                x_mm = frame_left_mm - gap_mm - label_w_mm
                y_top_mm = (frame_top_mm + frame_bottom_mm) * 0.5 - label_h_mm * 0.5

            else:  # RIGHT
                x_mm = frame_right_mm + gap_mm
                y_top_mm = (frame_top_mm + frame_bottom_mm) * 0.5 - label_h_mm * 0.5

            x_px = x_mm / 25.4 * sheet_dpi
            # Convert top-down sheet coordinate to bottom-up canvas coordinate.
            y_px = (page_h_mm - y_top_mm - label_h_mm) / 25.4 * sheet_dpi

            tsdraft_sheet_alpha_blit(canvas, label, x_px, y_px)


def tsdraft_add_dimension_labels_to_exact_png(scene, filepath, view_key, info):
    """
    Add dimension text to an already exported exact-size PNG.
    Used by '3面まとめて書き出し' so vertical dimension text is rotated
    and the canvas can grow to avoid clipping.
    """
    import numpy as np

    axis_map = {
        "top": ("X", "Y"),
        "front": ("X", "Z"),
        "side": ("Y", "Z"),
    }
    axes = axis_map.get(view_key)
    if not axes:
        return

    data = bpy.app.driver_namespace.get(DATA_KEY, [])
    axis_text = {}
    for item in data:
        axis = str(item.get("axis", "")).upper()
        if axis in axes:
            axis_text[axis] = get_dimension_text(scene, item)

    horizontal_text = axis_text.get(axes[0])
    vertical_text = axis_text.get(axes[1])
    if not horizontal_text and not vertical_text:
        return

    image = bpy.data.images.load(filepath, check_existing=False)
    try:
        src_w, src_h = int(image.size[0]), int(image.size[1])
        src = np.empty(src_w * src_h * 4, dtype=np.float32)
        image.pixels.foreach_get(src)
        src = src.reshape((src_h, src_w, 4))

        bbox_left = float(info.get("bbox_left_px", 0.0))
        bbox_right = float(src_w) - float(info.get("bbox_right_px", 0.0))
        bbox_bottom = float(info.get("bbox_bottom_px", 0.0))
        bbox_top = float(src_h) - float(info.get("bbox_top_px", 0.0))

        font_px = max(4.0, float(getattr(scene, "tsdraft_font_size", 20)))
        color = getattr(scene, "tsdraft_font_color", (0.05, 0.05, 0.05, 1.0))
        gap_px = max(8.0, font_px * 0.40)
        outer_pad = max(8.0, font_px * 0.35)

        labels = []

        if horizontal_text:
            label = tsdraft_sheet_text_rgba(horizontal_text, font_px, color)
            lh, lw = label.shape[0], label.shape[1]
            x = (bbox_left + bbox_right) * 0.5 - lw * 0.5
            y = bbox_top + gap_px
            labels.append((label, x, y))

        if vertical_text:
            label = tsdraft_sheet_text_rgba(vertical_text, font_px, color)
            # Actual raster rotation, independent of viewport BLF rotation.
            label = np.rot90(label, k=1).copy()
            lh, lw = label.shape[0], label.shape[1]
            x = bbox_left - gap_px - lw
            y = (bbox_bottom + bbox_top) * 0.5 - lh * 0.5
            labels.append((label, x, y))

        min_x = min([0.0] + [x for _lab, x, _y in labels]) - outer_pad
        min_y = min([0.0] + [y for _lab, _x, y in labels]) - outer_pad
        max_x = max([float(src_w)] + [x + lab.shape[1] for lab, x, _y in labels]) + outer_pad
        max_y = max([float(src_h)] + [y + lab.shape[0] for lab, _x, y in labels]) + outer_pad

        shift_x = max(0, int(math.ceil(-min_x)))
        shift_y = max(0, int(math.ceil(-min_y)))
        out_w = max(1, int(math.ceil(max_x + shift_x)))
        out_h = max(1, int(math.ceil(max_y + shift_y)))

        canvas = tsdraft_new_background_canvas(
            np,
            out_h,
            out_w,
            tsdraft_export_canvas_rgba(scene)
        )
        canvas[shift_y:shift_y + src_h, shift_x:shift_x + src_w, :] = src

        for label, x, y in labels:
            tsdraft_sheet_alpha_blit(
                canvas,
                label,
                x + shift_x,
                y + shift_y
            )

        out_image = bpy.data.images.new(
            name="TSDRAFT_Batch_Dimensioned_View",
            width=out_w,
            height=out_h,
            alpha=False,
            float_buffer=False
        )
        try:
            out_image.pixels.foreach_set(canvas.ravel())
            out_image.file_format = 'PNG'
            out_image.filepath_raw = filepath
            out_image.save()
        finally:
            bpy.data.images.remove(out_image)

        # Expanding the canvas must not change the BBox's physical scale.
        if not tsdraft_patch_png_dpi(filepath, float(info["dpi"])):
            raise RuntimeError("まとめ書き出しPNGへDPI情報を書き戻せんかったンゴ")

    finally:
        try:
            bpy.data.images.remove(image)
        except Exception:
            _debug.swallowed("draft.tsdraft_add_dimension_labels_to_exact_png")

def tsdraft_make_three_view_sheet_png(scene, filepath, exported):
    """Compose three exact-size PNGs into a clean, aligned, print-scale PNG sheet."""
    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError("図面シートPNGの作成に必要なNumPyを読み込めんかったンゴ") from exc

    denominator = tsdraft_sheet_scale_denominator(scene)
    paper = getattr(scene, 'tsdraft_sheet_paper_size', 'A4')
    orientation = getattr(scene, 'tsdraft_sheet_orientation', 'AUTO')

    paper_sizes = {
        'A4': (210.0, 297.0),
        'A3': (297.0, 420.0),
        'A2': (420.0, 594.0),
        'A1': (594.0, 841.0),
        'A0': (841.0, 1189.0),
    }
    if paper == 'CUSTOM':
        base_w = max(10.0, float(getattr(scene, 'tsdraft_sheet_custom_width_mm', 210.0)))
        base_h = max(10.0, float(getattr(scene, 'tsdraft_sheet_custom_height_mm', 297.0)))
    else:
        base_w, base_h = paper_sizes.get(paper, (210.0, 297.0))

    view_data = {}
    source_effective_dpis = []
    for key, item in exported.items():
        info = item['info']
        src_dpi = max(1.0, float(info['dpi']))
        mm_per_px = 25.4 / src_dpi / denominator

        frame_w = float(info['bbox_width_mm']) / denominator
        frame_h = float(info['bbox_height_mm']) / denominator

        view_data[key] = {
            'image_w_mm': float(info['image_w_px']) * mm_per_px,
            'image_h_mm': float(info['image_h_px']) * mm_per_px,
            'frame_w_mm': frame_w,
            'frame_h_mm': frame_h,
            'left_mm': float(info.get('bbox_left_px', 0.0)) * mm_per_px,
            'right_mm': float(info.get('bbox_right_px', 0.0)) * mm_per_px,
            'top_mm': float(info.get('bbox_top_px', 0.0)) * mm_per_px,
            'bottom_mm': float(info.get('bbox_bottom_px', 0.0)) * mm_per_px,
        }
        source_effective_dpis.append(src_dpi * denominator)

    if orientation == 'AUTO':
        candidates = [
            ('PORTRAIT', min(base_w, base_h), max(base_w, base_h)),
            ('LANDSCAPE', max(base_w, base_h), min(base_w, base_h)),
        ]
    elif orientation == 'LANDSCAPE':
        candidates = [('LANDSCAPE', max(base_w, base_h), min(base_w, base_h))]
    else:
        candidates = [('PORTRAIT', min(base_w, base_h), max(base_w, base_h))]

    chosen = None
    required = None
    for orient_name, page_w, page_h in candidates:
        positions, needed = tsdraft_sheet_layout_aligned(view_data, page_w, page_h)
        if positions is not None:
            chosen = (orient_name, page_w, page_h, positions)
            break
        required = needed

    if chosen is None:
        need_w, need_h = required or (0.0, 0.0)
        raise RuntimeError(
            f'{paper}・1:{denominator:g}では三面図が収まらんかったンゴ '
            f'（必要目安 {need_w + 24.0:.1f}×{need_h + 24.0:.1f}mm）。'
            '用紙を大きくするか縮率を下げてクレメンス'
        )

    orient_name, page_w, page_h, positions = chosen

    # 図面シートは印刷品質を優先。
    # 元ビューポートのDPIでページ全体を低解像度化しない。
    # A4/A3/A2は最大300dpi、A1/A0/巨大カスタムだけ
    # 約36MPを上限に自動でDPIを下げてメモリを守る。
    page_area_in2 = max(1e-6, (page_w / 25.4) * (page_h / 25.4))
    memory_safe_dpi = math.sqrt(36_000_000.0 / page_area_in2)
    sheet_dpi = max(96.0, min(300.0, memory_safe_dpi))

    page_px_w = max(1, int(round(page_w / 25.4 * sheet_dpi)))
    page_px_h = max(1, int(round(page_h / 25.4 * sheet_dpi)))
    canvas = tsdraft_new_background_canvas(
        np,
        page_px_h,
        page_px_w,
        tsdraft_export_canvas_rgba(scene)
    )

    loaded_images = []
    try:
        for key in ('top', 'side', 'front'):
            item = exported[key]
            x_mm, y_top_mm = positions[key]
            w_mm = view_data[key]['image_w_mm']
            h_mm = view_data[key]['image_h_mm']

            dst_w = max(1, int(round(w_mm / 25.4 * sheet_dpi)))
            dst_h = max(1, int(round(h_mm / 25.4 * sheet_dpi)))

            image = bpy.data.images.load(item['path'], check_existing=False)
            loaded_images.append(image)
            image.scale(dst_w, dst_h)

            src = np.empty(dst_w * dst_h * 4, dtype=np.float32)
            image.pixels.foreach_get(src)
            src = src.reshape((dst_h, dst_w, 4))

            x0 = int(round(x_mm / 25.4 * sheet_dpi))
            y0 = int(round((page_h - y_top_mm - h_mm) / 25.4 * sheet_dpi))
            x1 = min(page_px_w, x0 + dst_w)
            y1 = min(page_px_h, y0 + dst_h)
            if x1 <= x0 or y1 <= y0:
                continue

            src = src[:y1 - y0, :x1 - x0, :]
            dst = canvas[y0:y1, x0:x1, :]
            alpha = src[..., 3:4]
            dst[..., :3] = src[..., :3] * alpha + dst[..., :3] * (1.0 - alpha)
            dst[..., 3:4] = 1.0

        # Sheet dimensions are drawn here, not baked into the viewport screenshots.
        tsdraft_sheet_draw_dimension_labels(
            canvas,
            scene,
            positions,
            view_data,
            sheet_dpi,
            page_h
        )

        # Keep scale notation on the actual sheet.
        label = f"SCALE 1:{denominator:g}"
        label_scale = max(2, int(round(sheet_dpi / 100.0)))
        label_x = int(round(12.0 / 25.4 * sheet_dpi))
        label_y_top = int(round((page_h - 10.0) / 25.4 * sheet_dpi))
        tsdraft_draw_bitmap_text(canvas, label, label_x, label_y_top, label_scale)

        out_image = bpy.data.images.new(
            name="TSDRAFT_Three_View_Sheet",
            width=page_px_w,
            height=page_px_h,
            alpha=False,
            float_buffer=False
        )
        try:
            out_image.pixels.foreach_set(canvas.ravel())
            out_image.file_format = 'PNG'
            out_image.filepath_raw = filepath

            # save(), not save_render(): avoid applying the scene view transform a second time.
            out_image.save()
        finally:
            bpy.data.images.remove(out_image)

        if not tsdraft_patch_png_dpi(filepath, sheet_dpi):
            raise RuntimeError("図面シートPNGへDPI情報を書き込めんかったンゴ")
    finally:
        for image in loaded_images:
            try:
                bpy.data.images.remove(image)
            except Exception:
                _debug.swallowed("draft.tsdraft_make_three_view_sheet_png")

    return {
        'paper': paper,
        'orientation': orient_name,
        'page_w_mm': page_w,
        'page_h_mm': page_h,
        'scale_denominator': denominator,
        'dpi': sheet_dpi,
        'pixel_width': page_px_w,
        'pixel_height': page_px_h,
    }


def tsdraft_build_three_view_sheet(context, filepath):
    """Shared builder for preview and final export."""
    source_obj = tsdraft_resolve_source_object(context)
    if source_obj is None:
        raise RuntimeError('元オブジェクトを選択してクレメンス')
    if bpy.data.objects.get(BBOX_NAME) is None:
        raise RuntimeError('先にBOX＋寸法を作成してクレメンス')

    out_dir = os.path.dirname(filepath) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    # 2.4.13: intermediate view PNGs belong in the OS temp area, not beside
    # the user's exported drawing.  The old .tsdraft_sheet_temp directory could
    # survive on Windows when a file handle was released a little late.
    import tempfile
    import shutil
    temp_dir = Path(tempfile.mkdtemp(prefix='zoukei_helper_sheet_'))

    exported = {}
    namespace = bpy.app.driver_namespace
    old_sheet_layout_mode = namespace.get("TSDRAFT_SHEET_LAYOUT_MODE", False)
    old_suppress_dim_text = namespace.get("TSDRAFT_SHEET_SUPPRESS_DIM_TEXT", False)
    namespace["TSDRAFT_SHEET_LAYOUT_MODE"] = True
    namespace["TSDRAFT_SHEET_SUPPRESS_DIM_TEXT"] = True

    try:
        for key, label in (('top', '上面'), ('front', '前面'), ('side', '側面')):
            tmp = temp_dir / f'__tsdraft_{os.getpid()}_{key}.png'
            info = tsdraft_export_viewport_exact_png(
                context,
                str(tmp),
                key,
                fit_padding_ratio=0.60,
                suppress_dimension_text=True
            )
            exported[key] = {'path': str(tmp), 'info': info, 'label': label}
        return tsdraft_make_three_view_sheet_png(context.scene, filepath, exported)
    finally:
        namespace["TSDRAFT_SHEET_LAYOUT_MODE"] = old_sheet_layout_mode
        namespace["TSDRAFT_SHEET_SUPPRESS_DIM_TEXT"] = old_suppress_dim_text
        for item in exported.values():
            try:
                Path(item['path']).unlink(missing_ok=True)
            except Exception:
                _debug.swallowed("draft.tsdraft_build_three_view_sheet")
        # Remove the whole temporary work tree.  ignore_errors=True is
        # intentional here: export success must not be turned into an error
        # merely because Windows releases a temporary image handle late.
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            _debug.swallowed("draft.tsdraft_build_three_view_sheet")


class TSDRAFT_OT_preview_three_view_sheet(bpy.types.Operator):
    # 2.4.12: use a fresh operator id so an old/stale registration can never
    # resolve the Preview button to the ExportHelper operator after updating.
    bl_idname = 'truescale_draft.preview_three_view_sheet_safe'
    bl_label = '三面図シートをプレビュー'
    bl_description = '一時PNGを作ってプレビューします。保存先の指定は行いません'

    def invoke(self, context, event):
        # Preview is intentionally non-modal and never opens Blender's file selector.
        return self.execute(context)

    def execute(self, context):
        import tempfile
        preview_dir = Path(tempfile.gettempdir()) / 'zoukei_helper_preview'
        preview_dir.mkdir(parents=True, exist_ok=True)
        filepath = preview_dir / '三面図プレビュー.png'

        try:
            info = tsdraft_build_three_view_sheet(context, str(filepath))
            bpy.ops.wm.path_open(filepath=str(filepath))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"プレビュー: {info['paper']} / 1:{info['scale_denominator']:g} / {info['dpi']:.0f}dpi"
        )
        return {'FINISHED'}


class TSDRAFT_OT_export_three_view_sheet(bpy.types.Operator, ExportHelper):
    bl_idname = 'truescale_draft.export_three_view_sheet'
    bl_label = '三面図シートを書き出し'
    bl_description = '上面・前面・側面を選択した用紙サイズと縮率で1枚のPNG図面にまとめます'

    filename_ext = '.png'
    filter_glob: bpy.props.StringProperty(default='*.png', options={'HIDDEN'})

    def invoke(self, context, event):
        tsdraft_store_export_view_context(context)
        source_obj = tsdraft_resolve_source_object(context)
        base = source_obj.name if source_obj is not None else 'drawing'
        safe_base = ''.join(c if c not in '\\/:*?"<>|' else '_' for c in base)
        last_dir = tsdraft_get_last_export_dir()
        default_name = f'{safe_base}_三面図_{context.scene.tsdraft_sheet_paper_size}_1-{tsdraft_sheet_scale_denominator(context.scene):g}.png'
        self.filepath = os.path.join(last_dir, default_name) if last_dir else default_name
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        filepath = bpy.path.abspath(self.filepath)
        if not filepath.lower().endswith('.png'):
            filepath += '.png'

        try:
            sheet_info = tsdraft_build_three_view_sheet(context, filepath)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        tsdraft_remember_export_dir(filepath)
        self.report(
            {'INFO'},
            f"{sheet_info['paper']} / 1:{sheet_info['scale_denominator']:g} / {sheet_info['dpi']:.0f}dpi の三面図PNGを書き出したで"
        )
        return {'FINISHED'}


class TSDRAFT_OT_export_actual_png(bpy.types.Operator, ExportHelper):
    bl_idname = "truescale_draft.export_actual_png"
    bl_label = "実寸PNGを書き出し"
    bl_description = "現在のBlender図面ビューをそのままPNG化し、Bounding Box実寸で物理サイズを合わせます"

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(
        default="*.png",
        options={'HIDDEN'}
    )

    view_key: bpy.props.EnumProperty(
        name="ビュー",
        items=(
            ('top', "上面", ""),
            ('front', "前面", ""),
            ('side', "側面", ""),
            ('user', "任意", ""),
        ),
        default='front'
    )

    def invoke(self, context, event):
        tsdraft_store_export_view_context(context)

        source_obj = tsdraft_resolve_source_object(context)
        base = source_obj.name if source_obj is not None else "drawing"
        safe_base = "".join(c if c not in '\\/:*?"<>|' else "_" for c in base)

        default_name = f"{safe_base}_{tsdraft_svg_view_label(self.view_key)}.png"
        last_dir = tsdraft_get_last_export_dir()

        if last_dir:
            self.filepath = os.path.join(last_dir, default_name)
        else:
            self.filepath = default_name

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            info = tsdraft_export_viewport_exact_png(
                context,
                self.filepath,
                self.view_key
            )
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        tsdraft_remember_export_dir(self.filepath)

        self.report(
            {'INFO'},
            f"{tsdraft_svg_view_label(self.view_key)} PNG出力 "
            f"{info['bbox_width_mm']:.1f}×{info['bbox_height_mm']:.1f}mm "
            f"/ {info['dpi']:.1f}dpi"
        )
        return {'FINISHED'}


class TSDRAFT_OT_export_all_actual_png(bpy.types.Operator, ExportHelper):
    bl_idname = "truescale_draft.export_all_actual_png"
    bl_label = "3面まとめてPNG"
    bl_description = (
        "上面・前面・側面を実寸PNGとして一括書き出します。"
        "保存時に入力した名前をベースに、_上面 / _前面 / _側面 を自動付与します"
    )

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(
        default="*.png",
        options={'HIDDEN'}
    )

    def invoke(self, context, event):
        tsdraft_store_export_view_context(context)

        source_obj = tsdraft_resolve_source_object(context)
        base = source_obj.name if source_obj is not None else "drawing"
        safe_base = "".join(
            c if c not in '\\/:*?"<>|' else "_"
            for c in base
        )

        last_dir = tsdraft_get_last_export_dir()
        default_name = f"{safe_base}.png"

        if last_dir:
            self.filepath = os.path.join(last_dir, default_name)
        else:
            self.filepath = default_name

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        source_obj = tsdraft_resolve_source_object(context)
        if source_obj is None:
            self.report({'ERROR'}, "元オブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        chosen_path = bpy.path.abspath(self.filepath)
        target_dir = os.path.dirname(chosen_path)

        if not target_dir:
            target_dir = tsdraft_get_last_export_dir() or os.getcwd()

        os.makedirs(target_dir, exist_ok=True)

        # ユーザーが保存ダイアログで付けた名前をベース名として使う。
        chosen_filename = os.path.basename(chosen_path)
        typed_base, _ext = os.path.splitext(chosen_filename)

        if not typed_base:
            typed_base = source_obj.name

        safe_base = "".join(
            c if c not in '\\/:*?"<>|' else "_"
            for c in typed_base
        ).strip()

        if not safe_base:
            safe_base = "drawing"

        views = (
            ("top", "上面"),
            ("front", "前面"),
            ("side", "側面"),
        )

        errors = []
        done = 0

        # 3面まとめて書き出しは現在表示に依存せず、
        # 上面・前面・側面を内部で1面ずつ切り替え、個別フィットして出力する。
        view_ctx = tsdraft_get_export_view_context(context)
        if view_ctx is None:
            self.report({'ERROR'}, "書き出し元の3Dビューが見つからんかったンゴ")
            return {'CANCELLED'}

        for view_key, view_label in views:
            filepath = os.path.join(
                target_dir,
                f"{safe_base}_{view_label}.png"
            )
            try:
                info = tsdraft_export_viewport_exact_png(
                    context,
                    filepath,
                    view_key,
                    common_view_distance=None,
                    suppress_dimension_text=True
                )
                tsdraft_add_dimension_labels_to_exact_png(
                    context.scene,
                    filepath,
                    view_key,
                    info
                )
                done += 1
            except Exception as exc:
                errors.append(f"{view_label}: {exc}")

        if errors:
            self.report({'WARNING'}, " / ".join(errors))

        if done == 0:
            return {'CANCELLED'}

        tsdraft_remember_export_dir(target_dir)

        self.report(
            {'INFO'},
            f"{safe_base}_上面 / 前面 / 側面.png を{done}枚書き出したで"
        )
        return {'FINISHED'}


class TSDRAFT_OT_dark_place(bpy.types.Operator):
    bl_idname = "truescale_draft.dark_place"
    bl_label = "なんかずっと暗いとこ"
    bl_description = "押すたびに暗所表示のON/OFFを切り替えます"

    def execute(self, context):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "3Dビュー上で実行してクレメンス")
            return {'CANCELLED'}

        scene = context.scene
        space = context.area.spaces.active

        if not scene.tsdraft_dark_place:
            # 暗所は「通常Blender表示 ↔ 暗所」の往復専用にする。
            # 図面ビュー中なら、暗所へ入る前にまず元の通常表示へ戻す。
            if getattr(scene, "tsdraft_drawing_mode", False):
                try:
                    restore_view_state(space)
                except Exception:
                    _debug.swallowed("draft.TSDRAFT_OT_dark_place.execute")
                scene.tsdraft_drawing_mode = False

            # ここで通常表示を暗所の復帰先として保存してから暗所化。
            scene.tsdraft_dark_place = True
            apply_dark_place_view(space)

            # 暗所中だけ見た目上のグリッドと標準X/Y軸を直接OFF。
            try:
                if hasattr(space.overlay, "show_floor"):
                    space.overlay.show_floor = False
                if hasattr(space.overlay, "show_ortho_grid"):
                    space.overlay.show_ortho_grid = False
                if hasattr(space.overlay, "show_axis_x"):
                    space.overlay.show_axis_x = False
                if hasattr(space.overlay, "show_axis_y"):
                    space.overlay.show_axis_y = False
            except Exception:
                _debug.swallowed("draft.TSDRAFT_OT_dark_place.execute")
        else:
            scene.tsdraft_dark_place = False

            # 暗所に入る直前の背景・グリッド状態へそのまま戻す。
            restore_dark_place_view(space)

        redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_reset_label_offsets(bpy.types.Operator):
    bl_idname = "truescale_draft.reset_label_offsets"
    bl_label = "文字位置をリセット"

    def execute(self, context):
        for prop in (
        ):
            setattr(context.scene, prop, 0)
        for view in ("front", "top", "side", "user"):
            for axis in ("x", "y", "z"):
                setattr(context.scene, f"tsdraft_{view}_{axis}_offset_x_mm", 0.0)
                setattr(context.scene, f"tsdraft_{view}_{axis}_offset_y_mm", 0.0)

        redraw_viewports()
        return {'FINISHED'}


# =========================================================
# 図面ビュー：共通
# =========================================================

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
    if namespace.get(VIEW_STATE_KEY) is not None:
        return

    overlay = space.overlay
    shading = space.shading

    namespace[VIEW_STATE_KEY] = {
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


def restore_view_state(space):
    namespace = bpy.app.driver_namespace
    state = namespace.get(VIEW_STATE_KEY)

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

    namespace[VIEW_STATE_KEY] = None


def update_drawing_background(self, context):
    if not getattr(context.scene, "tsdraft_drawing_mode", False):
        # 通常Blender表示では背景やoverlayを勝手に変更しない。
        return

    if context.area and context.area.type == 'VIEW_3D':
        configure_drawing_view(
            context.area.spaces.active,
            getattr(context.scene, "tsdraft_drawing_background", 'WHITE'),
            getattr(context.scene, "tsdraft_drawing_background_color", (1.0, 1.0, 1.0))
        )
        redraw_viewports()


def tsdraft_fit_quad_for_drawing(context, area, padding_factor=1.28):
    """
    Quad Viewの上面・前面・側面をBBox中心へ寄せ、
    寸法文字のために少し余白を持たせる。
    任意ビューは触らない。
    """
    bbox_obj = bpy.data.objects.get(BBOX_NAME)
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
        region, rv3d = tsdraft_find_view_region(area, key)
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

    bpy.app.driver_namespace[ZOOM_SYNC_STATE_KEY] = {
        key: common_distance
        for key, _region, _rv3d in ortho_items
    }



def is_quad_view(space):
    try:
        return len(space.region_quadviews) > 0
    except Exception:
        return False


def switch_single_view(context, axis_type=None):
    override = get_view3d_override(context)

    if override is None:
        return False

    space = override["space_data"]

    configure_drawing_view(
        space,
        getattr(context.scene, "tsdraft_drawing_background", 'WHITE'),
        getattr(context.scene, "tsdraft_drawing_background_color", (1.0, 1.0, 1.0))
    )
    context.scene.tsdraft_drawing_mode = True

    if is_quad_view(space):
        with context.temp_override(**override):
            bpy.ops.screen.region_quadview()

        override = get_view3d_override(context)

        if override is None:
            return False

    if axis_type is not None:
        with context.temp_override(**override):
            bpy.ops.view3d.view_axis(
                type=axis_type,
                align_active=False
            )

    redraw_viewports()
    return True


# =========================================================
# 図面ビュー：Operator
# =========================================================

class TSDRAFT_OT_quad_view(bpy.types.Operator):
    bl_idname = "truescale_draft.quad_view"
    bl_label = "三面＋任意ビュー"

    def execute(self, context):
        bpy.app.driver_namespace[ZOOM_SYNC_STATE_KEY] = None
        override = get_view3d_override(context)

        if override is None:
            self.report({'ERROR'}, "3Dビュー上で実行してクレメンス")
            return {'CANCELLED'}

        space = override["space_data"]

        configure_drawing_view(
            space,
            context.scene.tsdraft_show_grid
        )
        context.scene.tsdraft_drawing_mode = True

        # 三面図モードでは、上面・前面・側面は図面表示、
        # 任意ビューだけイメージ確認用にBOX/寸法を隠す。
        context.scene.tsdraft_show_bbox_top = True
        context.scene.tsdraft_show_bbox_front = True
        context.scene.tsdraft_show_bbox_side = True
        context.scene.tsdraft_show_dimensions_top = True
        context.scene.tsdraft_show_dimensions_front = True
        context.scene.tsdraft_show_dimensions_side = True
        context.scene.tsdraft_show_bbox_user = False
        context.scene.tsdraft_show_dimensions_user = False

        if not is_quad_view(space):
            with context.temp_override(**override):
                bpy.ops.screen.region_quadview()

        # Quad View生成直後にBBox＋寸法の余白を確保。
        try:
            tsdraft_fit_quad_for_drawing(context, override["area"])
        except Exception:
            _debug.swallowed("draft.TSDRAFT_OT_quad_view.execute")

        try:
            tsdraft_sync_ortho_zoom(space)
        except Exception:
            _debug.swallowed("draft.TSDRAFT_OT_quad_view.execute")

        redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_front_view(bpy.types.Operator):
    bl_idname = "truescale_draft.front_view"
    bl_label = "正面"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = False
        if not switch_single_view(context, 'FRONT'):
            return {'CANCELLED'}
        return {'FINISHED'}


class TSDRAFT_OT_top_view(bpy.types.Operator):
    bl_idname = "truescale_draft.top_view"
    bl_label = "上面"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = False
        if not switch_single_view(context, 'TOP'):
            return {'CANCELLED'}
        return {'FINISHED'}


class TSDRAFT_OT_side_view(bpy.types.Operator):
    bl_idname = "truescale_draft.side_view"
    bl_label = "側面"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = False
        if not switch_single_view(context, 'RIGHT'):
            return {'CANCELLED'}
        return {'FINISHED'}


class TSDRAFT_OT_user_view(bpy.types.Operator):
    bl_idname = "truescale_draft.user_view"
    bl_label = "任意"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = True
        if not switch_single_view(context, None):
            return {'CANCELLED'}
        redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_apply_drawing_style(bpy.types.Operator):
    bl_idname = "truescale_draft.apply_drawing_style"
    bl_label = "図面表示を再適用"

    def execute(self, context):
        if context.area is None or context.area.type != 'VIEW_3D':
            return {'CANCELLED'}

        configure_drawing_view(
            context.area.spaces.active,
            getattr(context.scene, "tsdraft_drawing_background", 'WHITE'),
            getattr(context.scene, "tsdraft_drawing_background_color", (1.0, 1.0, 1.0))
        )
        context.scene.tsdraft_drawing_mode = True

        redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_restore_view(bpy.types.Operator):
    bl_idname = "truescale_draft.restore_view"
    bl_label = "元の表示に戻す"
    bl_description = "図面ビューに入る前のビューポート表示へ戻します"

    def execute(self, context):
        override = get_view3d_override(context)

        if override is None:
            self.report({'ERROR'}, "3Dビュー上で実行してクレメンス")
            return {'CANCELLED'}

        space = override["space_data"]

        # Quad Viewなら先に解除
        if is_quad_view(space):
            with context.temp_override(**override):
                bpy.ops.screen.region_quadview()

            override = get_view3d_override(context)
            if override is None:
                return {'CANCELLED'}

            space = override["space_data"]

        if getattr(context.scene, "tsdraft_dark_place", False):
            context.scene.tsdraft_dark_place = False
            restore_dark_place_view(space)

        restore_view_state(space)
        context.scene.tsdraft_drawing_mode = False
        context.scene.tsdraft_user_view_mode = False
        redraw_viewports()

        self.report({'INFO'}, "通常表示に戻したで")
        return {'FINISHED'}


# =========================================================
# Nパネル：統合
# =========================================================

class TSDRAFT_PT_main(bpy.types.Panel):
    bl_label = "Truescale Draft"
    bl_idname = "TSDRAFT_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Truescale"

    def draw(self, context):
        layout = self.layout

        # =====================================================
        # 1. サイズ表示
        # =====================================================
        box = layout.box()
        box.label(text="サイズ表示")

        box.operator(
            "truescale_draft.make_size_bbox",
            text="BOX＋寸法を作成",
            icon='CUBE'
        )

        box.operator(
            "truescale_draft.delete_bbox",
            text="BOX＋寸法を削除",
            icon='TRASH'
        )

        overlay_visible = (
            context.scene.tsdraft_show_bbox
            and context.scene.tsdraft_show_dimensions
        )

        box.operator(
            "truescale_draft.toggle_size_overlay",
            text=(
                "BOX＋寸法を非表示"
                if overlay_visible
                else "BOX＋寸法を表示"
            ),
            icon='HIDE_OFF' if overlay_visible else 'HIDE_ON'
        )

        box.prop(
            context.scene,
            "tsdraft_auto_follow",
            text="自動追従",
            toggle=True
        )

        layout.separator()

        # =====================================================
        # 2. 枠線
        # =====================================================
        box = layout.box()
        box.label(text="枠線")

        box.prop(
            context.scene,
            "tsdraft_font_size",
            text="文字サイズ"
        )

        box.prop(
            context.scene,
            "tsdraft_font_color",
            text="文字色"
        )

        box.prop(
            context.scene,
            "tsdraft_dimension_unit",
            text="単位"
        )

        box.prop(
            context.scene,
            "tsdraft_frame_mode",
            text="枠表示"
        )

        box.prop(
            context.scene,
            "tsdraft_frame_color",
            text="枠線色"
        )

        box.prop(
            context.scene,
            "tsdraft_frame_width",
            text="枠線の太さ"
        )

        layout.separator()

        # =====================================================
        # 3. 出力
        # =====================================================
        box = layout.box()
        box.label(text="出力")

        box.prop(
            context.scene,
            "tsdraft_export_background",
            text="書き出し背景"
        )
        if context.scene.tsdraft_export_background == 'CUSTOM':
            box.prop(
                context.scene,
                "tsdraft_export_background_color",
                text="カスタム色"
            )

        row = box.row(align=True)
        op = row.operator("truescale_draft.export_actual_png", text="上面")
        op.view_key = 'top'
        op = row.operator("truescale_draft.export_actual_png", text="前面")
        op.view_key = 'front'
        op = row.operator("truescale_draft.export_actual_png", text="側面")
        op.view_key = 'side'

        op = box.operator("truescale_draft.export_actual_png", text="任意")
        op.view_key = 'user'

        box.operator(
            "truescale_draft.export_all_actual_png",
            text="3面まとめて書き出し",
            icon='EXPORT'
        )

        sheet = box.box()
        sheet.label(text="図面シート")
        row = sheet.row(align=True)
        row.prop(context.scene, "tsdraft_sheet_paper_size", text="用紙")
        row.prop(context.scene, "tsdraft_sheet_orientation", text="向き")
        if context.scene.tsdraft_sheet_paper_size == 'CUSTOM':
            row = sheet.row(align=True)
            row.prop(context.scene, "tsdraft_sheet_custom_width_mm", text="幅(mm)")
            row.prop(context.scene, "tsdraft_sheet_custom_height_mm", text="高さ(mm)")
        sheet.prop(context.scene, "tsdraft_sheet_scale", text="縮率")
        if context.scene.tsdraft_sheet_scale == 'CUSTOM':
            sheet.prop(context.scene, "tsdraft_sheet_custom_scale", text="1 :")
        row = sheet.row(align=True)
        row.operator(
            "truescale_draft.preview_three_view_sheet_safe",
            text="プレビュー（保存しない）",
            icon='HIDE_OFF'
        )
        row.operator(
            "truescale_draft.export_three_view_sheet",
            text="書き出し",
            icon='FILE_IMAGE'
        )

        layout.separator()

        # =====================================================
        # 4. 図面ビュー
        # =====================================================
        box = layout.box()
        box.label(text="図面ビュー")

        box.prop(
            context.scene,
            "tsdraft_drawing_background",
            text="ビュー背景"
        )
        if context.scene.tsdraft_drawing_background == 'CUSTOM':
            box.prop(
                context.scene,
                "tsdraft_drawing_background_color",
                text="カスタム色"
            )

        row = box.row(align=True)
        row.operator("truescale_draft.front_view", text="正面")
        row.operator("truescale_draft.top_view", text="上面")

        row = box.row(align=True)
        row.operator("truescale_draft.side_view", text="側面")
        row.operator("truescale_draft.user_view", text="任意")

        sub = box.box()
        sub.label(text="任意ビュー表示")

        row = sub.row(align=True)
        row.prop(
            context.scene,
            "tsdraft_show_bbox_user",
            text="枠線",
            toggle=True
        )
        row.prop(
            context.scene,
            "tsdraft_show_dimensions_user",
            text="寸法",
            toggle=True
        )

        box.operator(
            "truescale_draft.restore_view",
            text="元の表示に戻す",
            icon='LOOP_BACK'
        )

        layout.separator()

        # =====================================================
        # 5. 寸法位置の微調整
        # =====================================================
        box = layout.box()
        row = box.row(align=True)
        row.prop(
            context.scene,
            "tsdraft_show_dimension_adjustments",
            text="寸法位置の微調整",
            icon='TRIA_DOWN' if context.scene.tsdraft_show_dimension_adjustments else 'TRIA_RIGHT',
            emboss=False
        )

        if context.scene.tsdraft_show_dimension_adjustments:
            box.label(text="自動配置位置からの追加調整")

            view_specs = (
                ("top", "上面", (
                    ("x", "左右"),
                    ("y", "上下"),
                )),
                ("front", "前面", (
                    ("x", "左右"),
                    ("z", "上下"),
                )),
                ("side", "側面", (
                    ("y", "左右"),
                    ("z", "上下"),
                )),
                ("user", "任意", (
                    ("x", "左右(X)"),
                    ("y", "奥行(Y)"),
                    ("z", "上下(Z)"),
                )),
            )

            for view_key, view_label, axes in view_specs:
                col = box.column(align=True)
                col.label(text=view_label)

                for axis, fallback_label in axes:
                    row = col.row(align=True)

                    axis_label = get_axis_dimension_text(
                        context.scene,
                        axis.upper(),
                        fallback_label
                    )
                    row.label(text=axis_label)

                    row.prop(
                        context.scene,
                        f"tsdraft_{view_key}_{axis}_offset_x_mm",
                        text="左右(mm)"
                    )
                    row.prop(
                        context.scene,
                        f"tsdraft_{view_key}_{axis}_offset_y_mm",
                        text="上下(mm)"
                    )

                box.separator()

            box.operator(
                "truescale_draft.reset_label_offsets",
                text="文字位置をリセット"
            )


        prefs = get_addon_preferences(context)
        if prefs is None or prefs.show_dark_place_button:
            layout.separator()
            box = layout.box()
            box.operator(
                "truescale_draft.dark_place",
                text="なんかずっと暗いとこ"
            )


classes = (
    TSDRAFT_Preferences,
    TSDRAFT_OT_toggle_size_overlay,
    TSDRAFT_OT_export_actual_png,
    TSDRAFT_OT_export_all_actual_png,
    TSDRAFT_OT_preview_three_view_sheet,
    TSDRAFT_OT_export_three_view_sheet,
    TSDRAFT_OT_make_size_bbox,
    TSDRAFT_OT_delete_bbox,
    TSDRAFT_OT_dark_place,
    TSDRAFT_OT_reset_label_offsets,
    TSDRAFT_OT_quad_view,
    TSDRAFT_OT_front_view,
    TSDRAFT_OT_top_view,
    TSDRAFT_OT_side_view,
    TSDRAFT_OT_user_view,
    TSDRAFT_OT_apply_drawing_style,
    TSDRAFT_OT_restore_view,
    TSDRAFT_PT_main,
)



def tsdraft_reset_scene_settings_to_defaults(scene):
    """
    Remove persisted addon setting values from the Scene.
    Registered bpy.props defaults then become active again.
    Objects/BBox themselves are not deleted.
    """
    try:
        keys = list(scene.keys())
    except Exception:
        return

    for key in keys:
        if isinstance(key, str) and key.startswith("tsdraft_"):
            try:
                del scene[key]
            except Exception:
                _debug.swallowed("draft.tsdraft_reset_scene_settings_to_defaults")


def tsdraft_reset_all_scenes_to_defaults():
    # During add-on registration Blender may expose _RestrictData,
    # which has no .scenes attribute yet.
    if not hasattr(bpy.data, "scenes"):
        return False

    for scene in bpy.data.scenes:
        tsdraft_reset_scene_settings_to_defaults(scene)

    ns = bpy.app.driver_namespace
    ns[AUTO_FOLLOW_SIGNATURE_KEY] = None
    return True


def tsdraft_deferred_startup_reset():
    try:
        if tsdraft_reset_all_scenes_to_defaults():
            redraw_viewports()
            return None
    except Exception:
        _debug.swallowed("draft.tsdraft_deferred_startup_reset")

    # Blender is still in restricted-data phase. Try again shortly.
    return 0.25


@persistent
def tsdraft_reset_defaults_on_load(_dummy):
    # Run after a .blend/startup file is loaded so old saved UI values
    # do not carry into the new session.
    try:
        tsdraft_reset_all_scenes_to_defaults()
        redraw_viewports()
    except Exception:
        _debug.swallowed("draft.tsdraft_reset_defaults_on_load")


def register():
    bpy.types.Scene.tsdraft_auto_follow = bpy.props.BoolProperty(
        name="自動追従",
        description="元オブジェクトの形状・変形に合わせてBounding Boxと寸法を自動更新",
        default=True,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_font_size = bpy.props.IntProperty(
        name="文字サイズ",
        default=30,
        min=10,
        max=200,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_font_color = bpy.props.FloatVectorProperty(
        name="文字色",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_front_x_offset_x_mm = bpy.props.FloatProperty(name="前面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_front_x_offset_y_mm = bpy.props.FloatProperty(name="前面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_front_y_offset_x_mm = bpy.props.FloatProperty(name="前面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_front_y_offset_y_mm = bpy.props.FloatProperty(name="前面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_front_z_offset_x_mm = bpy.props.FloatProperty(name="前面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_front_z_offset_y_mm = bpy.props.FloatProperty(name="前面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_top_x_offset_x_mm = bpy.props.FloatProperty(name="上面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_top_x_offset_y_mm = bpy.props.FloatProperty(name="上面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_top_y_offset_x_mm = bpy.props.FloatProperty(name="上面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_top_y_offset_y_mm = bpy.props.FloatProperty(name="上面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_top_z_offset_x_mm = bpy.props.FloatProperty(name="上面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_top_z_offset_y_mm = bpy.props.FloatProperty(name="上面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_side_x_offset_x_mm = bpy.props.FloatProperty(name="側面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_side_x_offset_y_mm = bpy.props.FloatProperty(name="側面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_side_y_offset_x_mm = bpy.props.FloatProperty(name="側面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_side_y_offset_y_mm = bpy.props.FloatProperty(name="側面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_side_z_offset_x_mm = bpy.props.FloatProperty(name="側面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_side_z_offset_y_mm = bpy.props.FloatProperty(name="側面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_user_x_offset_x_mm = bpy.props.FloatProperty(name="任意 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_user_x_offset_y_mm = bpy.props.FloatProperty(name="任意 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_user_y_offset_x_mm = bpy.props.FloatProperty(name="任意 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_user_y_offset_y_mm = bpy.props.FloatProperty(name="任意 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_user_z_offset_x_mm = bpy.props.FloatProperty(name="任意 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)
    bpy.types.Scene.tsdraft_user_z_offset_y_mm = bpy.props.FloatProperty(name="任意 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=redraw_viewports)



    bpy.types.Scene.tsdraft_show_bbox = bpy.props.BoolProperty(
        name="BOXを表示",
        default=True,
        update=update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_frame_mode = bpy.props.EnumProperty(
        name="枠表示",
        description="サイズ枠の表示方法",
        items=(
            ('BOX', "BOX", "外接BOXを表示"),
            ('LINES', "寸法線のみ", "表示中の寸法に対応する線だけ表示"),
            ('NONE', "なし", "枠線を表示しない"),
        ),
        default='BOX',
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_frame_color = bpy.props.FloatVectorProperty(
        name="枠線色",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_frame_width = bpy.props.FloatProperty(
        name="枠線の太さ",
        default=1.5,
        min=1.0,
        max=8.0,
        precision=1,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_bbox_top = bpy.props.BoolProperty(
        name="上面 BOX表示",
        default=False,
        update=update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_front = bpy.props.BoolProperty(
        name="前面 BOX表示",
        default=False,
        update=update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_side = bpy.props.BoolProperty(
        name="側面 BOX表示",
        default=False,
        update=update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_user = bpy.props.BoolProperty(
        name="任意 BOX表示",
        default=False,
        update=update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_dimensions = bpy.props.BoolProperty(
        name="寸法を表示",
        default=True,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_dimension_unit = bpy.props.EnumProperty(
        name="単位",
        description="寸法表示に使う単位",
        items=(
            ('MM', "mm", "ミリメートル"),
            ('CM', "cm", "センチメートル"),
            ('M', "m", "メートル"),
        ),
        default='MM',
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_top = bpy.props.BoolProperty(
        name="上面 寸法表示",
        default=False,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_front = bpy.props.BoolProperty(
        name="前面 寸法表示",
        default=False,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_side = bpy.props.BoolProperty(
        name="側面 寸法表示",
        default=False,
        update=redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_user = bpy.props.BoolProperty(
        name="任意 寸法表示",
        default=False,
        update=redraw_viewports
    )

    # 旧ファイル互換用。UIではtsdraft_drawing_backgroundを使用。
    bpy.types.Scene.tsdraft_show_grid = bpy.props.BoolProperty(
        name="グリッド表示（旧）",
        default=False
    )

    background_items = (
        ('WHITE', "白", "白背景"),
        ('GRID', "グリッド", "白背景にBlenderグリッドを表示"),
        ('BLACK', "黒", "黒背景"),
        ('CUSTOM', "カスタム", "好きな背景色を指定"),
    )

    bpy.types.Scene.tsdraft_drawing_background = bpy.props.EnumProperty(
        name="図面ビュー背景",
        description="図面ビューの背景表示",
        items=background_items,
        default='WHITE',
        update=update_drawing_background
    )

    bpy.types.Scene.tsdraft_drawing_background_color = bpy.props.FloatVectorProperty(
        name="図面ビューのカスタム背景色",
        subtype='COLOR',
        size=3,
        default=(0.18, 0.18, 0.18),
        min=0.0,
        max=1.0,
        update=update_drawing_background
    )

    bpy.types.Scene.tsdraft_export_background = bpy.props.EnumProperty(
        name="書き出し背景",
        description="実寸PNGを書き出す時の背景表示。軸・原点・3Dカーソルは常に非表示です",
        items=background_items,
        default='WHITE'
    )

    bpy.types.Scene.tsdraft_export_background_color = bpy.props.FloatVectorProperty(
        name="書き出しのカスタム背景色",
        subtype='COLOR',
        size=3,
        default=(0.18, 0.18, 0.18),
        min=0.0,
        max=1.0
    )

    bpy.types.Scene.tsdraft_sheet_paper_size = bpy.props.EnumProperty(
        name="用紙サイズ",
        items=(
            ('A4', "A4", "210×297mm"),
            ('A3', "A3", "297×420mm"),
            ('A2', "A2", "420×594mm"),
            ('A1', "A1", "594×841mm"),
            ('A0', "A0", "841×1189mm"),
            ('CUSTOM', "カスタム", "幅と高さをmmで指定"),
        ),
        default='A4'
    )

    bpy.types.Scene.tsdraft_sheet_custom_width_mm = bpy.props.FloatProperty(
        name="カスタム幅",
        description="カスタム用紙の幅(mm)",
        default=210.0,
        min=10.0,
        soft_max=3000.0,
        precision=1
    )

    bpy.types.Scene.tsdraft_sheet_custom_height_mm = bpy.props.FloatProperty(
        name="カスタム高さ",
        description="カスタム用紙の高さ(mm)",
        default=297.0,
        min=10.0,
        soft_max=3000.0,
        precision=1
    )

    bpy.types.Scene.tsdraft_sheet_orientation = bpy.props.EnumProperty(
        name="用紙の向き",
        items=(
            ('AUTO', "自動", "収まる向きを自動選択"),
            ('PORTRAIT', "縦", "縦向き"),
            ('LANDSCAPE', "横", "横向き"),
        ),
        default='AUTO'
    )

    bpy.types.Scene.tsdraft_sheet_scale = bpy.props.EnumProperty(
        name="縮率",
        items=(
            ('1_1', "1:1", "原寸"),
            ('1_2', "1:2", "50%"),
            ('1_5', "1:5", "20%"),
            ('1_10', "1:10", "10%"),
            ('CUSTOM', "任意", "任意の縮率"),
        ),
        default='1_1'
    )

    bpy.types.Scene.tsdraft_sheet_custom_scale = bpy.props.FloatProperty(
        name="任意縮率",
        description="1:N の N を指定します",
        default=2.0,
        min=1.0,
        soft_max=100.0,
        precision=2
    )

    bpy.types.Scene.tsdraft_show_dimension_adjustments = bpy.props.BoolProperty(
        name="寸法位置の微調整",
        default=False
    )

    bpy.types.Scene.tsdraft_drawing_mode = bpy.props.BoolProperty(
        name="図面モード",
        default=False
    )

    bpy.types.Scene.tsdraft_user_view_mode = bpy.props.BoolProperty(
        name="任意ビューモード",
        default=False
    )

    bpy.types.Scene.tsdraft_dark_place = bpy.props.BoolProperty(
        name="なんかずっと暗いとこ",
        default=False,
        update=redraw_viewports
    )

    for cls in classes:
        bpy.utils.register_class(cls)

    tsdraft_remove_legacy_draw_handlers()
    ensure_draw_handler()
    ensure_bbox_draw_handler()
    ensure_view_label_handler()
    ensure_cleanup_handler()

    if tsdraft_reset_defaults_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(tsdraft_reset_defaults_on_load)

    # register()中はbpy.dataが_RestrictDataのことがあるため、
    # 初期化はBlenderが通常状態へ戻ってから実行する。
    try:
        if not bpy.app.timers.is_registered(tsdraft_deferred_startup_reset):
            bpy.app.timers.register(
                tsdraft_deferred_startup_reset,
                first_interval=0.25
            )
    except Exception:
        _debug.swallowed("draft.register")

    try:
        if not bpy.app.timers.is_registered(tsdraft_quad_zoom_lock_timer):
            bpy.app.timers.register(
                tsdraft_quad_zoom_lock_timer,
                first_interval=0.10,
                persistent=True
            )
    except Exception:
        _debug.swallowed("draft.register")


def _unregister_scene_props():
    """このアドオンが register() で作った Scene プロパティを全て削除する。

    以前は削除対象を手書きのタプルで列挙していたが、register() 側に
    プロパティを足したときに追従されず、消し残しが発生していた。
    列挙をやめ、接頭辞で特定することで register() と必ず一致させる。
    """
    prefix = "tsdraft_"
    for name in [n for n in dir(bpy.types.Scene) if n.startswith(prefix)]:
        try:
            delattr(bpy.types.Scene, name)
        except Exception:
            # 1つ失敗しても残りの削除は続ける。内容は握り潰さず出す。
            traceback.print_exc()


def unregister():
    if tsdraft_reset_defaults_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(tsdraft_reset_defaults_on_load)

    try:
        if bpy.app.timers.is_registered(tsdraft_deferred_startup_reset):
            bpy.app.timers.unregister(tsdraft_deferred_startup_reset)
    except Exception:
        _debug.swallowed("draft.unregister")

    try:
        if bpy.app.timers.is_registered(tsdraft_quad_zoom_lock_timer):
            bpy.app.timers.unregister(tsdraft_quad_zoom_lock_timer)
    except Exception:
        _debug.swallowed("draft.unregister")

    remove_draw_handler()
    remove_bbox_draw_handler()
    remove_view_label_handler()
    remove_cleanup_handler()

    namespace = bpy.app.driver_namespace
    namespace[DATA_KEY] = []
    namespace[SOURCE_KEY] = None
    namespace[VIEW_STATE_KEY] = None
    namespace[DARK_VIEW_STATE_KEY] = None
    namespace[AUTO_FOLLOW_SIGNATURE_KEY] = None
    namespace[AUTO_FOLLOW_GUARD_KEY] = None

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    _unregister_scene_props()

if __name__ == "__main__":
    register()
