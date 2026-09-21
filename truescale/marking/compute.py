"""描画とPNG書き出しへ渡すデータを作る。

型紙IDの文字、上方向矢印、合印などを「どこに・どの向きで・どの色で」
置くかを計算し、線分や文字項目の一覧にして返す。

■ ローカル空間で計算してキャッシュする

重い計算（島内の配置探索など）はオブジェクトの位置に依存しない。
そこで型紙のローカル空間で計算してキャッシュし、ワールド変換だけを
キャッシュの外で毎回掛ける。

以前は matrix_world をキャッシュキーに含めていたため、オブジェクトを
動かすと毎フレーム作り直しになっていた。ビューを回すだけでは再現せず、
「移動したときだけ極端に重い」という分かりにくい症状になる。

■ キャッシュキーに入れるもの

計算結果に影響する設定は、必ずキーへ入れる。入れ忘れると
「値を変えても画面が変わらない」になる。逆に、結果に影響しない値を
入れるとキャッシュが効かなくなる。どちらも実際に起きた。
"""

import math

from mathutils import Vector

from .. import debug as _debug
from ..core import geometry as _geometry
from ..core import mapping as _mapping
from ..core import solve as _solve
from ..core import state as _state
from ..core import units as _units
from . import placement as _placement
from . import storage as _storage

_IDENTITY = None


def _identity():
    """恒等行列。ローカル空間で計算させるために渡す。"""
    global _IDENTITY
    if _IDENTITY is None:
        from mathutils import Matrix
        _IDENTITY = Matrix.Identity(4)
    return _IDENTITY


def readable_edge_angle(angle):
    """辺に沿った文字が上下逆さまにならない角度へ直す。

    接続先IDは辺に平行に置くが、そのままだと辺の向き次第で
    文字が逆さまになる。平行を保ったまま読める向きへ倒す。
    """
    while angle > math.pi:
        angle -= math.tau
    while angle <= -math.pi:
        angle += math.tau

    if angle > math.pi * 0.5:
        angle -= math.pi
    elif angle < -math.pi * 0.5:
        angle += math.pi

    return angle


