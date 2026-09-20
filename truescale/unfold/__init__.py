import bpy

from .. import debug as _debug
from ..core import paper as _paper
from ..core import geometry as _geometry
from ..core import mapping as _mapping
from ..core import objects as _objects
from ..core import solve as _solve
from ..core import session as _session
from ..core import state as _state
from ..marking import compute as _compute
from ..marking import placement as _placement
from ..marking import seams as _seams
from ..marking import source as _source
from .. import overlay as _overlay
from ..marking import storage as _storage
from ..core import units as _units
from ..export import png as _png
import traceback
import gpu
from bpy.props import EnumProperty, StringProperty, FloatProperty, BoolProperty, FloatVectorProperty
from bpy_extras.io_utils import ExportHelper
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils import Matrix
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

# 用紙サイズと印刷解像度の定義は共通モジュールが持つ。
# 既存の参照をそのまま動かすために別名を置いている。







# ビューポート描画は truescale.overlay にある。
# 既存の呼び出しをそのまま動かすための別名。
_preview_outline_segments = _overlay.preview_outline_segments
_draw_paper_guide = _overlay.draw_paper_guide
_pattern_flat_text_items = _overlay.flat_text_items
_pattern_flat_memo_text_items = _overlay.flat_memo_text_items
_pattern_source_text_items = _overlay.source_text_items
_pattern_auto_source_id_text_items = _overlay.auto_source_id_text_items
_pattern_arrow_hud_text = _overlay.arrow_hud_text
_pattern_draw_arrow_hud = _overlay.draw_arrow_hud
_pattern_draw_direction_arrow_overlay = _overlay.draw_direction_arrow_overlay
_draw_pattern_marks_3d = _overlay.draw_marks_3d
_draw_pattern_text_2d = _overlay.draw_text_2d

# オブジェクト解決と元モデル側の注記は別モジュールにある。
# 既存の呼び出しをそのまま動かすための別名。
_pattern_source_object_from_context = _objects.source_from_context
_pattern_unfold_for_source = _objects.unfold_for_source
_active_unfold_object = _objects.active_unfold
_resolve_unfold_mesh_for_layout = _objects.resolve_unfold_for_layout
_pattern_flat_face_source_map = _objects.flat_face_source_map
_pattern_edge_adjacent_polygons = _objects.edge_adjacent_polygons
_boundary_segments_world_xy = _objects.boundary_segments_world_xy
_pattern_anchor_point_source_local = _source.anchor_point_local
_pattern_source_notch_segment = _source.notch_segment
_pattern_source_seam_segments = _source.seam_segments
_pattern_source_colored_segments = _source.colored_segments

# 描画へ渡すデータの計算は truescale.marking.compute にある。
# 既存の呼び出しをそのまま動かすための別名。
_pattern_auto_island_metadata = _compute.island_metadata
_pattern_island_label = _compute.island_label
_pattern_auto_up_vector = _compute.auto_up_vector
_pattern_auto_up_vector_source_local = _compute.auto_up_vector_source_local
_pattern_sanitize_arrow_axis = _compute.sanitize_arrow_axis
_pattern_auto_arrow_direction_for_record = _compute.auto_arrow_direction_for_record
_pattern_anchor_point_flat_local = _compute.anchor_point_flat_local
_pattern_connection_label_inside_position = _compute.connection_label_inside_position
_pattern_visible_smooth_for_unfold = _compute.visible_smooth_for_unfold
_smooth_curve_segments_world_xy = _compute.smooth_curve_segments_world_xy
_pattern_flat_notch_segments = _compute.flat_notch_segments
_pattern_smooth_notch_segments = _compute.smooth_notch_segments
_pattern_transform_rows = _compute.transform_rows
_pattern_compute_auto_flat_oriented_text_items = _compute.compute_text_items
_pattern_auto_flat_oriented_text_items = _compute.text_items
_pattern_compute_auto_arrow_segments = _compute.compute_arrow_segments
_pattern_auto_arrow_segments_local = _compute.arrow_segments_local
_pattern_compute_flat_colored_segments = _compute.compute_colored_segments
_pattern_flat_colored_segments = _compute.colored_segments

# 対応表と数値計算は truescale.core にある。
# 既存の呼び出しをそのまま動かすための別名。
_pattern_flat_mapping = _mapping.read_indices
_pattern_flat_vertex_source_indices = _mapping.flat_vertex_to_source
_pattern_flat_edge_source_indices = _mapping.flat_edge_to_source
_pattern_flat_face_source_indices_local = _mapping.flat_face_to_source
_pattern_flat_edge_faces = _mapping.flat_edge_faces
_pattern_nearest_point_on_xy_segment = _solve.nearest_point_on_xy_segment
_pattern_solve_3x3_regularized = _solve.solve_3x3_regularized
_pattern_solve_2d_gradient = _solve.solve_2d_gradient
_bezier_point = _solve.bezier_point

# シーム走査と配置探索は truescale.marking にある。
# 既存の呼び出しをそのまま動かすための別名。
_pattern_sync_live_seams = _seams.sync_live_seams
_pattern_seam_trails = _seams.seam_trails
_pattern_trail_mark_positions = _seams.trail_mark_positions
_pattern_alpha_label = _placement.alpha_label
_pattern_arrow_geometry_local = _placement.arrow_geometry_local
_pattern_arrow_fits_island = _placement.arrow_fits_island
_pattern_id_footprint_inside = _placement.id_footprint_inside
_pattern_island_id_safe_position = _placement.island_id_safe_position
_pattern_safe_arrow_placement = _placement.safe_arrow_placement

# 平面の幾何処理は truescale.core.geometry にある。
# 既存の呼び出しをそのまま動かすための別名。
_get_face_islands = _geometry.face_islands
_island_bbox = _geometry.island_bbox
_rotate_vertices_90 = _geometry.rotate_vertices_90
_move_island_to = _geometry.move_island_to
_pattern_flat_polygons_2d = _geometry.flat_polygons_2d
_pattern_point_in_polys_2d = _geometry.point_in_polys_2d
_pattern_point_in_island_2d = _geometry.point_in_island_2d
_pattern_island_boundary_segments = _geometry.island_boundary_segments
_pattern_boundary_as_floats = _geometry.boundary_as_floats
_pattern_any_point_too_close = _geometry.any_point_too_close
_pattern_min_clearance = _geometry.min_clearance

PAPER_SIZES_MM = _paper.SIZES_MM
PRINT_DPI = _png.PRINT_DPI
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



# 描画用の重い解析は「型紙オブジェクトのローカル空間」で計算して
# キャッシュし、ワールド変換はキャッシュの外で毎回掛ける。
#
# 以前は matrix_world をキャッシュキーに含めていたため、オブジェクトを
# G で動かすと1フレームごとにキーが変わり、島内配置の探索などが毎フレーム
# 走っていた。ビューを回すだけなら matrix_world は変わらないのでキャッシュが
# 効くため、「移動したときだけ極端に重い」という症状になっていた。
_TS_IDENTITY = Matrix.Identity(4)


# ------------------------------------------------------------
# Unit / geometry helpers
# ------------------------------------------------------------

def _scene_unit_scale_raw(scene):
    """シーンが持っている Unit Scale をそのまま返す。"""
    return _units.scene_unit_scale_raw(scene)


def _scene_scale_to_meters(scene):
    """1 Blender Unit が何メートルに相当するか。

    実装は truescale.core.units にある。
    基準の決め方（シーンに従う / アドオンで指定）もそちらを参照。
    """
    return _units.scene_scale_to_meters(scene)


def _scene_bu_to_mm(scene):
    """1 Blender Unit が何ミリに相当するか。"""
    return _units.scene_mm_per_bu(scene)


def _scene_unit_summary(scene):
    return _units.scene_unit_summary(scene)


def _mm_to_bu(scene, mm):
    return _units.scene_mm_to_bu(scene, mm)


def _bu_to_mm(scene, bu):
    return _units.scene_bu_to_mm(scene, bu)


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

    new_obj["tsunfold_generated"] = True
    new_obj["tsunfold_source"] = src_obj.name

    # Record the unit context used to create this pattern.
    scene_scale, mm_per_bu = _scene_unit_summary(context.scene)
    new_obj["tsunfold_scene_scale_length"] = float(scene_scale)
    new_obj["tsunfold_mm_per_bu"] = float(mm_per_bu)
    new_obj["tsunfold_source_object_scale"] = [
        float(src_obj.scale.x),
        float(src_obj.scale.y),
        float(src_obj.scale.z),
    ]

    new_obj["tsunfold_flat_vertex_source_json"] = json.dumps(
        flat_vertex_source_vertex
    )
    new_obj["tsunfold_flat_face_source_json"] = json.dumps(
        flat_face_source_face
    )

    flat_edge_source = [-1] * len(new_mesh.edges)
    for edge in new_mesh.edges:
        key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        flat_edge_source[edge.index] = int(
            flat_side_source_edge.get(key, -1)
        )

    new_obj["tsunfold_flat_edge_source_json"] = json.dumps(
        flat_edge_source
    )

    for o in context.selected_objects:
        o.select_set(False)

    new_obj.select_set(True)
    context.view_layer.objects.active = new_obj
    return new_obj




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
    """パネル表示用の用紙名。"""
    return _paper.scene_display_name(scene)


