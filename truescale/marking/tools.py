"""マーキング道具の共通の中身。

合印・番号・矢印は、置くものが違うだけで操作はほぼ同じなので、
invoke と modal の中身をここで共有する。オペレータ側は
どの種類かを渡すだけにしてある。

■ トグルである

同じ道具をもう一度押すと止まる。押すたびに別の modal が
積み上がらないよう、動いているかどうかをシーンに持たせて見る。

■ 3Dビューの中のクリックだけを拾う

modal はサイドバーやヘッダの上のクリックも受け取る。そのまま
置くと、パネルを操作したつもりが印が置かれる。領域を確かめる。

■ 矢印は2クリック

始点と終点が要る。1クリック目を覚えておき、2クリック目で確定する。
"""

import bpy
from mathutils import Vector

from .. import debug as _debug
from ..core import objects as _objects
from ..core import session as _session
from ..core import units as _units
from ..core.view import tag_redraw as _view_tag_redraw
from . import interact as _interact
from . import source as _source
from . import storage as _storage


def toggle_invoke(operator, context, mode):
    source = _objects.source_from_context(context)

    if source is None:
        operator.report({'WARNING'}, "元の3Dモデルを選択してください")
        return {'CANCELLED'}

    current = _interact.active_tool(context.scene)

    if current == mode:
        _interact.set_active_tool(context.scene, "NONE")
        _interact.clear_live_preview()
        context.scene[_session.MODAL_RUNNING] = False
        context.scene[_session.MARKING_FINISH_REQUESTED] = False
        operator.report({'INFO'}, "マーキングツールをOFFにしました")
        return {'FINISHED'}

    _interact.begin_session(context, source)

    if mode == "NUMBER":
        context.scene.tsunfold_next_number = int(
            context.scene.tsunfold_number_start
        )

    _interact.set_active_tool(context.scene, mode)
    _interact.clear_live_preview()
    _interact.live_preview["mode"] = mode
    _interact.live_preview["source"] = source.name

    if not bool(context.scene.get(_session.MODAL_RUNNING, False)):
        operator._source_name = source.name
        operator._first_anchor = None
        operator._last_mode = mode
        context.scene[_session.MODAL_RUNNING] = True
        context.window_manager.modal_handler_add(operator)
        return {'RUNNING_MODAL'}

    return {'FINISHED'}


