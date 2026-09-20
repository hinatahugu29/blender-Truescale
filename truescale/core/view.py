"""3Dビューの見え方。

■ 再描画

設定を変えただけでは画面は描き直されない。値を書き換えたあとに
tag_redraw を呼んで初めて反映される。呼び忘れると「変えたのに
変わらない」という形で現れる。重い処理はしないので、迷ったら
呼んでよい。

■ 見る位置

型紙は原点に平たく作られるので、作った直後は画面の外にあることが
多い。作ったら上から見て枠に入れる、までをここで面倒を見る。

■ 印刷プレビュー中の元モデル

プレビュー中は元モデルを隠す。戻すときのために、隠す前の状態を
シーンへ控えておく。控えを取らずに一律で表示へ戻すと、もともと
隠していた人のモデルが勝手に出てくる。
"""

import bpy

from .. import debug as _debug
from . import objects as _objects
from . import session as _session


def tag_redraw():
    """開いている3Dビューすべてに再描画を要求する。"""
    wm = bpy.context.window_manager if bpy.context else None
    if not wm:
        return

    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def focus_selected(context, top_view=False):
    """Frame the active object and keep orbit/zoom centered on it."""
    obj = context.active_object
    if obj is None:
        return

    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return

    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)

    if region is None or rv3d is None:
        return

    try:
        rv3d.lock_rotation = False
    except Exception:
        _debug.swallowed("core.view._focus_selected_unfold")

    try:
        with context.temp_override(area=area, region=region, space_data=space):
            if top_view:
                bpy.ops.view3d.view_axis(type='TOP', align_active=False)
            bpy.ops.view3d.view_selected(use_all_regions=False)
    except Exception:
        _debug.swallowed("core.view._focus_selected_unfold")


def print_preview_source_visibility(context, preview_on):
    scene = context.scene

    if preview_on:
        source = _objects.seam_source(context)
        if source is None:
            source = _objects.source_from_context(context)

        # Active object may be the generated pattern; resolve its source.
        if source is None:
            unfold = _objects.resolve_unfold_for_layout(context)
            if unfold is not None:
                source = bpy.data.objects.get(
                    unfold.get("tsunfold_source", "")
                )

        if source is None:
            return

        scene[_session.PREVIEW_SOURCE_NAME] = source.name
        scene[_session.PREVIEW_SOURCE_HIDE_GET] = bool(
            source.hide_get()
        )
        scene[_session.PREVIEW_SOURCE_HIDE_VIEWPORT] = bool(
            source.hide_viewport
        )

        source.hide_set(True)
        source.hide_viewport = True

    else:
        source_name = str(
            scene.get(_session.PREVIEW_SOURCE_NAME, "")
        )
        source = bpy.data.objects.get(source_name)

        if source is not None:
            try:
                source.hide_viewport = bool(
                    scene.get(
                        _session.PREVIEW_SOURCE_HIDE_VIEWPORT,
                        False,
                    )
                )
                source.hide_set(
                    bool(
                        scene.get(
                            _session.PREVIEW_SOURCE_HIDE_GET,
                            False,
                        )
                    )
                )
            except Exception:
                _debug.swallowed("core.view._pattern_print_preview_source_visibility")

        scene[_session.PREVIEW_SOURCE_NAME] = ""
        scene[_session.PREVIEW_SOURCE_HIDE_GET] = False
        scene[_session.PREVIEW_SOURCE_HIDE_VIEWPORT] = False

    tag_redraw()