def _paper_dimensions_mm(scene):
    """シーンの設定から用紙寸法（幅, 高さ）をミリで返す。"""
    return _paper.scene_dimensions_mm(scene)








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







def _pattern_manual_layout_active(scene):
    return bool(scene.get(_session.MANUAL_LAYOUT_ACTIVE, False))


def _pattern_setting_updated(self, context):
    _pattern_invalidate_layout_cache()


def _pattern_notch_source_for_update(context):
    """合印設定の更新対象になる元モデルを返す。無ければ None。"""
    scene = context.scene
    if str(getattr(scene, "tsunfold_notch_mode", "AUTO")) != "AUTO":
        return None

    source = _pattern_seam_source(context)
    if source is None:
        source = _pattern_source_object_from_context(context)

    if source is None or source.type != 'MESH':
        return None

    return source


def _pattern_notch_divisions_updated(self, context):
    """分割数の変更。合印の位置が変わるので作り直す必要がある。

    アノテーションに保存しているのは type / edge / t / color / auto だけで、
    位置を決めるのは t（辺上の比率）。分割数が変わると t が変わるため、
    ここだけはシームを辿り直して作り直す。
    """
    try:
        source = _pattern_notch_source_for_update(context)
        if source is not None:
            _pattern_refresh_auto_notches(context, source)
    except Exception:
        # プロパティのコールバックでUI操作を壊さない。
        # ただし内容は握り潰さずに出す。
        traceback.print_exc()

    _pattern_invalidate_layout_cache()


def _pattern_workflow_status(context):
    """いま何ができていて、次に何をすればよいかを返す。

    (アイコン, 現在地, 次の一手) の3つ組。次の一手が無ければ None。

    初回に触ったとき「どこで何が起きているのか分からない」という
    状態になりやすいため、パネルの先頭に出して道案内にする。
    """
    scene = context.scene

    loaded_name = scene.get(_session.SEAM_SOURCE, "")
    source = bpy.data.objects.get(loaded_name) if loaded_name else None

    if source is None or source.type != 'MESH':
        active = context.active_object
        if active is not None and active.type == 'MESH':
            return (
                'INFO',
                f"未読み込み（選択中: {active.name}）",
                "「モデルの読み込み」を押してください",
            )
        return (
            'INFO',
            "未読み込み",
            "シームを設定したMeshを選んでください",
        )

    seam_count = sum(1 for edge in source.data.edges if edge.use_seam)

    if seam_count == 0:
        return (
            'ERROR',
            f"{source.name} を読み込み済み / シーム 0 本",
            "編集モードで辺を選び「シームを入れる」を押してください",
        )

    unfold = _pattern_unfold_for_source(source)
    if unfold is None:
        return (
            'INFO',
            f"{source.name} / シーム {seam_count} 本",
            "「型紙を作成 / 更新」を押してください",
        )

    islands = len(_get_face_islands(unfold.data))
    size = _object_xy_size_mm(context, unfold)
    size_text = f" / {size[0]:.0f}×{size[1]:.0f} mm" if size else ""

    return (
        'CHECKMARK',
        f"型紙 {islands} 枚{size_text}",
        None,
    )


def _pattern_scale_warnings(context, unfold_obj):
    """型紙が用紙に対して極端に大きい／小さい場合の注意を返す。

    Unit Scale が 1 のままだと 1 BU = 1000 mm 換算になり、Blenderの
    デフォルト円柱（半径1 BU）が直径2メートルの物体として扱われる。
    その状態では
      - 用紙ガイドが画面上でほぼ点にしかならない
      - 自動レイアウトが必ず失敗する
      - 300dpi PNG がピクセル数の上限を超える
      - 実寸で正しいはずの合印が小さすぎて見えない
    が同時に起きるが、どれも「なぜそうなるか」が画面に出ていなかった。
    """
    size = _object_xy_size_mm(context, unfold_obj)
    if not size:
        return []

    width_mm, height_mm = size
    longest = max(width_mm, height_mm)
    if longest <= 0.0:
        return []

    paper_w, paper_h = _paper_dimensions_mm(context.scene)
    paper_longest = max(paper_w, paper_h)

    lines = []

    # 用紙に対して大きすぎる
    if longest > paper_longest:
        ratio = longest / paper_longest
        lines.append(
            f"用紙 {_paper_display_name(context.scene)} の約 {ratio:.1f} 倍です"
        )

        fits = [
            name for name, (pw, ph) in sorted(
                PAPER_SIZES_MM.items(),
                key=lambda kv: kv[1][0] * kv[1][1],
            )
            if width_mm <= max(pw, ph) and height_mm <= min(pw, ph)
            or width_mm <= min(pw, ph) and height_mm <= max(pw, ph)
        ]
        if fits:
            lines.append(f"{fits[0]} なら収まります")
        else:
            lines.append("A0でも収まりません。分割が必要です")

    # 合印が小さすぎて見えない
    notch_mm = float(getattr(context.scene, "tsunfold_notch_length_mm", 6.0))
    if notch_mm > 0.0 and longest / notch_mm > 300.0:
        lines.append(
            f"合印 {notch_mm:.1f} mm は型紙に対して小さすぎます"
        )

    # 島の間隔が型紙より大きいと、島が散らばって全体が巨大に見える。
    # 間隔は絶対値のミリ指定なので、基準を小さくすると相対的に効きすぎる。
    spacing_mm = float(getattr(context.scene, "tsunfold_spacing_mm", 10.0))
    islands = len(_get_face_islands(unfold_obj.data))
    if islands > 1 and spacing_mm > 0.0:
        # 間隔の総和が型紙の長辺の半分を超えたら、間隔が支配的
        total_gap = spacing_mm * (islands - 1)
        if total_gap > longest * 0.5:
            lines.append(
                f"島の間隔 {spacing_mm:g} mm が型紙に対して大きすぎます"
            )

    # 原因が Unit Scale にありそうな場合だけ添える
    if lines:
        scale, mm_per_bu = _scene_unit_summary(context.scene)
        if mm_per_bu >= 100.0:
            lines.append(
                f"Unit Scale {scale:g}（1 BU = {mm_per_bu:g} mm）を確認してください"
            )

    return lines


def _pattern_manual_notch_count(context):
    """手動で置かれた合印の数。"""
    source = _pattern_seam_source(context)
    if source is None:
        source = _pattern_source_object_from_context(context)

    if source is None or source.type != 'MESH':
        return 0

    return sum(
        1
        for item in _pattern_get_annotations(source)
        if item.get("type") == "notch_edge" and not bool(item.get("auto", False))
    )


def _pattern_notch_status_text(context):
    """パネルに出す合印の現状。

    設定を変えても見た目の変化が分かりにくく、効いているのか
    判断できないという声があったため、件数を出して手応えを返す。
    """
    source = _pattern_seam_source(context)
    if source is None:
        source = _pattern_source_object_from_context(context)

    if source is None or source.type != 'MESH':
        return "元モデルが未読み込み"

    if _pattern_unfold_for_source(source) is None:
        return "型紙が未作成"

    auto = manual = 0
    for item in _pattern_get_annotations(source):
        if item.get("type") != "notch_edge":
            continue
        if bool(item.get("auto", False)):
            auto += 1
        else:
            manual += 1

    if auto == 0 and manual == 0:
        return "合印なし（「作成 / 更新」を押してください）"

    text = f"合印 {auto + manual} 個"
    if manual:
        text += f"（オート {auto} / 手動 {manual}）"
    return text


def _pattern_redraw_only_updated(self, context):
    """再描画するだけでよい設定の更新。

    以下のどちらかに当てはまる設定は、キャッシュを捨てる必要がない。

      1. すでに描画キャッシュのキーに含まれている設定
         （値を変えれば別のキーになるので、古い結果は自然に使われない）
      2. キャッシュを通さず、描画のたびにシーンから読み直している設定

    _pattern_invalidate_layout_cache() は epoch を進めて全キャッシュを
    破棄するため、スライダーをドラッグすると1フレームごとに
    島の解析・ID配置探索・矢印配置探索がまとめて作り直されていた。
    数値を少し変えるだけで重くなっていた原因。
    """
    _tag_redraw()



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
                    unfold.get("tsunfold_source", "")
                )

        if source is None:
            return

        scene[_session.PREVIEW_SOURCE_NAME] = source.name
        scene[_session.PREVIEW_SOURCE_HIDE_GET] = bool(
            source.hide_get()
        )
        scene[_session.PREVIEW_SOURCE_HIDE_VIEWPORT] = bool(
            source.hide_viewport
        )

        source.hide_set(True)
        source.hide_viewport = True

    else:
        source_name = str(
            scene.get(_session.PREVIEW_SOURCE_NAME, "")
        )
        source = bpy.data.objects.get(source_name)

        if source is not None:
            try:
                source.hide_viewport = bool(
                    scene.get(
                        _session.PREVIEW_SOURCE_HIDE_VIEWPORT,
                        False,
                    )
                )
                source.hide_set(
                    bool(
                        scene.get(
                            _session.PREVIEW_SOURCE_HIDE_GET,
                            False,
                        )
                    )
                )
            except Exception:
                _debug.swallowed("unfold._pattern_print_preview_source_visibility")

        scene[_session.PREVIEW_SOURCE_NAME] = ""
        scene[_session.PREVIEW_SOURCE_HIDE_GET] = False
        scene[_session.PREVIEW_SOURCE_HIDE_VIEWPORT] = False

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

    _pack_islands(context, obj, context.scene.tsunfold_spacing_mm)

    if was_edit:
        try:
            bpy.ops.object.mode_set(mode='EDIT')
        except RuntimeError:
            _debug.swallowed("unfold._spacing_updated")

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
        _debug.swallowed("unfold._show_generated_from_top")









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



