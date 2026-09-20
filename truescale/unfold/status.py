"""パネルに出す案内文。

初めて触ったとき「どこで何が起きているのか分からない」という状態に
なりやすい。ボタンは並んでいるが、押せる状態なのか、いま何段目なのかが
画面から読めなかった。ここはその道案内を組み立てる。

■ 進み具合

読み込み → シーム → 型紙、のどこにいるかを (アイコン, 現在地, 次の一手)
で返す。次の一手が None なら完了。

■ 数を出す

設定を変えても見た目が変わったか分かりにくい。合印の件数を出して、
効いていることを目に見える形で返す。

■ スケールの注意

Unit Scale が既定の 1 のままだと 1 BU = 1000 mm となり、デフォルトの
円柱が2メートルの物体になる。用紙ガイドが点になり、自動レイアウトが
必ず失敗し、合印が小さすぎて見えない。全部同じ原因なのに、画面には
どれも理由が出ていなかった。
"""

import bpy

from .. import debug as _debug
from ..core import geometry as _geometry
from ..core import objects as _objects
from ..core import paper as _paper
from ..core import session as _session
from ..core import units as _units
from ..marking import storage as _storage
from . import build as _build


def workflow(context):
    """いま何ができていて、次に何をすればよいかを返す。

    (アイコン, 現在地, 次の一手) の3つ組。次の一手が無ければ None。

    初回に触ったとき「どこで何が起きているのか分からない」という
    状態になりやすいため、パネルの先頭に出して道案内にする。
    """
    scene = context.scene

    loaded_name = scene.get(_session.SEAM_SOURCE, "")
    source = bpy.data.objects.get(loaded_name) if loaded_name else None

    if source is None or source.type != 'MESH':
        active = context.active_object
        if active is not None and active.type == 'MESH':
            return (
                'INFO',
                f"未読み込み（選択中: {active.name}）",
                "「モデルの読み込み」を押してください",
            )
        return (
            'INFO',
            "未読み込み",
            "シームを設定したMeshを選んでください",
        )

    seam_count = sum(1 for edge in source.data.edges if edge.use_seam)

    if seam_count == 0:
        return (
            'ERROR',
            f"{source.name} を読み込み済み / シーム 0 本",
            "編集モードで辺を選び「シームを入れる」を押してください",
        )

    unfold = _objects.unfold_for_source(source)
    if unfold is None:
        return (
            'INFO',
            f"{source.name} / シーム {seam_count} 本",
            "「型紙を作成 / 更新」を押してください",
        )

    islands = len(_geometry.face_islands(unfold.data))
    size = _build.object_xy_size_mm(context, unfold)
    size_text = f" / {size[0]:.0f}×{size[1]:.0f} mm" if size else ""

    return (
        'CHECKMARK',
        f"型紙 {islands} 枚{size_text}",
        None,
    )


def scale_warnings(context, unfold_obj):
    """型紙が用紙に対して極端に大きい／小さい場合の注意を返す。

    Unit Scale が 1 のままだと 1 BU = 1000 mm 換算になり、Blenderの
    デフォルト円柱（半径1 BU）が直径2メートルの物体として扱われる。
    その状態では
      - 用紙ガイドが画面上でほぼ点にしかならない
      - 自動レイアウトが必ず失敗する
      - 300dpi PNG がピクセル数の上限を超える
      - 実寸で正しいはずの合印が小さすぎて見えない
    が同時に起きるが、どれも「なぜそうなるか」が画面に出ていなかった。
    """
    size = _build.object_xy_size_mm(context, unfold_obj)
    if not size:
        return []

    width_mm, height_mm = size
    longest = max(width_mm, height_mm)
    if longest <= 0.0:
        return []

    paper_w, paper_h = _paper.scene_dimensions_mm(context.scene)
    paper_longest = max(paper_w, paper_h)

    lines = []

    # 用紙に対して大きすぎる
    if longest > paper_longest:
        ratio = longest / paper_longest
        lines.append(
            f"用紙 {_paper.scene_display_name(context.scene)} の約 {ratio:.1f} 倍です"
        )

        fits = [
            name for name, (pw, ph) in sorted(
                _paper.SIZES_MM.items(),
                key=lambda kv: kv[1][0] * kv[1][1],
            )
            if width_mm <= max(pw, ph) and height_mm <= min(pw, ph)
            or width_mm <= min(pw, ph) and height_mm <= max(pw, ph)
        ]
        if fits:
            lines.append(f"{fits[0]} なら収まります")
        else:
            lines.append("A0でも収まりません。分割が必要です")

    # 合印が小さすぎて見えない
    notch_mm = float(getattr(context.scene, "tsunfold_notch_length_mm", 6.0))
    if notch_mm > 0.0 and longest / notch_mm > 300.0:
        lines.append(
            f"合印 {notch_mm:.1f} mm は型紙に対して小さすぎます"
        )

    # 島の間隔が型紙より大きいと、島が散らばって全体が巨大に見える。
    # 間隔は絶対値のミリ指定なので、基準を小さくすると相対的に効きすぎる。
    spacing_mm = float(getattr(context.scene, "tsunfold_spacing_mm", 10.0))
    islands = len(_geometry.face_islands(unfold_obj.data))
    if islands > 1 and spacing_mm > 0.0:
        # 間隔の総和が型紙の長辺の半分を超えたら、間隔が支配的
        total_gap = spacing_mm * (islands - 1)
        if total_gap > longest * 0.5:
            lines.append(
                f"島の間隔 {spacing_mm:g} mm が型紙に対して大きすぎます"
            )

    # 原因が Unit Scale にありそうな場合だけ添える
    if lines:
        scale, mm_per_bu = _units.scene_unit_summary(context.scene)
        if mm_per_bu >= 100.0:
            lines.append(
                f"Unit Scale {scale:g}（1 BU = {mm_per_bu:g} mm）を確認してください"
            )

    return lines


