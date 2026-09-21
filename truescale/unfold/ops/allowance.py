"""糊代を辺ごとに消す／戻す／入れ替えるオペレータ。

糊代はシームの辺すべてに、片側だけ付く。それでよい場面が
ほとんどだが、人にしか決められないことが2つある。

  要る／要らない  「ここは差し込むので糊代は要らない」
  どちら側に付くか「この面には出したくない」

とくに2つめは、既定の側が内部の走査順で決まっていて、画面に
出ている A / B / C とは無関係。つまり利用者には説明できない。
だから移す手立てが要る。

入れ替えても、そちら側に置けなければ元の側のままになる。
置けない場所へ無理に置くと、切ったとき型紙自体を切ってしまう。

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


def _island_labels(context, source_obj, edges):
    """その辺の糊代が、いまどの型紙に乗っているか。辺番号 -> 呼び名。

    呼び名（A / B / C）は marking 側が決めている。export 側は面番号
    までしか知らないので、ここで突き合わせる。

    型紙が無い、まだ糊代を出していない、などのときは空を返す。
    答えられないときに黙って嘘の名前を出すより、何も言わないほうが
    よい。
    """
    from ...core import objects as _obj
    from ...export import allowance as _al
    from ...marking import compute as _compute

    unfold = _obj.unfold_for_source(source_obj)
    if unfold is None:
        return {}

    try:
        made = _al.build(context, source_obj, unfold)
        records, face_to_island, _adjacency = _compute.island_metadata(
            context, source_obj, unfold
        )
    except Exception:
        return {}

    labels = {}
    for index in edges:
        face = made.placed.get(int(index))
        if face is None:
            continue
        island = face_to_island.get(int(face))
        if island is None or island >= len(records):
            continue
        labels[int(index)] = records[island]["label"]

    return labels


def _moved_text(before, after, edges):
    """移り先を「A → C」の形にまとめる。答えられなければ空。"""
    moves = []
    for index in sorted(edges):
        was = before.get(index)
        now = after.get(index)
        if was and now and was != now:
            moves.append(f"{was} → {now}")

    if not moves:
        return ""

    unique = sorted(set(moves))
    if len(unique) <= 3:
        return "（" + "、".join(unique) + "）"
    return f"（{unique[0]} ほか {len(unique) - 1} 通り）"


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


class TSUNFOLD_OT_flip_tab_edges(bpy.types.Operator):
    bl_idname = "truescale_unfold.flip_tab_edges"
    bl_label = "選んだ辺の糊代を入れ替え"
    bl_description = (
        "選択したシームの辺について、糊代を反対側の型紙へ移します"
    )
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

        edges = _selected_seam_edges(obj)
        if not edges:
            self.report({'INFO'}, "シームの辺が選択されていません")
            return {'CANCELLED'}

        before = _island_labels(context, obj, edges)

        for index in edges:
            _allowance.toggle_flipped(obj, index)

        after = _island_labels(context, obj, edges)

        # 押したあと型紙を探しに行かなくて済むよう、移り先を言う。
        # 置けなくて元の側のままだったときは、何も出ない。
        moved = _moved_text(before, after, edges)

        self.report(
            {'INFO'}, f"{len(edges)} 本の糊代を入れ替えました{moved}"
        )
        return {'FINISHED'}


class TSUNFOLD_OT_reset_tab_edges(bpy.types.Operator):
    bl_idname = "truescale_unfold.reset_tab_edges"
    bl_label = "糊代を全部戻す"
    bl_description = (
        "手で消した辺と入れ替えた辺の記録を全て消し、既定の状態へ戻します"
    )
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

        count = _allowance.clear_edge_marks(obj)

        self.report({'INFO'}, f"{count} 本の指示を戻しました")
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_toggle_tab_edges,
    TSUNFOLD_OT_flip_tab_edges,
    TSUNFOLD_OT_reset_tab_edges,
)
