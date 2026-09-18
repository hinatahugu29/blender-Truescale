import bpy
import gpu
from bpy.props import EnumProperty, StringProperty, FloatProperty, BoolProperty, FloatVectorProperty
from bpy_extras.io_utils import ExportHelper
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils import geometry
from mathutils.kdtree import KDTree
from statistics import median
from pathlib import Path
import struct
import zlib
import binascii
import math
import heapq
import json
import time
import blf

PAPER_SIZES_MM = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
    "B5": (182.0, 257.0),
    "B4": (257.0, 364.0),
}

PRINT_DPI = 300
UNFOLD_SUFFIX = "_展開図"
_draw_handle = None
_pattern_draw_handle = None
_pattern_text_handle = None
_pattern_live_preview = {
    "mode": "NONE",
    "source": "",
    "notch_edge": -1,
    "notch_t": 0.5,
    "hover_anchor": None,
    "arrow_start": None,
}

_pattern_island_highlight = {
    "source": "",
    "source_faces": [],
}

_pattern_auto_island_cache = {
    "key": None,
    "records": None,
    "face_to_island": None,
    "adjacency": None,
}

# Incremented only when pattern geometry / annotations / display settings
# actually change. View orbit/pan does NOT touch this, so expensive pattern
# analysis can stay cached across viewport redraws.
_pattern_cache_epoch = 0
_pattern_draw_cache = {}






# ------------------------------------------------------------
# Unit / geometry helpers
# ------------------------------------------------------------

def _scene_scale_to_meters(scene):
    """Custom Scene mode: the current Scene Unit Scale is authoritative."""
    try:
        scale = float(scene.unit_settings.scale_length)
    except Exception:
        scale = 1.0

    return scale if scale > 0.0 else 1.0


def _scene_bu_to_mm(scene):
    """Return how many physical millimeters one Blender Unit represents."""
    return _scene_scale_to_meters(scene) * 1000.0


def _scene_unit_summary(scene):
    scale = _scene_scale_to_meters(scene)
    mm_per_bu = _scene_bu_to_mm(scene)
    return scale, mm_per_bu


def _mm_to_bu(scene, mm):
    return (float(mm) / 1000.0) / _scene_scale_to_meters(scene)


def _bu_to_mm(scene, bu):
    return float(bu) * _scene_scale_to_meters(scene) * 1000.0


def _world_edge_length(obj, v1, v2):
    """Physical source edge length in current Scene Blender Units.

    Full matrix_world is used so unapplied object scale, parent scale,
    rotation, and other object transforms are reflected automatically.
    """
    mw = obj.matrix_world
    p1 = mw @ v1.co
    p2 = mw @ v2.co
    return (p2 - p1).length


def _calc_real_scale(obj, mesh, uv_layer):
    ratios = []
    eps = 1e-10

    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        if len(loops) < 2:
            continue

        for i, li_a in enumerate(loops):
            li_b = loops[(i + 1) % len(loops)]
            loop_a = mesh.loops[li_a]
            loop_b = mesh.loops[li_b]
            uv_a = uv_layer.data[li_a].uv
            uv_b = uv_layer.data[li_b].uv

            uv_len = (uv_b - uv_a).length
            if uv_len <= eps:
                continue

            v_a = mesh.vertices[loop_a.vertex_index]
            v_b = mesh.vertices[loop_b.vertex_index]
            world_len = _world_edge_length(obj, v_a, v_b)

            if world_len > eps:
                ratios.append(world_len / uv_len)

    return median(ratios) if ratios else None


def _build_flat_mesh(context, src_obj, mesh, uv_layer, scale_bu_per_uv):
    verts = []
    faces = []
    vert_map = {}
    flat_vertex_source_vertex = []
    flat_face_source_face = []
    flat_side_source_edge = {}

    source_edge_lookup = {
        tuple(sorted((int(e.vertices[0]), int(e.vertices[1])))): int(e.index)
        for e in mesh.edges
    }

    def uv_key(loop_index):
        loop = mesh.loops[loop_index]
        uv = uv_layer.data[loop_index].uv
        return (
            loop.vertex_index,
            round(float(uv.x), 8),
            round(float(uv.y), 8),
        )

    for poly in mesh.polygons:
        face = []
        loop_indices = list(poly.loop_indices)

        for li in loop_indices:
            loop = mesh.loops[li]
            key = uv_key(li)
            idx = vert_map.get(key)

            if idx is None:
                uv = uv_layer.data[li].uv
                idx = len(verts)
                vert_map[key] = idx
                verts.append((
                    float(uv.x) * scale_bu_per_uv,
                    float(uv.y) * scale_bu_per_uv,
                    0.0,
                ))
                flat_vertex_source_vertex.append(int(loop.vertex_index))

            face.append(idx)

        if len(face) >= 3:
            faces.append(face)
            flat_face_source_face.append(int(poly.index))

            for i, li in enumerate(loop_indices):
                lj = loop_indices[(i + 1) % len(loop_indices)]

                sva = int(mesh.loops[li].vertex_index)
                svb = int(mesh.loops[lj].vertex_index)
                source_edge = source_edge_lookup.get(
                    tuple(sorted((sva, svb))),
                    -1,
                )

                fva = int(face[i])
                fvb = int(face[(i + 1) % len(face)])
                flat_side_source_edge[tuple(sorted((fva, fvb)))] = source_edge

    if not verts or not faces:
        return None

    mesh_name = f"{src_obj.name}{UNFOLD_SUFFIX}_Mesh"
    obj_name = f"{src_obj.name}{UNFOLD_SUFFIX}"

    new_mesh = bpy.data.meshes.new(mesh_name)
    new_mesh.from_pydata(verts, [], faces)
    new_mesh.update()

    new_obj = bpy.data.objects.new(obj_name, new_mesh)
    context.collection.objects.link(new_obj)

    new_obj.location = (0.0, 0.0, 0.0)
    new_obj.rotation_euler = (0.0, 0.0, 0.0)
    new_obj.scale = (1.0, 1.0, 1.0)

    new_obj["unfold_helper_generated"] = True
    new_obj["unfold_helper_source"] = src_obj.name

    # Record the unit context used to create this pattern.
    scene_scale, mm_per_bu = _scene_unit_summary(context.scene)
    new_obj["pattern_helper_scene_scale_length"] = float(scene_scale)
    new_obj["pattern_helper_mm_per_bu"] = float(mm_per_bu)
    new_obj["pattern_helper_source_object_scale"] = [
        float(src_obj.scale.x),
        float(src_obj.scale.y),
        float(src_obj.scale.z),
    ]

    new_obj["pattern_helper_flat_vertex_source_json"] = json.dumps(
        flat_vertex_source_vertex
    )
    new_obj["pattern_helper_flat_face_source_json"] = json.dumps(
        flat_face_source_face
    )

    flat_edge_source = [-1] * len(new_mesh.edges)
    for edge in new_mesh.edges:
        key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        flat_edge_source[edge.index] = int(
            flat_side_source_edge.get(key, -1)
        )

    new_obj["pattern_helper_flat_edge_source_json"] = json.dumps(
        flat_edge_source
    )

    for o in context.selected_objects:
        o.select_set(False)

    new_obj.select_set(True)
    context.view_layer.objects.active = new_obj
    return new_obj


def _get_face_islands(mesh):
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


def _pack_islands(context, obj, spacing_mm):
    """Pack islands left-to-right. Translation only, never scaling."""
    if not obj or obj.type != 'MESH':
        return

    mesh = obj.data
    islands = _get_face_islands(mesh)
    if not islands:
        return

    spacing_bu = _mm_to_bu(context.scene, max(0.0, spacing_mm))

    data = []
    for ids in islands:
        xs = [mesh.vertices[i].co.x for i in ids]
        ys = [mesh.vertices[i].co.y for i in ids]
        data.append({
            "verts": ids,
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
        })

    # Stable order based on current left-to-right position.
    data.sort(key=lambda c: (c["min_x"], c["min_y"]))

    cursor_x = 0.0
    baseline_y = 0.0

    for comp in data:
        dx = cursor_x - comp["min_x"]
        dy = baseline_y - comp["min_y"]

        for vi in comp["verts"]:
            mesh.vertices[vi].co.x += dx
            mesh.vertices[vi].co.y += dy

        width = comp["max_x"] - comp["min_x"]
        cursor_x += width + spacing_bu

    mesh.update()


def _active_unfold_object(context):
    obj = context.active_object
    if (
        obj
        and obj.type == 'MESH'
        and bool(obj.get("unfold_helper_generated", False))
    ):
        return obj
    return None


def _object_xy_size_mm(context, obj):
    if obj is None or obj.type != 'MESH' or not obj.data.vertices:
        return None

    points = [obj.matrix_world @ v.co for v in obj.data.vertices]
    min_x = min(p.x for p in points)
    max_x = max(p.x for p in points)
    min_y = min(p.y for p in points)
    max_y = max(p.y for p in points)

    return (
        _bu_to_mm(context.scene, max_x - min_x),
        _bu_to_mm(context.scene, max_y - min_y),
    )


# ------------------------------------------------------------
# Paper guide overlay (no Blender object is created)
# ------------------------------------------------------------

def _paper_display_name(scene):
    key = str(getattr(scene, "unfold_helper_paper_size", "A4"))
    if key == "CUSTOM":
        w, h = _paper_dimensions_mm(scene)
        return f"カスタム {w:.0f}×{h:.0f} mm"
    return key



def _paper_dimensions_mm(scene):
    paper_key = str(getattr(scene, "unfold_helper_paper_size", "A4"))

    if paper_key == "CUSTOM":
        width = max(
            1.0,
            float(getattr(scene, "unfold_helper_custom_paper_width_mm", 600.0)),
        )
        height = max(
            1.0,
            float(getattr(scene, "unfold_helper_custom_paper_height_mm", 900.0)),
        )
        # Custom width/height are literal. No automatic portrait/landscape swap.
        return width, height

    base_w, base_h = PAPER_SIZES_MM[paper_key]
    portrait = (min(base_w, base_h), max(base_w, base_h))
    landscape = (portrait[1], portrait[0])

    orientation = scene.unfold_helper_orientation
    if orientation == "LANDSCAPE":
        return landscape
    if orientation == "PORTRAIT":
        return portrait

    # AUTO defaults to portrait for the visible guide.
    # Export can still choose the fitting orientation later if desired.
    return portrait



def _smooth_curve_segments_world_xy(obj, samples_per_segment=32):
    """Sample generated Bezier curve splines into world-space XY line segments."""
    if (
        obj is None
        or obj.type != 'CURVE'
        or not bool(obj.get("unfold_helper_smooth_generated", False))
    ):
        return []

    segments = []

    for spline in obj.data.splines:
        if spline.type != 'BEZIER':
            continue

        bps = spline.bezier_points
        n = len(bps)
        if n < 2:
            continue

        seg_count = n if spline.use_cyclic_u else n - 1

        for i in range(seg_count):
            a = bps[i]
            b = bps[(i + 1) % n]

            p0 = a.co.copy()
            p1 = a.handle_right.copy()
            p2 = b.handle_left.copy()
            p3 = b.co.copy()

            prev_local = p0
            prev_world = obj.matrix_world @ prev_local

            for s in range(1, samples_per_segment + 1):
                t = s / samples_per_segment
                cur_local = _bezier_point(p0, p1, p2, p3, t)
                cur_world = obj.matrix_world @ cur_local

                segments.append(
                    ((prev_world.x, prev_world.y), (cur_world.x, cur_world.y))
                )

                prev_world = cur_world

    return segments


def _preview_outline_segments(context):
    """Return the outline matching the currently displayed finish mode."""
    mode = context.scene.get("unfold_helper_display_mode", "POLY")

    if mode == "SMOOTH":
        # Prefer active smooth object.
        obj = context.active_object
        if (
            obj
            and obj.type == 'CURVE'
            and bool(obj.get("unfold_helper_smooth_generated", False))
            and not obj.hide_viewport
        ):
            return _smooth_curve_segments_world_xy(obj)

        # Fallback to any visible generated smooth curve.
        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("unfold_helper_smooth_generated", False))
                and not candidate.hide_viewport
            ):
                return _smooth_curve_segments_world_xy(candidate)

    # POLY or fallback.
    obj = _active_unfold_object(context)
    if obj is not None and not obj.hide_viewport:
        return _boundary_segments_world_xy(obj)

    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("unfold_helper_generated", False))
            and not candidate.hide_viewport
        ):
            return _boundary_segments_world_xy(candidate)

    return []


def _draw_paper_guide():
    context = bpy.context
    if context is None or context.scene is None:
        return

    scene = context.scene
    show_paper = getattr(scene, "unfold_helper_show_paper", True)
    show_preview = getattr(scene, "unfold_helper_preview", False)

    if not show_paper and not show_preview:
        return

    try:
        paper_w_mm, paper_h_mm = _paper_dimensions_mm(scene)
        w = _mm_to_bu(scene, paper_w_mm)
        h = _mm_to_bu(scene, paper_h_mm)

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
            segs = _preview_outline_segments(context)
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

            source = _pattern_source_object_from_context(context)
            unfold = _pattern_unfold_for_source(source) if source else None

            if source is not None and unfold is not None:
                for a, b, color, width_mm in _pattern_flat_colored_segments(
                    context,
                    source,
                    unfold,
                ):
                    mark_batch = batch_for_shader(
                        shader,
                        'LINES',
                        {"pos": [a, b]},
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
                    mark_batch.draw(shader)

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


def _tag_redraw():
    wm = bpy.context.window_manager if bpy.context else None
    if not wm:
        return

    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()




def _pattern_sanitize_arrow_axis(scene):
    axis = str(
        getattr(
            scene,
            "pattern_helper_arrow_up_axis",
            "Z",
        )
    )

    if axis not in {"X", "Y", "Z"}:
        try:
            scene.pattern_helper_arrow_up_axis = "Z"
        except Exception:
            pass
        return "Z"

    return axis



def _pattern_manual_layout_active(scene):
    return bool(scene.get("pattern_helper_manual_layout_active", False))


def _pattern_setting_updated(self, context):
    _pattern_invalidate_layout_cache()


def _pattern_notch_setting_updated(self, context):
    """Live-update notch geometry/settings without requiring the update button."""
    scene = context.scene

    # Any length/thickness/color change must invalidate cached viewport data.
    _pattern_invalidate_layout_cache()

    # Division changes in AUTO mode also rebuild the actual auto-notch
    # annotations immediately, so the count/positions update live.
    try:
        if str(getattr(scene, "pattern_helper_notch_mode", "AUTO")) != "AUTO":
            return

        source = _pattern_seam_source(context)
        if source is None:
            source = _pattern_source_object_from_context(context)

        if source is None or source.type != 'MESH':
            return

        _pattern_refresh_auto_notches(
            context,
            source,
        )
    except Exception:
        # Property update callbacks must never break Blender UI interaction.
        pass

    _pattern_invalidate_layout_cache()



def _paper_setting_updated(self, context):
    _tag_redraw()



def _pattern_print_preview_source_visibility(context, preview_on):
    scene = context.scene

    if preview_on:
        source = _pattern_seam_source(context)
        if source is None:
            source = _pattern_source_object_from_context(context)

        # Active object may be the generated pattern; resolve its source.
        if source is None:
            unfold = _resolve_unfold_mesh_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("unfold_helper_source", "")
                )

        if source is None:
            return

        scene["pattern_helper_preview_source_name"] = source.name
        scene["pattern_helper_preview_source_hide_get"] = bool(
            source.hide_get()
        )
        scene["pattern_helper_preview_source_hide_viewport"] = bool(
            source.hide_viewport
        )

        source.hide_set(True)
        source.hide_viewport = True

    else:
        source_name = str(
            scene.get("pattern_helper_preview_source_name", "")
        )
        source = bpy.data.objects.get(source_name)

        if source is not None:
            try:
                source.hide_viewport = bool(
                    scene.get(
                        "pattern_helper_preview_source_hide_viewport",
                        False,
                    )
                )
                source.hide_set(
                    bool(
                        scene.get(
                            "pattern_helper_preview_source_hide_get",
                            False,
                        )
                    )
                )
            except Exception:
                pass

        scene["pattern_helper_preview_source_name"] = ""
        scene["pattern_helper_preview_source_hide_get"] = False
        scene["pattern_helper_preview_source_hide_viewport"] = False

    _tag_redraw()


def _preview_setting_updated(self, context):
    _tag_redraw()


def _spacing_updated(self, context):
    """Realtime repack when spacing changes."""
    obj = _active_unfold_object(context)
    if obj is None:
        return

    # Editing geometry while in edit mode needs an object-mode data refresh.
    was_edit = (obj.mode == 'EDIT')
    if was_edit:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            return

    _pack_islands(context, obj, context.scene.unfold_helper_spacing_mm)

    if was_edit:
        try:
            bpy.ops.object.mode_set(mode='EDIT')
        except RuntimeError:
            pass

    _tag_redraw()


def _show_generated_from_top(context, obj):
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj

    try:
        bpy.ops.view3d.view_axis(type='TOP', align_active=False)
        bpy.ops.view3d.view_selected(use_all_regions=False)
    except RuntimeError:
        pass



def _rotate_vertices_90(mesh, vert_ids):
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


def _island_bbox(mesh, vert_ids):
    xs = [mesh.vertices[i].co.x for i in vert_ids]
    ys = [mesh.vertices[i].co.y for i in vert_ids]
    return min(xs), min(ys), max(xs), max(ys)


def _move_island_to(mesh, vert_ids, target_min_x, target_min_y):
    min_x, min_y, max_x, max_y = _island_bbox(mesh, vert_ids)
    dx = target_min_x - min_x
    dy = target_min_y - min_y

    for vi in vert_ids:
        mesh.vertices[vi].co.x += dx
        mesh.vertices[vi].co.y += dy


def _try_shelf_layout(context, obj, allow_rotate=True):
    """Simple deterministic paper packing.

    Uses island bounding rectangles, current paper size/orientation, and
    current spacing. No scaling is ever applied.
    """
    mesh = obj.data
    islands = _get_face_islands(mesh)
    if not islands:
        return False, "アイランドがありません"

    scene = context.scene
    paper_w_mm, paper_h_mm = _paper_dimensions_mm(scene)
    paper_w = _mm_to_bu(scene, paper_w_mm)
    paper_h = _mm_to_bu(scene, paper_h_mm)

    # Printer-safe margin must be defined before usable area is calculated.
    safe = _mm_to_bu(scene, 10.0)
    gap = _mm_to_bu(scene, 5.0)

    usable_w = max(0.0, paper_w - safe * 2.0)
    usable_h = max(0.0, paper_h - safe * 2.0)

    # Snapshot original coordinates so failure can restore everything.
    original = {v.index: v.co.copy() for v in mesh.vertices}

    # Work largest-first, tends to pack more reliably.
    items = []
    for ids in islands:
        min_x, min_y, max_x, max_y = _island_bbox(mesh, ids)
        w = max_x - min_x
        h = max_y - min_y
        items.append({
            "verts": ids,
            "w": w,
            "h": h,
            "area": w * h,
        })

    items.sort(key=lambda it: (max(it["w"], it["h"]), it["area"]), reverse=True)

    cursor_x = 0.0
    cursor_y = 0.0
    row_h = 0.0

    for item in items:
        ids = item["verts"]
        min_x, min_y, max_x, max_y = _island_bbox(mesh, ids)
        w = max_x - min_x
        h = max_y - min_y

        # If the island cannot fit the current row, consider rotation first
        # when that makes it fit horizontally and within paper height.
        rotated = False

        def fits_here(test_w, test_h):
            return (
                cursor_x + test_w <= usable_w + 1e-9
                and cursor_y + test_h <= usable_h + 1e-9
            )

        if allow_rotate:
            rw, rh = h, w
            normal_ok = fits_here(w, h)
            rotated_ok = fits_here(rw, rh)

            # Prefer rotation if normal does not fit but rotated does,
            # or if both fit and rotation wastes less remaining row width.
            if rotated_ok and (
                not normal_ok
                or (usable_w - (cursor_x + rw)) < (usable_w - (cursor_x + w))
            ):
                _rotate_vertices_90(mesh, ids)
                rotated = True
                min_x, min_y, max_x, max_y = _island_bbox(mesh, ids)
                w = max_x - min_x
                h = max_y - min_y

        # Start a new row if needed.
        if cursor_x > 0.0 and cursor_x + w > usable_w + 1e-9:
            cursor_x = 0.0
            cursor_y += row_h + gap
            row_h = 0.0

            # Re-evaluate rotation for the fresh row.
            if allow_rotate:
                min_x, min_y, max_x, max_y = _island_bbox(mesh, ids)
                w = max_x - min_x
                h = max_y - min_y
                rw, rh = h, w

                normal_ok = (
                    w <= usable_w + 1e-9
                    and cursor_y + h <= usable_h + 1e-9
                )
                rotated_ok = (
                    rw <= usable_w + 1e-9
                    and cursor_y + rh <= usable_h + 1e-9
                )

                if rotated_ok and (
                    not normal_ok
                    or (usable_w - rw) < (usable_w - w)
                ):
                    _rotate_vertices_90(mesh, ids)
                    rotated = not rotated
                    min_x, min_y, max_x, max_y = _island_bbox(mesh, ids)
                    w = max_x - min_x
                    h = max_y - min_y

        # Hard failure: no scaling, no clipping.
        if (
            w > usable_w + 1e-9
            or cursor_y + h > usable_h + 1e-9
        ):
            for vi, co in original.items():
                mesh.vertices[vi].co = co
            mesh.update()
            return False, (
                f"{paper_w_mm:.0f}×{paper_h_mm:.0f} mm の用紙に "
                "すべてのアイランドを収められませんでした"
            )

        _move_island_to(mesh, ids, cursor_x, cursor_y)

        cursor_x += w + gap
        row_h = max(row_h, h)

    # Center the final packed layout inside the safe printable area.
    all_x = [v.co.x for v in mesh.vertices]
    all_y = [v.co.y for v in mesh.vertices]

    if all_x and all_y:
        min_x = min(all_x)
        max_x = max(all_x)
        min_y = min(all_y)
        max_y = max(all_y)

        packed_w = max_x - min_x
        packed_h = max_y - min_y

        target_min_x = safe + max(0.0, (usable_w - packed_w) * 0.5)
        target_min_y = safe + max(0.0, (usable_h - packed_h) * 0.5)

        dx = target_min_x - min_x
        dy = target_min_y - min_y

        for v in mesh.vertices:
            v.co.x += dx
            v.co.y += dy

    mesh.update()
    return True, (
        f"{_paper_display_name(scene)} / 周囲10mm余白で中央配置しました"
    )



# ------------------------------------------------------------
# PNG helpers
# ------------------------------------------------------------

def _boundary_segments_world_xy(obj):
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


def _segments_bbox(segments):
    pts = [p for seg in segments for p in seg]
    if not pts:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _export_paper_dimensions(scene, shape_w_mm, shape_h_mm):
    paper_key = str(getattr(scene, "unfold_helper_paper_size", "A4"))

    if paper_key == "CUSTOM":
        return _paper_dimensions_mm(scene)

    orientation = str(
        getattr(scene, "unfold_helper_orientation", "PORTRAIT")
    )
    base_w, base_h = PAPER_SIZES_MM[paper_key]
    portrait = (min(base_w, base_h), max(base_w, base_h))
    landscape = (portrait[1], portrait[0])

    if orientation == "PORTRAIT":
        return portrait
    if orientation == "LANDSCAPE":
        return landscape

    fitting = [
        p for p in (portrait, landscape)
        if shape_w_mm <= p[0] + 1e-6 and shape_h_mm <= p[1] + 1e-6
    ]
    if fitting:
        return min(
            fitting,
            key=lambda p: (p[0] - shape_w_mm) * (p[1] - shape_h_mm)
        )

    def overflow(p):
        return max(0.0, shape_w_mm - p[0]) + max(0.0, shape_h_mm - p[1])

    return min((portrait, landscape), key=overflow)


def _png_chunk(chunk_type, data):
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(data, crc) & 0xffffffff
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _draw_line_rgb(
    buf,
    width,
    height,
    x0,
    y0,
    x1,
    y1,
    thickness=2,
    color=(0.0, 0.0, 0.0),
):
    x0 = int(round(x0))
    y0 = int(round(y0))
    x1 = int(round(x1))
    y1 = int(round(y1))

    rgb = bytes(
        max(0, min(255, int(round(float(c) * 255.0))))
        for c in color[:3]
    )

    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    radius = max(0, thickness // 2)

    while True:
        for oy in range(-radius, radius + 1):
            yy = y0 + oy
            if yy < 0 or yy >= height:
                continue

            for ox in range(-radius, radius + 1):
                xx = x0 + ox
                if xx < 0 or xx >= width:
                    continue

                pos = (yy * width + xx) * 3
                buf[pos:pos+3] = rgb

        if x0 == x1 and y0 == y1:
            break

        e2 = 2 * err

        if e2 >= dy:
            err += dy
            x0 += sx

        if e2 <= dx:
            err += dx
            y0 += sy


def _write_png_rgb(filepath, width, height, rgb_buffer, dpi):
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels_per_meter = int(round(dpi / 0.0254))
    phys = struct.pack(">IIB", pixels_per_meter, pixels_per_meter, 1)

    stride = width * 3
    raw = bytearray()

    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(rgb_buffer[start:start + stride])

    compressed = zlib.compress(bytes(raw), 6)

    png = (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"pHYs", phys)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )

    filepath.write_bytes(png)



# ------------------------------------------------------------
# Smooth finishing-line helpers
# ------------------------------------------------------------

SMOOTH_SUFFIX = "_なめらか線"


def _boundary_loops_from_mesh(obj):
    mesh = obj.data
    edge_count = {}

    for poly in mesh.polygons:
        vs = list(poly.vertices)
        for i in range(len(vs)):
            a = vs[i]
            b = vs[(i + 1) % len(vs)]
            key = (a, b) if a < b else (b, a)
            edge_count[key] = edge_count.get(key, 0) + 1

    boundary_edges = [e for e, count in edge_count.items() if count == 1]
    if not boundary_edges:
        return []

    adj = {}
    for a, b in boundary_edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    unused = set(boundary_edges)
    loops = []

    def edge_key(a, b):
        return (a, b) if a < b else (b, a)

    while unused:
        start_edge = next(iter(unused))
        start = start_edge[0]
        current = start
        prev = None
        loop_ids = []

        for _ in range(len(boundary_edges) + 5):
            loop_ids.append(current)
            candidates = [
                n for n in adj.get(current, [])
                if edge_key(current, n) in unused
            ]
            if not candidates:
                break

            nxt = None
            for n in candidates:
                if n != prev:
                    nxt = n
                    break
            if nxt is None:
                nxt = candidates[0]

            unused.discard(edge_key(current, nxt))
            prev, current = current, nxt

            if current == start:
                break

        if len(loop_ids) >= 3:
            pts = []
            for vi in loop_ids:
                p = obj.matrix_world @ mesh.vertices[vi].co
                pts.append(Vector((p.x, p.y, 0.0)))
            loops.append(pts)

    return loops


def _polyline_length(points, cyclic=False):
    if len(points) < 2:
        return 0.0

    total = 0.0
    for i in range(len(points) - 1):
        total += (points[i + 1] - points[i]).length

    if cyclic and len(points) > 2:
        total += (points[0] - points[-1]).length

    return total


def _turn_angle_deg(prev_p, p, next_p):
    a = p - prev_p
    b = next_p - p

    if a.length < 1e-12 or b.length < 1e-12:
        return 0.0

    a.normalize()
    b.normalize()
    dot = max(-1.0, min(1.0, a.dot(b)))
    return math.degrees(math.acos(dot))


