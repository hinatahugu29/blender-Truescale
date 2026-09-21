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
from ..export import allowance as _allowance

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


def edge_ratios(obj, mesh, uv_layer):
    """辺ごとの「実長 ÷ UV長」。倍率も歪み率も、ここから出す。

    倍率は中央値、歪み率はばらつき。同じリストの別の見方でしかない
    ので、二度数えない。
    """
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

    return ratios


def real_scale(obj, mesh, uv_layer):
    """採用する倍率。1 UV が何 BU にあたるか。"""
    ratios = edge_ratios(obj, mesh, uv_layer)
    return median(ratios) if ratios else None


def flat_mesh(context, src_obj, mesh, uv_layer, scale_bu_per_uv,
              distortion=None):
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

    # 展開でどれだけ縮んだか。作ったときの UV でしか測れないので、
    # ここで持たせる。あとから型紙メッシュだけ見ても出せない。
    if distortion:
        new_obj["tsunfold_distortion"] = [
            float(distortion[0]),
            float(distortion[1]),
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


# 型紙を手で並べたか。型紙オブジェクトに持たせる（.blend に残る）。
#
# 設定を変えたときに自動で並べ直してよいかを、これで決める。
# 手で並べた配置を黙って崩すと、利用者は二度と並べ直す気に
# ならない。MANUAL_LAYOUT_ACTIVE は動かしている最中しか立って
# いないので、この用途には使えない。
HAND_PLACED_PROP = "tsunfold_hand_placed"


def hand_placed(obj):
    return obj is not None and bool(obj.get(HAND_PLACED_PROP, False))


def set_hand_placed(obj, value):
    if obj is not None:
        obj[HAND_PLACED_PROP] = bool(value)


def layout_islands(scene, mesh):
    """並べる単位の一覧。(頂点番号, 外へ出る幅) の組。

    外へ出る幅は縫い代・糊代のぶん（export.allowance.island_reach）。
    並べる側はどれも、島をこの幅だけ太らせた箱として扱う。
    輪郭だけで詰めると、隣の島の裁断線と重なる。
    """
    edges = (
        _allowance.edge_lookup(mesh) if _allowance.enabled(scene) else None
    )
    out = []
    for faces in _geometry.face_island_polys(mesh):
        verts = set()
        for fi in faces:
            verts.update(mesh.polygons[fi].vertices)
        if verts:
            out.append((
                sorted(verts), _allowance.island_reach(scene, mesh, faces, edges)
            ))
    return out


def _padded_bbox(mesh, ids, pad):
    min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
    return min_x - pad, min_y - pad, max_x + pad, max_y + pad


def _move_padded(mesh, ids, x, y, pad):
    """太らせた箱の左下を (x, y) へ置く。"""
    _geometry.move_island_to(mesh, ids, x + pad, y + pad)


def pack_islands(context, obj, spacing_mm):
    """Pack islands left-to-right. Translation only, never scaling."""
    if not obj or obj.type != 'MESH':
        return

    mesh = obj.data
    islands = layout_islands(context.scene, mesh)
    if not islands:
        return

    spacing_bu = _units.scene_mm_to_bu(context.scene, max(0.0, spacing_mm))

    data = []
    for ids, pad in islands:
        min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
        data.append({
            "verts": ids,
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
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
    set_hand_placed(obj, False)


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
    scene = context.scene
    islands = layout_islands(scene, mesh)
    if not islands:
        return False, "アイランドがありません"

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
    for ids, pad in islands:
        min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
        w = max_x - min_x
        h = max_y - min_y
        items.append({
            "verts": ids,
            "pad": pad,
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
        pad = item["pad"]
        min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
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
                min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
                w = max_x - min_x
                h = max_y - min_y

        # Start a new row if needed.
        if cursor_x > 0.0 and cursor_x + w > usable_w + 1e-9:
            cursor_x = 0.0
            cursor_y += row_h + gap
            row_h = 0.0

            # Re-evaluate rotation for the fresh row.
            if allow_rotate:
                min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
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
                    min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
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

        _move_padded(mesh, ids, cursor_x, cursor_y, pad)

        cursor_x += w + gap
        row_h = max(row_h, h)

    # Center the final packed layout inside the safe printable area.
    # 縫い代・糊代まで含めた外形で測る。頂点だけで測ると、
    # そのぶん紙の端へ寄る。
    boxes = [
        _padded_bbox(mesh, item["verts"], item["pad"]) for item in items
    ]

    if boxes:
        min_x = min(b[0] for b in boxes)
        min_y = min(b[1] for b in boxes)
        max_x = max(b[2] for b in boxes)
        max_y = max(b[3] for b in boxes)

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
    set_hand_placed(obj, False)
    return True, (
        f"{_paper.scene_display_name(scene)} / 周囲10mm余白で中央配置しました"
    )


def _shelf_fill(mesh, islands, usable_w, gap, allow_rotate=True):
    """決めた幅の中へ、島を左から右・上から下へ詰める。

    islands は layout_islands の戻り値。縫い代・糊代のぶん太らせて詰める。

    紙に収まるかは見ない。とにかくその幅で詰めたら縦がどれだけに
    なるかを返す。用紙をまたぐ配置を決めるのに使う。

    戻り値は (実際に使った幅, 高さ)。拡大縮小はしない。
    """
    items = []
    for ids, pad in islands:
        min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
        items.append({
            "verts": ids,
            "pad": pad,
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
        pad = item["pad"]
        min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
        width = max_x - min_x
        height = max_y - min_y

        # 幅に入らないなら、回して入るか試す
        if allow_rotate and width > usable_w and height <= usable_w:
            _geometry.rotate_vertices_90(mesh, ids)
            min_x, min_y, max_x, max_y = _padded_bbox(mesh, ids, pad)
            width = max_x - min_x
            height = max_y - min_y

        # 今の段に入らなければ次の段へ
        if cursor_x > 0.0 and cursor_x + width > usable_w + 1e-9:
            cursor_x = 0.0
            cursor_y += row_h + gap
            row_h = 0.0

        _move_padded(mesh, ids, cursor_x, cursor_y, pad)

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
    content_w, content_h = content_size(scene)

    ok, _message, _cols, _rows = pack_for_pages(
        context, obj, content_w, content_h
    )
    if not ok:
        # 島がひとつも無いなど。従来どおり横一列にしておく。
        pack_islands(context, obj, scene.tsunfold_spacing_mm)


# 目盛りとタイル名を置く帯の高さ。紙の下側に確保する。
# export.sheets と同じ値でなければならない。片方だけ変えると、
# 並べ替えが「収めたつもり」で1段はみ出す。
FOOTER_MM = 12.0


def content_size(scene, margin_mm=None):
    """用紙のうち、型紙を載せてよい範囲（ミリ）。

    刷れない余白と、目盛りを置く帯を引いた残り。並べ替えも分割も
    この範囲を前提にする。別々に計算すると食い違い、ほとんど空の
    紙が出る。実際それで1列ぶん無駄になっていた。
    """
    if margin_mm is None:
        margin_mm = float(getattr(scene, "tsunfold_tile_margin_mm", 8.0))

    # 型紙のまわりに空ける余白。集める側は外形にこれを含めるので、
    # 詰め込むときはその分だけ狭い範囲を狙う。ここで引かないと、
    # 枠いっぱいに詰めたものが余白のぶんはみ出す。
    inset_mm = max(0.0, float(
        getattr(scene, "tsunfold_pattern_inset_mm", 0.0)
    ))

    paper_w, paper_h = _paper.scene_dimensions_mm(scene)
    return (
        paper_w - margin_mm * 2.0 - inset_mm * 2.0,
        paper_h - margin_mm * 2.0 - FOOTER_MM - inset_mm * 2.0,
    )


def _span(content, step, count):
    """紙を count 枚並べたときに載る長さ。

    最初の1枚は受け持ちぶん丸ごと。2枚目からは、隣と重なる分だけ
    実入りが減る。この式を分割側と揃えないと、「収めたつもり」が
    1列はみ出して、ほぼ空の紙が出る。
    """
    if count <= 0:
        return 0.0
    return content + step * (count - 1)


def _rows_needed(height, content, step):
    """その高さを収めるのに要る段数。"""
    if height <= content + 1e-9:
        return 1
    return 1 + int(math.ceil((height - content - 1e-9) / step))


def pack_for_pages(context, obj, content_w_mm, content_h_mm, max_pages_wide=8,
                   overlap_mm=None):
    """紙をまたいでよい前提で、枚数が少なくなるように詰める。

    1枚に収まらない型紙は、これまで「収まりません」で終わりだった。
    その結果、島は作ったときのまま横一列に並び続ける。球のような
    島の多い形だと 3 メートルの帯になり、A4 で 18 枚の横長になる。

    ここでは「紙を何枚横に並べるか」を 1 から順に試し、必要な枚数が
    最も少なくなる並べ方を選ぶ。拡大縮小はしない。

    戻り値は (成功したか, 説明, 列数, 行数)。
    """
    mesh = obj.data
    scene = context.scene
    islands = layout_islands(scene, mesh)
    if not islands:
        return False, "アイランドがありません", 0, 0

    if overlap_mm is None:
        overlap_mm = float(getattr(scene, "tsunfold_tile_overlap_mm", 15.0))

    content_w = _units.scene_mm_to_bu(scene, content_w_mm)
    content_h = _units.scene_mm_to_bu(scene, content_h_mm)
    overlap = _units.scene_mm_to_bu(scene, max(0.0, overlap_mm))
    gap = _units.scene_mm_to_bu(
        scene, max(0.0, float(getattr(scene, "tsunfold_spacing_mm", 5.0)))
    )

    if content_w <= 0.0 or content_h <= 0.0:
        return False, "用紙に対して余白が大きすぎます", 0, 0

    step_w = content_w - overlap
    step_h = content_h - overlap
    if step_w <= 0.0 or step_h <= 0.0:
        return False, "のりしろが用紙に対して大きすぎます", 0, 0

    original = {v.index: v.co.copy() for v in mesh.vertices}

    best = None
    for pages_wide in range(1, max_pages_wide + 1):
        # 試すたびに元へ戻す。前の試行の回転が残ると結果が変わる。
        for index, co in original.items():
            mesh.vertices[index].co = co

        width = _span(content_w, step_w, pages_wide)
        used_w, used_h = _shelf_fill(mesh, islands, width, gap)

        # 実際に何列・何段になるかは、分割側と同じ式で数える
        cols = 1
        while _span(content_w, step_w, cols) < used_w - 1e-9:
            cols += 1
        rows = _rows_needed(used_h, content_h, step_h)
        sheets = cols * rows

        # 枚数が同じなら、紙の余りが少ないほうを選ぶ。
        #
        # 見た目の良さで「四角い並び」を選ぶようにしたら、かえって
        # 悪くなった。段の境目をわずかに超えると、そのためだけに
        # ほとんど空の段が1つ増える。余りを見れば、その形は自然に
        # 避けられる。
        waste = (
            (_span(content_w, step_w, cols) - used_w)
            + (_span(content_h, step_h, rows) - used_h)
        )
        score = (sheets, waste)
        if best is None or score < best["score"]:
            best = {
                "score": score,
                "sheets": sheets,
                "wide": pages_wide,
                "cols": cols,
                "rows": rows,
            }

    # 選んだ並べ方でもう一度詰め直す
    for index, co in original.items():
        mesh.vertices[index].co = co
    _shelf_fill(mesh, islands, _span(content_w, step_w, best["wide"]), gap)
    set_hand_placed(obj, False)

    cols = best["cols"]
    rows = best["rows"]

    return (
        True,
        f"{cols}×{rows} = {cols * rows} 枚に収まるよう並べました",
        cols,
        rows,
    )
