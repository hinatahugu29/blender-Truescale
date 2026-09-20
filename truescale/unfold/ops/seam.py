"""シームと基準寸法のオペレータ。

型紙を作る前の段階。どこで切り開くかを決め、実寸の基準を合わせる。

シームの出来がそのまま型紙の形になるので、ここを間違えると後の
工程では取り返せない。左右対称の付け方は marking.symmetry にある。
"""

import bpy
from bpy.props import FloatProperty

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


class TSUNFOLD_OT_clear_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_seam"
    bl_label = "シームをクリア"
    bl_description = "Blender標準の『シームをクリア』を選択エッジに実行します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        if obj.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')

        context.tool_settings.mesh_select_mode = (False, True, False)

        try:
            bpy.ops.mesh.mark_seam(clear=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"シームをクリアできませんでした: {exc}")
            return {'CANCELLED'}


        return {'FINISHED'}


class TSUNFOLD_OT_mark_seam(bpy.types.Operator):
    bl_idname = "truescale_unfold.mark_seam"
    bl_label = "シームを入れる"
    bl_description = "選択エッジをシーム化します。対称ON時は対称位置も一緒に処理します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        count = _symmetry.apply_to_selected_edges(context, clear=False)

        if count == 0:
            self.report({'INFO'}, "選択エッジがありません")
            return {'CANCELLED'}

        self.report({'INFO'}, f"{count} 本のエッジをシーム化しました")
        return {'FINISHED'}


class TSUNFOLD_OT_load_seamed_object(bpy.types.Operator):
    bl_idname = "truescale_unfold.load_seamed_object"
    bl_label = "シーム付きモデルを読み込む"
    bl_description = "選択中のMeshを型紙元モデルとして登録します。既存のBlenderシームをそのまま使用します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and not bool(obj.get("tsunfold_generated", False))
        )

    def execute(self, context):
        _compute.sanitize_arrow_axis(context.scene)
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'WARNING'}, "シーム付きMeshオブジェクトを選択してください")
            return {'CANCELLED'}

        if bool(obj.get("tsunfold_generated", False)):
            self.report({'WARNING'}, "展開図ではなく元の3Dモデルを選択してください")
            return {'CANCELLED'}

        # Blender標準のEdit Modeで入れた最新シームも読み込む。
        _seams.sync_live_seams(obj)

        try:
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_load_seamed_object.execute")

        seam_count = sum(
            1 for edge in obj.data.edges
            if bool(edge.use_seam)
        )

        if seam_count == 0:
            self.report(
                {'WARNING'},
                "このオブジェクトにはシームがありません。Blender標準機能などで先にシームを設定してください",
            )
            return {'CANCELLED'}

        context.scene[_session.SEAM_SOURCE] = obj.name
        context.scene[_session.SEAM_PREVIEW_READY] = False

        _interact.clear_island_highlight()
        _interact.clear_live_preview()
        _interact.invalidate_layout_cache()
        _view.tag_redraw()

        scene_scale, mm_per_bu = _units.scene_unit_summary(context.scene)
        obj_scale = tuple(float(v) for v in obj.scale)

        self.report(
            {'INFO'},
            (
                f"{obj.name} を読み込みました / "
                f"Unit Scale {scene_scale:g} / "
                f"1 BU = {mm_per_bu:g} mm / "
                f"Object Scale "
                f"{obj_scale[0]:g}, {obj_scale[1]:g}, {obj_scale[2]:g}"
            ),
        )
        return {'FINISHED'}


