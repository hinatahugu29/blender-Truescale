"""サイドバーのパネル。

描くだけで、押した先の処理はオペレータが持つ。ここに判断を書くと、
同じことをオペレータ側でも書くことになり、片方だけ直す事故が起きる。

■ 先頭に道案内を出す

初見では、ボタンは並んでいるのにいま何段目なのかが分からなかった。
status が返す (現在地, 次の一手) を最初に出し、その下に手順の順で
並べている。

■ 状態によって出し分ける

型紙ができていない段階でレイアウトや書き出しを出しても押せない。
押せないものは薄くするのではなく、出さないか理由を添える。

■ 単位の注意は畳まない

スケールの警告は、それが出ている時点で結果が間違っている可能性がある。
折りたたみの中に入れると気付かれないので、その場に出す。
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


class TSUNFOLD_PT_main(bpy.types.Panel):
    bl_label = "Truescale Unfold"
    bl_idname = "TSUNFOLD_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Truescale"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        scene = context.scene
        unfold_obj = _objects.resolve_unfold_for_layout(context)

        # ------------------------------------------------------
        # 1. Load seamed model + build pattern
        # ------------------------------------------------------
        unit_box = layout.box()
        unit_box.label(text="実寸の基準")
        unit_box.prop(scene, "tsunfold_scale_mode", text="")

        scene_scale, mm_per_bu = _units.scene_unit_summary(scene)

        if scene.tsunfold_scale_mode == "MANUAL":
            unit_box.prop(scene, "tsunfold_manual_mm_per_bu", text="1 BU =")
            # シーン側の値も併記する。どちらが使われているかを明確にするため。
            unit_box.label(
                text=f"シーンの Unit Scale {_units.scene_unit_scale_raw(scene):g} は未使用",
                icon='INFO',
            )
        else:
            unit_box.label(
                text=f"Unit Scale {scene_scale:g} / 1 BU = {mm_per_bu:g} mm"
            )

        if context.mode == 'EDIT_MESH':
            unit_box.operator(
                "truescale_unfold.calibrate_scale",
                text="選択した辺を基準に決める",
                icon='DRIVER_DISTANCE',
            )

        active_source = _objects.seam_source(context)
        if active_source is not None:
            sx, sy, sz = (
                float(active_source.scale.x),
                float(active_source.scale.y),
                float(active_source.scale.z),
            )
            unit_box.label(
                text=f"Object Scale: {sx:g}, {sy:g}, {sz:g}"
            )
            if (
                abs(sx - 1.0) > 1.0e-6
                or abs(sy - 1.0) > 1.0e-6
                or abs(sz - 1.0) > 1.0e-6
            ):
                unit_box.label(
                    text="未適用Scaleもワールド寸法として反映",
                    icon='INFO',
                )

        # いまどの段階にいて、次に何を押せばよいかを先頭に出す。
        icon, current, next_step = _status.workflow(context)
        status_box = layout.box()
        status_box.label(text=current, icon=icon)
        if next_step:
            status_box.label(text=next_step, icon='FORWARD')

        if unfold_obj is not None:
            for warning in _status.scale_warnings(context, unfold_obj):
                status_box.label(text=warning, icon='ERROR')

        source_box = layout.box()
        source_box.label(text="1. モデルと型紙")

        loaded_name = scene.get(_session.SEAM_SOURCE, "")
        loaded_obj = bpy.data.objects.get(loaded_name) if loaded_name else None

        source_box.operator(
            "truescale_unfold.load_seamed_object",
            text="モデルの読み込み",
            icon='IMPORT',
        )

        # 編集モードで選んだエッジをその場でシーム化できるようにする。
        # これが無いと、シームを足すたびにパネルの外へ出る必要があった。
        seam_box = source_box.box()
        seam_box.label(text="シーム編集", icon='EDGESEL')

        if context.mode == 'EDIT_MESH':
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
        else:
            seam_box.label(text="編集モードでエッジを選ぶと使えます")

        if loaded_obj is not None and loaded_obj.type == 'MESH':
            vis_row = source_box.row(align=True)
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
            source_box.prop(
                scene,
                "tsunfold_lightweight_view",
                text="軽量ビュー",
            )

            # ONのあいだ元モデル側のマーキングが一切描かれない。
            # 既定でONなので、何も表示されない理由が分からなくなりやすい。
            if scene.tsunfold_lightweight_view:
                source_box.label(
                    text="元モデル側の合印表示はOFF",
                    icon='HIDE_ON',
                )

        build_row = source_box.row()
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

        delete_row = source_box.row()
        delete_row.enabled = (unfold_obj is not None)
        delete_row.operator(
            "truescale_unfold.delete_unfold",
            text="型紙を削除",
            icon='TRASH',
        )

        # ------------------------------------------------------
        # 2. Marking
        # ------------------------------------------------------
        marking = layout.box()
        marking.label(text="2. マーキング")

        # 道具はトグルで、押したあとの手応えが画面に無かった。
        # 動いているのかどうかを最初に出す。
        tool_text = _status.active_tool_text(context)
        if tool_text:
            running = marking.row()
            running.alert = True
            running.label(text=tool_text, icon='REC')

            # 同じボタンをもう一度押しても止まるが、それが分かる
            # 作りになっていなかった。止め方をその場に出す。
            stop = marking.row(align=True)
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

        source = _objects.source_from_context(context)
        if source is None:
            source = _objects.seam_source(context)

        # -------------------------
        # NOTCH
        # -------------------------
        notch_box = marking.box()
        notch_box.use_property_split = True
        notch_box.use_property_decorate = False
        head = notch_box.row()
        head.scale_y = 1.25
        head.label(text="◆ 合印", icon='SNAP_MIDPOINT')

        notch_box.prop(
            scene,
            "tsunfold_notch_mode",
            text="方式",
        )

        if scene.tsunfold_notch_mode == "AUTO":
            notch_box.prop(
                scene,
                "tsunfold_auto_notch_divisions",
                text="分割数",
            )


        if scene.tsunfold_notch_mode == "AUTO":
            row = notch_box.row(align=True)
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
            notch_box.label(text=_status.notch_text(context))

            # 手動で置いた合印があるときだけ、残す選択肢も出す。
            # 常に2つ並べると、違いが分からず選べない。
            if _status.manual_notch_count(context) > 0:
                notch_box.operator(
                    "truescale_unfold.remove_auto_notches",
                    text="オートだけ削除（手動は残す）",
                    icon='X',
                )

        if scene.tsunfold_notch_mode != "NONE":
            add_row = notch_box.row()
            add_row.operator(
                "truescale_unfold.place_notch",
                text="手動で合印を追加",
                icon='ADD',
            )

            hint = _status.source_required_hint(context)
            if hint:
                add_row.enabled = False
                notch_box.label(text=hint, icon='INFO')

            # Frequently changed geometry setting first.
            notch_box.prop(
                scene,
                "tsunfold_notch_length_mm",
                text="合印の長さ",
            )
            notch_box.prop(
                scene,
                "tsunfold_notch_thickness_mm",
                text="合印の太さ",
            )

            # Color is secondary.
            color_row = notch_box.row(align=True)
            color_row.label(text="色")
            color_row.prop(
                scene,
                "tsunfold_notch_color",
                text="",
            )

        # -------------------------
        # NUMBER / TEXT
        # -------------------------
        # どちらも実装は揃っていたのに、始める手段も設定も
        # パネルに無く、機能ごと隠れていた。
        text_box = marking.box()
        text_box.use_property_split = True
        text_box.use_property_decorate = False
        head = text_box.row()
        head.scale_y = 1.25
        head.label(text="◆ 番号・文字", icon='SMALL_CAPS')

        num_row = text_box.row(align=True)
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

        hint = _status.source_required_hint(context)
        if hint:
            num_row.enabled = False

        text_box.prop(scene, "tsunfold_number_start", text="開始番号")
        text_box.label(text=f"次に置く番号: {scene.tsunfold_next_number}")
        text_box.prop(scene, "tsunfold_number_size_mm", text="番号の大きさ")

        color_row = text_box.row(align=True)
        color_row.label(text="番号の色")
        color_row.prop(scene, "tsunfold_number_color", text="")

        text_box.separator()

        text_box.prop(scene, "tsunfold_custom_text", text="文字")
        add_text = text_box.row()
        add_text.operator(
            "truescale_unfold.place_text",
            text="手動で文字を追加",
            icon='ADD',
        )
        # 文字が空のまま始めても、クリックのたびに警告が出るだけ。
        add_text.enabled = bool(scene.tsunfold_custom_text) and not hint

        text_box.prop(scene, "tsunfold_text_size_mm", text="文字の大きさ")
        color_row = text_box.row(align=True)
        color_row.label(text="文字の色")
        color_row.prop(scene, "tsunfold_text_color", text="")

        if hint:
            text_box.label(text=hint, icon='INFO')

        # -------------------------
        # PATTERN ID
        # -------------------------
        id_box = marking.box()
        id_box.use_property_split = True
        id_box.use_property_decorate = False
        head = id_box.row()
        head.scale_y = 1.25
        head.label(text="◆ 型紙ID・接続先", icon='SORTALPHA')

        id_box.prop(
            scene,
            "tsunfold_auto_island_ids",
            text="自動IDを表示",
        )

        if scene.tsunfold_auto_island_ids:
            id_box.prop(
                scene,
                "tsunfold_island_id_style",
                text="形式",
            )

            # Size first, color second.
            id_box.prop(
                scene,
                "tsunfold_island_id_size_mm",
                text="ID文字サイズ",
            )

            id_box.label(text="各辺には接続先IDを自動表示")

        # -------------------------
        # ARROW
        # -------------------------
        arrow_box = marking.box()
        arrow_box.use_property_split = True
        arrow_box.use_property_decorate = False
        head = arrow_box.row()
        head.scale_y = 1.25
        head.label(text="↑ ◆ 上方向矢印")

        arrow_box.prop(
            scene,
            "tsunfold_show_direction_arrow",
            text="水色の方向ガイド",
        )

        arrow_box.prop(
            scene,
            "tsunfold_arrow_mode",
            text="方式",
        )

        # 手動で足す手段は、設定の下ではなく方式のすぐ下に置く。
        # 以前は設定4つの下にあり、あることに気付かれなかった。
        if scene.tsunfold_arrow_mode != "NONE":
            add_row = arrow_box.row()
            add_row.operator(
                "truescale_unfold.place_arrow",
                text="手動で矢印を追加（始点→終点）",
                icon='FORWARD',
            )

            hint = _status.source_required_hint(context)
            if hint:
                add_row.enabled = False
                arrow_box.label(text=hint, icon='INFO')

            count = _status.manual_arrow_count(context)
            if count:
                arrow_box.label(text=f"手動の矢印 {count} 本")

        if scene.tsunfold_arrow_mode == "AUTO":
            arrow_box.prop(
                scene,
                "tsunfold_arrow_up_axis",
                text="上方向",
            )

            arrow_box.prop(
                scene,
                "tsunfold_auto_arrow_length_mm",
                text="矢印の長さ",
            )

        if scene.tsunfold_arrow_mode != "NONE":
            arrow_box.prop(
                scene,
                "tsunfold_arrow_head_mm",
                text="矢印ヘッド長さ",
            )
            arrow_box.prop(
                scene,
                "tsunfold_arrow_thickness_mm",
                text="線の太さ",
            )

            color_row = arrow_box.row(align=True)
            color_row.label(text="色")
            color_row.prop(
                scene,
                "tsunfold_arrow_color",
                text="",
            )

            arrow_box.operator(
                "truescale_unfold.clear_arrows_all",
                text="矢印を全削除",
                icon='TRASH',
            )

        # -------------------------
        # COMMON MARKING TOOLS
        # -------------------------
        # Common destructive action only.
        marking.operator(
            "truescale_unfold.clear_annotations",
            text="すべてのマーキングをクリア",
            icon='TRASH',
        )

        # Correspondence controls stay close to marking but visually separate.
        corr_box = marking.box()
        corr_box.use_property_split = True
        corr_box.use_property_decorate = False
        head = corr_box.row()
        head.scale_y = 1.15
        head.label(text="◆ 対応確認・メモ", icon='RESTRICT_SELECT_OFF')

        corr_box.operator(
            "truescale_unfold.pick_corresponding_island",
            text=(
                "対応確認を終了"
                if scene.tsunfold_correspondence_mode
                else "対応確認を開始"
            ),
            depress=scene.tsunfold_correspondence_mode,
        )
        corr_box.operator(
            "truescale_unfold.clear_island_highlight",
            text="対応ハイライトをクリア",
        )

        if scene.tsunfold_correspondence_mode:
            corr_box.label(text="型紙を2回クリック → メモ追加")
            corr_box.label(text="既存メモをクリック → Rで回転")

        corr_box.separator()
        memo_row = corr_box.row(align=True)
        memo_row.operator(
            "truescale_unfold.place_flat_memo",
            text="型紙にメモを追加",
        )
        memo_row.operator(
            "truescale_unfold.clear_flat_memos",
            text="メモ全削除",
            icon='TRASH',
        )

        # ------------------------------------------------------
        # 3. Unfold / layout / line finish
        # ------------------------------------------------------
        output = layout.box()
        output.label(text="3. レイアウト・印刷")

        unfold_obj = _objects.resolve_unfold_for_layout(context)

        if unfold_obj is None:
            output.label(text="先に型紙を作成してください")
        else:
            size = _build.object_xy_size_mm(context, unfold_obj)
            if size:
                output.label(
                    text=f"実寸: 横 {size[0]:.1f} × 縦 {size[1]:.1f} mm"
                )

            output.label(text="用紙設定")
            row = output.row(align=True)
            row.prop(
                scene,
                "tsunfold_paper_size",
                text="用紙",
            )

            if scene.tsunfold_paper_size != "CUSTOM":
                row.prop(
                    scene,
                    "tsunfold_orientation",
                    text="向き",
                )
            else:
                custom = output.box()
                custom.label(text="カスタム用紙サイズ（mm）")
                custom_row = custom.row(align=True)
                custom_row.prop(
                    scene,
                    "tsunfold_custom_paper_width_mm",
                    text="幅",
                )
                custom_row.prop(
                    scene,
                    "tsunfold_custom_paper_height_mm",
                    text="高さ",
                )
                custom.label(text="入力した幅 × 高さをそのまま使用")

            paper_w_mm, paper_h_mm = _paper.scene_dimensions_mm(scene)
            output.label(
                text=f"使用サイズ: {paper_w_mm:.1f} × {paper_h_mm:.1f} mm"
            )

            output.prop(
                scene,
                "tsunfold_show_paper",
                text="用紙ガイドを表示",
            )

            output.separator()
            output.label(text="レイアウト")
            row = output.row(align=True)
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
            output.operator(
                "truescale_unfold.layout_confirm",
                text="レイアウト確定",
                icon='CHECKMARK',
            )
            if _manual_layout_active(scene):
                output.label(text="手動調整中：マーキング表示を一時停止")
            else:
                output.label(text="面を選択してGで移動 → レイアウト確定")

            output.separator()
            output.label(text="印刷・書き出し")

            output.operator(
                "truescale_unfold.toggle_preview",
                text=(
                    "印刷プレビューを終了"
                    if scene.tsunfold_preview
                    else "印刷プレビュー"
                ),
                icon='HIDE_OFF',
                depress=scene.tsunfold_preview,
            )

            output.operator(
                "truescale_unfold.export_png",
                text="実寸PNGを書き出し（300dpi）",
                icon='EXPORT',
            )

        finish = layout.box()
        finish.operator(
            "truescale_unfold.return_default",
            text="作業終了・型紙を片付ける",
            icon='HOME',
        )

        info = layout.box()
        info.label(text="型紙ヘルパー カスタムシーン Beta v1.5.6")
