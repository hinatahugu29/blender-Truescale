"""操作している最中の状態。

マーキング中にしか存在しない情報を持つ。たとえば「いまマウスの下に
合印を置いたらどこに付くか」の下描きや、選び直している島のハイライト。

■ .blend に保存しない

途中まで置きかけた印を保存しても意味がなく、次に開いたときに
消えない下描きが残るだけになる。ここはモジュール直下の辞書で持ち、
Blender を閉じれば消える。シーンに残す必要があるもの
（どのモデルを読み込み中か等）は core.session が持つ。

■ 置き場所を分けた理由

以前はこの状態が unfold 本体にあり、描画側（overlay）から名前で
参照していた。描画を別ファイルへ出したときに参照だけが取り残され、
未定義の名前になった。描画は例外を握り潰すので気付かれず、島の
ハイライトと配置プレビューが黙って出なくなっていた。

状態と、それを読み書きする側の両方から import できる場所へ置く。

■ 辞書は作り直さず中身を書き換える

参照を持たれている辞書を丸ごと差し替えると、古い方を見ている側が
更新に気付けない。clear 系は中身だけを書き換える。
"""

import json
import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils import geometry

from .. import debug as _debug
from ..core import geometry as _geometry
from ..core import mapping as _mapping
from ..core import objects as _objects
from ..core import session as _session
from ..core import state as _state
from ..core import units as _units
from ..core.view import tag_redraw as _tag_redraw
from . import compute as _compute
from . import seams as _seams
from . import source as _source
from . import storage as _storage

_PATTERN_FLAT_MEMO_PROP = "tsunfold_flat_memos_json"

# マウスの下に置いたらどうなるか、の下描き。確定前の状態。
live_preview = {
    "mode": "NONE",
    "source": "",
    "notch_edge": -1,
    "notch_t": 0.5,
    "hover_anchor": None,
    "arrow_start": None,
}

# 選び直している島。元モデル側の面番号で持つ。
island_highlight = {
    "source": "",
    "source_faces": [],
}

# 編集中のメモ。型紙オブジェクト名と、その中での番号。
selected_memo = {
    "unfold": "",
    "index": -1,
}


def clear_selected_memo():
    selected_memo["unfold"] = ""
    selected_memo["index"] = -1
    _tag_redraw()


def selected_memo_item():
    unfold = bpy.data.objects.get(selected_memo.get("unfold", ""))
    index = int(selected_memo.get("index", -1))
    if unfold is None:
        return None, -1, None
    items = get_flat_memos(unfold)
    if not (0 <= index < len(items)):
        return unfold, -1, None
    return unfold, index, items[index]


def get_flat_memos(unfold_obj):
    if unfold_obj is None:
        return []

    raw = unfold_obj.get(_PATTERN_FLAT_MEMO_PROP, "[]")
    try:
        data = json.loads(raw)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        pos = item.get("pos", None)
        if not text or not isinstance(pos, (list, tuple)) or len(pos) < 2:
            continue
        result.append(item)

    return result


def set_flat_memos(unfold_obj, items):
    if unfold_obj is None:
        return

    unfold_obj[_PATTERN_FLAT_MEMO_PROP] = json.dumps(
        list(items),
        ensure_ascii=False,
    )
    _tag_redraw()


def pick_flat_memo_at_mouse(context, event, max_px=26.0):
    region = context.region
    rv3d = getattr(context.space_data, "region_3d", None)
    if region is None or rv3d is None:
        return None

    unfold = _objects.resolve_unfold_for_layout(context)
    if unfold is None:
        source = _objects.source_from_context(context)
        if source is not None:
            unfold = _objects.unfold_for_source(source)
    if unfold is None:
        return None

    mx = float(event.mouse_region_x)
    my = float(event.mouse_region_y)
    best = None
    best_d2 = float(max_px) * float(max_px)

    for index, item in enumerate(get_flat_memos(unfold)):
        pos = item.get("pos", [0.0, 0.0, 0.0])
        try:
            local = Vector((
                float(pos[0]),
                float(pos[1]),
                float(pos[2]) if len(pos) > 2 else 0.0,
            ))
        except Exception:
            continue

        world = unfold.matrix_world @ local
        screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
        if screen is None:
            continue

        dx = mx - float(screen.x)
        dy = my - float(screen.y)
        d2 = dx * dx + dy * dy
        if d2 <= best_d2:
            best_d2 = d2
            best = (unfold, index)

    return best