def island_metadata(context, source_obj, unfold_obj):
    """Stable island IDs + seam-neighbor labels.

    IDs are sorted by the minimum original source-face index, so moving layout
    islands does not randomly renumber them.
    """

    if (
        source_obj is None
        or unfold_obj is None
        or unfold_obj.type != 'MESH'
    ):
        return [], {}, {}

    scene = context.scene
    cache_key = (
        unfold_obj.name,
        int(_state.epoch),
        len(unfold_obj.data.vertices),
        len(unfold_obj.data.edges),
        len(unfold_obj.data.polygons),
        str(getattr(scene, "tsunfold_island_id_style", "ALPHA")),
    )

    if _state.island_cache.get("key") == cache_key:
        return (
            _state.island_cache["records"],
            _state.island_cache["face_to_island"],
            _state.island_cache["adjacency"],
        )

    mesh = unfold_obj.data
    face_source = _mapping.flat_face_to_source(unfold_obj)

    vert_to_faces = {i: set() for i in range(len(mesh.vertices))}
    for poly in mesh.polygons:
        for vi in poly.vertices:
            vert_to_faces[int(vi)].add(int(poly.index))

    raw_records = []

    for verts in _geometry.face_islands(mesh):
        vert_set = set(int(v) for v in verts)
        faces = set()
        for vi in vert_set:
            faces.update(vert_to_faces.get(vi, set()))

        source_faces = {
            int(face_source[fi])
            for fi in faces
            if 0 <= fi < len(face_source)
        }

        coords = [mesh.vertices[vi].co.copy() for vi in vert_set]
        if not coords:
            continue

        center = sum(coords, Vector((0.0, 0.0, 0.0))) / len(coords)
        xs = [p.x for p in coords]
        ys = [p.y for p in coords]

        raw_records.append({
            "verts": vert_set,
            "faces": faces,
            "source_faces": source_faces,
            "face_indices": list(faces),
            "source_sort": min(source_faces) if source_faces else 10**12,
            "center_local": center,
            "bbox": (
                min(xs),
                min(ys),
                max(xs),
                max(ys),
            ),
        })

    raw_records.sort(
        key=lambda item: (
            item["source_sort"],
            item["bbox"][0],
            item["bbox"][1],
        )
    )

    records = []
    face_to_island = {}

    for index, item in enumerate(raw_records):
        item = dict(item)
        item["index"] = index
        item["label"] = island_label(scene, index)
        records.append(item)

        for face_index in item["faces"]:
            face_to_island[int(face_index)] = index

    # Build one neighbor label per island-pair connection.
    flat_edge_source = _mapping.flat_edge_to_source(unfold_obj)
    edge_faces = _mapping.flat_edge_faces(unfold_obj)

    by_source_edge = {}

    for edge in mesh.edges:
        if edge.index >= len(flat_edge_source):
            continue

        source_edge_index = int(flat_edge_source[edge.index])
        if not (0 <= source_edge_index < len(source_obj.data.edges)):
            continue

        if not bool(source_obj.data.edges[source_edge_index].use_seam):
            continue

        faces = edge_faces.get(int(edge.index), [])
        if len(faces) != 1:
            continue

        flat_face = int(faces[0])
        island_index = face_to_island.get(flat_face)
        if island_index is None:
            continue

        a = mesh.vertices[edge.vertices[0]].co
        b = mesh.vertices[edge.vertices[1]].co
        midpoint = (a + b) * 0.5

        tangent = b - a
        tangent.z = 0.0
        if tangent.length > 1e-12:
            tangent.normalize()

        poly_center = mesh.polygons[flat_face].center.copy()
        inward = poly_center - midpoint
        inward.z = 0.0
        if inward.length > 1e-12:
            inward.normalize()

        by_source_edge.setdefault(source_edge_index, []).append({
            "island": island_index,
            "mid": midpoint,
            "inward": inward,
            "tangent": tangent,
        })

    grouped = {}

    for entries in by_source_edge.values():
        islands = sorted(set(entry["island"] for entry in entries))
        if len(islands) < 2:
            continue

        for entry in entries:
            current = entry["island"]
            others = [v for v in islands if v != current]
            if not others:
                continue

            # Usually one counterpart. If more than one exists, use the first
            # stable island ID instead of multiplying labels on the same edge.
            other = others[0]
            grouped.setdefault((current, other), []).append(entry)

    adjacency = {}

    for (current, other), entries in grouped.items():
        if not entries:
            continue

        mid = sum(
            (entry["mid"] for entry in entries),
            Vector((0.0, 0.0, 0.0)),
        ) / len(entries)

        inward = sum(
            (entry["inward"] for entry in entries),
            Vector((0.0, 0.0, 0.0)),
        )

        if inward.length > 1e-12:
            inward.normalize()
        else:
            # Rare cancellation on a curved/multi-edge connection:
            # fall back to one face's known inward direction.
            inward = entries[0].get(
                "inward",
                Vector((0.0, 0.0, 0.0)),
            ).copy()
            if inward.length > 1e-12:
                inward.normalize()

        tangent = sum(
            (entry.get("tangent", Vector((1.0, 0.0, 0.0))) for entry in entries),
            Vector((0.0, 0.0, 0.0)),
        )
        if tangent.length <= 1e-12:
            tangent = entries[0].get(
                "tangent",
                Vector((1.0, 0.0, 0.0)),
            ).copy()
        if tangent.length <= 1e-12:
            tangent = Vector((1.0, 0.0, 0.0))
        tangent.normalize()

        # Keep the connection ID as close to the seam as possible,
        # but verify the whole text footprint is inside the island.
        connection_size_mm = float(
            getattr(
                context.scene,
                "tsunfold_island_id_size_mm",
                8.0,
            )
        )

        record = (
            records[current]
            if 0 <= int(current) < len(records)
            else None
        )
        face_indices = (
            list(record.get("face_indices", []))
            if record is not None
            else []
        )

        pos = connection_label_inside_position(
            context,
            mesh,
            face_indices,
            mid,
            inward,
            tangent,
            connection_size_mm,
        )

        adjacency.setdefault(current, []).append({
            "other": other,
            "pos_local": pos,
            "tangent_local": tangent,
        })

    _state.island_cache = {
        "key": cache_key,
        "records": records,
        "face_to_island": face_to_island,
        "adjacency": adjacency,
    }

    return records, face_to_island, adjacency


def island_label(scene, index):
    style = str(
        getattr(scene, "tsunfold_island_id_style", "ALPHA")
    )
    if style == "NUMBER":
        return str(int(index) + 1)
    return _placement.alpha_label(index)


