"""3Dビューへの重ね描き。

Blender の描画ハンドラから毎フレーム呼ばれる。描くだけで、
何を描くかの計算は marking 側が済ませている。

■ 毎フレーム走ることの意味

ここに重い処理を置くと、ビュー操作そのものが重くなる。
計算結果はキャッシュから読み、ここでは GPU へ渡すだけにする。

■ 手動レイアウト中は描かない

面を動かしている最中に注記を描き直すと、位置が追従せずちらつく。
レイアウト編集中は早期に戻る。

■ ここから unfold を呼ばない

描くために必要な情報は core と marking から取る。unfold（オペレータ）
を呼び戻すと循環importになり、それを避けるための遅延importが
「どこから何を見ているか」を隠してしまう。

■ 用紙ガイドとシームの線は印刷されない

画面で確認するための目印で、PNG書き出しには含めない。
シームの赤線は合印とは別物。
"""

import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy_extras import view3d_utils

from .. import debug as _debug
from ..core import geometry as _geometry
from ..core import mapping as _mapping
from ..core import objects as _objects
from ..core import paper as _paper
from ..core import session as _session
from ..core import units as _units
from ..marking import compute as _compute
from ..marking import interact as _interact
from ..marking import source as _source
from ..marking import storage as _storage

# 描画ハンドラの登録control。unregister で確実に外すために持つ。
_handles = []


def _scene_flag(scene, key, default=False):
    return bool(scene.get(key, default))


def manual_layout_active(scene):
    """手動レイアウト中か。中は注記を描かない。"""
    return _scene_flag(scene, _session.MANUAL_LAYOUT_ACTIVE)


def _plane_normal(obj):
    """型紙が乗っている面の法線。太さを広げる向きを決めるのに使う。"""
    if obj is None:
        return Vector((0.0, 0.0, 1.0))
    normal = obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))
    if normal.length <= 1e-12:
        return Vector((0.0, 0.0, 1.0))
    return normal.normalized()


def draw_thick_segments(rows, unfold_obj, scene):
    """太さのある線を、面の中で直角に広げた四角形として描く。

    gpu.state.line_width_set は使わない。OpenGL の太線は多くの
    ドライバが軸方向へ広げるため、斜めの線が平行四辺形になる。
    線の向きに対して直角へ広げれば、どの角度でも同じ太さになる。

    広げる幅はミリをそのまま Blender Unit へ直したもの。つまり
    画面上の太さが実寸に対応し、ズームしても実寸は変わらない。
    以前はミリを適当な係数でピクセルに換算していたので、
    ズームすると太さの意味が変わっていた。

    細い線はズームを引くと1ピクセル未満になって消えるので、
    中心線を1ピクセルで重ねて必ず見えるようにする。

    rows は (始点, 終点, 色, 太さmm) の並び。
    """
    if not rows:
        return

    normal = _plane_normal(unfold_obj)

    tris = []
    tri_colors = []
    lines = []
    line_colors = []

    for a, b, color, width_mm in rows:
        rgba = (float(color[0]), float(color[1]), float(color[2]), 1.0)

        lines.extend((a, b))
        line_colors.extend((rgba, rgba))

        direction = Vector(b) - Vector(a)
        if direction.length <= 1e-12:
            continue

        side = normal.cross(direction.normalized())
        if side.length <= 1e-12:
            continue

        half = _units.scene_mm_to_bu(scene, float(width_mm)) * 0.5
        if half <= 0.0:
            continue

        offset = side.normalized() * half
        p0 = Vector(a) + offset
        p1 = Vector(a) - offset
        p2 = Vector(b) - offset
        p3 = Vector(b) + offset

        tris.extend((p0, p1, p2, p0, p2, p3))
        tri_colors.extend((rgba,) * 6)

    smooth = gpu.shader.from_builtin('SMOOTH_COLOR')

    if tris:
        batch = batch_for_shader(
            smooth, 'TRIS', {"pos": tris, "color": tri_colors}
        )
        smooth.bind()
        batch.draw(smooth)

    if lines:
        gpu.state.line_width_set(1.0)
        batch = batch_for_shader(
            smooth, 'LINES', {"pos": lines, "color": line_colors}
        )
        smooth.bind()
        batch.draw(smooth)