def raycast_flat_pattern(context, event):
    if (
        context.region is None
        or context.space_data is None
        or context.area is None
        or context.area.type != 'VIEW_3D'
    ):
        return None

    rv3d = getattr(context.space_data, "region_3d", None)
    if rv3d is None:
        return None

    mouse = Vector((
        float(event.mouse_region_x),
        float(event.mouse_region_y),
    ))

    try:
        origin = view3d_utils.region_2d_to_origin_3d(
            context.region,
            rv3d,
            mouse,
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            context.region,
            rv3d,
            mouse,
        ).normalized()

        depsgraph = context.evaluated_depsgraph_get()
        hit, location, _normal, _face_index, hit_obj, _matrix = (
            context.scene.ray_cast(
                depsgraph,
                origin,
                direction,
            )
        )

        if not hit or hit_obj is None:
            return None

        original = (
            hit_obj.original
            if hasattr(hit_obj, "original")
            else hit_obj
        )

        if (
            original.type != 'MESH'
            or not bool(original.get("tsunfold_generated", False))
        ):
            return None

        local = original.matrix_world.inverted() @ Vector(location)
        return original, local

    except Exception:
        return None


def session_active(scene):
    return bool(scene.get(_session.MARKING_SESSION_ACTIVE, False))


def begin_session(context, source_obj):
    """Save the user's working state once, then enter marking workspace."""
    scene = context.scene

    if not session_active(scene):
        active = context.active_object
        selected_names = [
            obj.name
            for obj in context.selected_objects
            if obj is not None
        ]

        scene[_session.MARKING_PREV_ACTIVE] = (
            active.name if active is not None else ""
        )
        scene[_session.MARKING_PREV_SELECTED_JSON] = json.dumps(
            selected_names,
            ensure_ascii=False,
        )
        scene[_session.MARKING_PREV_MODE] = (
            active.mode if active is not None else "OBJECT"
        )
        scene[_session.MARKING_SESSION_ACTIVE] = True
        scene[_session.MARKING_FINISH_REQUESTED] = False

    # Marking always happens on the original source Mesh in Object Mode.
    try:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        _debug.swallowed("marking.interact._pattern_begin_marking_session")

    for obj in context.selected_objects:
        try:
            obj.select_set(False)
        except Exception:
            _debug.swallowed("marking.interact._pattern_begin_marking_session")

    source_obj.hide_set(False)
    source_obj.hide_viewport = False
    source_obj.select_set(True)
    context.view_layer.objects.active = source_obj

    _tag_redraw()


def request_finish(context):
    context.scene[_session.MARKING_FINISH_REQUESTED] = True
    _tag_redraw()


def restore_work_state(context):
    """Restore active object, selection and mode saved before marking."""
    scene = context.scene

    prev_active_name = scene.get(
        _session.MARKING_PREV_ACTIVE,
        "",
    )
    prev_mode = scene.get(
        _session.MARKING_PREV_MODE,
        "OBJECT",
    )

    try:
        selected_names = json.loads(
            scene.get(
                _session.MARKING_PREV_SELECTED_JSON,
                "[]",
            )
        )
    except Exception:
        selected_names = []

    try:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        _debug.swallowed("marking.interact._pattern_restore_work_state")

    for obj in context.selected_objects:
        try:
            obj.select_set(False)
        except Exception:
            _debug.swallowed("marking.interact._pattern_restore_work_state")

    for name in selected_names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            try:
                obj.select_set(True)
            except Exception:
                _debug.swallowed("marking.interact._pattern_restore_work_state")

    active = bpy.data.objects.get(prev_active_name)
    if active is not None:
        active.hide_set(False)
        active.hide_viewport = False
        active.select_set(True)
        context.view_layer.objects.active = active

        if prev_mode == 'EDIT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                _debug.swallowed("marking.interact._pattern_restore_work_state")
        elif prev_mode == 'SCULPT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='SCULPT')
            except Exception:
                _debug.swallowed("marking.interact._pattern_restore_work_state")
        elif prev_mode == 'VERTEX_PAINT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='VERTEX_PAINT')
            except Exception:
                _debug.swallowed("marking.interact._pattern_restore_work_state")
        elif prev_mode == 'WEIGHT_PAINT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            except Exception:
                _debug.swallowed("marking.interact._pattern_restore_work_state")

    scene[_session.MARKING_SESSION_ACTIVE] = False
    scene[_session.MARKING_FINISH_REQUESTED] = False
    scene[_session.MODAL_RUNNING] = False
    scene.tsunfold_active_tool = "NONE"
    scene[_session.MARKING_PREV_ACTIVE] = ""
    scene[_session.MARKING_PREV_SELECTED_JSON] = "[]"
    scene[_session.MARKING_PREV_MODE] = "OBJECT"

    _tag_redraw()


