"""UIに出す設定（Scene プロパティ）の定義。

パネルに並ぶ数値・色・切り替えは全てここで作る。

■ 更新時に何をするか

値を変えたあと、画面へ反映するには2種類の道がある。

  再描画だけ … キャッシュのキーに入っている設定、または
               描画のたびにシーンから読み直している設定
  キャッシュ破棄 … 上に当てはまらない設定

破棄は epoch を進めて全キャッシュを捨てるので、島の解析も
ID配置の探索も矢印の配置探索もまとめて作り直しになる。
スライダーをドラッグすると毎フレームそれが走る。数値を少し
変えるだけで重くなっていた原因がこれだった。

迷ったら破棄のほうが安全に見えるが、そちらを選ぶと重い。
キーに入れたうえで再描画だけにするのが正しい。

■ 削除は接頭辞でまとめて

以前は削除対象を手書きで並べていて、足したプロパティが
追従されず消し残しが出た。接頭辞で特定すれば、定義と削除が
ずれようがない。
"""

import traceback

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    StringProperty,
)

from .. import debug as _debug
from ..core import objects as _objects
from ..core import session as _session
from ..core import view as _view
from ..marking import auto_notch as _auto_notch
from ..marking import interact as _interact
from . import build as _build


def _pattern_setting_updated(self, context):
    _interact.invalidate_layout_cache()


def _pattern_notch_source_for_update(context):
    """合印設定の更新対象になる元モデルを返す。無ければ None。"""
    scene = context.scene
    if str(getattr(scene, "tsunfold_notch_mode", "AUTO")) != "AUTO":
        return None

    source = _objects.seam_source(context)
    if source is None:
        source = _objects.source_from_context(context)

    if source is None or source.type != 'MESH':
        return None

    return source


def _pattern_notch_divisions_updated(self, context):
    """分割数の変更。合印の位置が変わるので作り直す必要がある。

    アノテーションに保存しているのは type / edge / t / color / auto だけで、
    位置を決めるのは t（辺上の比率）。分割数が変わると t が変わるため、
    ここだけはシームを辿り直して作り直す。
    """
    try:
        source = _pattern_notch_source_for_update(context)
        if source is not None:
            _auto_notch.refresh(context, source)
    except Exception:
        # プロパティのコールバックでUI操作を壊さない。
        # ただし内容は握り潰さずに出す。
        traceback.print_exc()

    _interact.invalidate_layout_cache()


def _pattern_redraw_only_updated(self, context):
    """再描画するだけでよい設定の更新。

    以下のどちらかに当てはまる設定は、キャッシュを捨てる必要がない。

      1. すでに描画キャッシュのキーに含まれている設定
         （値を変えれば別のキーになるので、古い結果は自然に使われない）
      2. キャッシュを通さず、描画のたびにシーンから読み直している設定

    _interact.invalidate_layout_cache() は epoch を進めて全キャッシュを
    破棄するため、スライダーをドラッグすると1フレームごとに
    島の解析・ID配置探索・矢印配置探索がまとめて作り直されていた。
    数値を少し変えるだけで重くなっていた原因。
    """
    _view.tag_redraw()


def _paper_setting_updated(self, context):
    _view.tag_redraw()


def _preview_setting_updated(self, context):
    _view.tag_redraw()


def _spacing_updated(self, context):
    """Realtime repack when spacing changes."""
    obj = _objects.active_unfold(context)
    if obj is None:
        return

    # Editing geometry while in edit mode needs an object-mode data refresh.
    was_edit = (obj.mode == 'EDIT')
    if was_edit:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            return

    _build.pack_islands(context, obj, context.scene.tsunfold_spacing_mm)

    if was_edit:
        try:
            bpy.ops.object.mode_set(mode='EDIT')
        except RuntimeError:
            _debug.swallowed("unfold.props._spacing_updated")

    _view.tag_redraw()


def _unregister_scene_props():
    """このアドオンが register() で作った Scene プロパティを全て削除する。

    以前は削除対象を手書きのタプルで列挙していたが、register() 側に
    プロパティを足したときに追従されず、消し残しが発生していた。
    列挙をやめ、接頭辞で特定することで register() と必ず一致させる。
    """
    prefix = "tsunfold_"
    for name in [n for n in dir(bpy.types.Scene) if n.startswith(prefix)]:
        try:
            delattr(bpy.types.Scene, name)
        except Exception:
            # 1つ失敗しても残りの削除は続ける。内容は握り潰さず出す。
            traceback.print_exc()