def _segments_bbox(segments):
    pts = [p for seg in segments for p in seg]
    if not pts:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _export_paper_dimensions(scene, shape_w_mm, shape_h_mm):
    paper_key = str(getattr(scene, "tsunfold_paper_size", "A4"))

    if paper_key == "CUSTOM":
        return _paper_dimensions_mm(scene)

    orientation = str(
        getattr(scene, "tsunfold_orientation", "PORTRAIT")
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


def _draw_line_rgb(buf, width, height, x0, y0, x1, y1, thickness=1,
                   color=(0.0, 0.0, 0.0)):
    """ピクセルバッファへ直線を引く。実装は truescale.export.png。"""
    _png.draw_line(buf, width, height, x0, y0, x1, y1, thickness, color)


def _write_png_rgb(filepath, width, height, rgb_buffer, dpi):
    """RGBバッファを PNG として書き出す。実装は truescale.export.png。"""
    _png.write_rgb(filepath, width, height, rgb_buffer, dpi)



# ------------------------------------------------------------
# Smooth finishing-line helpers
# ------------------------------------------------------------

SMOOTH_SUFFIX = "_なめらか線"




def _active_smooth_object(context):
    obj = context.active_object
    if (
        obj
        and obj.type == 'CURVE'
        and bool(obj.get("tsunfold_smooth_generated", False))
    ):
        return obj
    return None




# ------------------------------------------------------------
# Annotation -> rough seam helpers
# ------------------------------------------------------------

# ------------------------------------------------------------
# Annotation -> clean curve -> knife helpers
# ------------------------------------------------------------

# ------------------------------------------------------------
# Experimental free-seam helpers
# ------------------------------------------------------------

# ------------------------------------------------------------
# Operators
# ------------------------------------------------------------


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
    use_x = bool(getattr(scene, "tsunfold_seam_symmetry_x", False))
    use_y = bool(getattr(scene, "tsunfold_seam_symmetry_y", False))
    use_z = bool(getattr(scene, "tsunfold_seam_symmetry_z", False))

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
        getattr(context.scene, "tsunfold_seam_symmetry_x", False)
    )
    mesh.use_mirror_y = bool(
        getattr(context.scene, "tsunfold_seam_symmetry_y", False)
    )
    mesh.use_mirror_z = bool(
        getattr(context.scene, "tsunfold_seam_symmetry_z", False)
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












def _pattern_auto_notch_division_value(scene):
    """Safely normalize auto-notch division selector to 2 / 3 / 4."""
    raw = getattr(
        scene,
        "tsunfold_auto_notch_divisions",
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

    mode = str(getattr(context.scene, "tsunfold_notch_mode", "AUTO"))
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

    count = 0

    for trail in _pattern_seam_trails(source_obj):
        for edge_index, fraction in _pattern_trail_mark_positions(
            context,
            source_obj,
            trail,
            divisions,
        ):
            # 色は保存しない。オート合印は常に現在のシーン設定に従う
            # （_pattern_item_color を参照）。保存すると色を変えるたびに
            # 全アノテーションの書き直しが必要になる。
            items.append({
                "type": "notch_edge",
                "edge": int(edge_index),
                "t": float(fraction),
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
            and bool(obj.get("tsunfold_generated", False))
            and obj.get("tsunfold_source", "") == source_obj.name
        )
    ]

    old_names = {obj.name for obj in old_meshes}

    old_curves = [
        obj
        for obj in list(bpy.data.objects)
        if (
            obj.type == 'CURVE'
            and bool(obj.get("tsunfold_smooth_generated", False))
            and (
                obj.get("tsunfold_smooth_source", "") in old_names
                or obj.get("tsunfold_source", "") == source_obj.name
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

    context.scene.tsunfold_pattern_preview = False
    context.scene.tsunfold_preview = False
    context.scene[_session.PREVIEW_PREV_ACTIVE] = ""
    context.scene[_session.DISPLAY_MODE] = "POLY"
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
            _debug.swallowed("unfold.TSUNFOLD_OT_refresh_auto_notches.execute")

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                _debug.swallowed("unfold.TSUNFOLD_OT_refresh_auto_notches.execute")

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
    bl_label = "オート合印だけ削除"
    bl_description = (
        "自動生成した合印だけを削除します。手動で置いた合印は残します"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _pattern_source_object_from_context(context)
        if source is None:
            return {'CANCELLED'}

        count = _pattern_remove_auto_notches(source)
        self.report({'INFO'}, f"オート合印を {count} 個削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_remove_all_notches(bpy.types.Operator):
    bl_idname = "truescale_unfold.remove_all_notches"
    bl_label = "合印を削除"
    bl_description = (
        "オートと手動の両方の合印を削除します。"
        "型紙IDや矢印など他のマーキングは残します"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _pattern_source_object_from_context(context) is not None

    def execute(self, context):
        source = _pattern_source_object_from_context(context)
        if source is None:
            return {'CANCELLED'}

        items = _pattern_get_annotations(source)
        auto = manual = 0
        remaining = []

        for item in items:
            if item.get("type") == "notch_edge":
                if bool(item.get("auto", False)):
                    auto += 1
                else:
                    manual += 1
                continue
            remaining.append(item)

        if auto == 0 and manual == 0:
            self.report({'INFO'}, "削除する合印がありません")
            return {'CANCELLED'}

        _pattern_set_annotations(source, remaining)

        detail = f"（オート {auto} / 手動 {manual}）" if manual else ""
        self.report(
            {'INFO'},
            f"合印を {auto + manual} 個削除しました{detail}",
        )
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
        _debug.swallowed("unfold._focus_selected_unfold")

    try:
        with context.temp_override(area=area, region=region, space_data=space):
            if top_view:
                bpy.ops.view3d.view_axis(type='TOP', align_active=False)
            bpy.ops.view3d.view_selected(use_all_regions=False)
    except Exception:
        _debug.swallowed("unfold._focus_selected_unfold")


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



class TSUNFOLD_OT_calibrate_scale(bpy.types.Operator):
    bl_idname = "truescale_unfold.calibrate_scale"
    bl_label = "選択した辺を基準に実寸を決める"
    bl_description = (
        "編集モードで選んだ辺の合計長さを、実際の寸法として指定します。"
        "そこから 1 BU が何ミリかを逆算します"
    )
    bl_options = {'REGISTER', 'UNDO'}

    target_mm: FloatProperty(
        name="実際の寸法 (mm)",
        description="選んだ辺が実物で何ミリあるか",
        default=100.0,
        min=0.001,
        soft_max=5000.0,
        precision=2,
    )

    _length_bu = 0.0

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and obj.mode == 'EDIT'
        )

    @staticmethod
    def _selected_length_bu(obj):
        """選択されている辺の合計長さ（Blender Unit、ワールド基準）。

        連続した辺を選べば、首周りのような曲線も測れる。
        """
        import bmesh

        bm = bmesh.from_edit_mesh(obj.data)
        matrix = obj.matrix_world

        total = 0.0
        for edge in bm.edges:
            if not edge.select:
                continue
            a = matrix @ edge.verts[0].co
            b = matrix @ edge.verts[1].co
            total += (b - a).length
        return total

    def invoke(self, context, event):
        obj = context.active_object
        self._length_bu = self._selected_length_bu(obj)

        if self._length_bu <= 1e-9:
            self.report({'WARNING'}, "辺が選択されていません")
            return {'CANCELLED'}

        # いまの基準での寸法を初期値にしておくと、
        # 「少しだけ直したい」場合に扱いやすい。
        self.target_mm = max(0.001, _bu_to_mm(context.scene, self._length_bu))
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"選択した辺の長さ: {self._length_bu:.6g} BU")
        layout.prop(self, "target_mm")

        if self._length_bu <= 0.0:
            return

        mm_per_bu = self.target_mm / self._length_bu
        layout.label(text=f"→ 1 BU = {mm_per_bu:.6g} mm", icon='DRIVER')

        # 桁を間違えたまま確定しないよう、結果の見当を先に出す。
        # 初期値が数千ミリになることがあり、桁の誤りに気づきにくい。
        current = _scene_bu_to_mm(context.scene)
        if current > 0.0:
            ratio = mm_per_bu / current
            if ratio >= 2.0 or ratio <= 0.5:
                layout.label(
                    text=f"現在の基準の {ratio:.4g} 倍になります",
                    icon='ERROR' if (ratio >= 100.0 or ratio <= 0.01) else 'INFO',
                )

        # 型紙ができていれば、その全体寸法がどうなるかを見せる
        source = _pattern_seam_source(context)
        unfold = _pattern_unfold_for_source(source) if source else None
        if unfold is not None:
            mesh = unfold.data
            if mesh.vertices:
                xs = [v.co.x for v in mesh.vertices]
                ys = [v.co.y for v in mesh.vertices]
                width = (max(xs) - min(xs)) * mm_per_bu
                height = (max(ys) - min(ys)) * mm_per_bu
                layout.label(
                    text=f"型紙全体: 約 {width:.1f} × {height:.1f} mm"
                )

        spacing_mm = float(getattr(context.scene, "tsunfold_spacing_mm", 10.0))
        layout.label(text=f"島の間隔 {spacing_mm:g} mm も同じ基準で扱われます")

    def execute(self, context):
        if self._length_bu <= 1e-9:
            # ダイアログを経ずに呼ばれた場合に備えて測り直す
            obj = context.active_object
            if obj is None or obj.mode != 'EDIT':
                self.report({'ERROR'}, "編集モードで辺を選んでください")
                return {'CANCELLED'}
            self._length_bu = self._selected_length_bu(obj)

        if self._length_bu <= 1e-9:
            self.report({'ERROR'}, "辺が選択されていません")
            return {'CANCELLED'}

        scene = context.scene
        scene.tsunfold_manual_mm_per_bu = self.target_mm / self._length_bu
        scene.tsunfold_scale_mode = "MANUAL"

        _pattern_invalidate_layout_cache()
        self.report(
            {'INFO'},
            f"1 BU = {scene.tsunfold_manual_mm_per_bu:.6g} mm に設定しました",
        )
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
        return obj is not None and obj.type == 'MESH' and not bool(obj.get("tsunfold_generated", False))

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
                _debug.swallowed("unfold.TSUNFOLD_OT_unfold_real_mesh.execute")

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
            _debug.swallowed("unfold.TSUNFOLD_OT_unfold_real_mesh.execute")

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
                _debug.swallowed("unfold.TSUNFOLD_OT_unfold_real_mesh.execute")

        if previous_uv_name:
            previous_layer = mesh.uv_layers.get(previous_uv_name)
            if previous_layer is not None:
                mesh.uv_layers.active = previous_layer

        if result is None:
            self.report({'ERROR'}, "平面Meshを生成できませんでした。")
            return {'CANCELLED'}

        _pack_islands(context, result, context.scene.tsunfold_spacing_mm)
        _pattern_invalidate_layout_cache()
        _show_generated_from_top(context, result)
        _focus_selected_unfold(context, top_view=True)

        size = _object_xy_size_mm(context, result)
        if size:
            self.report({'INFO'}, f"{result.name}：{size[0]:.1f} × {size[1]:.1f} mm")
        return {'FINISHED'}



def _pattern_seam_source(context):
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
            and not bool(obj.get("tsunfold_generated", False))
        )

    def execute(self, context):
        _pattern_sanitize_arrow_axis(context.scene)
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'WARNING'}, "シーム付きMeshオブジェクトを選択してください")
            return {'CANCELLED'}

        if bool(obj.get("tsunfold_generated", False)):
            self.report({'WARNING'}, "展開図ではなく元の3Dモデルを選択してください")
            return {'CANCELLED'}

        # Blender標準のEdit Modeで入れた最新シームも読み込む。
        _pattern_sync_live_seams(obj)

        try:
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            _debug.swallowed("unfold.TSUNFOLD_OT_load_seamed_object.execute")

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

        context.scene[_session.SEAM_SOURCE] = obj.name
        context.scene[_session.SEAM_PREVIEW_READY] = False

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
            _debug.swallowed("unfold.TSUNFOLD_OT_build_pattern.execute")

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                _debug.swallowed("unfold.TSUNFOLD_OT_build_pattern.execute")

        source.hide_set(False)
        source.hide_viewport = False
        source.select_set(True)
        context.view_layer.objects.active = source
        context.scene[_session.SEAM_SOURCE] = source.name

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

        context.scene[_session.SEAM_PREVIEW_READY] = False

        auto_notch_count = 0
        if str(
            getattr(
                context.scene,
                "tsunfold_notch_mode",
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


class TSUNFOLD_OT_select_unfold_source(bpy.types.Operator):
    bl_idname = "truescale_unfold.select_unfold_source"
    bl_label = "元の展開図を選択"
    bl_description = "なめらか線の元になった展開図Meshを選択します"

    @classmethod
    def poll(cls, context):
        return _active_smooth_object(context) is not None

    def execute(self, context):
        smooth = context.active_object
        source_name = smooth.get("tsunfold_smooth_source", "")
        source = bpy.data.objects.get(source_name)

        if source is None:
            self.report({'ERROR'}, "元の展開図が見つかりません")
            return {'CANCELLED'}

        for o in context.selected_objects:
            o.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source

        return {'FINISHED'}




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
            and bool(obj.get("tsunfold_generated", False))
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
            and bool(obj.get("tsunfold_generated", False))
        )

    def invoke(self, context, event):
        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = True
        _pattern_clear_island_highlight()
        _pattern_clear_live_preview()
        _tag_redraw()

        obj = _resolve_unfold_mesh_for_layout(context)

        if obj is None:
            return {'CANCELLED'}

        # Smooth finishing is a separate Curve. Manual layout edits operate
        # on the real flat Mesh, so temporarily return to POLY display.
        context.scene[_session.DISPLAY_MODE] = "POLY"
        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("tsunfold_smooth_generated", False))
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
                _debug.swallowed("unfold.TSUNFOLD_OT_layout_edit.invoke")

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
            or not bool(obj.get("tsunfold_generated", False))
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
                _debug.swallowed("unfold.TSUNFOLD_OT_layout_edit.modal")

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
            and bool(obj.get("tsunfold_generated", False))
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
                _debug.swallowed("unfold.TSUNFOLD_OT_layout_edit._finish")

        _tag_redraw()

        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                _debug.swallowed("unfold.TSUNFOLD_OT_layout_edit._finish")
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
                    _debug.swallowed("unfold.TSUNFOLD_OT_layout_confirm.execute")

                bpy.ops.object.mode_set(mode='OBJECT')

        except Exception as exc:
            self.report(
                {'ERROR'},
                f"レイアウト確定に失敗しました: {exc}",
            )
            return {'CANCELLED'}

        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        _pattern_invalidate_layout_cache()
        _tag_redraw()

        try:
            _show_generated_from_top(context, obj)
        except Exception:
            _debug.swallowed("unfold.TSUNFOLD_OT_layout_confirm.execute")

        self.report({'INFO'}, "レイアウトを確定しました")
        return {'FINISHED'}


class TSUNFOLD_OT_delete_unfold(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_unfold"
    bl_label = "展開図を削除"
    bl_description = "生成されたローポリ展開図となめらか線をまとめて削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        try:
            context.scene.tsunfold_pattern_preview = False
            context.scene[_session.PREVIEW_PREV_ACTIVE] = ""
        except Exception:
            _debug.swallowed("unfold.TSUNFOLD_OT_delete_unfold.execute")

        mesh_targets = [
            obj for obj in list(bpy.data.objects)
            if obj.type == 'MESH' and bool(obj.get("tsunfold_generated", False))
        ]

        curve_targets = [
            obj for obj in list(bpy.data.objects)
            if obj.type == 'CURVE' and bool(obj.get("tsunfold_smooth_generated", False))
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
        context.scene.tsunfold_show_paper = False
        context.scene.tsunfold_preview = False

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
                    and bool(candidate.get("tsunfold_generated", False))
                ):
                    unfold = candidate
                    break

        if unfold is None:
            self.report({'WARNING'}, "先に「型紙展開」で型紙を作成してください")
            return {'CANCELLED'}

        showing = bool(getattr(scene, "tsunfold_pattern_preview", False))

        if not showing:
            active = context.active_object
            scene[_session.PREVIEW_PREV_ACTIVE] = (
                active.name if active is not None else ""
            )

            for obj in context.selected_objects:
                try:
                    obj.select_set(False)
                except Exception:
                    _debug.swallowed("unfold.TSUNFOLD_OT_toggle_pattern_preview.execute")

            unfold.hide_set(False)
            unfold.hide_viewport = False
            unfold.select_set(True)
            context.view_layer.objects.active = unfold

            for obj in bpy.data.objects:
                if (
                    obj.type == 'CURVE'
                    and bool(obj.get("tsunfold_smooth_generated", False))
                ):
                    obj.hide_viewport = True

            scene.tsunfold_pattern_preview = True

            # 表示切替だけ行い、ビュー方向・ズーム・注視点は変更しない。
            # 元モデルが「消えたように見える」原因になる自動フレーミングを廃止。
            self.report({'INFO'}, "生成済み型紙を表示しました")

        else:
            unfold.hide_set(True)
            unfold.hide_viewport = True

            prev_name = scene.get(_session.PREVIEW_PREV_ACTIVE, "")
            prev = bpy.data.objects.get(prev_name)

            if prev is None:
                src_name = unfold.get("tsunfold_source", "")
                prev = bpy.data.objects.get(src_name)

            if prev is not None:
                for obj in context.selected_objects:
                    try:
                        obj.select_set(False)
                    except Exception:
                        _debug.swallowed("unfold.TSUNFOLD_OT_toggle_pattern_preview.execute")

                # 元モデルの表示状態は専用トグルの設定を尊重する。
                if not prev.hide_get():
                    prev.select_set(True)
                    context.view_layer.objects.active = prev

            scene.tsunfold_pattern_preview = False
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
        scene.tsunfold_preview = not scene.tsunfold_preview

        _pattern_print_preview_source_visibility(
            context,
            bool(scene.tsunfold_preview),
        )
        _tag_redraw()

        if scene.tsunfold_preview:
            self.report({'INFO'}, "Object Modeへ切り替えて印刷プレビュー ON")
        else:
            self.report({'INFO'}, "印刷プレビュー OFF")
        return {'FINISHED'}



def _export_outline_segments(context):
    """Return outline segments for the currently displayed finish."""
    mode = context.scene.get(_session.DISPLAY_MODE, "POLY")
    obj = context.active_object

    if (
        mode == "SMOOTH"
        or (
            obj
            and obj.type == 'CURVE'
            and bool(obj.get("tsunfold_smooth_generated", False))
        )
    ):
        if (
            obj
            and obj.type == 'CURVE'
            and bool(obj.get("tsunfold_smooth_generated", False))
        ):
            return _smooth_curve_segments_world_xy(obj)

        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("tsunfold_smooth_generated", False))
                and not candidate.hide_viewport
            ):
                return _smooth_curve_segments_world_xy(candidate)

    if (
        obj
        and obj.type == 'MESH'
        and bool(obj.get("tsunfold_generated", False))
    ):
        return _boundary_segments_world_xy(obj)

    for candidate in bpy.data.objects:
        if (
            candidate.type == 'MESH'
            and bool(candidate.get("tsunfold_generated", False))
            and not candidate.hide_viewport
        ):
            return _boundary_segments_world_xy(candidate)

    return []


def _can_export_current_finish(context):
    obj = context.active_object
    if obj is None:
        return False

    if obj.type == 'MESH' and bool(obj.get("tsunfold_generated", False)):
        return True

    if obj.type == 'CURVE' and bool(obj.get("tsunfold_smooth_generated", False)):
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
                _debug.swallowed("unfold._pattern_text_outline_segments")

        if curve is not None and curve.users == 0:
            try:
                bpy.data.curves.remove(curve)
            except Exception:
                _debug.swallowed("unfold._pattern_text_outline_segments")


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

        last_dir = context.scene.get(_session.LAST_EXPORT_DIR, "")
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
        context.scene[_session.LAST_EXPORT_DIR] = str(filepath.parent)

        self.report(
            {'INFO'},
            f"実寸PNGを書き出しました / {PRINT_DPI}dpi / 次回もこの保存先を開きます"
        )
        return {'FINISHED'}



# ------------------------------------------------------------
# 3D pattern annotation system
# ------------------------------------------------------------

# 注記の保存先は truescale.marking.storage が持つ。
_PATTERN_ANNOTATION_PROP = _storage.ANNOTATION_PROP


_PATTERN_FLAT_MEMO_PROP = "tsunfold_flat_memos_json"

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
            or not bool(original.get("tsunfold_generated", False))
        ):
            return None

        local = original.matrix_world.inverted() @ Vector(location)
        return original, local

    except Exception:
        return None




def _pattern_marking_session_active(scene):
    return bool(scene.get(_session.MARKING_SESSION_ACTIVE, False))


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

        scene[_session.MARKING_PREV_ACTIVE] = (
            active.name if active is not None else ""
        )
        scene[_session.MARKING_PREV_SELECTED_JSON] = json.dumps(
            selected_names,
            ensure_ascii=False,
        )
        scene[_session.MARKING_PREV_MODE] = (
            active.mode if active is not None else "OBJECT"
        )
        scene[_session.MARKING_SESSION_ACTIVE] = True
        scene[_session.MARKING_FINISH_REQUESTED] = False

    # Marking always happens on the original source Mesh in Object Mode.
    try:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        _debug.swallowed("unfold._pattern_begin_marking_session")

    for obj in context.selected_objects:
        try:
            obj.select_set(False)
        except Exception:
            _debug.swallowed("unfold._pattern_begin_marking_session")

    source_obj.hide_set(False)
    source_obj.hide_viewport = False
    source_obj.select_set(True)
    context.view_layer.objects.active = source_obj

    _tag_redraw()


def _pattern_request_finish_marking(context):
    context.scene[_session.MARKING_FINISH_REQUESTED] = True
    _tag_redraw()


def _pattern_restore_work_state(context):
    """Restore active object, selection and mode saved before marking."""
    scene = context.scene

    prev_active_name = scene.get(
        _session.MARKING_PREV_ACTIVE,
        "",
    )
    prev_mode = scene.get(
        _session.MARKING_PREV_MODE,
        "OBJECT",
    )

    try:
        selected_names = json.loads(
            scene.get(
                _session.MARKING_PREV_SELECTED_JSON,
                "[]",
            )
        )
    except Exception:
        selected_names = []

    try:
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        _debug.swallowed("unfold._pattern_restore_work_state")

    for obj in context.selected_objects:
        try:
            obj.select_set(False)
        except Exception:
            _debug.swallowed("unfold._pattern_restore_work_state")

    for name in selected_names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            try:
                obj.select_set(True)
            except Exception:
                _debug.swallowed("unfold._pattern_restore_work_state")

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
                _debug.swallowed("unfold._pattern_restore_work_state")
        elif prev_mode == 'SCULPT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='SCULPT')
            except Exception:
                _debug.swallowed("unfold._pattern_restore_work_state")
        elif prev_mode == 'VERTEX_PAINT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='VERTEX_PAINT')
            except Exception:
                _debug.swallowed("unfold._pattern_restore_work_state")
        elif prev_mode == 'WEIGHT_PAINT' and active.type == 'MESH':
            try:
                bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            except Exception:
                _debug.swallowed("unfold._pattern_restore_work_state")

    scene[_session.MARKING_SESSION_ACTIVE] = False
    scene[_session.MARKING_FINISH_REQUESTED] = False
    scene[_session.MODAL_RUNNING] = False
    scene.tsunfold_active_tool = "NONE"
    scene[_session.MARKING_PREV_ACTIVE] = ""
    scene[_session.MARKING_PREV_SELECTED_JSON] = "[]"
    scene[_session.MARKING_PREV_MODE] = "OBJECT"

    _tag_redraw()


class TSUNFOLD_OT_finish_marking(bpy.types.Operator):
    bl_idname = "truescale_unfold.finish_marking"
    bl_label = "マーキング終了"
    bl_description = "3D型紙マーキングを終了し、開始前の選択・モードへ戻ります"

    def execute(self, context):
        if not _pattern_marking_session_active(context.scene):
            self.report({'INFO'}, "現在マーキングモードではありません")
            return {'CANCELLED'}

        context.scene.tsunfold_active_tool = "NONE"
        _pattern_clear_live_preview()
        context.scene[_session.MODAL_RUNNING] = False
        _pattern_request_finish_marking(context)
        _pattern_restore_work_state(context)

        self.report({'INFO'}, "マーキングを終了し、元の作業状態へ戻しました")
        return {'FINISHED'}






def _pattern_get_annotations(source_obj):
    """注記の一覧。実装は truescale.marking.storage。"""
    return _storage.load(source_obj)


def _pattern_set_annotations(source_obj, annotations):
    """注記を書き込み、キャッシュを捨てる。

    保存の実装は truescale.marking.storage。
    キャッシュ破棄は描画側の都合なので、ここで渡す。
    """
    _storage.save(
        source_obj,
        annotations,
        on_changed=_pattern_invalidate_layout_cache,
    )








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




def _pattern_item_color(item, scene=None):
    """注記1件の色。実装は truescale.marking.storage。

    オート合印は保存値を持たず、常に現在のシーン設定に従う。
    保存すると色を変えるたびに全件の書き直しが必要になり、
    カラーピッカーのドラッグ中に毎フレーム走ってしまう。
    """
    auto_color = None
    if scene is not None:
        auto_color = getattr(scene, "tsunfold_notch_color", None)
    return _storage.item_color(item, auto_color)














def _pattern_invalidate_layout_cache():
    """型紙や注記が変わったときに呼ぶ。キャッシュを捨てて描き直す。

    キャッシュの実体は truescale.core.state。
    ここでは再描画の要求だけを足している。
    """
    _state.invalidate()
    _tag_redraw()






























































































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
        _debug.swallowed("unfold._pattern_nearest_seam_edge")

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
    """保存用に色を整える。実装は truescale.marking.storage。"""
    return _storage.normalize_color(value)


def _pattern_current_color(scene, mode=None):
    mode = mode or _pattern_active_tool(scene)

    if mode == "NOTCH":
        return _pattern_color_value(scene.tsunfold_notch_color)
    if mode == "NUMBER":
        return _pattern_color_value(scene.tsunfold_number_color)
    if mode == "TEXT":
        return _pattern_color_value(scene.tsunfold_text_color)
    if mode == "ARROW":
        return _pattern_color_value(scene.tsunfold_arrow_color)

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


def _pattern_preview_anchor_world(source_obj, anchor):
    if source_obj is None or anchor is None:
        return None
    p = _pattern_anchor_point_source_local(source_obj, anchor)
    if p is None:
        return None
    return source_obj.matrix_world @ p


def _pattern_active_tool(scene):
    try:
        return str(scene.tsunfold_active_tool)
    except Exception:
        return str(scene.get("tsunfold_active_tool", "NONE"))


def _pattern_set_active_tool(scene, mode):
    value = str(mode)
    try:
        scene.tsunfold_active_tool = value
    except Exception:
        scene["tsunfold_active_tool"] = value
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
        context.scene[_session.MODAL_RUNNING] = False
        context.scene[_session.MARKING_FINISH_REQUESTED] = False
        operator.report({'INFO'}, "マーキングツールをOFFにしました")
        return {'FINISHED'}

    _pattern_begin_marking_session(context, source)

    if mode == "NUMBER":
        context.scene.tsunfold_next_number = int(
            context.scene.tsunfold_number_start
        )

    _pattern_set_active_tool(context.scene, mode)
    _pattern_clear_live_preview()
    _pattern_live_preview["mode"] = mode
    _pattern_live_preview["source"] = source.name

    if not bool(context.scene.get(_session.MODAL_RUNNING, False)):
        operator._source_name = source.name
        operator._first_anchor = None
        operator._last_mode = mode
        context.scene[_session.MODAL_RUNNING] = True
        context.window_manager.modal_handler_add(operator)
        return {'RUNNING_MODAL'}

    return {'FINISHED'}


def _pattern_modal_common(operator, context, event):
    scene = context.scene

    if bool(scene.get(_session.MARKING_FINISH_REQUESTED, False)):
        _pattern_clear_live_preview()
        scene[_session.MODAL_RUNNING] = False
        return {'FINISHED'}

    mode = _pattern_active_tool(scene)

    if mode == "NONE":
        _pattern_clear_live_preview()
        scene[_session.MODAL_RUNNING] = False
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
        scene[_session.MODAL_RUNNING] = False
        scene[_session.MARKING_FINISH_REQUESTED] = False
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
        scene[_session.MODAL_RUNNING] = False
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
        if scene.tsunfold_notch_mode == "NONE":
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
        value = int(scene.tsunfold_next_number)

        items.append({
            "type": "number",
            "value": value,
            "anchor": anchor,
            "size_mm": float(scene.tsunfold_number_size_mm),
            "color": color,
        })
        _pattern_set_annotations(source, items)

        scene.tsunfold_next_number = value + 1
        operator.report({'INFO'}, f"型紙番号 {value} を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "TEXT":
        text = str(scene.tsunfold_custom_text)

        if not text:
            operator.report({'WARNING'}, "任意テキストを入力してください")
            return {'RUNNING_MODAL'}

        items.append({
            "type": "text",
            "text": text,
            "anchor": anchor,
            "size_mm": float(scene.tsunfold_text_size_mm),
            "color": color,
        })
        _pattern_set_annotations(source, items)

        operator.report({'INFO'}, f"「{text}」を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "ARROW":
        if str(
            getattr(scene, "tsunfold_arrow_mode", "AUTO")
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
            "head_mm": float(scene.tsunfold_arrow_head_mm),
            "thickness_mm": float(scene.tsunfold_arrow_thickness_mm),
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
        context.scene[_session.MODAL_RUNNING] = False
        context.scene[_session.MARKING_FINISH_REQUESTED] = False
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
            or not bool(unfold.get("tsunfold_generated", False))
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
            _debug.swallowed("unfold.TSUNFOLD_OT_confirm_flat_memo.execute")

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
                "tsunfold_correspondence_mode",
                False,
            )
        ):
            scene.tsunfold_correspondence_mode = False
            _pattern_clear_island_highlight()
            self.report({'INFO'}, "対応確認をOFFにしました")
            return {'FINISHED'}

        scene.tsunfold_correspondence_mode = True
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
                        _debug.swallowed("unfold.TSUNFOLD_OT_pick_corresponding_island.modal")
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
                "tsunfold_correspondence_mode",
                False,
            )
        ):
            return {'FINISHED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            scene.tsunfold_correspondence_mode = False
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
            _debug.swallowed("unfold.TSUNFOLD_OT_pick_corresponding_island.modal")

        if (
            hit_obj.type == 'MESH'
            and bool(hit_obj.get("tsunfold_generated", False))
        ):
            unfold = hit_obj
            source = bpy.data.objects.get(
                unfold.get("tsunfold_source", "")
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
            and not bool(hit_obj.get("tsunfold_generated", False))
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
        context.scene.tsunfold_correspondence_mode = False
        _pattern_clear_island_highlight()
        return {'FINISHED'}






class TSUNFOLD_OT_toggle_direction_arrow(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_direction_arrow"
    bl_label = "水色の方向ガイド"
    bl_description = "画面左側の水色の方向矢印を表示/非表示します"

    def execute(self, context):
        scene = context.scene
        scene.tsunfold_show_direction_arrow = not bool(
            scene.tsunfold_show_direction_arrow
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
                "tsunfold_preview",
                False,
            )
        ):
            _pattern_print_preview_source_visibility(
                context,
                False,
            )

        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        scene = context.scene
        source = _pattern_seam_source(context)

        if source is None:
            unfold = _resolve_unfold_mesh_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("tsunfold_source", "")
                )

        if source is None:
            active = context.active_object
            if (
                active is not None
                and active.type == 'MESH'
                and not bool(
                    active.get(
                        "tsunfold_generated",
                        False,
                    )
                )
            ):
                source = active

        # Stop all modes first.
        try:
            _pattern_set_active_tool(scene, "NONE")
        except Exception:
            _debug.swallowed("unfold.TSUNFOLD_OT_return_default.execute")

        _pattern_clear_live_preview()
        _pattern_clear_island_highlight()

        scene.tsunfold_correspondence_mode = False
        scene[_session.MODAL_RUNNING] = False
        scene[_session.MARKING_SESSION_ACTIVE] = False
        scene[_session.MARKING_FINISH_REQUESTED] = False
        scene[_session.SEAM_PREVIEW_READY] = False


        scene.tsunfold_preview = False
        scene.tsunfold_show_paper = False
        scene.tsunfold_pattern_preview = False

        try:
            active = context.active_object
            if active is not None and active.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            _debug.swallowed("unfold.TSUNFOLD_OT_return_default.execute")

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
                _debug.swallowed("unfold.TSUNFOLD_OT_return_default.execute")

        # Hide/delete state is now clean; restore just the source selection.
        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                _debug.swallowed("unfold.TSUNFOLD_OT_return_default.execute")

        if source is not None:
            source.hide_set(False)
            source.hide_viewport = False
            source.select_set(True)
            context.view_layer.objects.active = source
            scene[_session.SEAM_SOURCE] = ""
        else:
            scene[_session.SEAM_SOURCE] = ""

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
                    unfold.get("tsunfold_source", "")
                )

        if source is not None:
            items = [
                item
                for item in _pattern_get_annotations(source)
                if str(item.get("type", "")) != "arrow"
            ]
            _pattern_set_annotations(source, items)

        context.scene.tsunfold_arrow_mode = "NONE"
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
                    unfold.get("tsunfold_source", "")
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
        context.scene.tsunfold_number_start = 1
        context.scene.tsunfold_next_number = 1
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
                    unfold.get("tsunfold_source", "")
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
        unit_box.label(text="実寸の基準")
        unit_box.prop(scene, "tsunfold_scale_mode", text="")

        scene_scale, mm_per_bu = _scene_unit_summary(scene)

        if scene.tsunfold_scale_mode == "MANUAL":
            unit_box.prop(scene, "tsunfold_manual_mm_per_bu", text="1 BU =")
            # シーン側の値も併記する。どちらが使われているかを明確にするため。
            unit_box.label(
                text=f"シーンの Unit Scale {_scene_unit_scale_raw(scene):g} は未使用",
                icon='INFO',
            )
        else:
            unit_box.label(
                text=f"Unit Scale {scene_scale:g} / 1 BU = {mm_per_bu:g} mm"
            )

        if context.mode == 'EDIT_MESH':
            unit_box.operator(
                "truescale_unfold.calibrate_scale",
                text="選択した辺を基準に決める",
                icon='DRIVER_DISTANCE',
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

        # いまどの段階にいて、次に何を押せばよいかを先頭に出す。
        icon, current, next_step = _pattern_workflow_status(context)
        status_box = layout.box()
        status_box.label(text=current, icon=icon)
        if next_step:
            status_box.label(text=next_step, icon='FORWARD')

        if unfold_obj is not None:
            for warning in _pattern_scale_warnings(context, unfold_obj):
                status_box.label(text=warning, icon='ERROR')

        source_box = layout.box()
        source_box.label(text="1. モデルと型紙")

        loaded_name = scene.get(_session.SEAM_SOURCE, "")
        loaded_obj = bpy.data.objects.get(loaded_name) if loaded_name else None

        source_box.operator(
            "truescale_unfold.load_seamed_object",
            text="モデルの読み込み",
            icon='IMPORT',
        )

        # 編集モードで選んだエッジをその場でシーム化できるようにする。
        # これが無いと、シームを足すたびにパネルの外へ出る必要があった。
        seam_box = source_box.box()
        seam_box.label(text="シーム編集", icon='EDGESEL')

        if context.mode == 'EDIT_MESH':
            seam_row = seam_box.row(align=True)
            seam_row.operator(
                "truescale_unfold.mark_seam",
                text="シームを入れる",
                icon='ADD',
            )
            seam_row.operator(
                "truescale_unfold.clear_seam",
                text="外す",
                icon='REMOVE',
            )

            sym_row = seam_box.row(align=True)
            sym_row.label(text="対称")
            sym_row.prop(scene, "tsunfold_seam_symmetry_x", text="X", toggle=True)
            sym_row.prop(scene, "tsunfold_seam_symmetry_y", text="Y", toggle=True)
            sym_row.prop(scene, "tsunfold_seam_symmetry_z", text="Z", toggle=True)
        else:
            seam_box.label(text="編集モードでエッジを選ぶと使えます")

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
                "tsunfold_lightweight_view",
                text="軽量ビュー",
            )

            # ONのあいだ元モデル側のマーキングが一切描かれない。
            # 既定でONなので、何も表示されない理由が分からなくなりやすい。
            if scene.tsunfold_lightweight_view:
                source_box.label(
                    text="元モデル側の合印表示はOFF",
                    icon='HIDE_ON',
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
            "tsunfold_notch_mode",
            text="方式",
        )

        if scene.tsunfold_notch_mode == "AUTO":
            notch_box.prop(
                scene,
                "tsunfold_auto_notch_divisions",
                text="分割数",
            )


        if scene.tsunfold_notch_mode == "AUTO":
            row = notch_box.row(align=True)
            row.operator(
                "truescale_unfold.refresh_auto_notches",
                text="作成 / 更新",
            )
            # ラベルどおり、オートと手動の両方を消す。
            # 以前はオートだけを消すオペレータに繋がっており、
            # 手動で置いた合印が残って戸惑う原因になっていた。
            row.operator(
                "truescale_unfold.remove_all_notches",
                text="合印削除",
                icon='X',
            )

            # 今いくつ合印があるのかを出す。
            # これが無いと、設定を変えても効いているのか分からなかった。
            notch_box.label(text=_pattern_notch_status_text(context))

            # 手動で置いた合印があるときだけ、残す選択肢も出す。
            # 常に2つ並べると、違いが分からず選べない。
            if _pattern_manual_notch_count(context) > 0:
                notch_box.operator(
                    "truescale_unfold.remove_auto_notches",
                    text="オートだけ削除（手動は残す）",
                    icon='X',
                )

        if scene.tsunfold_notch_mode != "NONE":
            notch_box.operator(
                "truescale_unfold.place_notch",
                text="手動で合印を追加",
            )

            # Frequently changed geometry setting first.
            notch_box.prop(
                scene,
                "tsunfold_notch_length_mm",
                text="合印の長さ",
            )
            notch_box.prop(
                scene,
                "tsunfold_notch_thickness_mm",
                text="合印の太さ",
            )

            # Color is secondary.
            color_row = notch_box.row(align=True)
            color_row.label(text="色")
            color_row.prop(
                scene,
                "tsunfold_notch_color",
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
            "tsunfold_auto_island_ids",
            text="自動IDを表示",
        )

        if scene.tsunfold_auto_island_ids:
            id_box.prop(
                scene,
                "tsunfold_island_id_style",
                text="形式",
            )

            # Size first, color second.
            id_box.prop(
                scene,
                "tsunfold_island_id_size_mm",
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
            "tsunfold_show_direction_arrow",
            text="水色の方向ガイド",
        )

        arrow_box.prop(
            scene,
            "tsunfold_arrow_mode",
            text="方式",
        )

        if scene.tsunfold_arrow_mode == "AUTO":
            arrow_box.prop(
                scene,
                "tsunfold_arrow_up_axis",
                text="上方向",
            )

            arrow_box.prop(
                scene,
                "tsunfold_auto_arrow_length_mm",
                text="矢印の長さ",
            )

        if scene.tsunfold_arrow_mode != "NONE":
            arrow_box.prop(
                scene,
                "tsunfold_arrow_head_mm",
                text="矢印ヘッド長さ",
            )
            arrow_box.prop(
                scene,
                "tsunfold_arrow_thickness_mm",
                text="線の太さ",
            )

            color_row = arrow_box.row(align=True)
            color_row.label(text="色")
            color_row.prop(
                scene,
                "tsunfold_arrow_color",
                text="",
            )

            if scene.tsunfold_arrow_mode in {"AUTO", "CUSTOM"}:
                arrow_box.operator(
                    "truescale_unfold.place_arrow",
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
            "truescale_unfold.clear_annotations",
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
                if scene.tsunfold_correspondence_mode
                else "対応確認を開始"
            ),
            depress=scene.tsunfold_correspondence_mode,
        )
        corr_box.operator(
            "truescale_unfold.clear_island_highlight",
            text="対応ハイライトをクリア",
        )

        if scene.tsunfold_correspondence_mode:
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
                "tsunfold_paper_size",
                text="用紙",
            )

            if scene.tsunfold_paper_size != "CUSTOM":
                row.prop(
                    scene,
                    "tsunfold_orientation",
                    text="向き",
                )
            else:
                custom = output.box()
                custom.label(text="カスタム用紙サイズ（mm）")
                custom_row = custom.row(align=True)
                custom_row.prop(
                    scene,
                    "tsunfold_custom_paper_width_mm",
                    text="幅",
                )
                custom_row.prop(
                    scene,
                    "tsunfold_custom_paper_height_mm",
                    text="高さ",
                )
                custom.label(text="入力した幅 × 高さをそのまま使用")

            paper_w_mm, paper_h_mm = _paper_dimensions_mm(scene)
            output.label(
                text=f"使用サイズ: {paper_w_mm:.1f} × {paper_h_mm:.1f} mm"
            )

            output.prop(
                scene,
                "tsunfold_show_paper",
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
                    if scene.tsunfold_preview
                    else "印刷プレビュー"
                ),
                icon='HIDE_OFF',
                depress=scene.tsunfold_preview,
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
    TSUNFOLD_OT_remove_all_notches,
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
    TSUNFOLD_OT_calibrate_scale,
    TSUNFOLD_OT_mark_seam,
    TSUNFOLD_OT_clear_seam,
    TSUNFOLD_OT_unfold_real_mesh,
    TSUNFOLD_OT_select_unfold_source,
    TSUNFOLD_OT_auto_layout,
    TSUNFOLD_OT_layout_edit,
    TSUNFOLD_OT_delete_unfold,
    TSUNFOLD_OT_toggle_preview,
    TSUNFOLD_OT_export_png,
    TSUNFOLD_PT_main,
)



