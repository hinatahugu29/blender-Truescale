"""左右対称のシーム付け。

片側にシームを入れたら、反対側の対応する辺にも同じものを入れる。
衣装や部品は左右対称なことが多く、片側ずつ手で入れると必ずどこかが
食い違う。

■ 対応する辺の探し方

座標を鏡映して、その位置にある辺を探す。頂点の番号は当てにならない
（対称に作っても番号が対応するとは限らない）ので、位置で照合する。

厳密に一致する辺だけを対象にする。近いものを拾うと、対称でない
場所に勝手にシームが入り、型紙の形が変わってしまう。入らなかった
ことは分かるが、間違って入ったことは気付きにくい。

■ どの軸で鏡映するか

シーンの設定（X/Y/Z のどれで対称か）に従う。複数指定されている
場合は、その組み合わせすべてを試す。
"""

import bpy

from .. import debug as _debug
from ..core import session as _session
from ..core.view import tag_redraw as _view_tag_redraw


def mirrored_point(p, mirror_x=False, mirror_y=False, mirror_z=False):
    q = p.copy()
    if mirror_x:
        q.x *= -1.0
    if mirror_y:
        q.y *= -1.0
    if mirror_z:
        q.z *= -1.0
    return q


def find_mirrored_edge(
    mesh,
    source_edge,
    mirror_x=False,
    mirror_y=False,
    mirror_z=False,
):
    if not mirror_x and not mirror_y and not mirror_z:
        return source_edge

    a_idx, b_idx = source_edge.vertices
    a = mesh.vertices[a_idx].co.copy()
    b = mesh.vertices[b_idx].co.copy()

    ma = mirrored_point(
        a,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        mirror_z=mirror_z,
    )
    mb = mirrored_point(
        b,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        mirror_z=mirror_z,
    )

    # Strict matching avoids the old "nearest unrelated edge" problem.
    avg_len = 0.0
    if mesh.edges:
        for e in mesh.edges:
            p1 = mesh.vertices[e.vertices[0]].co
            p2 = mesh.vertices[e.vertices[1]].co
            avg_len += (p2 - p1).length
        avg_len /= max(len(mesh.edges), 1)

    tol = max(avg_len * 0.08, 1e-6)

    nearest_a = None
    nearest_b = None
    best_a = float("inf")
    best_b = float("inf")

    for v in mesh.vertices:
        da = (v.co - ma).length
        if da < best_a:
            best_a = da
            nearest_a = v.index

        db = (v.co - mb).length
        if db < best_b:
            best_b = db
            nearest_b = v.index

    if (
        nearest_a is None
        or nearest_b is None
        or best_a > tol
        or best_b > tol
        or nearest_a == nearest_b
    ):
        return None

    wanted = {nearest_a, nearest_b}
    for e in mesh.edges:
        if set(e.vertices) == wanted:
            return e

    return None


def mirror_variants(scene):
    """どの軸で鏡映するかの組み合わせ。

    設定でXとYが有効なら、X反転・Y反転・XY反転の3通りを返す
    （無反転を含む4通りから、呼ぶ側が無反転を飛ばす）。
    """
    use_x = bool(getattr(scene, "tsunfold_seam_symmetry_x", False))
    use_y = bool(getattr(scene, "tsunfold_seam_symmetry_y", False))
    use_z = bool(getattr(scene, "tsunfold_seam_symmetry_z", False))

    variants = []
    for mx in ([False, True] if use_x else [False]):
        for my in ([False, True] if use_y else [False]):
            for mz in ([False, True] if use_z else [False]):
                variants.append((mx, my, mz))

    return variants


def sync_mesh_symmetry(context, obj=None):
    """Drive Blender's native Edit Mode symmetry from the helper XYZ toggles."""
    if obj is None:
        obj = context.active_object

    if obj is None or obj.type != 'MESH':
        return

    mesh = obj.data
    mesh.use_mirror_x = bool(
        getattr(context.scene, "tsunfold_seam_symmetry_x", False)
    )
    mesh.use_mirror_y = bool(
        getattr(context.scene, "tsunfold_seam_symmetry_y", False)
    )
    mesh.use_mirror_z = bool(
        getattr(context.scene, "tsunfold_seam_symmetry_z", False)
    )


def apply_to_selected_edges(context, clear=False):
    """Apply seam/clear to selected edges and exact XYZ mirrored mates.

    Selection is preserved. Knife topology mirroring itself is delegated to
    Blender's native Mesh.use_mirror_x/y/z settings.
    """
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return 0

    sync_mesh_symmetry(context, obj)

    if obj.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')

    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = obj.data

    selected = [e for e in mesh.edges if e.select]
    if not selected:
        bpy.ops.object.mode_set(mode='EDIT')
        return 0

    targets = set(e.index for e in selected)
    variants = mirror_variants(context.scene)

    for edge in selected:
        for mx, my, mz in variants:
            if not mx and not my and not mz:
                continue

            mirrored = find_mirrored_edge(
                mesh,
                edge,
                mirror_x=mx,
                mirror_y=my,
                mirror_z=mz,
            )
            if mirrored is not None:
                targets.add(mirrored.index)

    for edge_idx in targets:
        e = mesh.edges[edge_idx]
        e.use_seam = not clear
        e.select = True

    mesh.update()
    bpy.ops.object.mode_set(mode='EDIT')
    context.tool_settings.mesh_select_mode = (False, True, False)

    return len(targets)