def make_anchor_from_hit(source_obj, face_index, local_hit):
    mesh = source_obj.data
    mesh.calc_loop_triangles()

    candidates = [
        tri for tri in mesh.loop_triangles
        if tri.polygon_index == face_index
    ]

    if not candidates:
        return None

    chosen = None
    chosen_weights = None
    best_score = None

    unit_a = Vector((1.0, 0.0, 0.0))
    unit_b = Vector((0.0, 1.0, 0.0))
    unit_c = Vector((0.0, 0.0, 1.0))

    for tri in candidates:
        ids = list(tri.vertices)
        a = mesh.vertices[ids[0]].co
        b = mesh.vertices[ids[1]].co
        c = mesh.vertices[ids[2]].co

        weights = geometry.barycentric_transform(
            local_hit,
            a, b, c,
            unit_a, unit_b, unit_c,
        )

        vals = [float(weights.x), float(weights.y), float(weights.z)]
        score = sum(max(0.0, -v) for v in vals)

        if best_score is None or score < best_score:
            best_score = score
            chosen = ids
            chosen_weights = vals

        if score <= 1e-5:
            break

    if chosen is None:
        return None

    # Clamp tiny numerical errors and normalize.
    chosen_weights = [max(0.0, v) for v in chosen_weights]
    total = sum(chosen_weights)
    if total <= 1e-12:
        return None
    chosen_weights = [v / total for v in chosen_weights]

    return {
        "face": int(face_index),
        "tri": [int(v) for v in chosen],
        "w": [round(float(v), 8) for v in chosen_weights],
    }


def invalidate_layout_cache():
    """型紙や注記が変わったときに呼ぶ。キャッシュを捨てて描き直す。

    キャッシュの実体は truescale.core.state。
    ここでは再描画の要求だけを足している。
    """
    _state.invalidate()
    _tag_redraw()


def raycast_source_detail(context, event, source_obj):
    region = context.region
    rv3d = context.space_data.region_3d
    coord = (event.mouse_region_x, event.mouse_region_y)

    world_origin = view3d_utils.region_2d_to_origin_3d(
        region,
        rv3d,
        coord,
    )
    world_dir = view3d_utils.region_2d_to_vector_3d(
        region,
        rv3d,
        coord,
    ).normalized()

    inv = source_obj.matrix_world.inverted()
    local_origin = inv @ world_origin
    local_dir = (inv.to_3x3() @ world_dir).normalized()

    hit, location, normal, face_index = source_obj.ray_cast(
        local_origin,
        local_dir,
    )

    if not hit or face_index < 0:
        return None

    anchor = make_anchor_from_hit(
        source_obj,
        face_index,
        location,
    )
    if anchor is None:
        return None

    return anchor, location.copy(), int(face_index)


def nearest_seam_edge(context, source_obj, local_hit):
    try:
        source_obj.update_from_editmode()
    except Exception:
        _debug.swallowed("marking.interact._pattern_nearest_seam_edge")

    world_hit = source_obj.matrix_world @ local_hit
    best = None
    best_t = 0.5
    best_dist = None

    for edge in source_obj.data.edges:
        if not edge.use_seam:
            continue

        a = source_obj.matrix_world @ source_obj.data.vertices[edge.vertices[0]].co
        b = source_obj.matrix_world @ source_obj.data.vertices[edge.vertices[1]].co
        ab = b - a
        denom = ab.length_squared

        if denom <= 1e-20:
            continue

        t = (world_hit - a).dot(ab) / denom
        t = max(0.0, min(1.0, t))
        q = a + ab * t
        dist = (world_hit - q).length

        if best_dist is None or dist < best_dist:
            best = int(edge.index)
            best_t = float(t)
            best_dist = float(dist)

    max_dist = _units.scene_mm_to_bu(context.scene, 20.0)

    if best is None or best_dist is None or best_dist > max_dist:
        return None

    return best, best_t, best_dist


def current_color(scene, mode=None):
    mode = mode or active_tool(scene)

    if mode == "NOTCH":
        return _storage.normalize_color(scene.tsunfold_notch_color)
    if mode == "NUMBER":
        return _storage.normalize_color(scene.tsunfold_number_color)
    if mode == "TEXT":
        return _storage.normalize_color(scene.tsunfold_text_color)
    if mode == "ARROW":
        return _storage.normalize_color(scene.tsunfold_arrow_color)

    return [0.0, 0.0, 0.0]


