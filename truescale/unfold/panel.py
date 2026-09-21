"""サイドバーのパネル。

描くだけで、押した先の処理はオペレータが持つ。ここに判断を書くと、
同じことをオペレータ側でも書くことになり、片方だけ直す事故が起きる。

■ 必須の道を4段に絞り、任意のものは畳む

  前提（畳む）  実寸の基準。最初に一度決めたら触らない
  道案内        いまどこにいて、次に何を押すか
  1 読み込む
  2 型紙にする
  3 紙に並べる
  4 出す / 残す
  ── ここから任意 ──
  印をつける（畳む）
  対応を確かめる・メモ（畳む）

以前はマーキングが「2/4」に置かれ、パネル全体の半分を占めていた。
番号が付いていると必須に読めるが、型紙を作って刷るだけなら要らない。
必須の道が、任意の作業で分断されていた。

段の名前は動詞で揃える。「モデルと型紙」のような名詞だと、そこで
何をするのかが読み取れない。

■ 紙に並べるのが、印をつけるより前

用紙より大きい型紙は分割して刷る。継ぎ目の位置が決まっていないと、
合印が継ぎ目に乗ってしまう。分割が無かった頃は用紙が最後の確認
だったので、この順序は当時の名残だった。

■ 道案内と、動いている道具は必ず見える場所へ

畳んだ中に隠すと、道具が動いていることに気付けない。止め方も
そこへ置く。

■ 状態によって出し分ける

型紙ができていない段階でレイアウトや書き出しを出しても押せない。
押せないものは薄くするのではなく、出さないか理由を添える。

■ スケールの警告は畳まない

それが出ている時点で結果が間違っている可能性がある。折りたたみの
中に入れると気付かれない。
"""

import bpy

from ..core import objects as _objects
from ..core import paper as _paper
from ..core import session as _session
from ..core import units as _units
from . import build as _build
from . import status as _status


def _manual_layout_active(scene):
    """手動レイアウト中か。中は編集用のボタンだけを出す。"""
    from .. import overlay
    return overlay.manual_layout_active(scene)


def _folded(layout, scene, prop, text, icon='NONE'):
    """畳める区画。(中身を入れる場所, 開いているか) を返す。

    見出しは常に出す。中身を隠しても、そこに何があるかは分かる
    ようにしておかないと、機能ごと見えなくなる。
    """
    box = layout.box()
    header = box.row(align=True)
    open_now = bool(getattr(scene, prop, False))
    header.prop(
        scene,
        prop,
        text="",
        icon='TRIA_DOWN' if open_now else 'TRIA_RIGHT',
        emboss=False,
    )
    header.label(text=text, icon=icon)
    return box, open_now


def _step(layout, number, text, icon='NONE'):
    """番号付きの段。必須の道なので、常に開いている。"""
    box = layout.box()
    head = box.row()
    head.scale_y = 1.1
    head.label(text=f"{number}. {text}", icon=icon)
    return box