def _detect_corner_indices(points, threshold_deg=32.0):
    n = len(points)
    if n < 3:
        return [0]

    corners = []
    for i in range(n):
        angle = _turn_angle_deg(
            points[(i - 1) % n],
            points[i],
            points[(i + 1) % n]
        )
        if angle >= threshold_deg:
            corners.append(i)

    if len(corners) < 2:
        corners = [0, n // 2]

    filtered = []
    for idx in corners:
        if not filtered:
            filtered.append(idx)
            continue

        prev_idx = filtered[-1]
        gap = (idx - prev_idx) % n

        if gap <= 1:
            a1 = _turn_angle_deg(
                points[(prev_idx - 1) % n],
                points[prev_idx],
                points[(prev_idx + 1) % n]
            )
            a2 = _turn_angle_deg(
                points[(idx - 1) % n],
                points[idx],
                points[(idx + 1) % n]
            )
            if a2 > a1:
                filtered[-1] = idx
        else:
            filtered.append(idx)

    return sorted(set(filtered))


def _cyclic_chain(points, start_idx, end_idx):
    n = len(points)
    chain = [points[start_idx].copy()]
    i = start_idx

    while i != end_idx:
        i = (i + 1) % n
        chain.append(points[i].copy())
        if len(chain) > n + 2:
            break

    return chain


def _resample_polyline(points, count):
    if len(points) < 2 or count <= 2:
        return [points[0].copy(), points[-1].copy()]

    seg_lengths = []
    cumulative = [0.0]
    total = 0.0

    for i in range(len(points) - 1):
        d = (points[i + 1] - points[i]).length
        seg_lengths.append(d)
        total += d
        cumulative.append(total)

    if total < 1e-12:
        return [points[0].copy() for _ in range(count)]

    result = []
    seg = 0

    for j in range(count):
        target = total * (j / (count - 1))

        while seg < len(seg_lengths) - 1 and cumulative[seg + 1] < target:
            seg += 1

        seg_start = cumulative[seg]
        seg_len = seg_lengths[seg]

        if seg_len < 1e-12:
            result.append(points[seg].copy())
        else:
            t = (target - seg_start) / seg_len
            result.append(points[seg].lerp(points[seg + 1], t))

    result[0] = points[0].copy()
    result[-1] = points[-1].copy()
    return result


def _laplacian_smooth_open(points, iterations=4, strength=0.35):
    if len(points) <= 2:
        return [p.copy() for p in points]

    pts = [p.copy() for p in points]

    for _ in range(iterations):
        new_pts = [pts[0].copy()]

        for i in range(1, len(pts) - 1):
            midpoint = (pts[i - 1] + pts[i + 1]) * 0.5
            new_pts.append(pts[i].lerp(midpoint, strength))

        new_pts.append(pts[-1].copy())
        pts = new_pts

    return pts


def _bezier_point(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        p0 * (u ** 3)
        + p1 * (3.0 * u * u * t)
        + p2 * (3.0 * u * t * t)
        + p3 * (t ** 3)
    )


def _sample_spline_length(spline, samples_per_segment=32):
    bps = spline.bezier_points
    n = len(bps)
    if n < 2:
        return 0.0

    total = 0.0

    for i in range(n - 1):
        a = bps[i]
        b = bps[i + 1]

        p0 = a.co.copy()
        p1 = a.handle_right.copy()
        p2 = b.handle_left.copy()
        p3 = b.co.copy()

        prev = p0
        for s in range(1, samples_per_segment + 1):
            t = s / samples_per_segment
            cur = _bezier_point(p0, p1, p2, p3, t)
            total += (cur - prev).length
            prev = cur

    return total


def _safe_unit(v):
    if v.length < 1e-12:
        return Vector((0.0, 0.0, 0.0))
    return v.normalized()


def _set_fair_bezier_handles(spline, source_chain=None):
    """Set restrained, visually fair Bezier handles.

    Corners are spline endpoints and therefore remain exact.
    Interior tangents use a distance-weighted bisector (centripetal-ish),
    while handle lengths stay close to 1/3 of neighboring chord lengths.
    This favors a clean drafting curve over mathematically exact perimeter.
    """
    bps = spline.bezier_points
    n = len(bps)
    if n < 2:
        return

    anchors = [bp.co.copy() for bp in bps]

    # Optional source tangents near the true corner endpoints.
    start_tangent = None
    end_tangent = None
    if source_chain and len(source_chain) >= 3:
        start_tangent = _safe_unit(source_chain[min(2, len(source_chain)-1)] - source_chain[0])
        end_tangent = _safe_unit(source_chain[-1] - source_chain[max(0, len(source_chain)-3)])

    for i, bp in enumerate(bps):
        bp.handle_left_type = 'FREE'
        bp.handle_right_type = 'FREE'

        if i == 0:
            chord = anchors[1] - anchors[0]
            dist = chord.length
            tangent = start_tangent if start_tangent is not None and start_tangent.length > 0 else _safe_unit(chord)

            # Keep the corner exact and only send the outgoing handle inward.
            bp.handle_left = anchors[0]
            bp.handle_right = anchors[0] + tangent * (dist * 0.34)

        elif i == n - 1:
            chord = anchors[-1] - anchors[-2]
            dist = chord.length
            tangent = end_tangent if end_tangent is not None and end_tangent.length > 0 else _safe_unit(chord)

            bp.handle_left = anchors[-1] - tangent * (dist * 0.34)
            bp.handle_right = anchors[-1]

        else:
            prev_vec = anchors[i] - anchors[i - 1]
            next_vec = anchors[i + 1] - anchors[i]
            prev_len = prev_vec.length
            next_len = next_vec.length

            if prev_len < 1e-12 or next_len < 1e-12:
                tangent = _safe_unit(anchors[i + 1] - anchors[i - 1])
            else:
                # Distance-weighted direction reduces kinks when anchor spacing differs.
                d1 = prev_vec / prev_len
                d2 = next_vec / next_len
                weight1 = next_len ** 0.5
                weight2 = prev_len ** 0.5
                tangent = _safe_unit(d1 * weight1 + d2 * weight2)

                if tangent.length < 1e-12:
                    tangent = _safe_unit(anchors[i + 1] - anchors[i - 1])

            # Restrained handles. This is the main difference from v0.5.2:
            # no global tension multiplier chasing perimeter length.
            left_len = prev_len * 0.32
            right_len = next_len * 0.32

            bp.handle_left = anchors[i] - tangent * left_len
            bp.handle_right = anchors[i] + tangent * right_len


def _build_bezier_chain(curve_data, original_chain):
    """Create a sparse, fair Bezier spline between two fixed corners."""
    original_len = _polyline_length(original_chain, cyclic=False)

    if len(original_chain) <= 2:
        anchors = [p.copy() for p in original_chain]
    else:
        # Sparse anchors: enough to capture broad curvature, not low-poly chatter.
        # Long/complex sides get a few more points automatically.
        desired = max(3, min(6, int(round(len(original_chain) / 9.0)) + 2))

        dense = _resample_polyline(original_chain, max(desired * 5, 15))
        dense = _laplacian_smooth_open(dense, iterations=7, strength=0.40)
        anchors = _resample_polyline(dense, desired)

        # Corners must never move.
        anchors[0] = original_chain[0].copy()
        anchors[-1] = original_chain[-1].copy()

    spline = curve_data.splines.new('BEZIER')
    spline.bezier_points.add(len(anchors) - 1)
    spline.use_cyclic_u = False
    spline.resolution_u = 32

    for bp, p in zip(spline.bezier_points, anchors):
        bp.co = p

    _set_fair_bezier_handles(spline, source_chain=original_chain)

    final_len = _sample_spline_length(spline, samples_per_segment=48)

    return spline, original_len, final_len, len(anchors)


def _remove_previous_smooth_for_source(source_obj):
    for obj in list(bpy.data.objects):
        if (
            obj.type == 'CURVE'
            and bool(obj.get("unfold_helper_smooth_generated", False))
            and obj.get("unfold_helper_smooth_source") == source_obj.name
        ):
            curve_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if curve_data and curve_data.users == 0:
                bpy.data.curves.remove(curve_data)


def _build_closed_bezier_island(curve_data, loop_points):
    """Build one closed Bezier spline for one island.

    Corners are preserved exactly. Each corner-to-corner span is reduced to a
    few anchors, then all anchors are merged into one cyclic spline so the
    curve can be filled and previewed as a closed surface.
    """
    corner_indices = _detect_corner_indices(loop_points, threshold_deg=32.0)
    corner_set = set(corner_indices)

    anchor_records = []  # (point, is_corner)
    total_original = 0.0

    for j, start_idx in enumerate(corner_indices):
        end_idx = corner_indices[(j + 1) % len(corner_indices)]
        chain = _cyclic_chain(loop_points, start_idx, end_idx)

        if len(chain) < 2:
            continue

        total_original += _polyline_length(chain, cyclic=False)

        if len(chain) <= 2:
            anchors = [chain[0].copy(), chain[-1].copy()]
        else:
            desired = max(3, min(6, int(round(len(chain) / 9.0)) + 2))
            dense = _resample_polyline(chain, max(desired * 5, 15))
            dense = _laplacian_smooth_open(dense, iterations=7, strength=0.40)
            anchors = _resample_polyline(dense, desired)
            anchors[0] = chain[0].copy()
            anchors[-1] = chain[-1].copy()

        # Add all but the final corner; the next span starts with that same point.
        for i, p in enumerate(anchors[:-1]):
            anchor_records.append((p.copy(), i == 0))

    if len(anchor_records) < 3:
        return None, 0.0, 0.0, 0, 0

    spline = curve_data.splines.new('BEZIER')
    spline.bezier_points.add(len(anchor_records) - 1)
    spline.use_cyclic_u = True
    spline.resolution_u = 32

    for bp, (p, is_corner) in zip(spline.bezier_points, anchor_records):
        bp.co = p
        bp.handle_left_type = 'FREE'
        bp.handle_right_type = 'FREE'

    bps = spline.bezier_points
    n = len(bps)

    # Fair handles on a cyclic spline. At detected corners, both handles are
    # collapsed to the corner so the point stays visually sharp.
    for i, (bp, rec) in enumerate(zip(bps, anchor_records)):
        p, is_corner = rec
        prev_p = bps[(i - 1) % n].co.copy()
        next_p = bps[(i + 1) % n].co.copy()

        if is_corner:
            bp.handle_left = bp.co.copy()
            bp.handle_right = bp.co.copy()
            continue

        prev_vec = bp.co - prev_p
        next_vec = next_p - bp.co
        prev_len = prev_vec.length
        next_len = next_vec.length

        if prev_len < 1e-12 or next_len < 1e-12:
            tangent = _safe_unit(next_p - prev_p)
        else:
            d1 = prev_vec / prev_len
            d2 = next_vec / next_len
            tangent = _safe_unit(
                d1 * (next_len ** 0.5) +
                d2 * (prev_len ** 0.5)
            )
            if tangent.length < 1e-12:
                tangent = _safe_unit(next_p - prev_p)

        bp.handle_left = bp.co - tangent * (prev_len * 0.32)
        bp.handle_right = bp.co + tangent * (next_len * 0.32)

    # Sample cyclic Bezier length.
    total_smooth = 0.0
    for i in range(n):
        a = bps[i]
        b = bps[(i + 1) % n]

        p0 = a.co.copy()
        p1 = a.handle_right.copy()
        p2 = b.handle_left.copy()
        p3 = b.co.copy()

        prev = p0
        for s in range(1, 49):
            t = s / 48.0
            cur = _bezier_point(p0, p1, p2, p3, t)
            total_smooth += (cur - prev).length
            prev = cur

    return spline, total_original, total_smooth, len(corner_indices), len(anchor_records)


def _create_smooth_curve(context, source_obj, preserve_length=True):
    loops = _boundary_loops_from_mesh(source_obj)
    if not loops:
        return None, None

    _remove_previous_smooth_for_source(source_obj)

    curve_data = bpy.data.curves.new(
        name=f"{source_obj.name}{SMOOTH_SUFFIX}_Curve",
        type='CURVE'
    )
    curve_data.dimensions = '2D'
    curve_data.resolution_u = 32
    curve_data.render_resolution_u = 32

    # Closed 2D splines can be filled, so the smooth result is visible as a surface.
    curve_data.fill_mode = 'BOTH'
    curve_data.resolution_u = 32
    curve_data.bevel_depth = 0.0

    total_original = 0.0
    total_smooth = 0.0
    total_corners = 0
    total_bezier_points = 0
    island_count = 0

    for loop_points in loops:
        if len(loop_points) < 3:
            continue

        spline, orig_len, smooth_len, corner_count, point_count = _build_closed_bezier_island(
            curve_data,
            loop_points
        )

        if spline is None:
            continue

        total_original += orig_len
        total_smooth += smooth_len
        total_corners += corner_count
        total_bezier_points += point_count
        island_count += 1

    if island_count == 0:
        bpy.data.curves.remove(curve_data)
        return None, None

    curve_obj = bpy.data.objects.new(
        f"{source_obj.name}{SMOOTH_SUFFIX}",
        curve_data
    )
    context.collection.objects.link(curve_obj)

    curve_obj["unfold_helper_smooth_generated"] = True
    curve_obj["unfold_helper_smooth_source"] = source_obj.name

    scene = context.scene
    original_mm = _bu_to_mm(scene, total_original)
    smooth_mm = _bu_to_mm(scene, total_smooth)

    diff_pct = 0.0
    if original_mm > 1e-9:
        diff_pct = ((smooth_mm - original_mm) / original_mm) * 100.0

    curve_obj["unfold_helper_original_length_mm"] = original_mm
    curve_obj["unfold_helper_smooth_length_mm"] = smooth_mm
    curve_obj["unfold_helper_length_diff_pct"] = diff_pct
    curve_obj["unfold_helper_corner_count"] = total_corners
    curve_obj["unfold_helper_bezier_point_count"] = total_bezier_points

    for o in context.selected_objects:
        o.select_set(False)
    curve_obj.select_set(True)
    context.view_layer.objects.active = curve_obj

    return curve_obj, {
        "original_mm": original_mm,
        "smooth_mm": smooth_mm,
        "diff_pct": diff_pct,
        "loop_count": island_count,
        "corner_count": total_corners,
        "bezier_point_count": total_bezier_points,
    }

def _active_smooth_object(context):
    obj = context.active_object
    if (
        obj
        and obj.type == 'CURVE'
        and bool(obj.get("unfold_helper_smooth_generated", False))
    ):
        return obj
    return None




# ------------------------------------------------------------
# Annotation -> rough seam helpers
# ------------------------------------------------------------

def _get_annotation_strokes(context):
    """Return visible strokes from the active annotation layer/frame."""
    data = getattr(context, "annotation_data", None)

    if data is None:
        scene = context.scene
        data = getattr(scene, "annotation", None)

    if data is None:
        return []

    layers = getattr(data, "layers", None)
    if not layers:
        return []

    layer = getattr(layers, "active", None)
    if layer is None and len(layers):
        layer = layers[0]

    if layer is None:
        return []

    frame = getattr(layer, "active_frame", None)
    if frame is None:
        return []

    strokes = getattr(frame, "strokes", None)
    if strokes is None:
        return []

    result = []
    for stroke in strokes:
        pts = [Vector(p.co) for p in stroke.points]
        if len(pts) >= 2:
            result.append(pts)

    return result


def _mesh_world_vertices_and_graph(mesh_obj):
    mesh = mesh_obj.data
    mw = mesh_obj.matrix_world

    world_verts = [mw @ v.co for v in mesh.vertices]

    graph = [[] for _ in mesh.vertices]
    edge_lookup = {}

    for e in mesh.edges:
        a, b = e.vertices
        pa = world_verts[a]
        pb = world_verts[b]
        length = max((pb - pa).length, 1e-9)

        graph[a].append((b, length, e.index))
        graph[b].append((a, length, e.index))

        key = (a, b) if a < b else (b, a)
        edge_lookup[key] = e.index

    return world_verts, graph, edge_lookup


def _nearest_vertex_indices(world_verts, points):
    kd = KDTree(len(world_verts))
    for i, co in enumerate(world_verts):
        kd.insert(co, i)
    kd.balance()

    out = []
    for p in points:
        _, idx, _ = kd.find(p)
        if not out or out[-1] != idx:
            out.append(idx)
    return out


def _sample_stroke_waypoints(points, max_waypoints=24):
    if len(points) <= max_waypoints:
        return points

    sampled = []
    last = len(points) - 1

    for i in range(max_waypoints):
        t = i / (max_waypoints - 1)
        idx = int(round(t * last))
        sampled.append(points[idx])

    return sampled


def _make_guide_kd(points):
    kd = KDTree(len(points))
    for i, p in enumerate(points):
        kd.insert(p, i)
    kd.balance()
    return kd


def _guided_shortest_path(
    world_verts,
    graph,
    start_idx,
    goal_idx,
    guide_kd,
    guide_strength=5.0,
    scale=0.01,
):
    """Dijkstra path biased toward the annotation stroke."""
    if start_idx == goal_idx:
        return [start_idx]

    n = len(world_verts)
    dist = [float("inf")] * n
    prev = [-1] * n
    dist[start_idx] = 0.0

    heap = [(0.0, start_idx)]

    while heap:
        cur_cost, u = heapq.heappop(heap)

        if cur_cost != dist[u]:
            continue

        if u == goal_idx:
            break

        pu = world_verts[u]

        for v, edge_len, edge_idx in graph[u]:
            pv = world_verts[v]
            mid = (pu + pv) * 0.5

            _, _, guide_dist = guide_kd.find(mid)

            # Base edge length + penalty for wandering away from drawn line.
            normalized = guide_dist / max(scale, 1e-9)
            weight = edge_len * (1.0 + guide_strength * normalized)

            nd = cur_cost + weight

            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if prev[goal_idx] == -1:
        return []

    path = [goal_idx]
    cur = goal_idx

    while cur != start_idx:
        cur = prev[cur]
        if cur == -1:
            return []
        path.append(cur)

    path.reverse()
    return path


def _average_mesh_edge_length(mesh_obj):
    mesh = mesh_obj.data
    mw = mesh_obj.matrix_world

    if not mesh.edges:
        return 0.01

    total = 0.0
    for e in mesh.edges:
        a, b = e.vertices
        total += ((mw @ mesh.vertices[a].co) - (mw @ mesh.vertices[b].co)).length

    return total / len(mesh.edges)





def _bezier2d(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        p0 * (u ** 3)
        + p1 * (3.0 * u * u * t)
        + p2 * (3.0 * u * t * t)
        + p3 * (t ** 3)
    )


def _mirror_world_point_for_target(target, world_point, mirror_x=False, mirror_z=False):
    inv = target.matrix_world.inverted()
    local = inv @ world_point
    if mirror_x:
        local.x *= -1.0
    if mirror_z:
        local.z *= -1.0
    return target.matrix_world @ local


def _symmetry_variants(scene):
    use_x = bool(getattr(scene, "unfold_helper_surface_symmetry_x", False))
    use_z = bool(getattr(scene, "unfold_helper_surface_symmetry_z", False))

    variants = [(False, False)]
    if use_x:
        variants.append((True, False))
    if use_z:
        variants.append((False, True))
    if use_x and use_z:
        variants.append((True, True))
    return variants



# ------------------------------------------------------------
# Annotation -> clean curve -> knife helpers
# ------------------------------------------------------------

def _resample_points_by_count(points, count):
    if len(points) < 2 or count <= 2:
        return [p.copy() for p in points]

    # cumulative lengths
    seg_lengths = []
    total = 0.0
    for a, b in zip(points[:-1], points[1:]):
        l = (b - a).length
        seg_lengths.append(l)
        total += l

    if total <= 1e-12:
        return [points[0].copy(), points[-1].copy()]

    targets = [total * i / (count - 1) for i in range(count)]
    out = []

    seg_i = 0
    acc = 0.0

    for t in targets:
        while seg_i < len(seg_lengths) - 1 and acc + seg_lengths[seg_i] < t:
            acc += seg_lengths[seg_i]
            seg_i += 1

        seg_len = max(seg_lengths[seg_i], 1e-12)
        local_t = (t - acc) / seg_len
        p = points[seg_i].lerp(points[seg_i + 1], local_t)
        out.append(p)

    return out


def _smooth_polyline(points, iterations=5, strength=0.45):
    if len(points) < 3:
        return [p.copy() for p in points]

    pts = [p.copy() for p in points]

    for _ in range(iterations):
        new_pts = [pts[0].copy()]

        for i in range(1, len(pts) - 1):
            avg = (pts[i - 1] + pts[i + 1]) * 0.5
            new_pts.append(pts[i].lerp(avg, strength))

        new_pts.append(pts[-1].copy())
        pts = new_pts

    return pts


def _create_curve_from_annotation_strokes(context, strokes):
    if not strokes:
        return None

    curve_data = bpy.data.curves.new("AnnotationSeamGuide_Curve", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.resolution_u = 24
    curve_data.render_resolution_u = 24
    curve_data.bevel_depth = 0.0

    total_splines = 0

    for stroke in strokes:
        if len(stroke) < 2:
            continue

        # Reduce hand jitter but keep broad intent.
        desired = max(4, min(24, int(round(len(stroke) / 3.0))))
        sampled = _resample_points_by_count(stroke, desired)
        smoothed = _smooth_polyline(sampled, iterations=5, strength=0.42)

        spline = curve_data.splines.new('BEZIER')
        spline.bezier_points.add(len(smoothed) - 1)
        spline.use_cyclic_u = False
        spline.resolution_u = 24

        for i, (bp, p) in enumerate(zip(spline.bezier_points, smoothed)):
            bp.co = p
            bp.handle_left_type = 'AUTO'
            bp.handle_right_type = 'AUTO'

        total_splines += 1

    if total_splines == 0:
        bpy.data.curves.remove(curve_data)
        return None

    curve_obj = bpy.data.objects.new("AnnotationSeamGuide", curve_data)
    context.collection.objects.link(curve_obj)
    curve_obj["unfold_helper_annotation_guide"] = True

    return curve_obj


def _find_annotation_guide():
    guides = [
        o for o in bpy.data.objects
        if o.type == 'CURVE' and bool(o.get("unfold_helper_annotation_guide", False))
    ]
    if not guides:
        return None
    return guides[-1]



# ------------------------------------------------------------
# Experimental free-seam helpers
# ------------------------------------------------------------

def _selected_mesh_and_curve(context):
    meshes = [o for o in context.selected_objects if o.type == 'MESH']
    curves = [o for o in context.selected_objects if o.type == 'CURVE']

    if len(meshes) != 1 or len(curves) != 1:
        return None, None

    return meshes[0], curves[0]


def _edge_key_from_edge(mesh, edge):
    a, b = edge.vertices
    return (a, b) if a < b else (b, a)


def _duplicate_curve_as_mesh(context, curve_obj):
    """Duplicate a curve, convert duplicate to mesh, return cutter object."""
    bpy.ops.object.mode_set(mode='OBJECT') if context.active_object and context.active_object.mode != 'OBJECT' else None

    for o in context.selected_objects:
        o.select_set(False)

    curve_obj.select_set(True)
    context.view_layer.objects.active = curve_obj

    bpy.ops.object.duplicate()
    cutter = context.active_object
    cutter.name = f"{curve_obj.name}_SEAM_CUTTER_TMP"

    # Convert evaluated curve (including Shrinkwrap) to a real mesh.
    bpy.ops.object.convert(target='MESH')
    cutter = context.active_object

    return cutter


def _delete_object_and_data(obj):
    if obj is None:
        return

    data = obj.data
    obj_type = obj.type

    bpy.data.objects.remove(obj, do_unlink=True)

    if data and data.users == 0:
        if obj_type == 'MESH':
            bpy.data.meshes.remove(data)
        elif obj_type == 'CURVE':
            bpy.data.curves.remove(data)



# ------------------------------------------------------------
# Operators
# ------------------------------------------------------------


class TSUNFOLD_OT_surface_seam_pen(bpy.types.Operator):
    bl_idname = "truescale_unfold.surface_seam_pen"
    bl_label = "表面にカーブを描く"
    bl_description = "Illustratorのペンツール風に、クリックでアンカー・ドラッグでハンドルを作りながら表面カーブを描きます"
    bl_options = {'REGISTER', 'UNDO'}

    _handle = None
    _target_name = ""
    _region = None
    _rv3d = None

    _anchors = None          # [{"screen": Vector2, "drag": Vector2}]
    _finished_strokes = None # world-space sampled strokes
    _mouse_down = False
    _press_screen = None
    _hover_screen = None

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == 'VIEW_3D'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def _target(self):
        return bpy.data.objects.get(self._target_name)

    def _ray_hit_target(self, context, screen_xy):
        target = self._target()
        if target is None:
            return None

        origin = view3d_utils.region_2d_to_origin_3d(
            self._region, self._rv3d, screen_xy
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            self._region, self._rv3d, screen_xy
        ).normalized()

        depsgraph = context.evaluated_depsgraph_get()
        hit, location, normal, face_index, hit_obj, matrix = context.scene.ray_cast(
            depsgraph, origin, direction
        )

        if not hit or hit_obj is None:
            return None

        original = hit_obj.original if hasattr(hit_obj, "original") else hit_obj
        if original != target:
            return None

        # Front-facing visible surface only.
        if normal.dot(direction) >= -1e-5:
            return None

        return Vector(location)

    def _segment_screen_samples(self, a, b, steps=32):
        p0 = a["screen"]
        p1 = a["screen"] + a["drag"]
        p3 = b["screen"]
        p2 = b["screen"] - b["drag"]

        return [
            _bezier2d(p0, p1, p2, p3, i / steps)
            for i in range(steps + 1)
        ]

    def _screen_segment_to_world_strokes(self, context, a, b):
        strokes = []
        current = []

        for sp in self._segment_screen_samples(a, b, steps=36):
            hit = self._ray_hit_target(context, sp)

            if hit is not None:
                if not current or (hit - current[-1]).length > 1e-7:
                    current.append(hit)
            else:
                if len(current) >= 2:
                    strokes.append(current)
                current = []

        if len(current) >= 2:
            strokes.append(current)

        return strokes

    def _current_preview_world(self, context):
        strokes = list(self._finished_strokes)

        if len(self._anchors) >= 2:
            for i in range(len(self._anchors) - 1):
                strokes.extend(
                    self._screen_segment_to_world_strokes(
                        context,
                        self._anchors[i],
                        self._anchors[i + 1],
                    )
                )

        # Preview from last anchor to current mouse as a straight/no-handle endpoint.
        if self._anchors and self._hover_screen is not None and not self._mouse_down:
            temp = {
                "screen": self._hover_screen.copy(),
                "drag": Vector((0.0, 0.0)),
            }
            strokes.extend(
                self._screen_segment_to_world_strokes(
                    context,
                    self._anchors[-1],
                    temp,
                )
            )

        return strokes

    def _commit_anchor_path(self, context):
        if len(self._anchors) < 2:
            self._anchors = []
            return

        for i in range(len(self._anchors) - 1):
            self._finished_strokes.extend(
                self._screen_segment_to_world_strokes(
                    context,
                    self._anchors[i],
                    self._anchors[i + 1],
                )
            )

        self._anchors = []

    def _undo(self):
        if self._anchors:
            self._anchors.pop()
            return True
        if self._finished_strokes:
            self._finished_strokes.pop()
            return True
        return False

    def _clear(self):
        self._anchors = []
        self._finished_strokes = []

    def _draw_callback(self):
        import gpu
        from gpu_extras.batch import batch_for_shader

        target = self._target()
        if target is None:
            return

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')

        # Important: respect viewport depth so the guide disappears behind
        # the target instead of x-ray showing through the back side.
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.line_width_set(3.0)

        try:
            # Draw committed + live curve preview.
            context = bpy.context
            strokes = self._current_preview_world(context)

            variants = _symmetry_variants(context.scene)

            for stroke in strokes:
                if len(stroke) < 2:
                    continue

                for mirror_x, mirror_z in variants:
                    pts = [
                        _mirror_world_point_for_target(
                            target, p, mirror_x=mirror_x, mirror_z=mirror_z
                        )
                        for p in stroke
                    ]

                    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": pts})
                    shader.bind()
                    shader.uniform_float("color", (0.10, 0.62, 1.0, 1.0))
                    batch.draw(shader)

            # Draw anchor handle visualization in screen-independent world form
            # by raycasting anchor and handle endpoints.
            gpu.state.line_width_set(1.5)

            handle_lines = []
            for a in self._anchors:
                anchor_hit = self._ray_hit_target(context, a["screen"])
                if anchor_hit is None:
                    continue

                for hs in (a["screen"] + a["drag"], a["screen"] - a["drag"]):
                    handle_hit = self._ray_hit_target(context, hs)
                    if handle_hit is not None:
                        handle_lines.extend([anchor_hit, handle_hit])

            if handle_lines:
                batch = batch_for_shader(shader, 'LINES', {"pos": handle_lines})
                shader.bind()
                shader.uniform_float("color", (1.0, 0.55, 0.08, 1.0))
                batch.draw(shader)

        finally:
            gpu.state.line_width_set(1.0)
            gpu.state.depth_test_set('NONE')

    def _remove_draw_handler(self):
        if self._handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except Exception:
                pass
            self._handle = None

    def _make_curve(self, context):
        self._commit_anchor_path(context)

        strokes = [s for s in self._finished_strokes if len(s) >= 2]
        if not strokes:
            return None

        target = self._target()
        if target is None:
            return None

        # Apply requested symmetry as real guide splines.
        all_strokes = []
        for stroke in strokes:
            for mirror_x, mirror_z in _symmetry_variants(context.scene):
                all_strokes.append([
                    _mirror_world_point_for_target(
                        target, p,
                        mirror_x=mirror_x,
                        mirror_z=mirror_z
                    )
                    for p in stroke
                ])

        for obj in list(bpy.data.objects):
            if obj.type == 'CURVE' and bool(obj.get("unfold_helper_annotation_guide", False)):
                _delete_object_and_data(obj)

        guide = _create_curve_from_annotation_strokes(context, all_strokes)
        if guide is None:
            return None

        # Pin all resulting curves back to the target surface.
        mod = guide.modifiers.new(
            "展開図ヘルパー_サーフェスペン吸着",
            'SHRINKWRAP'
        )
        mod.target = target
        mod.wrap_method = 'NEAREST_SURFACEPOINT'
        mod.wrap_mode = 'ON_SURFACE'
        mod.offset = 0.0002

        guide["unfold_helper_surface_pen_target"] = target.name

        for o in context.selected_objects:
            o.select_set(False)
        guide.select_set(True)
        target.select_set(True)
        context.view_layer.objects.active = guide

        return guide

    def invoke(self, context, event):
        target = context.active_object
        self._target_name = target.name
        self._anchors = []
        self._finished_strokes = []
        self._mouse_down = False
        self._press_screen = None
        self._hover_screen = Vector((event.mouse_region_x, event.mouse_region_y))
        self._region = context.region
        self._rv3d = context.space_data.region_3d

        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback,
            (),
            'WINDOW',
            'POST_VIEW',
        )

        context.window_manager.modal_handler_add(self)

        self.report(
            {'INFO'},
            "クリックでアンカー / ドラッグでハンドル / Enter確定 / Ctrl+Z戻す / Del全消し"
        )

        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if event.type == 'ESC':
            self._remove_draw_handler()
            return {'CANCELLED'}

        if event.type == 'Z' and event.value == 'PRESS' and event.ctrl:
            self._undo()
            return {'RUNNING_MODAL'}

        if event.type in {'DEL', 'BACK_SPACE'} and event.value == 'PRESS':
            self._clear()
            return {'RUNNING_MODAL'}

        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._remove_draw_handler()
            guide = self._make_curve(context)

            if guide is None:
                self.report({'WARNING'}, "有効なカーブがありませんでした")
                return {'CANCELLED'}

            self.report({'INFO'}, "サーフェスカーブを確定しました")
            return {'FINISHED'}

        if event.type == 'MOUSEMOVE':
            self._hover_screen = Vector(
                (event.mouse_region_x, event.mouse_region_y)
            )

            if self._mouse_down and self._press_screen is not None:
                # Illustrator-style: drag vector becomes the tangent handle.
                drag = self._hover_screen - self._press_screen

                if self._anchors:
                    self._anchors[-1]["drag"] = drag

            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE':
            screen = Vector((event.mouse_region_x, event.mouse_region_y))

            if event.value == 'PRESS':
                # Only accept anchors whose screen position currently hits
                # the chosen visible front surface.
                if self._ray_hit_target(context, screen) is None:
                    return {'RUNNING_MODAL'}

                self._mouse_down = True
                self._press_screen = screen.copy()
                self._anchors.append({
                    "screen": screen.copy(),
                    "drag": Vector((0.0, 0.0)),
                })
                return {'RUNNING_MODAL'}

            if event.value == 'RELEASE':
                self._mouse_down = False
                self._press_screen = None
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


class TSUNFOLD_OT_annotation_to_curve_guide(bpy.types.Operator):
    bl_idname = "truescale_unfold.annotation_to_curve_guide"
    bl_label = "アノテート → 綺麗なカーブ"
    bl_description = "アノテート線を少し整えてBezierカーブ化します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        strokes = _get_annotation_strokes(context)

        if not strokes:
            self.report({'ERROR'}, "アクティブなアノテート線が見つかりません")
            return {'CANCELLED'}

        # Remove previous generated guide(s).
        for obj in list(bpy.data.objects):
            if obj.type == 'CURVE' and bool(obj.get("unfold_helper_annotation_guide", False)):
                _delete_object_and_data(obj)

        guide = _create_curve_from_annotation_strokes(context, strokes)

        if guide is None:
            self.report({'ERROR'}, "カーブガイドを生成できませんでした")
            return {'CANCELLED'}

        for o in context.selected_objects:
            o.select_set(False)
        guide.select_set(True)
        context.view_layer.objects.active = guide

        self.report({'INFO'}, "アノテートをBezierカーブ化しました")
        return {'FINISHED'}


class TSUNFOLD_OT_annotation_curve_snap(bpy.types.Operator):
    bl_idname = "truescale_unfold.annotation_curve_snap"
    bl_label = "カーブを表面に吸着"
    bl_description = "生成したカーブガイドを選択Mesh表面へShrinkwrapします"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False
        return obj.type == 'MESH' or obj.type == 'CURVE'

    def execute(self, context):
        guide = _find_annotation_guide()
        if guide is None:
            self.report({'ERROR'}, "アノテートカーブがありません")
            return {'CANCELLED'}

        mesh_obj = None
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                mesh_obj = obj
                break

        if mesh_obj is None and context.active_object and context.active_object.type == 'MESH':
            mesh_obj = context.active_object

        if mesh_obj is None:
            self.report({'ERROR'}, "吸着先のMeshを選択してください")
            return {'CANCELLED'}

        # Remove previous helper shrinkwrap.
        for mod in list(guide.modifiers):
            if mod.type == 'SHRINKWRAP' and mod.name.startswith("展開図ヘルパー_"):
                guide.modifiers.remove(mod)

        mod = guide.modifiers.new("展開図ヘルパー_アノテート吸着", 'SHRINKWRAP')
        mod.target = mesh_obj
        mod.wrap_method = 'NEAREST_SURFACEPOINT'
        mod.wrap_mode = 'ON_SURFACE'
        mod.offset = 0.0002

        for o in context.selected_objects:
            o.select_set(False)
        guide.select_set(True)
        mesh_obj.select_set(True)
        context.view_layer.objects.active = guide

        self.report({'INFO'}, "カーブガイドをMesh表面へ吸着しました")
        return {'FINISHED'}


class TSUNFOLD_OT_annotation_curve_knife_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.annotation_curve_knife_seam"
    bl_label = "描いた線で切ってシーム化"
    bl_description = "サーフェスペンのCurveをそのままKnife Projectし、切断エッジをシーム化します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == 'VIEW_3D'
            and _find_annotation_guide() is not None
        )

    def execute(self, context):
        guide = _find_annotation_guide()
        if guide is None:
            self.report({'ERROR'}, "サーフェスペンのガイドがありません")
            return {'CANCELLED'}

        # Prefer the exact Mesh remembered by the surface pen.
        mesh_obj = None
        target_name = guide.get("unfold_helper_surface_pen_target", "")
        if target_name:
            candidate = bpy.data.objects.get(target_name)
            if candidate and candidate.type == 'MESH':
                mesh_obj = candidate

        # Fallback to any selected Mesh.
        if mesh_obj is None:
            for obj in context.selected_objects:
                if obj.type == 'MESH':
                    mesh_obj = obj
                    break

        if mesh_obj is None:
            self.report({'ERROR'}, "切断対象のMeshが見つかりません")
            return {'CANCELLED'}

        try:
            if context.active_object and context.active_object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

            # IMPORTANT:
            # Keep the cutter as a Curve object. If we convert it to Mesh,
            # Blender 5.2 may put both selected Mesh objects into multi-object
            # Edit Mode, leaving Knife Project with no external cutter.
            for o in context.selected_objects:
                o.select_set(False)

            guide.hide_set(False)
            guide.hide_viewport = False
            guide.select_set(True)

            mesh_obj.hide_set(False)
            mesh_obj.hide_viewport = False
            mesh_obj.select_set(True)

            context.view_layer.objects.active = mesh_obj

            # Only the Mesh target enters Edit Mode; Curve remains an external
            # selected projection object.
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='DESELECT')

            bpy.ops.mesh.knife_project(cut_through=False)

            # Fresh Knife Project geometry is selected. Mark it directly.
            bpy.ops.mesh.mark_seam(clear=False)
            context.tool_settings.mesh_select_mode = (False, True, False)

            # Verify result.
            bpy.ops.object.mode_set(mode='OBJECT')
            selected_cut_edges = [
                e for e in mesh_obj.data.edges
                if e.select and e.use_seam
            ]

            if not selected_cut_edges:
                self.report(
                    {'WARNING'},
                    "切断線は作られましたがシーム対象を確認できませんでした。ビュー角度を変えて再試行してください"
                )
                return {'CANCELLED'}

            bpy.ops.object.mode_set(mode='EDIT')

            self.report(
                {'INFO'},
                f"切断エッジ {len(selected_cut_edges)} 本をシーム化しました"
            )
            return {'FINISHED'}

        except RuntimeError as exc:
            try:
                if context.active_object and context.active_object.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass

            self.report({'ERROR'}, f"切断処理に失敗しました: {exc}")
            return {'CANCELLED'}

        finally:
            # Preserve the guide. Just restore target as the active object.
            if mesh_obj and mesh_obj.name in bpy.data.objects:
                try:
                    if context.active_object and context.active_object.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                except Exception:
                    pass

                for o in context.selected_objects:
                    o.select_set(False)

                mesh_obj.select_set(True)
                context.view_layer.objects.active = mesh_obj


