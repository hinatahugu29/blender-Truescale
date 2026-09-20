"""集めた線を、紙の上へ並べる。

1枚に収まるならそのまま。収まらないなら分割して、貼り合わせる
ための目印を足す。出力が PDF でも PNG でも、ここが決めた内容は
同じになる。

■ 足す目印は3つだけ

  位置合わせの十字 … 受け持ち範囲の四隅。隣の紙と重ねて合わせる
  実寸の目盛り     … 100mm。刷ったものを定規で測るため
  タイル名         … 1-A、2-A…。紙を並べるときの手掛かり

多くしても読めない。貼るのに要るものだけにする。

■ 目盛りを必ず入れる理由

印刷のとき「用紙に合わせる」を選ぶと数パーセント縮む。分割の
計算がどれだけ正確でも、そこで全部台無しになる。しかも目では
気付けない。定規を当てて 100mm でなければ設定が違う、と
分かるようにしておく。

■ 線は受け持ち範囲の外まで少し描く

タイルの境目でぴたりと切ると、重ねたときにどちらの線が正しいか
分からない。重ねしろの分まで描いておけば、線が続いていることを
目で確かめながら貼れる。
"""

RULER_MM = 100.0
CROSS_MM = 6.0
LABEL_MM = 7.0

GUIDE = (0.62, 0.62, 0.62)
BLACK = (0.0, 0.0, 0.0)


class Sheet:
    """1枚分の内容。座標はミリ、紙の左下が原点。"""

    def __init__(self, paper_w, paper_h, label=""):
        self.paper_w = paper_w
        self.paper_h = paper_h
        self.label = label
        self.lines = []

    def add(self, x0, y0, x1, y1, color, width_mm):
        self.lines.append((x0, y0, x1, y1, color, width_mm))


def _clip(x0, y0, x1, y1, box):
    """線を枠で切る。枠の外なら None。

    端点を動かすだけの素直な切り方（Liang-Barsky）。線の向きは
    変わらないので、切っても実寸はずれない。
    """
    left, bottom, right, top = box
    dx = x1 - x0
    dy = y1 - y0

    t0 = 0.0
    t1 = 1.0

    for p, q in (
        (-dx, x0 - left),
        (dx, right - x0),
        (-dy, y0 - bottom),
        (dy, top - y0),
    ):
        if abs(p) < 1e-12:
            if q < 0.0:
                return None
            continue
        t = q / p
        if p < 0.0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)

    if t0 > t1:
        return None

    return (
        x0 + dx * t0,
        y0 + dy * t0,
        x0 + dx * t1,
        y0 + dy * t1,
    )


def _cross(sheet, x, y):
    sheet.add(x - CROSS_MM, y, x + CROSS_MM, y, GUIDE, 0.25)
    sheet.add(x, y - CROSS_MM, x, y + CROSS_MM, GUIDE, 0.25)


def _ruler(sheet, x, y):
    """実寸 100mm の目盛り。10mm ごとに刻み、50mm は長くする。"""
    sheet.add(x, y, x + RULER_MM, y, BLACK, 0.3)
    for i in range(11):
        height = 4.0 if i % 5 == 0 else 2.0
        at = x + i * 10.0
        sheet.add(at, y, at, y + height, BLACK, 0.3)


def _text(sheet, text, x, y, size=LABEL_MM):
    """紙に出す短い記号を、線で描く。

    タイル名は 1-A のような英数字だけなので、7セグメント風の
    簡単な字形で足りる。Blenderのフォントを通すと一時オブジェクトの
    生成が枚数分走るので、ここは自前で持つ。
    """
    step = size * 0.62
    for index, char in enumerate(str(text)):
        _glyph(sheet, char, x + index * step, y, size)


