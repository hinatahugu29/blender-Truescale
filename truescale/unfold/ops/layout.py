"""用紙への配置のオペレータ。

実寸のまま紙に収める。収まらなければ収まらないと言う。
縮めて収めることは決してしない。

手で動かす編集は modal で、その間はマーキングを描かない。
動かしている面に注記が追従せず、ちらつくため。
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
from ...marking import dragging as _dragging
from ...marking import interact as _interact
from ...marking import seams as _seams
from ...marking import source as _source
from ...marking import storage as _storage
from ...marking import symmetry as _symmetry
from ...marking import tools as _tools
from .. import build as _build


class TSUNFOLD_OT_auto_layout(bpy.types.Operator):
    bl_idname = "truescale_unfold.auto_layout"
    bl_label = "用紙に自動レイアウト"
    bl_description = "表示中の用紙枠へ、実寸のままアイランドを自動配置します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _objects.resolve_unfold_for_layout(context) is not None

    def execute(self, context):
        obj = _objects.resolve_unfold_for_layout(context)

        if obj.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError as exc:
                self.report({'ERROR'}, f"Object Modeへ戻せませんでした: {exc}")
                return {'CANCELLED'}

        ok, message = _build.try_shelf_layout(context, obj, allow_rotate=True)

        if not ok:
            # 1枚に収まらないなら、紙をまたいで並べ直す。
            #
            # 以前はここで諦めていた。その結果、島は作ったときのまま
            # 横一列に並び続ける。島の多い形だと3メートルの帯になり、
            # 分割すると A4 で18枚の横長になってしまう。
            content_w, content_h = _build.content_size(context.scene)
            ok, message, _cols, _rows = _build.pack_for_pages(
                context, obj, content_w, content_h
            )

        _interact.invalidate_layout_cache()

        if not ok:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}

        _build.show_from_top(context, obj)
        self.report({'INFO'}, message)
        return {'FINISHED'}


class TSUNFOLD_OT_layout_edit(bpy.types.Operator):
    bl_idname = "truescale_unfold.layout_edit"
    bl_label = "レイアウトの変更"
    bl_description = "編集モードに入り、面を1枚選ぶだけでアイランド全体を自動選択します"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _last_selected = None

    @classmethod
    def poll(cls, context):
        return _objects.resolve_unfold_for_layout(context) is not None

    def invoke(self, context, event):
        # 手動レイアウトの印は、編集モードへ入れたあとで立てる。
        # ここで立てると、途中で失敗して CANCELLED を返す道が
        # 2つあり、そのとき立ったまま残る。残ると印が消える。
        _interact.clear_island_highlight()
        _interact.clear_live_preview()
        _view.tag_redraw()

        obj = _objects.resolve_unfold_for_layout(context)

        if obj is None:
            return {'CANCELLED'}

        # 動かしている最中も印が見えるよう、いまの位置を控える。
        # 形は変わらないので、あとは島ごとの移動量を足すだけで済む。
        source = _objects.source_from_context(context)
        if source is not None:
            _dragging.take_snapshot(context, source, obj)

        # Smooth finishing is a separate Curve. Manual layout edits operate
        # on the real flat Mesh, so temporarily return to POLY display.
        context.scene[_session.DISPLAY_MODE] = "POLY"
        for candidate in bpy.data.objects:
            if (
                candidate.type == 'CURVE'
                and bool(candidate.get("tsunfold_smooth_generated", False))
            ):
                candidate.hide_viewport = True
                candidate.hide_set(True)

        obj.hide_viewport = False
        obj.hide_set(False)

        for o in context.selected_objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        if obj.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                _debug.swallowed("ops.TSUNFOLD_OT_layout_edit.invoke")

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='DESELECT')
            context.tool_settings.mesh_select_mode = (False, False, True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"レイアウト編集モードへ入れませんでした: {exc}")
            return {'CANCELLED'}

        _view.focus_selected(context, top_view=False)

        # ここまで来て初めて、手動レイアウト中になる。
        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = True
        _view.tag_redraw()

        self._last_selected = frozenset()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.12, window=context.window)
        wm.modal_handler_add(self)

        self.report({'INFO'}, "面をクリック → アイランド全選択 → Gで移動できます")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        obj = context.active_object

        if (
            obj is None
            or obj.type != 'MESH'
            or not bool(obj.get("tsunfold_generated", False))
            or obj.mode != 'EDIT'
        ):
            self._finish(context)
            return {'FINISHED'}

        if event.type == 'ESC':
            self._finish(context)
            self.report({'INFO'}, "アイランド自動選択を終了しました")
            return {'FINISHED'}

        if event.type == 'TIMER':
            try:
                # Manual layout is intentionally quiet:
                # do not sync obj.data or rebuild marking caches every TIMER.
                # BMesh selection handling below is enough while moving.

                import bmesh
                bm = bmesh.from_edit_mesh(obj.data)
                bm.faces.ensure_lookup_table()

                # IMPORTANT:
                # Do not call update_edit_mesh every TIMER tick. Doing so while
                # Blender's native G/rotate transform is running can fight the
                # transform modal and make the selected island appear immovable.
                selected_now = frozenset(f.index for f in bm.faces if f.select)

                # Ignore the selection set created by our own previous expansion.
                if selected_now != self._last_selected:
                    newly_selected = selected_now - (self._last_selected or frozenset())

                    if newly_selected:
                        # Expand from each newly selected face through connected geometry.
                        # The flattened UV islands are disconnected mesh components,
                        # so connectivity exactly matches an island.
                        target_faces = set()

                        for face_index in newly_selected:
                            if face_index >= len(bm.faces):
                                continue

                            seed = bm.faces[face_index]
                            stack = [seed]
                            visited = {seed}

                            while stack:
                                face = stack.pop()
                                target_faces.add(face)

                                for edge in face.edges:
                                    for linked_face in edge.link_faces:
                                        if linked_face not in visited:
                                            visited.add(linked_face)
                                            stack.append(linked_face)

                        # Preserve already-selected islands, then add full new islands.
                        for face in target_faces:
                            face.select = True

                        bmesh.update_edit_mesh(
                            obj.data,
                            loop_triangles=False,
                            destructive=False,
                        )
                        _view.tag_redraw()

                        # Refresh after expansion so TIMER does not re-trigger on our own work.
                        self._last_selected = frozenset(
                            f.index for f in bm.faces if f.select
                        )
                    else:
                        self._last_selected = selected_now

            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_layout_edit.modal")

        if (
            event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'G', 'R', 'S'}
            and event.value == 'RELEASE'
        ):
            _view.tag_redraw()

        return {'PASS_THROUGH'}

    def _finish(self, context):
        obj = context.active_object

        # 先に BMesh の内容をメッシュへ書き戻す。このあと手動
        # レイアウトの印を下ろすので、その時点でメッシュが最新で
        # ないと、印が動かす前の位置に出る。
        if (
            obj is not None
            and obj.type == 'MESH'
            and bool(obj.get("tsunfold_generated", False))
            and obj.mode == 'EDIT'
        ):
            try:
                import bmesh
                bmesh.update_edit_mesh(
                    obj.data,
                    loop_triangles=False,
                    destructive=False,
                )
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_layout_edit._finish")

        # 手動レイアウト中の印を下ろす。以前はここで下ろしておらず、
        # 「レイアウト確定」を押さずに Tab で抜けると立ったまま
        # 残った。残ると描画側が「まだ動かしている最中」と思い込み、
        # 控えをずらそうとして BMesh を取りに行く。編集モードを
        # 抜けているので取れず、例外は握り潰され、印だけが黙って
        # 消える。
        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        _dragging.clear()
        _interact.invalidate_layout_cache()

        _view.tag_redraw()

        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                _debug.swallowed("ops.TSUNFOLD_OT_layout_edit._finish")
            self._timer = None


class TSUNFOLD_OT_layout_confirm(bpy.types.Operator):
    bl_idname = "truescale_unfold.layout_confirm"
    bl_label = "レイアウト確定"
    bl_description = "手動レイアウト編集を終了してObject Modeへ戻ります"

    @classmethod
    def poll(cls, context):
        obj = _objects.resolve_unfold_for_layout(context)
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = _objects.resolve_unfold_for_layout(context)

        if obj is None:
            self.report({'WARNING'}, "型紙が見つかりません")
            return {'CANCELLED'}

        try:
            # Ensure the generated pattern is active before mode change.
            if context.active_object is not obj:
                for selected in context.selected_objects:
                    selected.select_set(False)

                obj.hide_set(False)
                obj.hide_viewport = False
                obj.select_set(True)
                context.view_layer.objects.active = obj

            if obj.mode == 'EDIT':
                try:
                    obj.update_from_editmode()
                except Exception:
                    _debug.swallowed("ops.TSUNFOLD_OT_layout_confirm.execute")

                bpy.ops.object.mode_set(mode='OBJECT')

        except Exception as exc:
            self.report(
                {'ERROR'},
                f"レイアウト確定に失敗しました: {exc}",
            )
            return {'CANCELLED'}

        context.scene[_session.MANUAL_LAYOUT_ACTIVE] = False
        _dragging.clear()
        _interact.invalidate_layout_cache()
        _view.tag_redraw()

        try:
            _build.show_from_top(context, obj)
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_layout_confirm.execute")

        self.report({'INFO'}, "レイアウトを確定しました")
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_auto_layout,
    TSUNFOLD_OT_layout_edit,
    TSUNFOLD_OT_layout_confirm,
)
