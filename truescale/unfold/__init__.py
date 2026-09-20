"""型紙づくり側の登録。

このファイルが持つのは登録だけ。中身はそれぞれの置き場にある。

  unfold.ops      ボタンを押したときに走るもの
  unfold.panel    サイドバーの見た目
  unfold.props    UIに出す設定（Scene プロパティ）
  unfold.build    UVから実寸メッシュを作る
  unfold.status   パネルに出す案内文
  overlay         3Dビューへの重ね描き
  marking         印の計算・保存・操作
  core            単位、幾何、オブジェクト解決、キャッシュ
  export          PNG書き出し

■ 描画ハンドラは必ず外す

register で3つ足す。unregister で外し損ねると、アドオンを
無効にしたあとも描画が呼ばれ続け、参照先の消えたモジュールを
触って落ちる。ハンドルをモジュール直下に持つのはそのため。

■ ファイルを開いたときの後始末

modal の動作中フラグなどは .blend に残る。残ったまま開くと
「動いていないのに動いていることになっている」状態になり、
道具が二度と起動しなくなる。load_post で戻す。
"""

import bpy
from bpy.app.handlers import persistent

from .. import debug as _debug
from .. import overlay as _overlay
from ..core import session as _session
from . import ops as _ops
from . import props as _props
from .panel import TSUNFOLD_PT_main

_draw_handle = None
_pattern_draw_handle = None
_pattern_text_handle = None






# PNG helpers


# Smooth finishing-line helpers

SMOOTH_SUFFIX = "_なめらか線"


# Annotation -> rough seam helpers

# Annotation -> clean curve -> knife helpers

# Experimental free-seam helpers

# Operators


# 3D pattern annotation system

# 注記の保存先は truescale.marking.storage が持つ。


_PATTERN_FLAT_MEMO_PROP = "tsunfold_flat_memos_json"

# Runtime-only memo edit state.
_pattern_selected_memo = {
    "unfold": "",
    "index": -1,
}


_DIGIT_5X7 = {
    "0": ("01110","10001","10011","10101","11001","10001","01110"),
    "1": ("00100","01100","00100","00100","00100","00100","01110"),
    "2": ("01110","10001","00001","00010","00100","01000","11111"),
    "3": ("11110","00001","00001","01110","00001","00001","11110"),
    "4": ("00010","00110","01010","10010","11111","00010","00010"),
    "5": ("11111","10000","10000","11110","00001","00001","11110"),
    "6": ("01110","10000","10000","11110","10001","10001","01110"),
    "7": ("11111","00001","00010","00100","01000","01000","01000"),
    "8": ("01110","10001","10001","01110","10001","10001","01110"),
    "9": ("01110","10001","10001","01111","00001","00001","01110"),
}


# UI


# 登録するクラス。オペレータは ops/ が機能別に持っている。
# パネルは最後。先にオペレータが登録されていないと、パネルの
# 参照先が無い状態になる。
classes = _ops.classes + (
    TSUNFOLD_PT_main,
)


# @persistent が要る。付けないと、Blender はファイルを読み込む
# ときにこのハンドラを一覧から外す。初期化が事実上1回しか
# 走らず、2つ目のファイルを開くと前の作業状態が残る。
@persistent
def _tsunfold_reset_overlays_on_load(_dummy=None):
    # Runs only after a .blend has loaded, when bpy.data is available.
    try:
        scenes = bpy.data.scenes
    except Exception:
        return

    for scene in scenes:
        try:
            scene.tsunfold_show_paper = False
        except Exception:
            _debug.swallowed("unfold._tsunfold_reset_overlays_on_load")
        try:
            scene.tsunfold_preview = False
        except Exception:
            _debug.swallowed("unfold._tsunfold_reset_overlays_on_load")
        try:
            # 作業状態のキーは session が一覧を持っている。ここで
            # 書き写すと、足したキーが片方にだけ入って食い違う。
            # 実際、手動レイアウト中かどうかのキーが漏れていて、
            # その状態で保存すると開き直しても解除されなかった。
            # 解除されないと注記が一切描かれない。
            _session.reset_on_load(scene)

            scene.tsunfold_active_tool = "NONE"
            scene.tsunfold_correspondence_mode = False
            scene.tsunfold_auto_island_ids = True
            scene.tsunfold_island_id_style = "ALPHA"
            scene.tsunfold_arrow_mode = "AUTO"
            scene.tsunfold_arrow_up_axis = "Z"
            scene.tsunfold_number_start = 1
            scene.tsunfold_next_number = 1
            scene.tsunfold_notch_mode = "AUTO"
            scene.tsunfold_auto_notch_divisions = "3"
            scene.tsunfold_pattern_preview = False

            # 退避は session の初期化対象に入れていない（解除時の
            # 戻し先が消えるため）。ファイルを開いた直後は前回の
            # 退避が意味を持たないので、ここで空にする。
            scene[_session.PREVIEW_SOURCE_NAME] = ""
            scene[_session.PREVIEW_SOURCE_HIDE_GET] = False
            scene[_session.PREVIEW_SOURCE_HIDE_VIEWPORT] = False
        except Exception:
            _debug.swallowed("unfold._tsunfold_reset_overlays_on_load")


def register():
    """クラス・設定・描画ハンドラを登録する。"""
    global _draw_handle, _pattern_draw_handle, _pattern_text_handle

    for cls in classes:
        bpy.utils.register_class(cls)

    _props.register()


    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _overlay.draw_paper_guide, (), 'WINDOW', 'POST_VIEW'
        )

    if _pattern_draw_handle is None:
        _pattern_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _overlay.draw_marks_3d, (), 'WINDOW', 'POST_VIEW'
        )

    if _pattern_text_handle is None:
        _pattern_text_handle = bpy.types.SpaceView3D.draw_handler_add(
            _overlay.draw_text_2d, (), 'WINDOW', 'POST_PIXEL'
        )

    if _tsunfold_reset_overlays_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_tsunfold_reset_overlays_on_load)


def unregister():

    if _tsunfold_reset_overlays_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_tsunfold_reset_overlays_on_load)
    global _draw_handle, _pattern_draw_handle, _pattern_text_handle

    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            _debug.swallowed("unfold.unregister")
        _draw_handle = None

    if _pattern_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _pattern_draw_handle, 'WINDOW'
            )
        except Exception:
            _debug.swallowed("unfold.unregister")
        _pattern_draw_handle = None

    if _pattern_text_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _pattern_text_handle, 'WINDOW'
            )
        except Exception:
            _debug.swallowed("unfold.unregister")
        _pattern_text_handle = None

    _props.unregister()

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