def auto_up_vector(scene):
    """Return the selected Blender GLOBAL/WORLD positive axis."""
    mode = sanitize_arrow_axis(scene)

    if mode == "X":
        return Vector((1.0, 0.0, 0.0))
    if mode == "Y":
        return Vector((0.0, 1.0, 0.0))

    return Vector((0.0, 0.0, 1.0))


def auto_up_vector_source_local(scene, source_obj):
    """Convert the selected Blender world axis into source local space.

    The UI choice follows Blender's navigation gizmo/global axes, while the
    unfold correspondence math operates on source mesh local coordinates.
    """
    world_up = auto_up_vector(scene)

    try:
        local_up = (
            source_obj.matrix_world.to_3x3().inverted_safe()
            @ world_up
        )
    except Exception:
        local_up = world_up.copy()

    if local_up.length <= 1e-12:
        return Vector((0.0, 0.0, 1.0))

    local_up.normalize()
    return local_up


def sanitize_arrow_axis(scene):
    axis = str(
        getattr(
            scene,
            "tsunfold_arrow_up_axis",
            "Z",
        )
    )

    if axis not in {"X", "Y", "Z"}:
        try:
            scene.tsunfold_arrow_up_axis = "Z"
        except Exception:
            _debug.swallowed("marking._pattern_sanitize_arrow_axis")
        return "Z"

    return axis


def auto_arrow_direction_for_record(
    context,
    source_obj,
    unfold_obj,
    record,
):
    """Map the chosen Blender world axis onto the flat island.

    Fit a 2x3 linear map using corresponding original/flat EDGE vectors:
        flat_delta ~= M * source_delta
    Then evaluate M * up_vector.

    This is a vector mapping, unlike the old scalar-height gradient, and is
    noticeably more stable on skewed/distorted islands.
    """
    flat_to_source = _mapping.flat_vertex_to_source(unfold_obj)
    up = auto_up_vector_source_local(
        context.scene,
        source_obj,
    )

    vert_set = set(int(v) for v in record["verts"])
    mesh = unfold_obj.data

    normal = [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]
    rhs_x = [0.0, 0.0, 0.0]
    rhs_y = [0.0, 0.0, 0.0]
    sample_count = 0

    for edge in mesh.edges:
        f0 = int(edge.vertices[0])
        f1 = int(edge.vertices[1])

        if f0 not in vert_set or f1 not in vert_set:
            continue

        if (
            f0 >= len(flat_to_source)
            or f1 >= len(flat_to_source)
        ):
            continue

        s0 = int(flat_to_source[f0])
        s1 = int(flat_to_source[f1])

        if (
            not (0 <= s0 < len(source_obj.data.vertices))
            or not (0 <= s1 < len(source_obj.data.vertices))
        ):
            continue

        ds = (
            source_obj.data.vertices[s1].co
            - source_obj.data.vertices[s0].co
        )
        df = (
            mesh.vertices[f1].co
            - mesh.vertices[f0].co
        )

        if ds.length <= 1e-12 or df.length <= 1e-12:
            continue

        d = [float(ds.x), float(ds.y), float(ds.z)]

        for row in range(3):
            rhs_x[row] += d[row] * float(df.x)
            rhs_y[row] += d[row] * float(df.y)

            for col in range(3):
                normal[row][col] += d[row] * d[col]

        sample_count += 1

    if sample_count >= 2:
        scale = max(
            normal[0][0] + normal[1][1] + normal[2][2],
            1e-12,
        )
        ridge = scale * 1e-7

        row_x = _solve.solve_3x3_regularized(
            normal,
            rhs_x,
            ridge,
        )
        row_y = _solve.solve_3x3_regularized(
            normal,
            rhs_y,
            ridge,
        )

        if row_x is not None and row_y is not None:
            direction = Vector((
                row_x.dot(up),
                row_y.dot(up),
                0.0,
            ))

            if direction.length > 1e-10:
                direction.normalize()
                return direction

    # Robust fallback: old scalar field gradient.
    samples = []
    ranked = []

    for flat_vi in record["verts"]:
        if not (0 <= flat_vi < len(flat_to_source)):
            continue

        src_vi = int(flat_to_source[flat_vi])
        if not (0 <= src_vi < len(source_obj.data.vertices)):
            continue

        source_co = source_obj.data.vertices[src_vi].co
        flat_co = unfold_obj.data.vertices[flat_vi].co.copy()
        scalar = float(source_co.dot(up))

        samples.append((
            float(flat_co.x),
            float(flat_co.y),
            scalar,
        ))
        ranked.append((scalar, flat_co))

    direction = _solve.solve_2d_gradient(samples)

    if direction is None or direction.length <= 1e-10:
        if len(ranked) >= 2:
            low = min(ranked, key=lambda item: item[0])
            high = max(ranked, key=lambda item: item[0])
            direction = high[1] - low[1]
            direction.z = 0.0

    if direction is None or direction.length <= 1e-12:
        return Vector((0.0, 1.0, 0.0))

    direction.normalize()
    return direction


