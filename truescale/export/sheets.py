"""集めた線を、紙の上へ並べる。

1枚に収まるならそのまま。収まらないなら分割して、貼り合わせる
ための目印を足す。出力が PDF でも PNG でも、ここが決めた内容は
同じになる。

■ 足す目印は3つだけ

  合わせの印 … 重なった帯の中。隣の紙の同じ印と重ねる
  実寸の目盛り … 刷ったものを定規で測るため
  タイル名     … 1-A、2-A…。紙を並べるときの手掛かり

多くしても読めない。貼るのに要るものだけにする。

■ 合わせの印は「両方の紙に写っている場所」に置く

以前は受け持ち範囲の四隅に十字を置いていたが、これは誤り。
隣の紙の四隅とは、型紙の上で別の位置を指す。

    1枚目の右端の十字 → 型紙の 194mm
    2枚目の左端の十字 → 型紙の 179mm

重ねると、ちょうど重ねしろのぶんずれる。合わせるための印が、
合わせると狂う印になっていた。

両方の紙に写っているのは重なった帯だけ。その中央に印を置けば、
重ねたときに必ず一致する。

■ 目印は必ず刷れる場所へ置く

余白はプリンタが刷れない縁なので、そこへ描いたものは出ない。
以前は目盛りを端から 3.2mm の位置に描いていた（余白 8mm の指定と
矛盾していた）。刷れる範囲の中に帯を確保し、そこへ置く。

■ 目盛りを必ず入れる理由

印刷のとき「用紙に合わせる」を選ぶと数パーセント縮む。分割の
計算がどれだけ正確でも、そこで全部台無しになる。しかも目では
気付けない。定規を当てて長さが合わなければ設定が違う、と
分かるようにしておく。

長さは紙に入る中で一番大きい切りのよい値を選び、その数値を
横に書く。書いていないと、何ミリのはずなのか分からず測りようが
ない。紙からはみ出させてもいけない。はがきに 100mm の目盛りを
描いて 84mm に切られていたことがあり、それを測ると「縮んでいる」
と誤解する。無いより悪い。

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

    def __init__(self, paper_w, paper_h, label="", margin=None):
        self.paper_w = paper_w
        self.paper_h = paper_h
        self.label = label
        self.lines = []
        # 実際に描いた目盛りの長さ（ミリ）。描いていなければ 0。
        # 線から推測すると、同じ太さの別の印と混ざる。
        self.ruler_mm = 0.0
        # 刷れる範囲。None なら切らない。
        self.printable = (
            None
            if margin is None
            else (margin, margin, paper_w - margin, paper_h - margin)
        )

    def add(self, x0, y0, x1, y1, color, width_mm):
        """線を1本足す。刷れる範囲からはみ出す分は切る。

        のりしろの分だけ広く描くので、そのままでは余白へ食い込む
        ことがある。余白はプリンタが刷れない縁なので、そこへ
        置いても出ない。切っておけば、画面で見たものと刷ったものが
        食い違わない。
        """
        if self.printable is not None:
            cut = _clip(x0, y0, x1, y1, self.printable)
            if cut is None:
                return
            x0, y0, x1, y1 = cut
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
    """合わせの印。十字と菱形。

    枠線や型紙の線と見分けが付くように、丸（菱形）を添える。
    重ねたとき、隣の紙の同じ印とぴたり重なる。
    """
    sheet.add(x - CROSS_MM, y, x + CROSS_MM, y, BLACK, 0.3)
    sheet.add(x, y - CROSS_MM, x, y + CROSS_MM, BLACK, 0.3)

    r = CROSS_MM * 0.55
    sheet.add(x - r, y, x, y + r, BLACK, 0.3)
    sheet.add(x, y + r, x + r, y, BLACK, 0.3)
    sheet.add(x + r, y, x, y - r, BLACK, 0.3)
    sheet.add(x, y - r, x - r, y, BLACK, 0.3)


def registration_points(plan_obj, col, row):
    """そのタイルに置く合わせの印の位置（紙の上のミリ）。

    隣との重なりの中央に置く。そこは両方の紙に写っているので、
    重ねれば必ず一致する。受け持ち範囲の四隅ではいけない。隣の
    紙の四隅とは、型紙の上で別の位置を指すため。

    1本の継ぎ目につき3点。1点だけだと、その点を中心に回してしまう。
    """
    if plan_obj is None or plan_obj.count <= 1:
        return []

    x0, y0, x1, y1 = plan_obj.window(col, row)
    origin_x, origin_y = plan_obj.page_origin()
    half = plan_obj.overlap * 0.5

    # 継ぎ目の位置（型紙の座標）。列の境目と行の境目。
    seam_x = [
        (index + 1) * plan_obj.step_w + half
        for index in range(plan_obj.cols - 1)
    ]
    seam_y = [
        (index + 1) * plan_obj.step_h + half
        for index in range(plan_obj.rows - 1)
    ]

    points = []

    def spread(low, high):
        span = high - low
        return [low + span * ratio for ratio in (0.2, 0.5, 0.8)]

    for x in seam_x:
        if not (x0 - 1e-9 <= x <= x1 + 1e-9):
            continue
        for y in spread(y0, y1):
            points.append((x - x0 + origin_x, y - y0 + origin_y))

    for y in seam_y:
        if not (y0 - 1e-9 <= y <= y1 + 1e-9):
            continue
        for x in spread(x0, x1):
            points.append((x - x0 + origin_x, y - y0 + origin_y))

    return points


# 目盛りに使う長さの候補。大きいものから試して、紙に入るものを選ぶ。
RULER_CHOICES = (RULER_MM, 50.0, 30.0, 20.0, 10.0)

# 長さを書く文字の大きさ
RULER_LABEL_MM = 4.5


def ruler_length(available_mm):
    """その幅に収まる、一番大きい切りのよい目盛りの長さ。

    数値を書く場所も要るので、その分を引いてから選ぶ。どれも
    入らなければ None。
    """
    for length in RULER_CHOICES:
        if length <= available_mm:
            return length
    return None


def _ruler(sheet, x, y, available_mm):
    """実寸の目盛り。10mm ごとに刻み、50mm ごとに長くする。

    長さは紙に入るものを選び、数値を横に書く。書かないと何ミリの
    はずなのか分からず、測っても意味がない。
    """
    length = ruler_length(available_mm)
    if length is None:
        return

    sheet.ruler_mm = length
    sheet.add(x, y, x + length, y, BLACK, 0.3)

    step = 10.0
    count = int(round(length / step))
    for i in range(count + 1):
        height = 4.0 if i % 5 == 0 else 2.0
        at = x + i * step
        sheet.add(at, y, at, y + height, BLACK, 0.3)

    # 数値は線の上へ。横に置くと、その分だけ目盛りを短くすることに
    # なる。目盛りは長いほど、縮みを見つけやすい。
    _text(
        sheet,
        f"{int(length)}MM",
        x + 1.0,
        y + 5.0,
        RULER_LABEL_MM,
    )


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


def single(drawing, paper_w, paper_h, margin=8.0, footer=12.0):
    """1枚に収める。収まらないなら None。"""
    if drawing is None:
        return None
    if (drawing.width_mm > paper_w - margin * 2.0
            or drawing.height_mm > paper_h - margin * 2.0 - footer):
        return None

    sheet = Sheet(paper_w, paper_h, margin=margin)

    # 1枚のときは紙の中央へ置く。左下に寄せると、余りが右上へ
    # まとまって扱いにくい。継ぎ目が無いので寄せてよい。
    # 分割するときは寄せない（継ぎ目の位置は型紙の左下が原点）。
    usable_w = paper_w - margin * 2.0
    usable_h = paper_h - margin * 2.0 - footer
    origin_x = margin + max(0.0, (usable_w - drawing.width_mm) * 0.5)
    origin_y = margin + footer + max(0.0, (usable_h - drawing.height_mm) * 0.5)

    for x0, y0, x1, y1, color, width in drawing.lines:
        sheet.add(x0 + origin_x, y0 + origin_y,
                  x1 + origin_x, y1 + origin_y, color, width)

    # 目盛りは刷れる範囲の中。余白へ描くと切れて出ない。
    _ruler(sheet, margin, margin + 2.0, paper_w - margin * 2.0)
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
                margin=plan_obj.margin,
            )

            # 受け持ち範囲より、のりしろの分だけ広く描く。
            # 境目でぴたりと切ると、重ねたときに線が続いているか
            # 分からない。
            box = (x0 - bleed, y0 - bleed, x1 + bleed, y1 + bleed)
            origin_x, origin_y = plan_obj.page_origin()

            for gx0, gy0, gx1, gy1, color, width in drawing.lines:
                cut = _clip(gx0, gy0, gx1, gy1, box)
                if cut is None:
                    continue
                sheet.add(
                    cut[0] - x0 + origin_x,
                    cut[1] - y0 + origin_y,
                    cut[2] - x0 + origin_x,
                    cut[3] - y0 + origin_y,
                    color,
                    width,
                )

            _decorate(sheet, plan_obj, col, row)
            sheets.append(sheet)

    return sheets


def _decorate(sheet, plan_obj, col, row):
    """貼り合わせに要る目印を足す。

    どれも刷れる範囲の中に置く。余白へ描いたものは出ない。
    """
    left, bottom = plan_obj.page_origin()
    width = plan_obj.content_w
    height = plan_obj.content_h
    right = left + width
    top = bottom + height

    # 受け持ち範囲の枠。ここを隣の紙と合わせる。
    sheet.add(left, bottom, right, bottom, GUIDE, 0.2)
    sheet.add(right, bottom, right, top, GUIDE, 0.2)
    sheet.add(right, top, left, top, GUIDE, 0.2)
    sheet.add(left, top, left, bottom, GUIDE, 0.2)

    # 合わせの印は、隣と重なっている帯の中。四隅ではない。
    for x, y in registration_points(plan_obj, col, row):
        _cross(sheet, x, y)

    # タイル名は左上。紙をめくりながら探せる位置。
    _text(sheet, sheet.label, left + 2.0, top - LABEL_MM - 2.0)

    # 目盛りとタイルの総数は下の帯。型紙の線と重ならず、かつ
    # 刷れる範囲に収まる。
    # タイルの総数を先に置き、残った幅で目盛りの長さを決める。
    count_text = f"{plan_obj.cols}X{plan_obj.rows}"
    count_width = LABEL_MM * 0.7 * 0.62 * (len(count_text) + 1)
    _text(
        sheet,
        count_text,
        right - count_width,
        plan_obj.margin + 2.0,
        LABEL_MM * 0.7,
    )
    _ruler(sheet, left, plan_obj.margin + 2.0, width - count_width - 4.0)
