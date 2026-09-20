"""三面図側の登録。

このファイルが持つのは登録だけ。中身はそれぞれの置き場にある。

  draft.ops        ボタンを押したときに走るもの
  draft.panel      サイドバーの見た目
  draft.props      UIに出す設定（Scene プロパティ）
  draft.prefs      アドオン設定
  draft.bbox       寸法の基準になる箱と、その追従・後始末
  draft.overlay    箱の枠・寸法・面図の見出しの描画
  draft.dimension  寸法の値と、図面に出す文字
  draft.viewstate  作図用の表示へ変え、必ず元へ戻す
  draft.views      四分割と単一ビューの切り替え
  draft.export     実寸PNGと三面図シートの書き出し
  draft.keys       driver_namespace のキーと配色

■ 描画ハンドラは必ず外す

register で3つ足す。外し損ねると、アドオンを無効にしたあとも
描画が呼ばれ続ける。ハンドルは driver_namespace に置く（詳細は
draft.keys）。

■ ファイルを開いたときの初期化

作図の設定はその作業のためのもので、ファイルに残しても次の作業で
邪魔になる。読み込み後に既定へ戻す。@persistent が要る。付けないと
Blender が読み込み時にハンドラを外し、初期化が1回しか走らない。
"""

import bpy
from bpy.app.handlers import persistent

from .. import debug as _debug
from . import bbox as _bbox
from . import keys as _keys
from . import views as _views
from . import overlay as _overlay
from . import ops as _ops
from . import prefs as _prefs
from . import props as _props
from .panel import TSDRAFT_PT_main

SVG_PX_TO_MM = 25.4 / 96.0


# 登録するクラス。オペレータは ops/ が機能別に持っている。
# パネルは最後。先にオペレータが登録されていないと、パネルの
# 参照先が無い状態になる。
classes = (
    _prefs.TSDRAFT_Preferences,
) + _ops.classes + (
    TSDRAFT_PT_main,
)


def tsdraft_deferred_startup_reset():
    try:
        if _props.tsdraft_reset_all_scenes_to_defaults():
            _bbox.redraw_viewports()
            return None
    except Exception:
        _debug.swallowed("draft.tsdraft_deferred_startup_reset")

    # Blender is still in restricted-data phase. Try again shortly.
    return 0.25


@persistent
def tsdraft_reset_defaults_on_load(_dummy):
    # Run after a .blend/startup file is loaded so old saved UI values
    # do not carry into the new session.
    try:
        _props.tsdraft_reset_all_scenes_to_defaults()
        _bbox.redraw_viewports()
    except Exception:
        _debug.swallowed("draft.tsdraft_reset_defaults_on_load")


def register():
    """クラス・設定・ハンドラを登録する。"""
    _props.register()

    for cls in classes:
        bpy.utils.register_class(cls)

    _overlay.tsdraft_remove_legacy_draw_handlers()
    _overlay.ensure_draw_handler()
    _overlay.ensure_bbox_draw_handler()
    _overlay.ensure_view_label_handler()
    _bbox.ensure_cleanup_handler()

    if tsdraft_reset_defaults_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(tsdraft_reset_defaults_on_load)

    # register()中はbpy.dataが_RestrictDataのことがあるため、
    # 初期化はBlenderが通常状態へ戻ってから実行する。
    try:
        if not bpy.app.timers.is_registered(tsdraft_deferred_startup_reset):
            bpy.app.timers.register(
                tsdraft_deferred_startup_reset,
                first_interval=0.25
            )
    except Exception:
        _debug.swallowed("draft.register")

    try:
        if not bpy.app.timers.is_registered(_views.tsdraft_quad_zoom_lock_timer):
            bpy.app.timers.register(
                _views.tsdraft_quad_zoom_lock_timer,
                first_interval=0.10,
                persistent=True
            )
    except Exception:
        _debug.swallowed("draft.register")


def unregister():
    if tsdraft_reset_defaults_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(tsdraft_reset_defaults_on_load)

    try:
        if bpy.app.timers.is_registered(tsdraft_deferred_startup_reset):
            bpy.app.timers.unregister(tsdraft_deferred_startup_reset)
    except Exception:
        _debug.swallowed("draft.unregister")

    try:
        if bpy.app.timers.is_registered(_views.tsdraft_quad_zoom_lock_timer):
            bpy.app.timers.unregister(_views.tsdraft_quad_zoom_lock_timer)
    except Exception:
        _debug.swallowed("draft.unregister")

    _overlay.remove_draw_handler()
    _overlay.remove_bbox_draw_handler()
    _overlay.remove_view_label_handler()
    _bbox.remove_cleanup_handler()

    namespace = bpy.app.driver_namespace
    namespace[_keys.DATA_KEY] = []
    namespace[_keys.SOURCE_KEY] = None
    namespace[_keys.VIEW_STATE_KEY] = None
    namespace[_keys.DARK_VIEW_STATE_KEY] = None
    namespace[_keys.AUTO_FOLLOW_SIGNATURE_KEY] = None
    namespace[_keys.AUTO_FOLLOW_GUARD_KEY] = None

    for cls in reversed(classes):
        # 登録されていないものは飛ばす。未登録の状態で呼ばれても
        # クラスの数だけエラーを出さないようにするため。本当に
        # 外し損ねたときのエラーが埋もれる。
        if not hasattr(cls, "bl_rna"):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            _debug.swallowed("unfold.unregister")

    _props.unregister()

if __name__ == "__main__":
    register()