def event_is_view_window(context, event):
    """Return True only for clicks in the actual 3D WINDOW region.

    Modal operator context can remain bound to the WINDOW region even when the
    mouse is physically over the N-panel. Therefore use absolute window mouse
    coordinates against area.regions, not mouse_region_x/y alone.
    """
    area = context.area

    if area is None or area.type != 'VIEW_3D':
        return False

    try:
        mx = int(event.mouse_x)
        my = int(event.mouse_y)
    except Exception:
        return False

    # Any visible UI/header/tool region wins over the viewport.
    for region in area.regions:
        if region.type == 'WINDOW':
            continue

        if region.width <= 0 or region.height <= 0:
            continue

        inside = (
            region.x <= mx < region.x + region.width
            and region.y <= my < region.y + region.height
        )

        if inside:
            return False

    # Finally require the mouse to be physically inside the WINDOW region.
    for region in area.regions:
        if region.type != 'WINDOW':
            continue

        inside = (
            region.x <= mx < region.x + region.width
            and region.y <= my < region.y + region.height
        )

        if inside:
            return True

    return False


def clear_live_preview():
    live_preview.update({
        "mode": "NONE",
        "source": "",
        "notch_edge": -1,
        "notch_t": 0.5,
        "hover_anchor": None,
        "arrow_start": None,
    })
    _tag_redraw()


def preview_anchor_world(source_obj, anchor):
    if source_obj is None or anchor is None:
        return None
    p = _source.anchor_point_local(source_obj, anchor)
    if p is None:
        return None
    return source_obj.matrix_world @ p


def active_tool(scene):
    try:
        return str(scene.tsunfold_active_tool)
    except Exception:
        return str(scene.get("tsunfold_active_tool", "NONE"))


def set_active_tool(scene, mode):
    value = str(mode)
    try:
        scene.tsunfold_active_tool = value
    except Exception:
        scene["tsunfold_active_tool"] = value
    _tag_redraw()


def flat_face_island(unfold_obj, seed_poly_index):
    mesh = unfold_obj.data
    if not (0 <= int(seed_poly_index) < len(mesh.polygons)):
        return set()

    edge_to_faces = {}
    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            key = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            edge_to_faces.setdefault(key, []).append(int(poly.index))

    adjacency = {int(poly.index): set() for poly in mesh.polygons}
    for faces in edge_to_faces.values():
        if len(faces) < 2:
            continue
        for fa in faces:
            for fb in faces:
                if fa != fb:
                    adjacency[fa].add(fb)

    seen = {int(seed_poly_index)}
    stack = [int(seed_poly_index)]

    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.append(nxt)

    return seen


def source_faces_for_flat_island(unfold_obj, flat_faces):
    mapping = _objects.flat_face_source_map(unfold_obj)
    result = set()
    for flat_index in flat_faces:
        if 0 <= flat_index < len(mapping):
            result.add(int(mapping[flat_index]))
    return result


def flat_island_from_source_face(unfold_obj, source_face_index):
    mapping = _objects.flat_face_source_map(unfold_obj)
    seed = None
    for flat_index, source_index in enumerate(mapping):
        if int(source_index) == int(source_face_index):
            seed = flat_index
            break
    if seed is None:
        return set()
    return flat_face_island(unfold_obj, seed)


def set_island_highlight(source_obj, source_faces):
    island_highlight.update({
        "source": source_obj.name if source_obj else "",
        "source_faces": sorted(int(v) for v in source_faces),
    })
    _tag_redraw()


def clear_island_highlight():
    island_highlight.update({
        "source": "",
        "source_faces": [],
    })
    _tag_redraw()


def raycast_any_visible(context, event):
    if context.area is None or context.area.type != 'VIEW_3D':
        return None

    region = next(
        (r for r in context.area.regions if r.type == 'WINDOW'),
        None,
    )
    rv3d = context.space_data.region_3d
    if region is None or rv3d is None:
        return None

    coord = (
        event.mouse_x - region.x,
        event.mouse_y - region.y,
    )

    if (
        coord[0] < 0
        or coord[1] < 0
        or coord[0] >= region.width
        or coord[1] >= region.height
    ):
        return None

    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(
        region,
        rv3d,
        coord,
    ).normalized()

    depsgraph = context.evaluated_depsgraph_get()
    hit, location, normal, face_index, hit_obj, matrix = context.scene.ray_cast(
        depsgraph,
        origin,
        direction,
    )

    if not hit or hit_obj is None or face_index < 0:
        return None

    original = hit_obj.original if hasattr(hit_obj, "original") else hit_obj
    return original, int(face_index)


def save_annotations(source_obj, annotations):
    """注記を書き込み、キャッシュを捨てる。

    保存の実装は truescale.marking.storage。
    キャッシュ破棄は描画側の都合なので、ここで渡す。
    """
    _storage.save(
        source_obj,
        annotations,
        on_changed=invalidate_layout_cache,
    )
