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

■ 文字は線になっている

番号も型紙IDも、Blenderのフォント機能で一度メッシュにしてから
輪郭の線を取り出している。だからここへ来る時点では、ただの線。
PDFにフォントを埋め込まなくてよいのはこのため。
"""


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
    from . import outline as _outline

    scene = context.scene

    segments = _outline.current_finish_segments(context)
    box = _outline.bbox(segments)
    if box is None:
        return None

    min_x, min_y, _, _ = box

    def point(x, y):
        """Blender Unit の座標を、左下原点のミリへ。"""
        return (
            _mm(scene, _units, x - min_x),
            _mm(scene, _units, y - min_y),
        )

    lines = []

    # 1. 外周。型紙の形そのものなので、常に黒。
    outline_mm = OUTLINE_MM
    for (ax, ay), (bx, by) in segments:
        x0, y0 = point(ax, ay)
        x1, y1 = point(bx, by)
        lines.append((x0, y0, x1, y1, (0.0, 0.0, 0.0), outline_mm))

    source = _objects.source_from_context(context)
    unfold = _objects.unfold_for_source(source) if source else None

    if source is not None and unfold is not None:
        # 2. 合印と矢印。太さは設定どおり（実寸）。
        for wa, wb, color, width_mm in _compute.colored_segments(
            context, source, unfold
        ):
            x0, y0 = point(wa.x, wa.y)
            x1, y1 = point(wb.x, wb.y)
            lines.append((x0, y0, x1, y1, color, float(width_mm)))

        # 3〜5. 文字はすべて輪郭線として集める。
        for text, world_pos, size_mm, color, angle in _text_sources(
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

    width_mm = max(max(line[0], line[2]) for line in lines)
    height_mm = max(max(line[1], line[3]) for line in lines)

    return Drawing(lines, width_mm, height_mm)


def _text_sources(context, source, unfold):
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