_STROKES = {
    "0": ((0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0, 2, 0, 0)),
    "1": ((0.5, 0, 0.5, 2), (0.1, 1.7, 0.5, 2)),
    "2": ((0, 2, 1, 2), (1, 2, 1, 1), (1, 1, 0, 1), (0, 1, 0, 0), (0, 0, 1, 0)),
    "3": ((0, 2, 1, 2), (1, 2, 1, 0), (1, 0, 0, 0), (0, 1, 1, 1)),
    "4": ((0, 2, 0, 1), (0, 1, 1, 1), (1, 2, 1, 0)),
    "5": ((1, 2, 0, 2), (0, 2, 0, 1), (0, 1, 1, 1), (1, 1, 1, 0), (1, 0, 0, 0)),
    "6": ((1, 2, 0, 2), (0, 2, 0, 0), (0, 0, 1, 0), (1, 0, 1, 1), (1, 1, 0, 1)),
    "7": ((0, 2, 1, 2), (1, 2, 0.4, 0)),
    "8": ((0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0, 2, 0, 0), (0, 1, 1, 1)),
    "9": ((1, 1, 0, 1), (0, 1, 0, 2), (0, 2, 1, 2), (1, 2, 1, 0), (1, 0, 0, 0)),
    "-": ((0.1, 1, 0.9, 1),),
    "A": ((0, 0, 0.5, 2), (0.5, 2, 1, 0), (0.22, 0.85, 0.78, 0.85)),
    "B": ((0, 0, 0, 2), (0, 2, 0.9, 1.6), (0.9, 1.6, 0, 1),
          (0, 1, 0.9, 0.5), (0.9, 0.5, 0, 0)),
    "C": ((1, 1.8, 0, 1.6), (0, 1.6, 0, 0.4), (0, 0.4, 1, 0.2)),
    "D": ((0, 0, 0, 2), (0, 2, 0.9, 1.4), (0.9, 1.4, 0.9, 0.6), (0.9, 0.6, 0, 0)),
    "E": ((1, 2, 0, 2), (0, 2, 0, 0), (0, 0, 1, 0), (0, 1, 0.8, 1)),
    "F": ((1, 2, 0, 2), (0, 2, 0, 0), (0, 1, 0.8, 1)),
    "G": ((1, 1.8, 0, 1.6), (0, 1.6, 0, 0.4), (0, 0.4, 1, 0.2),
          (1, 0.2, 1, 1), (1, 1, 0.5, 1)),
    "H": ((0, 0, 0, 2), (1, 0, 1, 2), (0, 1, 1, 1)),
    "I": ((0.5, 0, 0.5, 2),),
    "J": ((1, 2, 1, 0.4), (1, 0.4, 0.2, 0.2)),
    "K": ((0, 0, 0, 2), (1, 2, 0, 1), (0, 1, 1, 0)),
    "L": ((0, 2, 0, 0), (0, 0, 1, 0)),
    "M": ((0, 0, 0, 2), (0, 2, 0.5, 1), (0.5, 1, 1, 2), (1, 2, 1, 0)),
    "N": ((0, 0, 0, 2), (0, 2, 1, 0), (1, 0, 1, 2)),
    "O": ((0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0, 2, 0, 0)),
    "P": ((0, 0, 0, 2), (0, 2, 1, 1.6), (1, 1.6, 0, 1.1)),
    "Q": ((0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0, 2, 0, 0),
          (0.6, 0.5, 1.1, -0.1)),
    "R": ((0, 0, 0, 2), (0, 2, 1, 1.6), (1, 1.6, 0, 1.1), (0.4, 1.1, 1, 0)),
    "S": ((1, 1.8, 0, 1.7), (0, 1.7, 0, 1.1), (0, 1.1, 1, 0.9),
          (1, 0.9, 1, 0.3), (1, 0.3, 0, 0.2)),
    "T": ((0, 2, 1, 2), (0.5, 2, 0.5, 0)),
    "U": ((0, 2, 0, 0.3), (0, 0.3, 1, 0.3), (1, 0.3, 1, 2)),
    "V": ((0, 2, 0.5, 0), (0.5, 0, 1, 2)),
    "W": ((0, 2, 0.25, 0), (0.25, 0, 0.5, 1.2), (0.5, 1.2, 0.75, 0),
          (0.75, 0, 1, 2)),
    "X": ((0, 2, 1, 0), (0, 0, 1, 2)),
    "Y": ((0, 2, 0.5, 1), (1, 2, 0.5, 1), (0.5, 1, 0.5, 0)),
    "Z": ((0, 2, 1, 2), (1, 2, 0, 0), (0, 0, 1, 0)),
}