class TSUNFOLD_OT_delete_annotation_curve_guide(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_annotation_curve_guide"
    bl_label = "カーブガイド削除"
    bl_description = "生成したアノテートカーブガイドを削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in list(bpy.data.objects):
            if obj.type == 'CURVE' and bool(obj.get("unfold_helper_annotation_guide", False)):
                _delete_object_and_data(obj)
                count += 1

        self.report({'INFO'}, f"カーブガイドを {count} 個削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_prepare_annotation(bpy.types.Operator):
    bl_idname = "truescale_unfold.prepare_annotation"
    bl_label = "表面にアノテートを描く"
    bl_description = "3Dアノテートの配置をSurfaceにし、アノテートツールを起動します"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        context.scene.tool_settings.annotation_stroke_placement_view3d = 'SURFACE'

        try:
            bpy.ops.wm.tool_set_by_id(name="builtin.annotate")
        except Exception:
            pass

        self.report({'INFO'}, "アノテート配置を「Surface」にしました。メッシュ表面へ線を描いてください")
        return {'FINISHED'}


class TSUNFOLD_OT_annotation_to_rough_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.annotation_to_rough_seam"
    bl_label = "アノテート → ざっくりシーム"
    bl_description = "表面アノテートの近くを通る既存エッジ列を自動でシーム化します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        mesh_obj = context.active_object

        if mesh_obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        strokes = _get_annotation_strokes(context)

        if not strokes:
            self.report({'ERROR'}, "アクティブなアノテート線が見つかりません")
            return {'CANCELLED'}

        world_verts, graph, edge_lookup = _mesh_world_vertices_and_graph(mesh_obj)

        if not world_verts or not mesh_obj.data.edges:
            self.report({'ERROR'}, "Meshに十分な頂点・エッジがありません")
            return {'CANCELLED'}

        avg_edge = _average_mesh_edge_length(mesh_obj)
        seam_edge_indices = set()
        used_strokes = 0

        for stroke_points in strokes:
            if len(stroke_points) < 2:
                continue

            # Keep guide detail for path bias, but use fewer waypoints for routing.
            guide_points = stroke_points
            waypoints = _sample_stroke_waypoints(stroke_points, max_waypoints=24)

            guide_kd = _make_guide_kd(guide_points)
            vertex_waypoints = _nearest_vertex_indices(world_verts, waypoints)

            if len(vertex_waypoints) < 2:
                continue

            stroke_added = False

            for a, b in zip(vertex_waypoints[:-1], vertex_waypoints[1:]):
                path = _guided_shortest_path(
                    world_verts,
                    graph,
                    a,
                    b,
                    guide_kd,
                    guide_strength=5.0,
                    scale=max(avg_edge * 2.5, 1e-6),
                )

                if len(path) < 2:
                    continue

                for u, v in zip(path[:-1], path[1:]):
                    key = (u, v) if u < v else (v, u)
                    edge_idx = edge_lookup.get(key)
                    if edge_idx is not None:
                        seam_edge_indices.add(edge_idx)
                        stroke_added = True

            if stroke_added:
                used_strokes += 1

        if not seam_edge_indices:
            self.report(
                {'WARNING'},
                "アノテート付近のエッジ経路を作れませんでした。Surface配置でメッシュ上に描いてください"
            )
            return {'CANCELLED'}

        mesh = mesh_obj.data

        for e in mesh.edges:
            e.select = False

        for idx in seam_edge_indices:
            e = mesh.edges[idx]
            e.use_seam = True
            e.select = True

        mesh.update()

        # Leave new seam visible in Edge Select Edit Mode.
        mesh_obj.select_set(True)
        context.view_layer.objects.active = mesh_obj
        bpy.ops.object.mode_set(mode='EDIT')
        context.tool_settings.mesh_select_mode = (False, True, False)

        self.report(
            {'INFO'},
            f"アノテート {used_strokes} 本から、既存エッジ {len(seam_edge_indices)} 本をシーム化しました"
        )
        return {'FINISHED'}


class TSUNFOLD_OT_snap_curve_to_surface(bpy.types.Operator):
    bl_idname = "truescale_unfold.snap_curve_to_surface"
    bl_label = "カーブを表面に吸着"
    bl_description = "選択したCurveを選択Meshの表面へShrinkwrapで吸着します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mesh_obj, curve_obj = _selected_mesh_and_curve(context)
        return mesh_obj is not None and curve_obj is not None

    def execute(self, context):
        mesh_obj, curve_obj = _selected_mesh_and_curve(context)

        # Remove old helper shrinkwrap modifiers created by this add-on.
        for mod in list(curve_obj.modifiers):
            if mod.type == 'SHRINKWRAP' and mod.name.startswith("展開図ヘルパー_"):
                curve_obj.modifiers.remove(mod)

        mod = curve_obj.modifiers.new(
            name="展開図ヘルパー_表面吸着",
            type='SHRINKWRAP'
        )
        mod.target = mesh_obj
        mod.wrap_method = 'NEAREST_SURFACEPOINT'
        mod.wrap_mode = 'ON_SURFACE'
        mod.offset = 0.0002

        # Give the curve enough evaluated resolution to follow the surface.
        curve_obj.data.resolution_u = max(curve_obj.data.resolution_u, 24)
        curve_obj.data.render_resolution_u = max(curve_obj.data.render_resolution_u, 24)

        for o in context.selected_objects:
            o.select_set(False)
        curve_obj.select_set(True)
        mesh_obj.select_set(True)
        context.view_layer.objects.active = curve_obj

        self.report({'INFO'}, "CurveをMesh表面へ吸着しました")
        return {'FINISHED'}


class TSUNFOLD_OT_curve_to_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.curve_to_seam"
    bl_label = "カーブからシーム確定"
    bl_description = "選択Curveを現在の3Dビュー方向からMeshへKnife Projectし、新規エッジをシーム化します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mesh_obj, curve_obj = _selected_mesh_and_curve(context)
        return (
            mesh_obj is not None
            and curve_obj is not None
            and context.area is not None
            and context.area.type == 'VIEW_3D'
        )

    def execute(self, context):
        mesh_obj, curve_obj = _selected_mesh_and_curve(context)

        if mesh_obj is None or curve_obj is None:
            self.report({'ERROR'}, "Meshを1個、Curveを1個だけ選択してください")
            return {'CANCELLED'}

        # Snapshot pre-cut topology using vertex-pair keys.
        mesh_data = mesh_obj.data
        before_keys = {_edge_key_from_edge(mesh_data, e) for e in mesh_data.edges}

        cutter = None

        try:
            # Create temporary evaluated mesh cutter from the curve.
            cutter = _duplicate_curve_as_mesh(context, curve_obj)

            # Select cutter + target, make target active, then enter Edit Mode.
            for o in context.selected_objects:
                o.select_set(False)

            cutter.select_set(True)
            mesh_obj.select_set(True)
            context.view_layer.objects.active = mesh_obj

            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='DESELECT')

            # Knife Project uses the current 3D view direction.
            bpy.ops.mesh.knife_project(cut_through=False)

            bpy.ops.object.mode_set(mode='OBJECT')
            mesh_data.update()

            # New topology edges are the most reliable candidates for the cut.
            new_edges = []
            for edge in mesh_data.edges:
                key = _edge_key_from_edge(mesh_data, edge)
                if key not in before_keys:
                    new_edges.append(edge)

            if not new_edges:
                self.report(
                    {'WARNING'},
                    "切断エッジを検出できませんでした。CurveがMesh上にあり、投影方向が見やすい角度か確認してください"
                )
                return {'CANCELLED'}

            # Mark only newly created edges as seams.
            for edge in mesh_data.edges:
                edge.select = False

            for edge in new_edges:
                edge.use_seam = True
                edge.select = True

            mesh_data.update()

            # Leave the target mesh selected in Edit Mode so the seam is immediately visible.
            for o in context.selected_objects:
                o.select_set(False)
            mesh_obj.select_set(True)
            context.view_layer.objects.active = mesh_obj
            bpy.ops.object.mode_set(mode='EDIT')
            context.tool_settings.mesh_select_mode = (False, True, False)

            self.report({'INFO'}, f"新規エッジ {len(new_edges)} 本をシーム化しました")
            return {'FINISHED'}

        except RuntimeError as exc:
            try:
                if mesh_obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass

            self.report({'ERROR'}, f"自由シーム処理に失敗しました: {exc}")
            return {'CANCELLED'}

        finally:
            # Temporary cutter must never remain in the user's Outliner.
            if cutter and cutter.name in bpy.data.objects:
                try:
                    if context.active_object and context.active_object.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                except Exception:
                    pass
                _delete_object_and_data(cutter)

                # Restore target selection.
                if mesh_obj and mesh_obj.name in bpy.data.objects:
                    for o in context.selected_objects:
                        o.select_set(False)
                    mesh_obj.select_set(True)
                    context.view_layer.objects.active = mesh_obj


class TSUNFOLD_OT_delete_seam_guides(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_seam_guides"
    bl_label = "選択カーブを削除"
    bl_description = "選択中のCurveガイドだけを削除します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'CURVE' for o in context.selected_objects)

    def execute(self, context):
        curves = [o for o in list(context.selected_objects) if o.type == 'CURVE']

        for obj in curves:
            _delete_object_and_data(obj)

        self.report({'INFO'}, f"Curveガイドを {len(curves)} 個削除しました")
        return {'FINISHED'}





def _edge_midpoint_local(mesh, edge):
    v1 = mesh.vertices[edge.vertices[0]].co
    v2 = mesh.vertices[edge.vertices[1]].co
    return (v1 + v2) * 0.5


def _edge_direction_local(mesh, edge):
    v1 = mesh.vertices[edge.vertices[0]].co
    v2 = mesh.vertices[edge.vertices[1]].co
    d = v2 - v1
    if d.length > 1e-12:
        d.normalize()
    return d


def _mirrored_point(p, mirror_x=False, mirror_z=False):
    q = p.copy()
    if mirror_x:
        q.x *= -1.0
    if mirror_z:
        q.z *= -1.0
    return q


def _mirrored_direction(d, mirror_x=False, mirror_z=False):
    q = d.copy()
    if mirror_x:
        q.x *= -1.0
    if mirror_z:
        q.z *= -1.0
    if q.length > 1e-12:
        q.normalize()
    return q


def _find_mirrored_edge(mesh, source_edge, mirror_x=False, mirror_z=False):
    if not mirror_x and not mirror_z:
        return source_edge

    target_mid = _mirrored_point(
        _edge_midpoint_local(mesh, source_edge),
        mirror_x=mirror_x,
        mirror_z=mirror_z
    )
    target_dir = _mirrored_direction(
        _edge_direction_local(mesh, source_edge),
        mirror_x=mirror_x,
        mirror_z=mirror_z
    )

    # Tolerance derived from average edge length, so it works across model scales.
    avg_len = 0.0
    if mesh.edges:
        for e in mesh.edges:
            v1 = mesh.vertices[e.vertices[0]].co
            v2 = mesh.vertices[e.vertices[1]].co
            avg_len += (v2 - v1).length
        avg_len /= max(len(mesh.edges), 1)

    max_dist = max(avg_len * 0.45, 1e-6)

    best = None
    best_score = float("inf")

    for edge in mesh.edges:
        mid = _edge_midpoint_local(mesh, edge)
        dist = (mid - target_mid).length
        if dist > max_dist:
            continue

        d = _edge_direction_local(mesh, edge)
        # Direction can be reversed, so compare absolute dot product.
        align_penalty = 1.0 - abs(d.dot(target_dir)) if d.length > 0 and target_dir.length > 0 else 0.0

        score = dist + align_penalty * max(avg_len, 1e-6)

        if score < best_score:
            best_score = score
            best = edge

    return best



def _mirrored_point_xyz(p, mirror_x=False, mirror_y=False, mirror_z=False):
    q = p.copy()
    if mirror_x:
        q.x *= -1.0
    if mirror_y:
        q.y *= -1.0
    if mirror_z:
        q.z *= -1.0
    return q


def _find_strict_mirrored_edge_xyz(
    mesh,
    source_edge,
    mirror_x=False,
    mirror_y=False,
    mirror_z=False,
):
    if not mirror_x and not mirror_y and not mirror_z:
        return source_edge

    a_idx, b_idx = source_edge.vertices
    a = mesh.vertices[a_idx].co.copy()
    b = mesh.vertices[b_idx].co.copy()

    ma = _mirrored_point_xyz(
        a,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        mirror_z=mirror_z,
    )
    mb = _mirrored_point_xyz(
        b,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        mirror_z=mirror_z,
    )

    # Strict matching avoids the old "nearest unrelated edge" problem.
    avg_len = 0.0
    if mesh.edges:
        for e in mesh.edges:
            p1 = mesh.vertices[e.vertices[0]].co
            p2 = mesh.vertices[e.vertices[1]].co
            avg_len += (p2 - p1).length
        avg_len /= max(len(mesh.edges), 1)

    tol = max(avg_len * 0.08, 1e-6)

    nearest_a = None
    nearest_b = None
    best_a = float("inf")
    best_b = float("inf")

    for v in mesh.vertices:
        da = (v.co - ma).length
        if da < best_a:
            best_a = da
            nearest_a = v.index

        db = (v.co - mb).length
        if db < best_b:
            best_b = db
            nearest_b = v.index

    if (
        nearest_a is None
        or nearest_b is None
        or best_a > tol
        or best_b > tol
        or nearest_a == nearest_b
    ):
        return None

    wanted = {nearest_a, nearest_b}
    for e in mesh.edges:
        if set(e.vertices) == wanted:
            return e

    return None


def _symmetry_variants_xyz(scene):
    use_x = bool(getattr(scene, "unfold_helper_seam_symmetry_x", False))
    use_y = bool(getattr(scene, "unfold_helper_seam_symmetry_y", False))
    use_z = bool(getattr(scene, "unfold_helper_seam_symmetry_z", False))

    variants = []
    for mx in ([False, True] if use_x else [False]):
        for my in ([False, True] if use_y else [False]):
            for mz in ([False, True] if use_z else [False]):
                variants.append((mx, my, mz))

    return variants


def _sync_blender_mesh_symmetry(context, obj=None):
    """Drive Blender's native Edit Mode symmetry from the helper XYZ toggles."""
    if obj is None:
        obj = context.active_object

    if obj is None or obj.type != 'MESH':
        return

    mesh = obj.data
    mesh.use_mirror_x = bool(
        getattr(context.scene, "unfold_helper_seam_symmetry_x", False)
    )
    mesh.use_mirror_y = bool(
        getattr(context.scene, "unfold_helper_seam_symmetry_y", False)
    )
    mesh.use_mirror_z = bool(
        getattr(context.scene, "unfold_helper_seam_symmetry_z", False)
    )


def _apply_selected_edges_seam_strict_symmetry(context, clear=False):
    """Apply seam/clear to selected edges and exact XYZ mirrored mates.

    Selection is preserved. Knife topology mirroring itself is delegated to
    Blender's native Mesh.use_mirror_x/y/z settings.
    """
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return 0

    _sync_blender_mesh_symmetry(context, obj)

    if obj.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')

    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = obj.data

    selected = [e for e in mesh.edges if e.select]
    if not selected:
        bpy.ops.object.mode_set(mode='EDIT')
        return 0

    targets = set(e.index for e in selected)
    variants = _symmetry_variants_xyz(context.scene)

    for edge in selected:
        for mx, my, mz in variants:
            if not mx and not my and not mz:
                continue

            mirrored = _find_strict_mirrored_edge_xyz(
                mesh,
                edge,
                mirror_x=mx,
                mirror_y=my,
                mirror_z=mz,
            )
            if mirrored is not None:
                targets.add(mirrored.index)

    for edge_idx in targets:
        e = mesh.edges[edge_idx]
        e.use_seam = not clear
        e.select = True

    mesh.update()
    bpy.ops.object.mode_set(mode='EDIT')
    context.tool_settings.mesh_select_mode = (False, True, False)

    return len(targets)





def _pattern_sync_live_seams(source_obj):
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
            pass

    try:
        source_obj.update_from_editmode()
    except Exception:
        pass

    try:
        source_obj.data.update()
    except Exception:
        pass


def _pattern_seam_trails(source_obj):
    """Return ordered seam trails as lists of (edge_index, from_v, to_v).

    A trail is a continuous seam line. Degree != 2 vertices break trails;
    closed seam loops are handled separately.
    """
    if source_obj is None or source_obj.type != 'MESH':
        return []

    _pattern_sync_live_seams(source_obj)

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


def _pattern_trail_mark_positions(context, source_obj, trail, divisions):
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



def _pattern_auto_notch_division_value(scene):
    """Safely normalize auto-notch division selector to 2 / 3 / 4."""
    raw = getattr(
        scene,
        "pattern_helper_auto_notch_divisions",
        "3",
    )

    try:
        value = int(str(raw).strip())
    except Exception:
        value = 3

    if value not in {2, 3, 4}:
        value = 3

    return value


def _pattern_refresh_auto_notches(context, source_obj):
    """Rebuild only automatically generated notch annotations."""
    if source_obj is None or source_obj.type != 'MESH':
        return 0

    _pattern_sync_live_seams(source_obj)

    mode = str(getattr(context.scene, "pattern_helper_notch_mode", "AUTO"))
    items = _pattern_get_annotations(source_obj)

    # Preserve manual notches and every other annotation type.
    items = [
        item
        for item in items
        if not (
            item.get("type") == "notch_edge"
            and bool(item.get("auto", False))
        )
    ]

    if mode != "AUTO":
        _pattern_set_annotations(source_obj, items)
        return 0

    divisions = _pattern_auto_notch_division_value(
        context.scene
    )

    color = _pattern_color_value(
        context.scene.pattern_helper_notch_color
    )

    count = 0

    for trail in _pattern_seam_trails(source_obj):
        for edge_index, fraction in _pattern_trail_mark_positions(
            context,
            source_obj,
            trail,
            divisions,
        ):
            items.append({
                "type": "notch_edge",
                "edge": int(edge_index),
                "t": float(fraction),
                "color": color,
                "auto": True,
            })
            count += 1

    _pattern_set_annotations(source_obj, items)
    return count


def _pattern_remove_auto_notches(source_obj):
    if source_obj is None:
        return 0

    items = _pattern_get_annotations(source_obj)
    before = len(items)

    items = [
        item
        for item in items
        if not (
            item.get("type") == "notch_edge"
            and bool(item.get("auto", False))
        )
    ]

    _pattern_set_annotations(source_obj, items)
    return before - len(items)


def _pattern_delete_generated_for_source(context, source_obj):
    """Delete stale generated pattern/layout objects before regeneration."""
    if source_obj is None:
        return 0

    old_meshes = [
        obj
        for obj in list(bpy.data.objects)
        if (
            obj.type == 'MESH'
            and bool(obj.get("unfold_helper_generated", False))
            and obj.get("unfold_helper_source", "") == source_obj.name
        )
    ]

    old_names = {obj.name for obj in old_meshes}

    old_curves = [
        obj
        for obj in list(bpy.data.objects)
        if (
            obj.type == 'CURVE'
            and bool(obj.get("unfold_helper_smooth_generated", False))
            and (
                obj.get("unfold_helper_smooth_source", "") in old_names
                or obj.get("unfold_helper_source", "") == source_obj.name
            )
        )
    ]

    total = 0

    for obj in old_curves:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.curves.remove(data)
        total += 1

    for obj in old_meshes:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
        total += 1

    context.scene.pattern_helper_pattern_preview = False
    context.scene.unfold_helper_preview = False
    context.scene["pattern_helper_preview_prev_active"] = ""
    context.scene["unfold_helper_display_mode"] = "POLY"
    _pattern_invalidate_layout_cache()

    return total


class TSUNFOLD_OT_refresh_auto_notches(bpy.types.Operator):
    bl_idname = "truescale_unfold.refresh_auto_notches"
    bl_label = "オート合印を更新"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_source_object_from_context(context)
        if source is None:
            self.report({'WARNING'}, "元の3Dモデルを選択してください")
            return {'CANCELLED'}

        _pattern_sync_live_seams(source)

        seam_count = sum(
            1 for edge in source.data.edges
            if bool(edge.use_seam)
        )

        if seam_count == 0:
            self.report({'WARNING'}, "シームがありません。先にシームを入れてください")
            return {'CANCELLED'}

        count = _pattern_refresh_auto_notches(context, source)

        if count == 0:
            self.report(
                {'WARNING'},
                "オート合印を生成できませんでした。分割数とシームを確認してください"
            )
            return {'CANCELLED'}

        # Keep the original model visible/active so the generated marks are
        # immediately visible on the 3D object.
        try:
            if context.active_object and context.active_object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                pass

        source.hide_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source

        _tag_redraw()

        self.report(
            {'INFO'},
            f"シーム {seam_count} 本からオート合印 {count} 個を生成しました"
        )
        return {'FINISHED'}


class TSUNFOLD_OT_remove_auto_notches(bpy.types.Operator):
    bl_idname = "truescale_unfold.remove_auto_notches"
    bl_label = "オート合印を削除"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_source_object_from_context(context)
        if source is None:
            return {'CANCELLED'}

        count = _pattern_remove_auto_notches(source)
        self.report({'INFO'}, f"オート合印を {count} 個削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_end_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.end_seam"
    bl_label = "シーム終了"
    bl_description = "シーム編集を終了してオブジェクトモードへ戻ります"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if obj.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}



def _focus_selected_unfold(context, top_view=False):
    """Frame the active object and keep orbit/zoom centered on it."""
    obj = context.active_object
    if obj is None:
        return

    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return

    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)

    if region is None or rv3d is None:
        return

    try:
        rv3d.lock_rotation = False
    except Exception:
        pass

    try:
        with context.temp_override(area=area, region=region, space_data=space):
            if top_view:
                bpy.ops.view3d.view_axis(type='TOP', align_active=False)
            bpy.ops.view3d.view_selected(use_all_regions=False)
    except Exception:
        pass


class TSUNFOLD_OT_start_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.start_seam"
    bl_label = "シームを開始"
    bl_description = "編集モード＋エッジ選択でシーム編集を開始します"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        context.scene.unfold_helper_seam_symmetry_x = False
        context.scene.unfold_helper_seam_symmetry_y = False
        context.scene.unfold_helper_seam_symmetry_z = False
        context.scene["pattern_helper_seam_source"] = obj.name
        context.scene["pattern_helper_seam_preview_ready"] = False

        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        bpy.ops.object.mode_set(mode='EDIT')
        context.tool_settings.mesh_select_mode = (False, True, False)

        _sync_blender_mesh_symmetry(context, obj)

        bpy.ops.mesh.select_all(action='DESELECT')

        return {'FINISHED'}


class TSUNFOLD_OT_clear_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_seam"
    bl_label = "シームをクリア"
    bl_description = "Blender標準の『シームをクリア』を選択エッジに実行します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')

        context.tool_settings.mesh_select_mode = (False, True, False)

        try:
            bpy.ops.mesh.mark_seam(clear=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"シームをクリアできませんでした: {exc}")
            return {'CANCELLED'}


        return {'FINISHED'}



class TSUNFOLD_OT_mark_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.mark_seam"
    bl_label = "シームを入れる"
    bl_description = "選択エッジをシーム化します。対称ON時は対称位置も一緒に処理します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        count = _apply_selected_edges_seam_strict_symmetry(context, clear=False)

        if count == 0:
            self.report({'INFO'}, "選択エッジがありません")
            return {'CANCELLED'}

        self.report({'INFO'}, f"{count} 本のエッジをシーム化しました")
        return {'FINISHED'}


class TSUNFOLD_OT_unfold_real_mesh(bpy.types.Operator):
    bl_idname = "truescale_unfold.unfold_real_mesh"
    bl_label = "展開"
    bl_description = "シームに従ってUV展開し、実寸スケールの平面Meshを生成します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and not bool(obj.get("unfold_helper_generated", False))

    def execute(self, context):
        src_obj = context.active_object

        if src_obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = src_obj.data

        # Make sure the latest seam flags are in the Mesh datablock.
        _pattern_sync_live_seams(src_obj)

        # Never reuse the previous helper unwrap.
        # Keep user's own UV maps untouched and create a fresh temporary map.
        previous_uv_name = (
            mesh.uv_layers.active.name
            if mesh.uv_layers.active is not None
            else ""
        )

        temp_uv_name = "__PATTERN_HELPER_TEMP_UV__"

        existing_temp = mesh.uv_layers.get(temp_uv_name)
        if existing_temp is not None:
            try:
                mesh.uv_layers.remove(existing_temp)
            except Exception:
                pass

        temp_uv = mesh.uv_layers.new(
            name=temp_uv_name,
            do_init=False,
        )
        mesh.uv_layers.active = temp_uv

        for o in context.selected_objects:
            o.select_set(False)

        src_obj.select_set(True)
        context.view_layer.objects.active = src_obj

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')

        # Clear any stale UV selection/pin state on the fresh layer.
        try:
            bpy.ops.uv.select_all(action='SELECT')
            bpy.ops.uv.reset()
        except Exception:
            pass

        try:
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
        except RuntimeError:
            bpy.ops.uv.unwrap(method='CONFORMAL', margin=0.001)

        bpy.ops.object.mode_set(mode='OBJECT')

        mesh.update()
        uv_layer = mesh.uv_layers.get(temp_uv_name)

        if uv_layer is None:
            self.report({'ERROR'}, "UV展開に失敗しました。Meshとシームを確認してください。")
            return {'CANCELLED'}

        scale = _calc_real_scale(src_obj, mesh, uv_layer)
        if scale is None:
            self.report({'ERROR'}, "実寸スケールを計算できませんでした。")
            return {'CANCELLED'}

        # Current seam state is the only source of truth.
        # Delete stale pattern/layout/smooth output before rebuilding.
        _pattern_delete_generated_for_source(context, src_obj)

        result = _build_flat_mesh(context, src_obj, mesh, uv_layer, scale)

        # Temporary helper UV is no longer needed after the flat Mesh/mappings
        # have been built. Restore the user's original active UV if possible.
        temp_layer = mesh.uv_layers.get(temp_uv_name)
        if temp_layer is not None:
            try:
                mesh.uv_layers.remove(temp_layer)
            except Exception:
                pass

        if previous_uv_name:
            previous_layer = mesh.uv_layers.get(previous_uv_name)
            if previous_layer is not None:
                mesh.uv_layers.active = previous_layer

        if result is None:
            self.report({'ERROR'}, "平面Meshを生成できませんでした。")
            return {'CANCELLED'}

        _pack_islands(context, result, context.scene.unfold_helper_spacing_mm)
        _pattern_invalidate_layout_cache()
        _show_generated_from_top(context, result)
        _focus_selected_unfold(context, top_view=True)

        size = _object_xy_size_mm(context, result)
        if size:
            self.report({'INFO'}, f"{result.name}：{size[0]:.1f} × {size[1]:.1f} mm")
        return {'FINISHED'}



def _pattern_seam_source(context):
    name = context.scene.get("pattern_helper_seam_source", "")
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == 'MESH':
        return obj

    active = context.active_object
    if active is not None:
        if (
            active.type == 'MESH'
            and not bool(active.get("unfold_helper_generated", False))
        ):
            return active

        if bool(active.get("unfold_helper_generated", False)):
            src = bpy.data.objects.get(
                active.get("unfold_helper_source", "")
            )
            if src is not None and src.type == 'MESH':
                return src

    return None



class TSUNFOLD_OT_load_seamed_object(bpy.types.Operator):
    bl_idname = "truescale_unfold.load_seamed_object"
    bl_label = "シーム付きモデルを読み込む"
    bl_description = "選択中のMeshを型紙元モデルとして登録します。既存のBlenderシームをそのまま使用します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and not bool(obj.get("unfold_helper_generated", False))
        )

    def execute(self, context):
        _pattern_sanitize_arrow_axis(context.scene)
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'WARNING'}, "シーム付きMeshオブジェクトを選択してください")
            return {'CANCELLED'}

        if bool(obj.get("unfold_helper_generated", False)):
            self.report({'WARNING'}, "展開図ではなく元の3Dモデルを選択してください")
            return {'CANCELLED'}

        # Blender標準のEdit Modeで入れた最新シームも読み込む。
        _pattern_sync_live_seams(obj)

        try:
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        seam_count = sum(
            1 for edge in obj.data.edges
            if bool(edge.use_seam)
        )

        if seam_count == 0:
            self.report(
                {'WARNING'},
                "このオブジェクトにはシームがありません。Blender標準機能などで先にシームを設定してください",
            )
            return {'CANCELLED'}

        context.scene["pattern_helper_seam_source"] = obj.name
        context.scene["pattern_helper_seam_preview_ready"] = False

        _pattern_clear_island_highlight()
        _pattern_clear_live_preview()
        _pattern_invalidate_layout_cache()
        _tag_redraw()

        scene_scale, mm_per_bu = _scene_unit_summary(context.scene)
        obj_scale = tuple(float(v) for v in obj.scale)

        self.report(
            {'INFO'},
            (
                f"{obj.name} を読み込みました / "
                f"Unit Scale {scene_scale:g} / "
                f"1 BU = {mm_per_bu:g} mm / "
                f"Object Scale "
                f"{obj_scale[0]:g}, {obj_scale[1]:g}, {obj_scale[2]:g}"
            ),
        )
        return {'FINISHED'}


