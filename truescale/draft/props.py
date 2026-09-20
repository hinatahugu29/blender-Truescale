"""UIに出す設定（Scene プロパティ）。

面図ごとの寸法の表示／非表示、用紙、縮尺、背景など。

■ 開くたびに既定へ戻す

作図の設定は「その作業のためのもの」で、ファイルに残しても
次の作業では邪魔になる。特に表示の上書きが残ると、開いた人は
なぜそう見えているのか分からない。読み込み後に既定へ戻す。

戻すのは Blender の準備が済んでからでないと触れないので、
タイマーで少し待ってから行う。

■ 削除は接頭辞でまとめて

列挙して消す形だと、足したプロパティが追従されず消し残る。
接頭辞で特定すれば、定義と削除がずれようがない。
"""

import traceback

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from .. import debug as _debug
from ..core import paper as _paper
from . import bbox as _bbox
from . import keys as _keys
from . import views as _views
from . import viewstate as _viewstate


def tsdraft_reset_scene_settings_to_defaults(scene):
    """
    Remove persisted addon setting values from the Scene.
    Registered bpy.props defaults then become active again.
    Objects/BBox themselves are not deleted.
    """
    try:
        keys = list(scene.keys())
    except Exception:
        return

    for key in keys:
        if isinstance(key, str) and key.startswith("tsdraft_"):
            try:
                del scene[key]
            except Exception:
                _debug.swallowed("draft.tsdraft_reset_scene_settings_to_defaults")


def tsdraft_reset_all_scenes_to_defaults():
    # During add-on registration Blender may expose _RestrictData,
    # which has no .scenes attribute yet.
    if not hasattr(bpy.data, "scenes"):
        return False

    for scene in bpy.data.scenes:
        tsdraft_reset_scene_settings_to_defaults(scene)

    ns = bpy.app.driver_namespace
    ns[_keys.AUTO_FOLLOW_SIGNATURE_KEY] = None
    return True


def _unregister_scene_props():
    """このアドオンが register() で作った Scene プロパティを全て削除する。

    以前は削除対象を手書きのタプルで列挙していたが、register() 側に
    プロパティを足したときに追従されず、消し残しが発生していた。
    列挙をやめ、接頭辞で特定することで register() と必ず一致させる。
    """
    prefix = "tsdraft_"
    for name in [n for n in dir(bpy.types.Scene) if n.startswith(prefix)]:
        try:
            delattr(bpy.types.Scene, name)
        except Exception:
            # 1つ失敗しても残りの削除は続ける。内容は握り潰さず出す。
            traceback.print_exc()


