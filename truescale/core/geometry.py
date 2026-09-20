"""平面化した型紙の幾何処理。

アイランドの抽出、2Dの内外判定、境界からの距離など。
どれも bpy の Mesh を読むだけで、シーンやUIの状態には触らない。

内外判定と距離計算は、型紙IDや矢印の配置探索から数万回呼ばれる。
Blender の RNA へ毎回アクセスすると支配的なコストになるため、
座標を素のタプルへ展開してから回す作りにしてある。
"""

import math

from mathutils import Vector

from . import state as _state


def face_islands(mesh):
    """Return disconnected face islands as lists of vertex indices."""
    if not mesh.polygons:
        return []

    vert_to_polys = {i: [] for i in range(len(mesh.vertices))}
    for poly in mesh.polygons:
        for vi in poly.vertices:
            vert_to_polys[vi].append(poly.index)

    unvisited = set(range(len(mesh.polygons)))
    islands = []

    while unvisited:
        start = unvisited.pop()
        stack = [start]
        poly_ids = [start]

        while stack:
            pi = stack.pop()
            poly = mesh.polygons[pi]

            for vi in poly.vertices:
                for neighbor in vert_to_polys[vi]:
                    if neighbor in unvisited:
                        unvisited.remove(neighbor)
                        stack.append(neighbor)
                        poly_ids.append(neighbor)

        vert_ids = set()
        for pi in poly_ids:
            vert_ids.update(mesh.polygons[pi].vertices)

        if vert_ids:
            islands.append(sorted(vert_ids))

    return islands


def island_bbox(mesh, vert_ids):
    xs = [mesh.vertices[i].co.x for i in vert_ids]
    ys = [mesh.vertices[i].co.y for i in vert_ids]
    return min(xs), min(ys), max(xs), max(ys)


def rotate_vertices_90(mesh, vert_ids):
    """Rotate one disconnected island +90 degrees around its local bbox center."""
    xs = [mesh.vertices[i].co.x for i in vert_ids]
    ys = [mesh.vertices[i].co.y for i in vert_ids]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5

    for vi in vert_ids:
        v = mesh.vertices[vi].co
        x = v.x - cx
        y = v.y - cy
        v.x = cx - y
        v.y = cy + x


def move_island_to(mesh, vert_ids, target_min_x, target_min_y):
    min_x, min_y, max_x, max_y = island_bbox(mesh, vert_ids)
    dx = target_min_x - min_x
    dy = target_min_y - min_y

    for vi in vert_ids:
        mesh.vertices[vi].co.x += dx
        mesh.vertices[vi].co.y += dy


def flat_polygons_2d(mesh):
    """面ごとの2D座標を、素のPythonタプルで返す。

    戻り値は面インデックス順の [(minx, miny, maxx, maxy, [(x, y), ...]), ...]。

    内外判定は配置探索から数万回呼ばれる。毎回 mesh.polygons[i] や
    mesh.vertices[vi].co を辿ると、そのたびにBlenderのRNAアクセスが
    発生して支配的なコストになる。座標は探索中に変わらないので、
    一度だけ素のタプルへ展開して使い回す。

    バウンディングボックスも一緒に持つ。ほとんどの点はほとんどの面の
    外側にあるので、多角形の走査に入る前に弾ける。
    """
    key = (
        mesh.name,
        len(mesh.vertices),
        len(mesh.polygons),
        int(_state.epoch),
    )
    if _state.flat_poly_cache["key"] == key:
        return _state.flat_poly_cache["polys"]

    coords = [(float(v.co.x), float(v.co.y)) for v in mesh.vertices]

    polys = []
    for poly in mesh.polygons:
        points = [coords[int(vi)] for vi in poly.vertices]
        if len(points) < 3:
            polys.append(None)
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        polys.append((min(xs), min(ys), max(xs), max(ys), points))

    _state.flat_poly_cache["key"] = key
    _state.flat_poly_cache["polys"] = polys
    return polys


