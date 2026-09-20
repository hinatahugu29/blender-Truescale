"""紙に出るものを、ミリの線として1つの並びに集める。

輪郭・合印・矢印・番号・文字・型紙ID、書き出すもの全部をここで
集める。集めたあと、PNGにするかPDFにするか、1枚にするか分割するかは
それぞれの担当が決める。

■ なぜ集める層を分けたか

以前は書き出しオペレータの中で、5種類それぞれについて
「Blender Unit → ミリ → ピクセル」の換算を書いていた。同じ式が
5回あり、1つ直し忘れればそこだけずれる。分割を足すと、その5つが
さらにタイルごとに増える。

集めるところまでをミリで済ませておけば、あとは出力先ごとに
1回換算するだけになる。

■ 原点は型紙の左下

集めた時点で、型紙を囲む最小の矩形の左下が (0, 0) になるよう
ずらしてある。分割の計算も同じ前提なので、そのまま渡せる。

■ 大きさだけなら安く求まる

パネルや用紙ガイドが要るのは「型紙が何ミリか」だけで、線そのものは
要らない。pattern_lines は文字の輪郭を起こすために一時的な FONT
オブジェクトを作るので、毎フレーム呼べるものではない（1回 350ms）。

extent はそれを避け、文字は占める範囲の見積もりで済ませる。
少し大きめに見るので、実際より枚数が減ることはない。

■ 文字は線になっている

番号も型紙IDも、Blenderのフォント機能で一度メッシュにしてから
輪郭の線を取り出している。だからここへ来る時点では、ただの線。
PDFにフォントを埋め込まなくてよいのはこのため。
"""


from mathutils import Vector


# 外周と文字の線の太さ（ミリ）。設定にはしていない。
# 細すぎると切る線が見えず、太すぎると切る位置が曖昧になる。
# 型紙として実用になる範囲は狭いので、決め打ちでよい。
OUTLINE_MM = 0.4


class Drawing:
    """紙に出る線の集まり。座標はミリ、左下が原点。"""

    def __init__(self, lines, width_mm, height_mm):
        self.lines = lines
        self.width_mm = width_mm
        self.height_mm = height_mm

    def __bool__(self):
        return bool(self.lines)

    @property
    def count(self):
        return len(self.lines)


def _mm(scene, units, value):
    return units.scene_bu_to_mm(scene, value)


