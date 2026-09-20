import bpy

from .. import debug as _debug
from . import bbox as _bbox
from . import overlay as _overlay
from .export import capture as _capture
from .export import sheet as _sheet
from . import views as _views
from . import keys as _keys
from . import dimension as _dimension
from . import viewstate as _viewstate
import traceback
import os
import html
import math
import struct
import zlib
import base64
from pathlib import Path
import blf
import gpu
import mathutils
from mathutils import Vector
from gpu_extras.batch import batch_for_shader
from bpy.app.handlers import persistent
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper





# AddonPreferences の bl_idname は、サブパッケージ名ではなく
# アドオン本体のパッケージ名でなければならない。
# このモジュールは <アドオン>.draft として読み込まれるので、
# 末尾の ".draft" を落としたものが本体のパッケージ名になる。
ADDON_PACKAGE = __package__.rpartition(".")[0]


def get_addon_preferences(context=None):
    context = context or bpy.context
    try:
        addon = context.preferences.addons.get(ADDON_PACKAGE)
        if addon is not None:
            return addon.preferences
    except Exception:
        _debug.swallowed("draft.get_addon_preferences")
    return None


class TSDRAFT_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    show_dark_place_button: bpy.props.BoolProperty(
        name="「なんかずっと暗いとこ」を表示",
        description="お遊び機能のボタンを造形ヘルパーに表示します",
        default=True
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="お遊び")
        layout.prop(self, "show_dark_place_button")


























SVG_PX_TO_MM = 25.4 / 96.0



































































# =========================================================
# 共通：再描画
# =========================================================





# =========================================================
# サイズ計測：Draw Handler
# =========================================================

























# =========================================================
# サイズ計測：BOX＋寸法作成
# =========================================================


class TSDRAFT_OT_toggle_size_overlay(bpy.types.Operator):
    bl_idname = "truescale_draft.toggle_size_overlay"
    bl_label = "BOX＋寸法 表示切替"
    bl_description = "BOXと寸法を削除せず、一時的に表示／非表示を切り替えます"

    def execute(self, context):
        scene = context.scene
        bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)

        if bbox_obj is None:
            self.report({'WARNING'}, "先にBOX＋寸法を作成してクレメンス")
            return {'CANCELLED'}

        # 両方表示中なら隠す。それ以外ならまとめて表示。
        currently_visible = (
            bool(getattr(scene, "tsdraft_show_bbox", True))
            and bool(getattr(scene, "tsdraft_show_dimensions", True))
        )

        new_state = not currently_visible

        if new_state:
            # 再表示時はグローバル表示を先にONにしてから、
            # 各ビュー個別フラグも確実にONへ戻す。
            scene.tsdraft_show_bbox = True
            scene.tsdraft_show_dimensions = True

            scene.tsdraft_show_bbox_top = True
            scene.tsdraft_show_bbox_front = True
            scene.tsdraft_show_bbox_side = True
            scene.tsdraft_show_bbox_user = True

            scene.tsdraft_show_dimensions_top = True
            scene.tsdraft_show_dimensions_front = True
            scene.tsdraft_show_dimensions_side = True
            scene.tsdraft_show_dimensions_user = True
        else:
            scene.tsdraft_show_bbox = False
            scene.tsdraft_show_dimensions = False

        _bbox.redraw_viewports()
        return {'FINISHED'}