def register():
    """UIに出す設定を作る。"""
    bpy.types.Scene.tsdraft_auto_follow = bpy.props.BoolProperty(
        name="自動追従",
        description="元オブジェクトの形状・変形に合わせてBounding Boxと寸法を自動更新",
        default=True,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_font_size = bpy.props.IntProperty(
        name="文字サイズ",
        default=30,
        min=10,
        max=200,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_font_color = bpy.props.FloatVectorProperty(
        name="文字色",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_front_x_offset_x_mm = bpy.props.FloatProperty(name="前面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_x_offset_y_mm = bpy.props.FloatProperty(name="前面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_y_offset_x_mm = bpy.props.FloatProperty(name="前面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_y_offset_y_mm = bpy.props.FloatProperty(name="前面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_z_offset_x_mm = bpy.props.FloatProperty(name="前面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_front_z_offset_y_mm = bpy.props.FloatProperty(name="前面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_x_offset_x_mm = bpy.props.FloatProperty(name="上面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_x_offset_y_mm = bpy.props.FloatProperty(name="上面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_y_offset_x_mm = bpy.props.FloatProperty(name="上面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_y_offset_y_mm = bpy.props.FloatProperty(name="上面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_z_offset_x_mm = bpy.props.FloatProperty(name="上面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_top_z_offset_y_mm = bpy.props.FloatProperty(name="上面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_x_offset_x_mm = bpy.props.FloatProperty(name="側面 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_x_offset_y_mm = bpy.props.FloatProperty(name="側面 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_y_offset_x_mm = bpy.props.FloatProperty(name="側面 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_y_offset_y_mm = bpy.props.FloatProperty(name="側面 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_z_offset_x_mm = bpy.props.FloatProperty(name="側面 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_side_z_offset_y_mm = bpy.props.FloatProperty(name="側面 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_x_offset_x_mm = bpy.props.FloatProperty(name="任意 X 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_x_offset_y_mm = bpy.props.FloatProperty(name="任意 X 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_y_offset_x_mm = bpy.props.FloatProperty(name="任意 Y 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_y_offset_y_mm = bpy.props.FloatProperty(name="任意 Y 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_z_offset_x_mm = bpy.props.FloatProperty(name="任意 Z 左右(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)
    bpy.types.Scene.tsdraft_user_z_offset_y_mm = bpy.props.FloatProperty(name="任意 Z 上下(mm)", default=0.0, min=-1000.0, max=1000.0, precision=2, update=_bbox.redraw_viewports)



    bpy.types.Scene.tsdraft_show_bbox = bpy.props.BoolProperty(
        name="BOXを表示",
        default=True,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_frame_mode = bpy.props.EnumProperty(
        name="枠表示",
        description="サイズ枠の表示方法",
        items=(
            ('BOX', "BOX", "外接BOXを表示"),
            ('LINES', "寸法線のみ", "表示中の寸法に対応する線だけ表示"),
            ('NONE', "なし", "枠線を表示しない"),
        ),
        default='BOX',
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_frame_color = bpy.props.FloatVectorProperty(
        name="枠線色",
        subtype='COLOR',
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_frame_width = bpy.props.FloatProperty(
        name="枠線の太さ",
        default=1.5,
        min=1.0,
        max=8.0,
        precision=1,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_bbox_top = bpy.props.BoolProperty(
        name="上面 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_front = bpy.props.BoolProperty(
        name="前面 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_side = bpy.props.BoolProperty(
        name="側面 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_bbox_user = bpy.props.BoolProperty(
        name="任意 BOX表示",
        default=False,
        update=_bbox.update_bbox_visibility
    )

    bpy.types.Scene.tsdraft_show_dimensions = bpy.props.BoolProperty(
        name="寸法を表示",
        default=True,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_dimension_unit = bpy.props.EnumProperty(
        name="単位",
        description="寸法表示に使う単位",
        items=(
            ('MM', "mm", "ミリメートル"),
            ('CM', "cm", "センチメートル"),
            ('M', "m", "メートル"),
        ),
        default='MM',
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_top = bpy.props.BoolProperty(
        name="上面 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_front = bpy.props.BoolProperty(
        name="前面 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_side = bpy.props.BoolProperty(
        name="側面 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    bpy.types.Scene.tsdraft_show_dimensions_user = bpy.props.BoolProperty(
        name="任意 寸法表示",
        default=False,
        update=_bbox.redraw_viewports
    )

    # 旧ファイル互換用。UIではtsdraft_drawing_backgroundを使用。
    bpy.types.Scene.tsdraft_show_grid = bpy.props.BoolProperty(
        name="グリッド表示（旧）",
        default=False
    )

    background_items = (
        ('WHITE', "白", "白背景"),
        ('GRID', "グリッド", "白背景にBlenderグリッドを表示"),
        ('BLACK', "黒", "黒背景"),
        ('CUSTOM', "カスタム", "好きな背景色を指定"),
    )

    bpy.types.Scene.tsdraft_drawing_background = bpy.props.EnumProperty(
        name="図面ビュー背景",
        description="図面ビューの背景表示",
        items=background_items,
        default='WHITE',
        update=_views.update_drawing_background
    )

    bpy.types.Scene.tsdraft_drawing_background_color = bpy.props.FloatVectorProperty(
        name="図面ビューのカスタム背景色",
        subtype='COLOR',
        size=3,
        default=(0.18, 0.18, 0.18),
        min=0.0,
        max=1.0,
        update=_views.update_drawing_background
    )

    bpy.types.Scene.tsdraft_export_background = bpy.props.EnumProperty(
        name="書き出し背景",
        description="実寸PNGを書き出す時の背景表示。軸・原点・3Dカーソルは常に非表示です",
        items=background_items,
        default='WHITE'
    )

    bpy.types.Scene.tsdraft_export_background_color = bpy.props.FloatVectorProperty(
        name="書き出しのカスタム背景色",
        subtype='COLOR',
        size=3,
        default=(0.18, 0.18, 0.18),
        min=0.0,
        max=1.0
    )

    # 一覧は core.paper の表から作る。以前はここに手で並べていて、
    # A5 と B判が抜けていた。同じ「用紙サイズ」という名前なのに、
    # 型紙側と三面図側で選べるものが違っていた。
    bpy.types.Scene.tsdraft_sheet_paper_size = bpy.props.EnumProperty(
        name="用紙サイズ",
        items=_paper.enum_items(),
        default='A4'
    )

    bpy.types.Scene.tsdraft_sheet_custom_width_mm = bpy.props.FloatProperty(
        name="カスタム幅",
        description="カスタム用紙の幅(mm)",
        default=210.0,
        min=10.0,
        soft_max=3000.0,
        precision=1
    )

    bpy.types.Scene.tsdraft_sheet_custom_height_mm = bpy.props.FloatProperty(
        name="カスタム高さ",
        description="カスタム用紙の高さ(mm)",
        default=297.0,
        min=10.0,
        soft_max=3000.0,
        precision=1
    )

    bpy.types.Scene.tsdraft_sheet_orientation = bpy.props.EnumProperty(
        name="用紙の向き",
        items=(
            ('AUTO', "自動", "収まる向きを自動選択"),
            ('PORTRAIT', "縦", "縦向き"),
            ('LANDSCAPE', "横", "横向き"),
        ),
        default='AUTO'
    )

    bpy.types.Scene.tsdraft_sheet_scale = bpy.props.EnumProperty(
        name="縮尺",
        items=(
            ('1_1', "1:1", "原寸"),
            ('1_2', "1:2", "50%"),
            ('1_5', "1:5", "20%"),
            ('1_10', "1:10", "10%"),
            ('CUSTOM', "任意", "任意の縮尺"),
        ),
        default='1_1'
    )

    bpy.types.Scene.tsdraft_sheet_custom_scale = bpy.props.FloatProperty(
        name="任意縮尺",
        description="1:N の N を指定します",
        default=2.0,
        min=1.0,
        soft_max=100.0,
        precision=2
    )

    bpy.types.Scene.tsdraft_show_dimension_adjustments = bpy.props.BoolProperty(
        name="寸法位置の微調整",
        default=False
    )

    bpy.types.Scene.tsdraft_drawing_mode = bpy.props.BoolProperty(
        name="図面モード",
        default=False
    )

    bpy.types.Scene.tsdraft_user_view_mode = bpy.props.BoolProperty(
        name="任意ビューモード",
        default=False
    )

    bpy.types.Scene.tsdraft_dark_place = bpy.props.BoolProperty(
        name="暗所表示",
        default=False,
        update=_bbox.redraw_viewports
    )


def unregister():
    """作った設定を全て消す。"""
    _unregister_scene_props()
