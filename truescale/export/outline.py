"""PNG へ書き出す線を集める。

画面に見えているものと印刷されるものは同じではない。ここが決めるのは
「紙に出る線」だけで、用紙ガイドや島のハイライトは含めない。

■ 何を書き出すか

生成した型紙メッシュの外周。選択中のオブジェクトを優先し、無ければ
表示されている型紙を探す。
「選んでいないと書き出せない」を避けるため。

■ 文字は一度メッシュにしてから輪郭を取る

blf の文字は画面へ直接描くもので、線の座標が取れない。そのため一時的に
FONT オブジェクトを作り、評価済みメッシュの境界辺を拾って消す。
一時オブジェクトは finally で必ず片付ける。
"""

import bpy

from .. import debug as _debug
from ..core import paper as _paper
from ..core import units as _units


def _objects():
    """オブジェクト解決。循環importを避けるため呼ばれた時に読み込む。"""
    from ..core import objects
    return objects


def bbox(segments):
    pts = [p for seg in segments for p in seg]
    if not pts:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def current_finish_segments(context, skip=None):
    """いま表示している型紙の外周線。

    skip は外周から外す辺（頂点番号の組）。糊代のタブが付いた辺は
    根元が折り線になるので、元の実線を残すと切られてしまう。
    """
    obj = context.active_object

    if (
        obj
        and obj.type == 'MESH'
        and bool(obj.get("tsunfold_generated", False))
    ):
        return _objects().boundary_segments_world_xy(obj, skip=skip)

    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("tsunfold_generated", False))
            and not candidate.hide_viewport
        ):
            return _objects().boundary_segments_world_xy(candidate, skip=skip)

    return []


def can_export(context):
    obj = context.active_object
    if obj is None:
        return False

    return obj.type == 'MESH' and bool(obj.get("tsunfold_generated", False))


def text_segments(context, text, world_pos, size_mm, angle=0.0):
    """Create temporary Blender FONT geometry and return boundary segments."""
    if not text:
        return []

    curve = None
    obj = None
    mesh = None

    try:
        curve = bpy.data.curves.new(
            "型紙ヘルパー_TMP_TEXT",
            type='FONT',
        )
        curve.body = str(text)
        curve.align_x = 'CENTER'
        curve.align_y = 'CENTER'
        curve.size = _units.scene_mm_to_bu(
            context.scene,
            float(size_mm),
        )
        curve.extrude = 0.0
        curve.offset = 0.0

        obj = bpy.data.objects.new(
            "型紙ヘルパー_TMP_TEXT",
            curve,
        )
        context.scene.collection.objects.link(obj)
        obj.location = (
            float(world_pos.x),
            float(world_pos.y),
            0.0,
        )
        obj.rotation_euler[2] = float(angle)

        depsgraph = context.evaluated_depsgraph_get()
        depsgraph.update()

        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()

        edge_counts = {}
        edge_lookup = {}

        for edge in mesh.edges:
            key = tuple(sorted((
                int(edge.vertices[0]),
                int(edge.vertices[1]),
            )))
            edge_lookup[key] = int(edge.index)
            edge_counts[int(edge.index)] = 0

        for poly in mesh.polygons:
            for key in poly.edge_keys:
                idx = edge_lookup.get(tuple(sorted(key)))
                if idx is not None:
                    edge_counts[idx] += 1

        result = []

        for edge in mesh.edges:
            if edge_counts.get(int(edge.index), 0) != 1:
                continue

            a = obj.matrix_world @ mesh.vertices[edge.vertices[0]].co
            b = obj.matrix_world @ mesh.vertices[edge.vertices[1]].co
            result.append((a, b))

        eval_obj.to_mesh_clear()
        mesh = None
        return result

    except Exception:
        return []

    finally:
        if obj is not None and obj.name in bpy.data.objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                _debug.swallowed("export._pattern_text_outline_segments")

        if curve is not None and curve.users == 0:
            try:
                bpy.data.curves.remove(curve)
            except Exception:
                _debug.swallowed("export._pattern_text_outline_segments")