def register():
    """UIに出す設定を作る。"""
    # --- パネルの折りたたみ ---
    #
    # 必須の4段は常に開いておき、それ以外は畳む。以前はマーキングが
    # パネルの半分を占め、必須の道を分断していた。
    #
    # 開閉はシーンに持つ。ファイルを開き直しても、その人が開いて
    # いた区画が開いたままになる。

    bpy.types.Scene.tsunfold_show_scale_setup = BoolProperty(
        name="実寸の前提を開く",
        description="1 Blender Unit を何ミリとして扱うかの設定",
        default=False,
    )

    bpy.types.Scene.tsunfold_show_marking = BoolProperty(
        name="印をつけるを開く",
        description="合印・番号・文字・型紙ID・矢印",
        default=False,
    )

    bpy.types.Scene.tsunfold_show_allowance = BoolProperty(
        name="縫い代・糊代",
        default=False,
        options={'HIDDEN'},
    )

    bpy.types.Scene.tsunfold_show_correspondence = BoolProperty(
        name="対応を確かめるを開く",
        description="平面のどこが立体のどこだったかの確認と、型紙へのメモ",
        default=False,
    )

    bpy.types.Scene.tsunfold_export_format = EnumProperty(
        name="形式",
        description="書き出すファイルの形式",
        items=[
            (
                "PDF",
                "PDF",
                "分割しても1つのファイルにまとまる。ページの大きさを"
                "実寸で持つので、原寸で刷りやすい",
            ),
            (
                "PNG",
                "PNG",
                "画像。他のソフトへ持ち込むとき。分割すると枚数分の"
                "ファイルになる",
            ),
        ],
        default="PDF",
    )

    # unit='LENGTH' を付けてはいけない。付けると Blender が中身を
    # シーンの長さ単位（既定はメートル）として表示するので、8 が
    # 「8 m」と出る。実際にはミリとして使っている値なので、画面の
    # 表示だけが嘘になる。名前と説明にミリと書いておく。
    bpy.types.Scene.tsunfold_tile_margin_mm = FloatProperty(
        name="用紙の余白 (mm)",
        description=(
            "プリンタが刷れない縁の幅（ミリ）。機種によって違うので"
            "多めに取る。足りないと、端の線が切れる"
        ),
        default=8.0,
        min=0.0,
        max=40.0,
        precision=1,
    )

    bpy.types.Scene.tsunfold_pattern_inset_mm = FloatProperty(
        name="型紙のまわりの余白 (mm)",
        description=(
            "型紙の外形と、用紙ガイドの枠との間に空ける幅（ミリ）。"
            "切るときに鋏が入る余地になる。糊代が継ぎ目の上に"
            "乗るのも避けられる。枚数が増えることがある"
        ),
        default=0.0,
        min=0.0,
        max=50.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_tile_overlap_mm = FloatProperty(
        name="重ねしろ (mm)",
        description=(
            "隣の紙と重ねる幅（ミリ）。切らずに重ねて貼るためのもの。"
            "広くしても枚数はほとんど変わらないので、貼りやすさで決める"
        ),
        default=15.0,
        min=0.0,
        max=60.0,
        precision=1,
    )

    bpy.types.Scene.tsunfold_scale_mode = EnumProperty(
        name="実寸の基準",
        description="1 Blender Unit を何ミリとして扱うかの決め方",
        items=[
            (
                "SCENE",
                "シーンに従う",
                "Scene の Unit Scale をそのまま使う",
            ),
            (
                "MANUAL",
                "このアドオンで指定",
                "シーンの Unit Scale を使わず、下の値で換算する",
            ),
        ],
        default="SCENE",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_manual_mm_per_bu = FloatProperty(
        name="1 BU の長さ (mm)",
        description=(
            "「このアドオンで指定」のときに使う換算値。"
            "シーンの Unit Scale は変更しません"
        ),
        default=1000.0,
        min=0.000001,
        soft_min=0.01,
        soft_max=10000.0,
        precision=4,
        update=_pattern_setting_updated,
    )

    # シーム化の対称オプション。
    # _apply_selected_edges_seam_strict_symmetry と
    # _sync_blender_mesh_symmetry が参照するが、これまで register されて
    # おらず、getattr の既定値で常に False に落ちていた。
    bpy.types.Scene.tsunfold_seam_symmetry_x = BoolProperty(
        name="X対称",
        description="シーム化するとき、X軸で対称な位置のエッジも一緒に処理します",
        default=False,
    )

    bpy.types.Scene.tsunfold_seam_symmetry_y = BoolProperty(
        name="Y対称",
        description="シーム化するとき、Y軸で対称な位置のエッジも一緒に処理します",
        default=False,
    )

    bpy.types.Scene.tsunfold_seam_symmetry_z = BoolProperty(
        name="Z対称",
        description="シーム化するとき、Z軸で対称な位置のエッジも一緒に処理します",
        default=False,
    )

    bpy.types.Scene.tsunfold_spacing_mm = FloatProperty(
        name="アイランド間隔",
        description="展開後のアイランド同士の間隔。変更するとリアルタイムで再配置します",
        default=10.0,
        min=0.0,
        soft_max=100.0,
        precision=1,
        update=_spacing_updated,
    )

    bpy.types.Scene.tsunfold_paper_size = EnumProperty(
        name="用紙サイズ",
        description="用紙ガイドとPNGの用紙サイズ",
        items=[
            ("A5", "A5", "148 × 210 mm"),
            ("A4", "A4", "210 × 297 mm"),
            ("A3", "A3", "297 × 420 mm"),
            ("A2", "A2", "420 × 594 mm"),
            ("A1", "A1", "594 × 841 mm"),
            ("A0", "A0", "841 × 1189 mm"),
            ("B5", "B5", "182 × 257 mm"),
            ("B4", "B4", "257 × 364 mm"),
            ("CUSTOM", "カスタム", "幅と高さをmmで指定"),
        ],
        default="A4",
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_custom_paper_width_mm = FloatProperty(
        name="カスタム用紙 幅",
        description="カスタム用紙の横幅をmmで指定します",
        default=600.0,
        min=1.0,
        soft_max=3000.0,
        precision=1,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_custom_paper_height_mm = FloatProperty(
        name="カスタム用紙 高さ",
        description="カスタム用紙の高さをmmで指定します",
        default=900.0,
        min=1.0,
        soft_max=3000.0,
        precision=1,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_orientation = EnumProperty(
        name="用紙の向き",
        description="用紙の向き",
        items=[
            ("AUTO", "自動", "表示ガイドは縦。PNG書き出し時は収まりやすい向きを自動選択"),
            ("PORTRAIT", "縦", "縦向き"),
            ("LANDSCAPE", "横", "横向き"),
        ],
        default="PORTRAIT",
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_pattern_preview = BoolProperty(
        name="型紙プレビュー",
        description="現在の型紙状態をViewportに表示します",
        default=False,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_notch_mode = EnumProperty(
        name="合印方式",
        description="合印の作り方",
        items=[
            (
                "AUTO",
                "オート",
                "分割数に応じてシーム全長へ自動配置します",
            ),
            (
                "NONE",
                "なし",
                "合印を作りません",
            ),
            (
                "CUSTOM",
                "カスタム",
                "クリックした位置だけに合印を追加します",
            ),
        ],
        default="AUTO",
    )

    bpy.types.Scene.tsunfold_auto_notch_divisions = EnumProperty(
        name="分割数",
        description="2なら中央1個、3なら1/3と2/3、4なら1/4・1/2・3/4",
        items=[
            ("2", "2", "中央に1個"),
            ("3", "3", "1/3と2/3に配置"),
            ("4", "4", "1/4・1/2・3/4に配置"),
        ],
        default="3",
        update=_pattern_notch_divisions_updated,
    )

    bpy.types.Scene.tsunfold_show_direction_arrow = BoolProperty(
        name="水色の方向ガイド",
        default=False,
        update=_pattern_redraw_only_updated,
    )


    bpy.types.Scene.tsunfold_correspondence_mode = BoolProperty(
        name="対応確認",
        default=False,
        options={'HIDDEN'},
    )

    # --- 縫い代と糊代 -------------------------------------------------
    #
    # 2つに分けてあるのは、形も付く側も違うため。縫い代は輪郭全体を
    # 外へ広げ、両方の型紙に付く。糊代は辺ごとの台形で、片側にしか
    # 付かない。1つの設定にまとめると、どちらかが必ず狂う。
    #
    # 幅がミリなのは、型紙を必ず実寸で刷るから。紙の上のミリと
    # 実物のミリが同じ値になるので、換算は要らない。

    bpy.types.Scene.tsunfold_tab_enable = BoolProperty(
        name="糊代（のりしろ）",
        description=(
            "貼り合わせる辺に、折って糊を付けるための台形を出します。"
            "シームの辺すべてに付き、相手のいない辺には付きません"
        ),
        default=False,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_tab_width_mm = FloatProperty(
        name="糊代の幅 (mm)",
        description=(
            "紙の上での幅。置けない辺では自動で細くし、"
            "それでも入らなければその辺には付けません"
        ),
        default=6.0,
        min=1.0,
        soft_max=20.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_seam_enable = BoolProperty(
        name="縫い代",
        description=(
            "輪郭の外側へ一定量ひろげた裁断線を出します。"
            "元の輪郭は縫い線として破線になります"
        ),
        default=False,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_seam_width_mm = FloatProperty(
        name="縫い代の幅 (mm)",
        description="輪郭から外側へひろげる量",
        default=10.0,
        min=0.5,
        soft_max=50.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_auto_island_ids = BoolProperty(
        name="自動型紙ID",
        default=True,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_island_id_style = EnumProperty(
        name="型紙ID形式",
        items=[
            ("ALPHA", "A / B / C", "アイランドをアルファベットで表示"),
            ("NUMBER", "1 / 2 / 3", "アイランドを数字で表示"),
        ],
        default="ALPHA",
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_island_id_size_mm = FloatProperty(
        name="型紙IDサイズ",
        default=8.0,
        min=3.0,
        max=30.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_island_id_color = FloatVectorProperty(
        name="型紙ID色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_mode = EnumProperty(
        name="矢印方式",
        items=[
            ("AUTO", "オート", "各アイランドへ上方向矢印を自動配置"),
            ("NONE", "なし", "矢印を表示しない"),
            ("CUSTOM", "カスタム", "手動で矢印を配置"),
        ],
        default="AUTO",
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_up_axis = EnumProperty(
        name="上方向",
        description="Blender右上のXYZギズモと同じグローバル軸を使用します",
        items=[
            ("Z", "Z+", "BlenderグローバルZ+"),
            ("Y", "Y+", "BlenderグローバルY+"),
            ("X", "X+", "BlenderグローバルX+"),
        ],
        default="Z",
        update=_pattern_redraw_only_updated,
    )



    bpy.types.Scene.tsunfold_auto_arrow_length_mm = FloatProperty(
        name="オート矢印長さ",
        default=24.0,
        min=5.0,
        max=100.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_active_tool = StringProperty(
        name="マーキングツール",
        default="NONE",
        options={'HIDDEN'},
    )

    bpy.types.Scene.tsunfold_notch_color = FloatVectorProperty(
        name="合印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_number_color = FloatVectorProperty(
        name="数字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_text_color = FloatVectorProperty(
        name="文字の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_color = FloatVectorProperty(
        name="矢印の色",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0),
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_notch_length_mm = FloatProperty(
        name="合印長さ",
        default=6.0,
        min=1.0,
        max=30.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_notch_thickness_mm = FloatProperty(
        name="合印の太さ",
        description="合印線の表示・出力時の太さ",
        default=0.6,
        min=0.1,
        soft_max=3.0,
        precision=2,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_number_size_mm = FloatProperty(
        name="数字サイズ",
        default=8.0,
        min=2.0,
        max=40.0,
        precision=1,
    )

    bpy.types.Scene.tsunfold_text_size_mm = FloatProperty(
        name="文字サイズ",
        default=8.0,
        min=2.0,
        max=60.0,
        precision=1,
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_custom_text = StringProperty(
        name="型紙名 / 任意テキスト",
        default="",
        update=_pattern_setting_updated,
    )

    bpy.types.Scene.tsunfold_number_start = bpy.props.IntProperty(
        name="開始番号",
        description="型紙番号モードをONにした時に最初に入る番号",
        default=1,
        min=1,
        max=9999,
    )

    bpy.types.Scene.tsunfold_arrow_head_mm = FloatProperty(
        name="矢印先端サイズ",
        default=8.0,
        min=2.0,
        max=30.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_arrow_thickness_mm = FloatProperty(
        name="矢印線の太さ",
        default=0.8,
        min=0.2,
        max=5.0,
        precision=1,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_next_number = bpy.props.IntProperty(
        name="次の型紙番号",
        description="型紙番号ツールが次に置く番号",
        default=1,
        min=1,
        max=9999,
    )

    bpy.types.Scene.tsunfold_show_paper = BoolProperty(
        name="用紙枠を表示",
        description="3Dビューに実寸用紙枠を表示します。オブジェクトは作りません",
        default=False,
        update=_paper_setting_updated,
    )

    bpy.types.Scene.tsunfold_lightweight_view = BoolProperty(
        name="軽量ビュー",
        description="元モデル側の補助マーキング描画を減らして3Dビュー操作を軽くします",
        default=True,
        update=_pattern_redraw_only_updated,
    )

    bpy.types.Scene.tsunfold_preview = BoolProperty(
        name="印刷プレビュー",
        description="最終PNGに近い白紙＋黒外周線を3Dビューに表示します",
        default=False,
        update=_preview_setting_updated,
    )


def unregister():
    """作った設定を全て消す。"""
    _unregister_scene_props()
