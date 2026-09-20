"""UV から実寸の型紙メッシュを作り、紙の上に並べる。

■ 実寸はどこから来るか

UV は 0〜1 の比率でしかないので、そのままでは長さを持たない。元モデルの
辺の実長と UV 上の辺の長さの比を全辺で取り、その中央値を倍率とする。
平均ではなく中央値なのは、歪んだ面がひとつあるだけで平均が引きずられる
ため。辺長は matrix_world で測るので、適用していないスケールや親の
スケールも自動的に反映される。

■ 生成物には元の情報を持たせる

型紙メッシュには、どの頂点・面・辺が元のどれに当たるかを JSON で埋める。
あとから合印を元モデル側と型紙側の両方に出すために要る。生成時の
シーンスケールも記録する。あとで単位設定を変えられても、その型紙が
どの前提で作られたかが分かるようにするため。

■ 並べ替えは移動だけ

実寸が命なので、用紙に収めるための拡大縮小は決してしない。収まらない
場合は収まらないと返し、頂点座標は元へ戻す。中途半端に縮んだ型紙は、
黙って間違ったものを刷ることになるため。
"""

import json
from statistics import median

import bpy

from .. import debug as _debug
from ..core import geometry as _geometry
from ..core import paper as _paper
from ..core import units as _units

# 生成した型紙オブジェクトの名前に付く接尾辞。
UNFOLD_SUFFIX = "_展開図"


def world_edge_length(obj, v1, v2):
    """Physical source edge length in current Scene Blender Units.

    Full matrix_world is used so unapplied object scale, parent scale,
    rotation, and other object transforms are reflected automatically.
    """
    mw = obj.matrix_world
    p1 = mw @ v1.co
    p2 = mw @ v2.co
    return (p2 - p1).length


def real_scale(obj, mesh, uv_layer):
    ratios = []
    eps = 1e-10

    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        if len(loops) < 2:
            continue

        for i, li_a in enumerate(loops):
            li_b = loops[(i + 1) % len(loops)]
            loop_a = mesh.loops[li_a]
            loop_b = mesh.loops[li_b]
            uv_a = uv_layer.data[li_a].uv
            uv_b = uv_layer.data[li_b].uv

            uv_len = (uv_b - uv_a).length
            if uv_len <= eps:
                continue

            v_a = mesh.vertices[loop_a.vertex_index]
            v_b = mesh.vertices[loop_b.vertex_index]
            world_len = world_edge_length(obj, v_a, v_b)

            if world_len > eps:
                ratios.append(world_len / uv_len)

    return median(ratios) if ratios else None