def pattern_lines(context):
    """いま書き出せる型紙を、ミリの線として集める。

    戻り値は Drawing。線は (x0, y0, x1, y1, 色, 太さmm) の組。
    書き出せるものが無ければ None。
    """
    from ..core import objects as _objects
    from ..core import units as _units
    from ..marking import compute as _compute
    from . import allowance as _allowance
    from . import linestyle as _linestyle
    from . import outline as _outline

    scene = context.scene

    source = _objects.source_from_context(context)
    unfold = _objects.unfold_for_source(source) if source else None

    # 縫い代と糊代は、外周より先に求める。タブが付いた辺は外周から
    # 外さなければならず、外周を集めるときに渡す必要があるため。
    allow = _allowance.build(context, source, unfold)
    extra = _world_allowance(unfold, allow)

    segments = _outline.current_finish_segments(
        context, skip=allow.suppress
    )
    box = _outline.bbox(segments)

    if extra["cut"] or extra["fold"]:
        points = [
            (v[0], v[1]) for v in extra["cut"] + extra["fold"]
        ] + [
            (v[2], v[3]) for v in extra["cut"] + extra["fold"]
        ]
        if box is None:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            box = (min(xs), min(ys), max(xs), max(ys))
        else:
            box = (
                min(box[0], min(p[0] for p in points)),
                min(box[1], min(p[1] for p in points)),
                max(box[2], max(p[0] for p in points)),
                max(box[3], max(p[1] for p in points)),
            )

    if box is None:
        return None

    min_x, min_y, _, _ = box

    # 型紙のまわりに空ける余白。外形に含めてしまえば、枚数の計算も
    # 用紙ガイドの位置も、何も変えずに正しくなる。ガイドの枠は
    # 型紙の左下に合わせて置かれるので、外形が広がればそのぶん
    # 枠が外へ出て、間に隙間ができる。
    inset = max(0.0, float(
        getattr(scene, "tsunfold_pattern_inset_mm", 0.0)
    ))

    def point(x, y):
        """Blender Unit の座標を、左下原点のミリへ。余白のぶんずらす。"""
        return (
            _mm(scene, _units, x - min_x) + inset,
            _mm(scene, _units, y - min_y) + inset,
        )

    lines = []
    BLACK = (0.0, 0.0, 0.0)

    # 1. 外周。型紙の形そのものなので、常に黒。
    #
    #    縫い代を付けたときだけ、ここは裁断線ではなく縫い線になる。
    #    切る線は外側へずれた輪のほうなので、元の輪は破線にする。
    #    実線のままだと、どちらを切るのか区別が付かない。
    outline_mm = OUTLINE_MM
    for (ax, ay), (bx, by) in segments:
        x0, y0 = point(ax, ay)
        x1, y1 = point(bx, by)
        if allow.stitched:
            lines.extend(
                _linestyle.dashed_line(x0, y0, x1, y1, BLACK, outline_mm)
            )
        else:
            lines.append((x0, y0, x1, y1, BLACK, outline_mm))

    # 1b. 縫い代・糊代。切る線は実線、折る線は破線。
    for ax, ay, bx, by in extra["cut"]:
        x0, y0 = point(ax, ay)
        x1, y1 = point(bx, by)
        lines.append((x0, y0, x1, y1, BLACK, outline_mm))

    for ax, ay, bx, by in extra["fold"]:
        x0, y0 = point(ax, ay)
        x1, y1 = point(bx, by)
        lines.extend(
            _linestyle.dashed_line(x0, y0, x1, y1, BLACK, outline_mm)
        )

    if source is not None and unfold is not None:
        # 2. 合印と矢印。太さは設定どおり（実寸）。
        for wa, wb, color, width_mm in _compute.colored_segments(
            context, source, unfold
        ):
            x0, y0 = point(wa.x, wa.y)
            x1, y1 = point(wb.x, wb.y)
            lines.append((x0, y0, x1, y1, color, float(width_mm)))

        # 3〜5. 文字はすべて輪郭線として集める。
        for text, world_pos, size_mm, color, angle in text_sources(
            context, source, unfold
        ):
            for wa, wb in _outline.text_segments(
                context, text, world_pos, size_mm, angle
            ):
                x0, y0 = point(wa.x, wa.y)
                x1, y1 = point(wb.x, wb.y)
                lines.append((x0, y0, x1, y1, color, outline_mm))

    if not lines:
        return None

    width_mm = max(max(line[0], line[2]) for line in lines) + inset
    height_mm = max(max(line[1], line[3]) for line in lines) + inset

    return Drawing(lines, width_mm, height_mm)


def _world_allowance(unfold, allow):
    """縫い代・糊代の線を、型紙のローカルからワールドへ直す。

    幾何はローカルで計算している。型紙を動かしても形は変わらない
    ので、そのほうが使い回しが効く。ここで一度だけ変換する。
    """
    empty = {"cut": [], "fold": []}
    if unfold is None or not allow:
        return empty

    matrix = unfold.matrix_world

    def move(items):
        out = []
        for ax, ay, bx, by in items:
            pa = matrix @ Vector((ax, ay, 0.0))
            pb = matrix @ Vector((bx, by, 0.0))
            out.append((pa.x, pa.y, pb.x, pb.y))
        return out

    return {"cut": move(allow.cut), "fold": move(allow.fold)}


