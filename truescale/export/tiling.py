"""大きな型紙を、紙に収まる大きさへ分割する。

用紙より大きい型紙を、複数枚に分けて刷り、貼り合わせて元の実寸へ
戻すための計算。A4でもA3でも、用紙の寸法を渡せば同じように働く。

■ 詰め込みではなく、正確さを優先する

枚数を最小にすることは目的ではない。貼り合わせた結果が元の寸法と
一致することが目的。なので余白ものりしろもゆとりを持って取り、
そのぶん枚数が増えることは受け入れる。

■ 位置は必ず型紙の左下からの絶対値で出す

タイルを1枚ずつ「前のタイルの右隣」と決めていくと、丸めの誤差が
枚数だけ積み上がる。10枚並べれば10回分ずれる。

そうではなく、どのタイルも「型紙の左下から何ミリの位置か」で
決める。誤差は1枚あたり半ピクセル（300dpiで0.042mm）に留まり、
枚数が増えても積み上がらない。

■ 紙の端では合わせない

プリンタが刷れない余白の量は機種ごとに違う。紙の端を基準にすると、
その差がそのままずれになる。貼り合わせは印刷された位置合わせの
印で行う。だから、のりしろは「重ねしろ」であって「切りしろ」では
ない。切らずに重ねて貼れる。

■ 刷れる範囲の中に、型紙の場所と目印の場所を分けて持つ

  余白   … プリンタが刷れない縁。ここには何も描けない
  帯     … 目盛りとタイル名を置く場所。刷れる範囲の下側
  受け持ち… 型紙そのものが載る場所

目盛りを余白の中に描いてしまうと、刷ったときに切れて出ない。
縮尺を確かめるためのものなので、それでは意味がない。

■ 刷るときの拡大縮小が最大の敵

「用紙に合わせる」で刷ると数パーセント縮む。それだけで実寸が
崩れるが、目では気付けない。各タイルに実寸の目盛りを入れて、
定規で測れば分かるようにする（それは描画側の仕事）。
"""

import math


class Plan:
    """分割の計画。どのタイルが型紙のどの範囲を受け持つか。

    寸法は全てミリ。原点は型紙の左下。
    """

    def __init__(self, cols, rows, step_w, step_h, content_w, content_h,
                 margin, overlap, paper_w, paper_h, shape_w, shape_h,
                 footer):
        self.cols = cols
        self.rows = rows
        self.step_w = step_w
        self.step_h = step_h
        self.content_w = content_w
        self.content_h = content_h
        self.margin = margin
        self.overlap = overlap
        self.paper_w = paper_w
        self.paper_h = paper_h
        self.shape_w = shape_w
        self.shape_h = shape_h
        self.footer = footer

    @property
    def count(self):
        return self.cols * self.rows

    def window(self, col, row):
        """タイル(col, row)が受け持つ範囲 (x0, y0, x1, y1)。

        型紙の左下からの絶対位置。前のタイルからの積み上げでは
        求めない。丸めの誤差を貯めないため。
        """
        x0 = col * self.step_w
        y0 = row * self.step_h
        return (x0, y0, x0 + self.content_w, y0 + self.content_h)

    def page_origin(self):
        """紙の上で、受け持ち範囲の左下がどこに来るか。

        下には目盛りとタイル名の帯があるので、その分だけ上へ。
        """
        return (self.margin, self.margin + self.footer)

    def label(self, col, row):
        """人が貼り合わせるときの呼び名。左上を 1-A とする。

        行は上から数える。紙を並べたときの見た目と一致させないと、
        番号を見ながら並べられない。
        """
        return f"{col + 1}-{chr(ord('A') + (self.rows - 1 - row))}"

    def describe(self):
        return (
            f"{self.paper_w:.0f}×{self.paper_h:.0f} mm を "
            f"{self.cols}×{self.rows} = {self.count} 枚"
        )


def plan(shape_w, shape_h, paper_w, paper_h, margin=8.0, overlap=15.0,
         footer=12.0):
    """分割の計画を立てる。収まらない指定なら None。

    margin  … プリンタが刷れない余白。機種差を見込んで多めに取る
    overlap … 貼り合わせの重ねしろ。切らずに重ねて貼るための幅
    footer  … 目盛りとタイル名を置く帯。刷れる範囲の中に確保する

    のりしろを広げると枚数は増える。1枚が受け持つ幅がその分
    減るため。効き方は型紙の寸法しだいで、境目をまたぐ位置に
    よっては 15mm から 25mm へ変えただけで1段増えることもある。

        420×520  のりしろ 15mm → 6枚 / 25mm → 9枚
        580×280  のりしろ  0mm → 6枚 /  5mm → 8枚

    貼りやすさと枚数の釣り合いで決めること。
    """
    shape_w = float(shape_w)
    shape_h = float(shape_h)
    paper_w = float(paper_w)
    paper_h = float(paper_h)
    margin = max(0.0, float(margin))
    overlap = max(0.0, float(overlap))
    footer = max(0.0, float(footer))

    if shape_w <= 0.0 or shape_h <= 0.0:
        return None

    content_w = paper_w - margin * 2.0
    content_h = paper_h - margin * 2.0 - footer

    if content_w <= 0.0 or content_h <= 0.0:
        return None

    step_w = content_w - overlap
    step_h = content_h - overlap

    if step_w <= 0.0 or step_h <= 0.0:
        return None

    cols = max(1, math.ceil((shape_w - overlap) / step_w))
    rows = max(1, math.ceil((shape_h - overlap) / step_h))

    return Plan(
        cols=cols,
        rows=rows,
        step_w=step_w,
        step_h=step_h,
        content_w=content_w,
        content_h=content_h,
        margin=margin,
        overlap=overlap,
        paper_w=paper_w,
        paper_h=paper_h,
        shape_w=shape_w,
        shape_h=shape_h,
        footer=footer,
    )