def anchor_point_flat_local(unfold_obj, anchor):
    vert_src, face_src = _mapping.read_indices(unfold_obj)
    if not vert_src or not face_src:
        return None

    try:
        source_face = int(anchor["face"])
        tri = [int(v) for v in anchor["tri"]]
        w = [float(v) for v in anchor["w"]]
    except Exception:
        return None

    flat_poly_index = None
    for i, source_index in enumerate(face_src):
        if int(source_index) == source_face:
            flat_poly_index = i
            break

    if flat_poly_index is None or flat_poly_index >= len(unfold_obj.data.polygons):
        return None

    poly = unfold_obj.data.polygons[flat_poly_index]
    src_to_flat = {}

    for flat_vi in poly.vertices:
        if flat_vi < len(vert_src):
            src_to_flat[int(vert_src[flat_vi])] = int(flat_vi)

    flat_vis = []
    for source_vi in tri:
        flat_vi = src_to_flat.get(source_vi)
        if flat_vi is None:
            return None
        flat_vis.append(flat_vi)

    a = unfold_obj.data.vertices[flat_vis[0]].co
    b = unfold_obj.data.vertices[flat_vis[1]].co
    c = unfold_obj.data.vertices[flat_vis[2]].co

    return a * w[0] + b * w[1] + c * w[2]


def connection_label_inside_position(
    context,
    mesh,
    face_indices,
    mid,
    inward,
    tangent,
    size_mm,
):
    """Return the closest seam-adjacent position whose text footprint is inside.

    The glyph footprint is approximated as a tangent-aligned rectangle.
    Candidate positions are progressively moved inward until all four corners
    are inside the island. This is intentionally conservative.
    """
    if inward.length <= 1.0e-12:
        return mid.copy()

    inward = inward.normalized()

    if tangent.length <= 1.0e-12:
        tangent = Vector((1.0, 0.0, 0.0))
    else:
        tangent = tangent.normalized()

    # Single-character connection IDs are approximately square-ish.
    # Add safety padding so anti-aliased glyph edges do not touch the boundary.
    half_h = _units.scene_mm_to_bu(
        context.scene,
        max(0.8, float(size_mm) * 0.58),
    )
    half_w = _units.scene_mm_to_bu(
        context.scene,
        max(0.8, float(size_mm) * 0.48),
    )

    # Start just inside the seam, then walk inward until all corners fit.
    start_offset = half_h + _units.scene_mm_to_bu(context.scene, 0.5)
    step = _units.scene_mm_to_bu(
        context.scene,
        max(0.5, float(size_mm) * 0.20),
    )

    side = Vector((-tangent.y, tangent.x, 0.0))
    if side.length <= 1.0e-12:
        side = Vector((1.0, 0.0, 0.0))
    else:
        side.normalize()

    # Align the normal axis to the known inward side.
    if side.dot(inward) < 0.0:
        side = -side

    for i in range(18):
        candidate = mid + inward * (start_offset + step * i)

        corners = (
            candidate + tangent * half_w + side * half_h,
            candidate + tangent * half_w - side * half_h,
            candidate - tangent * half_w + side * half_h,
            candidate - tangent * half_w - side * half_h,
        )

        if all(
            _geometry.point_in_island_2d(
                mesh,
                face_indices,
                corner,
            )
            for corner in corners
        ):
            return candidate

    # Fallback: use the centroid of all island vertices.
    verts = []
    seen = set()
    for face_index in face_indices:
        if not (0 <= int(face_index) < len(mesh.polygons)):
            continue
        for vi in mesh.polygons[int(face_index)].vertices:
            vi = int(vi)
            if vi in seen:
                continue
            seen.add(vi)
            verts.append(mesh.vertices[vi].co.copy())

    if verts:
        center = sum(
            verts,
            Vector((0.0, 0.0, 0.0)),
        ) / len(verts)
        return center

    return mid + inward * start_offset