def text_sources(context, source, unfold):
    """紙に出る文字を、(文字, 位置, 大きさ, 色, 角度) で順に返す。

    出どころが3つある。まとめておかないと、書き出しの経路が
    増えるたびに3つとも書くことになる。

      手で置いた番号・文字 … 元モデル側に持つ注記
      型紙へのメモ         … その紙だけの書き込み
      自動の型紙ID         … 島ごとに振る記号と、接続先
    """
    from ..marking import compute as _compute
    from .. import overlay as _overlay

    for text, world_pos, size_mm, color in _overlay.flat_text_items(
        source, unfold, context.scene
    ):
        yield (text, world_pos, size_mm, color, 0.0)

    for text, world_pos, size_mm, color, angle in (
        _overlay.flat_memo_text_items(unfold)
    ):
        yield (text, world_pos, size_mm, color, angle)

    if bool(getattr(context.scene, "tsunfold_auto_island_ids", True)):
        for (
            text,
            world_pos,
            size_mm,
            color,
            angle,
            _edge_locked,
        ) in _compute.text_items(context, source, unfold):
            yield (text, world_pos, size_mm, color, angle)


def pattern_bounds(context):
    """型紙の外形（min_x, min_y, max_x, max_y）。Blender Unit、ワールド。

    大きさだけでなく位置も要る場面がある。用紙ガイドは、分割の枠を
    型紙の左下へ合わせて並べる必要がある（書き出しと同じ基準で
    ないと、画面の枠と実際の切れ目がずれる）。
    """
    from ..core import objects as _objects
    from ..core import units as _units
    from ..marking import compute as _compute
    from . import outline as _outline

    scene = context.scene

    box = _outline.bbox(_outline.current_finish_segments(context))
    if box is None:
        return None

    min_x, min_y, max_x, max_y = box

    # 縫い代と糊代のぶん。実際に形を起こすと重いので、四方へ
    # 一番大きい幅だけ広げて見積もる。少し大きめに見るので、
    # 実際より枚数が減ることはない。減るほうへ間違えると、
    # 用紙ガイドが「収まる」と言ったものが刷ると収まらない。
    from . import allowance as _allowance

    conf = _allowance.settings(scene)
    pad_mm = 0.0
    if conf["tab"]:
        pad_mm = max(pad_mm, conf["tab_mm"])
    if conf["seam"]:
        pad_mm = max(pad_mm, conf["seam_mm"])

    # 型紙のまわりに空ける余白。pattern_lines と同じ値を足す。
    # ここが食い違うと、画面のガイドと刷ったものがずれる。
    pad_mm += max(0.0, float(
        getattr(scene, "tsunfold_pattern_inset_mm", 0.0)
    ))

    if pad_mm > 0.0:
        pad = _units.scene_mm_to_bu(scene, pad_mm)
        min_x -= pad
        min_y -= pad
        max_x += pad
        max_y += pad

    source = _objects.source_from_context(context)
    unfold = _objects.unfold_for_source(source) if source else None

    if source is not None and unfold is not None:
        for wa, wb, _color, _width in _compute.colored_segments(
            context, source, unfold
        ):
            min_x = min(min_x, wa.x, wb.x)
            max_x = max(max_x, wa.x, wb.x)
            min_y = min(min_y, wa.y, wb.y)
            max_y = max(max_y, wa.y, wb.y)

        for text, pos, size_mm, _color, _angle in text_sources(
            context, source, unfold
        ):
            # 文字の輪郭は起こさず、占める範囲を見積もる。回転して
            # いるかもしれないので、縦横とも長いほうで見る。
            reach = _units.scene_mm_to_bu(
                scene, float(size_mm) * max(1.0, len(str(text))) * 0.7
            )
            min_x = min(min_x, pos.x - reach)
            max_x = max(max_x, pos.x + reach)
            min_y = min(min_y, pos.y - reach)
            max_y = max(max_y, pos.y + reach)

    return (min_x, min_y, max_x, max_y)


def pattern_extent(context):
    """型紙の外形の大きさ（幅mm, 高さmm）。無ければ None。

    線を作らずに求める。パネルと用紙ガイドは毎フレーム呼ぶので、
    pattern_lines（文字の輪郭を起こす、1回 350ms）は使えない。
    """
    from ..core import units as _units

    box = pattern_bounds(context)
    if box is None:
        return None

    min_x, min_y, max_x, max_y = box
    scene = context.scene
    return (
        _units.scene_bu_to_mm(scene, max_x - min_x),
        _units.scene_bu_to_mm(scene, max_y - min_y),
    )
