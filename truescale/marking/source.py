"""元モデル（3D側）に描く注記の位置計算。

型紙（平面）側とは別に、元の3Dモデルの上にも合印などを出せる。
どのシームのどこに印が付くのかを、立体を見ながら確認するため。

型紙側は平面なので2Dで済むが、こちらは3D上の辺に沿って位置を出し、
面の向きを見て法線方向へ少し浮かせる必要がある。メッシュに埋まって
見えなくなるのを避けるため。

なお既定では「軽量ビュー」が有効で、この計算は行われない。
高密度メッシュで重くなるのを避けるための設定だが、そのぶん
3Dモデル上には何も表示されない。
"""

import math

from mathutils import Vector

from .. import debug as _debug
from ..core import objects as _objects
from ..core import units as _units
from . import storage as _storage


def anchor_point_local(source_obj, anchor):
    try:
        tri = [int(v) for v in anchor["tri"]]
        w = [float(v) for v in anchor["w"]]
    except Exception:
        return None

    if len(tri) != 3 or len(w) != 3:
        return None

    if any(v < 0 or v >= len(source_obj.data.vertices) for v in tri):
        return None

    a = source_obj.data.vertices[tri[0]].co
    b = source_obj.data.vertices[tri[1]].co
    c = source_obj.data.vertices[tri[2]].co

    return a * w[0] + b * w[1] + c * w[2]


def notch_segment(context, source_obj, item):
    try:
        edge_index = int(item.get("edge", -1))
        fraction = float(item.get("t", 0.5))
    except Exception:
        return None

    if not (0 <= edge_index < len(source_obj.data.edges)):
        return None

    edge = source_obj.data.edges[edge_index]
    if not bool(edge.use_seam):
        return None
    a_local = source_obj.data.vertices[edge.vertices[0]].co
    b_local = source_obj.data.vertices[edge.vertices[1]].co

    p_local = a_local.lerp(b_local, fraction)
    mw = source_obj.matrix_world

    a_world = mw @ a_local
    b_world = mw @ b_local
    p_world = mw @ p_local

    tangent = b_world - a_world
    if tangent.length <= 1e-12:
        return None
    tangent.normalize()

    polys = _objects.edge_adjacent_polygons(source_obj.data, edge)
    normal = Vector((0.0, 0.0, 0.0))

    for poly in polys:
        normal += source_obj.matrix_world.to_3x3() @ poly.normal

    if normal.length <= 1e-12:
        normal = Vector((0.0, 0.0, 1.0))
    else:
        normal.normalize()

    across = normal.cross(tangent)
    if across.length <= 1e-12:
        return None
    across.normalize()

    length = _units.scene_mm_to_bu(
        context.scene,
        float(getattr(context.scene, "tsunfold_notch_length_mm", 6.0))
    )

    p1 = p_world - across * length * 0.5
    p2 = p_world + across * length * 0.5
    return p1, p2


def seam_segments(source_obj):
    """World-space seam edges for the source model, using live Edit Mode data."""
    if source_obj is None or source_obj.type != 'MESH':
        return []

    mw = source_obj.matrix_world
    result = []

    if source_obj.mode == 'EDIT':
        try:
            import bmesh
            bm = bmesh.from_edit_mesh(source_obj.data)

            for edge in bm.edges:
                if not bool(getattr(edge, "seam", False)):
                    continue
                a = mw @ edge.verts[0].co
                b = mw @ edge.verts[1].co
                result.append((a, b))

            return result
        except Exception:
            _debug.swallowed("_pattern_source_seam_segments")

    try:
        source_obj.update_from_editmode()
    except Exception:
        _debug.swallowed("_pattern_source_seam_segments")

    for edge in source_obj.data.edges:
        if not edge.use_seam:
            continue
        a = mw @ source_obj.data.vertices[edge.vertices[0]].co
        b = mw @ source_obj.data.vertices[edge.vertices[1]].co
        result.append((a, b))

    return result


def colored_segments(context, source_obj):
    segments = []
    size = _units.scene_mm_to_bu(context.scene, 8.0)
    mw = source_obj.matrix_world
    normal_matrix = mw.to_3x3()

    for item in _storage.load(source_obj):
        kind = item.get("type")
        color = _storage.scene_item_color(item, context.scene)

        if kind == "notch_edge":
            seg = notch_segment(context, source_obj, item)
            if seg is not None:
                notch_thickness = float(
                    getattr(
                        context.scene,
                        "tsunfold_notch_thickness_mm",
                        0.6,
                    )
                )
                segments.append((
                    seg[0],
                    seg[1],
                    color,
                    notch_thickness,
                ))

        elif kind == "arrow":
            if str(
                getattr(context.scene, "tsunfold_arrow_mode", "AUTO")
            ) == "NONE":
                continue

            pa = anchor_point_local(
                source_obj, item.get("a", {})
            )
            pb = anchor_point_local(
                source_obj, item.get("b", {})
            )
            if pa is None or pb is None:
                continue

            a = mw @ pa
            b = mw @ pb
            thickness = float(item.get("thickness_mm", 0.8))
            head = _units.scene_mm_to_bu(
                context.scene,
                float(item.get("head_mm", 8.0)),
            )
            segments.append((a, b, color, thickness))

            direction = b - a
            if direction.length > 1e-12:
                direction.normalize()
                n = normal_matrix @ Vector((0.0, 0.0, 1.0))
                side = direction.cross(n)
                if side.length <= 1e-12:
                    side = Vector((1.0, 0.0, 0.0))
                side.normalize()

                back = b - direction * head
                segments.append(
                    (b, back + side * head * 0.45, color, thickness)
                )
                segments.append(
                    (b, back - side * head * 0.45, color, thickness)
                )

    return segments
