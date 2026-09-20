"""視点を切り替えるオペレータ。

四分割と単一ビュー、正面・上面・側面・自由。作図向けの表示への
切り替えと、元へ戻すもの。

戻す操作を必ず用意してあるのは、作図用の表示が「その人の
Blender の設定を一時的に上書きしたもの」だから。戻せないと、
このアドオンを使っただけで作業環境が変わってしまう。
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
            _pkg_debug.swallowed("draft.TSDRAFT_OT_quad_view.execute")

        try:
            _overlay.tsdraft_sync_ortho_zoom(space)
        except Exception:
            _pkg_debug.swallowed("draft.TSDRAFT_OT_quad_view.execute")

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
                    _pkg_debug.swallowed("draft.TSDRAFT_OT_dark_place.execute")
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
                _pkg_debug.swallowed("draft.TSDRAFT_OT_dark_place.execute")
        else:
            scene.tsdraft_dark_place = False

            # 暗所に入る直前の背景・グリッド状態へそのまま戻す。
            _viewstate.restore_dark_place_view(space)

        _bbox.redraw_viewports()
        return {'FINISHED'}


classes = (
    TSDRAFT_OT_quad_view,
    TSDRAFT_OT_front_view,
    TSDRAFT_OT_top_view,
    TSDRAFT_OT_side_view,
    TSDRAFT_OT_user_view,
    TSDRAFT_OT_apply_drawing_style,
    TSDRAFT_OT_restore_view,
    TSDRAFT_OT_dark_place,
)