def preview_outline_segments(context):
    """Return the outline matching the currently displayed finish mode."""
    mode = context.scene.get(_session.DISPLAY_MODE, "POLY")

    if mode == "SMOOTH":
        # Prefer active smooth object.
        obj = context.active_object
        if (
            obj
            and obj.type == 'CURVE'
            and bool(obj.get("tsunfold_smooth_generated", False))
            and not obj.hide_viewport
        ):
            return _compute.smooth_curve_segments_world_xy(obj)

        # Fallback to any visible generated smooth curve.
        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("tsunfold_smooth_generated", False))
                and not candidate.hide_viewport
            ):
                return _compute.smooth_curve_segments_world_xy(candidate)

    # POLY or fallback.
    obj = _objects.active_unfold(context)
    if obj is not None and not obj.hide_viewport:
        return _objects.boundary_segments_world_xy(obj)

    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("tsunfold_generated", False))
            and not candidate.hide_viewport
        ):
            return _objects.boundary_segments_world_xy(candidate)

    return []


def draw_paper_guide():
    context = bpy.context
    if context is None or context.scene is None:
        return

    scene = context.scene
    show_paper = getattr(scene, "tsunfold_show_paper", True)
    show_preview = getattr(scene, "tsunfold_preview", False)

    if not show_paper and not show_preview:
        return

    try:
        paper_w_mm, paper_h_mm = _paper.scene_dimensions_mm(scene)
        w = _units.scene_mm_to_bu(scene, paper_w_mm)
        h = _units.scene_mm_to_bu(scene, paper_h_mm)

        # Slightly above XY plane so the overlay remains visible.
        z = 0.00015

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')

        if show_preview:
            # White paper fill.
            fill_verts = [
                (0.0, 0.0, z),
                (w, 0.0, z),
                (w, h, z),
                (0.0, h, z),
            ]
            fill_indices = [(0, 1, 2), (0, 2, 3)]
            fill_batch = batch_for_shader(
                shader, 'TRIS',
                {"pos": fill_verts},
                indices=fill_indices
            )

            gpu.state.blend_set('ALPHA')
            shader.bind()
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.96))
            fill_batch.draw(shader)

            # Paper border in dark gray for print-preview mode.
            border = [
                (0.0, 0.0, z + 0.00002),
                (w, 0.0, z + 0.00002),
                (w, h, z + 0.00002),
                (0.0, h, z + 0.00002),
                (0.0, 0.0, z + 0.00002),
            ]
            border_batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": border})
            gpu.state.line_width_set(2.0)
            shader.bind()
            shader.uniform_float("color", (0.2, 0.2, 0.2, 1.0))
            border_batch.draw(shader)

            # Draw the outline that matches the currently displayed finish mode.
            segs = preview_outline_segments(context)
            line_verts = []

            for (ax, ay), (bx, by) in segs:
                line_verts.append((ax, ay, z + 0.00004))
                line_verts.append((bx, by, z + 0.00004))

            if line_verts:
                line_batch = batch_for_shader(shader, 'LINES', {"pos": line_verts})
                gpu.state.line_width_set(2.0)
                shader.bind()
                shader.uniform_float("color", (0.0, 0.0, 0.0, 1.0))
                line_batch.draw(shader)

            source = _objects.source_from_context(context)
            unfold = _objects.unfold_for_source(source) if source else None

            if source is not None and unfold is not None:
                draw_thick_segments(
                    _compute.colored_segments(context, source, unfold),
                    unfold,
                    context.scene,
                )

            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')

        elif show_paper:
            # Normal layout guide: blue outline only.
            verts = [
                (0.0, 0.0, z),
                (w, 0.0, z),
                (w, h, z),
                (0.0, h, z),
                (0.0, 0.0, z),
            ]
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": verts})

            gpu.state.blend_set('ALPHA')
            gpu.state.line_width_set(2.0)
            shader.bind()
            shader.uniform_float("color", (0.12, 0.55, 1.0, 0.9))
            batch.draw(shader)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')

    except Exception:
        # Viewport preview must never break the add-on.
        pass


def flat_text_items(source_obj, unfold_obj, scene=None):
    result = []

    for item in _storage.load(source_obj):
        if item.get("type") not in {"number", "text"}:
            continue

        p = _compute.anchor_point_flat_local(
            unfold_obj, item.get("anchor", {})
        )
        if p is None:
            continue

        text = (
            str(item.get("value", "?"))
            if item.get("type") == "number"
            else str(item.get("text", ""))
        )
        size_mm = float(item.get("size_mm", 8.0))
        result.append((
            text,
            unfold_obj.matrix_world @ p,
            size_mm,
            _storage.scene_item_color(item, scene),
        ))

    return result


