"""型紙IDと上方向矢印を、島の内側の収まる場所へ置く。

単純に島の中心へ置くと、リング状（中央が空洞）やコの字型の島では
文字や矢印が型紙の外へ出てしまう。候補を格子状に散らして、
実際に面がある場所で、かつ輪郭から余裕がある位置を選ぶ。

■ 判定の内容

型紙ID … 文字の外接矩形の中心・四隅・辺の中点の9点が全て島の内側にあること
矢印   … 軸と矢尻を等間隔にサンプリングした点が全て島の内側にあること
         途中が空洞を通る配置を弾くため、両端だけでなく中間も見る

どちらも輪郭からの余裕（クリアランス）を score にして、
最も余裕のある候補を選ぶ。

■ 速度

内外判定は候補数×サンプル点数だけ呼ばれるので、数万回になる。
座標を素のタプルへ展開した core.geometry の関数を使い、
外接矩形で先に弾く。ここを素朴に書くと設定変更のたびに数十ミリ秒かかる。
"""

import math

from mathutils import Vector

from ..core import geometry as _geometry
from ..core import units as _units


def alpha_label(index):
    index = int(index)
    value = ""
    while True:
        index, rem = divmod(index, 26)
        value = chr(ord('A') + rem) + value
        if index == 0:
            break
        index -= 1
    return value


def arrow_geometry_local(center, direction, length, head):
    """Return arrow shaft/head points in flat local coordinates."""
    d = direction.copy()
    d.z = 0.0
    if d.length <= 1.0e-12:
        d = Vector((0.0, 1.0, 0.0))
    else:
        d.normalize()

    side = Vector((-d.y, d.x, 0.0))
    if side.length <= 1.0e-12:
        side = Vector((1.0, 0.0, 0.0))
    else:
        side.normalize()

    start = center - d * length * 0.5
    end = center + d * length * 0.5
    back = end - d * min(head, max(0.0, length * 0.45))

    head_a = back + side * min(head * 0.45, max(0.0, length * 0.22))
    head_b = back - side * min(head * 0.45, max(0.0, length * 0.22))

    return start, end, head_a, head_b


def arrow_fits_island(
    mesh,
    face_indices,
    center,
    direction,
    length,
    head,
    safety_margin,
    boundary=None,
):
    """Check the whole auto arrow against the actual flattened island.

    Sampling along the shaft and both arrowhead strokes prevents a ring/C-shaped
    island from accepting an arrow whose endpoints are inside but middle passes
    through an empty hole.
    """
    start, end, head_a, head_b = arrow_geometry_local(
        center,
        direction,
        length,
        head,
    )

    sample_points = [center, start, end, head_a, head_b]

    # Sample all three visible strokes. This catches holes / concave gaps.
    for a, b in ((start, end), (end, head_a), (end, head_b)):
        steps = 5
        for i in range(1, steps):
            t = i / steps
            sample_points.append(a.lerp(b, t))

    # polys はここで一度だけ取得して使い回す
    polys = _geometry.flat_polygons_2d(mesh)
    points_xy = [(float(p.x), float(p.y)) for p in sample_points]

    for px, py in points_xy:
        if not _geometry.point_in_polys_2d(polys, face_indices, px, py):
            return False

    # Require a little boundary clearance so the line is not visually clipped.
    # Boundary may be precomputed by the caller; rebuilding it for every
    # candidate is extremely expensive on dense pattern pieces.
    if boundary is None:
        boundary = _geometry.island_boundary_segments(mesh, face_indices)
        # 候補ごとに Vector の属性を読み直すと高くつくので、
        # 先に素のfloatへ展開しておく。
        boundary_flat = _geometry.boundary_as_floats(boundary)

    if boundary and safety_margin > 0.0:
        if _geometry.any_point_too_close(
            points_xy,
            _geometry.boundary_as_floats(boundary),
            safety_margin,
        ):
            return False

    return True


