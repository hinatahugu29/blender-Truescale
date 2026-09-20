"""手動レイアウト中に、印を島と一緒に動かす。

手動レイアウトでは島を選んで G で動かす。動かしている最中も合印や
IDが見えていないと、「この島をここへ置くと合印が継ぎ目に乗る」と
いった判断ができない。

これまでは表示ごと止めていた。編集モード中の形は BMesh 側にあり、
obj.data へは反映されないので、そのまま描くと動かす前の位置に
出てしまうため。

■ 作り直さずに、ずらすだけで済む

手動レイアウトでやれるのは島の移動だけで、形は変わらない。
つまり印の位置関係も変わらない。始めた時点で一度だけ計算し、
あとは島ごとの移動量を足せばよい。

作り直すと1フレーム 39〜205ms かかる（島の数しだい）。
ドラッグ中にそれが毎フレーム走ると 5〜25 fps まで落ちる。
ずらすだけなら頂点を数えるだけで済む。

■ 頂点の数が変わったら諦める

移動だけのはずだが、編集モードでは何でもできる。頂点を足したり
消したりされたら、覚えている対応が合わなくなる。そのときは
表示を止める。間違った位置に出すより、出さないほうがよい。
"""

import bmesh
from mathutils import Vector

from .. import debug as _debug
from ..core import geometry as _geometry
from ..core import state as _state

# 始めた時点の控え。手動レイアウトの間だけ持つ。
_snapshot = {
    "object": "",
    "epoch": -1,
    "vertex_count": 0,
    # 島ごとの、控えた時点の重心（ローカル座標）
    "centroids": [],
    # 島ごとの頂点番号
    "islands": [],
    # 印: (始点, 終点, 色, 太さ, 島番号)
    "segments": [],
    # 文字: (文字, 位置, 大きさ, 色, 角度, 島番号)
    "texts": [],
    # 縫い代・糊代の切る線: (始点, 終点, 島番号)
    "cut": [],
    # 同じく折る線。控える時点で破線へ刻んである。
    "fold": [],
}


def clear():
    """控えを捨てる。手動レイアウトを抜けるときに呼ぶ。"""
    _snapshot["object"] = ""
    _snapshot["epoch"] = -1
    _snapshot["vertex_count"] = 0
    _snapshot["centroids"] = []
    _snapshot["islands"] = []
    _snapshot["segments"] = []
    _snapshot["texts"] = []
    _snapshot["cut"] = []
    _snapshot["fold"] = []


def _centroid(mesh, vertex_ids):
    if not vertex_ids:
        return Vector((0.0, 0.0, 0.0))
    total = Vector((0.0, 0.0, 0.0))
    for index in vertex_ids:
        total += mesh.vertices[index].co
    return total / len(vertex_ids)


def _island_of(islands, polys, point):
    """その点がどの島に属するか。属さなければ一番近い島。

    印は島の内側か、すぐ外側（合印は辺から外へ出る）にある。
    内側に無ければ重心が最も近い島に預ける。
    """
    best = 0
    best_distance = None

    for index, (_ids, center, box) in enumerate(polys):
        if (box[0] <= point.x <= box[2]) and (box[1] <= point.y <= box[3]):
            return index
        distance = (center - point).length_squared
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = index

    return best