def _sheets(found):
    return found.count if found is not None else math.inf


def plan_either_way(shape_w, shape_h, paper_w, paper_h, **options):
    """縦と横のうち、枚数の少ないほうの計画。同じなら渡した向き。

    向きを「自動」にしたとき、紙の外形で比べると間違える。余白と
    目盛りの帯を引いた残りで比べないと、外形には入るが刷れる範囲に
    入らない向きを選び、1枚で済むものを2枚にする。枚数そのもので
    比べれば、その食い違いは起きない。
    """
    given = plan(shape_w, shape_h, paper_w, paper_h, **options)
    turned = plan(shape_w, shape_h, paper_h, paper_w, **options)
    if _sheets(turned) < _sheets(given):
        return turned
    return given


def smallest_single_sheet(shape_w, shape_h, sizes, **options):
    """1枚で刷れる、いちばん小さい用紙の名前。無ければ None。

    sizes は (名前, (幅, 高さ)) を小さい順に並べたもの
    （core.paper.SIZES_BY_AREA）。向きはどちらでもよい。
    紙の外形ではなく、分割と同じ計算で1枚になるかを見る。
    外形で見ると、余白と帯のぶん「収まる」と言いすぎる。
    """
    for name, (paper_w, paper_h) in sizes:
        found = plan_either_way(shape_w, shape_h, paper_w, paper_h, **options)
        if found is not None and found.count == 1:
            return name
    return None


def covers(plan_obj, x, y):
    """型紙上の点 (x, y) が、少なくとも1枚のタイルに入っているか。

    抜けがあると、貼り合わせても穴が空く。
    """
    if plan_obj is None:
        return False

    for row in range(plan_obj.rows):
        for col in range(plan_obj.cols):
            x0, y0, x1, y1 = plan_obj.window(col, row)
            if x0 - 1e-9 <= x <= x1 + 1e-9 and y0 - 1e-9 <= y <= y1 + 1e-9:
                return True
    return False


def seam_overlap(plan_obj):
    """隣り合うタイルが実際に重なる幅 (横, 縦)。

    指定した overlap と一致するはず。ずれていたら計算が狂っている。
    """
    if plan_obj is None:
        return (0.0, 0.0)

    horizontal = plan_obj.content_w - plan_obj.step_w
    vertical = plan_obj.content_h - plan_obj.step_h
    return (horizontal, vertical)


def pixel_size(plan_obj, dpi):
    """タイル1枚の画像の大きさ（ピクセル）。

    紙そのものの大きさで作る。刷ったときに紙と1対1で対応させるため。
    """
    if plan_obj is None:
        return (0, 0)
    px_per_mm = float(dpi) / 25.4
    return (
        int(round(plan_obj.paper_w * px_per_mm)),
        int(round(plan_obj.paper_h * px_per_mm)),
    )


def to_pixels(plan_obj, col, row, x_mm, y_mm, dpi):
    """型紙上の位置を、タイル画像の中のピクセル位置へ直す。

    型紙の左下からの絶対位置を、そのタイルの受け持ち範囲を引いて
    求める。タイルを順に足していく形にしないこと。丸めの誤差が
    枚数だけ積み上がる。

    画像は左上が原点なので、縦は反転する。
    """
    px_per_mm = float(dpi) / 25.4
    x0, y0, _, _ = plan_obj.window(col, row)

    origin_x, origin_y = plan_obj.page_origin()
    local_x = (float(x_mm) - x0) + origin_x
    local_y = (float(y_mm) - y0) + origin_y

    _, height_px = pixel_size(plan_obj, dpi)

    return (
        local_x * px_per_mm,
        height_px - local_y * px_per_mm,
    )


def from_pixels(plan_obj, col, row, px, py, dpi):
    """to_pixels の逆。検証のために使う。"""
    px_per_mm = float(dpi) / 25.4
    x0, y0, _, _ = plan_obj.window(col, row)
    _, height_px = pixel_size(plan_obj, dpi)

    origin_x, origin_y = plan_obj.page_origin()
    local_x = float(px) / px_per_mm - origin_x
    local_y = (height_px - float(py)) / px_per_mm - origin_y

    return (local_x + x0, local_y + y0)


def tile_for(plan_obj, x_mm, y_mm):
    """その点を最もゆとりを持って含むタイル。無ければ None。

    重なり部分はどちらのタイルにも入る。端に近いところで切れると
    貼り合わせの手掛かりが減るので、中央に近い方を選ぶ。
    """
    if plan_obj is None:
        return None

    best = None
    best_margin = None

    for row in range(plan_obj.rows):
        for col in range(plan_obj.cols):
            x0, y0, x1, y1 = plan_obj.window(col, row)
            if not (x0 <= x_mm <= x1 and y0 <= y_mm <= y1):
                continue
            room = min(x_mm - x0, x1 - x_mm, y_mm - y0, y1 - y_mm)
            if best_margin is None or room > best_margin:
                best_margin = room
                best = (col, row)

    return best
