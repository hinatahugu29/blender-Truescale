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
import math
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


def _shelf_fill(mesh, islands, usable_w, gap, allow_rotate=True):
    """決めた幅の中へ、島を左から右・上から下へ詰める。

    紙に収まるかは見ない。とにかくその幅で詰めたら縦がどれだけに
    なるかを返す。用紙をまたぐ配置を決めるのに使う。

    戻り値は (実際に使った幅, 高さ)。拡大縮小はしない。
    """
    items = []
    for ids in islands:
        min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
        items.append({
            "verts": ids,
            "w": max_x - min_x,
            "h": max_y - min_y,
        })

    # 背の高いものから置くと、棚の隙間が減る
    items.sort(key=lambda it: max(it["w"], it["h"]), reverse=True)

    cursor_x = 0.0
    cursor_y = 0.0
    row_h = 0.0
    used_w = 0.0

    for item in items:
        ids = item["verts"]
        min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
        width = max_x - min_x
        height = max_y - min_y

        # 幅に入らないなら、回して入るか試す
        if allow_rotate and width > usable_w and height <= usable_w:
            _geometry.rotate_vertices_90(mesh, ids)
            min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
            width = max_x - min_x
            height = max_y - min_y

        # 今の段に入らなければ次の段へ
        if cursor_x > 0.0 and cursor_x + width > usable_w + 1e-9:
            cursor_x = 0.0
            cursor_y += row_h + gap
            row_h = 0.0

        _geometry.move_island_to(mesh, ids, cursor_x, cursor_y)

        cursor_x += width + gap
        used_w = max(used_w, cursor_x - gap)
        row_h = max(row_h, height)

    mesh.update()
    return used_w, cursor_y + row_h


def initial_layout(context, obj):
    """型紙を作った直後の並び。用紙に沿って詰める。

    以前は横一列に並べていた（pack_islands）。島が30個ある形だと
    2 メートルの帯になり、そのまま書き出すと A4 で12枚の横長に
    なる。自動レイアウトを押せば直るが、押さないと使い物にならない
    並びを既定にする理由が無い。

    ここで用紙に沿えておけば、そのまま書き出しても筋の通った
    枚数になる。拡大縮小はしない。
    """
    scene = context.scene
    margin = float(getattr(scene, "tsunfold_tile_margin_mm", 8.0))
    footer = 12.0
    paper_w, paper_h = _paper.scene_dimensions_mm(scene)

    ok, _message, _cols, _rows = pack_for_pages(
        context,
        obj,
        paper_w - margin * 2.0,
        paper_h - margin * 2.0 - footer,
    )
    if not ok:
        # 島がひとつも無いなど。従来どおり横一列にしておく。
        pack_islands(context, obj, scene.tsunfold_spacing_mm)


def pack_for_pages(context, obj, content_w_mm, content_h_mm, max_pages_wide=8):
    """紙をまたいでよい前提で、枚数が少なくなるように詰める。

    1枚に収まらない型紙は、これまで「収まりません」で終わりだった。
    その結果、島は作ったときのまま横一列に並び続ける。球のような
    島の多い形だと 3 メートルの帯になり、A4 で 18 枚の横長になる。

    ここでは「紙を何枚横に並べるか」を 1 から順に試し、必要な枚数が
    最も少なくなる並べ方を選ぶ。拡大縮小はしない。

    戻り値は (成功したか, 説明, 列数, 行数)。
    """
    mesh = obj.data
    islands = _geometry.face_islands(mesh)
    if not islands:
        return False, "アイランドがありません", 0, 0

    scene = context.scene
    content_w = _units.scene_mm_to_bu(scene, content_w_mm)
    content_h = _units.scene_mm_to_bu(scene, content_h_mm)
    gap = _units.scene_mm_to_bu(
        scene, max(0.0, float(getattr(scene, "tsunfold_spacing_mm", 5.0)))
    )

    if content_w <= 0.0 or content_h <= 0.0:
        return False, "用紙に対して余白が大きすぎます", 0, 0

    original = {v.index: v.co.copy() for v in mesh.vertices}

    best = None
    for pages_wide in range(1, max_pages_wide + 1):
        # 試すたびに元へ戻す。前の試行の回転が残ると結果が変わる。
        for index, co in original.items():
            mesh.vertices[index].co = co

        width = content_w * pages_wide
        used_w, used_h = _shelf_fill(mesh, islands, width, gap)
        rows = max(1, int(math.ceil((used_h - 1e-9) / content_h)))
        sheets = pages_wide * rows

        if best is None or sheets < best["sheets"]:
            best = {
                "sheets": sheets,
                "wide": pages_wide,
                "rows": rows,
                "used_w": used_w,
                "used_h": used_h,
            }

    # 選んだ並べ方でもう一度詰め直す
    for index, co in original.items():
        mesh.vertices[index].co = co
    _shelf_fill(mesh, islands, content_w * best["wide"], gap)

    cols = max(
        1,
        int(math.ceil((_units.scene_bu_to_mm(scene, best["used_w"]) - 1e-9)
                      / content_w_mm)),
    )
    rows = best["rows"]

    return (
        True,
        f"{cols}×{rows} = {cols * rows} 枚に収まるよう並べました",
        cols,
        rows,
    )