def take_snapshot(context, source_obj, unfold_obj):
    """いまの印を控える。手動レイアウトを始めるときに1度だけ。"""
    from . import compute as _compute

    clear()

    if unfold_obj is None or unfold_obj.type != 'MESH':
        return False

    mesh = unfold_obj.data
    islands = _geometry.face_islands(mesh)
    if not islands:
        return False

    # 島ごとの重心と、判定に使う囲み
    polys = []
    for ids in islands:
        min_x, min_y, max_x, max_y = _geometry.island_bbox(mesh, ids)
        center = _centroid(mesh, ids)
        polys.append((ids, center, (min_x, min_y, max_x, max_y)))

    inverse = unfold_obj.matrix_world.inverted_safe()

    segments = []
    for wa, wb, color, width in _compute.colored_segments(
        context, source_obj, unfold_obj
    ):
        local_a = inverse @ Vector(wa)
        local_b = inverse @ Vector(wb)
        middle = (local_a + local_b) * 0.5
        segments.append(
            (local_a, local_b, color, width, _island_of(islands, polys, middle))
        )

    # 文字は書き出しと同じ一覧から取る。以前はここで型紙IDだけを
    # 拾っていたため、並べている最中はメモと転写ラベルが消えていた。
    # 出どころを2つ持つと、片方に足した文字がもう片方から漏れる。
    from ..export import collect as _collect

    texts = []
    for text, world_pos, size_mm, color, angle in _collect.text_sources(
        context, source_obj, unfold_obj
    ):
        local = inverse @ Vector(world_pos)
        texts.append(
            (text, local, size_mm, color, angle,
             _island_of(islands, polys, local))
        )

    # 縫い代と糊代。これも島と一緒に動く。控えずに毎フレーム
    # 作り直すと、面320の型紙で1フレーム 340ms かかる。
    from ..export import allowance as _allowance

    allow = _allowance.build(context, source_obj, unfold_obj)

    def by_island(items):
        out = []
        for ax, ay, bx, by in items:
            middle = Vector(((ax + bx) * 0.5, (ay + by) * 0.5, 0.0))
            out.append((
                Vector((ax, ay, 0.0)),
                Vector((bx, by, 0.0)),
                _island_of(islands, polys, middle),
            ))
        return out

    # 折り線はここで刻んでおく。刻みは平行移動で変わらないので、
    # 控えた時点で済ませてよい。毎フレーム刻み直す理由がない。
    from ..export import linestyle as _linestyle
    from ..core import units as _units

    dash = _units.scene_mm_to_bu(
        context.scene, _linestyle.FOLD_DASH_MM
    )
    gap = _units.scene_mm_to_bu(context.scene, _linestyle.FOLD_GAP_MM)

    chopped = []
    for ax, ay, bx, by in allow.fold:
        chopped.extend(_linestyle.dashed(ax, ay, bx, by, dash, gap))

    _snapshot["cut"] = by_island(allow.cut)
    _snapshot["fold"] = by_island(chopped)

    _snapshot["object"] = unfold_obj.name
    _snapshot["epoch"] = int(_state.epoch)
    _snapshot["vertex_count"] = len(mesh.vertices)
    _snapshot["centroids"] = [center for _ids, center, _box in polys]
    _snapshot["islands"] = [list(ids) for ids in islands]
    _snapshot["segments"] = segments
    _snapshot["texts"] = texts
    return True


def _offsets(unfold_obj):
    """島ごとの、控えた時点からの移動量。取れなければ None。

    編集モード中の形は BMesh 側にある。obj.data を読むと動かす前の
    位置になるので、そちらを直接見る。
    """
    if _snapshot["object"] != getattr(unfold_obj, "name", ""):
        return None

    try:
        mesh = bmesh.from_edit_mesh(unfold_obj.data)
    except Exception:
        _debug.swallowed("marking.dragging._offsets")
        return None

    if len(mesh.verts) != _snapshot["vertex_count"]:
        # 頂点が増減した。覚えている対応が合わない。
        return None

    mesh.verts.ensure_lookup_table()

    moved = []
    for index, ids in enumerate(_snapshot["islands"]):
        if not ids:
            moved.append(Vector((0.0, 0.0, 0.0)))
            continue
        total = Vector((0.0, 0.0, 0.0))
        for vertex in ids:
            total += mesh.verts[vertex].co
        moved.append(total / len(ids) - _snapshot["centroids"][index])

    return moved


def current(unfold_obj):
    """いまの印。(線の並び, 文字の並び)。出せなければ (None, None)。

    線は (始点, 終点, 色, 太さ) のワールド座標。
    """
    if not _snapshot["segments"] and not _snapshot["texts"]:
        return (None, None)

    moved = _offsets(unfold_obj)
    if moved is None:
        return (None, None)

    matrix = unfold_obj.matrix_world

    segments = [
        (
            matrix @ (a + moved[island]),
            matrix @ (b + moved[island]),
            color,
            width,
        )
        for a, b, color, width, island in _snapshot["segments"]
    ]

    texts = [
        (text, matrix @ (position + moved[island]), size, color, angle)
        for text, position, size, color, angle, island in _snapshot["texts"]
    ]

    return (segments, texts)


def current_allowance(unfold_obj):
    """いまの縫い代・糊代。(切る線, 折る線)。出せなければ (None, None)。

    線は (始点, 終点) のワールド座標。印と同じく、控えたものを
    島ごとの移動量だけずらす。手動レイアウトでやれるのは島の
    移動だけなので、形は変わらない。
    """
    if not _snapshot["cut"] and not _snapshot["fold"]:
        return (None, None)

    moved = _offsets(unfold_obj)
    if moved is None:
        return (None, None)

    matrix = unfold_obj.matrix_world

    def shift(items):
        return [
            (matrix @ (a + moved[island]), matrix @ (b + moved[island]))
            for a, b, island in items
        ]

    return (shift(_snapshot["cut"]), shift(_snapshot["fold"]))