class TSUNFOLD_OT_build_pattern(bpy.types.Operator):
    bl_idname = "truescale_unfold.build_pattern"
    bl_label = "型紙作成"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_seam_source(context)
        if source is None:
            self.report({'WARNING'}, "元モデルが見つかりません")
            return {'CANCELLED'}

        try:
            if context.active_object and context.active_object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                pass

        source.hide_set(False)
        source.hide_viewport = False
        source.select_set(True)
        context.view_layer.objects.active = source
        context.scene["pattern_helper_seam_source"] = source.name

        # A new build is always a full regeneration from the CURRENT seams on the loaded source model.
        _pattern_remove_auto_notches(source)
        _pattern_clear_island_highlight()
        _pattern_invalidate_layout_cache()
        _pattern_sync_live_seams(source)

        seam_count = sum(
            1 for edge in source.data.edges
            if bool(edge.use_seam)
        )
        if seam_count == 0:
            self.report(
                {'WARNING'},
                "読み込み済みモデルにシームがありません。元モデル側でシームを設定してください",
            )
            return {'CANCELLED'}

        result = bpy.ops.truescale_unfold.unfold_real_mesh()
        if 'FINISHED' not in result:
            return {'CANCELLED'}

        context.scene["pattern_helper_seam_preview_ready"] = False

        auto_notch_count = 0
        if str(
            getattr(
                context.scene,
                "pattern_helper_notch_mode",
                "AUTO",
            )
        ) == "AUTO":
            auto_notch_count = _pattern_refresh_auto_notches(
                context,
                source,
            )

        _pattern_invalidate_layout_cache()
        _tag_redraw()

        if auto_notch_count:
            self.report(
                {'INFO'},
                f"型紙＋ID＋矢印＋オート合印 {auto_notch_count} 個を作成しました"
            )
        else:
            self.report({'INFO'}, "型紙＋ID＋矢印を作成しました")
        return {'FINISHED'}


class TSUNFOLD_OT_cancel_pattern(bpy.types.Operator):
    bl_idname = "truescale_unfold.cancel_pattern"
    bl_label = "キャンセル"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_seam_source(context)
        if source is None:
            return {'CANCELLED'}

        # Cancel means the preview is discarded completely.
        # Remove generated pattern/smooth outputs and preview-only auto data.
        _pattern_delete_generated_for_source(context, source)
        _pattern_remove_auto_notches(source)

        context.scene.pattern_helper_correspondence_mode = False
        context.scene["pattern_helper_seam_preview_ready"] = False

        _pattern_clear_island_highlight()
        _pattern_clear_live_preview()
        _pattern_invalidate_layout_cache()

        try:
            if context.active_object and context.active_object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                pass

        source.hide_set(False)
        source.hide_viewport = False
        source.select_set(True)
        context.view_layer.objects.active = source

        try:
            source.data.update()
            bpy.ops.object.mode_set(mode='EDIT')
            context.tool_settings.mesh_select_mode = (False, True, False)
            bpy.ops.mesh.select_all(action='DESELECT')
        except Exception:
            pass

        context.scene["pattern_helper_seam_preview_ready"] = False
        _tag_redraw()
        self.report({'INFO'}, "型紙情報を破棄して、現在のシーム編集へ戻りました")
        return {'FINISHED'}


class TSUNFOLD_OT_confirm_pattern(bpy.types.Operator):
    bl_idname = "truescale_unfold.confirm_pattern"
    bl_label = "確定"

    def execute(self, context):
        source = _pattern_seam_source(context)
        if source is None:
            return {'CANCELLED'}

        unfold = _pattern_unfold_for_source(source)
        if unfold is None:
            self.report({'WARNING'}, "先に「型紙作成」を押してください")
            return {'CANCELLED'}

        try:
            if context.active_object and context.active_object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                pass

        source.hide_set(False)
        source.hide_viewport = False
        source.select_set(True)
        context.view_layer.objects.active = source

        context.scene.unfold_helper_seam_symmetry_x = False
        context.scene.unfold_helper_seam_symmetry_y = False
        context.scene.unfold_helper_seam_symmetry_z = False
        context.scene.pattern_helper_active_tool = "NONE"
        context.scene["pattern_helper_modal_running"] = False
        context.scene["pattern_helper_seam_preview_ready"] = False

        if str(
            getattr(
                context.scene,
                "pattern_helper_notch_mode",
                "AUTO",
            )
        ) == "AUTO":
            _pattern_refresh_auto_notches(
                context,
                source,
            )

        _pattern_clear_live_preview()
        _pattern_invalidate_layout_cache()
        _tag_redraw()

        self.report({'INFO'}, "型紙を確定しました。マーキング工程へ進めます")
        return {'FINISHED'}


class TSUNFOLD_OT_make_smooth_line(bpy.types.Operator):
    bl_idname = "truescale_unfold.make_smooth_line"
    bl_label = "ラインをなめらかに"
    bl_description = "展開図の外周から、元の周長をほぼ維持した滑らかなCurveを別オブジェクトとして作ります"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and bool(obj.get("unfold_helper_generated", False))
        )

    def execute(self, context):
        source = context.active_object

        if source.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                pass

        curve_obj, stats = _create_smooth_curve(
            context,
            source,
            preserve_length=True
        )

        if curve_obj is None or stats is None:
            self.report({'ERROR'}, "外周線からなめらか線を作れませんでした")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"なめらか線：角 {stats['corner_count']}点固定 / "
            f"Bezier点 {stats['bezier_point_count']} / "
            f"元 {stats['original_mm']:.1f} mm / "
            f"後 {stats['smooth_mm']:.1f} mm / 差 {stats['diff_pct']:+.3f}%"
        )
        return {'FINISHED'}


class TSUNFOLD_OT_select_unfold_source(bpy.types.Operator):
    bl_idname = "truescale_unfold.select_unfold_source"
    bl_label = "元の展開図を選択"
    bl_description = "なめらか線の元になった展開図Meshを選択します"

    @classmethod
    def poll(cls, context):
        return _active_smooth_object(context) is not None

    def execute(self, context):
        smooth = context.active_object
        source_name = smooth.get("unfold_helper_smooth_source", "")
        source = bpy.data.objects.get(source_name)

        if source is None:
            self.report({'ERROR'}, "元の展開図が見つかりません")
            return {'CANCELLED'}

        for o in context.selected_objects:
            o.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source

        return {'FINISHED'}


class TSUNFOLD_OT_show_poly(bpy.types.Operator):
    bl_idname = "truescale_unfold.show_poly"
    bl_label = "ローポリ表示"
    bl_description = "ローポリ展開図を表示し、なめらか線を非表示にします"

    def execute(self, context):
        smooth = _active_smooth_object(context)
        source = None

        if smooth:
            source_name = smooth.get("unfold_helper_smooth_source", "")
            source = bpy.data.objects.get(source_name)

        if source is None:
            obj = context.active_object
            if obj and obj.type == 'MESH' and bool(obj.get("unfold_helper_generated", False)):
                source = obj

        for obj in bpy.data.objects:
            if obj.type == 'CURVE' and bool(obj.get("unfold_helper_smooth_generated", False)):
                obj.hide_viewport = True
                obj.hide_render = True

        for obj in bpy.data.objects:
            if obj.type == 'MESH' and bool(obj.get("unfold_helper_generated", False)):
                obj.hide_viewport = False
                obj.hide_render = False

        if source:
            for o in context.selected_objects:
                o.select_set(False)
            source.select_set(True)
            context.view_layer.objects.active = source

        context.scene["unfold_helper_display_mode"] = "POLY"
        _tag_redraw()
        return {'FINISHED'}


class TSUNFOLD_OT_show_smooth(bpy.types.Operator):
    bl_idname = "truescale_unfold.show_smooth"
    bl_label = "なめらか表示"
    bl_description = "なめらか線がなければ生成し、表示を切り替えます"

    def execute(self, context):
        source = None
        obj = context.active_object

        if obj and obj.type == 'MESH' and bool(obj.get("unfold_helper_generated", False)):
            source = obj

        elif obj and obj.type == 'CURVE' and bool(obj.get("unfold_helper_smooth_generated", False)):
            source_name = obj.get("unfold_helper_smooth_source", "")
            source = bpy.data.objects.get(source_name)

        if source is None:
            self.report({'ERROR'}, "元の展開図を選択してください")
            return {'CANCELLED'}

        smooth_obj = None
        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("unfold_helper_smooth_generated", False))
                and candidate.get("unfold_helper_smooth_source") == source.name
            ):
                smooth_obj = candidate
                break

        if smooth_obj is None:
            smooth_obj, stats = _create_smooth_curve(context, source, preserve_length=True)
            if smooth_obj is None:
                self.report({'ERROR'}, "なめらか線を生成できませんでした")
                return {'CANCELLED'}

        for candidate in bpy.data.objects:
            if candidate.type == 'MESH' and bool(candidate.get("unfold_helper_generated", False)):
                candidate.hide_viewport = True
                candidate.hide_render = True

        for candidate in bpy.data.objects:
            if candidate.type == 'CURVE' and bool(candidate.get("unfold_helper_smooth_generated", False)):
                candidate.hide_viewport = False
                candidate.hide_render = False

        for o in context.selected_objects:
            o.select_set(False)
        smooth_obj.select_set(True)
        context.view_layer.objects.active = smooth_obj

        context.scene["unfold_helper_display_mode"] = "POLY"

        # Markings are not duplicated/stored for smooth mode.
        # They are recalculated against the current smooth outline on redraw,
        # so switching finish mode instantly moves only the notches.
        _tag_redraw()

        return {'FINISHED'}



def _resolve_unfold_mesh_for_layout(context):
    """Resolve the underlying generated unfold Mesh for layout operations."""
    obj = context.active_object

    # Directly selected unfold Mesh.
    if (
        obj is not None
        and obj.type == 'MESH'
        and bool(obj.get("unfold_helper_generated", False))
    ):
        return obj

    # Smooth Curve selected: resolve back to its source unfold Mesh.
    if (
        obj is not None
        and obj.type == 'CURVE'
        and bool(obj.get("unfold_helper_smooth_generated", False))
    ):
        source_name = obj.get("unfold_helper_source", "")
        if source_name:
            candidate = bpy.data.objects.get(source_name)
            if (
                candidate is not None
                and candidate.type == 'MESH'
                and bool(candidate.get("unfold_helper_generated", False))
            ):
                return candidate

        # Fallback: smooth object may carry source unfold name under another key.
        source_name = obj.get("unfold_helper_unfold_source", "")
        if source_name:
            candidate = bpy.data.objects.get(source_name)
            if (
                candidate is not None
                and candidate.type == 'MESH'
                and bool(candidate.get("unfold_helper_generated", False))
            ):
                return candidate

    # Fallback to any visible generated unfold Mesh.
    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("unfold_helper_generated", False))
            and not candidate.hide_viewport
        ):
            return candidate

    # Last resort: any generated unfold Mesh, even if hidden by smooth display.
    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("unfold_helper_generated", False))
        ):
            return candidate

    return None


class TSUNFOLD_OT_auto_layout(bpy.types.Operator):
    bl_idname = "truescale_unfold.auto_layout"
    bl_label = "用紙に自動レイアウト"
    bl_description = "表示中の用紙枠へ、実寸のままアイランドを自動配置します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _resolve_unfold_mesh_for_layout(context) is not None

        return (
            obj is not None
            and obj.type == 'MESH'
            and bool(obj.get("unfold_helper_generated", False))
        )

    def execute(self, context):
        obj = _resolve_unfold_mesh_for_layout(context)

        if obj.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError as exc:
                self.report({'ERROR'}, f"Object Modeへ戻せませんでした: {exc}")
                return {'CANCELLED'}

        ok, message = _try_shelf_layout(context, obj, allow_rotate=True)
        _pattern_invalidate_layout_cache()

        if not ok:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}

        _show_generated_from_top(context, obj)
        self.report({'INFO'}, message)
        return {'FINISHED'}


class TSUNFOLD_OT_layout_edit(bpy.types.Operator):
    bl_idname = "truescale_unfold.layout_edit"
    bl_label = "レイアウトの変更"
    bl_description = "編集モードに入り、面を1枚選ぶだけでアイランド全体を自動選択します"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _last_selected = None

    @classmethod
    def poll(cls, context):
        return _resolve_unfold_mesh_for_layout(context) is not None

        return (
            obj is not None
            and obj.type == 'MESH'
            and bool(obj.get("unfold_helper_generated", False))
        )

    def invoke(self, context, event):
        context.scene["pattern_helper_manual_layout_active"] = True
        _pattern_clear_island_highlight()
        _pattern_clear_live_preview()
        _tag_redraw()

        obj = _resolve_unfold_mesh_for_layout(context)

        if obj is None:
            return {'CANCELLED'}

        # Smooth finishing is a separate Curve. Manual layout edits operate
        # on the real flat Mesh, so temporarily return to POLY display.
        context.scene["unfold_helper_display_mode"] = "POLY"
        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("unfold_helper_smooth_generated", False))
            ):
                candidate.hide_viewport = True
                candidate.hide_set(True)

        obj.hide_viewport = False
        obj.hide_set(False)

        for o in context.selected_objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        if obj.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                pass

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='DESELECT')
            context.tool_settings.mesh_select_mode = (False, False, True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"レイアウト編集モードへ入れませんでした: {exc}")
            return {'CANCELLED'}

        _focus_selected_unfold(context, top_view=False)

        self._last_selected = frozenset()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.12, window=context.window)
        wm.modal_handler_add(self)

        self.report({'INFO'}, "面をクリック → アイランド全選択 → Gで移動できます")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        obj = context.active_object

        if (
            obj is None
            or obj.type != 'MESH'
            or not bool(obj.get("unfold_helper_generated", False))
            or obj.mode != 'EDIT'
        ):
            self._finish(context)
            return {'FINISHED'}

        if event.type == 'ESC':
            self._finish(context)
            self.report({'INFO'}, "アイランド自動選択を終了しました")
            return {'FINISHED'}

        if event.type == 'TIMER':
            try:
                # Manual layout is intentionally quiet:
                # do not sync obj.data or rebuild marking caches every TIMER.
                # BMesh selection handling below is enough while moving.

                import bmesh
                bm = bmesh.from_edit_mesh(obj.data)
                bm.faces.ensure_lookup_table()

                # IMPORTANT:
                # Do not call update_edit_mesh every TIMER tick. Doing so while
                # Blender's native G/rotate transform is running can fight the
                # transform modal and make the selected island appear immovable.
                selected_now = frozenset(f.index for f in bm.faces if f.select)

                # Ignore the selection set created by our own previous expansion.
                if selected_now != self._last_selected:
                    newly_selected = selected_now - (self._last_selected or frozenset())

                    if newly_selected:
                        # Expand from each newly selected face through connected geometry.
                        # The flattened UV islands are disconnected mesh components,
                        # so connectivity exactly matches an island.
                        target_faces = set()

                        for face_index in newly_selected:
                            if face_index >= len(bm.faces):
                                continue

                            seed = bm.faces[face_index]
                            stack = [seed]
                            visited = {seed}

                            while stack:
                                face = stack.pop()
                                target_faces.add(face)

                                for edge in face.edges:
                                    for linked_face in edge.link_faces:
                                        if linked_face not in visited:
                                            visited.add(linked_face)
                                            stack.append(linked_face)

                        # Preserve already-selected islands, then add full new islands.
                        for face in target_faces:
                            face.select = True

                        bmesh.update_edit_mesh(
                            obj.data,
                            loop_triangles=False,
                            destructive=False,
                        )
                        _tag_redraw()

                        # Refresh after expansion so TIMER does not re-trigger on our own work.
                        self._last_selected = frozenset(
                            f.index for f in bm.faces if f.select
                        )
                    else:
                        self._last_selected = selected_now

            except Exception:
                pass

        if (
            event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'G', 'R', 'S'}
            and event.value == 'RELEASE'
        ):
            _tag_redraw()

        return {'PASS_THROUGH'}

    def _finish(self, context):
        obj = context.active_object
        if (
            obj is not None
            and obj.type == 'MESH'
            and bool(obj.get("unfold_helper_generated", False))
            and obj.mode == 'EDIT'
        ):
            try:
                import bmesh
                bmesh.update_edit_mesh(
                    obj.data,
                    loop_triangles=False,
                    destructive=False,
                )
            except Exception:
                pass

        _tag_redraw()

        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None



class TSUNFOLD_OT_layout_confirm(bpy.types.Operator):
    bl_idname = "truescale_unfold.layout_confirm"
    bl_label = "レイアウト確定"
    bl_description = "手動レイアウト編集を終了してObject Modeへ戻ります"

    @classmethod
    def poll(cls, context):
        obj = _resolve_unfold_mesh_for_layout(context)
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = _resolve_unfold_mesh_for_layout(context)

        if obj is None:
            self.report({'WARNING'}, "型紙が見つかりません")
            return {'CANCELLED'}

        try:
            # Ensure the generated pattern is active before mode change.
            if context.active_object is not obj:
                for selected in context.selected_objects:
                    selected.select_set(False)

                obj.hide_set(False)
                obj.hide_viewport = False
                obj.select_set(True)
                context.view_layer.objects.active = obj

            if obj.mode == 'EDIT':
                try:
                    obj.update_from_editmode()
                except Exception:
                    pass

                bpy.ops.object.mode_set(mode='OBJECT')

        except Exception as exc:
            self.report(
                {'ERROR'},
                f"レイアウト確定に失敗しました: {exc}",
            )
            return {'CANCELLED'}

        context.scene["pattern_helper_manual_layout_active"] = False
        _pattern_invalidate_layout_cache()
        _tag_redraw()

        try:
            _show_generated_from_top(context, obj)
        except Exception:
            pass

        self.report({'INFO'}, "レイアウトを確定しました")
        return {'FINISHED'}


class TSUNFOLD_OT_delete_unfold(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_unfold"
    bl_label = "展開図を削除"
    bl_description = "生成されたローポリ展開図となめらか線をまとめて削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene["pattern_helper_manual_layout_active"] = False
        try:
            context.scene.pattern_helper_pattern_preview = False
            context.scene["pattern_helper_preview_prev_active"] = ""
        except Exception:
            pass

        mesh_targets = [
            obj for obj in list(bpy.data.objects)
            if obj.type == 'MESH' and bool(obj.get("unfold_helper_generated", False))
        ]

        curve_targets = [
            obj for obj in list(bpy.data.objects)
            if obj.type == 'CURVE' and bool(obj.get("unfold_helper_smooth_generated", False))
        ]

        total = 0

        for obj in curve_targets:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                bpy.data.curves.remove(data)
            total += 1

        for obj in mesh_targets:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                bpy.data.meshes.remove(data)
            total += 1

        # Deleting the unfold result also clears viewport paper/preview overlays.
        context.scene.unfold_helper_show_paper = False
        context.scene.unfold_helper_preview = False

        if total == 0:
            self.report({'INFO'}, "展開図はありません。用紙枠とプレビューをOFFにしました")
            _tag_redraw()
            return {'CANCELLED'}

        self.report({'INFO'}, f"展開図関連を {total} 個削除し、用紙枠も非表示にしました")
        _tag_redraw()
        return {'FINISHED'}




class TSUNFOLD_OT_toggle_source_visibility(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_source_visibility"
    bl_label = "元モデル表示 / 非表示"
    bl_description = "読み込み済みの元3Dモデルだけを表示/非表示します。型紙やビュー位置は変更しません"

    def execute(self, context):
        source = _pattern_seam_source(context)
        if source is None:
            source = _pattern_source_object_from_context(context)

        if source is None:
            self.report({'WARNING'}, "元モデルが見つかりません")
            return {'CANCELLED'}

        # hide_set() is local-view aware and very軽量。
        # hide_viewport datablock settingは変更しない。
        try:
            is_hidden = bool(source.hide_get())
        except Exception:
            is_hidden = False

        try:
            source.hide_set(not is_hidden)
        except Exception as exc:
            self.report({'WARNING'}, f"元モデルの表示切替に失敗しました: {exc}")
            return {'CANCELLED'}

        _tag_redraw()

        if is_hidden:
            self.report({'INFO'}, "元モデルを表示しました")
        else:
            self.report({'INFO'}, "元モデルを非表示にしました")

        return {'FINISHED'}


class TSUNFOLD_OT_toggle_pattern_preview(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_pattern_preview"
    bl_label = "型紙を表示 / 隠す"
    bl_description = "実際に生成された型紙オブジェクトを表示/非表示し、元モデルとの確認を切り替えます"

    def execute(self, context):
        scene = context.scene

        unfold = _resolve_unfold_mesh_for_layout(context)
        if unfold is None:
            for candidate in bpy.data.objects:
                if (
                    candidate.type == 'MESH'
                    and bool(candidate.get("unfold_helper_generated", False))
                ):
                    unfold = candidate
                    break

        if unfold is None:
            self.report({'WARNING'}, "先に「型紙展開」で型紙を作成してください")
            return {'CANCELLED'}

        showing = bool(getattr(scene, "pattern_helper_pattern_preview", False))

        if not showing:
            active = context.active_object
            scene["pattern_helper_preview_prev_active"] = (
                active.name if active is not None else ""
            )

            for obj in context.selected_objects:
                try:
                    obj.select_set(False)
                except Exception:
                    pass

            unfold.hide_set(False)
            unfold.hide_viewport = False
            unfold.select_set(True)
            context.view_layer.objects.active = unfold

            for obj in bpy.data.objects:
                if (
                    obj.type == 'CURVE'
                    and bool(obj.get("unfold_helper_smooth_generated", False))
                ):
                    obj.hide_viewport = True

            scene.pattern_helper_pattern_preview = True

            # 表示切替だけ行い、ビュー方向・ズーム・注視点は変更しない。
            # 元モデルが「消えたように見える」原因になる自動フレーミングを廃止。
            self.report({'INFO'}, "生成済み型紙を表示しました")

        else:
            unfold.hide_set(True)
            unfold.hide_viewport = True

            prev_name = scene.get("pattern_helper_preview_prev_active", "")
            prev = bpy.data.objects.get(prev_name)

            if prev is None:
                src_name = unfold.get("unfold_helper_source", "")
                prev = bpy.data.objects.get(src_name)

            if prev is not None:
                for obj in context.selected_objects:
                    try:
                        obj.select_set(False)
                    except Exception:
                        pass

                # 元モデルの表示状態は専用トグルの設定を尊重する。
                if not prev.hide_get():
                    prev.select_set(True)
                    context.view_layer.objects.active = prev

            scene.pattern_helper_pattern_preview = False
            self.report({'INFO'}, "型紙を隠して元の作業へ戻りました")

        _tag_redraw()
        return {'FINISHED'}


class TSUNFOLD_OT_toggle_preview(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_preview"
    bl_label = "印刷プレビュー"
    bl_description = "必要ならObject Modeへ戻して、用紙と最終印刷輪郭を3Dビューに表示します"

    def execute(self, context):
        obj = context.active_object

        # Print preview is a layout-level view, so always return to Object Mode.
        if obj is not None and obj.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError as exc:
                self.report({'ERROR'}, f"Object Modeへ切り替えられませんでした: {exc}")
                return {'CANCELLED'}

        scene = context.scene
        scene.unfold_helper_preview = not scene.unfold_helper_preview

        _pattern_print_preview_source_visibility(
            context,
            bool(scene.unfold_helper_preview),
        )
        _tag_redraw()

        if scene.unfold_helper_preview:
            self.report({'INFO'}, "Object Modeへ切り替えて印刷プレビュー ON")
        else:
            self.report({'INFO'}, "印刷プレビュー OFF")
        return {'FINISHED'}



def _export_outline_segments(context):
    """Return outline segments for the currently displayed finish."""
    mode = context.scene.get("unfold_helper_display_mode", "POLY")
    obj = context.active_object

    if (
        mode == "SMOOTH"
        or (
            obj
            and obj.type == 'CURVE'
            and bool(obj.get("unfold_helper_smooth_generated", False))
        )
    ):
        if (
            obj
            and obj.type == 'CURVE'
            and bool(obj.get("unfold_helper_smooth_generated", False))
        ):
            return _smooth_curve_segments_world_xy(obj)

        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("unfold_helper_smooth_generated", False))
                and not candidate.hide_viewport
            ):
                return _smooth_curve_segments_world_xy(candidate)

    if (
        obj
        and obj.type == 'MESH'
        and bool(obj.get("unfold_helper_generated", False))
    ):
        return _boundary_segments_world_xy(obj)

    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("unfold_helper_generated", False))
            and not candidate.hide_viewport
        ):
            return _boundary_segments_world_xy(candidate)

    return []


def _can_export_current_finish(context):
    obj = context.active_object
    if obj is None:
        return False

    if obj.type == 'MESH' and bool(obj.get("unfold_helper_generated", False)):
        return True

    if obj.type == 'CURVE' and bool(obj.get("unfold_helper_smooth_generated", False)):
        return True

    return False



