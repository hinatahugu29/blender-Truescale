"""型紙に直接書く文字と、島の対応付けのオペレータ。

合印などと違い、型紙オブジェクト側に持たせる。元モデルの
どこに対応するかを持たない、その紙だけのための書き込みだから。

島の対応付けは、平面のどの一枚が立体のどこだったかを確かめる
ためのもの。型紙だけ見ても分からなくなるので、選ぶと元モデル側が
光る。
"""

import bpy
import math
import time

from bpy.props import (
    FloatProperty,
    FloatVectorProperty,
    StringProperty,
)
from bpy_extras import view3d_utils
from mathutils import Vector

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


class TSUNFOLD_OT_place_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.place_flat_memo"
    bl_label = "型紙にメモを追加"
    bl_description = "型紙上をクリックして、文字入力ダイアログを開きます"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        unfold = _objects.resolve_unfold_for_layout(context)
        if unfold is None:
            self.report({'WARNING'}, "先に型紙を作成してください")
            return {'CANCELLED'}

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "メモを置きたい型紙位置をクリックしてください")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.report({'INFO'}, "メモ配置をキャンセルしました")
            return {'CANCELLED'}

        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        if not _interact.event_is_view_window(context, event):
            return {'PASS_THROUGH'}

        hit = _interact.raycast_flat_pattern(
            context,
            event,
        )
        if hit is None:
            self.report({'WARNING'}, "型紙の面をクリックしてください")
            return {'RUNNING_MODAL'}

        unfold, local = hit

        bpy.ops.truescale_unfold.confirm_flat_memo(
            'INVOKE_DEFAULT',
            unfold_name=unfold.name,
            local_pos=(
                float(local.x),
                float(local.y),
                float(local.z),
            ),
        )
        return {'FINISHED'}


class TSUNFOLD_OT_confirm_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.confirm_flat_memo"
    bl_label = "型紙メモ"
    bl_description = "型紙に配置するメモ文字を入力します"
    bl_options = {'REGISTER', 'UNDO'}

    memo_text: StringProperty(
        name="メモ文字",
        description="型紙に印刷する自由メモ",
        default="",
    )

    memo_size_mm: FloatProperty(
        name="文字サイズ",
        description="メモ文字のサイズ",
        default=6.0,
        min=2.0,
        max=30.0,
        precision=1,
    )

    memo_angle: FloatProperty(
        name="回転",
        description="型紙上でのメモ文字の回転角度",
        subtype='ANGLE',
        default=0.0,
        min=-math.pi,
        max=math.pi,
    )

    unfold_name: StringProperty(
        options={'HIDDEN'},
        default="",
    )

    local_pos: FloatVectorProperty(
        options={'HIDDEN'},
        size=3,
        default=(0.0, 0.0, 0.0),
    )

    def invoke(self, context, event):
        self.memo_text = ""
        self.memo_size_mm = 6.0
        return context.window_manager.invoke_props_dialog(
            self,
            width=360,
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "memo_text", text="文字")
        layout.prop(self, "memo_size_mm", text="文字サイズ")

    def execute(self, context):
        text = str(self.memo_text).strip()
        if not text:
            self.report({'WARNING'}, "メモ文字を入力してください")
            return {'CANCELLED'}

        unfold = bpy.data.objects.get(self.unfold_name)
        if (
            unfold is None
            or unfold.type != 'MESH'
            or not bool(unfold.get("tsunfold_generated", False))
        ):
            self.report({'WARNING'}, "配置先の型紙が見つかりません")
            return {'CANCELLED'}

        items = _interact.get_flat_memos(unfold)
        items.append({
            "text": text,
            "pos": [
                float(self.local_pos[0]),
                float(self.local_pos[1]),
                float(self.local_pos[2]),
            ],
            "size_mm": float(self.memo_size_mm),
            "angle": 0.0,
        })
        memo_index = len(items) - 1
        _interact.set_flat_memos(unfold, items)

        # Place first, then let the user visually rotate it in the viewport.
        try:
            bpy.ops.truescale_unfold.rotate_flat_memo(
                'INVOKE_DEFAULT',
                unfold_name=unfold.name,
                memo_index=memo_index,
            )
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_confirm_flat_memo.execute")

        self.report({'INFO'}, f"メモ「{text}」を配置しました")
        return {'FINISHED'}


