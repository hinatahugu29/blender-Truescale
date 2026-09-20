"""合印のオペレータ。

オートで入れ直す、オートだけ消す、全部消す。

手で置いた印を巻き込まないことが要点。作り直しのたびに手の作業が
消えては使えない。生成そのものは marking.auto_notch にある。
"""

import bpy

from ... import debug as _debug
from ...core import geometry as _geometry
from ...core import objects as _objects
from ...core import paper as _paper
from ...core import session as _session
from ...core import state as _state
from ...core import units as _units
from ...core import view as _view
from ...export import outline as _outline
from ...export import png as _png
from ...marking import auto_notch as _auto_notch
from ...marking import compute as _compute
from ...marking import interact as _interact
from ...marking import seams as _seams
from ...marking import source as _source
from ...marking import storage as _storage
from ...marking import symmetry as _symmetry
from ...marking import tools as _tools
from .. import build as _build


class TSUNFOLD_OT_refresh_auto_notches(bpy.types.Operator):
    bl_idname = "truescale_unfold.refresh_auto_notches"
    bl_label = "オート合印を更新"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _objects.source_from_context(context)
        if source is None:
            self.report({'WARNING'}, "元の3Dモデルを選択してください")
            return {'CANCELLED'}

        _seams.sync_live_seams(source)

        seam_count = sum(
            1 for edge in source.data.edges
            if bool(edge.use_seam)
        )

        if seam_count == 0:
            self.report({'WARNING'}, "シームがありません。先にシームを入れてください")
            return {'CANCELLED'}

        count = _auto_notch.refresh(context, source)

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
            _debug.swallowed("ops.TSUNFOLD_OT_refresh_auto_notches.execute")

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_refresh_auto_notches.execute")

        source.hide_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source

        _view.tag_redraw()

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
        source = _objects.source_from_context(context)
        if source is None:
            return {'CANCELLED'}

        count = _auto_notch.remove(source)
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
        return _objects.source_from_context(context) is not None

    def execute(self, context):
        source = _objects.source_from_context(context)
        if source is None:
            return {'CANCELLED'}

        items = _storage.load(source)
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

        _interact.save_annotations(source, remaining)

        detail = f"（オート {auto} / 手動 {manual}）" if manual else ""
        self.report(
            {'INFO'},
            f"合印を {auto + manual} 個削除しました{detail}",
        )
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_refresh_auto_notches,
    TSUNFOLD_OT_remove_auto_notches,
    TSUNFOLD_OT_remove_all_notches,
)