def _tsunfold_reset_overlays_on_load(_dummy=None):
    # Runs only after a .blend has loaded, when bpy.data is available.
    try:
        scenes = bpy.data.scenes
    except Exception:
        return

    for scene in scenes:
        try:
            scene.tsunfold_show_paper = False
        except Exception:
            _debug.swallowed("unfold._tsunfold_reset_overlays_on_load")
        try:
            scene.tsunfold_preview = False
        except Exception:
            _debug.swallowed("unfold._tsunfold_reset_overlays_on_load")
        try:
            scene[_session.MARKING_SESSION_ACTIVE] = False
            scene[_session.MARKING_FINISH_REQUESTED] = False
            scene[_session.MARKING_PREV_ACTIVE] = ""
            scene[_session.MARKING_PREV_SELECTED_JSON] = "[]"
            scene[_session.MARKING_PREV_MODE] = "OBJECT"
            scene[_session.SEAM_SOURCE] = ""
            scene[_session.SEAM_PREVIEW_READY] = False
            scene[_session.MODAL_RUNNING] = False
            scene.tsunfold_active_tool = "NONE"
            scene.tsunfold_correspondence_mode = False
            scene.tsunfold_auto_island_ids = True
            scene.tsunfold_island_id_style = "ALPHA"
            scene.tsunfold_arrow_mode = "AUTO"
            scene.tsunfold_arrow_up_axis = "Z"
            scene.tsunfold_number_start = 1
            scene.tsunfold_next_number = 1
            scene.tsunfold_notch_mode = "AUTO"
            scene.tsunfold_auto_notch_divisions = "3"
            scene.tsunfold_pattern_preview = False
            scene[_session.PREVIEW_SOURCE_NAME] = ""
            scene[_session.PREVIEW_SOURCE_HIDE_GET] = False
            scene[_session.PREVIEW_SOURCE_HIDE_VIEWPORT] = False
        except Exception:
            _debug.swallowed("unfold._tsunfold_reset_overlays_on_load")