class TSUNFOLD_OT_rotate_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.rotate_flat_memo"
    bl_label = "メモを回転"
    bl_description = "配置したメモをマウスで回転し、左クリックで確定します"
    bl_options = {'REGISTER', 'UNDO'}

    unfold_name: StringProperty(
        options={'HIDDEN'},
        default="",
    )

    memo_index: bpy.props.IntProperty(
        options={'HIDDEN'},
        default=-1,
    )

    _original_angle = 0.0

    def invoke(self, context, event):
        unfold = bpy.data.objects.get(self.unfold_name)
        if unfold is None:
            return {'CANCELLED'}

        items = _interact.get_flat_memos(unfold)
        if not (0 <= int(self.memo_index) < len(items)):
            return {'CANCELLED'}

        self._original_angle = float(
            items[int(self.memo_index)].get("angle", 0.0)
        )

        context.window_manager.modal_handler_add(self)
        self.report(
            {'INFO'},
            "マウスで回転 → 左クリックで確定 / ESC・右クリックで回転キャンセル",
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        unfold = bpy.data.objects.get(self.unfold_name)
        if unfold is None:
            return {'CANCELLED'}

        items = _interact.get_flat_memos(unfold)
        index = int(self.memo_index)
        if not (0 <= index < len(items)):
            return {'CANCELLED'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            items[index]["angle"] = float(self._original_angle)
            _interact.set_flat_memos(unfold, items)
            self.report({'INFO'}, "回転をキャンセルしました")
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.report({'INFO'}, "メモの回転を確定しました")
            return {'FINISHED'}

        if event.type == 'MOUSEMOVE':
            region = context.region
            rv3d = getattr(context.space_data, "region_3d", None)

            if (
                region is None
                or rv3d is None
                or context.area is None
                or context.area.type != 'VIEW_3D'
            ):
                return {'PASS_THROUGH'}

            pos = items[index].get("pos", [0.0, 0.0, 0.0])
            try:
                local = Vector((
                    float(pos[0]),
                    float(pos[1]),
                    float(pos[2]) if len(pos) > 2 else 0.0,
                ))
            except Exception:
                return {'PASS_THROUGH'}

            world = unfold.matrix_world @ local
            screen = view3d_utils.location_3d_to_region_2d(
                region,
                rv3d,
                world,
            )
            if screen is None:
                return {'PASS_THROUGH'}

            dx = float(event.mouse_region_x) - float(screen.x)
            dy = float(event.mouse_region_y) - float(screen.y)

            if abs(dx) + abs(dy) <= 2.0:
                return {'RUNNING_MODAL'}

            angle = math.atan2(dy, dx)
            items[index]["angle"] = float(angle)
            _interact.set_flat_memos(unfold, items)
            _view.tag_redraw()

            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


class TSUNFOLD_OT_edit_flat_memo(bpy.types.Operator):
    bl_idname = "truescale_unfold.edit_flat_memo"
    bl_label = "型紙メモ編集"
    bl_description = "選択した型紙メモをRで回転、X/Deleteで削除します"
    bl_options = {'REGISTER', 'UNDO'}

    _mode = "IDLE"
    _start_angle = 0.0

    def invoke(self, context, event):
        picked = _interact.pick_flat_memo_at_mouse(context, event)
        if picked is None:
            _interact.clear_selected_memo()
            return {'CANCELLED'}

        unfold, index = picked
        _interact.selected_memo["unfold"] = unfold.name
        _interact.selected_memo["index"] = int(index)
        _view.tag_redraw()

        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "メモ選択中：R 回転 / X・Delete 削除 / Esc 終了")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        unfold, index, item = _interact.selected_memo_item()
        if unfold is None or item is None:
            return {'CANCELLED'}

        if event.type == 'ESC':
            if self._mode == "ROTATE":
                items = _interact.get_flat_memos(unfold)
                items[index]["angle"] = float(self._start_angle)
                _interact.set_flat_memos(unfold, items)
            else:
                _interact.clear_selected_memo()
                return {'FINISHED'}

            self._mode = "IDLE"
            _view.tag_redraw()
            return {'RUNNING_MODAL'}

        if self._mode == "IDLE":
            if event.type == 'R' and event.value == 'PRESS':
                self._mode = "ROTATE"
                self._start_angle = float(item.get("angle", 0.0))
                self.report({'INFO'}, "メモ回転中：マウスで回転 / 左クリック確定 / 右クリック・Escキャンセル")
                return {'RUNNING_MODAL'}

            if event.type in {'X', 'DEL', 'BACK_SPACE'} and event.value == 'PRESS':
                items = _interact.get_flat_memos(unfold)
                del items[index]
                _interact.set_flat_memos(unfold, items)
                _interact.clear_selected_memo()
                self.report({'INFO'}, "メモを削除しました")
                return {'FINISHED'}

            # Clicking another memo selects it; clicking empty space exits selection.
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                picked = _interact.pick_flat_memo_at_mouse(context, event)
                if picked is None:
                    _interact.clear_selected_memo()
                    return {'FINISHED'}
                new_unfold, new_index = picked
                _interact.selected_memo["unfold"] = new_unfold.name
                _interact.selected_memo["index"] = int(new_index)
                _view.tag_redraw()
                return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}

        if self._mode == "ROTATE":
            if event.type == 'MOUSEMOVE':
                region = context.region
                rv3d = getattr(context.space_data, "region_3d", None)
                if region is None or rv3d is None:
                    return {'RUNNING_MODAL'}

                pos = item.get("pos", [0.0, 0.0, 0.0])
                local = Vector((float(pos[0]), float(pos[1]), float(pos[2])))
                world = unfold.matrix_world @ local
                screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
                if screen is None:
                    return {'RUNNING_MODAL'}

                dx = float(event.mouse_region_x) - float(screen.x)
                dy = float(event.mouse_region_y) - float(screen.y)
                if abs(dx) + abs(dy) > 2.0:
                    angle = math.atan2(dy, dx)
                    items = _interact.get_flat_memos(unfold)
                    items[index]["angle"] = float(angle)
                    _interact.set_flat_memos(unfold, items)
                return {'RUNNING_MODAL'}

            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                self._mode = "IDLE"
                self.report({'INFO'}, "メモ回転を確定しました")
                return {'RUNNING_MODAL'}

            if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                items = _interact.get_flat_memos(unfold)
                items[index]["angle"] = float(self._start_angle)
                _interact.set_flat_memos(unfold, items)
                self._mode = "IDLE"
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}