def _pattern_text_outline_segments(context, text, world_pos, size_mm, angle=0.0):
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
        curve.size = _mm_to_bu(
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
                pass

        if curve is not None and curve.users == 0:
            try:
                bpy.data.curves.remove(curve)
            except Exception:
                pass


class TSUNFOLD_OT_export_png(bpy.types.Operator, ExportHelper):
    bl_idname = "truescale_unfold.export_png"
    bl_label = "実寸PNGを書き出し"
    bl_description = "選択した展開図を実寸PNGとして300dpiで書き出します"

    filename_ext = ".png"
    filter_glob: StringProperty(default="*.png", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _can_export_current_finish(context)

    def invoke(self, context, event):
        obj = context.active_object
        filename = bpy.path.clean_name(obj.name) + ".png"

        last_dir = context.scene.get("unfold_helper_last_export_dir", "")
        if last_dir and Path(last_dir).exists():
            self.filepath = str(Path(last_dir) / filename)
        else:
            self.filepath = filename

        return super().invoke(context, event)

    def execute(self, context):
        obj = context.active_object
        scene = context.scene

        segments = _export_outline_segments(context)
        bbox = _segments_bbox(segments)

        if bbox is None:
            self.report({'ERROR'}, "印刷できる外周線がありません。")
            return {'CANCELLED'}

        min_x, min_y, max_x, max_y = bbox
        bu_to_mm = _scene_scale_to_meters(scene) * 1000.0

        shape_w_mm = (max_x - min_x) * bu_to_mm
        shape_h_mm = (max_y - min_y) * bu_to_mm

        paper_w_mm, paper_h_mm = _export_paper_dimensions(
            scene,
            shape_w_mm,
            shape_h_mm,
        )

        if shape_w_mm > paper_w_mm + 1e-6 or shape_h_mm > paper_h_mm + 1e-6:
            self.report(
                {'ERROR'},
                f"展開図 {shape_w_mm:.1f}×{shape_h_mm:.1f} mm は "
                f"{_paper_display_name(scene)} に収まりません。"
            )
            return {'CANCELLED'}

        px_per_mm = PRINT_DPI / 25.4
        width_px = int(round(paper_w_mm * px_per_mm))
        height_px = int(round(paper_h_mm * px_per_mm))

        total_pixels = width_px * height_px
        if total_pixels > 160_000_000:
            self.report({'ERROR'}, f"画像が大きすぎます ({width_px}×{height_px}px)")
            return {'CANCELLED'}

        try:
            buffer = bytearray(b"\xff" * (width_px * height_px * 3))
        except MemoryError:
            self.report({'ERROR'}, "PNG作成用のメモリを確保できませんでした。")
            return {'CANCELLED'}

        # 1) Pattern outline: always black.
        for (ax, ay), (bx, by) in segments:
            x1_mm = _bu_to_mm(scene, ax)
            y1_mm = _bu_to_mm(scene, ay)
            x2_mm = _bu_to_mm(scene, bx)
            y2_mm = _bu_to_mm(scene, by)

            x1 = x1_mm * px_per_mm
            y1 = height_px - (y1_mm * px_per_mm)
            x2 = x2_mm * px_per_mm
            y2 = height_px - (y2_mm * px_per_mm)

            _draw_line_rgb(
                buffer,
                width_px,
                height_px,
                x1,
                y1,
                x2,
                y2,
                thickness=2,
                color=(0.0, 0.0, 0.0),
            )

        source = _pattern_source_object_from_context(context)
        unfold = _pattern_unfold_for_source(source) if source else None

        # 2) Colored geometric annotations.
        if source is not None and unfold is not None:
            for wa, wb, color, width_mm in _pattern_flat_colored_segments(
                context,
                source,
                unfold,
            ):
                x1 = _bu_to_mm(scene, wa.x) * px_per_mm
                y1 = height_px - (_bu_to_mm(scene, wa.y) * px_per_mm)
                x2 = _bu_to_mm(scene, wb.x) * px_per_mm
                y2 = height_px - (_bu_to_mm(scene, wb.y) * px_per_mm)

                _draw_line_rgb(
                    buffer,
                    width_px,
                    height_px,
                    x1,
                    y1,
                    x2,
                    y2,
                    thickness=max(
                        1,
                        int(round(float(width_mm) * px_per_mm)),
                    ),
                    color=color,
                )

            # 3) Number / arbitrary text as Blender FONT outline geometry.
            for text, world_pos, size_mm, color in _pattern_flat_text_items(
                source,
                unfold,
            ):
                for wa, wb in _pattern_text_outline_segments(
                    context,
                    text,
                    world_pos,
                    size_mm,
                ):
                    x1 = _bu_to_mm(scene, wa.x) * px_per_mm
                    y1 = height_px - (_bu_to_mm(scene, wa.y) * px_per_mm)
                    x2 = _bu_to_mm(scene, wb.x) * px_per_mm
                    y2 = height_px - (_bu_to_mm(scene, wb.y) * px_per_mm)

                    _draw_line_rgb(
                        buffer,
                        width_px,
                        height_px,
                        x1,
                        y1,
                        x2,
                        y2,
                        thickness=2,
                        color=color,
                    )


            # 4) Flat-only memo text.
            for text, world_pos, size_mm, color, angle in _pattern_flat_memo_text_items(
                unfold
            ):
                for wa, wb in _pattern_text_outline_segments(
                    context,
                    text,
                    world_pos,
                    size_mm,
                    angle,
                ):
                    x1 = _bu_to_mm(scene, wa.x) * px_per_mm
                    y1 = height_px - (_bu_to_mm(scene, wa.y) * px_per_mm)
                    x2 = _bu_to_mm(scene, wb.x) * px_per_mm
                    y2 = height_px - (_bu_to_mm(scene, wb.y) * px_per_mm)

                    _draw_line_rgb(
                        buffer,
                        width_px,
                        height_px,
                        x1,
                        y1,
                        x2,
                        y2,
                        thickness=2,
                        color=color,
                    )

            # 5) Automatic island IDs / connection labels with orientation.
            if bool(
                getattr(
                    scene,
                    "pattern_helper_auto_island_ids",
                    True,
                )
            ):
                for (
                    text,
                    world_pos,
                    size_mm,
                    color,
                    angle,
                    _edge_locked,
                ) in _pattern_auto_flat_oriented_text_items(
                    context,
                    source,
                    unfold,
                ):
                    for wa, wb in _pattern_text_outline_segments(
                        context,
                        text,
                        world_pos,
                        size_mm,
                        angle,
                    ):
                        x1 = _bu_to_mm(scene, wa.x) * px_per_mm
                        y1 = height_px - (_bu_to_mm(scene, wa.y) * px_per_mm)
                        x2 = _bu_to_mm(scene, wb.x) * px_per_mm
                        y2 = height_px - (_bu_to_mm(scene, wb.y) * px_per_mm)

                        _draw_line_rgb(
                            buffer,
                            width_px,
                            height_px,
                            x1,
                            y1,
                            x2,
                            y2,
                            thickness=2,
                            color=color,
                        )

        filepath = Path(bpy.path.abspath(self.filepath))
        if filepath.suffix.lower() != ".png":
            filepath = filepath.with_suffix(".png")

        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            _write_png_rgb(filepath, width_px, height_px, buffer, PRINT_DPI)
        except Exception as exc:
            self.report({'ERROR'}, f"PNGを書き出せませんでした: {exc}")
            return {'CANCELLED'}

        # Remember the directory used for the latest successful export.
        context.scene["unfold_helper_last_export_dir"] = str(filepath.parent)

        self.report(
            {'INFO'},
            f"実寸PNGを書き出しました / {PRINT_DPI}dpi / 次回もこの保存先を開きます"
        )
        return {'FINISHED'}



# ------------------------------------------------------------
# 3D pattern annotation system
# ------------------------------------------------------------

_PATTERN_ANNOTATION_PROP = "pattern_helper_annotations_json"


_PATTERN_FLAT_MEMO_PROP = "pattern_helper_flat_memos_json"

# Runtime-only memo edit state.
_pattern_selected_memo = {
    "unfold": "",
    "index": -1,
}


def _pattern_clear_selected_memo():
    _pattern_selected_memo["unfold"] = ""
    _pattern_selected_memo["index"] = -1
    _tag_redraw()


def _pattern_selected_memo_item():
    unfold = bpy.data.objects.get(_pattern_selected_memo.get("unfold", ""))
    index = int(_pattern_selected_memo.get("index", -1))
    if unfold is None:
        return None, -1, None
    items = _pattern_get_flat_memos(unfold)
    if not (0 <= index < len(items)):
        return unfold, -1, None
    return unfold, index, items[index]



def _pattern_get_flat_memos(unfold_obj):
    if unfold_obj is None:
        return []

    raw = unfold_obj.get(_PATTERN_FLAT_MEMO_PROP, "[]")
    try:
        data = json.loads(raw)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        pos = item.get("pos", None)
        if not text or not isinstance(pos, (list, tuple)) or len(pos) < 2:
            continue
        result.append(item)

    return result


def _pattern_set_flat_memos(unfold_obj, items):
    if unfold_obj is None:
        return

    unfold_obj[_PATTERN_FLAT_MEMO_PROP] = json.dumps(
        list(items),
        ensure_ascii=False,
    )
    _tag_redraw()


def _pattern_flat_memo_text_items(unfold_obj):
    result = []
    if unfold_obj is None:
        return result

    mw = unfold_obj.matrix_world

    for item in _pattern_get_flat_memos(unfold_obj):
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


def _pattern_pick_flat_memo_at_mouse(context, event, max_px=26.0):
    region = context.region
    rv3d = getattr(context.space_data, "region_3d", None)
    if region is None or rv3d is None:
        return None

    unfold = _resolve_unfold_mesh_for_layout(context)
    if unfold is None:
        source = _pattern_source_object_from_context(context)
        if source is not None:
            unfold = _pattern_unfold_for_source(source)
    if unfold is None:
        return None

    mx = float(event.mouse_region_x)
    my = float(event.mouse_region_y)
    best = None
    best_d2 = float(max_px) * float(max_px)

    for index, item in enumerate(_pattern_get_flat_memos(unfold)):
        pos = item.get("pos", [0.0, 0.0, 0.0])
        try:
            local = Vector((
                float(pos[0]),
                float(pos[1]),
                float(pos[2]) if len(pos) > 2 else 0.0,
            ))
        except Exception:
            continue

        world = unfold.matrix_world @ local
        screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
        if screen is None:
            continue

        dx = mx - float(screen.x)
        dy = my - float(screen.y)
        d2 = dx * dx + dy * dy
        if d2 <= best_d2:
            best_d2 = d2
            best = (unfold, index)

    return best


def _pattern_raycast_flat_pattern_location(context, event):
    if (
        context.region is None
        or context.space_data is None
        or context.area is None
        or context.area.type != 'VIEW_3D'
    ):
        return None

    rv3d = getattr(context.space_data, "region_3d", None)
    if rv3d is None:
        return None

    mouse = Vector((
        float(event.mouse_region_x),
        float(event.mouse_region_y),
    ))

    try:
        origin = view3d_utils.region_2d_to_origin_3d(
            context.region,
            rv3d,
            mouse,
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            context.region,
            rv3d,
            mouse,
        ).normalized()

        depsgraph = context.evaluated_depsgraph_get()
        hit, location, _normal, _face_index, hit_obj, _matrix = (
            context.scene.ray_cast(
                depsgraph,
                origin,
                direction,
            )
        )

        if not hit or hit_obj is None:
            return None

        original = (
            hit_obj.original
            if hasattr(hit_obj, "original")
            else hit_obj
        )

        if (
            original.type != 'MESH'
            or not bool(original.get("unfold_helper_generated", False))
        ):
            return None

        local = original.matrix_world.inverted() @ Vector(location)
        return original, local

    except Exception:
        return None




def _pattern_marking_session_active(scene):
    return bool(scene.get("pattern_helper_marking_session_active", False))


def _pattern_begin_marking_session(context, source_obj):
    """Save the user's working state once, then enter marking workspace."""
    scene = context.scene

    if not _pattern_marking_session_active(scene):
        active = context.active_object
        selected_names = [
            obj.name
            for obj in context.selected_objects
            if obj is not None
        ]

        scene["pattern_helper_marking_prev_active"] = (
            active.name if active is not None else ""
        )
        scene["pattern_helper_marking_prev_selected_json"] = json.dumps(
            selected_names,
            ensure_ascii=False,
        )
        scene["pattern_helper_marking_prev_mode"] = (
            active.mode if active is not None else "OBJECT"
        )
        scene["pattern_helper_marking_session_active"] = True
        scene["pattern_helper_marking_finish_requested"] = False

    # Marking always happens on the original source Mesh in Object Mode.
    try:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass

    for obj in context.selected_objects:
        try:
            obj.select_set(False)
        except Exception:
            pass

    source_obj.hide_set(False)
    source_obj.hide_viewport = False
    source_obj.select_set(True)
    context.view_layer.objects.active = source_obj

    _tag_redraw()


def _pattern_request_finish_marking(context):
    context.scene["pattern_helper_marking_finish_requested"] = True
    _tag_redraw()


def _pattern_restore_work_state(context):
    """Restore active object, selection and mode saved before marking."""
    scene = context.scene

    prev_active_name = scene.get(
        "pattern_helper_marking_prev_active",
        "",
    )
    prev_mode = scene.get(
        "pattern_helper_marking_prev_mode",
        "OBJECT",
    )

    try:
        selected_names = json.loads(
            scene.get(
                "pattern_helper_marking_prev_selected_json",
                "[]",
            )
        )
    except Exception:
        selected_names = []

    try:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass

    for obj in context.selected_objects:
        try:
            obj.select_set(False)
        except Exception:
            pass

    for name in selected_names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            try:
                obj.select_set(True)
            except Exception:
                pass

    active = bpy.data.objects.get(prev_active_name)
    if active is not None:
        active.hide_set(False)
        active.hide_viewport = False
        active.select_set(True)
        context.view_layer.objects.active = active

        if prev_mode == 'EDIT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass
        elif prev_mode == 'SCULPT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='SCULPT')
            except Exception:
                pass
        elif prev_mode == 'VERTEX_PAINT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='VERTEX_PAINT')
            except Exception:
                pass
        elif prev_mode == 'WEIGHT_PAINT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            except Exception:
                pass

    scene["pattern_helper_marking_session_active"] = False
    scene["pattern_helper_marking_finish_requested"] = False
    scene["pattern_helper_modal_running"] = False
    scene.pattern_helper_active_tool = "NONE"
    scene["pattern_helper_marking_prev_active"] = ""
    scene["pattern_helper_marking_prev_selected_json"] = "[]"
    scene["pattern_helper_marking_prev_mode"] = "OBJECT"

    _tag_redraw()


class TSUNFOLD_OT_finish_marking(bpy.types.Operator):
    bl_idname = "truescale_unfold.finish_marking"
    bl_label = "マーキング終了"
    bl_description = "3D型紙マーキングを終了し、開始前の選択・モードへ戻ります"

    def execute(self, context):
        if not _pattern_marking_session_active(context.scene):
            self.report({'INFO'}, "現在マーキングモードではありません")
            return {'CANCELLED'}

        context.scene.pattern_helper_active_tool = "NONE"
        _pattern_clear_live_preview()
        context.scene["pattern_helper_modal_running"] = False
        _pattern_request_finish_marking(context)
        _pattern_restore_work_state(context)

        self.report({'INFO'}, "マーキングを終了し、元の作業状態へ戻しました")
        return {'FINISHED'}


def _pattern_source_object_from_context(context):
    obj = context.active_object

    if (
        obj is not None
        and obj.type == 'MESH'
        and not bool(obj.get("unfold_helper_generated", False))
    ):
        return obj

    if obj is not None and bool(obj.get("unfold_helper_generated", False)):
        name = obj.get("unfold_helper_source", "")
        src = bpy.data.objects.get(name)
        if src is not None and src.type == 'MESH':
            return src

    if (
        obj is not None
        and obj.type == 'CURVE'
        and bool(obj.get("unfold_helper_smooth_generated", False))
    ):
        unfold_name = obj.get("unfold_helper_smooth_source", "")
        unfold = bpy.data.objects.get(unfold_name)
        if unfold is not None:
            name = unfold.get("unfold_helper_source", "")
            src = bpy.data.objects.get(name)
            if src is not None and src.type == 'MESH':
                return src

    return None


def _pattern_unfold_for_source(source_obj):
    if source_obj is None:
        return None

    for obj in bpy.data.objects:
        if (
            obj.type == 'MESH'
            and bool(obj.get("unfold_helper_generated", False))
            and obj.get("unfold_helper_source", "") == source_obj.name
        ):
            return obj

    return None


def _pattern_get_annotations(source_obj):
    if source_obj is None:
        return []

    raw = source_obj.get(_PATTERN_ANNOTATION_PROP, "[]")
    try:
        data = json.loads(raw)
    except Exception:
        return []

    return data if isinstance(data, list) else []


def _pattern_set_annotations(source_obj, annotations):
    source_obj[_PATTERN_ANNOTATION_PROP] = json.dumps(
        annotations,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        source_obj.data.update()
    except Exception:
        pass

    _pattern_invalidate_layout_cache()


def _pattern_anchor_point_source_local(source_obj, anchor):
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


def _pattern_anchor_normal_source_local(source_obj, anchor):
    try:
        face_index = int(anchor["face"])
    except Exception:
        return Vector((0.0, 0.0, 1.0))

    if not (0 <= face_index < len(source_obj.data.polygons)):
        return Vector((0.0, 0.0, 1.0))

    n = source_obj.data.polygons[face_index].normal.copy()
    if n.length <= 1e-12:
        return Vector((0.0, 0.0, 1.0))
    return n.normalized()


def _pattern_flat_mapping(unfold_obj):
    try:
        vert_src = json.loads(
            unfold_obj.get("pattern_helper_flat_vertex_source_json", "[]")
        )
        face_src = json.loads(
            unfold_obj.get("pattern_helper_flat_face_source_json", "[]")
        )
    except Exception:
        return [], []

    return vert_src, face_src


def _pattern_anchor_point_flat_local(unfold_obj, anchor):
    vert_src, face_src = _pattern_flat_mapping(unfold_obj)
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


def _pattern_make_anchor_from_hit(source_obj, face_index, local_hit):
    mesh = source_obj.data
    mesh.calc_loop_triangles()

    candidates = [
        tri for tri in mesh.loop_triangles
        if tri.polygon_index == face_index
    ]

    if not candidates:
        return None

    chosen = None
    chosen_weights = None
    best_score = None

    unit_a = Vector((1.0, 0.0, 0.0))
    unit_b = Vector((0.0, 1.0, 0.0))
    unit_c = Vector((0.0, 0.0, 1.0))

    for tri in candidates:
        ids = list(tri.vertices)
        a = mesh.vertices[ids[0]].co
        b = mesh.vertices[ids[1]].co
        c = mesh.vertices[ids[2]].co

        weights = geometry.barycentric_transform(
            local_hit,
            a, b, c,
            unit_a, unit_b, unit_c,
        )

        vals = [float(weights.x), float(weights.y), float(weights.z)]
        score = sum(max(0.0, -v) for v in vals)

        if best_score is None or score < best_score:
            best_score = score
            chosen = ids
            chosen_weights = vals

        if score <= 1e-5:
            break

    if chosen is None:
        return None

    # Clamp tiny numerical errors and normalize.
    chosen_weights = [max(0.0, v) for v in chosen_weights]
    total = sum(chosen_weights)
    if total <= 1e-12:
        return None
    chosen_weights = [v / total for v in chosen_weights]

    return {
        "face": int(face_index),
        "tri": [int(v) for v in chosen],
        "w": [round(float(v), 8) for v in chosen_weights],
    }


def _pattern_raycast_source(context, event, source_obj):
    region = context.region
    rv3d = context.space_data.region_3d
    coord = (event.mouse_region_x, event.mouse_region_y)

    world_origin = view3d_utils.region_2d_to_origin_3d(
        region, rv3d, coord
    )
    world_dir = view3d_utils.region_2d_to_vector_3d(
        region, rv3d, coord
    ).normalized()

    inv = source_obj.matrix_world.inverted()
    local_origin = inv @ world_origin
    local_dir = (inv.to_3x3() @ world_dir).normalized()

    hit, location, normal, face_index = source_obj.ray_cast(
        local_origin,
        local_dir,
    )

    if not hit or face_index < 0:
        return None

    return _pattern_make_anchor_from_hit(
        source_obj,
        face_index,
        location,
    )



def _pattern_source_seam_segments(source_obj):
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
            pass

    try:
        source_obj.update_from_editmode()
    except Exception:
        pass

    for edge in source_obj.data.edges:
        if not edge.use_seam:
            continue
        a = mw @ source_obj.data.vertices[edge.vertices[0]].co
        b = mw @ source_obj.data.vertices[edge.vertices[1]].co
        result.append((a, b))

    return result


def _pattern_item_color(item):
    value = item.get("color", [0.0, 0.0, 0.0])
    try:
        return (
            max(0.0, min(1.0, float(value[0]))),
            max(0.0, min(1.0, float(value[1]))),
            max(0.0, min(1.0, float(value[2]))),
        )
    except Exception:
        return (0.0, 0.0, 0.0)


def _pattern_edge_adjacent_polygons(mesh, edge):
    key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
    result = []

    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            if tuple(sorted(edge_key)) == key:
                result.append(poly)
                break

    return result


def _pattern_source_notch_segment(context, source_obj, item):
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

    polys = _pattern_edge_adjacent_polygons(source_obj.data, edge)
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

    length = _mm_to_bu(
        context.scene,
        float(getattr(context.scene, "pattern_helper_notch_length_mm", 6.0))
    )

    p1 = p_world - across * length * 0.5
    p2 = p_world + across * length * 0.5
    return p1, p2


def _pattern_flat_edge_faces(unfold_obj):
    mesh = unfold_obj.data
    lookup = {
        tuple(sorted((int(e.vertices[0]), int(e.vertices[1])))): int(e.index)
        for e in mesh.edges
    }
    result = {int(e.index): [] for e in mesh.edges}

    for poly in mesh.polygons:
        for key in poly.edge_keys:
            idx = lookup.get(tuple(sorted(key)))
            if idx is not None:
                result[idx].append(int(poly.index))

    return result


def _pattern_flat_edge_source_indices(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("pattern_helper_flat_edge_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def _pattern_flat_vertex_source_indices(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("pattern_helper_flat_vertex_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []




def _pattern_invalidate_layout_cache():
    global _pattern_auto_island_cache
    global _pattern_cache_epoch
    global _pattern_draw_cache

    _pattern_cache_epoch += 1
    _pattern_auto_island_cache = {
        "key": None,
        "records": None,
        "face_to_island": None,
        "adjacency": None,
    }
    _pattern_draw_cache = {}
    _tag_redraw()


def _pattern_flat_face_source_indices_local(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("pattern_helper_flat_face_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def _pattern_alpha_label(index):
    index = int(index)
    value = ""
    while True:
        index, rem = divmod(index, 26)
        value = chr(ord('A') + rem) + value
        if index == 0:
            break
        index -= 1
    return value


def _pattern_island_label(scene, index):
    style = str(
        getattr(scene, "pattern_helper_island_id_style", "ALPHA")
    )
    if style == "NUMBER":
        return str(int(index) + 1)
    return _pattern_alpha_label(index)


def _pattern_island_signature(unfold_obj):
    mesh = unfold_obj.data
    sx = 0.0
    sy = 0.0
    s2 = 0.0

    for vertex in mesh.vertices:
        x = float(vertex.co.x)
        y = float(vertex.co.y)
        sx += x
        sy += y
        s2 += x * x + y * y

    return (
        unfold_obj.name,
        len(mesh.vertices),
        len(mesh.edges),
        len(mesh.polygons),
        round(sx, 6),
        round(sy, 6),
        round(s2, 6),
    )



def _pattern_point_in_island_2d(mesh, face_indices, point):
    """2D point-in-island test using the flattened mesh polygons."""
    x = float(point.x)
    y = float(point.y)

    for face_index in face_indices:
        if not (0 <= int(face_index) < len(mesh.polygons)):
            continue

        poly = mesh.polygons[int(face_index)]
        verts = [
            mesh.vertices[int(vi)].co
            for vi in poly.vertices
        ]
        if len(verts) < 3:
            continue

        inside = False
        j = len(verts) - 1

        for i in range(len(verts)):
            xi = float(verts[i].x)
            yi = float(verts[i].y)
            xj = float(verts[j].x)
            yj = float(verts[j].y)

            crosses = (
                (yi > y) != (yj > y)
                and x < (
                    (xj - xi) * (y - yi)
                    / ((yj - yi) if abs(yj - yi) > 1.0e-12 else 1.0e-12)
                    + xi
                )
            )

            if crosses:
                inside = not inside

            j = i

        if inside:
            return True

    return False



def _pattern_point_segment_distance_2d(point, a, b):
    """Distance from a 2D point to a line segment in flat local space."""
    px = float(point.x)
    py = float(point.y)
    ax = float(a.x)
    ay = float(a.y)
    bx = float(b.x)
    by = float(b.y)

    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy

    if denom <= 1.0e-20:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / denom
    t = max(0.0, min(1.0, t))

    qx = ax + dx * t
    qy = ay + dy * t
    return math.hypot(px - qx, py - qy)


def _pattern_island_boundary_segments(mesh, face_indices):
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


def _pattern_id_footprint_inside(
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

    return all(
        _pattern_point_in_island_2d(
            mesh,
            face_indices,
            center + offset,
        )
        for offset in offsets
    )


def _pattern_island_id_safe_position(
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
    half_h = _mm_to_bu(
        context.scene,
        max(1.0, float(size_mm) * 0.62),
    )
    half_w = _mm_to_bu(
        context.scene,
        max(1.0, float(size_mm) * (0.42 * char_count + 0.10)),
    )

    boundary = _pattern_island_boundary_segments(
        mesh,
        face_indices,
    )

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
        _mm_to_bu(context.scene, 12.0),
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
        if not _pattern_point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        if not _pattern_id_footprint_inside(
            mesh,
            face_indices,
            candidate,
            angle,
            half_w,
            half_h,
        ):
            continue

        if boundary:
            clearance = min(
                _pattern_point_segment_distance_2d(
                    candidate,
                    a,
                    b,
                )
                for a, b in boundary
            )
        else:
            clearance = 0.0

        # Prefer clear open area first. For similarly safe candidates, give a
        # smaller bonus to lateral separation from the center arrow line.
        lateral = abs((candidate - center).dot(side))
        lateral_cap = _mm_to_bu(
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
        if not _pattern_point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        if boundary:
            clearance = min(
                _pattern_point_segment_distance_2d(
                    candidate,
                    a,
                    b,
                )
                for a, b in boundary
            )
        else:
            clearance = 0.0

        fallback.append((clearance, candidate.copy()))

    if fallback:
        fallback.sort(key=lambda item: item[0], reverse=True)
        return fallback[0][1]

    # Absolute last resort. Normally unreachable unless the island data is bad.
    return center


def _pattern_connection_label_inside_position(
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
    half_h = _mm_to_bu(
        context.scene,
        max(0.8, float(size_mm) * 0.58),
    )
    half_w = _mm_to_bu(
        context.scene,
        max(0.8, float(size_mm) * 0.48),
    )

    # Start just inside the seam, then walk inward until all corners fit.
    start_offset = half_h + _mm_to_bu(context.scene, 0.5)
    step = _mm_to_bu(
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
            _pattern_point_in_island_2d(
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


def _pattern_auto_island_metadata(context, source_obj, unfold_obj):
    """Stable island IDs + seam-neighbor labels.

    IDs are sorted by the minimum original source-face index, so moving layout
    islands does not randomly renumber them.
    """
    global _pattern_auto_island_cache

    if (
        source_obj is None
        or unfold_obj is None
        or unfold_obj.type != 'MESH'
    ):
        return [], {}, {}

    scene = context.scene
    cache_key = (
        unfold_obj.name,
        int(_pattern_cache_epoch),
        len(unfold_obj.data.vertices),
        len(unfold_obj.data.edges),
        len(unfold_obj.data.polygons),
        str(getattr(scene, "pattern_helper_island_id_style", "ALPHA")),
    )

    if _pattern_auto_island_cache.get("key") == cache_key:
        return (
            _pattern_auto_island_cache["records"],
            _pattern_auto_island_cache["face_to_island"],
            _pattern_auto_island_cache["adjacency"],
        )

    mesh = unfold_obj.data
    face_source = _pattern_flat_face_source_indices_local(unfold_obj)

    vert_to_faces = {i: set() for i in range(len(mesh.vertices))}
    for poly in mesh.polygons:
        for vi in poly.vertices:
            vert_to_faces[int(vi)].add(int(poly.index))

    raw_records = []

    for verts in _get_face_islands(mesh):
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
        item["label"] = _pattern_island_label(scene, index)
        records.append(item)

        for face_index in item["faces"]:
            face_to_island[int(face_index)] = index

    # Build one neighbor label per island-pair connection.
    flat_edge_source = _pattern_flat_edge_source_indices(unfold_obj)
    edge_faces = _pattern_flat_edge_faces(unfold_obj)

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
                "pattern_helper_island_id_size_mm",
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

        pos = _pattern_connection_label_inside_position(
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

    _pattern_auto_island_cache = {
        "key": cache_key,
        "records": records,
        "face_to_island": face_to_island,
        "adjacency": adjacency,
    }

    return records, face_to_island, adjacency


def _pattern_readable_edge_angle(angle):
    """Keep edge text parallel but avoid upside-down baseline where possible."""
    while angle > math.pi:
        angle -= math.tau
    while angle <= -math.pi:
        angle += math.tau

    if angle > math.pi * 0.5:
        angle -= math.pi
    elif angle < -math.pi * 0.5:
        angle += math.pi

    return angle


def _pattern_compute_auto_flat_oriented_text_items(
    context,
    source_obj,
    unfold_obj,
):
    records, _face_to_island, adjacency = _pattern_auto_island_metadata(
        context,
        source_obj,
        unfold_obj,
    )

    if not records:
        return []

    scene = context.scene
    size_mm = float(
        getattr(scene, "pattern_helper_island_id_size_mm", 8.0)
    )
    color = tuple(
        float(v)
        for v in getattr(
            scene,
            "pattern_helper_island_id_color",
            (0.0, 0.0, 0.0),
        )
    )
    mw = unfold_obj.matrix_world
    result = []

    for record in records:
        center = record["center_local"].copy()
        direction = _pattern_auto_arrow_direction_for_record(
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
        label_pos = _pattern_island_id_safe_position(
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

            edge_angle = _pattern_readable_edge_angle(
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


def _pattern_auto_flat_oriented_text_items(
    context,
    source_obj,
    unfold_obj,
):
    global _pattern_draw_cache

    scene = context.scene
    key = (
        "auto_text",
        int(_pattern_cache_epoch),
        source_obj.name if source_obj else "",
        unfold_obj.name if unfold_obj else "",
        tuple(round(float(v), 7) for row in unfold_obj.matrix_world for v in row),
        str(getattr(scene, "pattern_helper_island_id_style", "ALPHA")),
        round(float(getattr(scene, "pattern_helper_island_id_size_mm", 8.0)), 4),
        tuple(
            round(float(v), 4)
            for v in getattr(
                scene,
                "pattern_helper_island_id_color",
                (0.0, 0.0, 0.0),
            )
        ),
        str(getattr(scene, "pattern_helper_arrow_up_axis", "Z")),
    )

    cached = _pattern_draw_cache.get(key)
    if cached is not None:
        return cached

    result = _pattern_compute_auto_flat_oriented_text_items(
        context,
        source_obj,
        unfold_obj,
    )
    _pattern_draw_cache[key] = result
    return result


def _pattern_auto_flat_id_text_items(context, source_obj, unfold_obj):
    # Legacy 4-tuple wrapper for old callers.
    return [
        (text, pos, size_mm, color)
        for text, pos, size_mm, color, _angle, _edge_locked
        in _pattern_auto_flat_oriented_text_items(
            context,
            source_obj,
            unfold_obj,
        )
    ]




def _pattern_auto_source_id_text_items(context, source_obj, unfold_obj):
    records, _face_to_island, _adjacency = _pattern_auto_island_metadata(
        context,
        source_obj,
        unfold_obj,
    )

    if not records:
        return []

    scene = context.scene
    size_mm = float(
        getattr(scene, "pattern_helper_island_id_size_mm", 8.0)
    )
    color = tuple(
        float(v)
        for v in getattr(
            scene,
            "pattern_helper_island_id_color",
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
            center = center + normal * _mm_to_bu(scene, 0.6)

        result.append((
            record["label"],
            mw @ center,
            size_mm,
            color,
        ))

    return result


def _pattern_auto_up_vector(scene):
    """Return the selected Blender GLOBAL/WORLD positive axis."""
    mode = _pattern_sanitize_arrow_axis(scene)

    if mode == "X":
        return Vector((1.0, 0.0, 0.0))
    if mode == "Y":
        return Vector((0.0, 1.0, 0.0))

    return Vector((0.0, 0.0, 1.0))


def _pattern_auto_up_vector_source_local(scene, source_obj):
    """Convert the selected Blender world axis into source local space.

    The UI choice follows Blender's navigation gizmo/global axes, while the
    unfold correspondence math operates on source mesh local coordinates.
    """
    world_up = _pattern_auto_up_vector(scene)

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


def _pattern_solve_2d_gradient(samples):
    """Fit scalar ~= ax*x + ay*y + c and return 2D gradient (ax, ay).

    samples: [(x, y, scalar), ...]
    """
    if len(samples) < 3:
        return None

    n = float(len(samples))
    sx = sum(s[0] for s in samples)
    sy = sum(s[1] for s in samples)
    sz = sum(s[2] for s in samples)

    sxx = sum(s[0] * s[0] for s in samples)
    syy = sum(s[1] * s[1] for s in samples)
    sxy = sum(s[0] * s[1] for s in samples)
    sxz = sum(s[0] * s[2] for s in samples)
    syz = sum(s[1] * s[2] for s in samples)

    # Solve normal equations:
    # [sxx sxy sx] [ax]   [sxz]
    # [sxy syy sy] [ay] = [syz]
    # [sx  sy  n ] [ c]   [ sz ]
    m = [
        [sxx, sxy, sx, sxz],
        [sxy, syy, sy, syz],
        [sx,  sy,  n,  sz ],
    ]

    # Gaussian elimination with partial pivoting.
    for col in range(3):
        pivot = max(
            range(col, 3),
            key=lambda row: abs(m[row][col]),
        )
        if abs(m[pivot][col]) <= 1e-12:
            return None

        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]

        div = m[col][col]
        for j in range(col, 4):
            m[col][j] /= div

        for row in range(3):
            if row == col:
                continue
            factor = m[row][col]
            if abs(factor) <= 1e-20:
                continue
            for j in range(col, 4):
                m[row][j] -= factor * m[col][j]

    return Vector((m[0][3], m[1][3], 0.0))



def _pattern_solve_3x3_regularized(matrix, rhs, ridge=1e-9):
    m = [
        [
            float(matrix[row][col])
            + (float(ridge) if row == col else 0.0)
            for col in range(3)
        ]
        + [float(rhs[row])]
        for row in range(3)
    ]

    for col in range(3):
        pivot = max(
            range(col, 3),
            key=lambda row: abs(m[row][col]),
        )
        if abs(m[pivot][col]) <= 1e-14:
            return None

        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]

        div = m[col][col]
        for j in range(col, 4):
            m[col][j] /= div

        for row in range(3):
            if row == col:
                continue

            factor = m[row][col]
            if abs(factor) <= 1e-20:
                continue

            for j in range(col, 4):
                m[row][j] -= factor * m[col][j]

    return Vector((m[0][3], m[1][3], m[2][3]))


def _pattern_auto_arrow_direction_for_record(
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
    flat_to_source = _pattern_flat_vertex_source_indices(unfold_obj)
    up = _pattern_auto_up_vector_source_local(
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

        row_x = _pattern_solve_3x3_regularized(
            normal,
            rhs_x,
            ridge,
        )
        row_y = _pattern_solve_3x3_regularized(
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

    direction = _pattern_solve_2d_gradient(samples)

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





def _pattern_arrow_geometry_local(center, direction, length, head):
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


def _pattern_arrow_fits_island(
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
    start, end, head_a, head_b = _pattern_arrow_geometry_local(
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

    if not all(
        _pattern_point_in_island_2d(mesh, face_indices, p)
        for p in sample_points
    ):
        return False

    # Require a little boundary clearance so the line is not visually clipped.
    # Boundary may be precomputed by the caller; rebuilding it for every
    # candidate is extremely expensive on dense pattern pieces.
    if boundary is None:
        boundary = _pattern_island_boundary_segments(mesh, face_indices)

    if boundary and safety_margin > 0.0:
        for p in sample_points:
            clearance = min(
                _pattern_point_segment_distance_2d(p, a, b)
                for a, b in boundary
            )
            if clearance < safety_margin:
                return False

    return True


def _pattern_safe_arrow_placement(
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
    boundary = _pattern_island_boundary_segments(mesh, face_indices)

    # Visual margin around the line. Keep it small so narrow strips still work.
    safety_margin = _mm_to_bu(
        context.scene,
        max(
            0.35,
            float(getattr(
                context.scene,
                "pattern_helper_arrow_thickness_mm",
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
        _mm_to_bu(context.scene, 14.0),
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
        if not _pattern_point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        if not _pattern_arrow_fits_island(
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

        if boundary:
            clearance = min(
                _pattern_point_segment_distance_2d(
                    candidate,
                    a,
                    b,
                )
                for a, b in boundary
            )
        else:
            clearance = 0.0

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
    min_length = _mm_to_bu(context.scene, 4.0)
    best = None

    for ratio in (0.80, 0.60, 0.45, 0.30):
        length = max(min_length, requested_length * ratio)
        head = min(requested_head, max(length * 0.34, min_length * 0.45))

        for candidate in unique:
            if not _pattern_point_in_island_2d(
                mesh,
                face_indices,
                candidate,
            ):
                continue

            if not _pattern_arrow_fits_island(
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

            if boundary:
                clearance = min(
                    _pattern_point_segment_distance_2d(
                        candidate,
                        a,
                        b,
                    )
                    for a, b in boundary
                )
            else:
                clearance = 0.0

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
        if not _pattern_point_in_island_2d(
            mesh,
            face_indices,
            candidate,
        ):
            continue

        if boundary:
            clearance = min(
                _pattern_point_segment_distance_2d(
                    candidate,
                    a,
                    b,
                )
                for a, b in boundary
            )
        else:
            clearance = 0.0

        fallback.append((clearance, candidate.copy()))

    if fallback:
        fallback.sort(key=lambda item: item[0], reverse=True)
        return fallback[0][1], min_length, min(requested_head, min_length * 0.35)

    return old_center, min_length, min(requested_head, min_length * 0.35)


def _pattern_compute_auto_arrow_segments(context, source_obj, unfold_obj):
    if str(getattr(context.scene, "pattern_helper_arrow_mode", "AUTO")) != "AUTO":
        return []

    records, _face_to_island, _adjacency = _pattern_auto_island_metadata(
        context,
        source_obj,
        unfold_obj,
    )
    if not records:
        return []

    flat_to_source = _pattern_flat_vertex_source_indices(unfold_obj)
    scene = context.scene
    mw = unfold_obj.matrix_world

    requested_length = _mm_to_bu(
        scene,
        float(getattr(scene, "pattern_helper_auto_arrow_length_mm", 24.0)),
    )
    head = _mm_to_bu(
        scene,
        float(getattr(scene, "pattern_helper_arrow_head_mm", 8.0)),
    )
    thickness = float(
        getattr(scene, "pattern_helper_arrow_thickness_mm", 0.8)
    )
    color = tuple(
        float(v)
        for v in getattr(
            scene,
            "pattern_helper_arrow_color",
            (0.0, 0.0, 0.0),
        )
    )

    result = []

    for record in records:
        direction = _pattern_auto_arrow_direction_for_record(
            context,
            source_obj,
            unfold_obj,
            record,
        )

        # Keep the selected world-axis direction, but place the arrow in an
        # actually usable part of the island. Concave/ring shapes can have an
        # empty geometric center, so endpoints alone are not enough.
        center, length, safe_head = _pattern_safe_arrow_placement(
            context,
            unfold_obj.data,
            record,
            direction,
            requested_length,
            head,
        )

        start, end, head_a, head_b = _pattern_arrow_geometry_local(
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




def _pattern_auto_arrow_segments(context, source_obj, unfold_obj):
    global _pattern_draw_cache

    scene = context.scene
    key = (
        "auto_arrows",
        int(_pattern_cache_epoch),
        source_obj.name if source_obj else "",
        unfold_obj.name if unfold_obj else "",
        tuple(round(float(v), 7) for row in unfold_obj.matrix_world for v in row),
        str(getattr(scene, "pattern_helper_arrow_mode", "AUTO")),
        str(getattr(scene, "pattern_helper_arrow_up_axis", "Z")),
        round(float(getattr(scene, "pattern_helper_auto_arrow_length_mm", 24.0)), 4),
        round(float(getattr(scene, "pattern_helper_arrow_head_mm", 8.0)), 4),
        round(float(getattr(scene, "pattern_helper_arrow_thickness_mm", 0.8)), 4),
        tuple(
            round(float(v), 4)
            for v in getattr(
                scene,
                "pattern_helper_arrow_color",
                (0.0, 0.0, 0.0),
            )
        ),
    )

    cached = _pattern_draw_cache.get(key)
    if cached is not None:
        return cached

    result = _pattern_compute_auto_arrow_segments(
        context,
        source_obj,
        unfold_obj,
    )
    _pattern_draw_cache[key] = result
    return result


def _pattern_flat_notch_segments(context, source_obj, unfold_obj, item):
    try:
        source_edge_index = int(item.get("edge", -1))
        source_fraction = float(item.get("t", 0.5))
    except Exception:
        return []

    if not (0 <= source_edge_index < len(source_obj.data.edges)):
        return []

    if not bool(source_obj.data.edges[source_edge_index].use_seam):
        return []

    flat_edge_source = _pattern_flat_edge_source_indices(unfold_obj)
    flat_vert_source = _pattern_flat_vertex_source_indices(unfold_obj)

    if not flat_edge_source or not flat_vert_source:
        return []

    source_edge = source_obj.data.edges[source_edge_index]
    source_v0 = int(source_edge.vertices[0])
    source_v1 = int(source_edge.vertices[1])

    face_map = _pattern_flat_edge_faces(unfold_obj)
    mesh = unfold_obj.data
    mw = unfold_obj.matrix_world

    length = _mm_to_bu(
        context.scene,
        float(getattr(context.scene, "pattern_helper_notch_length_mm", 6.0))
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



def _pattern_visible_smooth_for_unfold(unfold_obj):
    if unfold_obj is None:
        return None

    for candidate in bpy.data.objects:
        if (
            candidate.type == 'CURVE'
            and bool(candidate.get("unfold_helper_smooth_generated", False))
            and candidate.get("unfold_helper_smooth_source", "") == unfold_obj.name
            and not candidate.hide_viewport
        ):
            return candidate

    return None


def _pattern_nearest_point_on_xy_segment(point, a, b):
    ab = b - a
    denom = ab.length_squared

    if denom <= 1e-20:
        return a.copy(), 0.0

    t = (point - a).dot(ab) / denom
    t = max(0.0, min(1.0, t))
    return a + ab * t, t


def _pattern_smooth_notch_segments(context, source_obj, unfold_obj, item):
    """Project poly-notch positions to nearest visible smooth outline.

    The original seam correspondence still determines WHICH location the notch
    belongs to. Only the final boundary point/tangent is moved to the smooth
    finishing curve.
    """
    poly_segments = _pattern_flat_notch_segments(
        context,
        source_obj,
        unfold_obj,
        item,
    )

    if not poly_segments:
        return []

    smooth_obj = _pattern_visible_smooth_for_unfold(unfold_obj)
    if smooth_obj is None:
        return poly_segments

    sampled = _smooth_curve_segments_world_xy(
        smooth_obj,
        samples_per_segment=24,
    )
    if not sampled:
        return poly_segments

    notch_length = _mm_to_bu(
        context.scene,
        float(
            getattr(
                context.scene,
                "pattern_helper_notch_length_mm",
                6.0,
            )
        ),
    )

    result = []

    for poly_a, poly_b in poly_segments:
        base = Vector((poly_a.x, poly_a.y, 0.0))
        inward = Vector(
            (
                poly_b.x - poly_a.x,
                poly_b.y - poly_a.y,
                0.0,
            )
        )

        if inward.length <= 1e-12:
            continue
        inward.normalize()

        best_point = None
        best_tangent = None
        best_dist = None

        for (ax, ay), (bx, by) in sampled:
            sa = Vector((ax, ay, 0.0))
            sb = Vector((bx, by, 0.0))
            nearest, _t = _pattern_nearest_point_on_xy_segment(
                base,
                sa,
                sb,
            )
            dist = (nearest - base).length

            if best_dist is None or dist < best_dist:
                tangent = sb - sa
                if tangent.length <= 1e-12:
                    continue

                best_dist = dist
                best_point = nearest
                best_tangent = tangent.normalized()

        if best_point is None or best_tangent is None:
            result.append((poly_a, poly_b))
            continue

        perp = Vector(
            (-best_tangent.y, best_tangent.x, 0.0)
        )

        # Keep the notch pointing to the same side as the original poly notch.
        if perp.dot(inward) < 0.0:
            perp.negate()

        end = best_point + perp * notch_length
        result.append((best_point, end))

    return result



def _pattern_source_colored_segments(context, source_obj):
    segments = []
    size = _mm_to_bu(context.scene, 8.0)
    mw = source_obj.matrix_world
    normal_matrix = mw.to_3x3()

    for item in _pattern_get_annotations(source_obj):
        kind = item.get("type")
        color = _pattern_item_color(item)

        if kind == "notch_edge":
            seg = _pattern_source_notch_segment(context, source_obj, item)
            if seg is not None:
                notch_thickness = float(
                    getattr(
                        context.scene,
                        "pattern_helper_notch_thickness_mm",
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
                getattr(context.scene, "pattern_helper_arrow_mode", "AUTO")
            ) == "NONE":
                continue

            pa = _pattern_anchor_point_source_local(
                source_obj, item.get("a", {})
            )
            pb = _pattern_anchor_point_source_local(
                source_obj, item.get("b", {})
            )
            if pa is None or pb is None:
                continue

            a = mw @ pa
            b = mw @ pb
            thickness = float(item.get("thickness_mm", 0.8))
            head = _mm_to_bu(
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


def _pattern_compute_flat_colored_segments(context, source_obj, unfold_obj):
    segments = []
    size = _mm_to_bu(context.scene, 8.0)
    mw = unfold_obj.matrix_world

    for item in _pattern_get_annotations(source_obj):
        kind = item.get("type")
        color = _pattern_item_color(item)

        if kind == "notch_edge":
            if context.scene.get("unfold_helper_display_mode", "POLY") == "SMOOTH":
                notch_segments = _pattern_smooth_notch_segments(
                    context,
                    source_obj,
                    unfold_obj,
                    item,
                )
            else:
                notch_segments = _pattern_flat_notch_segments(
                    context,
                    source_obj,
                    unfold_obj,
                    item,
                )

            notch_thickness = float(
                getattr(
                    context.scene,
                    "pattern_helper_notch_thickness_mm",
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
                getattr(context.scene, "pattern_helper_arrow_mode", "AUTO")
            ) == "NONE":
                continue

            pa = _pattern_anchor_point_flat_local(
                unfold_obj, item.get("a", {})
            )
            pb = _pattern_anchor_point_flat_local(
                unfold_obj, item.get("b", {})
            )
            if pa is None or pb is None:
                continue

            a = mw @ pa
            b = mw @ pb
            thickness = float(item.get("thickness_mm", 0.8))
            head = _mm_to_bu(
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

    segments.extend(
        _pattern_auto_arrow_segments(
            context,
            source_obj,
            unfold_obj,
        )
    )

    return segments


def _pattern_flat_colored_segments(context, source_obj, unfold_obj):
    global _pattern_draw_cache

    annotations_raw = source_obj.get(
        _PATTERN_ANNOTATION_PROP,
        "[]",
    )
    scene = context.scene

    key = (
        "flat_segments",
        int(_pattern_cache_epoch),
        source_obj.name if source_obj else "",
        unfold_obj.name if unfold_obj else "",
        tuple(round(float(v), 7) for row in unfold_obj.matrix_world for v in row),
        annotations_raw,
        str(getattr(scene, "pattern_helper_arrow_mode", "AUTO")),
        round(float(getattr(scene, "pattern_helper_notch_length_mm", 6.0)), 4),
        str(scene.get("unfold_helper_display_mode", "POLY")),
    )

    cached = _pattern_draw_cache.get(key)
    if cached is not None:
        return cached

    result = _pattern_compute_flat_colored_segments(
        context,
        source_obj,
        unfold_obj,
    )
    _pattern_draw_cache[key] = result
    return result


def _pattern_source_text_items(source_obj):
    result = []
    mw = source_obj.matrix_world

    for item in _pattern_get_annotations(source_obj):
        if item.get("type") not in {"number", "text"}:
            continue

        p = _pattern_anchor_point_source_local(
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
            _pattern_item_color(item),
        ))

    context = bpy.context
    unfold_obj = _pattern_unfold_for_source(source_obj)
    if (
        context is not None
        and unfold_obj is not None
        and bool(
            getattr(
                context.scene,
                "pattern_helper_auto_island_ids",
                True,
            )
        )
    ):
        result.extend(
            _pattern_auto_source_id_text_items(
                context,
                source_obj,
                unfold_obj,
            )
        )

    return result


def _pattern_flat_text_items(source_obj, unfold_obj):
    result = []

    for item in _pattern_get_annotations(source_obj):
        if item.get("type") not in {"number", "text"}:
            continue

        p = _pattern_anchor_point_flat_local(
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
            _pattern_item_color(item),
        ))

    return result


# Compatibility wrappers used by print-preview code.
def _pattern_flat_mark_segments(context, source_obj, unfold_obj):
    return [
        (a, b)
        for a, b, _color, _width in _pattern_flat_colored_segments(
            context, source_obj, unfold_obj
        )
    ]


def _pattern_source_mark_segments(context, source_obj):
    return [
        (a, b)
        for a, b, _color, _width in _pattern_source_colored_segments(
            context, source_obj
        )
    ]


def _pattern_flat_number_positions(source_obj, unfold_obj):
    return [
        (text, pos)
        for text, pos, _size, _color in _pattern_flat_text_items(
            source_obj, unfold_obj
        )
        if text.isdigit()
    ]


def _pattern_source_number_positions(source_obj):
    return [
        (text, pos)
        for text, pos, _size, _color in _pattern_source_text_items(source_obj)
        if text.isdigit()
    ]




def _pattern_world_point_visible(context, target_obj, world_point):
    """Visibility test for source annotations, independent of viewport X-ray."""
    if (
        target_obj is None
        or context.region is None
        or context.space_data is None
    ):
        return True

    rv3d = getattr(context.space_data, "region_3d", None)
    if rv3d is None:
        return True

    try:
        screen = view3d_utils.location_3d_to_region_2d(
            context.region,
            rv3d,
            world_point,
        )
        if screen is None:
            return False

        origin = view3d_utils.region_2d_to_origin_3d(
            context.region,
            rv3d,
            screen,
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            context.region,
            rv3d,
            screen,
        ).normalized()

        depsgraph = context.evaluated_depsgraph_get()
        hit, location, normal, face_index, hit_obj, matrix = context.scene.ray_cast(
            depsgraph,
            origin,
            direction,
        )

        if not hit or hit_obj is None:
            return False

        original = (
            hit_obj.original
            if hasattr(hit_obj, "original")
            else hit_obj
        )

        if original != target_obj:
            return False

        tolerance = _mm_to_bu(context.scene, 15.0)
        return (Vector(location) - Vector(world_point)).length <= tolerance

    except Exception:
        return True


def _draw_pattern_marks_3d():
    context = bpy.context
    if context is None:
        return
    if _pattern_manual_layout_active(context.scene):
        return
    if context.area is None or context.area.type != 'VIEW_3D':
        return

    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.depth_test_set('LESS_EQUAL')

        source = _pattern_source_object_from_context(context)

        # Fallback: active generated pattern can still resolve its source.
        if source is None:
            unfold_fallback = _resolve_unfold_mesh_for_layout(context)
            if unfold_fallback is not None:
                source_name = unfold_fallback.get("unfold_helper_source", "")
                source = bpy.data.objects.get(source_name)

        if source is not None:
            if (
                not bool(source.hide_get())
                and not bool(source.hide_viewport)
                and _pattern_island_highlight.get("source", "") == source.name
                and _pattern_island_highlight.get("source_faces")
            ):
                wanted = set(
                    int(v)
                    for v in _pattern_island_highlight.get(
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

                unfold_h = _pattern_unfold_for_source(source)
                if unfold_h is not None and not unfold_h.hide_viewport:
                    mapping = _pattern_flat_face_source_map(unfold_h)
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
                context.scene.get("pattern_helper_seam_source", "")
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
                _pattern_source_seam_segments(source)
                if show_seam_overlay
                else []
            )
            source_colored = (
                _pattern_source_colored_segments(
                    context,
                    source,
                )
                if (
                    source_visible
                    and not bool(
                        getattr(
                            context.scene,
                            "pattern_helper_lightweight_view",
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
                gpu.state.line_width_set(
                            max(
                                1.0,
                                min(
                                    12.0,
                                    float(
                                        getattr(
                                            context.scene,
                                            "pattern_helper_notch_thickness_mm",
                                            0.6,
                                        )
                                    ) * 4.0,
                                ),
                            )
                        )
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
            unfold = _pattern_unfold_for_source(source)
            show_transferred = (
                unfold is not None
                and (
                    not unfold.hide_viewport
                    or context.scene.get(
                        "unfold_helper_display_mode",
                        "POLY",
                    ) == "SMOOTH"
                )
            )

            if show_transferred:
                flat_colored = _pattern_flat_colored_segments(
                    context,
                    source,
                    unfold,
                )

                for pa, pb, color, width_mm in flat_colored:
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
            # Live placement preview on source.
            # ------------------------------------------------------
            preview_mode = _pattern_active_tool(context.scene)
            preview_source = bpy.data.objects.get(
                _pattern_live_preview.get("source", "")
            )

            if preview_source is source:
                preview_color = _pattern_current_color(
                    context.scene,
                    preview_mode,
                )

                if (
                    preview_mode == "NOTCH"
                    and int(_pattern_live_preview.get("notch_edge", -1)) >= 0
                ):
                    item = {
                        "edge": int(_pattern_live_preview["notch_edge"]),
                        "t": float(_pattern_live_preview["notch_t"]),
                    }
                    seg = _pattern_source_notch_segment(
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
                                            "pattern_helper_notch_thickness_mm",
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
                                "unfold_helper_display_mode",
                                "POLY",
                            ) == "SMOOTH"
                        )
                    ):
                        preview_item = {
                            "type": "notch_edge",
                            "edge": int(_pattern_live_preview["notch_edge"]),
                            "t": float(_pattern_live_preview["notch_t"]),
                        }
                        if context.scene.get(
                            "unfold_helper_display_mode",
                            "POLY",
                        ) == "SMOOTH":
                            preview_notches = _pattern_smooth_notch_segments(
                                context,
                                source,
                                unfold,
                                preview_item,
                            )
                        else:
                            preview_notches = _pattern_flat_notch_segments(
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
                    start_anchor = _pattern_live_preview.get("arrow_start")
                    hover_anchor = _pattern_live_preview.get("hover_anchor")

                    if start_anchor is not None and hover_anchor is not None:
                        wa = _pattern_preview_anchor_world(
                            source,
                            start_anchor,
                        )
                        wb = _pattern_preview_anchor_world(
                            source,
                            hover_anchor,
                        )

                        if wa is not None and wb is not None:
                            direction = wb - wa
                            if direction.length > 1e-12:
                                direction.normalize()
                                head = _mm_to_bu(
                                    context.scene,
                                    context.scene.pattern_helper_arrow_head_mm,
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
                                            context.scene.pattern_helper_arrow_thickness_mm
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
            pass





def _pattern_draw_direction_arrow_overlay(context):
    if _pattern_manual_layout_active(context.scene):
        return

    """Large light-blue viewport-only arrow for the chosen Blender world axis."""
    scene = context.scene

    if not bool(
        getattr(scene, "pattern_helper_show_direction_arrow", True)
    ):
        return

    if str(
        getattr(scene, "pattern_helper_arrow_mode", "AUTO")
    ) == "NONE":
        return

    source = _pattern_source_object_from_context(context)

    if source is None:
        unfold = _resolve_unfold_mesh_for_layout(context)
        if unfold is not None:
            source = bpy.data.objects.get(
                unfold.get("unfold_helper_source", "")
            )

    if source is None:
        return

    region = context.region
    rv3d = getattr(context.space_data, "region_3d", None)

    if region is None or rv3d is None:
        return

    # Match Blender's navigation gizmo exactly:
    # X/Y/Z here are WORLD axes, independent of source object rotation.
    world_up = _pattern_auto_up_vector(scene)

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
        pass
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')
        except Exception:
            pass


def _pattern_arrow_hud_text(scene):
    mode = str(
        getattr(
            scene,
            "pattern_helper_arrow_mode",
            "AUTO",
        )
    )

    if mode == "NONE":
        return "矢印：なし"

    if mode == "CUSTOM":
        return "矢印：カスタム配置"

    axis = _pattern_sanitize_arrow_axis(scene)

    if axis == "X":
        return "↑ 矢印基準：元モデル X+"
    if axis == "Y":
        return "↑ 矢印基準：元モデル Y+"

    return "↑ 矢印基準：元モデル Z+（正面投影が0なら点表示）"


def _pattern_draw_arrow_hud(context, font_id=0):
    if _pattern_manual_layout_active(context.scene):
        return

    """Viewport-only legend. Never becomes geometry or PNG content."""
    try:
        region = context.region
        if region is None:
            return

        text = _pattern_arrow_hud_text(context.scene)

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
                "pattern_helper_arrow_mode",
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
        pass


def _draw_pattern_text_2d():
    context = bpy.context

    if context is not None and _pattern_manual_layout_active(context.scene):
        return

    if (
        context is None
        or context.area is None
        or context.area.type != 'VIEW_3D'
        or context.region is None
        or context.space_data is None
    ):
        return

    source = _pattern_source_object_from_context(context)

    if source is None:
        unfold_fallback = _resolve_unfold_mesh_for_layout(context)
        if unfold_fallback is not None:
            source_name = unfold_fallback.get("unfold_helper_source", "")
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
                pass

            blf.draw(font_id, str(text))

            try:
                blf.disable(font_id, blf.ROTATION)
            except Exception:
                pass

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
                "pattern_helper_lightweight_view",
                True,
            )
        )

        source_labels = (
            _pattern_source_text_items(source)
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
        unfold = _pattern_unfold_for_source(source)

        if (
            unfold is not None
            and (
                not unfold.hide_viewport
                or context.scene.get(
                    "unfold_helper_display_mode",
                    "POLY",
                ) == "SMOOTH"
            )
        ):
            flat_labels = _pattern_flat_text_items(
                source,
                unfold,
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
            memo_items = _pattern_flat_memo_text_items(unfold)
            selected_unfold = _pattern_selected_memo.get("unfold", "")
            selected_index = int(_pattern_selected_memo.get("index", -1))

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
                    "pattern_helper_auto_island_ids",
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
                ) in _pattern_auto_flat_oriented_text_items(
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
        preview_mode = _pattern_active_tool(context.scene)

        if preview_mode in {"NUMBER", "TEXT"}:
            preview_source = bpy.data.objects.get(
                _pattern_live_preview.get("source", "")
            )
            hover_anchor = _pattern_live_preview.get("hover_anchor")

            if (
                preview_source is source
                and hover_anchor is not None
            ):
                world_pos = _pattern_preview_anchor_world(
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
                                context.scene.pattern_helper_next_number
                            )
                            size_mm = float(
                                context.scene.pattern_helper_number_size_mm
                            )
                        else:
                            preview_text = str(
                                context.scene.pattern_helper_custom_text
                            )
                            size_mm = float(
                                context.scene.pattern_helper_text_size_mm
                            )

                        if preview_text:
                            color = _pattern_current_color(
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
        _pattern_draw_direction_arrow_overlay(context)
        _pattern_draw_arrow_hud(
            context,
            font_id,
        )

    except Exception:
        pass



def _pattern_raycast_source_detail(context, event, source_obj):
    region = context.region
    rv3d = context.space_data.region_3d
    coord = (event.mouse_region_x, event.mouse_region_y)

    world_origin = view3d_utils.region_2d_to_origin_3d(
        region,
        rv3d,
        coord,
    )
    world_dir = view3d_utils.region_2d_to_vector_3d(
        region,
        rv3d,
        coord,
    ).normalized()

    inv = source_obj.matrix_world.inverted()
    local_origin = inv @ world_origin
    local_dir = (inv.to_3x3() @ world_dir).normalized()

    hit, location, normal, face_index = source_obj.ray_cast(
        local_origin,
        local_dir,
    )

    if not hit or face_index < 0:
        return None

    anchor = _pattern_make_anchor_from_hit(
        source_obj,
        face_index,
        location,
    )
    if anchor is None:
        return None

    return anchor, location.copy(), int(face_index)


def _pattern_nearest_seam_edge(context, source_obj, local_hit):
    try:
        source_obj.update_from_editmode()
    except Exception:
        pass

    world_hit = source_obj.matrix_world @ local_hit
    best = None
    best_t = 0.5
    best_dist = None

    for edge in source_obj.data.edges:
        if not edge.use_seam:
            continue

        a = source_obj.matrix_world @ source_obj.data.vertices[edge.vertices[0]].co
        b = source_obj.matrix_world @ source_obj.data.vertices[edge.vertices[1]].co
        ab = b - a
        denom = ab.length_squared

        if denom <= 1e-20:
            continue

        t = (world_hit - a).dot(ab) / denom
        t = max(0.0, min(1.0, t))
        q = a + ab * t
        dist = (world_hit - q).length

        if best_dist is None or dist < best_dist:
            best = int(edge.index)
            best_t = float(t)
            best_dist = float(dist)

    max_dist = _mm_to_bu(context.scene, 20.0)

    if best is None or best_dist is None or best_dist > max_dist:
        return None

    return best, best_t, best_dist


def _pattern_color_value(value):
    return [
        round(float(value[0]), 5),
        round(float(value[1]), 5),
        round(float(value[2]), 5),
    ]


def _pattern_current_color(scene, mode=None):
    mode = mode or _pattern_active_tool(scene)

    if mode == "NOTCH":
        return _pattern_color_value(scene.pattern_helper_notch_color)
    if mode == "NUMBER":
        return _pattern_color_value(scene.pattern_helper_number_color)
    if mode == "TEXT":
        return _pattern_color_value(scene.pattern_helper_text_color)
    if mode == "ARROW":
        return _pattern_color_value(scene.pattern_helper_arrow_color)

    return [0.0, 0.0, 0.0]


def _pattern_event_is_view_window(context, event):
    """Return True only for clicks in the actual 3D WINDOW region.

    Modal operator context can remain bound to the WINDOW region even when the
    mouse is physically over the N-panel. Therefore use absolute window mouse
    coordinates against area.regions, not mouse_region_x/y alone.
    """
    area = context.area

    if area is None or area.type != 'VIEW_3D':
        return False

    try:
        mx = int(event.mouse_x)
        my = int(event.mouse_y)
    except Exception:
        return False

    # Any visible UI/header/tool region wins over the viewport.
    for region in area.regions:
        if region.type == 'WINDOW':
            continue

        if region.width <= 0 or region.height <= 0:
            continue

        inside = (
            region.x <= mx < region.x + region.width
            and region.y <= my < region.y + region.height
        )

        if inside:
            return False

    # Finally require the mouse to be physically inside the WINDOW region.
    for region in area.regions:
        if region.type != 'WINDOW':
            continue

        inside = (
            region.x <= mx < region.x + region.width
            and region.y <= my < region.y + region.height
        )

        if inside:
            return True

    return False



def _pattern_clear_live_preview():
    global _pattern_live_preview
    _pattern_live_preview = {
        "mode": "NONE",
        "source": "",
        "notch_edge": -1,
        "notch_t": 0.5,
        "hover_anchor": None,
        "arrow_start": None,
    }
    _tag_redraw()


def _pattern_set_preview_anchor(mode, source_obj, anchor):
    _pattern_live_preview["mode"] = mode
    _pattern_live_preview["source"] = source_obj.name if source_obj else ""
    _pattern_live_preview["hover_anchor"] = anchor
    _tag_redraw()


def _pattern_preview_anchor_world(source_obj, anchor):
    if source_obj is None or anchor is None:
        return None
    p = _pattern_anchor_point_source_local(source_obj, anchor)
    if p is None:
        return None
    return source_obj.matrix_world @ p


def _pattern_active_tool(scene):
    try:
        return str(scene.pattern_helper_active_tool)
    except Exception:
        return str(scene.get("pattern_helper_active_tool", "NONE"))


def _pattern_set_active_tool(scene, mode):
    value = str(mode)
    try:
        scene.pattern_helper_active_tool = value
    except Exception:
        scene["pattern_helper_active_tool"] = value
    _tag_redraw()


def _pattern_toggle_tool_invoke(operator, context, mode):
    source = _pattern_source_object_from_context(context)

    if source is None:
        operator.report({'WARNING'}, "元の3Dモデルを選択してください")
        return {'CANCELLED'}

    current = _pattern_active_tool(context.scene)

    if current == mode:
        _pattern_set_active_tool(context.scene, "NONE")
        _pattern_clear_live_preview()
        context.scene["pattern_helper_modal_running"] = False
        context.scene["pattern_helper_marking_finish_requested"] = False
        operator.report({'INFO'}, "マーキングツールをOFFにしました")
        return {'FINISHED'}

    _pattern_begin_marking_session(context, source)

    if mode == "NUMBER":
        context.scene.pattern_helper_next_number = int(
            context.scene.pattern_helper_number_start
        )

    _pattern_set_active_tool(context.scene, mode)
    _pattern_clear_live_preview()
    _pattern_live_preview["mode"] = mode
    _pattern_live_preview["source"] = source.name

    if not bool(context.scene.get("pattern_helper_modal_running", False)):
        operator._source_name = source.name
        operator._first_anchor = None
        operator._last_mode = mode
        context.scene["pattern_helper_modal_running"] = True
        context.window_manager.modal_handler_add(operator)
        return {'RUNNING_MODAL'}

    return {'FINISHED'}


def _pattern_modal_common(operator, context, event):
    scene = context.scene

    if bool(scene.get("pattern_helper_marking_finish_requested", False)):
        _pattern_clear_live_preview()
        scene["pattern_helper_modal_running"] = False
        return {'FINISHED'}

    mode = _pattern_active_tool(scene)

    if mode == "NONE":
        _pattern_clear_live_preview()
        scene["pattern_helper_modal_running"] = False
        operator._first_anchor = None
        return {'FINISHED'}

    if mode != getattr(operator, "_last_mode", mode):
        operator._first_anchor = None
        operator._last_mode = mode

    # N-panel, header, toolbar, etc. belong to Blender UI.
    # Never consume their mouse events.
    if event.type in {
        'LEFTMOUSE',
        'RIGHTMOUSE',
        'MIDDLEMOUSE',
        'WHEELUPMOUSE',
        'WHEELDOWNMOUSE',
    } and not _pattern_event_is_view_window(context, event):
        return {'PASS_THROUGH'}

    if event.type == 'ESC' and event.value == 'PRESS':
        _pattern_set_active_tool(scene, "NONE")
        _pattern_clear_live_preview()
        scene["pattern_helper_modal_running"] = False
        scene["pattern_helper_marking_finish_requested"] = False
        operator._first_anchor = None
        return {'FINISHED'}

    source = bpy.data.objects.get(getattr(operator, "_source_name", ""))

    # Real-time preview follows the mouse inside the actual viewport.
    if event.type == 'MOUSEMOVE':
        if source is None:
            return {'PASS_THROUGH'}

        detail = _pattern_raycast_source_detail(
            context,
            event,
            source,
        )

        _pattern_live_preview["mode"] = mode
        _pattern_live_preview["source"] = source.name

        if detail is None:
            _pattern_live_preview["hover_anchor"] = None
            _pattern_live_preview["notch_edge"] = -1
            _tag_redraw()
            return {'PASS_THROUGH'}

        hover_anchor, hover_local, _hover_face = detail
        _pattern_live_preview["hover_anchor"] = hover_anchor

        if mode == "NOTCH":
            nearest = _pattern_nearest_seam_edge(
                context,
                source,
                hover_local,
            )
            if nearest is None:
                _pattern_live_preview["notch_edge"] = -1
            else:
                edge_index, fraction, _dist = nearest
                _pattern_live_preview["notch_edge"] = int(edge_index)
                _pattern_live_preview["notch_t"] = float(fraction)

        if mode == "ARROW":
            _pattern_live_preview["arrow_start"] = operator._first_anchor

        _tag_redraw()
        return {'PASS_THROUGH'}

    if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
        return {'PASS_THROUGH'}

    if source is None:
        scene["pattern_helper_modal_running"] = False
        return {'CANCELLED'}

    detail = _pattern_raycast_source_detail(
        context,
        event,
        source,
    )
    if detail is None:
        operator.report({'WARNING'}, "モデル表面をクリックしてください")
        return {'RUNNING_MODAL'}

    anchor, local_hit, _face_index = detail
    color = _pattern_current_color(scene, mode)
    items = _pattern_get_annotations(source)

    if mode == "NOTCH":
        if scene.pattern_helper_notch_mode == "NONE":
            operator.report({'WARNING'}, "合印方式が「合印なし」です")
            return {'RUNNING_MODAL'}

        nearest = _pattern_nearest_seam_edge(
            context,
            source,
            local_hit,
        )

        if nearest is None:
            operator.report({'WARNING'}, "赤いシーム付近をクリックしてください")
            return {'RUNNING_MODAL'}

        edge_index, fraction, _dist = nearest

        items.append({
            "type": "notch_edge",
            "edge": edge_index,
            "t": round(float(fraction), 7),
            "color": color,
            "auto": False,
        })
        _pattern_set_annotations(source, items)

        operator.report({'INFO'}, "合印を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "NUMBER":
        value = int(scene.pattern_helper_next_number)

        items.append({
            "type": "number",
            "value": value,
            "anchor": anchor,
            "size_mm": float(scene.pattern_helper_number_size_mm),
            "color": color,
        })
        _pattern_set_annotations(source, items)

        scene.pattern_helper_next_number = value + 1
        operator.report({'INFO'}, f"型紙番号 {value} を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "TEXT":
        text = str(scene.pattern_helper_custom_text)

        if not text:
            operator.report({'WARNING'}, "任意テキストを入力してください")
            return {'RUNNING_MODAL'}

        items.append({
            "type": "text",
            "text": text,
            "anchor": anchor,
            "size_mm": float(scene.pattern_helper_text_size_mm),
            "color": color,
        })
        _pattern_set_annotations(source, items)

        operator.report({'INFO'}, f"「{text}」を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "ARROW":
        if str(
            getattr(scene, "pattern_helper_arrow_mode", "AUTO")
        ) == "NONE":
            operator.report({'WARNING'}, "矢印方式が「なし」です")
            return {'RUNNING_MODAL'}

        if operator._first_anchor is None:
            operator._first_anchor = anchor
            _pattern_live_preview["arrow_start"] = anchor
            _pattern_live_preview["hover_anchor"] = anchor
            _tag_redraw()
            operator.report({'INFO'}, "次に矢印の先端をクリック")
            return {'RUNNING_MODAL'}

        items.append({
            "type": "arrow",
            "a": operator._first_anchor,
            "b": anchor,
            "color": color,
            "head_mm": float(scene.pattern_helper_arrow_head_mm),
            "thickness_mm": float(scene.pattern_helper_arrow_thickness_mm),
        })
        _pattern_set_annotations(source, items)

        operator._first_anchor = None
        _pattern_live_preview["arrow_start"] = None
        _pattern_live_preview["hover_anchor"] = None
        _tag_redraw()
        operator.report({'INFO'}, "上方向矢印を追加しました")
        return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}

class TSUNFOLD_OT_marking_tool_off(bpy.types.Operator):
    bl_idname = "truescale_unfold.marking_tool_off"
    bl_label = "マーキングツールOFF"
    bl_description = "現在の合印・番号・矢印・文字の連続配置ツールを停止します"

    def execute(self, context):
        _pattern_set_active_tool(context.scene, "NONE")
        _pattern_clear_live_preview()
        context.scene["pattern_helper_modal_running"] = False
        context.scene["pattern_helper_marking_finish_requested"] = False
        _tag_redraw()
        self.report({'INFO'}, "マーキングツールをOFFにしました")
        return {'FINISHED'}


class TSUNFOLD_OT_place_notch(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_notch"
    bl_label = "合印モード"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _pattern_source_object_from_context(context) is not None

    def invoke(self, context, event):
        return _pattern_toggle_tool_invoke(
            self,
            context,
            "NOTCH",
        )

    def modal(self, context, event):
        return _pattern_modal_common(self, context, event)


class TSUNFOLD_OT_place_number(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_number"
    bl_label = "型紙番号モード"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _pattern_source_object_from_context(context) is not None

    def invoke(self, context, event):
        return _pattern_toggle_tool_invoke(
            self,
            context,
            "NUMBER",
        )

    def modal(self, context, event):
        return _pattern_modal_common(self, context, event)


class TSUNFOLD_OT_place_arrow(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_arrow"
    bl_label = "上方向矢印モード"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _pattern_source_object_from_context(context) is not None

    def invoke(self, context, event):
        return _pattern_toggle_tool_invoke(
            self,
            context,
            "ARROW",
        )

    def modal(self, context, event):
        return _pattern_modal_common(self, context, event)



class TSUNFOLD_OT_set_text(bpy.types.Operator):
    bl_idname = "truescale_unfold.set_text"
    bl_label = "文字を入力 / 変更"

    text_value: StringProperty(
        name="文字",
        default="",
    )

    def invoke(self, context, event):
        try:
            _pattern_set_active_tool(context.scene, "NONE")
            _pattern_clear_live_preview()
            context.scene["pattern_helper_modal_running"] = False
            context.scene["pattern_helper_marking_finish_requested"] = False
        except Exception:
            pass

        self.text_value = str(context.scene.pattern_helper_custom_text)
        return context.window_manager.invoke_props_dialog(
            self,
            width=420,
        )

    def execute(self, context):
        context.scene.pattern_helper_custom_text = str(self.text_value)
        _tag_redraw()
        return {'FINISHED'}


class TSUNFOLD_OT_place_text(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_text"
    bl_label = "任意テキストモード"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _pattern_source_object_from_context(context) is not None

    def invoke(self, context, event):
        if (
            _pattern_active_tool(context.scene) != "TEXT"
            and not str(context.scene.pattern_helper_custom_text).strip()
        ):
            self.report({'WARNING'}, "先に文字を入力してください")
            return {'CANCELLED'}

        return _pattern_toggle_tool_invoke(
            self,
            context,
            "TEXT",
        )

    def modal(self, context, event):
        return _pattern_modal_common(self, context, event)





def _pattern_flat_face_source_map(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("pattern_helper_flat_face_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def _pattern_flat_face_island(unfold_obj, seed_poly_index):
    mesh = unfold_obj.data
    if not (0 <= int(seed_poly_index) < len(mesh.polygons)):
        return set()

    edge_to_faces = {}
    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            key = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            edge_to_faces.setdefault(key, []).append(int(poly.index))

    adjacency = {int(poly.index): set() for poly in mesh.polygons}
    for faces in edge_to_faces.values():
        if len(faces) < 2:
            continue
        for fa in faces:
            for fb in faces:
                if fa != fb:
                    adjacency[fa].add(fb)

    seen = {int(seed_poly_index)}
    stack = [int(seed_poly_index)]

    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.append(nxt)

    return seen


def _pattern_source_faces_for_flat_island(unfold_obj, flat_faces):
    mapping = _pattern_flat_face_source_map(unfold_obj)
    result = set()
    for flat_index in flat_faces:
        if 0 <= flat_index < len(mapping):
            result.add(int(mapping[flat_index]))
    return result


def _pattern_flat_island_from_source_face(unfold_obj, source_face_index):
    mapping = _pattern_flat_face_source_map(unfold_obj)
    seed = None
    for flat_index, source_index in enumerate(mapping):
        if int(source_index) == int(source_face_index):
            seed = flat_index
            break
    if seed is None:
        return set()
    return _pattern_flat_face_island(unfold_obj, seed)


def _pattern_set_island_highlight(source_obj, source_faces):
    global _pattern_island_highlight
    _pattern_island_highlight = {
        "source": source_obj.name if source_obj else "",
        "source_faces": sorted(int(v) for v in source_faces),
    }
    _tag_redraw()


def _pattern_clear_island_highlight():
    global _pattern_island_highlight
    _pattern_island_highlight = {
        "source": "",
        "source_faces": [],
    }
    _tag_redraw()


def _pattern_raycast_any_visible(context, event):
    if context.area is None or context.area.type != 'VIEW_3D':
        return None

    region = next(
        (r for r in context.area.regions if r.type == 'WINDOW'),
        None,
    )
    rv3d = context.space_data.region_3d
    if region is None or rv3d is None:
        return None

    coord = (
        event.mouse_x - region.x,
        event.mouse_y - region.y,
    )

    if (
        coord[0] < 0
        or coord[1] < 0
        or coord[0] >= region.width
        or coord[1] >= region.height
    ):
        return None

    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(
        region,
        rv3d,
        coord,
    ).normalized()

    depsgraph = context.evaluated_depsgraph_get()
    hit, location, normal, face_index, hit_obj, matrix = context.scene.ray_cast(
        depsgraph,
        origin,
        direction,
    )

    if not hit or hit_obj is None or face_index < 0:
        return None

    original = hit_obj.original if hasattr(hit_obj, "original") else hit_obj
    return original, int(face_index)



class TSUNFOLD_OT_place_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_flat_memo"
    bl_label = "型紙にメモを追加"
    bl_description = "型紙上をクリックして、文字入力ダイアログを開きます"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        unfold = _resolve_unfold_mesh_for_layout(context)
        if unfold is None:
            self.report({'WARNING'}, "先に型紙を作成してください")
            return {'CANCELLED'}

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "メモを置きたい型紙位置をクリックしてください")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.report({'INFO'}, "メモ配置をキャンセルしました")
            return {'CANCELLED'}

        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        if not _pattern_event_is_view_window(context, event):
            return {'PASS_THROUGH'}

        hit = _pattern_raycast_flat_pattern_location(
            context,
            event,
        )
        if hit is None:
            self.report({'WARNING'}, "型紙の面をクリックしてください")
            return {'RUNNING_MODAL'}

        unfold, local = hit

        bpy.ops.truescale_unfold.confirm_flat_memo(
            'INVOKE_DEFAULT',
            unfold_name=unfold.name,
            local_pos=(
                float(local.x),
                float(local.y),
                float(local.z),
            ),
        )
        return {'FINISHED'}


class TSUNFOLD_OT_confirm_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.confirm_flat_memo"
    bl_label = "型紙メモ"
    bl_description = "型紙に配置するメモ文字を入力します"
    bl_options = {'REGISTER', 'UNDO'}

    memo_text: StringProperty(
        name="メモ文字",
        description="型紙に印刷する自由メモ",
        default="",
    )

    memo_size_mm: FloatProperty(
        name="文字サイズ",
        description="メモ文字のサイズ",
        default=6.0,
        min=2.0,
        max=30.0,
        precision=1,
    )

    memo_angle: FloatProperty(
        name="回転",
        description="型紙上でのメモ文字の回転角度",
        subtype='ANGLE',
        default=0.0,
        min=-math.pi,
        max=math.pi,
    )

    unfold_name: StringProperty(
        options={'HIDDEN'},
        default="",
    )

    local_pos: FloatVectorProperty(
        options={'HIDDEN'},
        size=3,
        default=(0.0, 0.0, 0.0),
    )

    def invoke(self, context, event):
        self.memo_text = ""
        self.memo_size_mm = 6.0
        return context.window_manager.invoke_props_dialog(
            self,
            width=360,
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "memo_text", text="文字")
        layout.prop(self, "memo_size_mm", text="文字サイズ")

    def execute(self, context):
        text = str(self.memo_text).strip()
        if not text:
            self.report({'WARNING'}, "メモ文字を入力してください")
            return {'CANCELLED'}

        unfold = bpy.data.objects.get(self.unfold_name)
        if (
            unfold is None
            or unfold.type != 'MESH'
            or not bool(unfold.get("unfold_helper_generated", False))
        ):
            self.report({'WARNING'}, "配置先の型紙が見つかりません")
            return {'CANCELLED'}

        items = _pattern_get_flat_memos(unfold)
        items.append({
            "text": text,
            "pos": [
                float(self.local_pos[0]),
                float(self.local_pos[1]),
                float(self.local_pos[2]),
            ],
            "size_mm": float(self.memo_size_mm),
            "angle": 0.0,
        })
        memo_index = len(items) - 1
        _pattern_set_flat_memos(unfold, items)

        # Place first, then let the user visually rotate it in the viewport.
        try:
            bpy.ops.truescale_unfold.rotate_flat_memo(
                'INVOKE_DEFAULT',
                unfold_name=unfold.name,
                memo_index=memo_index,
            )
        except Exception:
            pass

        self.report({'INFO'}, f"メモ「{text}」を配置しました")
        return {'FINISHED'}




class TSUNFOLD_OT_rotate_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.rotate_flat_memo"
    bl_label = "メモを回転"
    bl_description = "配置したメモをマウスで回転し、左クリックで確定します"
    bl_options = {'REGISTER', 'UNDO'}

    unfold_name: StringProperty(
        options={'HIDDEN'},
        default="",
    )

    memo_index: bpy.props.IntProperty(
        options={'HIDDEN'},
        default=-1,
    )

    _original_angle = 0.0

    def invoke(self, context, event):
        unfold = bpy.data.objects.get(self.unfold_name)
        if unfold is None:
            return {'CANCELLED'}

        items = _pattern_get_flat_memos(unfold)
        if not (0 <= int(self.memo_index) < len(items)):
            return {'CANCELLED'}

        self._original_angle = float(
            items[int(self.memo_index)].get("angle", 0.0)
        )

        context.window_manager.modal_handler_add(self)
        self.report(
            {'INFO'},
            "マウスで回転 → 左クリックで確定 / ESC・右クリックで回転キャンセル",
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        unfold = bpy.data.objects.get(self.unfold_name)
        if unfold is None:
            return {'CANCELLED'}

        items = _pattern_get_flat_memos(unfold)
        index = int(self.memo_index)
        if not (0 <= index < len(items)):
            return {'CANCELLED'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            items[index]["angle"] = float(self._original_angle)
            _pattern_set_flat_memos(unfold, items)
            self.report({'INFO'}, "回転をキャンセルしました")
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.report({'INFO'}, "メモの回転を確定しました")
            return {'FINISHED'}

        if event.type == 'MOUSEMOVE':
            region = context.region
            rv3d = getattr(context.space_data, "region_3d", None)

            if (
                region is None
                or rv3d is None
                or context.area is None
                or context.area.type != 'VIEW_3D'
            ):
                return {'PASS_THROUGH'}

            pos = items[index].get("pos", [0.0, 0.0, 0.0])
            try:
                local = Vector((
                    float(pos[0]),
                    float(pos[1]),
                    float(pos[2]) if len(pos) > 2 else 0.0,
                ))
            except Exception:
                return {'PASS_THROUGH'}

            world = unfold.matrix_world @ local
            screen = view3d_utils.location_3d_to_region_2d(
                region,
                rv3d,
                world,
            )
            if screen is None:
                return {'PASS_THROUGH'}

            dx = float(event.mouse_region_x) - float(screen.x)
            dy = float(event.mouse_region_y) - float(screen.y)

            if abs(dx) + abs(dy) <= 2.0:
                return {'RUNNING_MODAL'}

            angle = math.atan2(dy, dx)
            items[index]["angle"] = float(angle)
            _pattern_set_flat_memos(unfold, items)
            _tag_redraw()

            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


class TSUNFOLD_OT_edit_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.edit_flat_memo"
    bl_label = "型紙メモ編集"
    bl_description = "選択した型紙メモをRで回転、X/Deleteで削除します"
    bl_options = {'REGISTER', 'UNDO'}

    _mode = "IDLE"
    _start_angle = 0.0

    def invoke(self, context, event):
        picked = _pattern_pick_flat_memo_at_mouse(context, event)
        if picked is None:
            _pattern_clear_selected_memo()
            return {'CANCELLED'}

        unfold, index = picked
        _pattern_selected_memo["unfold"] = unfold.name
        _pattern_selected_memo["index"] = int(index)
        _tag_redraw()

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "メモ選択中：R 回転 / X・Delete 削除 / Esc 終了")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        unfold, index, item = _pattern_selected_memo_item()
        if unfold is None or item is None:
            return {'CANCELLED'}

        if event.type == 'ESC':
            if self._mode == "ROTATE":
                items = _pattern_get_flat_memos(unfold)
                items[index]["angle"] = float(self._start_angle)
                _pattern_set_flat_memos(unfold, items)
            else:
                _pattern_clear_selected_memo()
                return {'FINISHED'}

            self._mode = "IDLE"
            _tag_redraw()
            return {'RUNNING_MODAL'}

        if self._mode == "IDLE":
            if event.type == 'R' and event.value == 'PRESS':
                self._mode = "ROTATE"
                self._start_angle = float(item.get("angle", 0.0))
                self.report({'INFO'}, "メモ回転中：マウスで回転 / 左クリック確定 / 右クリック・Escキャンセル")
                return {'RUNNING_MODAL'}

            if event.type in {'X', 'DEL', 'BACK_SPACE'} and event.value == 'PRESS':
                items = _pattern_get_flat_memos(unfold)
                del items[index]
                _pattern_set_flat_memos(unfold, items)
                _pattern_clear_selected_memo()
                self.report({'INFO'}, "メモを削除しました")
                return {'FINISHED'}

            # Clicking another memo selects it; clicking empty space exits selection.
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                picked = _pattern_pick_flat_memo_at_mouse(context, event)
                if picked is None:
                    _pattern_clear_selected_memo()
                    return {'FINISHED'}
                new_unfold, new_index = picked
                _pattern_selected_memo["unfold"] = new_unfold.name
                _pattern_selected_memo["index"] = int(new_index)
                _tag_redraw()
                return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}

        if self._mode == "ROTATE":
            if event.type == 'MOUSEMOVE':
                region = context.region
                rv3d = getattr(context.space_data, "region_3d", None)
                if region is None or rv3d is None:
                    return {'RUNNING_MODAL'}

                pos = item.get("pos", [0.0, 0.0, 0.0])
                local = Vector((float(pos[0]), float(pos[1]), float(pos[2])))
                world = unfold.matrix_world @ local
                screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
                if screen is None:
                    return {'RUNNING_MODAL'}

                dx = float(event.mouse_region_x) - float(screen.x)
                dy = float(event.mouse_region_y) - float(screen.y)
                if abs(dx) + abs(dy) > 2.0:
                    angle = math.atan2(dy, dx)
                    items = _pattern_get_flat_memos(unfold)
                    items[index]["angle"] = float(angle)
                    _pattern_set_flat_memos(unfold, items)
                return {'RUNNING_MODAL'}

            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                self._mode = "IDLE"
                self.report({'INFO'}, "メモ回転を確定しました")
                return {'RUNNING_MODAL'}

            if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                items = _pattern_get_flat_memos(unfold)
                items[index]["angle"] = float(self._start_angle)
                _pattern_set_flat_memos(unfold, items)
                self._mode = "IDLE"
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


class TSUNFOLD_OT_clear_flat_memos(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_flat_memos"
    bl_label = "メモを全削除"
    bl_description = "型紙に直接配置したメモだけをすべて削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        unfold = _resolve_unfold_mesh_for_layout(context)

        if unfold is None:
            # Source selected fallback.
            source = _pattern_source_object_from_context(context)
            if source is not None:
                unfold = _pattern_unfold_for_source(source)

        if unfold is None:
            self.report({'WARNING'}, "型紙が見つかりません")
            return {'CANCELLED'}

        count = len(_pattern_get_flat_memos(unfold))
        _pattern_set_flat_memos(unfold, [])

        self.report({'INFO'}, f"型紙メモを {count} 個削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_pick_corresponding_island(bpy.types.Operator):
    _last_click_time = 0.0
    _last_click_x = -10000
    _last_click_y = -10000

    bl_idname = "truescale_unfold.pick_corresponding_island"
    bl_label = "対応確認"

    def invoke(self, context, event):
        self._last_click_time = 0.0
        self._last_click_x = -10000
        self._last_click_y = -10000

        scene = context.scene

        if bool(
            getattr(
                scene,
                "pattern_helper_correspondence_mode",
                False,
            )
        ):
            scene.pattern_helper_correspondence_mode = False
            _pattern_clear_island_highlight()
            self.report({'INFO'}, "対応確認をOFFにしました")
            return {'FINISHED'}

        scene.pattern_helper_correspondence_mode = True
        context.window_manager.modal_handler_add(self)
        self.report(
            {'INFO'},
            "対応確認ON：元モデルか型紙をクリックすると対応片を表示します"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # If a flat memo is currently selected, its edit operator owns
        # transform/delete events. Do not let those keys pass through to
        # Blender's native object transform, otherwise G moves the whole pattern.
        selected_unfold, selected_index, selected_item = _pattern_selected_memo_item()
        if selected_item is not None:
            # Keep Blender's native transform/delete shortcuts from seeing
            # memo-edit keys. Mouse motion must remain available to the memo
            # edit modal, otherwise G-move cannot follow the cursor.
            if event.type in {
                'R',
                'X',
                'DEL',
                'BACK_SPACE',
            }:
                return {'RUNNING_MODAL'}

        # Existing memo has click priority so it can be edited directly.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if _pattern_event_is_view_window(context, event):
                picked_memo = _pattern_pick_flat_memo_at_mouse(context, event)
                if picked_memo is not None:
                    try:
                        bpy.ops.truescale_unfold.edit_flat_memo(
                            'INVOKE_DEFAULT'
                        )
                    except Exception:
                        pass
                    return {'RUNNING_MODAL'}

        # BlenderのDOUBLE_CLICK通知に依存せず、
        # 同じ位置への短時間2クリックを自前判定する。
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            now = time.monotonic()
            dx = int(event.mouse_region_x) - int(self._last_click_x)
            dy = int(event.mouse_region_y) - int(self._last_click_y)

            is_double_click = (
                (now - float(self._last_click_time)) <= 0.38
                and (dx * dx + dy * dy) <= (14 * 14)
            )

            if is_double_click:
                self._last_click_time = 0.0
                self._last_click_x = -10000
                self._last_click_y = -10000

                if _pattern_event_is_view_window(context, event):
                    hit = _pattern_raycast_flat_pattern_location(
                        context,
                        event,
                    )
                    if hit is not None:
                        unfold, local = hit
                        try:
                            bpy.ops.truescale_unfold.confirm_flat_memo(
                                'INVOKE_DEFAULT',
                                unfold_name=unfold.name,
                                local_pos=(
                                    float(local.x),
                                    float(local.y),
                                    float(local.z),
                                ),
                            )
                        except Exception as exc:
                            self.report(
                                {'WARNING'},
                                f"メモ入力を開けませんでした: {exc}",
                            )
                        return {'RUNNING_MODAL'}

            else:
                self._last_click_time = now
                self._last_click_x = int(event.mouse_region_x)
                self._last_click_y = int(event.mouse_region_y)

        scene = context.scene

        if not bool(
            getattr(
                scene,
                "pattern_helper_correspondence_mode",
                False,
            )
        ):
            return {'FINISHED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            scene.pattern_helper_correspondence_mode = False
            _pattern_clear_island_highlight()
            return {'FINISHED'}

        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        if not _pattern_event_is_view_window(context, event):
            return {'PASS_THROUGH'}

        hit = _pattern_raycast_any_visible(context, event)
        if hit is None:
            return {'RUNNING_MODAL'}

        hit_obj, face_index = hit

        try:
            if hit_obj.type == 'MESH' and hit_obj.mode == 'EDIT':
                hit_obj.update_from_editmode()
        except Exception:
            pass

        if (
            hit_obj.type == 'MESH'
            and bool(hit_obj.get("unfold_helper_generated", False))
        ):
            unfold = hit_obj
            source = bpy.data.objects.get(
                unfold.get("unfold_helper_source", "")
            )
            if source is None:
                return {'RUNNING_MODAL'}

            flat_faces = _pattern_flat_face_island(
                unfold,
                face_index,
            )
            source_faces = _pattern_source_faces_for_flat_island(
                unfold,
                flat_faces,
            )

        elif (
            hit_obj.type == 'MESH'
            and not bool(hit_obj.get("unfold_helper_generated", False))
        ):
            source = hit_obj
            unfold = _pattern_unfold_for_source(source)

            if unfold is None:
                return {'RUNNING_MODAL'}

            flat_faces = _pattern_flat_island_from_source_face(
                unfold,
                face_index,
            )
            source_faces = _pattern_source_faces_for_flat_island(
                unfold,
                flat_faces,
            )

        else:
            return {'RUNNING_MODAL'}

        if source_faces:
            _pattern_set_island_highlight(
                source,
                source_faces,
            )

        return {'RUNNING_MODAL'}


class TSUNFOLD_OT_clear_island_highlight(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_island_highlight"
    bl_label = "対応表示を解除"

    def execute(self, context):
        context.scene.pattern_helper_correspondence_mode = False
        _pattern_clear_island_highlight()
        return {'FINISHED'}






class TSUNFOLD_OT_toggle_direction_arrow(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_direction_arrow"
    bl_label = "水色の方向ガイド"
    bl_description = "画面左側の水色の方向矢印を表示/非表示します"

    def execute(self, context):
        scene = context.scene
        scene.pattern_helper_show_direction_arrow = not bool(
            scene.pattern_helper_show_direction_arrow
        )
        _tag_redraw()
        return {'FINISHED'}


class TSUNFOLD_OT_return_default(bpy.types.Operator):
    bl_idname = "truescale_unfold.return_default"
    bl_label = "作業終了・型紙を片付ける"
    bl_description = "型紙とマーキングを削除して終了します。元モデルに設定済みのシームは保持します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if bool(
            getattr(
                context.scene,
                "unfold_helper_preview",
                False,
            )
        ):
            _pattern_print_preview_source_visibility(
                context,
                False,
            )

        context.scene["pattern_helper_manual_layout_active"] = False
        scene = context.scene
        source = _pattern_seam_source(context)

        if source is None:
            unfold = _resolve_unfold_mesh_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("unfold_helper_source", "")
                )

        if source is None:
            active = context.active_object
            if (
                active is not None
                and active.type == 'MESH'
                and not bool(
                    active.get(
                        "unfold_helper_generated",
                        False,
                    )
                )
            ):
                source = active

        # Stop all modes first.
        try:
            _pattern_set_active_tool(scene, "NONE")
        except Exception:
            pass

        _pattern_clear_live_preview()
        _pattern_clear_island_highlight()

        scene.pattern_helper_correspondence_mode = False
        scene["pattern_helper_modal_running"] = False
        scene["pattern_helper_marking_session_active"] = False
        scene["pattern_helper_marking_finish_requested"] = False
        scene["pattern_helper_seam_preview_ready"] = False


        scene.unfold_helper_preview = False
        scene.unfold_helper_show_paper = False
        scene.pattern_helper_pattern_preview = False

        try:
            active = context.active_object
            if active is not None and active.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        if source is not None:
            # 1) All stored/manual/auto annotations.
            _pattern_set_annotations(source, [])

            # 2) All generated pattern outputs.
            _pattern_delete_generated_for_source(
                context,
                source,
            )

            # 元モデルのシームはユーザーの入力データなので絶対に変更しない。
            try:
                source.data.update()
            except Exception:
                pass

        # Hide/delete state is now clean; restore just the source selection.
        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                pass

        if source is not None:
            source.hide_set(False)
            source.hide_viewport = False
            source.select_set(True)
            context.view_layer.objects.active = source
            scene["pattern_helper_seam_source"] = ""
        else:
            scene["pattern_helper_seam_source"] = ""

        _pattern_invalidate_layout_cache()
        _tag_redraw()

        self.report(
            {'INFO'},
            "型紙とマーキングを片付けました。元モデルのシームは保持しています",
        )
        return {'FINISHED'}




class TSUNFOLD_OT_clear_arrows_all(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_arrows_all"
    bl_label = "矢印を全削除"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_source_object_from_context(context)

        if source is None:
            unfold = _resolve_unfold_mesh_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("unfold_helper_source", "")
                )

        if source is not None:
            items = [
                item
                for item in _pattern_get_annotations(source)
                if str(item.get("type", "")) != "arrow"
            ]
            _pattern_set_annotations(source, items)

        context.scene.pattern_helper_arrow_mode = "NONE"
        _pattern_clear_live_preview()
        _tag_redraw()
        self.report({'INFO'}, "自動・手動の矢印をすべて非表示/削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_delete_last_type(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_last_type"
    bl_label = "種類別マーキング削除"
    bl_options = {'REGISTER', 'UNDO'}

    annotation_type: StringProperty(default="")

    def execute(self, context):
        source = _pattern_source_object_from_context(context)

        if source is None:
            unfold = _resolve_unfold_mesh_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("unfold_helper_source", "")
                )

        if source is None:
            return {'CANCELLED'}

        items = _pattern_get_annotations(source)
        before = len(items)

        items = [
            item
            for item in items
            if str(item.get("type", "")) != self.annotation_type
        ]

        removed = before - len(items)

        if removed <= 0:
            self.report({'INFO'}, "削除するマーキングがありません")
            return {'CANCELLED'}

        _pattern_set_annotations(source, items)

        names = {
            "notch_edge": "合印",
            "number": "型紙番号",
            "text": "テキスト",
            "arrow": "矢印",
        }
        label = names.get(self.annotation_type, "マーキング")
        self.report({'INFO'}, f"{label}を {removed} 個削除しました")
        return {'FINISHED'}




class TSUNFOLD_OT_reset_number(bpy.types.Operator):
    bl_idname = "truescale_unfold.reset_number"
    bl_label = "番号を1に戻す"

    def execute(self, context):
        context.scene.pattern_helper_number_start = 1
        context.scene.pattern_helper_next_number = 1
        return {'FINISHED'}


class TSUNFOLD_OT_delete_last_annotation(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_last_annotation"
    bl_label = "最後の印を削除"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_source_object_from_context(context)
        if source is None:
            return {'CANCELLED'}

        items = _pattern_get_annotations(source)
        if items:
            items.pop()
            _pattern_set_annotations(source, items)

        return {'FINISHED'}


class TSUNFOLD_OT_clear_annotations(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_annotations"
    bl_label = "型紙印を全部削除"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_source_object_from_context(context)

        if source is None:
            unfold = _resolve_unfold_mesh_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("unfold_helper_source", "")
                )

        if source is None:
            return {'CANCELLED'}

        _pattern_set_annotations(source, [])
        _pattern_clear_live_preview()
        self.report({'INFO'}, "すべてのマーキングをクリアしました")
        return {'FINISHED'}


_DIGIT_5X7 = {
    "0": ("01110","10001","10011","10101","11001","10001","01110"),
    "1": ("00100","01100","00100","00100","00100","00100","01110"),
    "2": ("01110","10001","00001","00010","00100","01000","11111"),
    "3": ("11110","00001","00001","01110","00001","00001","11110"),
    "4": ("00010","00110","01010","10010","11111","00010","00010"),
    "5": ("11111","10000","10000","11110","00001","00001","11110"),
    "6": ("01110","10000","10000","11110","10001","10001","01110"),
    "7": ("11111","00001","00010","00100","01000","01000","01000"),
    "8": ("01110","10001","10001","01110","10001","10001","01110"),
    "9": ("01110","10001","10001","01111","00001","00001","01110"),
}


def _pattern_draw_digits_rgb(buffer, width, height, x, y, text, scale=2):
    cursor = int(round(x))
    top = int(round(y))
    scale = max(1, int(scale))

    for ch in str(text):
        glyph = _DIGIT_5X7.get(ch)
        if glyph is None:
            cursor += 6 * scale
            continue

        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue

                for sy in range(scale):
                    py = top + gy * scale + sy
                    if py < 0 or py >= height:
                        continue

                    for sx in range(scale):
                        px = cursor + gx * scale + sx
                        if px < 0 or px >= width:
                            continue

                        off = (py * width + px) * 3
                        buffer[off] = 0
                        buffer[off + 1] = 0
                        buffer[off + 2] = 0

        cursor += 6 * scale


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

class TSUNFOLD_PT_main(bpy.types.Panel):
    bl_label = "Truescale Unfold"
    bl_idname = "TSUNFOLD_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Truescale"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        scene = context.scene
        unfold_obj = _resolve_unfold_mesh_for_layout(context)

        # ------------------------------------------------------
        # 1. Load seamed model + build pattern
        # ------------------------------------------------------
        unit_box = layout.box()
        unit_box.label(text="カスタムシーン実寸基準")
        scene_scale, mm_per_bu = _scene_unit_summary(scene)
        unit_box.label(
            text=f"Unit Scale: {scene_scale:g} / 1 BU = {mm_per_bu:g} mm"
        )

        active_source = _pattern_seam_source(context)
        if active_source is not None:
            sx, sy, sz = (
                float(active_source.scale.x),
                float(active_source.scale.y),
                float(active_source.scale.z),
            )
            unit_box.label(
                text=f"Object Scale: {sx:g}, {sy:g}, {sz:g}"
            )
            if (
                abs(sx - 1.0) > 1.0e-6
                or abs(sy - 1.0) > 1.0e-6
                or abs(sz - 1.0) > 1.0e-6
            ):
                unit_box.label(
                    text="未適用Scaleもワールド寸法として反映",
                    icon='INFO',
                )

        source_box = layout.box()
        source_box.label(text="1. モデルと型紙")

        loaded_name = scene.get("pattern_helper_seam_source", "")
        loaded_obj = bpy.data.objects.get(loaded_name) if loaded_name else None

        source_box.operator(
            "truescale_unfold.load_seamed_object",
            text="モデルの読み込み",
            icon='IMPORT',
        )

        if loaded_obj is not None and loaded_obj.type == 'MESH':
            vis_row = source_box.row(align=True)
            vis_row.operator(
                "truescale_unfold.toggle_source_visibility",
                text=(
                    "元モデルを表示"
                    if loaded_obj.hide_get()
                    else "元モデルを非表示"
                ),
                icon='HIDE_OFF' if loaded_obj.hide_get() else 'HIDE_ON',
            )
            source_box.prop(
                scene,
                "pattern_helper_lightweight_view",
                text="軽量ビュー",
            )

        build_row = source_box.row()
        build_row.enabled = (
            loaded_obj is not None
            and loaded_obj.type == 'MESH'
            and any(bool(edge.use_seam) for edge in loaded_obj.data.edges)
        )
        build_row.operator(
            "truescale_unfold.build_pattern",
            text="型紙を作成 / 更新",
            icon='UV',
        )

        delete_row = source_box.row()
        delete_row.enabled = (unfold_obj is not None)
        delete_row.operator(
            "truescale_unfold.delete_unfold",
            text="型紙を削除",
            icon='TRASH',
        )

        # ------------------------------------------------------
        # 2. Marking
        # ------------------------------------------------------
        marking = layout.box()
        marking.label(text="2. マーキング")

        source = _pattern_source_object_from_context(context)
        if source is None:
            source = _pattern_seam_source(context)

        # -------------------------
        # NOTCH
        # -------------------------
        notch_box = marking.box()
        notch_box.use_property_split = True
        notch_box.use_property_decorate = False
        head = notch_box.row()
        head.scale_y = 1.25
        head.label(text="◆ 合印", icon='SNAP_MIDPOINT')

        notch_box.prop(
            scene,
            "pattern_helper_notch_mode",
            text="方式",
        )

        if scene.pattern_helper_notch_mode == "AUTO":
            notch_box.prop(
                scene,
                "pattern_helper_auto_notch_divisions",
                text="分割数",
            )


        if scene.pattern_helper_notch_mode == "AUTO":
            row = notch_box.row(align=True)
            row.operator(
                "truescale_unfold.refresh_auto_notches",
                text="作成 / 更新",
            )
            row.operator(
                "truescale_unfold.clear_notches",
                text="合印削除",
                icon='X',
            )

        if scene.pattern_helper_notch_mode != "NONE":
            notch_box.operator(
                "truescale_unfold.start_notch",
                text="手動で合印を追加",
            )

            # Frequently changed geometry setting first.
            notch_box.prop(
                scene,
                "pattern_helper_notch_length_mm",
                text="合印の長さ",
            )
            notch_box.prop(
                scene,
                "pattern_helper_notch_thickness_mm",
                text="合印の太さ",
            )

            # Color is secondary.
            color_row = notch_box.row(align=True)
            color_row.label(text="色")
            color_row.prop(
                scene,
                "pattern_helper_notch_color",
                text="",
            )

        # -------------------------
        # PATTERN ID
        # -------------------------
        id_box = marking.box()
        id_box.use_property_split = True
        id_box.use_property_decorate = False
        head = id_box.row()
        head.scale_y = 1.25
        head.label(text="◆ 型紙ID・接続先", icon='SORTALPHA')

        id_box.prop(
            scene,
            "pattern_helper_auto_island_ids",
            text="自動IDを表示",
        )

        if scene.pattern_helper_auto_island_ids:
            id_box.prop(
                scene,
                "pattern_helper_island_id_style",
                text="形式",
            )

            # Size first, color second.
            id_box.prop(
                scene,
                "pattern_helper_island_id_size_mm",
                text="ID文字サイズ",
            )

            id_box.label(text="各辺には接続先IDを自動表示")

        # -------------------------
        # ARROW
        # -------------------------
        arrow_box = marking.box()
        arrow_box.use_property_split = True
        arrow_box.use_property_decorate = False
        head = arrow_box.row()
        head.scale_y = 1.25
        head.label(text="↑ ◆ 上方向矢印")

        arrow_box.prop(
            scene,
            "pattern_helper_show_direction_arrow",
            text="水色の方向ガイド",
        )

        arrow_box.prop(
            scene,
            "pattern_helper_arrow_mode",
            text="方式",
        )

        if scene.pattern_helper_arrow_mode == "AUTO":
            arrow_box.prop(
                scene,
                "pattern_helper_arrow_up_axis",
                text="上方向",
            )

            arrow_box.prop(
                scene,
                "pattern_helper_auto_arrow_length_mm",
                text="矢印の長さ",
            )

        if scene.pattern_helper_arrow_mode != "NONE":
            arrow_box.prop(
                scene,
                "pattern_helper_arrow_head_mm",
                text="矢印ヘッド長さ",
            )
            arrow_box.prop(
                scene,
                "pattern_helper_arrow_thickness_mm",
                text="線の太さ",
            )

            color_row = arrow_box.row(align=True)
            color_row.label(text="色")
            color_row.prop(
                scene,
                "pattern_helper_arrow_color",
                text="",
            )

            if scene.pattern_helper_arrow_mode in {"AUTO", "CUSTOM"}:
                arrow_box.operator(
                    "truescale_unfold.start_arrow",
                    text="手動で矢印を追加",
                )

            arrow_box.operator(
                "truescale_unfold.clear_arrows_all",
                text="矢印を全削除",
                icon='TRASH',
            )

        # -------------------------
        # COMMON MARKING TOOLS
        # -------------------------
        # Common destructive action only.
        marking.operator(
            "truescale_unfold.clear_all_marks",
            text="すべてのマーキングをクリア",
            icon='TRASH',
        )

        # Correspondence controls stay close to marking but visually separate.
        corr_box = marking.box()
        corr_box.use_property_split = True
        corr_box.use_property_decorate = False
        head = corr_box.row()
        head.scale_y = 1.15
        head.label(text="◆ 対応確認・メモ", icon='RESTRICT_SELECT_OFF')

        corr_box.operator(
            "truescale_unfold.pick_corresponding_island",
            text=(
                "対応確認を終了"
                if scene.pattern_helper_correspondence_mode
                else "対応確認を開始"
            ),
            depress=scene.pattern_helper_correspondence_mode,
        )
        corr_box.operator(
            "truescale_unfold.clear_corresponding_island",
            text="対応ハイライトをクリア",
        )

        if scene.pattern_helper_correspondence_mode:
            corr_box.label(text="型紙を2回クリック → メモ追加")
            corr_box.label(text="既存メモをクリック → Rで回転")

        corr_box.separator()
        memo_row = corr_box.row(align=True)
        memo_row.operator(
            "truescale_unfold.place_flat_memo",
            text="型紙にメモを追加",
        )
        memo_row.operator(
            "truescale_unfold.clear_flat_memos",
            text="メモ全削除",
            icon='TRASH',
        )

        # ------------------------------------------------------
        # 3. Unfold / layout / line finish
        # ------------------------------------------------------
        output = layout.box()
        output.label(text="3. レイアウト・印刷")

        unfold_obj = _resolve_unfold_mesh_for_layout(context)

        if unfold_obj is None:
            output.label(text="先に型紙を作成してください")
        else:
            size = _object_xy_size_mm(context, unfold_obj)
            if size:
                output.label(
                    text=f"実寸: 横 {size[0]:.1f} × 縦 {size[1]:.1f} mm"
                )

            output.label(text="用紙設定")
            row = output.row(align=True)
            row.prop(
                scene,
                "unfold_helper_paper_size",
                text="用紙",
            )

            if scene.unfold_helper_paper_size != "CUSTOM":
                row.prop(
                    scene,
                    "unfold_helper_orientation",
                    text="向き",
                )
            else:
                custom = output.box()
                custom.label(text="カスタム用紙サイズ（mm）")
                custom_row = custom.row(align=True)
                custom_row.prop(
                    scene,
                    "unfold_helper_custom_paper_width_mm",
                    text="幅",
                )
                custom_row.prop(
                    scene,
                    "unfold_helper_custom_paper_height_mm",
                    text="高さ",
                )
                custom.label(text="入力した幅 × 高さをそのまま使用")

            paper_w_mm, paper_h_mm = _paper_dimensions_mm(scene)
            output.label(
                text=f"使用サイズ: {paper_w_mm:.1f} × {paper_h_mm:.1f} mm"
            )

            output.prop(
                scene,
                "unfold_helper_show_paper",
                text="用紙ガイドを表示",
            )

            output.separator()
            output.label(text="レイアウト")
            row = output.row(align=True)
            row.operator(
                "truescale_unfold.auto_layout",
                text="自動レイアウト",
                icon='NODE_CORNER',
            )
            row.operator(
                "truescale_unfold.layout_edit",
                text="手動で調整",
                icon='EDITMODE_HLT',
            )
            output.operator(
                "truescale_unfold.layout_confirm",
                text="レイアウト確定",
                icon='CHECKMARK',
            )
            if _pattern_manual_layout_active(scene):
                output.label(text="手動調整中：マーキング表示を一時停止")
            else:
                output.label(text="面を選択してGで移動 → レイアウト確定")

            output.separator()
            output.label(text="印刷・書き出し")

            output.operator(
                "truescale_unfold.toggle_preview",
                text=(
                    "印刷プレビューを終了"
                    if scene.unfold_helper_preview
                    else "印刷プレビュー"
                ),
                icon='HIDE_OFF',
                depress=scene.unfold_helper_preview,
            )

            output.operator(
                "truescale_unfold.export_png",
                text="実寸PNGを書き出し（300dpi）",
                icon='EXPORT',
            )

        finish = layout.box()
        finish.operator(
            "truescale_unfold.return_default",
            text="作業終了・型紙を片付ける",
            icon='HOME',
        )

        info = layout.box()
        info.label(text="型紙ヘルパー カスタムシーン Beta v1.5.6")


classes = (
    TSUNFOLD_OT_edit_flat_memo,
    TSUNFOLD_OT_rotate_flat_memo,
    TSUNFOLD_OT_confirm_flat_memo,
    TSUNFOLD_OT_place_flat_memo,
    TSUNFOLD_OT_clear_flat_memos,
    TSUNFOLD_OT_toggle_source_visibility,
    TSUNFOLD_OT_load_seamed_object,
    TSUNFOLD_OT_toggle_direction_arrow,
    TSUNFOLD_OT_layout_confirm,
    TSUNFOLD_OT_return_default,
    TSUNFOLD_OT_clear_arrows_all,
    TSUNFOLD_OT_clear_island_highlight,
    TSUNFOLD_OT_pick_corresponding_island,
    TSUNFOLD_OT_build_pattern,
    TSUNFOLD_OT_remove_auto_notches,
    TSUNFOLD_OT_refresh_auto_notches,
    TSUNFOLD_OT_reset_number,
    TSUNFOLD_OT_delete_last_type,
    TSUNFOLD_OT_marking_tool_off,
    TSUNFOLD_OT_toggle_pattern_preview,
    TSUNFOLD_OT_finish_marking,
    TSUNFOLD_OT_place_notch,
    TSUNFOLD_OT_place_number,
    TSUNFOLD_OT_place_arrow,
    TSUNFOLD_OT_delete_last_annotation,
    TSUNFOLD_OT_clear_annotations,
    TSUNFOLD_OT_unfold_real_mesh,
    TSUNFOLD_OT_select_unfold_source,
    TSUNFOLD_OT_auto_layout,
    TSUNFOLD_OT_layout_edit,
    TSUNFOLD_OT_delete_unfold,
    TSUNFOLD_OT_toggle_preview,
    TSUNFOLD_OT_export_png,
    TSUNFOLD_PT_main,
)



def _unfold_helper_reset_overlays_on_load(_dummy=None):
    # Runs only after a .blend has loaded, when bpy.data is available.
    try:
        scenes = bpy.data.scenes
    except Exception:
        return

    for scene in scenes:
        try:
            scene.unfold_helper_show_paper = False
        except Exception:
            pass
        try:
            scene.unfold_helper_preview = False
        except Exception:
            pass
        try:
            scene["pattern_helper_marking_session_active"] = False
            scene["pattern_helper_marking_finish_requested"] = False
            scene["pattern_helper_marking_prev_active"] = ""
            scene["pattern_helper_marking_prev_selected_json"] = "[]"
            scene["pattern_helper_marking_prev_mode"] = "OBJECT"
            scene["pattern_helper_seam_source"] = ""
            scene["pattern_helper_seam_preview_ready"] = False
            scene["pattern_helper_modal_running"] = False
            scene.pattern_helper_active_tool = "NONE"
            scene.pattern_helper_correspondence_mode = False
            scene.pattern_helper_auto_island_ids = True
            scene.pattern_helper_island_id_style = "ALPHA"
            scene.pattern_helper_arrow_mode = "AUTO"
            scene.pattern_helper_arrow_up_axis = "Z"
            scene.pattern_helper_number_start = 1
            scene.pattern_helper_next_number = 1
            scene.pattern_helper_notch_mode = "AUTO"
            scene.pattern_helper_auto_notch_divisions = "3"
            scene.pattern_helper_pattern_preview = False
            scene["pattern_helper_preview_source_name"] = ""
            scene["pattern_helper_preview_source_hide_get"] = False
            scene["pattern_helper_preview_source_hide_viewport"] = False
        except Exception:
            pass



def register():
    global _draw_handle, _pattern_draw_handle, _pattern_text_handle

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.unfold_helper_spacing_mm = FloatProperty(
        name="アイランド間隔",
        description="展開後のアイランド同士の間隔。変更するとリアルタイムで再配置します",
        default=10.0,
        min=0.0,
        soft_max=100.0,
        precision=1,
        update=_spacing_updated,
    )

    bpy.types.Scene.unfold_helper_paper_size = EnumProperty(
        name="用紙サイズ",
        description="用紙ガイドとPNGの用紙サイズ",
        items=[
            ("A5", "A5", "148 × 210 mm"),
            ("A4", "A4", "210 × 297 mm"),
            ("A3", "A3", "297 × 420 mm"),
            ("A2", "A2", "420 × 594 mm"),
            ("A1", "A1", "594 × 841 mm"),
            ("A0", "A0", "841 × 1189 mm"),
            ("B5", "B5", "182 × 257 mm"),
            ("B4", "B4", "257 × 364 mm"),
            ("CUSTOM", "カスタム", "幅と高さをmmで指定"),
        ],
        default="A4",
        update=_paper_setting_updated,
    )

    bpy.types.Scene.unfold_helper_custom_paper_width_mm = FloatProperty(
        name="カスタム用紙 幅",
        description="カスタム用紙の横幅をmmで指定します",
        default=600.0,
        min=1.0,
        soft_max=3000.0,
        precision=1,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.unfold_helper_custom_paper_height_mm = FloatProperty(
        name="カスタム用紙 高さ",
        description="カスタム用紙の高さをmmで指定します",
        default=900.0,
        min=1.0,
        soft_max=3000.0,
        precision=1,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.unfold_helper_orientation = EnumProperty(
        name="用紙の向き",
        description="用紙の向き",
        items=[
            ("AUTO", "自動", "表示ガイドは縦。PNG書き出し時は収まりやすい向きを自動選択"),
            ("PORTRAIT", "縦", "縦向き"),
            ("LANDSCAPE", "横", "横向き"),
        ],
        default="PORTRAIT",
        update=_paper_setting_updated,
    )

    bpy.types.Scene.pattern_helper_pattern_preview = BoolProperty(
        name="型紙プレビュー",
        description="現在の型紙状態をViewportに表示します",
        default=False,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.pattern_helper_notch_mode = EnumProperty(
        name="合印方式",
        description="合印の作り方",
        items=[
            (
                "AUTO",
                "オート",
                "分割数に応じてシーム全長へ自動配置します",
            ),
            (
                "NONE",
                "なし",
                "合印を作りません",
            ),
            (
                "CUSTOM",
                "カスタム",
                "クリックした位置だけに合印を追加します",
            ),
        ],
        default="AUTO",
    )

    bpy.types.Scene.pattern_helper_auto_notch_divisions = EnumProperty(
        name="分割数",
        description="2なら中央1個、3なら1/3と2/3、4なら1/4・1/2・3/4",
        items=[
            ("2", "2", "中央に1個"),
            ("3", "3", "1/3と2/3に配置"),
            ("4", "4", "1/4・1/2・3/4に配置"),
        ],
        default="3",
        update=_pattern_notch_setting_updated,
    )

    bpy.types.Scene.pattern_helper_show_direction_arrow = BoolProperty(
        name="水色の方向ガイド",
        default=False,
        update=_pattern_setting_updated,
    )





    bpy.types.Scene.pattern_helper_correspondence_mode = BoolProperty(
        name="対応確認",
        default=False,
        options={'HIDDEN'},
    )

    bpy.types.Scene.pattern_helper_auto_island_ids = BoolProperty(
        name="自動型紙ID",
        default=True,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_island_id_style = EnumProperty(
        name="型紙ID形式",
        items=[
            ("ALPHA", "A / B / C", "アイランドをアルファベットで表示"),
            ("NUMBER", "1 / 2 / 3", "アイランドを数字で表示"),
        ],
        default="ALPHA",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_island_id_size_mm = FloatProperty(
        name="型紙IDサイズ",
        default=8.0,
        min=3.0,
        max=30.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_island_id_color = FloatVectorProperty(
        name="型紙ID色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_arrow_mode = EnumProperty(
        name="矢印方式",
        items=[
            ("AUTO", "オート", "各アイランドへ上方向矢印を自動配置"),
            ("NONE", "なし", "矢印を表示しない"),
            ("CUSTOM", "カスタム", "手動で矢印を配置"),
        ],
        default="AUTO",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_arrow_up_axis = EnumProperty(
        name="上方向",
        description="Blender右上のXYZギズモと同じグローバル軸を使用します",
        items=[
            ("Z", "Z+", "BlenderグローバルZ+"),
            ("Y", "Y+", "BlenderグローバルY+"),
            ("X", "X+", "BlenderグローバルX+"),
        ],
        default="Z",
        update=_pattern_setting_updated,
    )

    

    bpy.types.Scene.pattern_helper_auto_arrow_length_mm = FloatProperty(
        name="オート矢印長さ",
        default=24.0,
        min=5.0,
        max=100.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_active_tool = StringProperty(
        name="マーキングツール",
        default="NONE",
        options={'HIDDEN'},
    )

    bpy.types.Scene.pattern_helper_mark_color = FloatVectorProperty(
        name="旧印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        options={'HIDDEN'},
    )

    bpy.types.Scene.pattern_helper_notch_color = FloatVectorProperty(
        name="合印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_notch_setting_updated,
    )

    bpy.types.Scene.pattern_helper_number_color = FloatVectorProperty(
        name="数字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_text_color = FloatVectorProperty(
        name="文字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_arrow_color = FloatVectorProperty(
        name="矢印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_notch_length_mm = FloatProperty(
        name="合印長さ",
        default=6.0,
        min=1.0,
        max=30.0,
        precision=1,
        update=_pattern_notch_setting_updated,
    )

    bpy.types.Scene.pattern_helper_notch_thickness_mm = FloatProperty(
        name="合印の太さ",
        description="合印線の表示・出力時の太さ",
        default=0.6,
        min=0.1,
        soft_max=3.0,
        precision=2,
        update=_pattern_notch_setting_updated,
    )

    bpy.types.Scene.pattern_helper_number_size_mm = FloatProperty(
        name="数字サイズ",
        default=8.0,
        min=2.0,
        max=40.0,
        precision=1,
    )

    bpy.types.Scene.pattern_helper_text_size_mm = FloatProperty(
        name="文字サイズ",
        default=8.0,
        min=2.0,
        max=60.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_custom_text = StringProperty(
        name="型紙名 / 任意テキスト",
        default="",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_number_start = bpy.props.IntProperty(
        name="開始番号",
        description="型紙番号モードをONにした時に最初に入る番号",
        default=1,
        min=1,
        max=9999,
    )

    bpy.types.Scene.pattern_helper_arrow_head_mm = FloatProperty(
        name="矢印先端サイズ",
        default=8.0,
        min=2.0,
        max=30.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_arrow_thickness_mm = FloatProperty(
        name="矢印線の太さ",
        default=0.8,
        min=0.2,
        max=5.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.pattern_helper_next_number = bpy.props.IntProperty(
        name="次の型紙番号",
        description="型紙番号ツールが次に置く番号",
        default=1,
        min=1,
        max=9999,
    )

    bpy.types.Scene.unfold_helper_show_paper = BoolProperty(
        name="用紙枠を表示",
        description="3Dビューに実寸用紙枠を表示します。オブジェクトは作りません",
        default=False,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.pattern_helper_lightweight_view = BoolProperty(
        name="軽量ビュー",
        description="元モデル側の補助マーキング描画を減らして3Dビュー操作を軽くします",
        default=True,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.unfold_helper_preview = BoolProperty(
        name="印刷プレビュー",
        description="最終PNGに近い白紙＋黒外周線を3Dビューに表示します",
        default=False,
        update=_preview_setting_updated,
    )

    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_paper_guide, (), 'WINDOW', 'POST_VIEW'
        )

    if _pattern_draw_handle is None:
        _pattern_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_pattern_marks_3d, (), 'WINDOW', 'POST_VIEW'
        )

    if _pattern_text_handle is None:
        _pattern_text_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_pattern_text_2d, (), 'WINDOW', 'POST_PIXEL'
        )

    if _unfold_helper_reset_overlays_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_unfold_helper_reset_overlays_on_load)


def unregister():

    if _unfold_helper_reset_overlays_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_unfold_helper_reset_overlays_on_load)
    global _draw_handle, _pattern_draw_handle, _pattern_text_handle

    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None

    if _pattern_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _pattern_draw_handle, 'WINDOW'
            )
        except Exception:
            pass
        _pattern_draw_handle = None

    if _pattern_text_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _pattern_text_handle, 'WINDOW'
            )
        except Exception:
            pass
        _pattern_text_handle = None

    for prop in (
        "pattern_helper_notch_mode",
        "pattern_helper_auto_notch_divisions",
        "pattern_helper_active_tool",
        "pattern_helper_mark_color",
        "pattern_helper_notch_color",
        "pattern_helper_number_color",
        "pattern_helper_text_color",
        "pattern_helper_arrow_color",
        "pattern_helper_notch_length_mm",
        "pattern_helper_number_size_mm",
        "pattern_helper_text_size_mm",
        "pattern_helper_custom_text",
        "pattern_helper_pattern_preview",
        "pattern_helper_number_start",
        "pattern_helper_arrow_head_mm",
        "pattern_helper_arrow_thickness_mm",
        "pattern_helper_next_number",
        "unfold_helper_preview",
        "unfold_helper_show_paper",
        "unfold_helper_orientation",
        "unfold_helper_paper_size",
        "unfold_helper_custom_paper_width_mm",
        "unfold_helper_custom_paper_height_mm",
        "unfold_helper_spacing_mm",
    ):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

    if hasattr(bpy.types.Scene, "pattern_helper_lightweight_view"):
        del bpy.types.Scene.pattern_helper_lightweight_view

    if hasattr(bpy.types.Scene, "pattern_helper_notch_thickness_mm"):
        del bpy.types.Scene.pattern_helper_notch_thickness_mm

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