def register():
    global _draw_handle, _pattern_draw_handle, _pattern_text_handle

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.tsunfold_scale_mode = EnumProperty(
        name="実寸の基準",
        description="1 Blender Unit を何ミリとして扱うかの決め方",
        items=[
            (
                "SCENE",
                "シーンに従う",
                "Scene の Unit Scale をそのまま使う",
            ),
            (
                "MANUAL",
                "このアドオンで指定",
                "シーンの Unit Scale を使わず、下の値で換算する",
            ),
        ],
        default="SCENE",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_manual_mm_per_bu = FloatProperty(
        name="1 BU の長さ (mm)",
        description=(
            "「このアドオンで指定」のときに使う換算値。"
            "シーンの Unit Scale は変更しません"
        ),
        default=1000.0,
        min=0.000001,
        soft_min=0.01,
        soft_max=10000.0,
        precision=4,
        update=_pattern_setting_updated,
    )

    # シーム化の対称オプション。
    # _apply_selected_edges_seam_strict_symmetry と
    # _sync_blender_mesh_symmetry が参照するが、これまで register されて
    # おらず、getattr の既定値で常に False に落ちていた。
    bpy.types.Scene.tsunfold_seam_symmetry_x = BoolProperty(
        name="X対称",
        description="シーム化するとき、X軸で対称な位置のエッジも一緒に処理します",
        default=False,
    )

    bpy.types.Scene.tsunfold_seam_symmetry_y = BoolProperty(
        name="Y対称",
        description="シーム化するとき、Y軸で対称な位置のエッジも一緒に処理します",
        default=False,
    )

    bpy.types.Scene.tsunfold_seam_symmetry_z = BoolProperty(
        name="Z対称",
        description="シーム化するとき、Z軸で対称な位置のエッジも一緒に処理します",
        default=False,
    )

    bpy.types.Scene.tsunfold_spacing_mm = FloatProperty(
        name="アイランド間隔",
        description="展開後のアイランド同士の間隔。変更するとリアルタイムで再配置します",
        default=10.0,
        min=0.0,
        soft_max=100.0,
        precision=1,
        update=_spacing_updated,
    )

    bpy.types.Scene.tsunfold_paper_size = EnumProperty(
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

    bpy.types.Scene.tsunfold_custom_paper_width_mm = FloatProperty(
        name="カスタム用紙 幅",
        description="カスタム用紙の横幅をmmで指定します",
        default=600.0,
        min=1.0,
        soft_max=3000.0,
        precision=1,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_custom_paper_height_mm = FloatProperty(
        name="カスタム用紙 高さ",
        description="カスタム用紙の高さをmmで指定します",
        default=900.0,
        min=1.0,
        soft_max=3000.0,
        precision=1,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_orientation = EnumProperty(
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

    bpy.types.Scene.tsunfold_pattern_preview = BoolProperty(
        name="型紙プレビュー",
        description="現在の型紙状態をViewportに表示します",
        default=False,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_notch_mode = EnumProperty(
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

    bpy.types.Scene.tsunfold_auto_notch_divisions = EnumProperty(
        name="分割数",
        description="2なら中央1個、3なら1/3と2/3、4なら1/4・1/2・3/4",
        items=[
            ("2", "2", "中央に1個"),
            ("3", "3", "1/3と2/3に配置"),
            ("4", "4", "1/4・1/2・3/4に配置"),
        ],
        default="3",
        update=_pattern_notch_divisions_updated,
    )

    bpy.types.Scene.tsunfold_show_direction_arrow = BoolProperty(
        name="水色の方向ガイド",
        default=False,
        update=_pattern_redraw_only_updated,
    )





    bpy.types.Scene.tsunfold_correspondence_mode = BoolProperty(
        name="対応確認",
        default=False,
        options={'HIDDEN'},
    )

    bpy.types.Scene.tsunfold_auto_island_ids = BoolProperty(
        name="自動型紙ID",
        default=True,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_island_id_style = EnumProperty(
        name="型紙ID形式",
        items=[
            ("ALPHA", "A / B / C", "アイランドをアルファベットで表示"),
            ("NUMBER", "1 / 2 / 3", "アイランドを数字で表示"),
        ],
        default="ALPHA",
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_island_id_size_mm = FloatProperty(
        name="型紙IDサイズ",
        default=8.0,
        min=3.0,
        max=30.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_island_id_color = FloatVectorProperty(
        name="型紙ID色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_mode = EnumProperty(
        name="矢印方式",
        items=[
            ("AUTO", "オート", "各アイランドへ上方向矢印を自動配置"),
            ("NONE", "なし", "矢印を表示しない"),
            ("CUSTOM", "カスタム", "手動で矢印を配置"),
        ],
        default="AUTO",
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_up_axis = EnumProperty(
        name="上方向",
        description="Blender右上のXYZギズモと同じグローバル軸を使用します",
        items=[
            ("Z", "Z+", "BlenderグローバルZ+"),
            ("Y", "Y+", "BlenderグローバルY+"),
            ("X", "X+", "BlenderグローバルX+"),
        ],
        default="Z",
        update=_pattern_redraw_only_updated,
    )

    

    bpy.types.Scene.tsunfold_auto_arrow_length_mm = FloatProperty(
        name="オート矢印長さ",
        default=24.0,
        min=5.0,
        max=100.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_active_tool = StringProperty(
        name="マーキングツール",
        default="NONE",
        options={'HIDDEN'},
    )

    bpy.types.Scene.tsunfold_notch_color = FloatVectorProperty(
        name="合印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_number_color = FloatVectorProperty(
        name="数字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_text_color = FloatVectorProperty(
        name="文字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_arrow_color = FloatVectorProperty(
        name="矢印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_notch_length_mm = FloatProperty(
        name="合印長さ",
        default=6.0,
        min=1.0,
        max=30.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_notch_thickness_mm = FloatProperty(
        name="合印の太さ",
        description="合印線の表示・出力時の太さ",
        default=0.6,
        min=0.1,
        soft_max=3.0,
        precision=2,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_number_size_mm = FloatProperty(
        name="数字サイズ",
        default=8.0,
        min=2.0,
        max=40.0,
        precision=1,
    )

    bpy.types.Scene.tsunfold_text_size_mm = FloatProperty(
        name="文字サイズ",
        default=8.0,
        min=2.0,
        max=60.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_custom_text = StringProperty(
        name="型紙名 / 任意テキスト",
        default="",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_number_start = bpy.props.IntProperty(
        name="開始番号",
        description="型紙番号モードをONにした時に最初に入る番号",
        default=1,
        min=1,
        max=9999,
    )

    bpy.types.Scene.tsunfold_arrow_head_mm = FloatProperty(
        name="矢印先端サイズ",
        default=8.0,
        min=2.0,
        max=30.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_thickness_mm = FloatProperty(
        name="矢印線の太さ",
        default=0.8,
        min=0.2,
        max=5.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_next_number = bpy.props.IntProperty(
        name="次の型紙番号",
        description="型紙番号ツールが次に置く番号",
        default=1,
        min=1,
        max=9999,
    )

    bpy.types.Scene.tsunfold_show_paper = BoolProperty(
        name="用紙枠を表示",
        description="3Dビューに実寸用紙枠を表示します。オブジェクトは作りません",
        default=False,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_lightweight_view = BoolProperty(
        name="軽量ビュー",
        description="元モデル側の補助マーキング描画を減らして3Dビュー操作を軽くします",
        default=True,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_preview = BoolProperty(
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

    if _tsunfold_reset_overlays_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_tsunfold_reset_overlays_on_load)


def _unregister_scene_props():
    """このアドオンが register() で作った Scene プロパティを全て削除する。

    以前は削除対象を手書きのタプルで列挙していたが、register() 側に
    プロパティを足したときに追従されず、消し残しが発生していた。
    列挙をやめ、接頭辞で特定することで register() と必ず一致させる。
    """
    prefix = "tsunfold_"
    for name in [n for n in dir(bpy.types.Scene) if n.startswith(prefix)]:
        try:
            delattr(bpy.types.Scene, name)
        except Exception:
            # 1つ失敗しても残りの削除は続ける。内容は握り潰さず出す。
            traceback.print_exc()


def unregister():

    if _tsunfold_reset_overlays_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_tsunfold_reset_overlays_on_load)
    global _draw_handle, _pattern_draw_handle, _pattern_text_handle

    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            _debug.swallowed("unfold.unregister")
        _draw_handle = None

    if _pattern_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _pattern_draw_handle, 'WINDOW'
            )
        except Exception:
            _debug.swallowed("unfold.unregister")
        _pattern_draw_handle = None

    if _pattern_text_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _pattern_text_handle, 'WINDOW'
            )
        except Exception:
            _debug.swallowed("unfold.unregister")
        _pattern_text_handle = None

    _unregister_scene_props()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