def flat_notch_segments(
    context,
    source_obj,
    unfold_obj,
    item,
    matrix=None,
):
    try:
        source_edge_index = int(item.get("edge", -1))
        source_fraction = float(item.get("t", 0.5))
    except Exception:
        return []

    if not (0 <= source_edge_index < len(source_obj.data.edges)):
        return []

    if not bool(source_obj.data.edges[source_edge_index].use_seam):
        return []

    flat_edge_source = _mapping.flat_edge_to_source(unfold_obj)
    flat_vert_source = _mapping.flat_vertex_to_source(unfold_obj)

    if not flat_edge_source or not flat_vert_source:
        return []

    source_edge = source_obj.data.edges[source_edge_index]
    source_v0 = int(source_edge.vertices[0])
    source_v1 = int(source_edge.vertices[1])

    face_map = _mapping.flat_edge_faces(unfold_obj)
    mesh = unfold_obj.data
    # 呼び出し側がローカル空間を求める場合は matrix に恒等行列が渡る。
    mw = unfold_obj.matrix_world if matrix is None else matrix

    length = _units.scene_mm_to_bu(
        context.scene,
        float(getattr(context.scene, "tsunfold_notch_length_mm", 6.0))
    )

    result = []

    for edge in mesh.edges:
        if edge.index >= len(flat_edge_source):
            continue
        if int(flat_edge_source[edge.index]) != source_edge_index:
            continue

        faces = face_map.get(int(edge.index), [])
        if len(faces) != 1:
            continue

        fva = int(edge.vertices[0])
        fvb = int(edge.vertices[1])

        if fva >= len(flat_vert_source) or fvb >= len(flat_vert_source):
            continue

        sva = int(flat_vert_source[fva])
        svb = int(flat_vert_source[fvb])

        if sva == source_v0 and svb == source_v1:
            t = source_fraction
        elif sva == source_v1 and svb == source_v0:
            t = 1.0 - source_fraction
        else:
            # UV split vertex mapping can occasionally be ambiguous.
            t = source_fraction

        a = mesh.vertices[fva].co
        b = mesh.vertices[fvb].co
        p = a.lerp(b, t)

        tangent = b - a
        tangent.z = 0.0
        if tangent.length <= 1e-12:
            continue
        tangent.normalize()

        perp = Vector((-tangent.y, tangent.x, 0.0))
        poly = mesh.polygons[faces[0]]
        toward_inside = poly.center - p
        toward_inside.z = 0.0

        if toward_inside.dot(perp) < 0.0:
            perp.negate()

        q = p + perp * length

        result.append((mw @ p, mw @ q))

    return result


def transform_rows(rows, matrix, point_indices):
    """タプルの並びのうち、指定した位置の座標だけを変換して返す。

    角度・色・太さはローカルとワールドで変わらないのでそのまま通す。
    文字の角度は元々ローカルの接線から求めており matrix_world の影響を
    受けない実装なので、ここでも触らない。
    """
    result = []
    for row in rows:
        row = list(row)
        for index in point_indices:
            row[index] = matrix @ row[index]
        result.append(tuple(row))
    return result


def compute_text_items(
    context,
    source_obj,
    unfold_obj,
):
    records, _face_to_island, adjacency = island_metadata(
        context,
        source_obj,
        unfold_obj,
    )

    if not records:
        return []

    scene = context.scene
    size_mm = float(
        getattr(scene, "tsunfold_island_id_size_mm", 8.0)
    )
    color = tuple(
        float(v)
        for v in getattr(
            scene,
            "tsunfold_island_id_color",
            (0.0, 0.0, 0.0),
        )
    )
    # ローカル空間で計算する。ワールド変換は呼び出し側で掛ける。
    mw = _identity()
    result = []

    for record in records:
        center = record["center_local"].copy()
        direction = auto_arrow_direction_for_record(
            context,
            source_obj,
            unfold_obj,
            record,
        )

        # Font local +Y is its "up". Rotate it so +Y follows the auto arrow.
        id_angle = math.atan2(direction.y, direction.x) - (math.pi * 0.5)

        # Find a real, safe area inside this island instead of assuming the
        # average vertex center is usable. This handles concave, C-shaped,
        # ring-shaped and very elongated pattern pieces.
        label_pos = _placement.island_id_safe_position(
            context,
            unfold_obj.data,
            record,
            record["label"],
            id_angle,
            size_mm,
            direction,
        )

        result.append((
            record["label"],
            mw @ label_pos,
            size_mm,
            color,
            id_angle,
            False,
        ))

        for neighbor in adjacency.get(record["index"], []):
            other_index = int(neighbor["other"])
            if not (0 <= other_index < len(records)):
                continue

            tangent = neighbor.get(
                "tangent_local",
                Vector((1.0, 0.0, 0.0)),
            ).copy()

            if tangent.length <= 1e-12:
                tangent = Vector((1.0, 0.0, 0.0))
            tangent.normalize()

            edge_angle = readable_edge_angle(
                math.atan2(tangent.y, tangent.x)
            )

            result.append((
                records[other_index]["label"],
                mw @ neighbor["pos_local"],
                max(3.0, size_mm * 0.62),
                color,
                edge_angle,
                True,
            ))

    return result