def flat_mesh(context, src_obj, mesh, uv_layer, scale_bu_per_uv):
    verts = []
    faces = []
    vert_map = {}
    flat_vertex_source_vertex = []
    flat_face_source_face = []
    flat_side_source_edge = {}

    source_edge_lookup = {
        tuple(sorted((int(e.vertices[0]), int(e.vertices[1])))): int(e.index)
        for e in mesh.edges
    }

    def uv_key(loop_index):
        loop = mesh.loops[loop_index]
        uv = uv_layer.data[loop_index].uv
        return (
            loop.vertex_index,
            round(float(uv.x), 8),
            round(float(uv.y), 8),
        )

    for poly in mesh.polygons:
        face = []
        loop_indices = list(poly.loop_indices)

        for li in loop_indices:
            loop = mesh.loops[li]
            key = uv_key(li)
            idx = vert_map.get(key)

            if idx is None:
                uv = uv_layer.data[li].uv
                idx = len(verts)
                vert_map[key] = idx
                verts.append((
                    float(uv.x) * scale_bu_per_uv,
                    float(uv.y) * scale_bu_per_uv,
                    0.0,
                ))
                flat_vertex_source_vertex.append(int(loop.vertex_index))

            face.append(idx)

        if len(face) >= 3:
            faces.append(face)
            flat_face_source_face.append(int(poly.index))

            for i, li in enumerate(loop_indices):
                lj = loop_indices[(i + 1) % len(loop_indices)]

                sva = int(mesh.loops[li].vertex_index)
                svb = int(mesh.loops[lj].vertex_index)
                source_edge = source_edge_lookup.get(
                    tuple(sorted((sva, svb))),
                    -1,
                )

                fva = int(face[i])
                fvb = int(face[(i + 1) % len(face)])
                flat_side_source_edge[tuple(sorted((fva, fvb)))] = source_edge

    if not verts or not faces:
        return None

    mesh_name = f"{src_obj.name}{UNFOLD_SUFFIX}_Mesh"
    obj_name = f"{src_obj.name}{UNFOLD_SUFFIX}"

    new_mesh = bpy.data.meshes.new(mesh_name)
    new_mesh.from_pydata(verts, [], faces)
    new_mesh.update()

    new_obj = bpy.data.objects.new(obj_name, new_mesh)
    context.collection.objects.link(new_obj)

    new_obj.location = (0.0, 0.0, 0.0)
    new_obj.rotation_euler = (0.0, 0.0, 0.0)
    new_obj.scale = (1.0, 1.0, 1.0)

    new_obj["tsunfold_generated"] = True
    new_obj["tsunfold_source"] = src_obj.name

    # Record the unit context used to create this pattern.
    scene_scale, mm_per_bu = _units.scene_unit_summary(context.scene)
    new_obj["tsunfold_scene_scale_length"] = float(scene_scale)
    new_obj["tsunfold_mm_per_bu"] = float(mm_per_bu)
    new_obj["tsunfold_source_object_scale"] = [
        float(src_obj.scale.x),
        float(src_obj.scale.y),
        float(src_obj.scale.z),
    ]

    new_obj["tsunfold_flat_vertex_source_json"] = json.dumps(
        flat_vertex_source_vertex
    )
    new_obj["tsunfold_flat_face_source_json"] = json.dumps(
        flat_face_source_face
    )

    flat_edge_source = [-1] * len(new_mesh.edges)
    for edge in new_mesh.edges:
        key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        flat_edge_source[edge.index] = int(
            flat_side_source_edge.get(key, -1)
        )

    new_obj["tsunfold_flat_edge_source_json"] = json.dumps(
        flat_edge_source
    )

    for o in context.selected_objects:
        o.select_set(False)

    new_obj.select_set(True)
    context.view_layer.objects.active = new_obj
    return new_obj


def pack_islands(context, obj, spacing_mm):
    """Pack islands left-to-right. Translation only, never scaling."""
    if not obj or obj.type != 'MESH':
        return

    mesh = obj.data
    islands = _geometry.face_islands(mesh)
    if not islands:
        return

    spacing_bu = _units.scene_mm_to_bu(context.scene, max(0.0, spacing_mm))

    data = []
    for ids in islands:
        xs = [mesh.vertices[i].co.x for i in ids]
        ys = [mesh.vertices[i].co.y for i in ids]
        data.append({
            "verts": ids,
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
        })

    # Stable order based on current left-to-right position.
    data.sort(key=lambda c: (c["min_x"], c["min_y"]))

    cursor_x = 0.0
    baseline_y = 0.0

    for comp in data:
        dx = cursor_x - comp["min_x"]
        dy = baseline_y - comp["min_y"]

        for vi in comp["verts"]:
            mesh.vertices[vi].co.x += dx
            mesh.vertices[vi].co.y += dy

        width = comp["max_x"] - comp["min_x"]
        cursor_x += width + spacing_bu

    mesh.update()


def object_xy_size_mm(context, obj):
    if obj is None or obj.type != 'MESH' or not obj.data.vertices:
        return None

    points = [obj.matrix_world @ v.co for v in obj.data.vertices]
    min_x = min(p.x for p in points)
    max_x = max(p.x for p in points)
    min_y = min(p.y for p in points)
    max_y = max(p.y for p in points)

    return (
        _units.scene_bu_to_mm(context.scene, max_x - min_x),
        _units.scene_bu_to_mm(context.scene, max_y - min_y),
    )


def show_from_top(context, obj):
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj

    try:
        bpy.ops.view3d.view_axis(type='TOP', align_active=False)
        bpy.ops.view3d.view_selected(use_all_regions=False)
    except RuntimeError:
        _debug.swallowed("unfold.build._show_generated_from_top")


