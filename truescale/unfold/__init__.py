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
from ..marking import interact as _interact
from ..marking import auto_notch as _auto_notch
from ..marking import symmetry as _symmetry
from ..marking import tools as _tools
from ..core import units as _units
from ..core import view as _view
from ..export import png as _png
from . import build as _build
from . import status as _status
from .panel import TSUNFOLD_PT_main
from . import ops as _ops
from ..export import outline as _outline
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


# 書き出す線の抽出は truescale.export.outline にある。
# 既存の呼び出しをそのまま動かすための別名。
_segments_bbox = _outline.bbox
_export_paper_dimensions = _outline.paper_dimensions
_export_outline_segments = _outline.current_finish_segments
_can_export_current_finish = _outline.can_export
_pattern_text_outline_segments = _outline.text_segments


# 型紙メッシュの生成と配置は truescale.unfold.build にある。
# 既存の呼び出しをそのまま動かすための別名。
_world_edge_length = _build.world_edge_length
_calc_real_scale = _build.real_scale
_build_flat_mesh = _build.flat_mesh
_pack_islands = _build.pack_islands
_object_xy_size_mm = _build.object_xy_size_mm
_show_generated_from_top = _build.show_from_top
_try_shelf_layout = _build.try_shelf_layout


# パネルの案内文は truescale.unfold.status にある。
# 既存の呼び出しをそのまま動かすための別名。
_pattern_seam_source = _objects.seam_source
_pattern_workflow_status = _status.workflow
_pattern_scale_warnings = _status.scale_warnings
_pattern_manual_notch_count = _status.manual_notch_count
_pattern_notch_status_text = _status.notch_text


# 操作中の状態は truescale.marking.interact にある。
# 既存の呼び出しをそのまま動かすための別名。
# 状態そのもの（live_preview 等）は別名を作らず、
# _interact.live_preview のように属性で参照すること。
# 別名にすると、作り直されたときに古い方を見続ける。
_pattern_clear_selected_memo = _interact.clear_selected_memo
_pattern_selected_memo_item = _interact.selected_memo_item
_pattern_get_flat_memos = _interact.get_flat_memos
_pattern_set_flat_memos = _interact.set_flat_memos
_pattern_pick_flat_memo_at_mouse = _interact.pick_flat_memo_at_mouse
_pattern_raycast_flat_pattern_location = _interact.raycast_flat_pattern
_pattern_marking_session_active = _interact.session_active
_pattern_begin_marking_session = _interact.begin_session
_pattern_request_finish_marking = _interact.request_finish
_pattern_restore_work_state = _interact.restore_work_state
_pattern_make_anchor_from_hit = _interact.make_anchor_from_hit
_pattern_item_color = _storage.scene_item_color
_pattern_invalidate_layout_cache = _interact.invalidate_layout_cache
_pattern_raycast_source_detail = _interact.raycast_source_detail
_pattern_nearest_seam_edge = _interact.nearest_seam_edge
_pattern_current_color = _interact.current_color
_pattern_event_is_view_window = _interact.event_is_view_window
_pattern_clear_live_preview = _interact.clear_live_preview
_pattern_preview_anchor_world = _interact.preview_anchor_world
_pattern_active_tool = _interact.active_tool
_pattern_set_active_tool = _interact.set_active_tool
_pattern_flat_face_island = _interact.flat_face_island
_pattern_source_faces_for_flat_island = _interact.source_faces_for_flat_island
_pattern_flat_island_from_source_face = _interact.flat_island_from_source_face
_pattern_set_island_highlight = _interact.set_island_highlight
_pattern_clear_island_highlight = _interact.clear_island_highlight
_pattern_raycast_any_visible = _interact.raycast_any_visible


# シーム対称・オート合印・道具の中身は truescale.marking にある。
# 既存の呼び出しをそのまま動かすための別名。
_mirrored_point_xyz = _symmetry.mirrored_point
_find_strict_mirrored_edge_xyz = _symmetry.find_mirrored_edge
_symmetry_variants_xyz = _symmetry.variants
_sync_blender_mesh_symmetry = _symmetry.sync_mesh_symmetry
_apply_selected_edges_seam_strict_symmetry = _symmetry.apply_to_selected_edges
_pattern_auto_notch_division_value = _auto_notch.division_value
_pattern_refresh_auto_notches = _auto_notch.refresh
_pattern_remove_auto_notches = _auto_notch.remove
_pattern_toggle_tool_invoke = _tools.toggle_invoke
_pattern_modal_common = _tools.modal


# オブジェクト操作と視点は core にある。
# 既存の呼び出しをそのまま動かすための別名。
_active_smooth_object = _objects.active_smooth
_pattern_delete_generated_for_source = _objects.delete_generated_for_source
_focus_selected_unfold = _view.focus_selected
_pattern_print_preview_source_visibility = _view.print_preview_source_visibility
_pattern_set_annotations = _interact.save_annotations

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
UNFOLD_SUFFIX = _build.UNFOLD_SUFFIX
_draw_handle = None
_pattern_draw_handle = None
_pattern_text_handle = None




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



























# ------------------------------------------------------------
# Paper guide overlay (no Blender object is created)
# ------------------------------------------------------------











_tag_redraw = _view.tag_redraw









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














# ------------------------------------------------------------
# PNG helpers
# ------------------------------------------------------------












# ------------------------------------------------------------
# Smooth finishing-line helpers
# ------------------------------------------------------------

SMOOTH_SUFFIX = "_なめらか線"








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



# 登録するクラス。オペレータは ops/ が機能別に持っている。
# パネルは最後。先にオペレータが登録されていないと、パネルの
# 参照先が無い状態になる。
classes = _ops.classes + (
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
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_text_color = FloatVectorProperty(
        name="文字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
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