def manual_notch_count(context):
    """手動で置かれた合印の数。"""
    source = _objects.seam_source(context)
    if source is None:
        source = _objects.source_from_context(context)

    if source is None or source.type != 'MESH':
        return 0

    return sum(
        1
        for item in _storage.load(source)
        if item.get("type") == "notch_edge" and not bool(item.get("auto", False))
    )


def manual_arrow_count(context):
    """手動で置かれた矢印の数。

    オート矢印は注記として保存しないので、ここに出るのは
    手動で置いたものだけ。
    """
    source = _objects.seam_source(context)
    if source is None:
        source = _objects.source_from_context(context)

    if source is None or source.type != 'MESH':
        return 0

    return sum(
        1 for item in _storage.load(source) if item.get("type") == "arrow"
    )


def _plan(context):
    """いまの型紙の分割計画。取れなければ (None, None)。"""
    try:
        from .ops.export import tile_plan
        return tile_plan(context)
    except Exception:
        _debug.swallowed("unfold.status._plan")
        return (None, None)


def needs_tiling(context):
    """いまの型紙が用紙に収まらないか。"""
    drawing, plan = _plan(context)
    if drawing is None or plan is None:
        return False
    return plan.count > 1


def paper_fit_text(context):
    """用紙に対する収まり具合。押す前に知らせる。

    「入らない」とだけ言われても先へ進めない。何枚になるのか、
    どの用紙なら1枚で済むのかまで出す。
    """
    drawing, plan = _plan(context)
    if drawing is None:
        return []

    size = f"{drawing.width_mm:.0f} × {drawing.height_mm:.0f} mm"

    if plan is None:
        return [f"型紙 {size}", "余白と重ねしろが用紙に対して大きすぎます"]

    if plan.count <= 1:
        return [f"型紙 {size} → 1枚に収まります"]

    lines = [f"型紙 {size} → {plan.describe()}"]

    # 1枚で済む用紙があるなら教える
    for name, (pw, ph) in sorted(
        _paper.SIZES_MM.items(), key=lambda kv: kv[1][0] * kv[1][1]
    ):
        usable_w = max(pw, ph) - plan.margin * 2.0
        usable_h = min(pw, ph) - plan.margin * 2.0
        if (
            (drawing.width_mm <= usable_h and drawing.height_mm <= usable_w)
            or (drawing.width_mm <= usable_w and drawing.height_mm <= usable_h)
        ):
            lines.append(f"{name} なら1枚で収まります")
            break

    return lines


def active_tool_text(context):
    """いま動いているマーキング道具の案内。無ければ None。

    道具はトグルで、押したあとも画面に手応えが無かった。動いて
    いるのかどうかが分からないという声があったので、状態と
    止め方を出す。
    """
    from ..marking import interact as _interact

    labels = {
        "NOTCH": "合印",
        "NUMBER": "番号",
        "TEXT": "文字",
        "ARROW": "矢印",
    }
    mode = _interact.active_tool(context.scene)
    name = labels.get(mode)
    if name is None:
        return None

    if mode == "ARROW":
        return f"{name}を配置中：始点と終点を順にクリック / Esc で終了"
    return f"{name}を配置中：置きたい場所をクリック / Esc で終了"


def source_required_hint(context):
    """手動で置く道具が使えない理由。使えるなら None。"""
    source = _objects.source_from_context(context)
    if source is None:
        source = _objects.seam_source(context)
    if source is None:
        return "元の3Dモデルか型紙を選ぶと、手動で置けます"
    return None


def notch_text(context):
    """パネルに出す合印の現状。

    設定を変えても見た目の変化が分かりにくく、効いているのか
    判断できないという声があったため、件数を出して手応えを返す。
    """
    source = _objects.seam_source(context)
    if source is None:
        source = _objects.source_from_context(context)

    if source is None or source.type != 'MESH':
        return "元モデルが未読み込み"

    if _objects.unfold_for_source(source) is None:
        return "型紙が未作成"

    auto = manual = 0
    for item in _storage.load(source):
        if item.get("type") != "notch_edge":
            continue
        if bool(item.get("auto", False)):
            auto += 1
        else:
            manual += 1

    if auto == 0 and manual == 0:
        return "合印なし（「作成 / 更新」を押してください）"

    text = f"合印 {auto + manual} 個"
    if manual:
        text += f"（オート {auto} / 手動 {manual}）"
    return text