class TSUNFOLD_OT_calibrate_scale(bpy.types.Operator):
    bl_idname = "truescale_unfold.calibrate_scale"
    bl_label = "選択した辺を基準に実寸を決める"
    bl_description = (
        "編集モードで選んだ辺の合計長さを、実際の寸法として指定します。"
        "そこから 1 BU が何ミリかを逆算します"
    )
    bl_options = {'REGISTER', 'UNDO'}

    target_mm: FloatProperty(
        name="実際の寸法 (mm)",
        description="選んだ辺が実物で何ミリあるか",
        default=100.0,
        min=0.001,
        soft_max=5000.0,
        precision=2,
    )

    _length_bu = 0.0

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == 'MESH'
            and obj.mode == 'EDIT'
        )

    @staticmethod
    def _selected_length_bu(obj):
        """選択されている辺の合計長さ（Blender Unit、ワールド基準）。

        連続した辺を選べば、首周りのような曲線も測れる。
        """
        import bmesh

        bm = bmesh.from_edit_mesh(obj.data)
        matrix = obj.matrix_world

        total = 0.0
        for edge in bm.edges:
            if not edge.select:
                continue
            a = matrix @ edge.verts[0].co
            b = matrix @ edge.verts[1].co
            total += (b - a).length
        return total

    def invoke(self, context, event):
        obj = context.active_object
        self._length_bu = self._selected_length_bu(obj)

        if self._length_bu <= 1e-9:
            self.report({'WARNING'}, "辺が選択されていません")
            return {'CANCELLED'}

        # いまの基準での寸法を初期値にしておくと、
        # 「少しだけ直したい」場合に扱いやすい。
        self.target_mm = max(0.001, _units.scene_bu_to_mm(context.scene, self._length_bu))
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"選択した辺の長さ: {self._length_bu:.6g} BU")
        layout.prop(self, "target_mm")

        if self._length_bu <= 0.0:
            return

        mm_per_bu = self.target_mm / self._length_bu
        layout.label(text=f"→ 1 BU = {mm_per_bu:.6g} mm", icon='DRIVER')

        # 桁を間違えたまま確定しないよう、結果の見当を先に出す。
        # 初期値が数千ミリになることがあり、桁の誤りに気づきにくい。
        current = _units.scene_mm_per_bu(context.scene)
        if current > 0.0:
            ratio = mm_per_bu / current
            if ratio >= 2.0 or ratio <= 0.5:
                layout.label(
                    text=f"現在の基準の {ratio:.4g} 倍になります",
                    icon='ERROR' if (ratio >= 100.0 or ratio <= 0.01) else 'INFO',
                )

        # 型紙ができていれば、その全体寸法がどうなるかを見せる
        source = _objects.seam_source(context)
        unfold = _objects.unfold_for_source(source) if source else None
        if unfold is not None:
            mesh = unfold.data
            if mesh.vertices:
                xs = [v.co.x for v in mesh.vertices]
                ys = [v.co.y for v in mesh.vertices]
                width = (max(xs) - min(xs)) * mm_per_bu
                height = (max(ys) - min(ys)) * mm_per_bu
                layout.label(
                    text=f"型紙全体: 約 {width:.1f} × {height:.1f} mm"
                )

        spacing_mm = float(getattr(context.scene, "tsunfold_spacing_mm", 10.0))
        layout.label(text=f"島の間隔 {spacing_mm:g} mm も同じ基準で扱われます")

    def execute(self, context):
        if self._length_bu <= 1e-9:
            # ダイアログを経ずに呼ばれた場合に備えて測り直す
            obj = context.active_object
            if obj is None or obj.mode != 'EDIT':
                self.report({'ERROR'}, "編集モードで辺を選んでください")
                return {'CANCELLED'}
            self._length_bu = self._selected_length_bu(obj)

        if self._length_bu <= 1e-9:
            self.report({'ERROR'}, "辺が選択されていません")
            return {'CANCELLED'}

        scene = context.scene
        scene.tsunfold_manual_mm_per_bu = self.target_mm / self._length_bu
        scene.tsunfold_scale_mode = "MANUAL"

        _interact.invalidate_layout_cache()
        self.report(
            {'INFO'},
            f"1 BU = {scene.tsunfold_manual_mm_per_bu:.6g} mm に設定しました",
        )
        return {'FINISHED'}


class TSUNFOLD_OT_select_unfold_source(bpy.types.Operator):
    bl_idname = "truescale_unfold.select_unfold_source"
    bl_label = "元の展開図を選択"
    bl_description = "なめらか線の元になった展開図Meshを選択します"

    @classmethod
    def poll(cls, context):
        return _objects.active_smooth(context) is not None

    def execute(self, context):
        smooth = context.active_object
        source_name = smooth.get("tsunfold_smooth_source", "")
        source = bpy.data.objects.get(source_name)

        if source is None:
            self.report({'ERROR'}, "元の展開図が見つかりません")
            return {'CANCELLED'}

        for o in context.selected_objects:
            o.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source

        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_clear_seam,
    TSUNFOLD_OT_mark_seam,
    TSUNFOLD_OT_load_seamed_object,
    TSUNFOLD_OT_calibrate_scale,
    TSUNFOLD_OT_select_unfold_source,
)