def text_items(
    context,
    source_obj,
    unfold_obj,
):

    scene = context.scene
    key = (
        "auto_text",
        int(_state.epoch),
        source_obj.name if source_obj else "",
        unfold_obj.name if unfold_obj else "",
        str(getattr(scene, "tsunfold_island_id_style", "ALPHA")),
        round(float(getattr(scene, "tsunfold_island_id_size_mm", 8.0)), 4),
        tuple(
            round(float(v), 4)
            for v in getattr(
                scene,
                "tsunfold_island_id_color",
                (0.0, 0.0, 0.0),
            )
        ),
        str(getattr(scene, "tsunfold_arrow_up_axis", "Z")),
    )

    cached = _state.get(key)
    if cached is None:
        cached = _state.store(
            key,
            compute_text_items(
                context,
                source_obj,
                unfold_obj,
            ),
        )

    # キャッシュはローカル空間。位置だけワールドへ移す。
    return transform_rows(cached, unfold_obj.matrix_world, (1,))


def compute_arrow_segments(context, source_obj, unfold_obj):
    if str(getattr(context.scene, "tsunfold_arrow_mode", "AUTO")) != "AUTO":
        return []

    records, _face_to_island, _adjacency = island_metadata(
        context,
        source_obj,
        unfold_obj,
    )
    if not records:
        return []

    flat_to_source = _mapping.flat_vertex_to_source(unfold_obj)
    scene = context.scene
    # ローカル空間で計算する。ワールド変換は呼び出し側で掛ける。
    mw = _identity()

    requested_length = _units.scene_mm_to_bu(
        scene,
        float(getattr(scene, "tsunfold_auto_arrow_length_mm", 24.0)),
    )
    head = _units.scene_mm_to_bu(
        scene,
        float(getattr(scene, "tsunfold_arrow_head_mm", 8.0)),
    )
    thickness = float(
        getattr(scene, "tsunfold_arrow_thickness_mm", 0.8)
    )
    color = tuple(
        float(v)
        for v in getattr(
            scene,
            "tsunfold_arrow_color",
            (0.0, 0.0, 0.0),
        )
    )

    result = []

    for record in records:
        direction = auto_arrow_direction_for_record(
            context,
            source_obj,
            unfold_obj,
            record,
        )

        # Keep the selected world-axis direction, but place the arrow in an
        # actually usable part of the island. Concave/ring shapes can have an
        # empty geometric center, so endpoints alone are not enough.
        center, length, safe_head = _placement.safe_arrow_placement(
            context,
            unfold_obj.data,
            record,
            direction,
            requested_length,
            head,
        )

        start, end, head_a, head_b = _placement.arrow_geometry_local(
            center,
            direction,
            length,
            safe_head,
        )

        a = mw @ start
        b = mw @ end
        result.append((a, b, color, thickness))

        result.append((
            mw @ end,
            mw @ head_a,
            color,
            thickness,
        ))
        result.append((
            mw @ end,
            mw @ head_b,
            color,
            thickness,
        ))

    return result


