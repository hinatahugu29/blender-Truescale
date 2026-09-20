"""寸法の基準になるバウンディングボックス。

対象のモデルを囲む直方体を作り、その辺の長さを寸法として出す。
モデルそのものではなく箱を測るのは、凹凸のある形でも「全体の
縦横高さ」という読み方ができるようにするため。

■ 元の形を追いかける

モデルを編集したら箱も追従する。毎フレーム測り直すと重いので、
形の特徴（頂点数と境界の座標）を控えておき、変わったときだけ
作り直す。

追従の最中に箱を作り直すと、それがまた depsgraph の更新を呼び、
無限に往復する。更新中であることを印に持って、入れ子を止める。

■ 元が消えたときの後始末

元のモデルを消しても箱だけ残ると、何を測っているのか分からない
オブジェクトが残る。depsgraph のハンドラで見て、一緒に消す。

■ 旧バージョンの控えを引き取る

以前の接頭辞で保存された情報があれば読み替える。読み替えないと、
前のバージョンで作った .blend を開いたときに寸法表示が消える。
"""

import mathutils
import bpy
from bpy.app.handlers import persistent

from .. import debug as _debug
from ..core import units as _units
from . import overlay as _overlay
from . import keys as _keys


def redraw_viewports(self=None, context=None):
    wm = bpy.context.window_manager
    if wm is None:
        return

    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def update_bbox_visibility(self=None, context=None):
    bbox = bpy.data.objects.get(_keys.BBOX_NAME)

    # 実オブジェクトは常時隠し、ビュー別表示はGPU描画へ任せる
    if bbox is not None:
        bbox.hide_set(True)

    _overlay.ensure_bbox_draw_handler()
    redraw_viewports()


def tsdraft_import_legacy_state():
    namespace = bpy.app.driver_namespace

    if not namespace.get(_keys.DATA_KEY):
        legacy_data = namespace.get(_keys.LEGACY_DATA_KEY)
        if legacy_data:
            namespace[_keys.DATA_KEY] = legacy_data

    if not namespace.get(_keys.SOURCE_KEY):
        legacy_source = namespace.get(_keys.LEGACY_SOURCE_KEY)
        if legacy_source:
            namespace[_keys.SOURCE_KEY] = legacy_source


