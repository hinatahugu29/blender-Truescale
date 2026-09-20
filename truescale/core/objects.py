"""オブジェクトの解決。

「いま対象にしている元モデルはどれか」「その型紙はどれか」を、
選択状態とカスタムプロパティから決める。

型紙オブジェクトには生成時に印を付けてある。

  tsunfold_generated … 型紙として生成したものである
  tsunfold_source    … 元になったモデルの名前

元モデルを選んでいても型紙を選んでいても、そこから相手を辿れるように
両方向から解決する。パネルやオペレータが「どちらが選ばれていても
動く」ようにするため。
"""

import bpy

from . import mapping as _mapping

GENERATED_PROP = "tsunfold_generated"
SOURCE_PROP = "tsunfold_source"


def source_from_context(context):
    obj = context.active_object

    if (
        obj is not None
        and obj.type == 'MESH'
        and not bool(obj.get("tsunfold_generated", False))
    ):
        return obj

    if obj is not None and bool(obj.get("tsunfold_generated", False)):
        name = obj.get("tsunfold_source", "")
        src = bpy.data.objects.get(name)
        if src is not None and src.type == 'MESH':
            return src

    if (
        obj is not None
        and obj.type == 'CURVE'
        and bool(obj.get("tsunfold_smooth_generated", False))
    ):
        unfold_name = obj.get("tsunfold_smooth_source", "")
        unfold = bpy.data.objects.get(unfold_name)
        if unfold is not None:
            name = unfold.get("tsunfold_source", "")
            src = bpy.data.objects.get(name)
            if src is not None and src.type == 'MESH':
                return src

    return None


def unfold_for_source(source_obj):
    if source_obj is None:
        return None

    for obj in bpy.data.objects:
        if (
            obj.type == 'MESH'
            and bool(obj.get("tsunfold_generated", False))
            and obj.get("tsunfold_source", "") == source_obj.name
        ):
            return obj

    return None


def active_unfold(context):
    obj = context.active_object
    if (
        obj
        and obj.type == 'MESH'
        and bool(obj.get("tsunfold_generated", False))
    ):
        return obj
    return None


def resolve_unfold_for_layout(context):
    """Resolve the underlying generated unfold Mesh for layout operations."""
    obj = context.active_object

    # Directly selected unfold Mesh.
    if (
        obj is not None
        and obj.type == 'MESH'
        and bool(obj.get("tsunfold_generated", False))
    ):
        return obj

    # Smooth Curve selected: resolve back to its source unfold Mesh.
    if (
        obj is not None
        and obj.type == 'CURVE'
        and bool(obj.get("tsunfold_smooth_generated", False))
    ):
        source_name = obj.get("tsunfold_source", "")
        if source_name:
            candidate = bpy.data.objects.get(source_name)
            if (
                candidate is not None
                and candidate.type == 'MESH'
                and bool(candidate.get("tsunfold_generated", False))
            ):
                return candidate

        # Fallback: smooth object may carry source unfold name under another key.
        source_name = obj.get("tsunfold_unfold_source", "")
        if source_name:
            candidate = bpy.data.objects.get(source_name)
            if (
                candidate is not None
                and candidate.type == 'MESH'
                and bool(candidate.get("tsunfold_generated", False))
            ):
                return candidate

    # Fallback to any visible generated unfold Mesh.
    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("tsunfold_generated", False))
            and not candidate.hide_viewport
        ):
            return candidate

    # Last resort: any generated unfold Mesh, even if hidden by smooth display.
    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("tsunfold_generated", False))
        ):
            return candidate

    return None


def flat_face_source_map(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("tsunfold_flat_face_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def edge_adjacent_polygons(mesh, edge):
    key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
    result = []

    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            if tuple(sorted(edge_key)) == key:
                result.append(poly)
                break

    return result


def boundary_segments_world_xy(obj):
    mesh = obj.data
    edge_key_count = {}

    for poly in mesh.polygons:
        verts = list(poly.vertices)
        n = len(verts)

        for i in range(n):
            a = verts[i]
            b = verts[(i + 1) % n]
            key = (a, b) if a < b else (b, a)
            edge_key_count[key] = edge_key_count.get(key, 0) + 1

    segments = []
    for key, count in edge_key_count.items():
        if count != 1:
            continue

        a, b = key
        pa = obj.matrix_world @ mesh.vertices[a].co
        pb = obj.matrix_world @ mesh.vertices[b].co
        segments.append(((pa.x, pa.y), (pb.x, pb.y)))

    return segments