def arrow_segments_local(context, source_obj, unfold_obj):
    """矢印のセグメントを型紙のローカル空間で返す（キャッシュ対象）。

    matrix_world をキーに含めないので、オブジェクトを動かしても
    キャッシュが効く。
    """
    scene = context.scene
    key = (
        "auto_arrows",
        int(_state.epoch),
        source_obj.name if source_obj else "",
        unfold_obj.name if unfold_obj else "",
        str(getattr(scene, "tsunfold_arrow_mode", "AUTO")),
        str(getattr(scene, "tsunfold_arrow_up_axis", "Z")),
        round(float(getattr(scene, "tsunfold_auto_arrow_length_mm", 24.0)), 4),
        round(float(getattr(scene, "tsunfold_arrow_head_mm", 8.0)), 4),
        round(float(getattr(scene, "tsunfold_arrow_thickness_mm", 0.8)), 4),
        tuple(
            round(float(v), 4)
            for v in getattr(
                scene,
                "tsunfold_arrow_color",
                (0.0, 0.0, 0.0),
            )
        ),
    )

    cached = _state.get(key)
    if cached is not None:
        return cached

    return _state.store(
        key,
        compute_arrow_segments(context, source_obj, unfold_obj),
    )


def compute_colored_segments(context, source_obj, unfold_obj):
    segments = []
    size = _units.scene_mm_to_bu(context.scene, 8.0)
    # ローカル空間で計算する。ワールド変換は呼び出し側で掛ける。
    mw = _identity()

    for item in _storage.load(source_obj):
        kind = item.get("type")
        color = _storage.scene_item_color(item, context.scene)

        if kind == "notch_edge":
            notch_segments = flat_notch_segments(
                context,
                source_obj,
                unfold_obj,
                item,
                matrix=_identity(),
            )

            notch_thickness = float(
                getattr(
                    context.scene,
                    "tsunfold_notch_thickness_mm",
                    0.6,
                )
            )
            for a, b in notch_segments:
                segments.append((
                    a,
                    b,
                    color,
                    notch_thickness,
                ))

        elif kind == "arrow":
            if str(
                getattr(context.scene, "tsunfold_arrow_mode", "AUTO")
            ) == "NONE":
                continue

            pa = anchor_point_flat_local(
                unfold_obj, item.get("a", {})
            )
            pb = anchor_point_flat_local(
                unfold_obj, item.get("b", {})
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

            direction = Vector((b.x - a.x, b.y - a.y, 0.0))
            if direction.length > 1e-12:
                direction.normalize()
                side = Vector((-direction.y, direction.x, 0.0))
                back = b - direction * head
                segments.append(
                    (b, back + side * head * 0.45, color, thickness)
                )
                segments.append(
                    (b, back - side * head * 0.45, color, thickness)
                )

    # 矢印はここで混ぜない。
    # 混ぜてしまうと、この関数の結果をキャッシュするキーに矢印の設定
    # （長さ・ヘッド・太さ・色）も入れなければならなくなり、
    # 追加のたびにキーへ足し忘れて「変えても反映されない」が起きる。
    # 矢印は矢印自身のキャッシュを持っているので、呼び出し側で足す。

    return segments


def colored_segments(context, source_obj, unfold_obj):

    annotations_raw = source_obj.get(
        _storage.ANNOTATION_PROP,
        "[]",
    )
    scene = context.scene

    key = (
        "flat_segments",
        int(_state.epoch),
        source_obj.name if source_obj else "",
        unfold_obj.name if unfold_obj else "",
        annotations_raw,
        str(getattr(scene, "tsunfold_arrow_mode", "AUTO")),
        round(float(getattr(scene, "tsunfold_notch_length_mm", 6.0)), 4),
        # 合印の太さは計算結果のタプルに焼き込まれる。
        # キーに入れないと、値を変えても古い太さのまま描かれ続ける。
        round(float(getattr(scene, "tsunfold_notch_thickness_mm", 0.6)), 4),
        # 色は保存値ではなく設定から引くので、設定をキーに入れる。
        # 入れ忘れると、色を変えても古い結果が使われる。
        # 種類ごとに設定があるため、描く可能性のある全種類を入れる。
        tuple(
            tuple(
                round(float(v), 4)
                for v in getattr(scene, prop, (0.0, 0.0, 0.0))
            )
            for prop in sorted(_storage.COLOR_PROP.values())
        ),
    )

    cached = _state.get(key)
    if cached is None:
        cached = _state.store(
            key,
            compute_colored_segments(
                context,
                source_obj,
                unfold_obj,
            ),
        )

    # 矢印はここで足す。矢印は矢印自身のキャッシュを持っており、
    # そちらは矢印の設定をキーに含んでいる。
    local = list(cached)
    local.extend(
        arrow_segments_local(context, source_obj, unfold_obj)
    )

    # キャッシュはローカル空間。両端の座標だけワールドへ移す。
    return transform_rows(local, unfold_obj.matrix_world, (0, 1))