def modal(operator, context, event):
    scene = context.scene

    if bool(scene.get(_session.MARKING_FINISH_REQUESTED, False)):
        _interact.clear_live_preview()
        scene[_session.MODAL_RUNNING] = False
        return {'FINISHED'}

    mode = _interact.active_tool(scene)

    if mode == "NONE":
        _interact.clear_live_preview()
        scene[_session.MODAL_RUNNING] = False
        operator._first_anchor = None
        return {'FINISHED'}

    if mode != getattr(operator, "_last_mode", mode):
        operator._first_anchor = None
        operator._last_mode = mode

    # N-panel, header, toolbar, etc. belong to Blender UI.
    # Never consume their mouse events.
    if event.type in {
        'LEFTMOUSE',
        'RIGHTMOUSE',
        'MIDDLEMOUSE',
        'WHEELUPMOUSE',
        'WHEELDOWNMOUSE',
    } and not _interact.event_is_view_window(context, event):
        return {'PASS_THROUGH'}

    if event.type == 'ESC' and event.value == 'PRESS':
        _interact.set_active_tool(scene, "NONE")
        _interact.clear_live_preview()
        scene[_session.MODAL_RUNNING] = False
        scene[_session.MARKING_FINISH_REQUESTED] = False
        operator._first_anchor = None
        return {'FINISHED'}

    source = bpy.data.objects.get(getattr(operator, "_source_name", ""))

    # Real-time preview follows the mouse inside the actual viewport.
    if event.type == 'MOUSEMOVE':
        if source is None:
            return {'PASS_THROUGH'}

        detail = _interact.raycast_source_detail(
            context,
            event,
            source,
        )

        _interact.live_preview["mode"] = mode
        _interact.live_preview["source"] = source.name

        if detail is None:
            _interact.live_preview["hover_anchor"] = None
            _interact.live_preview["notch_edge"] = -1
            _view_tag_redraw()
            return {'PASS_THROUGH'}

        hover_anchor, hover_local, _hover_face = detail
        _interact.live_preview["hover_anchor"] = hover_anchor

        if mode == "NOTCH":
            nearest = _interact.nearest_seam_edge(
                context,
                source,
                hover_local,
            )
            if nearest is None:
                _interact.live_preview["notch_edge"] = -1
            else:
                edge_index, fraction, _dist = nearest
                _interact.live_preview["notch_edge"] = int(edge_index)
                _interact.live_preview["notch_t"] = float(fraction)

        if mode == "ARROW":
            _interact.live_preview["arrow_start"] = operator._first_anchor

        _view_tag_redraw()
        return {'PASS_THROUGH'}

    if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
        return {'PASS_THROUGH'}

    if source is None:
        scene[_session.MODAL_RUNNING] = False
        return {'CANCELLED'}

    detail = _interact.raycast_source_detail(
        context,
        event,
        source,
    )
    if detail is None:
        operator.report({'WARNING'}, "モデル表面をクリックしてください")
        return {'RUNNING_MODAL'}

    anchor, local_hit, _face_index = detail
    color = _interact.current_color(scene, mode)
    items = _storage.load(source)

    if mode == "NOTCH":
        if scene.tsunfold_notch_mode == "NONE":
            operator.report({'WARNING'}, "合印方式が「合印なし」です")
            return {'RUNNING_MODAL'}

        nearest = _interact.nearest_seam_edge(
            context,
            source,
            local_hit,
        )

        if nearest is None:
            operator.report({'WARNING'}, "赤いシーム付近をクリックしてください")
            return {'RUNNING_MODAL'}

        edge_index, fraction, _dist = nearest

        items.append({
            "type": "notch_edge",
            "edge": edge_index,
            "t": round(float(fraction), 7),
            "color": color,
            "auto": False,
        })
        _storage.save(source, items)

        operator.report({'INFO'}, "合印を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "NUMBER":
        value = int(scene.tsunfold_next_number)

        items.append({
            "type": "number",
            "value": value,
            "anchor": anchor,
            "size_mm": float(scene.tsunfold_number_size_mm),
            "color": color,
        })
        _storage.save(source, items)

        scene.tsunfold_next_number = value + 1
        operator.report({'INFO'}, f"型紙番号 {value} を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "TEXT":
        text = str(scene.tsunfold_custom_text)

        if not text:
            operator.report({'WARNING'}, "任意テキストを入力してください")
            return {'RUNNING_MODAL'}

        items.append({
            "type": "text",
            "text": text,
            "anchor": anchor,
            "size_mm": float(scene.tsunfold_text_size_mm),
            "color": color,
        })
        _storage.save(source, items)

        operator.report({'INFO'}, f"「{text}」を追加しました")
        return {'RUNNING_MODAL'}

    if mode == "ARROW":
        if str(
            getattr(scene, "tsunfold_arrow_mode", "AUTO")
        ) == "NONE":
            operator.report({'WARNING'}, "矢印方式が「なし」です")
            return {'RUNNING_MODAL'}

        if operator._first_anchor is None:
            operator._first_anchor = anchor
            _interact.live_preview["arrow_start"] = anchor
            _interact.live_preview["hover_anchor"] = anchor
            _view_tag_redraw()
            operator.report({'INFO'}, "次に矢印の先端をクリック")
            return {'RUNNING_MODAL'}

        items.append({
            "type": "arrow",
            "a": operator._first_anchor,
            "b": anchor,
            "color": color,
            "head_mm": float(scene.tsunfold_arrow_head_mm),
            "thickness_mm": float(scene.tsunfold_arrow_thickness_mm),
        })
        _storage.save(source, items)

        operator._first_anchor = None
        _interact.live_preview["arrow_start"] = None
        _interact.live_preview["hover_anchor"] = None
        _view_tag_redraw()
        operator.report({'INFO'}, "上方向矢印を追加しました")
        return {'RUNNING_MODAL'}

    return {'PASS_THROUGH'}
