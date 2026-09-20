"""サイドバーのパネル（三面図側）。

描くだけで、押した先の処理はオペレータが持つ。

寸法の欄は、面図ごとに意味のある軸だけを出す。正面図に奥行きの
寸法を出しても読めないため。どの軸が有効かは dimension が決める。
"""

import bpy

from . import bbox as _bbox
from . import dimension as _dimension
from . import prefs as _prefs
from . import keys as _keys
from . import viewstate as _viewstate


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

        # 四分割は三面図の本命の表示なのに、押す場所が無かった。
        # 単一ビューの4つより先に置く。
        quad = box.row()
        quad.scale_y = 1.2
        quad.operator(
            "truescale_draft.quad_view",
            text="三面＋任意ビュー（四分割）",
            icon='MESH_GRID',
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


        prefs = _prefs.get_addon_preferences(context)
        if prefs is None or prefs.show_dark_place_button:
            layout.separator()
            box = layout.box()
            box.operator(
                "truescale_draft.dark_place",
                text="暗所表示"
            )