def tsdraft_resolve_source_object(context):
    tsdraft_import_legacy_state()
    namespace = bpy.app.driver_namespace

    # 1. 従来の一時記憶
    source_name = namespace.get(_keys.SOURCE_KEY)
    if source_name:
        obj = bpy.data.objects.get(source_name)
        if obj is not None:
            return obj

    # 2. Bounding Box自身に保存した永続情報
    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)
    if bbox_obj is not None:
        source_name = bbox_obj.get("tsdraft_source_name")
        if source_name:
            obj = bpy.data.objects.get(source_name)
            if obj is not None:
                namespace[_keys.SOURCE_KEY] = obj.name
                return obj

    # 3. 現在のアクティブメッシュから復旧
    active = getattr(context, "active_object", None)
    if (
        active is not None
        and active.type == 'MESH'
        and active.name != _keys.BBOX_NAME
    ):
        namespace[_keys.SOURCE_KEY] = active.name
        if bbox_obj is not None:
            bbox_obj["tsdraft_source_name"] = active.name
        return active

    # 4. 選択中のメッシュが1個だけならそれを採用
    selected_meshes = [
        obj for obj in getattr(context, "selected_objects", [])
        if obj.type == 'MESH' and obj.name != _keys.BBOX_NAME
    ]
    if len(selected_meshes) == 1:
        obj = selected_meshes[0]
        namespace[_keys.SOURCE_KEY] = obj.name
        if bbox_obj is not None:
            bbox_obj["tsdraft_source_name"] = obj.name
        return obj

    # 5. シーン内に候補メッシュが1個だけなら最後の救済
    candidates = [
        obj for obj in context.scene.objects
        if obj.type == 'MESH' and obj.name != _keys.BBOX_NAME
    ]
    if len(candidates) == 1:
        obj = candidates[0]
        namespace[_keys.SOURCE_KEY] = obj.name
        if bbox_obj is not None:
            bbox_obj["tsdraft_source_name"] = obj.name
        return obj

    return None


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

    if namespace.get(_keys.AUTO_FOLLOW_GUARD_KEY):
        return False

    if not getattr(scene, "tsdraft_auto_follow", True):
        return False

    source_name = namespace.get(_keys.SOURCE_KEY)
    if not source_name:
        # Fall back to the persistent name stored on the BBox object.
        bbox_existing = bpy.data.objects.get(_keys.BBOX_NAME)
        if bbox_existing is not None:
            source_name = bbox_existing.get("tsdraft_source_name")
            if source_name:
                namespace[_keys.SOURCE_KEY] = source_name

    if not source_name:
        return False

    src = bpy.data.objects.get(source_name)
    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)

    if src is None or bbox_obj is None or src.type != 'MESH':
        return False

    bounds = tsdraft_get_source_bounds_local(src, depsgraph)
    if bounds is None:
        return False

    signature = tsdraft_auto_follow_signature(src, bounds)
    if not force and namespace.get(_keys.AUTO_FOLLOW_SIGNATURE_KEY) == signature:
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

    namespace[_keys.AUTO_FOLLOW_GUARD_KEY] = True

    try:
        mesh = bbox_obj.data

        # Rebuild only the tiny 8-vertex box mesh.
        mesh.clear_geometry()
        mesh.from_pydata(verts, edges, [])
        mesh.update()

        bbox_obj.matrix_world = src.matrix_world.copy()
        bbox_obj["tsdraft_source_name"] = src.name

        # 1 BU が何メートルか。シーンの Unit Scale を直に読んではいけない。
        # このアドオンには「シーンを見ず、アドオンの中だけで基準を決める」
        # モードがある。直読みすると、型紙側が 1 BU = 40mm で計算して
        # いるのに、こちらは 1000mm で測る、ということが起きる。
        # 実際にそうなっていた（同じファイルで 25 倍の食い違い）。
        unit_scale = _units.scene_scale_to_meters(scene)

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

        namespace[_keys.DATA_KEY] = dimension_data
        namespace[_keys.AUTO_FOLLOW_SIGNATURE_KEY] = signature

    finally:
        namespace[_keys.AUTO_FOLLOW_GUARD_KEY] = False

    if request_redraw:
        redraw_viewports()
    return True


# depsgraph のハンドラは @persistent が要る。
# 付いていないと、.blend を開いた時点で Blender が
# 一覧から外してしまい、以後まったく呼ばれなくなる。
@persistent
def size_bbox_auto_follow_handler(scene, depsgraph):
    try:
        # The depsgraph event itself already schedules viewport redraws.
        # Do not force-redraw every VIEW_3D area again from inside the handler.
        tsdraft_update_bbox_from_source(scene, depsgraph, force=False, request_redraw=False)
    except Exception:
        # Never let the measurement helper break Blender's depsgraph.
        pass


# depsgraph のハンドラは @persistent が要る。
# 付いていないと、.blend を開いた時点で Blender が
# 一覧から外してしまい、以後まったく呼ばれなくなる。
@persistent
def size_bbox_cleanup_handler(scene, depsgraph):
    namespace = bpy.app.driver_namespace
    source_name = namespace.get(_keys.SOURCE_KEY)

    if not source_name:
        return

    source_exists = bpy.data.objects.get(source_name) is not None
    bbox_exists = bpy.data.objects.get(_keys.BBOX_NAME) is not None

    if not source_exists:
        bbox = bpy.data.objects.get(_keys.BBOX_NAME)

        if bbox is not None:
            bpy.data.objects.remove(bbox, do_unlink=True)

        namespace[_keys.DATA_KEY] = []
        namespace[_keys.SOURCE_KEY] = None
        redraw_viewports()
        return

    if not bbox_exists:
        namespace[_keys.DATA_KEY] = []
        namespace[_keys.SOURCE_KEY] = None
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
