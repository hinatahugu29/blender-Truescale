"""寸法の箱を作る・消す・表示を切り替えるオペレータ。

測る対象を決めるところ。ここで作った箱の辺の長さが、
そのまま図面の寸法になる。
"""

import bpy
from bpy_extras.io_utils import ExportHelper

from ... import debug as _pkg_debug
from .. import bbox as _bbox
from .. import dimension as _dimension
from .. import keys as _keys
from .. import overlay as _overlay
from .. import viewstate as _viewstate
from .. import views as _views
from ..export import capture as _capture
from ..export import sheet as _sheet


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
            _pkg_debug.swallowed("draft.TSDRAFT_OT_make_size_bbox.execute")

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
                _pkg_debug.swallowed("draft.TSDRAFT_OT_delete_bbox.execute")

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


classes = (
    TSDRAFT_OT_toggle_size_overlay,
    TSDRAFT_OT_make_size_bbox,
    TSDRAFT_OT_delete_bbox,
    TSDRAFT_OT_reset_label_offsets,
)
