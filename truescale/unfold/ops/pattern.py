"""型紙の生成と表示切り替えのオペレータ。

UV から実寸メッシュを作り、要らなくなったものを片付け、
元モデルと型紙のどちらを見るかを切り替える。

作る処理そのものは unfold.build にある。ここが持つのは、
どれを対象にするか、作り直すときに何を消すか、といった段取り。
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


class TSUNFOLD_OT_unfold_real_mesh(bpy.types.Operator):
    bl_idname = "truescale_unfold.unfold_real_mesh"
    bl_label = "展開"
    bl_description = "シームに従ってUV展開し、実寸スケールの平面Meshを生成します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and not bool(obj.get("tsunfold_generated", False))

    def execute(self, context):
        src_obj = context.active_object

        if src_obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = src_obj.data

        # Make sure the latest seam flags are in the Mesh datablock.
        _seams.sync_live_seams(src_obj)

        # Never reuse the previous helper unwrap.
        # Keep user's own UV maps untouched and create a fresh temporary map.
        previous_uv_name = (
            mesh.uv_layers.active.name
            if mesh.uv_layers.active is not None
            else ""
        )

        temp_uv_name = "__PATTERN_HELPER_TEMP_UV__"

        existing_temp = mesh.uv_layers.get(temp_uv_name)
        if existing_temp is not None:
            try:
                mesh.uv_layers.remove(existing_temp)
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_unfold_real_mesh.execute")

        temp_uv = mesh.uv_layers.new(
            name=temp_uv_name,
            do_init=False,
        )
        mesh.uv_layers.active = temp_uv

        for o in context.selected_objects:
            o.select_set(False)

        src_obj.select_set(True)
        context.view_layer.objects.active = src_obj

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')

        # Clear any stale UV selection/pin state on the fresh layer.
        try:
            bpy.ops.uv.select_all(action='SELECT')
            bpy.ops.uv.reset()
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_unfold_real_mesh.execute")

        try:
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
        except RuntimeError:
            bpy.ops.uv.unwrap(method='CONFORMAL', margin=0.001)

        bpy.ops.object.mode_set(mode='OBJECT')

        mesh.update()
        uv_layer = mesh.uv_layers.get(temp_uv_name)

        if uv_layer is None:
            self.report({'ERROR'}, "UV展開に失敗しました。Meshとシームを確認してください。")
            return {'CANCELLED'}

        scale = _build.real_scale(src_obj, mesh, uv_layer)
        if scale is None:
            self.report({'ERROR'}, "実寸スケールを計算できませんでした。")
            return {'CANCELLED'}

        # Current seam state is the only source of truth.
        # Delete stale pattern/layout/smooth output before rebuilding.
        _objects.delete_generated_for_source(context, src_obj)

        result = _build.flat_mesh(context, src_obj, mesh, uv_layer, scale)

        # Temporary helper UV is no longer needed after the flat Mesh/mappings
        # have been built. Restore the user's original active UV if possible.
        temp_layer = mesh.uv_layers.get(temp_uv_name)
        if temp_layer is not None:
            try:
                mesh.uv_layers.remove(temp_layer)
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_unfold_real_mesh.execute")

        if previous_uv_name:
            previous_layer = mesh.uv_layers.get(previous_uv_name)
            if previous_layer is not None:
                mesh.uv_layers.active = previous_layer

        if result is None:
            self.report({'ERROR'}, "平面Meshを生成できませんでした。")
            return {'CANCELLED'}

        _build.pack_islands(context, result, context.scene.tsunfold_spacing_mm)
        _interact.invalidate_layout_cache()
        _build.show_from_top(context, result)
        _view.focus_selected(context, top_view=True)

        size = _build.object_xy_size_mm(context, result)
        if size:
            self.report({'INFO'}, f"{result.name}：{size[0]:.1f} × {size[1]:.1f} mm")
        return {'FINISHED'}


class TSUNFOLD_OT_build_pattern(bpy.types.Operator):
    bl_idname = "truescale_unfold.build_pattern"
    bl_label = "型紙作成"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        source = _objects.seam_source(context)
        if source is None:
            self.report({'WARNING'}, "元モデルが見つかりません")
            return {'CANCELLED'}

        try:
            if context.active_object and context.active_object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_build_pattern.execute")

        for obj in context.selected_objects:
            try:
                obj.select_set(False)
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_build_pattern.execute")

        source.hide_set(False)
        source.hide_viewport = False
        source.select_set(True)
        context.view_layer.objects.active = source
        context.scene[_session.SEAM_SOURCE] = source.name

        # A new build is always a full regeneration from the CURRENT seams on the loaded source model.
        _auto_notch.remove(source)
        _interact.clear_island_highlight()
        _interact.invalidate_layout_cache()
        _seams.sync_live_seams(source)

        seam_count = sum(
            1 for edge in source.data.edges
            if bool(edge.use_seam)
        )
        if seam_count == 0:
            self.report(
                {'WARNING'},
                "読み込み済みモデルにシームがありません。元モデル側でシームを設定してください",
            )
            return {'CANCELLED'}

        result = bpy.ops.truescale_unfold.unfold_real_mesh()
        if 'FINISHED' not in result:
            return {'CANCELLED'}

        context.scene[_session.SEAM_PREVIEW_READY] = False

        auto_notch_count = 0
        if str(
            getattr(
                context.scene,
                "tsunfold_notch_mode",
                "AUTO",
            )
        ) == "AUTO":
            auto_notch_count = _auto_notch.refresh(
                context,
                source,
            )

        _interact.invalidate_layout_cache()
        _view.tag_redraw()

        if auto_notch_count:
            self.report(
                {'INFO'},
                f"型紙＋ID＋矢印＋オート合印 {auto_notch_count} 個を作成しました"
            )
        else:
            self.report({'INFO'}, "型紙＋ID＋矢印を作成しました")
        return {'FINISHED'}


class TSUNFOLD_OT_delete_unfold(bpy.types.Operator):
    bl_idname = "truescale_unfold.delete_unfold"
    bl_label = "展開図を削除"
    bl_description = "生成されたローポリ展開図となめらか線をまとめて削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        try:
            context.scene.tsunfold_pattern_preview = False
            context.scene[_session.PREVIEW_PREV_ACTIVE] = ""
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_delete_unfold.execute")

        mesh_targets = [
            obj for obj in list(bpy.data.objects)
            if obj.type == 'MESH' and bool(obj.get("tsunfold_generated", False))
        ]

        curve_targets = [
            obj for obj in list(bpy.data.objects)
            if obj.type == 'CURVE' and bool(obj.get("tsunfold_smooth_generated", False))
        ]

        total = 0

        for obj in curve_targets:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                bpy.data.curves.remove(data)
            total += 1

        for obj in mesh_targets:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                bpy.data.meshes.remove(data)
            total += 1

        # Deleting the unfold result also clears viewport paper/preview overlays.
        context.scene.tsunfold_show_paper = False
        context.scene.tsunfold_preview = False

        if total == 0:
            self.report({'INFO'}, "展開図はありません。用紙枠とプレビューをOFFにしました")
            _view.tag_redraw()
            return {'CANCELLED'}

        self.report({'INFO'}, f"展開図関連を {total} 個削除し、用紙枠も非表示にしました")
        _view.tag_redraw()
        return {'FINISHED'}


class TSUNFOLD_OT_toggle_source_visibility(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_source_visibility"
    bl_label = "元モデル表示 / 非表示"
    bl_description = "読み込み済みの元3Dモデルだけを表示/非表示します。型紙やビュー位置は変更しません"

    def execute(self, context):
        source = _objects.seam_source(context)
        if source is None:
            source = _objects.source_from_context(context)

        if source is None:
            self.report({'WARNING'}, "元モデルが見つかりません")
            return {'CANCELLED'}

        # hide_set() is local-view aware and very軽量。
        # hide_viewport datablock settingは変更しない。
        try:
            is_hidden = bool(source.hide_get())
        except Exception:
            is_hidden = False

        try:
            source.hide_set(not is_hidden)
        except Exception as exc:
            self.report({'WARNING'}, f"元モデルの表示切替に失敗しました: {exc}")
            return {'CANCELLED'}

        _view.tag_redraw()

        if is_hidden:
            self.report({'INFO'}, "元モデルを表示しました")
        else:
            self.report({'INFO'}, "元モデルを非表示にしました")

        return {'FINISHED'}


class TSUNFOLD_OT_toggle_pattern_preview(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_pattern_preview"
    bl_label = "型紙を表示 / 隠す"
    bl_description = "実際に生成された型紙オブジェクトを表示/非表示し、元モデルとの確認を切り替えます"

    def execute(self, context):
        scene = context.scene

        unfold = _objects.resolve_unfold_for_layout(context)
        if unfold is None:
            for candidate in bpy.data.objects:
                if (
                    candidate.type == 'MESH'
                    and bool(candidate.get("tsunfold_generated", False))
                ):
                    unfold = candidate
                    break

        if unfold is None:
            self.report({'WARNING'}, "先に「型紙展開」で型紙を作成してください")
            return {'CANCELLED'}

        showing = bool(getattr(scene, "tsunfold_pattern_preview", False))

        if not showing:
            active = context.active_object
            scene[_session.PREVIEW_PREV_ACTIVE] = (
                active.name if active is not None else ""
            )

            for obj in context.selected_objects:
                try:
                    obj.select_set(False)
                except Exception:
                    _debug.swallowed("ops.TSUNFOLD_OT_toggle_pattern_preview.execute")

            unfold.hide_set(False)
            unfold.hide_viewport = False
            unfold.select_set(True)
            context.view_layer.objects.active = unfold

            for obj in bpy.data.objects:
                if (
                    obj.type == 'CURVE'
                    and bool(obj.get("tsunfold_smooth_generated", False))
                ):
                    obj.hide_viewport = True

            scene.tsunfold_pattern_preview = True

            # 表示切替だけ行い、ビュー方向・ズーム・注視点は変更しない。
            # 元モデルが「消えたように見える」原因になる自動フレーミングを廃止。
            self.report({'INFO'}, "生成済み型紙を表示しました")

        else:
            unfold.hide_set(True)
            unfold.hide_viewport = True

            prev_name = scene.get(_session.PREVIEW_PREV_ACTIVE, "")
            prev = bpy.data.objects.get(prev_name)

            if prev is None:
                src_name = unfold.get("tsunfold_source", "")
                prev = bpy.data.objects.get(src_name)

            if prev is not None:
                for obj in context.selected_objects:
                    try:
                        obj.select_set(False)
                    except Exception:
                        _debug.swallowed("ops.TSUNFOLD_OT_toggle_pattern_preview.execute")

                # 元モデルの表示状態は専用トグルの設定を尊重する。
                if not prev.hide_get():
                    prev.select_set(True)
                    context.view_layer.objects.active = prev

            scene.tsunfold_pattern_preview = False
            self.report({'INFO'}, "型紙を隠して元の作業へ戻りました")

        _view.tag_redraw()
        return {'FINISHED'}


class TSUNFOLD_OT_toggle_preview(bpy.types.Operator):
    bl_idname = "truescale_unfold.toggle_preview"
    bl_label = "印刷プレビュー"
    bl_description = "必要ならObject Modeへ戻して、用紙と最終印刷輪郭を3Dビューに表示します"

    def execute(self, context):
        obj = context.active_object

        # Print preview is a layout-level view, so always return to Object Mode.
        if obj is not None and obj.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError as exc:
                self.report({'ERROR'}, f"Object Modeへ切り替えられませんでした: {exc}")
                return {'CANCELLED'}

        scene = context.scene
        scene.tsunfold_preview = not scene.tsunfold_preview

        _view.print_preview_source_visibility(
            context,
            bool(scene.tsunfold_preview),
        )
        _view.tag_redraw()

        if scene.tsunfold_preview:
            self.report({'INFO'}, "Object Modeへ切り替えて印刷プレビュー ON")
        else:
            self.report({'INFO'}, "印刷プレビュー OFF")
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_unfold_real_mesh,
    TSUNFOLD_OT_build_pattern,
    TSUNFOLD_OT_delete_unfold,
    TSUNFOLD_OT_toggle_source_visibility,
    TSUNFOLD_OT_toggle_pattern_preview,
    TSUNFOLD_OT_toggle_preview,
)
