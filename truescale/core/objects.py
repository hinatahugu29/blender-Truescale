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

import json

import bpy

from .. import debug as _debug

from . import mapping as _mapping

from . import session as _session

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

    # Fallback to any visible generated unfold Mesh.
    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("tsunfold_generated", False))
            and not candidate.hide_viewport
        ):
            return candidate

    # Last resort: any generated unfold Mesh, even if hidden.
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


def boundary_segments_world_xy(obj, skip=None):
    """外周の線分。skip に頂点番号の組を渡すと、その辺は返さない。

    糊代のタブが付いた辺は、根元が折り線になる。元の実線が
    残っていると、そこで切られてタブが落ちる。
    """
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

        if skip and key in skip:
            continue

        a, b = key
        pa = obj.matrix_world @ mesh.vertices[a].co
        pb = obj.matrix_world @ mesh.vertices[b].co
        segments.append(((pa.x, pa.y), (pb.x, pb.y)))

    return segments


def seam_source(context):
    name = context.scene.get(_session.SEAM_SOURCE, "")
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == 'MESH':
        return obj

    active = context.active_object
    if active is not None:
        if (
            active.type == 'MESH'
            and not bool(active.get("tsunfold_generated", False))
        ):
            return active

        if bool(active.get("tsunfold_generated", False)):
            src = bpy.data.objects.get(
                active.get("tsunfold_source", "")
            )
            if src is not None and src.type == 'MESH':
                return src

    return None


def delete_generated_for_source(context, source_obj):
    """Delete stale generated pattern/layout objects before regeneration."""
    if source_obj is None:
        return 0

    old_meshes = [
        obj
        for obj in list(bpy.data.objects)
        if (
            obj.type == 'MESH'
            and bool(obj.get("tsunfold_generated", False))
            and obj.get("tsunfold_source", "") == source_obj.name
        )
    ]

    total = 0

    for obj in old_meshes:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
        total += 1

    context.scene.tsunfold_pattern_preview = False
    context.scene.tsunfold_preview = False
    context.scene[_session.PREVIEW_PREV_ACTIVE] = ""
    _invalidate_caches()

    return total


def _invalidate_caches():
    """描画キャッシュを捨てる。

    marking.interact はこちらを使うので、逆向きに import できない。
    呼ばれた時に読み込む。
    """
    from ..marking import interact
    interact.invalidate_layout_cache()


def detach(obj):
    """型紙をアドオンの管理から外し、ただのメッシュにする。

    アドオンは全ての判断を tsunfold_ で始まるカスタムプロパティで
    行っている。それを外せば、生成物としては扱われなくなる。

      片付けの対象から外れる（消されない）
      マーキングが描かれなくなる
      型紙として解決されなくなる

    消すキーを並べて書かないこと。プロパティを足したときに
    追従されず、消し残しが出る。接頭辞で拾えば必ず一致する。
    同じ失敗を register / unregister でやったことがある。

    戻り値は外したキーの数。
    """
    if obj is None:
        return 0

    keys = [key for key in obj.keys() if str(key).startswith("tsunfold_")]
    for key in keys:
        try:
            del obj[key]
        except Exception:
            _debug.swallowed("core.objects.detach")

    return len(keys)