class TSUNFOLD_PT_main(bpy.types.Panel):
    bl_label = "Truescale Unfold"
    bl_idname = "TSUNFOLD_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Truescale"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        unfold_obj = _objects.resolve_unfold_for_layout(context)

        loaded_name = scene.get(_session.SEAM_SOURCE, "")
        loaded_obj = bpy.data.objects.get(loaded_name) if loaded_name else None

        self._draw_setup(layout, context, scene)
        self._draw_status(layout, context, unfold_obj)

        self._draw_load(layout, context, scene)
        self._draw_build(layout, context, scene, loaded_obj, unfold_obj)
        self._draw_allowance(layout, context, scene)
        self._draw_layout(layout, context, scene, unfold_obj)
        self._draw_output(layout, context, scene, unfold_obj)

        layout.separator()

        self._draw_marking(layout, context, scene)
        self._draw_correspondence(layout, context, scene)

        info = layout.box()
        info.label(text="Truescale Unfold Beta v1.5.6")

    # ------------------------------------------------------------
    # 前提と道案内
    # ------------------------------------------------------------

    def _draw_setup(self, layout, context, scene):
        """実寸の基準。最初に一度決めたら触らないので畳んでおく。"""
        box, open_now = _folded(
            layout, scene, "tsunfold_show_scale_setup",
            "実寸の前提", 'DRIVER_DISTANCE',
        )

        scene_scale, mm_per_bu = _units.scene_unit_summary(scene)

        if not open_now:
            # 畳んでいても、いまの基準だけは見えるようにする。
            # これが分からないと、出てくる寸法の意味が決まらない。
            box.label(text=f"1 BU = {mm_per_bu:g} mm")
            return

        box.prop(scene, "tsunfold_scale_mode", text="")

        if scene.tsunfold_scale_mode == "MANUAL":
            box.prop(scene, "tsunfold_manual_mm_per_bu", text="1 BU =")
            # シーン側の値も併記する。どちらが使われているかを明確にするため。
            box.label(
                text=(
                    f"シーンの Unit Scale "
                    f"{_units.scene_unit_scale_raw(scene):g} は未使用"
                ),
                icon='INFO',
            )
        else:
            box.label(
                text=f"Unit Scale {scene_scale:g} / 1 BU = {mm_per_bu:g} mm"
            )

        if context.mode == 'EDIT_MESH':
            box.operator(
                "truescale_unfold.calibrate_scale",
                text="選択した辺を基準に決める",
                icon='DRIVER_DISTANCE',
            )

        active_source = _objects.seam_source(context)
        if active_source is None:
            return

        sx = float(active_source.scale.x)
        sy = float(active_source.scale.y)
        sz = float(active_source.scale.z)
        box.label(text=f"Object Scale: {sx:g}, {sy:g}, {sz:g}")

        if (
            abs(sx - 1.0) > 1.0e-6
            or abs(sy - 1.0) > 1.0e-6
            or abs(sz - 1.0) > 1.0e-6
        ):
            box.label(text="未適用Scaleもワールド寸法として反映", icon='INFO')

    def _draw_status(self, layout, context, unfold_obj):
        """いまどこにいて、次に何を押すか。動いている道具もここ。

        畳める区画の中には置かない。道具が動いていることに気付けず、
        止め方も分からなくなる。
        """
        icon, current, next_step = _status.workflow(context)
        box = layout.box()
        box.label(text=current, icon=icon)
        if next_step:
            box.label(text=next_step, icon='FORWARD')

        if unfold_obj is not None:
            for warning in _status.scale_warnings(context, unfold_obj):
                box.label(text=warning, icon='ERROR')

        tool_text = _status.active_tool_text(context)
        if not tool_text:
            return

        running = box.row()
        running.alert = True
        running.label(text=tool_text, icon='REC')

        stop = box.row(align=True)
        stop.operator(
            "truescale_unfold.marking_tool_off",
            text="道具を止める",
            icon='PAUSE',
        )
        stop.operator(
            "truescale_unfold.finish_marking",
            text="終了して元の選択へ",
            icon='LOOP_BACK',
        )

    # ------------------------------------------------------------
    # 必須の4段
    # ------------------------------------------------------------

    def _draw_load(self, layout, context, scene):
        """1. 読み込む。モデルを決めて、どこで切り開くかを入れる。"""
        box = _step(layout, 1, "読み込む", 'IMPORT')

        box.operator(
            "truescale_unfold.load_seamed_object",
            text="モデルの読み込み",
            icon='IMPORT',
        )

        # 編集モードで選んだエッジをその場でシーム化できるようにする。
        # これが無いと、シームを足すたびにパネルの外へ出る必要があった。
        seam_box = box.box()
        seam_box.label(text="シーム編集", icon='EDGESEL')

        if context.mode != 'EDIT_MESH':
            seam_box.label(text="編集モードでエッジを選ぶと使えます")
            return

        seam_row = seam_box.row(align=True)
        seam_row.operator(
            "truescale_unfold.mark_seam",
            text="シームを入れる",
            icon='ADD',
        )
        seam_row.operator(
            "truescale_unfold.clear_seam",
            text="外す",
            icon='REMOVE',
        )

        sym_row = seam_box.row(align=True)
        sym_row.label(text="対称")
        sym_row.prop(scene, "tsunfold_seam_symmetry_x", text="X", toggle=True)
        sym_row.prop(scene, "tsunfold_seam_symmetry_y", text="Y", toggle=True)
        sym_row.prop(scene, "tsunfold_seam_symmetry_z", text="Z", toggle=True)

    def _draw_build(self, layout, context, scene, loaded_obj, unfold_obj):
        """2. 型紙にする。作る・消す・どちらを見るか。"""
        box = _step(layout, 2, "型紙にする", 'UV')

        build_row = box.row()
        build_row.scale_y = 1.2
        build_row.enabled = (
            loaded_obj is not None
            and loaded_obj.type == 'MESH'
            and any(bool(edge.use_seam) for edge in loaded_obj.data.edges)
        )
        build_row.operator(
            "truescale_unfold.build_pattern",
            text="型紙を作成 / 更新",
            icon='UV',
        )

        if loaded_obj is not None and loaded_obj.type == 'MESH':
            vis_row = box.row(align=True)
            vis_row.operator(
                "truescale_unfold.toggle_source_visibility",
                text=(
                    "元モデルを表示"
                    if loaded_obj.hide_get()
                    else "元モデルを非表示"
                ),
                icon='HIDE_OFF' if loaded_obj.hide_get() else 'HIDE_ON',
            )

            # 元モデル側だけでなく、型紙側へ跳ぶ手段も要る。
            # 作った型紙が画面の外にあると、探す方法が無かった。
            vis_row.operator(
                "truescale_unfold.toggle_pattern_preview",
                text=(
                    "型紙を隠す"
                    if scene.tsunfold_pattern_preview
                    else "型紙を表示して選択"
                ),
                icon='MESH_GRID',
            )

            box.prop(scene, "tsunfold_lightweight_view", text="軽量ビュー")

            # ONのあいだ元モデル側のマーキングが一切描かれない。
            # 既定でONなので、何も表示されない理由が分からなくなりやすい。
            if scene.tsunfold_lightweight_view:
                box.label(text="元モデル側の合印表示はOFF", icon='HIDE_ON')

        delete_row = box.row()
        delete_row.enabled = (unfold_obj is not None)
        delete_row.operator(
            "truescale_unfold.delete_unfold",
            text="型紙を削除",
            icon='TRASH',
        )

    def _draw_layout(self, layout, context, scene, unfold_obj):
        """3. 紙に並べる。用紙を決め、島を置き、分割の見通しを出す。

        印をつけるより前に置く。用紙より大きい型紙は分割して刷るので、
        継ぎ目の位置が決まっていないと、合印が継ぎ目に乗る。
        """
        box = _step(layout, 3, "紙に並べる", 'MESH_GRID')

        if unfold_obj is None:
            box.label(text="先に型紙を作成してください")
            return

        size = _build.object_xy_size_mm(context, unfold_obj)
        if size:
            box.label(text=f"実寸: 横 {size[0]:.1f} × 縦 {size[1]:.1f} mm")

        row = box.row(align=True)
        row.prop(scene, "tsunfold_paper_size", text="用紙")

        if scene.tsunfold_paper_size != "CUSTOM":
            row.prop(scene, "tsunfold_orientation", text="向き")
        else:
            custom = box.box()
            custom.label(text="カスタム用紙サイズ（mm）")
            custom_row = custom.row(align=True)
            custom_row.prop(
                scene, "tsunfold_custom_paper_width_mm", text="幅"
            )
            custom_row.prop(
                scene, "tsunfold_custom_paper_height_mm", text="高さ"
            )
            custom.label(text="入力した幅 × 高さをそのまま使用")

        paper_w_mm, paper_h_mm = _paper.scene_dimensions_mm(scene)
        box.label(text=f"使用サイズ: {paper_w_mm:.1f} × {paper_h_mm:.1f} mm")

        # 何枚になるかは、用紙を変えられるこの場所で出す。
        # 書き出しの直前で知らされても、戻って直すことになる。
        for line in _status.paper_fit_text(context):
            box.label(text=line, icon='INFO')

        box.prop(scene, "tsunfold_show_paper", text="用紙ガイドを表示")

        # 余白は常に出す。以前は分割が要るときだけ出していたが、
        # 用紙の余白と型紙のまわりは1枚に収まるときも効いている。
        # 効いているのに見えない設定があると、「なぜか小さく刷られる」
        # の原因に辿り着けない。
        tiling = _status.needs_tiling(context)

        paper = box.box()
        paper.label(text="紙のとりかた", icon='MOD_BUILD')
        paper.use_property_split = True
        paper.prop(scene, "tsunfold_tile_margin_mm", text="用紙の余白 (mm)")
        paper.prop(
            scene, "tsunfold_pattern_inset_mm",
            text="型紙のまわり (mm)",
        )

        # 重ねしろは分割するときだけ意味がある。出したままだと
        # 「効いていない設定」に見えるので、そのときだけ出す。
        if tiling:
            paper.prop(
                scene, "tsunfold_tile_overlap_mm", text="重ねしろ (mm)"
            )
            note = paper.column(align=True)
            note.scale_y = 0.8
            note.label(text="切らずに重ねて貼れます")
            note.label(text="刷ったら目盛りを定規で確認")

        box.separator()

        row = box.row(align=True)
        row.operator(
            "truescale_unfold.auto_layout",
            text="自動レイアウト",
            icon='NODE_CORNER',
        )
        row.operator(
            "truescale_unfold.layout_edit",
            text="手動で調整",
            icon='EDITMODE_HLT',
        )
        box.operator(
            "truescale_unfold.layout_confirm",
            text="レイアウト確定",
            icon='CHECKMARK',
        )

        if _manual_layout_active(scene):
            box.label(text="手動調整中：印は島と一緒に動きます")
        else:
            box.label(text="面を選択してGで移動 → レイアウト確定")

    def _draw_output(self, layout, context, scene, unfold_obj):
        """4. 出す / 残す。ゴールは2つあるので、並べて出す。"""
        box = _step(layout, 4, "出す / 残す", 'EXPORT')

        has_pattern = unfold_obj is not None

        export = box.column()
        export.enabled = has_pattern

        export.operator(
            "truescale_unfold.toggle_preview",
            text=(
                "印刷プレビューを終了"
                if scene.tsunfold_preview
                else "印刷プレビュー"
            ),
            icon='HIDE_OFF',
            depress=scene.tsunfold_preview,
        )
        export.prop(scene, "tsunfold_export_format", text="形式")

        run = export.row()
        run.scale_y = 1.2
        run.operator(
            "truescale_unfold.export_sheets",
            text="実寸で書き出し（300dpi）",
            icon='EXPORT',
        )

        box.separator()

        keep = box.column()
        keep.enabled = has_pattern
        keep.operator(
            "truescale_unfold.finalize_pattern",
            text="型紙を確定して残す",
            icon='CHECKMARK',
        )
        keep.label(text="アドオンから切り離し、普通のメッシュにします")
        keep.label(text="マーキングは消えますが、片付けでも消えません")

        box.separator()

        box.operator(
            "truescale_unfold.return_default",
            text="作業終了・型紙を片付ける",
            icon='HOME',
        )
        box.label(text="確定していない型紙は消えます")

    # ------------------------------------------------------------
    # 任意
    # ------------------------------------------------------------

    def _draw_allowance(self, layout, context, scene):
        """縫い代と糊代。

        並べるより前に置く。どちらも型紙を大きくするので、あとから
        足すと用紙の計算が合わなくなる。必要な人だけが開けばよいので
        畳んでおくが、順番としてはここにある。
        """
        box, open_now = _folded(
            layout, scene, "tsunfold_show_allowance",
            "縫い代・糊代（任意）", 'MOD_SOLIDIFY',
        )
        if not open_now:
            return

        glue = box.box()
        glue.prop(scene, "tsunfold_tab_enable")
        if getattr(scene, "tsunfold_tab_enable", False):
            glue.prop(scene, "tsunfold_tab_width_mm", text="幅 (mm)")
            note = glue.column(align=True)
            note.scale_y = 0.8
            note.label(text="シームの辺すべてに片側だけ付きます")
            note.label(text="根元の破線は折り線。切らないでください")

            # 辺ごとの調整。元メッシュを編集モードにして辺を選び、
            # ボタンを押す。シーム指定と同じ手順。
            picked = glue.column(align=True)
            picked.label(text="辺ごとの調整（元メッシュで辺を選択）")

            row = picked.row(align=True)
            row.operator(
                "truescale_unfold.toggle_tab_edges",
                text="消す", icon='X',
            ).off = True
            row.operator(
                "truescale_unfold.toggle_tab_edges",
                text="戻す", icon='CHECKMARK',
            ).off = False

            picked.operator(
                "truescale_unfold.flip_tab_edges",
                text="反対側へ移す", icon='ARROW_LEFTRIGHT',
            )
            picked.operator(
                "truescale_unfold.reset_tab_edges",
                text="全部戻す", icon='LOOP_BACK',
            )

            hint = glue.column(align=True)
            hint.scale_y = 0.8
            hint.label(text="移した先に置けないときは、元の側のままです")
            hint.label(text="元モデルのシーム線の色で、状態が分かります")
            hint.label(text="　赤=そのまま　灰=消した　橙=入れ替えた")

        sew = box.box()
        sew.prop(scene, "tsunfold_seam_enable")
        if getattr(scene, "tsunfold_seam_enable", False):
            sew.prop(scene, "tsunfold_seam_width_mm", text="幅 (mm)")
            note = sew.column(align=True)
            note.scale_y = 0.8
            note.label(text="外側の実線が裁断線、内側の破線が縫い線")

        if (getattr(scene, "tsunfold_tab_enable", False)
                or getattr(scene, "tsunfold_seam_enable", False)):
            shown = box.box()
            shown.label(text="画面での色（刷ると黒になります）")
            row = shown.row(align=True)
            row.prop(
                scene, "tsunfold_allowance_cut_color", text="裁断線",
            )
            row.prop(
                scene, "tsunfold_allowance_fold_color", text="折り線",
            )

    def _draw_marking(self, layout, context, scene):
        """印をつける。必要に応じてやるものなので畳んでおく。"""
        box, open_now = _folded(
            layout, scene, "tsunfold_show_marking",
            "印をつける（任意）", 'SNAP_MIDPOINT',
        )
        if not open_now:
            return

        hint = _status.source_required_hint(context)

        self._draw_notch(box, context, scene, hint)
        self._draw_number_text(box, context, scene, hint)
        self._draw_island_id(box, scene)
        self._draw_arrow(box, context, scene, hint)

        box.operator(
            "truescale_unfold.clear_annotations",
            text="すべてのマーキングをクリア",
            icon='TRASH',
        )

    def _draw_notch(self, layout, context, scene, hint):
        box = layout.box()
        box.use_property_split = True
        box.use_property_decorate = False
        head = box.row()
        head.scale_y = 1.25
        head.label(text="◆ 合印", icon='SNAP_MIDPOINT')

        box.prop(scene, "tsunfold_notch_mode", text="方式")

        if scene.tsunfold_notch_mode == "AUTO":
            box.prop(scene, "tsunfold_auto_notch_divisions", text="分割数")

            row = box.row(align=True)
            row.operator(
                "truescale_unfold.refresh_auto_notches",
                text="作成 / 更新",
            )
            # ラベルどおり、オートと手動の両方を消す。
            # 以前はオートだけを消すオペレータに繋がっており、
            # 手動で置いた合印が残って戸惑う原因になっていた。
            row.operator(
                "truescale_unfold.remove_all_notches",
                text="合印削除",
                icon='X',
            )

            # 今いくつ合印があるのかを出す。
            # これが無いと、設定を変えても効いているのか分からなかった。
            box.label(text=_status.notch_text(context))

            # 手動で置いた合印があるときだけ、残す選択肢も出す。
            # 常に2つ並べると、違いが分からず選べない。
            if _status.manual_notch_count(context) > 0:
                box.operator(
                    "truescale_unfold.remove_auto_notches",
                    text="オートだけ削除（手動は残す）",
                    icon='X',
                )

        if scene.tsunfold_notch_mode == "NONE":
            return

        add_row = box.row()
        add_row.operator(
            "truescale_unfold.place_notch",
            text="手動で合印を追加",
            icon='ADD',
        )
        if hint:
            add_row.enabled = False
            box.label(text=hint, icon='INFO')

        # よく変える寸法を先に、色を後に。
        box.prop(scene, "tsunfold_notch_length_mm", text="合印の長さ")
        box.prop(scene, "tsunfold_notch_thickness_mm", text="合印の太さ")

        color_row = box.row(align=True)
        color_row.label(text="色")
        color_row.prop(scene, "tsunfold_notch_color", text="")

    def _draw_number_text(self, layout, context, scene, hint):
        box = layout.box()
        box.use_property_split = True
        box.use_property_decorate = False
        head = box.row()
        head.scale_y = 1.25
        head.label(text="◆ 番号・文字", icon='SMALL_CAPS')

        num_row = box.row(align=True)
        num_row.operator(
            "truescale_unfold.place_number",
            text="手動で番号を追加",
            icon='ADD',
        )
        num_row.operator(
            "truescale_unfold.reset_number",
            text="",
            icon='LOOP_BACK',
        )
        if hint:
            num_row.enabled = False

        box.prop(scene, "tsunfold_number_start", text="開始番号")
        box.label(text=f"次に置く番号: {scene.tsunfold_next_number}")
        box.prop(scene, "tsunfold_number_size_mm", text="番号の大きさ")

        color_row = box.row(align=True)
        color_row.label(text="番号の色")
        color_row.prop(scene, "tsunfold_number_color", text="")

        box.separator()

        box.prop(scene, "tsunfold_custom_text", text="文字")
        add_text = box.row()
        add_text.operator(
            "truescale_unfold.place_text",
            text="手動で文字を追加",
            icon='ADD',
        )
        # 文字が空のまま始めても、クリックのたびに警告が出るだけ。
        add_text.enabled = bool(scene.tsunfold_custom_text) and not hint

        box.prop(scene, "tsunfold_text_size_mm", text="文字の大きさ")
        color_row = box.row(align=True)
        color_row.label(text="文字の色")
        color_row.prop(scene, "tsunfold_text_color", text="")

        if hint:
            box.label(text=hint, icon='INFO')

    def _draw_island_id(self, layout, scene):
        box = layout.box()
        box.use_property_split = True
        box.use_property_decorate = False
        head = box.row()
        head.scale_y = 1.25
        head.label(text="◆ 型紙ID・接続先", icon='SORTALPHA')

        box.prop(scene, "tsunfold_auto_island_ids", text="自動IDを表示")

        if not scene.tsunfold_auto_island_ids:
            return

        box.prop(scene, "tsunfold_island_id_style", text="形式")
        box.prop(scene, "tsunfold_island_id_size_mm", text="ID文字サイズ")
        box.label(text="各辺には接続先IDを自動表示")

    def _draw_arrow(self, layout, context, scene, hint):
        box = layout.box()
        box.use_property_split = True
        box.use_property_decorate = False
        head = box.row()
        head.scale_y = 1.25
        head.label(text="↑ ◆ 上方向矢印")

        box.prop(scene, "tsunfold_show_direction_arrow", text="水色の方向ガイド")
        box.prop(scene, "tsunfold_arrow_mode", text="方式")

        if scene.tsunfold_arrow_mode == "NONE":
            return

        # 手動で足す手段は、設定の下ではなく方式のすぐ下に置く。
        # 以前は設定4つの下にあり、あることに気付かれなかった。
        add_row = box.row()
        add_row.operator(
            "truescale_unfold.place_arrow",
            text="手動で矢印を追加（始点→終点）",
            icon='FORWARD',
        )
        if hint:
            add_row.enabled = False
            box.label(text=hint, icon='INFO')

        count = _status.manual_arrow_count(context)
        if count:
            box.label(text=f"手動の矢印 {count} 本")

        if scene.tsunfold_arrow_mode == "AUTO":
            box.prop(scene, "tsunfold_arrow_up_axis", text="上方向")
            box.prop(scene, "tsunfold_auto_arrow_length_mm", text="矢印の長さ")

        box.prop(scene, "tsunfold_arrow_head_mm", text="矢印ヘッド長さ")
        box.prop(scene, "tsunfold_arrow_thickness_mm", text="線の太さ")

        color_row = box.row(align=True)
        color_row.label(text="色")
        color_row.prop(scene, "tsunfold_arrow_color", text="")

        box.operator(
            "truescale_unfold.clear_arrows_all",
            text="矢印を全削除",
            icon='TRASH',
        )

    def _draw_correspondence(self, layout, context, scene):
        """立体のどこだったかを確かめる。使うときだけ開く。"""
        box, open_now = _folded(
            layout, scene, "tsunfold_show_correspondence",
            "対応を確かめる・メモ（任意）", 'RESTRICT_SELECT_OFF',
        )
        if not open_now:
            return

        box.operator(
            "truescale_unfold.pick_corresponding_island",
            text=(
                "対応確認を終了"
                if scene.tsunfold_correspondence_mode
                else "対応確認を開始"
            ),
            depress=scene.tsunfold_correspondence_mode,
        )
        box.operator(
            "truescale_unfold.clear_island_highlight",
            text="対応ハイライトをクリア",
        )

        if scene.tsunfold_correspondence_mode:
            box.label(text="型紙を2回クリック → メモ追加")
            box.label(text="既存メモをクリック → Rで回転")

        box.separator()

        memo_row = box.row(align=True)
        memo_row.operator(
            "truescale_unfold.place_flat_memo",
            text="型紙にメモを追加",
        )
        memo_row.operator(
            "truescale_unfold.clear_flat_memos",
            text="メモ全削除",
            icon='TRASH',
        )