def flat_memo_text_items(unfold_obj):
    result = []
    if unfold_obj is None:
        return result

    mw = unfold_obj.matrix_world

    for item in _interact.get_flat_memos(unfold_obj):
        pos = item.get("pos", [0.0, 0.0, 0.0])
        try:
            local = Vector((
                float(pos[0]),
                float(pos[1]),
                float(pos[2]) if len(pos) > 2 else 0.0,
            ))
        except Exception:
            continue

        result.append((
            str(item.get("text", "")),
            mw @ local,
            float(item.get("size_mm", 6.0)),
            (0.0, 0.0, 0.0),
            float(item.get("angle", 0.0)),
        ))

    return result


def source_text_items(source_obj, scene=None):
    result = []
    mw = source_obj.matrix_world

    for item in _storage.load(source_obj):
        if item.get("type") not in {"number", "text"}:
            continue

        p = _source.anchor_point_local(
            source_obj, item.get("anchor", {})
        )
        if p is None:
            continue

        text = (
            str(item.get("value", "?"))
            if item.get("type") == "number"
            else str(item.get("text", ""))
        )
        size_mm = float(item.get("size_mm", 8.0))
        result.append((
            text,
            mw @ p,
            size_mm,
            _storage.scene_item_color(item, scene),
        ))

    context = bpy.context
    unfold_obj = _objects.unfold_for_source(source_obj)
    if (
        context is not None
        and unfold_obj is not None
        and bool(
            getattr(
                context.scene,
                "tsunfold_auto_island_ids",
                True,
            )
        )
    ):
        result.extend(
            auto_source_id_text_items(
                context,
                source_obj,
                unfold_obj,
            )
        )

    return result