class TSDRAFT_OT_make_size_bbox(bpy.types.Operator):
    bl_idname = "truescale_draft.make_size_bbox"
    bl_label = "BOX＋寸法を作成"
    bl_description = "選択オブジェクトからサイズ用Bounding Boxを作成し、3辺の寸法を表示します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        src = getattr(context, "active_object", None)

        # 選択オブジェクトが無い、または既に削除済みなら安全に中止
        if src is None:
            self.report({'ERROR'}, "オブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        # Blender側の参照が途中で無効化されていないかも確認
        if bpy.data.objects.get(src.name) is None:
            self.report({'ERROR'}, "選択オブジェクトが見つからんかったンゴ")
            return {'CANCELLED'}

        if src.type != 'MESH':
            self.report({'ERROR'}, "メッシュオブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        if src.name == _keys.BBOX_NAME:
            self.report({'ERROR'}, "元オブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        # mode属性へ触る前に参照を再確認
        src = bpy.data.objects.get(src.name)
        if src is None:
            self.report({'ERROR'}, "選択オブジェクトが途中で消えたンゴ")
            return {'CANCELLED'}

        src_mode = getattr(src, "mode", 'OBJECT')
        if src_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                self.report({'ERROR'}, "Object Modeへ切り替えられんかったンゴ")
                return {'CANCELLED'}

        namespace = bpy.app.driver_namespace
        namespace[_keys.SOURCE_KEY] = src.name

        old_bbox = bpy.data.objects.get(_keys.BBOX_NAME)
        if old_bbox is not None:
            bpy.data.objects.remove(old_bbox, do_unlink=True)

        namespace[_keys.DATA_KEY] = []

        bbox_obj = src.copy()

        if src.data:
            bbox_obj.data = src.data.copy()

        if src.users_collection:
            src.users_collection[0].objects.link(bbox_obj)
        else:
            context.collection.objects.link(bbox_obj)

        bpy.ops.object.select_all(action='DESELECT')
        bbox_obj.select_set(True)
        context.view_layer.objects.active = bbox_obj

        node_group = bpy.data.node_groups.new(
            name="AUTO_BoundingBox",
            type="GeometryNodeTree"
        )

        node_group.interface.new_socket(
            name="Geometry",
            in_out='INPUT',
            socket_type='NodeSocketGeometry'
        )

        node_group.interface.new_socket(
            name="Geometry",
            in_out='OUTPUT',
            socket_type='NodeSocketGeometry'
        )

        nodes = node_group.nodes
        links = node_group.links

        input_node = nodes.new("NodeGroupInput")
        bbox_node = nodes.new("GeometryNodeBoundBox")
        output_node = nodes.new("NodeGroupOutput")

        links.new(
            input_node.outputs["Geometry"],
            bbox_node.inputs["Geometry"]
        )

        links.new(
            bbox_node.outputs["Bounding Box"],
            output_node.inputs["Geometry"]
        )

        modifier = bbox_obj.modifiers.new(
            name="AUTO Bounding Box",
            type='NODES'
        )

        modifier.node_group = node_group

        bpy.ops.object.modifier_apply(
            modifier=modifier.name
        )

        if node_group.users == 0:
            bpy.data.node_groups.remove(node_group)

        bbox_obj.name = _keys.BBOX_NAME
        bbox_obj["tsdraft_source_name"] = src.name
        bbox_obj.display_type = 'BOUNDS'
        bbox_obj.display_bounds_type = 'BOX'

        # ビュー別表示に対応するため、実オブジェクトは常時非表示
        bbox_obj.hide_set(True)
        _overlay.ensure_bbox_draw_handler()

        mesh = bbox_obj.data

        anchor = min(
            mesh.vertices,
            key=lambda v: (
                v.co.x,
                -v.co.y,
                -v.co.z
            )
        )

        neighbor_indices = []

        for edge in mesh.edges:
            if anchor.index in edge.vertices:
                for vertex_index in edge.vertices:
                    if vertex_index != anchor.index:
                        neighbor_indices.append(vertex_index)

        neighbor_indices = list(dict.fromkeys(neighbor_indices))

        if len(neighbor_indices) != 3:
            self.report({'ERROR'}, "隣接頂点が3個にならんかったンゴ")
            return {'CANCELLED'}

        dimension_data = []
        anchor_co = mesh.vertices[anchor.index].co.copy()

        unit_scale = context.scene.unit_settings.scale_length
        if unit_scale == 0:
            unit_scale = 1.0

        for index in neighbor_indices:
            other_co = mesh.vertices[index].co.copy()

            world_a = bbox_obj.matrix_world @ anchor_co
            world_b = bbox_obj.matrix_world @ other_co

            length_world = (world_b - world_a).length
            length_mm = length_world * unit_scale * 1000.0

            midpoint = (world_a + world_b) * 0.5

            local_delta = other_co - anchor_co
            values = (abs(local_delta.x), abs(local_delta.y), abs(local_delta.z))
            axis = ("X", "Y", "Z")[values.index(max(values))]

            dimension_data.append({
                "location": midpoint,
                "text": f"{length_mm:.1f} mm",
                "length_mm": length_mm,
                "axis": axis,
                "world_a": tuple(world_a),
                "world_b": tuple(world_b)
            })

        namespace[_keys.DATA_KEY] = dimension_data

        # 作成直後は「出た！」が分かるよう、通常斜めビューで必ず表示。
        # 「任意」専用モードはいったん解除する。
        context.scene.tsdraft_user_view_mode = False

        # BOXと寸法を必ず表示へリセット
        context.scene.tsdraft_show_dimensions = True
        context.scene.tsdraft_show_dimensions_top = True
        context.scene.tsdraft_show_dimensions_front = True
        context.scene.tsdraft_show_dimensions_side = True
        context.scene.tsdraft_show_dimensions_user = False

        context.scene.tsdraft_show_bbox = True
        context.scene.tsdraft_frame_mode = 'BOX'
        context.scene.tsdraft_show_bbox_top = True
        context.scene.tsdraft_show_bbox_front = True
        context.scene.tsdraft_show_bbox_side = True
        context.scene.tsdraft_show_bbox_user = False

        _overlay.ensure_draw_handler()
        _bbox.ensure_cleanup_handler()

        # 作成ボタンを押した瞬間は、必ずBOX＋寸法が見える状態へ。
        # 三面は表示ON、任意ビューだけはパース確認用としてOFF。



        # 枠そのものが「なし」になっていた場合も作成時はBOXへ戻す。

        _overlay.ensure_bbox_draw_handler()
        _bbox.redraw_viewports()

        # 自動追従の初期署名をここで作る
        try:
            _bbox.tsdraft_update_bbox_from_source(
                context.scene,
                context.evaluated_depsgraph_get(),
                force=True
            )
        except Exception:
            _debug.swallowed("draft.TSDRAFT_OT_make_size_bbox.execute")

        bpy.ops.object.select_all(action='DESELECT')
        bbox_obj.select_set(True)
        context.view_layer.objects.active = bbox_obj

        _bbox.redraw_viewports()

        self.report({'INFO'}, "Bounding Box＋寸法表示、完成や！")
        return {'FINISHED'}


class TSDRAFT_OT_delete_bbox(bpy.types.Operator):
    bl_idname = "truescale_draft.delete_bbox"
    bl_label = "BOX＋寸法を削除"
    bl_description = "サイズ用Bounding Boxと寸法表示を削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        namespace = bpy.app.driver_namespace
        namespace[_keys.DATA_KEY] = []
        namespace[_keys.SOURCE_KEY] = None
        namespace[_keys.AUTO_FOLLOW_SIGNATURE_KEY] = None

        bbox = bpy.data.objects.get(_keys.BBOX_NAME)
        if bbox is not None:
            bpy.data.objects.remove(bbox, do_unlink=True)

        # BOX＋寸法を消したら、そのまま通常表示へ帰還
        if context.area is not None and context.area.type == 'VIEW_3D':
            try:
                bpy.ops.truescale_draft.restore_view()
            except Exception:
                _debug.swallowed("draft.TSDRAFT_OT_delete_bbox.execute")

        _bbox.redraw_viewports()
        return {'FINISHED'}





























class TSDRAFT_OT_preview_three_view_sheet(bpy.types.Operator):
    # 2.4.12: use a fresh operator id so an old/stale registration can never
    # resolve the Preview button to the ExportHelper operator after updating.
    bl_idname = 'truescale_draft.preview_three_view_sheet_safe'
    bl_label = '三面図シートをプレビュー'
    bl_description = '一時PNGを作ってプレビューします。保存先の指定は行いません'

    def invoke(self, context, event):
        # Preview is intentionally non-modal and never opens Blender's file selector.
        return self.execute(context)

    def execute(self, context):
        import tempfile
        preview_dir = Path(tempfile.gettempdir()) / 'zoukei_helper_preview'
        preview_dir.mkdir(parents=True, exist_ok=True)
        filepath = preview_dir / '三面図プレビュー.png'

        try:
            info = _sheet.tsdraft_build_three_view_sheet(context, str(filepath))
            bpy.ops.wm.path_open(filepath=str(filepath))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"プレビュー: {info['paper']} / 1:{info['scale_denominator']:g} / {info['dpi']:.0f}dpi"
        )
        return {'FINISHED'}


class TSDRAFT_OT_export_three_view_sheet(bpy.types.Operator, ExportHelper):
    bl_idname = 'truescale_draft.export_three_view_sheet'
    bl_label = '三面図シートを書き出し'
    bl_description = '上面・前面・側面を選択した用紙サイズと縮率で1枚のPNG図面にまとめます'

    filename_ext = '.png'
    filter_glob: bpy.props.StringProperty(default='*.png', options={'HIDDEN'})

    def invoke(self, context, event):
        _capture.tsdraft_store_export_view_context(context)
        source_obj = _bbox.tsdraft_resolve_source_object(context)
        base = source_obj.name if source_obj is not None else 'drawing'
        safe_base = ''.join(c if c not in '\\/:*?"<>|' else '_' for c in base)
        last_dir = _capture.tsdraft_get_last_export_dir()
        default_name = f'{safe_base}_三面図_{context.scene.tsdraft_sheet_paper_size}_1-{_sheet.tsdraft_sheet_scale_denominator(context.scene):g}.png'
        self.filepath = os.path.join(last_dir, default_name) if last_dir else default_name
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        filepath = bpy.path.abspath(self.filepath)
        if not filepath.lower().endswith('.png'):
            filepath += '.png'

        try:
            sheet_info = _sheet.tsdraft_build_three_view_sheet(context, filepath)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        _capture.tsdraft_remember_export_dir(filepath)
        self.report(
            {'INFO'},
            f"{sheet_info['paper']} / 1:{sheet_info['scale_denominator']:g} / {sheet_info['dpi']:.0f}dpi の三面図PNGを書き出したで"
        )
        return {'FINISHED'}


class TSDRAFT_OT_export_actual_png(bpy.types.Operator, ExportHelper):
    bl_idname = "truescale_draft.export_actual_png"
    bl_label = "実寸PNGを書き出し"
    bl_description = "現在のBlender図面ビューをそのままPNG化し、Bounding Box実寸で物理サイズを合わせます"

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(
        default="*.png",
        options={'HIDDEN'}
    )

    view_key: bpy.props.EnumProperty(
        name="ビュー",
        items=(
            ('top', "上面", ""),
            ('front', "前面", ""),
            ('side', "側面", ""),
            ('user', "任意", ""),
        ),
        default='front'
    )

    def invoke(self, context, event):
        _capture.tsdraft_store_export_view_context(context)

        source_obj = _bbox.tsdraft_resolve_source_object(context)
        base = source_obj.name if source_obj is not None else "drawing"
        safe_base = "".join(c if c not in '\\/:*?"<>|' else "_" for c in base)

        default_name = f"{safe_base}_{_dimension.tsdraft_svg_view_label(self.view_key)}.png"
        last_dir = _capture.tsdraft_get_last_export_dir()

        if last_dir:
            self.filepath = os.path.join(last_dir, default_name)
        else:
            self.filepath = default_name

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            info = _capture.tsdraft_export_viewport_exact_png(
                context,
                self.filepath,
                self.view_key
            )
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        _capture.tsdraft_remember_export_dir(self.filepath)

        self.report(
            {'INFO'},
            f"{_dimension.tsdraft_svg_view_label(self.view_key)} PNG出力 "
            f"{info['bbox_width_mm']:.1f}×{info['bbox_height_mm']:.1f}mm "
            f"/ {info['dpi']:.1f}dpi"
        )
        return {'FINISHED'}


class TSDRAFT_OT_export_all_actual_png(bpy.types.Operator, ExportHelper):
    bl_idname = "truescale_draft.export_all_actual_png"
    bl_label = "3面まとめてPNG"
    bl_description = (
        "上面・前面・側面を実寸PNGとして一括書き出します。"
        "保存時に入力した名前をベースに、_上面 / _前面 / _側面 を自動付与します"
    )

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(
        default="*.png",
        options={'HIDDEN'}
    )

    def invoke(self, context, event):
        _capture.tsdraft_store_export_view_context(context)

        source_obj = _bbox.tsdraft_resolve_source_object(context)
        base = source_obj.name if source_obj is not None else "drawing"
        safe_base = "".join(
            c if c not in '\\/:*?"<>|' else "_"
            for c in base
        )

        last_dir = _capture.tsdraft_get_last_export_dir()
        default_name = f"{safe_base}.png"

        if last_dir:
            self.filepath = os.path.join(last_dir, default_name)
        else:
            self.filepath = default_name

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        source_obj = _bbox.tsdraft_resolve_source_object(context)
        if source_obj is None:
            self.report({'ERROR'}, "元オブジェクトを選択してクレメンス")
            return {'CANCELLED'}

        chosen_path = bpy.path.abspath(self.filepath)
        target_dir = os.path.dirname(chosen_path)

        if not target_dir:
            target_dir = _capture.tsdraft_get_last_export_dir() or os.getcwd()

        os.makedirs(target_dir, exist_ok=True)

        # ユーザーが保存ダイアログで付けた名前をベース名として使う。
        chosen_filename = os.path.basename(chosen_path)
        typed_base, _ext = os.path.splitext(chosen_filename)

        if not typed_base:
            typed_base = source_obj.name

        safe_base = "".join(
            c if c not in '\\/:*?"<>|' else "_"
            for c in typed_base
        ).strip()

        if not safe_base:
            safe_base = "drawing"

        views = (
            ("top", "上面"),
            ("front", "前面"),
            ("side", "側面"),
        )

        errors = []
        done = 0

        # 3面まとめて書き出しは現在表示に依存せず、
        # 上面・前面・側面を内部で1面ずつ切り替え、個別フィットして出力する。
        view_ctx = _capture.tsdraft_get_export_view_context(context)
        if view_ctx is None:
            self.report({'ERROR'}, "書き出し元の3Dビューが見つからんかったンゴ")
            return {'CANCELLED'}

        for view_key, view_label in views:
            filepath = os.path.join(
                target_dir,
                f"{safe_base}_{view_label}.png"
            )
            try:
                info = _capture.tsdraft_export_viewport_exact_png(
                    context,
                    filepath,
                    view_key,
                    common_view_distance=None,
                    suppress_dimension_text=True
                )
                _sheet.tsdraft_add_dimension_labels_to_exact_png(
                    context.scene,
                    filepath,
                    view_key,
                    info
                )
                done += 1
            except Exception as exc:
                errors.append(f"{view_label}: {exc}")

        if errors:
            self.report({'WARNING'}, " / ".join(errors))

        if done == 0:
            return {'CANCELLED'}

        _capture.tsdraft_remember_export_dir(target_dir)

        self.report(
            {'INFO'},
            f"{safe_base}_上面 / 前面 / 側面.png を{done}枚書き出したで"
        )
        return {'FINISHED'}


class TSDRAFT_OT_dark_place(bpy.types.Operator):
    bl_idname = "truescale_draft.dark_place"
    bl_label = "なんかずっと暗いとこ"
    bl_description = "押すたびに暗所表示のON/OFFを切り替えます"

    def execute(self, context):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "3Dビュー上で実行してクレメンス")
            return {'CANCELLED'}

        scene = context.scene
        space = context.area.spaces.active

        if not scene.tsdraft_dark_place:
            # 暗所は「通常Blender表示 ↔ 暗所」の往復専用にする。
            # 図面ビュー中なら、暗所へ入る前にまず元の通常表示へ戻す。
            if getattr(scene, "tsdraft_drawing_mode", False):
                try:
                    _viewstate.restore_view_state(space)
                except Exception:
                    _debug.swallowed("draft.TSDRAFT_OT_dark_place.execute")
                scene.tsdraft_drawing_mode = False

            # ここで通常表示を暗所の復帰先として保存してから暗所化。
            scene.tsdraft_dark_place = True
            _viewstate.apply_dark_place_view(space)

            # 暗所中だけ見た目上のグリッドと標準X/Y軸を直接OFF。
            try:
                if hasattr(space.overlay, "show_floor"):
                    space.overlay.show_floor = False
                if hasattr(space.overlay, "show_ortho_grid"):
                    space.overlay.show_ortho_grid = False
                if hasattr(space.overlay, "show_axis_x"):
                    space.overlay.show_axis_x = False
                if hasattr(space.overlay, "show_axis_y"):
                    space.overlay.show_axis_y = False
            except Exception:
                _debug.swallowed("draft.TSDRAFT_OT_dark_place.execute")
        else:
            scene.tsdraft_dark_place = False

            # 暗所に入る直前の背景・グリッド状態へそのまま戻す。
            _viewstate.restore_dark_place_view(space)

        _bbox.redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_reset_label_offsets(bpy.types.Operator):
    bl_idname = "truescale_draft.reset_label_offsets"
    bl_label = "文字位置をリセット"

    def execute(self, context):
        for prop in (
        ):
            setattr(context.scene, prop, 0)
        for view in ("front", "top", "side", "user"):
            for axis in ("x", "y", "z"):
                setattr(context.scene, f"tsdraft_{view}_{axis}_offset_x_mm", 0.0)
                setattr(context.scene, f"tsdraft_{view}_{axis}_offset_y_mm", 0.0)

        _bbox.redraw_viewports()
        return {'FINISHED'}


# =========================================================
# 図面ビュー：共通
# =========================================================




















# =========================================================
# 図面ビュー：Operator
# =========================================================

class TSDRAFT_OT_quad_view(bpy.types.Operator):
    bl_idname = "truescale_draft.quad_view"
    bl_label = "三面＋任意ビュー"

    def execute(self, context):
        bpy.app.driver_namespace[_keys.ZOOM_SYNC_STATE_KEY] = None
        override = _viewstate.get_view3d_override(context)

        if override is None:
            self.report({'ERROR'}, "3Dビュー上で実行してクレメンス")
            return {'CANCELLED'}

        space = override["space_data"]

        _viewstate.configure_drawing_view(
            space,
            context.scene.tsdraft_show_grid
        )
        context.scene.tsdraft_drawing_mode = True

        # 三面図モードでは、上面・前面・側面は図面表示、
        # 任意ビューだけイメージ確認用にBOX/寸法を隠す。
        context.scene.tsdraft_show_bbox_top = True
        context.scene.tsdraft_show_bbox_front = True
        context.scene.tsdraft_show_bbox_side = True
        context.scene.tsdraft_show_dimensions_top = True
        context.scene.tsdraft_show_dimensions_front = True
        context.scene.tsdraft_show_dimensions_side = True
        context.scene.tsdraft_show_bbox_user = False
        context.scene.tsdraft_show_dimensions_user = False

        if not _views.is_quad_view(space):
            with context.temp_override(**override):
                bpy.ops.screen.region_quadview()

        # Quad View生成直後にBBox＋寸法の余白を確保。
        try:
            _views.tsdraft_fit_quad_for_drawing(context, override["area"])
        except Exception:
            _debug.swallowed("draft.TSDRAFT_OT_quad_view.execute")

        try:
            _overlay.tsdraft_sync_ortho_zoom(space)
        except Exception:
            _debug.swallowed("draft.TSDRAFT_OT_quad_view.execute")

        _bbox.redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_front_view(bpy.types.Operator):
    bl_idname = "truescale_draft.front_view"
    bl_label = "正面"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = False
        if not _views.switch_single_view(context, 'FRONT'):
            return {'CANCELLED'}
        return {'FINISHED'}


class TSDRAFT_OT_top_view(bpy.types.Operator):
    bl_idname = "truescale_draft.top_view"
    bl_label = "上面"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = False
        if not _views.switch_single_view(context, 'TOP'):
            return {'CANCELLED'}
        return {'FINISHED'}


class TSDRAFT_OT_side_view(bpy.types.Operator):
    bl_idname = "truescale_draft.side_view"
    bl_label = "側面"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = False
        if not _views.switch_single_view(context, 'RIGHT'):
            return {'CANCELLED'}
        return {'FINISHED'}


class TSDRAFT_OT_user_view(bpy.types.Operator):
    bl_idname = "truescale_draft.user_view"
    bl_label = "任意"

    def execute(self, context):
        context.scene.tsdraft_user_view_mode = True
        if not _views.switch_single_view(context, None):
            return {'CANCELLED'}
        _bbox.redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_apply_drawing_style(bpy.types.Operator):
    bl_idname = "truescale_draft.apply_drawing_style"
    bl_label = "図面表示を再適用"

    def execute(self, context):
        if context.area is None or context.area.type != 'VIEW_3D':
            return {'CANCELLED'}

        _viewstate.configure_drawing_view(
            context.area.spaces.active,
            getattr(context.scene, "tsdraft_drawing_background", 'WHITE'),
            getattr(context.scene, "tsdraft_drawing_background_color", (1.0, 1.0, 1.0))
        )
        context.scene.tsdraft_drawing_mode = True

        _bbox.redraw_viewports()
        return {'FINISHED'}


class TSDRAFT_OT_restore_view(bpy.types.Operator):
    bl_idname = "truescale_draft.restore_view"
    bl_label = "元の表示に戻す"
    bl_description = "図面ビューに入る前のビューポート表示へ戻します"

    def execute(self, context):
        override = _viewstate.get_view3d_override(context)

        if override is None:
            self.report({'ERROR'}, "3Dビュー上で実行してクレメンス")
            return {'CANCELLED'}

        space = override["space_data"]

        # Quad Viewなら先に解除
        if _views.is_quad_view(space):
            with context.temp_override(**override):
                bpy.ops.screen.region_quadview()

            override = _viewstate.get_view3d_override(context)
            if override is None:
                return {'CANCELLED'}

            space = override["space_data"]

        if getattr(context.scene, "tsdraft_dark_place", False):
            context.scene.tsdraft_dark_place = False
            _viewstate.restore_dark_place_view(space)

        _viewstate.restore_view_state(space)
        context.scene.tsdraft_drawing_mode = False
        context.scene.tsdraft_user_view_mode = False
        _bbox.redraw_viewports()

        self.report({'INFO'}, "通常表示に戻したで")
        return {'FINISHED'}


# =========================================================
# Nパネル：統合
# =========================================================

class TSDRAFT_PT_main(bpy.types.Panel):
    bl_label = "Truescale Draft"
    bl_idname = "TSDRAFT_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Truescale"

    def draw(self, context):
        layout = self.layout

        # =====================================================
        # 1. サイズ表示
        # =====================================================
        box = layout.box()
        box.label(text="サイズ表示")

        box.operator(
            "truescale_draft.make_size_bbox",
            text="BOX＋寸法を作成",
            icon='CUBE'
        )

        box.operator(
            "truescale_draft.delete_bbox",
            text="BOX＋寸法を削除",
            icon='TRASH'
        )

        overlay_visible = (
            context.scene.tsdraft_show_bbox
            and context.scene.tsdraft_show_dimensions
        )

        box.operator(
            "truescale_draft.toggle_size_overlay",
            text=(
                "BOX＋寸法を非表示"
                if overlay_visible
                else "BOX＋寸法を表示"
            ),
            icon='HIDE_OFF' if overlay_visible else 'HIDE_ON'
        )

        box.prop(
            context.scene,
            "tsdraft_auto_follow",
            text="自動追従",
            toggle=True
        )

        layout.separator()

        # =====================================================
        # 2. 枠線
        # =====================================================
        box = layout.box()
        box.label(text="枠線")

        box.prop(
            context.scene,
            "tsdraft_font_size",
            text="文字サイズ"
        )

        box.prop(
            context.scene,
            "tsdraft_font_color",
            text="文字色"
        )

        box.prop(
            context.scene,
            "tsdraft_dimension_unit",
            text="単位"
        )

        box.prop(
            context.scene,
            "tsdraft_frame_mode",
            text="枠表示"
        )

        box.prop(
            context.scene,
            "tsdraft_frame_color",
            text="枠線色"
        )

        box.prop(
            context.scene,
            "tsdraft_frame_width",
            text="枠線の太さ"
        )

        layout.separator()

        # =====================================================
        # 3. 出力
        # =====================================================
        box = layout.box()
        box.label(text="出力")

        box.prop(
            context.scene,
            "tsdraft_export_background",
            text="書き出し背景"
        )
        if context.scene.tsdraft_export_background == 'CUSTOM':
            box.prop(
                context.scene,
                "tsdraft_export_background_color",
                text="カスタム色"
            )

        row = box.row(align=True)
        op = row.operator("truescale_draft.export_actual_png", text="上面")
        op.view_key = 'top'
        op = row.operator("truescale_draft.export_actual_png", text="前面")
        op.view_key = 'front'
        op = row.operator("truescale_draft.export_actual_png", text="側面")
        op.view_key = 'side'

        op = box.operator("truescale_draft.export_actual_png", text="任意")
        op.view_key = 'user'

        box.operator(
            "truescale_draft.export_all_actual_png",
            text="3面まとめて書き出し",
            icon='EXPORT'
        )

        sheet = box.box()
        sheet.label(text="図面シート")
        row = sheet.row(align=True)
        row.prop(context.scene, "tsdraft_sheet_paper_size", text="用紙")
        row.prop(context.scene, "tsdraft_sheet_orientation", text="向き")
        if context.scene.tsdraft_sheet_paper_size == 'CUSTOM':
            row = sheet.row(align=True)
            row.prop(context.scene, "tsdraft_sheet_custom_width_mm", text="幅(mm)")
            row.prop(context.scene, "tsdraft_sheet_custom_height_mm", text="高さ(mm)")
        sheet.prop(context.scene, "tsdraft_sheet_scale", text="縮率")
        if context.scene.tsdraft_sheet_scale == 'CUSTOM':
            sheet.prop(context.scene, "tsdraft_sheet_custom_scale", text="1 :")
        row = sheet.row(align=True)
        row.operator(
            "truescale_draft.preview_three_view_sheet_safe",
            text="プレビュー（保存しない）",
            icon='HIDE_OFF'
        )
        row.operator(
            "truescale_draft.export_three_view_sheet",
            text="書き出し",
            icon='FILE_IMAGE'
        )

        layout.separator()

        # =====================================================
        # 4. 図面ビュー
        # =====================================================
        box = layout.box()
        box.label(text="図面ビュー")

        box.prop(
            context.scene,
            "tsdraft_drawing_background",
            text="ビュー背景"
        )
        if context.scene.tsdraft_drawing_background == 'CUSTOM':
            box.prop(
                context.scene,
                "tsdraft_drawing_background_color",
                text="カスタム色"
            )

        row = box.row(align=True)
        row.operator("truescale_draft.front_view", text="正面")
        row.operator("truescale_draft.top_view", text="上面")

        row = box.row(align=True)
        row.operator("truescale_draft.side_view", text="側面")
        row.operator("truescale_draft.user_view", text="任意")

        sub = box.box()
        sub.label(text="任意ビュー表示")

        row = sub.row(align=True)
        row.prop(
            context.scene,
            "tsdraft_show_bbox_user",
            text="枠線",
            toggle=True
        )
        row.prop(
            context.scene,
            "tsdraft_show_dimensions_user",
            text="寸法",
            toggle=True
        )

        box.operator(
            "truescale_draft.restore_view",
            text="元の表示に戻す",
            icon='LOOP_BACK'
        )

        layout.separator()

        # =====================================================
        # 5. 寸法位置の微調整
        # =====================================================
        box = layout.box()
        row = box.row(align=True)
        row.prop(
            context.scene,
            "tsdraft_show_dimension_adjustments",
            text="寸法位置の微調整",
            icon='TRIA_DOWN' if context.scene.tsdraft_show_dimension_adjustments else 'TRIA_RIGHT',
            emboss=False
        )

        if context.scene.tsdraft_show_dimension_adjustments:
            box.label(text="自動配置位置からの追加調整")

            view_specs = (
                ("top", "上面", (
                    ("x", "左右"),
                    ("y", "上下"),
                )),
                ("front", "前面", (
                    ("x", "左右"),
                    ("z", "上下"),
                )),
                ("side", "側面", (
                    ("y", "左右"),
                    ("z", "上下"),
                )),
                ("user", "任意", (
                    ("x", "左右(X)"),
                    ("y", "奥行(Y)"),
                    ("z", "上下(Z)"),
                )),
            )

            for view_key, view_label, axes in view_specs:
                col = box.column(align=True)
                col.label(text=view_label)

                for axis, fallback_label in axes:
                    row = col.row(align=True)

                    axis_label = _dimension.get_axis_dimension_text(
                        context.scene,
                        axis.upper(),
                        fallback_label
                    )
                    row.label(text=axis_label)

                    row.prop(
                        context.scene,
                        f"tsdraft_{view_key}_{axis}_offset_x_mm",
                        text="左右(mm)"
                    )
                    row.prop(
                        context.scene,
                        f"tsdraft_{view_key}_{axis}_offset_y_mm",
                        text="上下(mm)"
                    )

                box.separator()

            box.operator(
                "truescale_draft.reset_label_offsets",
                text="文字位置をリセット"
            )


        prefs = get_addon_preferences(context)
        if prefs is None or prefs.show_dark_place_button:
            layout.separator()
            box = layout.box()
            box.operator(
                "truescale_draft.dark_place",
                text="なんかずっと暗いとこ"
            )


classes = (
    TSDRAFT_Preferences,
    TSDRAFT_OT_toggle_size_overlay,
    TSDRAFT_OT_export_actual_png,
    TSDRAFT_OT_export_all_actual_png,
    TSDRAFT_OT_preview_three_view_sheet,
    TSDRAFT_OT_export_three_view_sheet,
    TSDRAFT_OT_make_size_bbox,
    TSDRAFT_OT_delete_bbox,
    TSDRAFT_OT_dark_place,
    TSDRAFT_OT_reset_label_offsets,
    TSDRAFT_OT_quad_view,
    TSDRAFT_OT_front_view,
    TSDRAFT_OT_top_view,
    TSDRAFT_OT_side_view,
    TSDRAFT_OT_user_view,
    TSDRAFT_OT_apply_drawing_style,
    TSDRAFT_OT_restore_view,
    TSDRAFT_PT_main,
)



def tsdraft_reset_scene_settings_to_defaults(scene):
    """
    Remove persisted addon setting values from the Scene.
    Registered bpy.props defaults then become active again.
    Objects/BBox themselves are not deleted.
    """
    try:
        keys = list(scene.keys())
    except Exception:
        return

    for key in keys:
        if isinstance(key, str) and key.startswith("tsdraft_"):
            try:
                del scene[key]
            except Exception:
                _debug.swallowed("draft.tsdraft_reset_scene_settings_to_defaults")


def tsdraft_reset_all_scenes_to_defaults():
    # During add-on registration Blender may expose _RestrictData,
    # which has no .scenes attribute yet.
    if not hasattr(bpy.data, "scenes"):
        return False

    for scene in bpy.data.scenes:
        tsdraft_reset_scene_settings_to_defaults(scene)

    ns = bpy.app.driver_namespace
    ns[_keys.AUTO_FOLLOW_SIGNATURE_KEY] = None
    return True


def tsdraft_deferred_startup_reset():
    try:
        if tsdraft_reset_all_scenes_to_defaults():
            _bbox.redraw_viewports()
            return None
    except Exception:
        _debug.swallowed("draft.tsdraft_deferred_startup_reset")

    # Blender is still in restricted-data phase. Try again shortly.
    return 0.25


@persistent
def tsdraft_reset_defaults_on_load(_dummy):
    # Run after a .blend/startup file is loaded so old saved UI values
    # do not carry into the new session.
    try:
        tsdraft_reset_all_scenes_to_defaults()
        _bbox.redraw_viewports()
    except Exception:
        _debug.swallowed("draft.tsdraft_reset_defaults_on_load")


def register():
    bpy.types.Scene.tsdraft_auto_follow = bpy.props.BoolProperty(
        name="自動追従",
        description="元オブジェクトの形状・変形に合わせてBounding Boxと寸法を自動更新",
        default=True,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_font_size = bpy.props.IntProperty(
        name="文字サイズ",
        default=30,
        min=10,
        max=200,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_font_color = bpy.props.FloatVectorProperty(
        name="文字色",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_front_x_offset_x_mm = bpy.props.FloatProperty(name="前面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_x_offset_y_mm = bpy.props.FloatProperty(name="前面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_y_offset_x_mm = bpy.props.FloatProperty(name="前面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_y_offset_y_mm = bpy.props.FloatProperty(name="前面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_z_offset_x_mm = bpy.props.FloatProperty(name="前面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_z_offset_y_mm = bpy.props.FloatProperty(name="前面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_x_offset_x_mm = bpy.props.FloatProperty(name="上面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_x_offset_y_mm = bpy.props.FloatProperty(name="上面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_y_offset_x_mm = bpy.props.FloatProperty(name="上面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_y_offset_y_mm = bpy.props.FloatProperty(name="上面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_z_offset_x_mm = bpy.props.FloatProperty(name="上面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_z_offset_y_mm = bpy.props.FloatProperty(name="上面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_x_offset_x_mm = bpy.props.FloatProperty(name="側面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_x_offset_y_mm = bpy.props.FloatProperty(name="側面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_y_offset_x_mm = bpy.props.FloatProperty(name="側面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_y_offset_y_mm = bpy.props.FloatProperty(name="側面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_z_offset_x_mm = bpy.props.FloatProperty(name="側面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_z_offset_y_mm = bpy.props.FloatProperty(name="側面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_x_offset_x_mm = bpy.props.FloatProperty(name="任意 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_x_offset_y_mm = bpy.props.FloatProperty(name="任意 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_y_offset_x_mm = bpy.props.FloatProperty(name="任意 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_y_offset_y_mm = bpy.props.FloatProperty(name="任意 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_z_offset_x_mm = bpy.props.FloatProperty(name="任意 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_z_offset_y_mm = bpy.props.FloatProperty(name="任意 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)



    bpy.types.Scene.tsdraft_show_bbox = bpy.props.BoolProperty(
        name="BOXを表示",
        default=True,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_frame_mode = bpy.props.EnumProperty(
        name="枠表示",
        description="サイズ枠の表示方法",
        items=(
            ('BOX', "BOX", "外接BOXを表示"),
            ('LINES', "寸法線のみ", "表示中の寸法に対応する線だけ表示"),
            ('NONE', "なし", "枠線を表示しない"),
        ),
        default='BOX',
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_frame_color = bpy.props.FloatVectorProperty(
        name="枠線色",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_frame_width = bpy.props.FloatProperty(
        name="枠線の太さ",
        default=1.5,
        min=1.0,
        max=8.0,
        precision=1,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_bbox_top = bpy.props.BoolProperty(
        name="上面 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_front = bpy.props.BoolProperty(
        name="前面 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_side = bpy.props.BoolProperty(
        name="側面 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_user = bpy.props.BoolProperty(
        name="任意 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_dimensions = bpy.props.BoolProperty(
        name="寸法を表示",
        default=True,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_dimension_unit = bpy.props.EnumProperty(
        name="単位",
        description="寸法表示に使う単位",
        items=(
            ('MM', "mm", "ミリメートル"),
            ('CM', "cm", "センチメートル"),
            ('M', "m", "メートル"),
        ),
        default='MM',
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_top = bpy.props.BoolProperty(
        name="上面 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_front = bpy.props.BoolProperty(
        name="前面 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_side = bpy.props.BoolProperty(
        name="側面 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_user = bpy.props.BoolProperty(
        name="任意 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    # 旧ファイル互換用。UIではtsdraft_drawing_backgroundを使用。
    bpy.types.Scene.tsdraft_show_grid = bpy.props.BoolProperty(
        name="グリッド表示（旧）",
        default=False
    )

    background_items = (
        ('WHITE', "白", "白背景"),
        ('GRID', "グリッド", "白背景にBlenderグリッドを表示"),
        ('BLACK', "黒", "黒背景"),
        ('CUSTOM', "カスタム", "好きな背景色を指定"),
    )

    bpy.types.Scene.tsdraft_drawing_background = bpy.props.EnumProperty(
        name="図面ビュー背景",
        description="図面ビューの背景表示",
        items=background_items,
        default='WHITE',
        update=_views.update_drawing_background
    )

    bpy.types.Scene.tsdraft_drawing_background_color = bpy.props.FloatVectorProperty(
        name="図面ビューのカスタム背景色",
        subtype='COLOR',
        size=3,
        default=(0.18, 0.18, 0.18),
        min=0.0,
        max=1.0,
        update=_views.update_drawing_background
    )

    bpy.types.Scene.tsdraft_export_background = bpy.props.EnumProperty(
        name="書き出し背景",
        description="実寸PNGを書き出す時の背景表示。軸・原点・3Dカーソルは常に非表示です",
        items=background_items,
        default='WHITE'
    )

    bpy.types.Scene.tsdraft_export_background_color = bpy.props.FloatVectorProperty(
        name="書き出しのカスタム背景色",
        subtype='COLOR',
        size=3,
        default=(0.18, 0.18, 0.18),
        min=0.0,
        max=1.0
    )

    bpy.types.Scene.tsdraft_sheet_paper_size = bpy.props.EnumProperty(
        name="用紙サイズ",
        items=(
            ('A4', "A4", "210×297mm"),
            ('A3', "A3", "297×420mm"),
            ('A2', "A2", "420×594mm"),
            ('A1', "A1", "594×841mm"),
            ('A0', "A0", "841×1189mm"),
            ('CUSTOM', "カスタム", "幅と高さをmmで指定"),
        ),
        default='A4'
    )

    bpy.types.Scene.tsdraft_sheet_custom_width_mm = bpy.props.FloatProperty(
        name="カスタム幅",
        description="カスタム用紙の幅(mm)",
        default=210.0,
        min=10.0,
        soft_max=3000.0,
        precision=1
    )

    bpy.types.Scene.tsdraft_sheet_custom_height_mm = bpy.props.FloatProperty(
        name="カスタム高さ",
        description="カスタム用紙の高さ(mm)",
        default=297.0,
        min=10.0,
        soft_max=3000.0,
        precision=1
    )

    bpy.types.Scene.tsdraft_sheet_orientation = bpy.props.EnumProperty(
        name="用紙の向き",
        items=(
            ('AUTO', "自動", "収まる向きを自動選択"),
            ('PORTRAIT', "縦", "縦向き"),
            ('LANDSCAPE', "横", "横向き"),
        ),
        default='AUTO'
    )

    bpy.types.Scene.tsdraft_sheet_scale = bpy.props.EnumProperty(
        name="縮率",
        items=(
            ('1_1', "1:1", "原寸"),
            ('1_2', "1:2", "50%"),
            ('1_5', "1:5", "20%"),
            ('1_10', "1:10", "10%"),
            ('CUSTOM', "任意", "任意の縮率"),
        ),
        default='1_1'
    )

    bpy.types.Scene.tsdraft_sheet_custom_scale = bpy.props.FloatProperty(
        name="任意縮率",
        description="1:N の N を指定します",
        default=2.0,
        min=1.0,
        soft_max=100.0,
        precision=2
    )

    bpy.types.Scene.tsdraft_show_dimension_adjustments = bpy.props.BoolProperty(
        name="寸法位置の微調整",
        default=False
    )

    bpy.types.Scene.tsdraft_drawing_mode = bpy.props.BoolProperty(
        name="図面モード",
        default=False
    )

    bpy.types.Scene.tsdraft_user_view_mode = bpy.props.BoolProperty(
        name="任意ビューモード",
        default=False
    )

    bpy.types.Scene.tsdraft_dark_place = bpy.props.BoolProperty(
        name="なんかずっと暗いとこ",
        default=False,
        update=_bbox.redraw_viewports
    )

    for cls in classes:
        bpy.utils.register_class(cls)

    _overlay.tsdraft_remove_legacy_draw_handlers()
    _overlay.ensure_draw_handler()
    _overlay.ensure_bbox_draw_handler()
    _overlay.ensure_view_label_handler()
    _bbox.ensure_cleanup_handler()

    if tsdraft_reset_defaults_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(tsdraft_reset_defaults_on_load)

    # register()中はbpy.dataが_RestrictDataのことがあるため、
    # 初期化はBlenderが通常状態へ戻ってから実行する。
    try:
        if not bpy.app.timers.is_registered(tsdraft_deferred_startup_reset):
            bpy.app.timers.register(
                tsdraft_deferred_startup_reset,
                first_interval=0.25
            )
    except Exception:
        _debug.swallowed("draft.register")

    try:
        if not bpy.app.timers.is_registered(_views.tsdraft_quad_zoom_lock_timer):
            bpy.app.timers.register(
                _views.tsdraft_quad_zoom_lock_timer,
                first_interval=0.10,
                persistent=True
            )
    except Exception:
        _debug.swallowed("draft.register")


def _unregister_scene_props():
    """このアドオンが register() で作った Scene プロパティを全て削除する。

    以前は削除対象を手書きのタプルで列挙していたが、register() 側に
    プロパティを足したときに追従されず、消し残しが発生していた。
    列挙をやめ、接頭辞で特定することで register() と必ず一致させる。
    """
    prefix = "tsdraft_"
    for name in [n for n in dir(bpy.types.Scene) if n.startswith(prefix)]:
        try:
            delattr(bpy.types.Scene, name)
        except Exception:
            # 1つ失敗しても残りの削除は続ける。内容は握り潰さず出す。
            traceback.print_exc()


def unregister():
    if tsdraft_reset_defaults_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(tsdraft_reset_defaults_on_load)

    try:
        if bpy.app.timers.is_registered(tsdraft_deferred_startup_reset):
            bpy.app.timers.unregister(tsdraft_deferred_startup_reset)
    except Exception:
        _debug.swallowed("draft.unregister")

    try:
        if bpy.app.timers.is_registered(_views.tsdraft_quad_zoom_lock_timer):
            bpy.app.timers.unregister(_views.tsdraft_quad_zoom_lock_timer)
    except Exception:
        _debug.swallowed("draft.unregister")

    _overlay.remove_draw_handler()
    _overlay.remove_bbox_draw_handler()
    _overlay.remove_view_label_handler()
    _bbox.remove_cleanup_handler()

    namespace = bpy.app.driver_namespace
    namespace[_keys.DATA_KEY] = []
    namespace[_keys.SOURCE_KEY] = None
    namespace[_keys.VIEW_STATE_KEY] = None
    namespace[_keys.DARK_VIEW_STATE_KEY] = None
    namespace[_keys.AUTO_FOLLOW_SIGNATURE_KEY] = None
    namespace[_keys.AUTO_FOLLOW_GUARD_KEY] = None

    for cls in reversed(classes):
        # 登録されていないものは飛ばす。未登録の状態で呼ばれても
        # クラスの数だけエラーを出さないようにするため。本当に
        # 外し損ねたときのエラーが埋もれる。
        if not hasattr(cls, "bl_rna"):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            _debug.swallowed("unfold.unregister")

    _unregister_scene_props()

if __name__ == "__main__":
    register()
