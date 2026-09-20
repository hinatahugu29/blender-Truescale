"""印を置く道具と、置いた印を消すオペレータ。

合印・番号・矢印を手で置く。道具はトグルで、invoke と modal の
中身は marking.tools が持つ。ここにあるのは種類の指定だけ。

消す側は段階を分けてある。直前の1つ、種類ごと、全部。まとめて
消すものしか無いと、少し戻したいときに全部やり直しになる。
"""

import bpy
from bpy.props import StringProperty

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


class TSUNFOLD_OT_marking_tool_off(bpy.types.Operator):
    bl_idname = "truescale_unfold.marking_tool_off"
    bl_label = "マーキングツールOFF"
    bl_description = "現在の合印・番号・矢印・文字の連続配置ツールを停止します"

    def execute(self, context):
        _interact.set_active_tool(context.scene, "NONE")
        _interact.clear_live_preview()
        context.scene[_session.MODAL_RUNNING] = False
        context.scene[_session.MARKING_FINISH_REQUESTED] = False
        _view.tag_redraw()
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
        return _objects.source_from_context(context) is not None

    def invoke(self, context, event):
        return _tools.toggle_invoke(
            self,
            context,
            "NOTCH",
        )

    def modal(self, context, event):
        return _tools.modal(self, context, event)


class TSUNFOLD_OT_place_number(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_number"
    bl_label = "型紙番号モード"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _objects.source_from_context(context) is not None

    def invoke(self, context, event):
        return _tools.toggle_invoke(
            self,
            context,
            "NUMBER",
        )

    def modal(self, context, event):
        return _tools.modal(self, context, event)


class TSUNFOLD_OT_place_text(bpy.types.Operator):
    """任意の文字を置く道具。

    入口だけが無かった。置いたあとの処理も描画も揃っていたのに、
    この道具を始める手段が無く、設定（文字・大きさ・色）も
    パネルに出ていなかった。

    型紙へ直接書くメモ（place_flat_memo）とは別物。こちらは元モデル
    側の注記として持つので、型紙を作り直しても残り、立体と平面の
    両方に出る。
    """

    bl_idname = "truescale_unfold.place_text"
    bl_label = "文字モード"
    bl_description = "入力した文字を、クリックした位置へ置きます"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _objects.source_from_context(context) is not None

    def invoke(self, context, event):
        return _tools.toggle_invoke(self, context, "TEXT")

    def modal(self, context, event):
        return _tools.modal(self, context, event)


class TSUNFOLD_OT_place_arrow(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_arrow"
    bl_label = "上方向矢印モード"

    _source_name = ""
    _first_anchor = None
    _last_mode = "NONE"

    @classmethod
    def poll(cls, context):
        return _objects.source_from_context(context) is not None

    def invoke(self, context, event):
        return _tools.toggle_invoke(
            self,
            context,
            "ARROW",
        )

    def modal(self, context, event):
        return _tools.modal(self, context, event)


class TSUNFOLD_OT_finish_marking(bpy.types.Operator):
    bl_idname = "truescale_unfold.finish_marking"
    bl_label = "マーキング終了"
    bl_description = "3D型紙マーキングを終了し、開始前の選択・モードへ戻ります"

    def execute(self, context):
        if not _interact.session_active(context.scene):
            self.report({'INFO'}, "現在マーキングモードではありません")
            return {'CANCELLED'}

        context.scene.tsunfold_active_tool = "NONE"
        _interact.clear_live_preview()
        context.scene[_session.MODAL_RUNNING] = False
        _interact.request_finish(context)
        _interact.restore_work_state(context)

        self.report({'INFO'}, "マーキングを終了し、元の作業状態へ戻しました")
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
            _view.print_preview_source_visibility(
                context,
                False,
            )

        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        scene = context.scene
        source = _objects.seam_source(context)

        if source is None:
            unfold = _objects.resolve_unfold_for_layout(context)
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
            _interact.set_active_tool(scene, "NONE")
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_return_default.execute")

        _interact.clear_live_preview()
        _interact.clear_island_highlight()

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
            _debug.swallowed("ops.TSUNFOLD_OT_return_default.execute")

        if source is not None:
            # 1) All stored/manual/auto annotations.
            _interact.save_annotations(source, [])

            # 2) All generated pattern outputs.
            _objects.delete_generated_for_source(
                context,
                source,
            )

            # 元モデルのシームはユーザーの入力データなので絶対に変更しない。
            try:
                source.data.update()
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_return_default.execute")

        # Hide/delete state is now clean; restore just the source selection.
        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_return_default.execute")

        if source is not None:
            source.hide_set(False)
            source.hide_viewport = False
            source.select_set(True)
            context.view_layer.objects.active = source
            scene[_session.SEAM_SOURCE] = ""
        else:
            scene[_session.SEAM_SOURCE] = ""

        _interact.invalidate_layout_cache()
        _view.tag_redraw()

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
        source = _objects.source_from_context(context)

        if source is None:
            unfold = _objects.resolve_unfold_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("tsunfold_source", "")
                )

        if source is not None:
            items = [
                item
                for item in _storage.load(source)
                if str(item.get("type", "")) != "arrow"
            ]
            _interact.save_annotations(source, items)

        context.scene.tsunfold_arrow_mode = "NONE"
        _interact.clear_live_preview()
        _view.tag_redraw()
        self.report({'INFO'}, "自動・手動の矢印をすべて非表示/削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_reset_number(bpy.types.Operator):
    bl_idname = "truescale_unfold.reset_number"
    bl_label = "番号を1に戻す"

    def execute(self, context):
        context.scene.tsunfold_number_start = 1
        context.scene.tsunfold_next_number = 1
        return {'FINISHED'}


class TSUNFOLD_OT_clear_annotations(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_annotations"
    bl_label = "型紙印を全部削除"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _objects.source_from_context(context)

        if source is None:
            unfold = _objects.resolve_unfold_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("tsunfold_source", "")
                )

        if source is None:
            return {'CANCELLED'}

        _interact.save_annotations(source, [])
        _interact.clear_live_preview()
        self.report({'INFO'}, "すべてのマーキングをクリアしました")
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_marking_tool_off,
    TSUNFOLD_OT_place_notch,
    TSUNFOLD_OT_place_number,
    TSUNFOLD_OT_place_arrow,
    TSUNFOLD_OT_place_text,
    TSUNFOLD_OT_finish_marking,
    TSUNFOLD_OT_return_default,
    TSUNFOLD_OT_clear_arrows_all,
    TSUNFOLD_OT_reset_number,
    TSUNFOLD_OT_clear_annotations,
)
