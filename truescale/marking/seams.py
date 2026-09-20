"""シームを辿って、合印を置く位置を決める。

■ トレイル

シームは1本の線とは限らない。分岐したり、閉じた輪になったりする。
そのままでは「何分割して印を置くか」を決められないので、
分岐点で区切った連なり（トレイル）へ分解する。

端点や分岐点から伸びるものを先に取り、残りを閉じた輪として扱う。

■ 位置の決め方

トレイル全体のワールド長を測り、指定された分割数で等分した位置を返す。
各位置は (辺のインデックス, その辺上の比率) で表す。
辺の番号と比率で持つのは、型紙を作り直しても同じ場所へ復元できるため。
"""

from .. import debug as _debug


def sync_live_seams(source_obj):
    """Push current Edit Mode seam state into Mesh datablock before analysis."""
    if source_obj is None or source_obj.type != 'MESH':
        return

    if source_obj.mode == 'EDIT':
        try:
            import bmesh
            bm = bmesh.from_edit_mesh(source_obj.data)

            # Force BMesh -> Mesh sync.
            bmesh.update_edit_mesh(
                source_obj.data,
                loop_triangles=False,
                destructive=False,
            )
        except Exception:
            _debug.swallowed("marking._pattern_sync_live_seams")

    try:
        source_obj.update_from_editmode()
    except Exception:
        _debug.swallowed("marking._pattern_sync_live_seams")

    try:
        source_obj.data.update()
    except Exception:
        _debug.swallowed("marking._pattern_sync_live_seams")


def seam_trails(source_obj):
    """Return ordered seam trails as lists of (edge_index, from_v, to_v).

    A trail is a continuous seam line. Degree != 2 vertices break trails;
    closed seam loops are handled separately.
    """
    if source_obj is None or source_obj.type != 'MESH':
        return []

    sync_live_seams(source_obj)

    mesh = source_obj.data
    seam_edges = {
        int(e.index): e
        for e in mesh.edges
        if bool(e.use_seam)
    }

    if not seam_edges:
        return []

    adjacency = {}
    for edge_index, edge in seam_edges.items():
        v0 = int(edge.vertices[0])
        v1 = int(edge.vertices[1])
        adjacency.setdefault(v0, []).append(edge_index)
        adjacency.setdefault(v1, []).append(edge_index)

    unused = set(seam_edges.keys())
    trails = []

    def other_vertex(edge_index, vertex):
        edge = seam_edges[edge_index]
        a = int(edge.vertices[0])
        b = int(edge.vertices[1])
        return b if a == vertex else a

    def walk(start_vertex, first_edge):
        trail = []
        current_v = start_vertex
        current_e = first_edge

        while current_e in unused:
            unused.remove(current_e)
            next_v = other_vertex(current_e, current_v)
            trail.append((current_e, current_v, next_v))

            candidates = [
                eidx
                for eidx in adjacency.get(next_v, [])
                if eidx in unused
            ]

            # End at an endpoint / junction. A new trail starts there later.
            if len(adjacency.get(next_v, [])) != 2:
                break

            if not candidates:
                break

            current_v = next_v
            current_e = candidates[0]

        return trail

    # Open trails and branch-to-branch segments first.
    special_vertices = sorted(
        vertex
        for vertex, edges in adjacency.items()
        if len(edges) != 2
    )

    for vertex in special_vertices:
        for edge_index in list(adjacency.get(vertex, [])):
            if edge_index not in unused:
                continue
            trail = walk(vertex, edge_index)
            if trail:
                trails.append(trail)

    # Remaining edges are closed loops.
    while unused:
        first_edge = min(unused)
        edge = seam_edges[first_edge]
        start_vertex = int(edge.vertices[0])
        trail = walk(start_vertex, first_edge)
        if trail:
            trails.append(trail)

    return trails


def trail_mark_positions(context, source_obj, trail, divisions):
    """Return (edge_index, t) positions equally spaced along trail world length."""
    divisions = max(2, int(divisions))
    mesh = source_obj.data
    mw = source_obj.matrix_world

    pieces = []
    total = 0.0

    for edge_index, from_v, to_v in trail:
        a = mw @ mesh.vertices[from_v].co
        b = mw @ mesh.vertices[to_v].co
        length = (b - a).length
        if length <= 1e-12:
            continue

        pieces.append((edge_index, from_v, to_v, total, length))
        total += length

    if total <= 1e-12:
        return []

    result = []

    for step in range(1, divisions):
        target = total * (step / divisions)

        for edge_index, from_v, to_v, start_len, edge_len in pieces:
            if target > start_len + edge_len + 1e-12:
                continue

            local_t = (target - start_len) / edge_len
            local_t = max(0.0, min(1.0, local_t))

            edge = mesh.edges[edge_index]
            ev0 = int(edge.vertices[0])
            ev1 = int(edge.vertices[1])

            # Stored notch fraction is relative to edge.vertices[0] -> [1].
            if from_v == ev0 and to_v == ev1:
                stored_t = local_t
            else:
                stored_t = 1.0 - local_t

            result.append((int(edge_index), round(float(stored_t), 7)))
            break

    return result
