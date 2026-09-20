"""糊代を辺ごとに消す／戻すオペレータ。

糊代はシームの辺すべてに付く。それでよい場面がほとんどだが、
「ここは差し込むので糊代は要らない」「ここは手が入らない」と
いった判断は人にしかできない。

■ 選んでからボタン、にした理由

画面をクリックして辺を拾う方式にもできるが、シームを指定する
のと同じ操作にしたほうが覚えることが少ない。元メッシュを編集
モードにして辺を選び、ボタンを押す。Blender のシーム指定と
同じ手順になる。

■ 覚えるのは元メッシュの辺番号

展開後の辺番号で覚えると、型紙を作り直した瞬間に全部戻る。
展開結果は作り直しのたびに別物になるため。詳しくは
export.allowance を見ること。
"""

import bpy
from bpy.props import BoolProperty

from ...core import objects as _objects
from ...export import allowance as _allowance


def _source(context):
    """糊代の設定を持つ元メッシュ。編集中のものを優先する。"""
    obj = context.active_object
    if obj is not None and obj.type == 'MESH':
        if not bool(obj.get("tsunfold_generated", False)):
            return obj
    return _objects.seam_source(context)


def _selected_seam_edges(obj):
    """選択されている辺のうち、シームが立っているものの番号。

    シームでない辺には糊代が付かないので、消す対象にもならない。
    黙って記録すると「消したはずなのに付いている」の原因が
    分からなくなる。
    """
    import bmesh

    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        return {
            int(e.index) for e in bm.edges
            if e.select and e.seam
        }

    return {
        int(e.index) for e in obj.data.edges
        if e.select and e.use_seam
    }


class TSUNFOLD_OT_toggle_tab_edges(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_tab_edges"
    bl_label = "選んだ辺の糊代"
    bl_description = "選択したシームの辺について、糊代を付けるかどうかを切り替えます"
    bl_options = {'REGISTER', 'UNDO'}

    off: BoolProperty(
        name="消す",
        description="真なら糊代を消し、偽なら戻す",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = _source(context)
        if obj is None:
            self.report({'ERROR'}, "元のメッシュが見つかりません")
            return {'CANCELLED'}

        edges = _selected_seam_edges(obj)
        if not edges:
            self.report({'INFO'}, "シームの辺が選択されていません")
            return {'CANCELLED'}

        for index in edges:
            _allowance.toggle_disabled(obj, index, off=bool(self.off))

        word = "消しました" if self.off else "戻しました"
        self.report({'INFO'}, f"{len(edges)} 本の糊代を{word}")
        return {'FINISHED'}


class TSUNFOLD_OT_reset_tab_edges(bpy.types.Operator):
    bl_idname = "truescale_unfold.reset_tab_edges"
    bl_label = "糊代を全部戻す"
    bl_description = "手で消した糊代の記録を全て消し、シームの辺すべてに付く状態へ戻します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = _source(context)
        if obj is None:
            self.report({'ERROR'}, "元のメッシュが見つかりません")
            return {'CANCELLED'}

        count = len(_allowance.disabled_edges(obj))
        _allowance.set_disabled_edges(obj, ())

        self.report({'INFO'}, f"{count} 本の糊代を戻しました")
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_toggle_tab_edges,
    TSUNFOLD_OT_reset_tab_edges,
)
