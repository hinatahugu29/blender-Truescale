"""図面を書き出すオペレータ。

1面ずつ、全面まとめて、三面図シート。実際の撮影と合成は
draft.export が持つ。ここにあるのは、どこへ保存するかと
保存後の後始末。
"""

import os
from pathlib import Path
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
            self.report({'ERROR'}, "元オブジェクトを選択してください")
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
            self.report({'ERROR'}, "書き出し元の3Dビューが見つかりませんでした")
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


classes = (
    TSDRAFT_OT_preview_three_view_sheet,
    TSDRAFT_OT_export_three_view_sheet,
    TSDRAFT_OT_export_actual_png,
    TSDRAFT_OT_export_all_actual_png,
)