def try_shelf_layout(context, obj, allow_rotate=True):
    """Simple deterministic paper packing.

    Uses island bounding rectangles, current paper size/orientation, and
    current spacing. No scaling is ever applied.
    """
    mesh = obj.data
    islands = _geometry.face_islands(mesh)
    if not islands:
        return False, "アイランドがありません"

    scene = context.scene
    paper_w_mm, paper_h_mm = _paper.scene_dimensions_mm(scene)
    paper_w = _units.scene_mm_to_bu(scene, paper_w_mm)
    paper_h = _units.scene_mm_to_bu(scene, paper_h_mm)

    # Printer-safe margin must be defined before usable area is calculated.
    safe = _units.scene_mm_to_bu(scene, 10.0)
    gap = _units.scene_mm_to_bu(scene, 5.0)

    usable_w = max(0.0, paper_w - safe * 2.0)
    usable_h = max(0.0, paper_h - safe * 2.0)

    # Snapshot original coordinates so failure can restore everything.
    original = {v.index: v.co.copy() for v in mesh.vertices}

    # Work largest-first, tends to pack more reliably.
    items = []
    for ids in islands:
        min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
        w = max_x - min_x
        h = max_y - min_y
        items.append({
            "verts": ids,
            "w": w,
            "h": h,
            "area": w * h,
        })

    items.sort(key=lambda it: (max(it["w"], it["h"]), it["area"]), reverse=True)

    cursor_x = 0.0
    cursor_y = 0.0
    row_h = 0.0

    for item in items:
        ids = item["verts"]
        min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
        w = max_x - min_x
        h = max_y - min_y

        # If the island cannot fit the current row, consider rotation first
        # when that makes it fit horizontally and within paper height.
        rotated = False

        def fits_here(test_w, test_h):
            return (
                cursor_x + test_w <= usable_w + 1e-9
                and cursor_y + test_h <= usable_h + 1e-9
            )

        if allow_rotate:
            rw, rh = h, w
            normal_ok = fits_here(w, h)
            rotated_ok = fits_here(rw, rh)

            # Prefer rotation if normal does not fit but rotated does,
            # or if both fit and rotation wastes less remaining row width.
            if rotated_ok and (
                not normal_ok
                or (usable_w - (cursor_x + rw)) < (usable_w - (cursor_x + w))
            ):
                _geometry.rotate_vertices_90(mesh, ids)
                rotated = True
                min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
                w = max_x - min_x
                h = max_y - min_y

        # Start a new row if needed.
        if cursor_x > 0.0 and cursor_x + w > usable_w + 1e-9:
            cursor_x = 0.0
            cursor_y += row_h + gap
            row_h = 0.0

            # Re-evaluate rotation for the fresh row.
            if allow_rotate:
                min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
                w = max_x - min_x
                h = max_y - min_y
                rw, rh = h, w

                normal_ok = (
                    w <= usable_w + 1e-9
                    and cursor_y + h <= usable_h + 1e-9
                )
                rotated_ok = (
                    rw <= usable_w + 1e-9
                    and cursor_y + rh <= usable_h + 1e-9
                )

                if rotated_ok and (
                    not normal_ok
                    or (usable_w - rw) < (usable_w - w)
                ):
                    _geometry.rotate_vertices_90(mesh, ids)
                    rotated = not rotated
                    min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
                    w = max_x - min_x
                    h = max_y - min_y

        # Hard failure: no scaling, no clipping.
        if (
            w > usable_w + 1e-9
            or cursor_y + h > usable_h + 1e-9
        ):
            for vi, co in original.items():
                mesh.vertices[vi].co = co
            mesh.update()
            return False, (
                f"{paper_w_mm:.0f}×{paper_h_mm:.0f} mm の用紙に "
                "すべてのアイランドを収められませんでした"
            )

        _geometry.move_island_to(mesh, ids, cursor_x, cursor_y)

        cursor_x += w + gap
        row_h = max(row_h, h)

    # Center the final packed layout inside the safe printable area.
    all_x = [v.co.x for v in mesh.vertices]
    all_y = [v.co.y for v in mesh.vertices]

    if all_x and all_y:
        min_x = min(all_x)
        max_x = max(all_x)
        min_y = min(all_y)
        max_y = max(all_y)

        packed_w = max_x - min_x
        packed_h = max_y - min_y

        target_min_x = safe + max(0.0, (usable_w - packed_w) * 0.5)
        target_min_y = safe + max(0.0, (usable_h - packed_h) * 0.5)

        dx = target_min_x - min_x
        dy = target_min_y - min_y

        for v in mesh.vertices:
            v.co.x += dx
            v.co.y += dy

    mesh.update()
    return True, (
        f"{_paper.scene_display_name(scene)} / 周囲10mm余白で中央配置しました"
    )