class TSUNFOLD_OT_clear_flat_memos(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_flat_memos"
    bl_label = "メモを全削除"
    bl_description = "型紙に直接配置したメモだけをすべて削除します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        unfold = _objects.resolve_unfold_for_layout(context)

        if unfold is None:
            # Source selected fallback.
            source = _objects.source_from_context(context)
            if source is not None:
                unfold = _objects.unfold_for_source(source)

        if unfold is None:
            self.report({'WARNING'}, "型紙が見つかりません")
            return {'CANCELLED'}

        count = len(_interact.get_flat_memos(unfold))
        _interact.set_flat_memos(unfold, [])

        self.report({'INFO'}, f"型紙メモを {count} 個削除しました")
        return {'FINISHED'}


class TSUNFOLD_OT_pick_corresponding_island(bpy.types.Operator):
    _last_click_time = 0.0
    _last_click_x = -10000
    _last_click_y = -10000

    bl_idname = "truescale_unfold.pick_corresponding_island"
    bl_label = "対応確認"

    def invoke(self, context, event):
        self._last_click_time = 0.0
        self._last_click_x = -10000
        self._last_click_y = -10000

        scene = context.scene

        if bool(
            getattr(
                scene,
                "tsunfold_correspondence_mode",
                False,
            )
        ):
            scene.tsunfold_correspondence_mode = False
            _interact.clear_island_highlight()
            self.report({'INFO'}, "対応確認をOFFにしました")
            return {'FINISHED'}

        scene.tsunfold_correspondence_mode = True
        context.window_manager.modal_handler_add(self)
        self.report(
            {'INFO'},
            "対応確認ON：元モデルか型紙をクリックすると対応片を表示します"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # If a flat memo is currently selected, its edit operator owns
        # transform/delete events. Do not let those keys pass through to
        # Blender's native object transform, otherwise G moves the whole pattern.
        selected_unfold, selected_index, selected_item = _interact.selected_memo_item()
        if selected_item is not None:
            # Keep Blender's native transform/delete shortcuts from seeing
            # memo-edit keys. Mouse motion must remain available to the memo
            # edit modal, otherwise G-move cannot follow the cursor.
            if event.type in {
                'R',
                'X',
                'DEL',
                'BACK_SPACE',
            }:
                return {'RUNNING_MODAL'}

        # Existing memo has click priority so it can be edited directly.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if _interact.event_is_view_window(context, event):
                picked_memo = _interact.pick_flat_memo_at_mouse(context, event)
                if picked_memo is not None:
                    try:
                        bpy.ops.truescale_unfold.edit_flat_memo(
                            'INVOKE_DEFAULT'
                        )
                    except Exception:
                        _debug.swallowed("ops.TSUNFOLD_OT_pick_corresponding_island.modal")
                    return {'RUNNING_MODAL'}

        # BlenderのDOUBLE_CLICK通知に依存せず、
        # 同じ位置への短時間2クリックを自前判定する。
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            now = time.monotonic()
            dx = int(event.mouse_region_x) - int(self._last_click_x)
            dy = int(event.mouse_region_y) - int(self._last_click_y)

            is_double_click = (
                (now - float(self._last_click_time)) <= 0.38
                and (dx * dx + dy * dy) <= (14 * 14)
            )

            if is_double_click:
                self._last_click_time = 0.0
                self._last_click_x = -10000
                self._last_click_y = -10000

                if _interact.event_is_view_window(context, event):
                    hit = _interact.raycast_flat_pattern(
                        context,
                        event,
                    )
                    if hit is not None:
                        unfold, local = hit
                        try:
                            bpy.ops.truescale_unfold.confirm_flat_memo(
                                'INVOKE_DEFAULT',
                                unfold_name=unfold.name,
                                local_pos=(
                                    float(local.x),
                                    float(local.y),
                                    float(local.z),
                                ),
                            )
                        except Exception as exc:
                            self.report(
                                {'WARNING'},
                                f"メモ入力を開けませんでした: {exc}",
                            )
                        return {'RUNNING_MODAL'}

            else:
                self._last_click_time = now
                self._last_click_x = int(event.mouse_region_x)
                self._last_click_y = int(event.mouse_region_y)

        scene = context.scene

        if not bool(
            getattr(
                scene,
                "tsunfold_correspondence_mode",
                False,
            )
        ):
            return {'FINISHED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            scene.tsunfold_correspondence_mode = False
            _interact.clear_island_highlight()
            return {'FINISHED'}

        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        if not _interact.event_is_view_window(context, event):
            return {'PASS_THROUGH'}

        hit = _interact.raycast_any_visible(context, event)
        if hit is None:
            return {'RUNNING_MODAL'}

        hit_obj, face_index = hit

        try:
            if hit_obj.type == 'MESH' and hit_obj.mode == 'EDIT':
                hit_obj.update_from_editmode()
        except Exception:
            _debug.swallowed("ops.TSUNFOLD_OT_pick_corresponding_island.modal")

        if (
            hit_obj.type == 'MESH'
            and bool(hit_obj.get("tsunfold_generated", False))
        ):
            unfold = hit_obj
            source = bpy.data.objects.get(
                unfold.get("tsunfold_source", "")
            )
            if source is None:
                return {'RUNNING_MODAL'}

            flat_faces = _interact.flat_face_island(
                unfold,
                face_index,
            )
            source_faces = _interact.source_faces_for_flat_island(
                unfold,
                flat_faces,
            )

        elif (
            hit_obj.type == 'MESH'
            and not bool(hit_obj.get("tsunfold_generated", False))
        ):
            source = hit_obj
            unfold = _objects.unfold_for_source(source)

            if unfold is None:
                return {'RUNNING_MODAL'}

            flat_faces = _interact.flat_island_from_source_face(
                unfold,
                face_index,
            )
            source_faces = _interact.source_faces_for_flat_island(
                unfold,
                flat_faces,
            )

        else:
            return {'RUNNING_MODAL'}

        if source_faces:
            _interact.set_island_highlight(
                source,
                source_faces,
            )

        return {'RUNNING_MODAL'}


class TSUNFOLD_OT_clear_island_highlight(bpy.types.Operator):
    bl_idname = "truescale_unfold.clear_island_highlight"
    bl_label = "対応表示を解除"

    def execute(self, context):
        context.scene.tsunfold_correspondence_mode = False
        _interact.clear_island_highlight()
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_place_flat_memo,
    TSUNFOLD_OT_confirm_flat_memo,
    TSUNFOLD_OT_rotate_flat_memo,
    TSUNFOLD_OT_edit_flat_memo,
    TSUNFOLD_OT_clear_flat_memos,
    TSUNFOLD_OT_pick_corresponding_island,
    TSUNFOLD_OT_clear_island_highlight,
)