def _glyph(sheet, char, x, y, size):
    strokes = _STROKES.get(str(char).upper())
    if not strokes:
        return
    scale = size / 2.0
    for ax, ay, bx, by in strokes:
        sheet.add(
            x + ax * scale * 0.9,
            y + ay * scale,
            x + bx * scale * 0.9,
            y + by * scale,
            BLACK,
            0.4,
        )


def single(drawing, paper_w, paper_h, margin=8.0):
    """1枚に収める。収まらないなら None。"""
    if drawing is None:
        return None
    if (drawing.width_mm > paper_w - margin * 2.0
            or drawing.height_mm > paper_h - margin * 2.0):
        return None

    sheet = Sheet(paper_w, paper_h)
    for x0, y0, x1, y1, color, width in drawing.lines:
        sheet.add(x0 + margin, y0 + margin, x1 + margin, y1 + margin,
                  color, width)

    _ruler(sheet, margin, margin * 0.4)
    return [sheet]


def tiled(drawing, plan_obj):
    """分割して並べる。左上のタイルから順に返す。

    紙を並べた見た目と同じ順にするので、印刷したものを上から
    重ねていけば、そのまま左上から右下の順になる。
    """
    if drawing is None or plan_obj is None:
        return []

    sheets = []
    bleed = plan_obj.overlap

    for row in range(plan_obj.rows - 1, -1, -1):
        for col in range(plan_obj.cols):
            x0, y0, x1, y1 = plan_obj.window(col, row)
            sheet = Sheet(
                plan_obj.paper_w,
                plan_obj.paper_h,
                plan_obj.label(col, row),
            )

            # 受け持ち範囲より、のりしろの分だけ広く描く。
            # 境目でぴたりと切ると、重ねたときに線が続いているか
            # 分からない。
            box = (x0 - bleed, y0 - bleed, x1 + bleed, y1 + bleed)
            margin = plan_obj.margin

            for gx0, gy0, gx1, gy1, color, width in drawing.lines:
                cut = _clip(gx0, gy0, gx1, gy1, box)
                if cut is None:
                    continue
                sheet.add(
                    cut[0] - x0 + margin,
                    cut[1] - y0 + margin,
                    cut[2] - x0 + margin,
                    cut[3] - y0 + margin,
                    color,
                    width,
                )

            _decorate(sheet, plan_obj, col, row)
            sheets.append(sheet)

    return sheets


def _decorate(sheet, plan_obj, col, row):
    """貼り合わせに要る目印を足す。"""
    margin = plan_obj.margin
    width = plan_obj.content_w
    height = plan_obj.content_h

    # 受け持ち範囲の枠。ここを隣の紙と合わせる。
    sheet.add(margin, margin, margin + width, margin, GUIDE, 0.2)
    sheet.add(margin + width, margin, margin + width, margin + height,
              GUIDE, 0.2)
    sheet.add(margin + width, margin + height, margin, margin + height,
              GUIDE, 0.2)
    sheet.add(margin, margin + height, margin, margin, GUIDE, 0.2)

    for x, y in (
        (margin, margin),
        (margin + width, margin),
        (margin, margin + height),
        (margin + width, margin + height),
    ):
        _cross(sheet, x, y)

    # タイル名は左上。紙をめくりながら探せる位置。
    _text(sheet, sheet.label, margin + 2.0, margin + height - LABEL_MM - 2.0)

    # 目盛りは下の余白。型紙の線と重ならないところ。
    _ruler(sheet, margin, margin * 0.4)

    # 何枚のうちの何枚目か。紙が散らばっても戻せる。
    _text(
        sheet,
        f"{plan_obj.cols}X{plan_obj.rows}",
        margin + width - LABEL_MM * 2.6,
        margin * 0.4,
        LABEL_MM * 0.7,
    )