def auto_source_id_text_items(context, source_obj, unfold_obj):
    records, _face_to_island, _adjacency = _compute.island_metadata(
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
    mw = source_obj.matrix_world
    result = []

    for record in records:
        centers = []
        for face_index in record["source_faces"]:
            if 0 <= face_index < len(source_obj.data.polygons):
                centers.append(
                    source_obj.data.polygons[face_index].center.copy()
                )

        if not centers:
            continue

        center = sum(
            centers,
            Vector((0.0, 0.0, 0.0)),
        ) / len(centers)

        normal = Vector((0.0, 0.0, 0.0))
        normal_count = 0

        for face_index in record["source_faces"]:
            if 0 <= face_index < len(source_obj.data.polygons):
                normal += source_obj.data.polygons[face_index].normal
                normal_count += 1

        if normal_count and normal.length > 1e-12:
            normal.normalize()
            center = center + normal * _units.scene_mm_to_bu(scene, 0.6)

        result.append((
            record["label"],
            mw @ center,
            size_mm,
            color,
        ))

    return result


def arrow_hud_text(scene):
    mode = str(
        getattr(
            scene,
            "tsunfold_arrow_mode",
            "AUTO",
        )
    )

    if mode == "NONE":
        return "矢印：なし"

    if mode == "CUSTOM":
        return "矢印：カスタム配置"

    axis = _compute.sanitize_arrow_axis(scene)

    if axis == "X":
        return "↑ 矢印基準：元モデル X+"
    if axis == "Y":
        return "↑ 矢印基準：元モデル Y+"

    return "↑ 矢印基準：元モデル Z+（正面投影が0なら点表示）"


def draw_arrow_hud(context, font_id=0):
    if manual_layout_active(context.scene):
        return

    """Viewport-only legend. Never becomes geometry or PNG content."""
    try:
        region = context.region
        if region is None:
            return

        text = arrow_hud_text(context.scene)

        blf.size(font_id, 15)
        blf.position(
            font_id,
            22.0,
            float(region.height) - 38.0,
            0.0,
        )
        blf.color(
            font_id,
            1.0,
            1.0,
            1.0,
            0.95,
        )
        blf.draw(font_id, text)

        if str(
            getattr(
                context.scene,
                "tsunfold_arrow_mode",
                "AUTO",
            )
        ) == "AUTO":
            blf.size(font_id, 11)
            blf.position(
                font_id,
                22.0,
                float(region.height) - 57.0,
                0.0,
            )
            blf.color(
                font_id,
                0.82,
                0.82,
                0.82,
                0.9,
            )
            blf.draw(
                font_id,
                "元モデルの上方向を各型紙へ投影",
            )
    except Exception:
        _debug.swallowed("overlay._pattern_draw_arrow_hud")


def draw_direction_arrow_overlay(context):
    if manual_layout_active(context.scene):
        return

    """Large light-blue viewport-only arrow for the chosen Blender world axis."""
    scene = context.scene

    if not bool(
        getattr(scene, "tsunfold_show_direction_arrow", True)
    ):
        return

    if str(
        getattr(scene, "tsunfold_arrow_mode", "AUTO")
    ) == "NONE":
        return

    source = _objects.source_from_context(context)

    if source is None:
        unfold = _objects.resolve_unfold_for_layout(context)
        if unfold is not None:
            source = bpy.data.objects.get(
                unfold.get("tsunfold_source", "")
            )

    if source is None:
        return

    region = context.region
    rv3d = getattr(context.space_data, "region_3d", None)

    if region is None or rv3d is None:
        return

    # Match Blender's navigation gizmo exactly:
    # X/Y/Z here are WORLD axes, independent of source object rotation.
    world_up = _compute.auto_up_vector(scene)

    if world_up.length <= 1e-12:
        return
    world_up.normalize()

    try:
        center_world = source.matrix_world.translation.copy()

        radius = max(
            (
                float(source.dimensions.x)
                + float(source.dimensions.y)
                + float(source.dimensions.z)
            ) / 6.0,
            0.1,
        )

        p0 = view3d_utils.location_3d_to_region_2d(
            region,
            rv3d,
            center_world,
        )
        p1 = view3d_utils.location_3d_to_region_2d(
            region,
            rv3d,
            center_world + world_up * radius,
        )

        if p0 is None or p1 is None:
            return

        screen_dir = Vector((
            float(p1.x - p0.x),
            float(p1.y - p0.y),
        ))

        # If the source up axis is almost parallel to the view direction,
        # its screen projection collapses. That is physically correct:
        # from this view it should look like a point, not an arbitrary arrow.
        camera_facing = screen_dir.length <= 2.0

        if not camera_facing:
            screen_dir.normalize()

        # Put the guide beside the projected original object.
        # Prefer the side with more room so it does not sit directly on top
        # of the source mesh.
        source_screen = p0

        gap = 105.0
        if float(source_screen.x) > float(region.width) * 0.42:
            base_x = float(source_screen.x) - gap
        else:
            base_x = float(source_screen.x) + gap

        base_y = float(source_screen.y)

        base = Vector((
            max(45.0, min(float(region.width) - 45.0, base_x)),
            max(65.0, min(float(region.height) - 65.0, base_y)),
        ))

        length = max(
            95.0,
            min(
                155.0,
                float(region.height) * 0.20,
            ),
        )

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        shader.uniform_float(
            "color",
            (0.34, 0.72, 1.0, 0.95),
        )

        gpu.state.blend_set('ALPHA')

        if camera_facing:
            # Draw a small cross/dot marker at the center. This represents
            # the direction pointing toward/away from the camera.
            dot_r = 7.0
            gpu.state.line_width_set(3.0)

            batch = batch_for_shader(
                shader,
                'LINES',
                {
                    "pos": [
                        (base.x - dot_r, base.y),
                        (base.x + dot_r, base.y),
                        (base.x, base.y - dot_r),
                        (base.x, base.y + dot_r),
                    ]
                },
            )
            batch.draw(shader)

        else:
            tip = base + screen_dir * length
            side = Vector((-screen_dir.y, screen_dir.x))

            head_len = 28.0
            head_half = 13.0
            back = tip - screen_dir * head_len
            head_a = back + side * head_half
            head_b = back - side * head_half

            gpu.state.line_width_set(3.0)

            batch = batch_for_shader(
                shader,
                'LINES',
                {
                    "pos": [
                        (base.x, base.y),
                        (tip.x, tip.y),
                        (tip.x, tip.y),
                        (head_a.x, head_a.y),
                        (tip.x, tip.y),
                        (head_b.x, head_b.y),
                    ]
                },
            )
            batch.draw(shader)

    except Exception:
        _debug.swallowed("overlay._pattern_draw_direction_arrow_overlay")
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')
        except Exception:
            _debug.swallowed("overlay._pattern_draw_direction_arrow_overlay")


def draw_marks_3d():
    context = bpy.context
    if context is None:
        return
    if manual_layout_active(context.scene):
        return
    if context.area is None or context.area.type != 'VIEW_3D':
        return

    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.depth_test_set('LESS_EQUAL')

        source = _objects.source_from_context(context)

        # Fallback: active generated pattern can still resolve its source.
        if source is None:
            unfold_fallback = _objects.resolve_unfold_for_layout(context)
            if unfold_fallback is not None:
                source_name = unfold_fallback.get("tsunfold_source", "")
                source = bpy.data.objects.get(source_name)

        if source is not None:
            if (
                not bool(source.hide_get())
                and not bool(source.hide_viewport)
                and _interact.island_highlight.get("source", "") == source.name
                and _interact.island_highlight.get("source_faces")
            ):
                wanted = set(
                    int(v)
                    for v in _interact.island_highlight.get(
                        "source_faces",
                        [],
                    )
                )

                shader.bind()
                gpu.state.blend_set('ALPHA')
                shader.uniform_float(
                    "color",
                    (1.0, 0.65, 0.05, 0.42),
                )

                source.data.calc_loop_triangles()
                src_verts = []
                for tri in source.data.loop_triangles:
                    if int(tri.polygon_index) not in wanted:
                        continue
                    for vi in tri.vertices:
                        src_verts.append(
                            source.matrix_world @ source.data.vertices[vi].co
                        )

                if src_verts:
                    batch = batch_for_shader(
                        shader,
                        'TRIS',
                        {"pos": src_verts},
                    )
                    batch.draw(shader)

                unfold_h = _objects.unfold_for_source(source)
                if unfold_h is not None and not unfold_h.hide_viewport:
                    mapping = _objects.flat_face_source_map(unfold_h)
                    unfold_h.data.calc_loop_triangles()
                    flat_verts = []

                    for tri in unfold_h.data.loop_triangles:
                        pi = int(tri.polygon_index)
                        if pi >= len(mapping) or int(mapping[pi]) not in wanted:
                            continue
                        for vi in tri.vertices:
                            flat_verts.append(
                                unfold_h.matrix_world
                                @ unfold_h.data.vertices[vi].co
                            )

                    if flat_verts:
                        batch = batch_for_shader(
                            shader,
                            'TRIS',
                            {"pos": flat_verts},
                        )
                        batch.draw(shader)

                gpu.state.blend_set('NONE')

            # ------------------------------------------------------
            # Draw live seams only while this source is actively loaded
            # into 型紙ヘルパー. After 作業終了 the source name is cleared,
            # so the red helper overlay disappears while real Blender seams
            # remain untouched.
            # ------------------------------------------------------
            loaded_source_name = str(
                context.scene.get(_session.SEAM_SOURCE, "")
            )
            source_visible = (
                not bool(source.hide_get())
                and not bool(source.hide_viewport)
            )
            show_seam_overlay = (
                bool(loaded_source_name)
                and loaded_source_name == source.name
                and source_visible
            )

            # Heavy source-side calculations are skipped entirely while the
            # original model is hidden.
            seam_segments = (
                _source.seam_segments(source)
                if show_seam_overlay
                else []
            )
            source_colored = (
                _source.colored_segments(
                    context,
                    source,
                )
                if (
                    source_visible
                    and not bool(
                        getattr(
                            context.scene,
                            "tsunfold_lightweight_view",
                            True,
                        )
                    )
                )
                else []
            )

            if seam_segments:
                seam_verts = []
                for pa, pb in seam_segments:
                    seam_verts.extend((pa, pb))

                seam_batch = batch_for_shader(
                    shader,
                    'LINES',
                    {"pos": seam_verts},
                )
                # シームの赤線はビューポート確認用の目印であって、
                # 印刷される合印とは別物。以前は線の太さを
                # 「合印の太さ」から取っていたため、合印の設定を変えると
                # シーム線まで太くなり、両者が同じものに見えてしまっていた。
                gpu.state.line_width_set(2.0)
                shader.bind()
                shader.uniform_float(
                    "color",
                    (1.0, 0.05, 0.02, 1.0),
                )
                seam_batch.draw(shader)

            # Do not ray-cast every mark on every viewport redraw.
            # GPU depth testing already hides lines behind the mesh and is
            # dramatically cheaper while orbiting/panning the view.
            for pa, pb, color, width_mm in source_colored:
                batch = batch_for_shader(
                    shader,
                    'LINES',
                    {"pos": [pa, pb]},
                )
                gpu.state.line_width_set(
                    max(
                        1.0,
                        min(
                            12.0,
                            float(width_mm) * 4.0,
                        ),
                    )
                )
                shader.bind()
                shader.uniform_float(
                    "color",
                    (color[0], color[1], color[2], 1.0),
                )
                batch.draw(shader)

            # ------------------------------------------------------
            # If a generated flat pattern exists, draw the transferred
            # markings there at the same time, regardless of selection.
            # ------------------------------------------------------
            unfold = _objects.unfold_for_source(source)
            show_transferred = (
                unfold is not None
                and (
                    not unfold.hide_viewport
                    or context.scene.get(
                        _session.DISPLAY_MODE,
                        "POLY",
                    ) == "SMOOTH"
                )
            )

            if show_transferred:
                flat_colored = _compute.colored_segments(
                    context,
                    source,
                    unfold,
                )

                draw_thick_segments(flat_colored, unfold, context.scene)
                shader.bind()

            # ------------------------------------------------------
            # Live placement preview on source.
            # ------------------------------------------------------
            preview_mode = _interact.active_tool(context.scene)
            preview_source = bpy.data.objects.get(
                _interact.live_preview.get("source", "")
            )

            if preview_source is source:
                preview_color = _interact.current_color(
                    context.scene,
                    preview_mode,
                )

                if (
                    preview_mode == "NOTCH"
                    and int(_interact.live_preview.get("notch_edge", -1)) >= 0
                ):
                    item = {
                        "edge": int(_interact.live_preview["notch_edge"]),
                        "t": float(_interact.live_preview["notch_t"]),
                    }
                    seg = _source.notch_segment(
                        context,
                        source,
                        item,
                    )
                    if seg is not None:
                        batch = batch_for_shader(
                            shader,
                            'LINES',
                            {"pos": [seg[0], seg[1]]},
                        )
                        gpu.state.line_width_set(
                            max(
                                1.0,
                                min(
                                    12.0,
                                    float(
                                        getattr(
                                            context.scene,
                                            "tsunfold_notch_thickness_mm",
                                            0.6,
                                        )
                                    ) * 4.0,
                                ),
                            )
                        )
                        shader.bind()
                        shader.uniform_float(
                            "color",
                            (
                                preview_color[0],
                                preview_color[1],
                                preview_color[2],
                                1.0,
                            ),
                        )
                        batch.draw(shader)

                    # Also preview the same notch on generated pattern,
                    # if it already exists.
                    if (
                        unfold is not None
                        and (
                            not unfold.hide_viewport
                            or context.scene.get(
                                _session.DISPLAY_MODE,
                                "POLY",
                            ) == "SMOOTH"
                        )
                    ):
                        preview_item = {
                            "type": "notch_edge",
                            "edge": int(_interact.live_preview["notch_edge"]),
                            "t": float(_interact.live_preview["notch_t"]),
                        }
                        if context.scene.get(
                            _session.DISPLAY_MODE,
                            "POLY",
                        ) == "SMOOTH":
                            preview_notches = _compute.smooth_notch_segments(
                                context,
                                source,
                                unfold,
                                preview_item,
                            )
                        else:
                            preview_notches = _compute.flat_notch_segments(
                                context,
                                source,
                                unfold,
                                preview_item,
                            )

                        for fa, fb in preview_notches:
                            batch = batch_for_shader(
                                shader,
                                'LINES',
                                {"pos": [fa, fb]},
                            )
                            gpu.state.line_width_set(3.0)
                            shader.bind()
                            shader.uniform_float(
                                "color",
                                (
                                    preview_color[0],
                                    preview_color[1],
                                    preview_color[2],
                                    1.0,
                                ),
                            )
                            batch.draw(shader)

                if preview_mode == "ARROW":
                    start_anchor = _interact.live_preview.get("arrow_start")
                    hover_anchor = _interact.live_preview.get("hover_anchor")

                    if start_anchor is not None and hover_anchor is not None:
                        wa = _interact.preview_anchor_world(
                            source,
                            start_anchor,
                        )
                        wb = _interact.preview_anchor_world(
                            source,
                            hover_anchor,
                        )

                        if wa is not None and wb is not None:
                            direction = wb - wa
                            if direction.length > 1e-12:
                                direction.normalize()
                                head = _units.scene_mm_to_bu(
                                    context.scene,
                                    context.scene.tsunfold_arrow_head_mm,
                                )
                                normal = (
                                    source.matrix_world.to_3x3()
                                    @ Vector((0.0, 0.0, 1.0))
                                )
                                side = direction.cross(normal)
                                if side.length <= 1e-12:
                                    side = Vector((1.0, 0.0, 0.0))
                                side.normalize()

                                back = wb - direction * head
                                preview_segments = [
                                    (wa, wb),
                                    (wb, back + side * head * 0.45),
                                    (wb, back - side * head * 0.45),
                                ]

                                gpu.state.line_width_set(
                                    max(
                                        1.0,
                                        float(
                                            context.scene.tsunfold_arrow_thickness_mm
                                        ) * 2.0,
                                    )
                                )
                                shader.bind()
                                shader.uniform_float(
                                    "color",
                                    (
                                        preview_color[0],
                                        preview_color[1],
                                        preview_color[2],
                                        1.0,
                                    ),
                                )

                                for pa, pb in preview_segments:
                                    batch = batch_for_shader(
                                        shader,
                                        'LINES',
                                        {"pos": [pa, pb]},
                                    )
                                    batch.draw(shader)

        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set('NONE')

    except Exception:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.depth_test_set('NONE')
        except Exception:
            _debug.swallowed("overlay._draw_pattern_marks_3d")


def draw_text_2d():
    context = bpy.context

    if context is not None and manual_layout_active(context.scene):
        return

    if (
        context is None
        or context.area is None
        or context.area.type != 'VIEW_3D'
        or context.region is None
        or context.space_data is None
    ):
        return

    source = _objects.source_from_context(context)

    if source is None:
        unfold_fallback = _objects.resolve_unfold_for_layout(context)
        if unfold_fallback is not None:
            source_name = unfold_fallback.get("tsunfold_source", "")
            source = bpy.data.objects.get(source_name)

    if source is None:
        return

    rv3d = getattr(context.space_data, "region_3d", None)
    if rv3d is None:
        return

    try:
        font_id = 0

        def draw_label(
            text,
            world_pos,
            size_mm,
            color,
            angle=0.0,
            edge_locked=False,
        ):
            screen = view3d_utils.location_3d_to_region_2d(
                context.region,
                rv3d,
                world_pos,
            )
            if screen is None:
                return

            px_size = max(8, int(round(float(size_mm) * 3.2)))
            blf.size(font_id, px_size)
            draw_x = float(screen.x)
            draw_y = float(screen.y)

            if edge_locked:
                # Geometry stays exactly on the edge. Only the glyph is nudged
                # a few screen pixels so it does not sit directly on top of
                # the seam line.
                normal_x = -math.sin(float(angle))
                normal_y = math.cos(float(angle))
                draw_x += normal_x * 4.0
                draw_y += normal_y * 4.0

            blf.position(
                font_id,
                draw_x,
                draw_y,
                0.0,
            )
            blf.color(
                font_id,
                color[0],
                color[1],
                color[2],
                1.0,
            )

            try:
                if abs(float(angle)) > 1e-8:
                    blf.enable(font_id, blf.ROTATION)
                    blf.rotation(font_id, float(angle))
                else:
                    blf.disable(font_id, blf.ROTATION)
            except Exception:
                _debug.swallowed("overlay._draw_pattern_text_2d.draw_label")

            blf.draw(font_id, str(text))

            try:
                blf.disable(font_id, blf.ROTATION)
            except Exception:
                _debug.swallowed("overlay._draw_pattern_text_2d.draw_label")

        # ------------------------------------------------------
        # Source-model labels.
        # In lightweight mode skip these completely. Previously each source
        # label also performed a scene ray-cast on every viewport redraw,
        # which was one of the largest orbit/pan slowdowns on dense meshes.
        # ------------------------------------------------------
        source_visible = (
            not bool(source.hide_get())
            and not bool(source.hide_viewport)
        )
        lightweight = bool(
            getattr(
                context.scene,
                "tsunfold_lightweight_view",
                True,
            )
        )

        source_labels = (
            source_text_items(source, context.scene)
            if source_visible and not lightweight
            else []
        )

        for text, world_pos, size_mm, color in source_labels:
            screen = view3d_utils.location_3d_to_region_2d(
                context.region,
                rv3d,
                world_pos,
            )
            if screen is None:
                continue

            px_size = max(8, int(round(float(size_mm) * 3.2)))
            blf.size(font_id, px_size)
            blf.position(
                font_id,
                screen.x + 5.0,
                screen.y + 5.0,
                0.0,
            )
            blf.color(
                font_id,
                color[0],
                color[1],
                color[2],
                1.0,
            )
            blf.draw(font_id, text)

        # ------------------------------------------------------
        # Transferred labels on generated flat pattern.
        # ------------------------------------------------------
        unfold = _objects.unfold_for_source(source)

        if (
            unfold is not None
            and (
                not unfold.hide_viewport
                or context.scene.get(
                    _session.DISPLAY_MODE,
                    "POLY",
                ) == "SMOOTH"
            )
        ):
            flat_labels = flat_text_items(
                source,
                unfold,
                context.scene,
            )

            for text, world_pos, size_mm, color in flat_labels:
                draw_label(
                    text,
                    world_pos,
                    size_mm,
                    color,
                    0.0,
                )

            # Flat-only free memos. These are intentionally not linked
            # to source-model correspondence data.
            memo_items = flat_memo_text_items(unfold)
            selected_unfold = _interact.selected_memo.get("unfold", "")
            selected_index = int(_interact.selected_memo.get("index", -1))

            for memo_index, (text, world_pos, size_mm, color, angle) in enumerate(memo_items):
                display_text = (
                    f"▶ {text}"
                    if unfold.name == selected_unfold and memo_index == selected_index
                    else text
                )
                draw_label(
                    display_text,
                    world_pos,
                    size_mm,
                    color,
                    angle,
                )

            if bool(
                getattr(
                    context.scene,
                    "tsunfold_auto_island_ids",
                    True,
                )
            ):
                for (
                    text,
                    world_pos,
                    size_mm,
                    color,
                    angle,
                    edge_locked,
                ) in _compute.text_items(
                    context,
                    source,
                    unfold,
                ):
                    draw_label(
                        text,
                        world_pos,
                        size_mm,
                        color,
                        angle,
                        edge_locked,
                    )

        # ------------------------------------------------------
        # Live number/text preview follows the mouse on source.
        # ------------------------------------------------------
        preview_mode = _interact.active_tool(context.scene)

        if preview_mode in {"NUMBER", "TEXT"}:
            preview_source = bpy.data.objects.get(
                _interact.live_preview.get("source", "")
            )
            hover_anchor = _interact.live_preview.get("hover_anchor")

            if (
                preview_source is source
                and hover_anchor is not None
            ):
                world_pos = _interact.preview_anchor_world(
                    source,
                    hover_anchor,
                )

                if world_pos is not None:
                    screen = view3d_utils.location_3d_to_region_2d(
                        context.region,
                        rv3d,
                        world_pos,
                    )

                    if screen is not None:
                        if preview_mode == "NUMBER":
                            preview_text = str(
                                context.scene.tsunfold_next_number
                            )
                            size_mm = float(
                                context.scene.tsunfold_number_size_mm
                            )
                        else:
                            preview_text = str(
                                context.scene.tsunfold_custom_text
                            )
                            size_mm = float(
                                context.scene.tsunfold_text_size_mm
                            )

                        if preview_text:
                            color = _interact.current_color(
                                context.scene,
                                preview_mode,
                            )
                            blf.size(
                                font_id,
                                max(8, int(round(size_mm * 3.2))),
                            )
                            blf.position(
                                font_id,
                                screen.x + 5.0,
                                screen.y + 5.0,
                                0.0,
                            )
                            blf.color(
                                font_id,
                                color[0],
                                color[1],
                                color[2],
                                1.0,
                            )
                            blf.draw(font_id, preview_text)

        # Viewport-only arrow direction legend.
        # This is deliberately not part of the pattern geometry or PNG.
        draw_direction_arrow_overlay(context)
        draw_arrow_hud(
            context,
            font_id,
        )

    except Exception:
        _debug.swallowed("overlay._draw_pattern_text_2d")