def id_footprint_inside(
    mesh,
    face_indices,
    center,
    angle,
    half_w,
    half_h,
):
    """Conservative rectangle test for a centered island-ID glyph block."""
    x_axis = Vector((
        math.cos(float(angle)),
        math.sin(float(angle)),
        0.0,
    ))
    y_axis = Vector((
        -math.sin(float(angle)),
        math.cos(float(angle)),
        0.0,
    ))

    # Center + all four corners + edge midpoints.
    # The extra midpoint checks are useful on concave / ring-like islands.
    offsets = (
        Vector((0.0, 0.0, 0.0)),
        x_axis * half_w + y_axis * half_h,
        x_axis * half_w - y_axis * half_h,
        -x_axis * half_w + y_axis * half_h,
        -x_axis * half_w - y_axis * half_h,
        x_axis * half_w,
        -x_axis * half_w,
        y_axis * half_h,
        -y_axis * half_h,
    )

    # polys はここで一度だけ取得する。
    # 9点それぞれで引き直すと、キャッシュのキー組み立てが積み重なる。
    polys = _geometry.flat_polygons_2d(mesh)

    for offset in offsets:
        point = center + offset
        if not _geometry.point_in_polys_2d(
            polys,
            face_indices,
            float(point.x),
            float(point.y),
        ):
            return False

    return True