def point_in_polys_2d(polys, face_indices, x, y):
    """内外判定の本体。座標は素のfloatで受け取る。

    探索ループから数万回呼ばれるので、呼び出し側で polys を一度だけ
    取得して渡す。毎回 _pattern_flat_polygons_2d を引くと、
    キャッシュのキーを組み立てるだけで無視できないコストになる。
    """
    count = len(polys)

    for face_index in face_indices:
        index = int(face_index)
        if not (0 <= index < count):
            continue

        entry = polys[index]
        if entry is None:
            continue

        min_x, min_y, max_x, max_y, points = entry
        # 面の外接矩形の外なら、多角形を走査するまでもない
        if x < min_x or x > max_x or y < min_y or y > max_y:
            continue

        inside = False
        j = len(points) - 1

        for i in range(len(points)):
            xi, yi = points[i]
            xj, yj = points[j]

            if (yi > y) != (yj > y):
                denominator = yj - yi
                if abs(denominator) <= 1.0e-12:
                    denominator = 1.0e-12
                if x < (xj - xi) * (y - yi) / denominator + xi:
                    inside = not inside

            j = i

        if inside:
            return True

    return False


def point_in_island_2d(mesh, face_indices, point):
    """2D point-in-island test using the flattened mesh polygons."""
    return point_in_polys_2d(
        flat_polygons_2d(mesh),
        face_indices,
        float(point.x),
        float(point.y),
    )


def island_boundary_segments(mesh, face_indices):
    """Collect only the visible boundary segments of one flat island."""
    edge_counts = {}
    edge_points = {}

    valid_faces = {
        int(fi)
        for fi in face_indices
        if 0 <= int(fi) < len(mesh.polygons)
    }

    for fi in valid_faces:
        poly = mesh.polygons[fi]
        verts = [int(v) for v in poly.vertices]

        for i, va in enumerate(verts):
            vb = verts[(i + 1) % len(verts)]
            key = tuple(sorted((va, vb)))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            edge_points[key] = (
                mesh.vertices[va].co.copy(),
                mesh.vertices[vb].co.copy(),
            )

    return [
        edge_points[key]
        for key, count in edge_counts.items()
        if count == 1
    ]


def boundary_as_floats(boundary):
    """境界セグメントを素のfloatへ展開する。

    (ax, ay, dx, dy, denom) の並び。denom は線分長の2乗で、
    射影パラメータの計算に使う。毎回 Vector の属性を読み直すより速い。
    """
    result = []
    for a, b in boundary:
        ax = float(a.x)
        ay = float(a.y)
        dx = float(b.x) - ax
        dy = float(b.y) - ay
        result.append((ax, ay, dx, dy, dx * dx + dy * dy))
    return result


def any_point_too_close(points_xy, boundary_flat, margin):
    """境界からの距離が margin 未満の点が1つでもあるか。

    以前は全セグメントとの距離の最小値を求めてから比較していたが、
    1つでも近い点が見つかればそこで打ち切れる。
    平方距離で比較して平方根の計算も省く。
    """
    if margin <= 0.0 or not boundary_flat:
        return False

    margin_sq = margin * margin

    for px, py in points_xy:
        for ax, ay, dx, dy, denom in boundary_flat:
            if denom <= 1.0e-20:
                ox = px - ax
                oy = py - ay
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / denom
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                ox = px - (ax + dx * t)
                oy = py - (ay + dy * t)

            if ox * ox + oy * oy < margin_sq:
                return True

    return False


def min_clearance(px, py, boundary_flat):
    """点から境界までの最短距離。

    候補の良さを測るので最小値そのものが必要。打ち切れない代わりに、
    平方距離で回して最後に一度だけ平方根を取る。
    """
    if not boundary_flat:
        return 0.0

    best = None
    for ax, ay, dx, dy, denom in boundary_flat:
        if denom <= 1.0e-20:
            ox = px - ax
            oy = py - ay
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            ox = px - (ax + dx * t)
            oy = py - (ay + dy * t)

        distance_sq = ox * ox + oy * oy
        if best is None or distance_sq < best:
            best = distance_sq

    return math.sqrt(best) if best is not None else 0.0