def island_id_safe_position(
    context,
    mesh,
    record,
    label,
    angle,
    size_mm,
    arrow_direction,
):
    """Find a human-friendly, safely contained island-ID position.

    Search actual island faces rather than trusting the average of vertices.
    This keeps IDs out of holes and concave empty regions, while preferring
    positions with generous boundary clearance. When choices are similar,
    prefer a position a little away from the central auto-arrow line.
    """
    face_indices = list(record.get("face_indices", []))
    min_x, min_y, max_x, max_y = record["bbox"]

    width = max(0.0, float(max_x - min_x))
    height = max(0.0, float(max_y - min_y))
    if width <= 1.0e-12 or height <= 1.0e-12:
        return record["center_local"].copy()

    label_text = str(label)
    char_count = max(1, len(label_text))

    # Approximate BLF/vector-text footprint conservatively.
    half_h = _units.scene_mm_to_bu(
        context.scene,
        max(1.0, float(size_mm) * 0.62),
    )
    half_w = _units.scene_mm_to_bu(
        context.scene,
        max(1.0, float(size_mm) * (0.42 * char_count + 0.10)),
    )

    boundary = _geometry.island_boundary_segments(
        mesh,
        face_indices,
    )
    # 候補ごとに Vector の属性を読み直すと高くつくので、
    # 先に素のfloatへ展開しておく。
    boundary_flat = _geometry.boundary_as_floats(boundary)

    center = record["center_local"].copy()

    direction = arrow_direction.copy()
    direction.z = 0.0
    if direction.length <= 1.0e-12:
        direction = Vector((0.0, 1.0, 0.0))
    else:
        direction.normalize()

    # Perpendicular distance from a candidate to the infinite arrow centerline.
    side = Vector((-direction.y, direction.x, 0.0))
    if side.length <= 1.0e-12:
        side = Vector((1.0, 0.0, 0.0))
    else:
        side.normalize()

    candidates = []

    # Face centers are valuable for thin / highly concave islands where a
    # regular grid can miss the usable strip.
    for fi in face_indices:
        if not (0 <= int(fi) < len(mesh.polygons)):
            continue
        poly = mesh.polygons[int(fi)]
        if len(poly.vertices) < 3:
            continue
        p = sum(
            (
                mesh.vertices[int(vi)].co.copy()
                for vi in poly.vertices
            ),
            Vector((0.0, 0.0, 0.0)),
        ) / len(poly.vertices)
        p.z = 0.0
        candidates.append(p)

    # Add the old sideways-shift idea as a preferred seed.
    side_shift = min(
        _units.scene_mm_to_bu(context.scene, 12.0),
        max(0.0, min(width, height) * 0.18),
    )
    candidates.append(center + side * side_shift)
    candidates.append(center - side * side_shift)

    # Uniform interior search. 17x17 is dense enough for labels but remains
    # cheap because the final result is cached with the marking layout.
    grid_n = 17
    for iy in range(grid_n):
        fy = (iy + 0.5) / grid_n
        y = min_y + height * fy
        for ix in range(grid_n):
            fx = (ix + 0.5) / grid_n
            x = min_x + width * fx
            candidates.append(Vector((x, y, 0.0)))

    valid = []

    for candidate in candidates:
        if not _geometry.point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        if not id_footprint_inside(
            mesh,
            face_indices,
            candidate,
            angle,
            half_w,
            half_h,
        ):
            continue

        clearance = _geometry.min_clearance(
            float(candidate.x),
            float(candidate.y),
            boundary_flat,
        )

        # Prefer clear open area first. For similarly safe candidates, give a
        # smaller bonus to lateral separation from the center arrow line.
        lateral = abs((candidate - center).dot(side))
        lateral_cap = _units.scene_mm_to_bu(
            context.scene,
            max(4.0, float(size_mm) * 1.8),
        )
        lateral_bonus = min(lateral, lateral_cap) * 0.28

        # Tiny preference toward the broad central region prevents a distant
        # appendage from winning only because it happens to be marginally wider.
        diag = max(math.hypot(width, height), 1.0e-12)
        central_penalty = (candidate - center).length / diag * clearance * 0.06

        score = clearance + lateral_bonus - central_penalty
        valid.append((score, clearance, candidate.copy()))

    if valid:
        valid.sort(
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        return valid[0][2]

    # No candidate can contain the full requested text footprint.
    # Still avoid empty holes: choose the actual in-island point with the
    # greatest boundary clearance, then let the existing text renderer draw it.
    fallback = []

    for candidate in candidates:
        if not _geometry.point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        clearance = _geometry.min_clearance(
            float(candidate.x),
            float(candidate.y),
            boundary_flat,
        )

        fallback.append((clearance, candidate.copy()))

    if fallback:
        fallback.sort(key=lambda item: item[0], reverse=True)
        return fallback[0][1]

    # Absolute last resort. Normally unreachable unless the island data is bad.
    return center


def safe_arrow_placement(
    context,
    mesh,
    record,
    direction,
    requested_length,
    requested_head,
):
    """Find an in-island arrow center; shorten only when no full-length fit exists."""
    face_indices = list(record.get("face_indices", []))
    min_x, min_y, max_x, max_y = record["bbox"]

    width = max(0.0, float(max_x - min_x))
    height = max(0.0, float(max_y - min_y))
    if width <= 1.0e-12 or height <= 1.0e-12:
        return (
            record["center_local"].copy(),
            requested_length,
            requested_head,
        )

    d = direction.copy()
    d.z = 0.0
    if d.length <= 1.0e-12:
        d = Vector((0.0, 1.0, 0.0))
    else:
        d.normalize()

    side = Vector((-d.y, d.x, 0.0))
    if side.length <= 1.0e-12:
        side = Vector((1.0, 0.0, 0.0))
    else:
        side.normalize()

    old_center = record["center_local"].copy()
    boundary = _geometry.island_boundary_segments(mesh, face_indices)
    # 候補ごとに Vector の属性を読み直すと高くつくので、
    # 先に素のfloatへ展開しておく。
    boundary_flat = _geometry.boundary_as_floats(boundary)

    # Visual margin around the line. Keep it small so narrow strips still work.
    safety_margin = _units.scene_mm_to_bu(
        context.scene,
        max(
            0.35,
            float(getattr(
                context.scene,
                "tsunfold_arrow_thickness_mm",
                0.8,
            )) * 0.75,
        ),
    )

    candidates = []

    # Face centers are useful for ring segments and narrow arms, but sampling
    # every face on a dense mesh is unnecessary. Cap to about 24 samples.
    face_step = max(1, len(face_indices) // 24)
    for offset, fi in enumerate(face_indices):
        if offset % face_step != 0:
            continue
        if not (0 <= int(fi) < len(mesh.polygons)):
            continue
        poly = mesh.polygons[int(fi)]
        if len(poly.vertices) < 3:
            continue
        p = sum(
            (
                mesh.vertices[int(vi)].co.copy()
                for vi in poly.vertices
            ),
            Vector((0.0, 0.0, 0.0)),
        ) / len(poly.vertices)
        p.z = 0.0
        candidates.append(p)

    # Candidate seeds around the old center.
    side_shift = min(
        _units.scene_mm_to_bu(context.scene, 14.0),
        max(0.0, min(width, height) * 0.22),
    )
    candidates.extend((
        old_center,
        old_center + side * side_shift,
        old_center - side * side_shift,
    ))

    # Lightweight coarse search only.
    # Arrow placement runs inside the marking cache build, so a dense grid
    # becomes painfully expensive on high-poly / many-island patterns.
    grid_n = 7
    for iy in range(grid_n):
        fy = (iy + 0.5) / grid_n
        y = min_y + height * fy
        for ix in range(grid_n):
            fx = (ix + 0.5) / grid_n
            x = min_x + width * fx
            candidates.append(Vector((x, y, 0.0)))

    # Remove obvious duplicates while preserving order.
    unique = []
    seen = set()
    for p in candidates:
        key = (round(float(p.x), 7), round(float(p.y), 7))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    # First priority: keep the user's requested arrow length.
    valid = []
    for candidate in unique:
        if not _geometry.point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        if not arrow_fits_island(
            mesh,
            face_indices,
            candidate,
            d,
            requested_length,
            requested_head,
            safety_margin,
            boundary,
        ):
            continue

        clearance = _geometry.min_clearance(
            float(candidate.x),
            float(candidate.y),
            boundary_flat,
        )

        # Prefer roomy places, then places close to the old visual center.
        diag = max(math.hypot(width, height), 1.0e-12)
        center_penalty = (candidate - old_center).length / diag
        score = clearance - center_penalty * safety_margin * 0.6
        valid.append((score, clearance, candidate.copy()))

    if valid:
        valid.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return valid[0][2], requested_length, requested_head

    # If the requested arrow physically cannot fit anywhere, shorten it
    # progressively rather than letting it protrude through a hole/boundary.
    min_length = _units.scene_mm_to_bu(context.scene, 4.0)
    best = None

    for ratio in (0.80, 0.60, 0.45, 0.30):
        length = max(min_length, requested_length * ratio)
        head = min(requested_head, max(length * 0.34, min_length * 0.45))

        for candidate in unique:
            if not _geometry.point_in_island_2d(
                mesh,
                face_indices,
                candidate,
            ):
                continue

            if not arrow_fits_island(
                mesh,
                face_indices,
                candidate,
                d,
                length,
                head,
                max(0.0, safety_margin * 0.6),
                boundary,
            ):
                continue

            clearance = _geometry.min_clearance(
                float(candidate.x),
                float(candidate.y),
                boundary_flat,
            )

            # Favor the longest fitting arrow first; clearance breaks ties.
            score = length * 1000.0 + clearance
            if best is None or score > best[0]:
                best = (score, candidate.copy(), length, head)

        if best is not None:
            break

    if best is not None:
        return best[1], best[2], best[3]

    # Absolute fallback: actual in-island point with most boundary clearance.
    fallback = []
    for candidate in unique:
        if not _geometry.point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        clearance = _geometry.min_clearance(
            float(candidate.x),
            float(candidate.y),
            boundary_flat,
        )

        fallback.append((clearance, candidate.copy()))

    if fallback:
        fallback.sort(key=lambda item: item[0], reverse=True)
        return fallback[0][1], min_length, min(requested_head, min_length * 0.35)

    return old_center, min_length, min(requested_head, min_length * 0.35)
