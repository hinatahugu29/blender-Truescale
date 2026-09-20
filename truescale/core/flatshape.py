"""縫い代と糊代の形を、平面の幾何として作る。

ここは純粋な幾何だけを扱う。bpy を読み込まないので、Blender の
外でそのまま試せる。メッシュや設定の読み出しは export.allowance の
担当で、そちらがここへ座標を渡す。

■ 縫い代と糊代は別物

            縫い代                  糊代（タブ）
  形        輪郭全体を外へ一定量     辺ごとに外向きの台形を1枚
  付く側    両方の型紙              片方だけ
  線の意味  外＝裁断線、内＝縫い線   外周＝裁断線、根元＝折り線
  寸法の元  実物の寸法              紙の上の寸法

寸法の元が違うのが効く。縮尺 1:2 のとき、紙の上の 5mm のタブは
実物では 10mm になる。紙を糊付けするのだから糊代は紙の寸法、
布を縫うのだから縫い代は実物の寸法。1つの設定にまとめると、
どちらかが必ず狂う。

■ 輪は呼ぶ側が組む

境界の線分を輪へつなぐのは export.allowance の仕事。あちらは
辺番号を持ったまま輪にする必要がある（この境界辺は元のどの
シーム辺か、を引くため）。座標だけでつなぐ版をここにも置くと、
同じことが2通りになり、いつか食い違う。

■ タブが重なったら細くする

隣り合う辺の両方にタブが付くと、凹んだ角では重なる。角度から
逃がす量を出す方法もあるが、離れた辺どうしが重なる形（細い
くびれなど）は拾えない。実際に交わるかどうかを見て、交われば
細くし、それでも駄目なら諦める。判定が形に依存しないので、
どんな型紙が来ても破綻しない。
"""

import math

# タブの端の逃がし（ミリ）。角で隣のタブと触れないようにする。
TAB_GAP_MM = 0.8

# タブを細くするときの下限（ミリ）。これ以下は糊付けできない。
TAB_MIN_MM = 1.5


def loop_area(loop):
    """輪の符号付き面積。正なら反時計回り。"""
    total = 0.0
    count = len(loop)
    for i in range(count):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % count]
        total += x0 * y1 - x1 * y0
    return total * 0.5


def point_in_loop(point, loop):
    """点が輪の内側にあるか。向きは問わない。"""
    x, y = point
    inside = False
    count = len(loop)
    for i in range(count):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % count]
        if (y0 > y) != (y1 > y):
            span = y1 - y0
            if abs(span) > 1e-12:
                at = x0 + (y - y0) * (x1 - x0) / span
                if at > x:
                    inside = not inside
    return inside


def loop_windings(loops):
    """輪ごとに「反時計回りであるべきか」を返す。

    一番大きい輪を外周とし、その内側にある輪は穴とみなす。外周は
    反時計回り、穴は時計回りにすると、どちらも「進行方向の右が
    材料の外」になる。穴の内側には材料が無いので、穴にとっての外は
    穴の中である。これで外周も穴も同じ式で扱える。

    島に穴が開くのは、元の立体に貫通した穴があるときだけで珍しい。
    ただし、向きを揃え損ねると縫い代が材料の側へ広がり、切ると
    型紙が壊れる。安いので常に確かめる。

    戻り値は loops と同じ並びの真偽値。向きを直すのは呼ぶ側の
    仕事で、ここでは判断だけを返す。点の並びと、点＋辺番号の並びの
    両方から使うため、形を限定しない。
    """
    if not loops:
        return []

    areas = [abs(loop_area(loop)) for loop in loops]
    outer_at = areas.index(max(areas))
    outer = loops[outer_at]

    return [
        not (index != outer_at and point_in_loop(loop[0], outer))
        for index, loop in enumerate(loops)
    ]


def outward_normal(ax, ay, bx, by):
    """反時計回りの辺 a→b に対する外向きの単位法線。

    反時計回りなら内側は進行方向の左なので、外は右。
    """
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return (0.0, 0.0)
    return (dy / length, -dx / length)


def offset_loop(loop, distance, miter_limit=2.5):
    """輪を外へ一定量ずらす。裁断線になる。

    角は基本的に尖らせる（マイター）。ただし鋭い角では尖りが
    際限なく伸びるので、そこは切り落とす。伸びたままだと紙から
    はみ出し、裁断線としても意味を失う。

    向きは直さない。反時計回りなら外へ、時計回りなら内へずれる。
    穴の輪は時計回りで渡す（orient_loops が揃える）。
    """
    if distance <= 0.0 or len(loop) < 3:
        return list(loop)

    source = list(loop)
    count = len(source)
    out = []

    for i in range(count):
        ax, ay = source[i - 1]
        bx, by = source[i]
        cx, cy = source[(i + 1) % count]

        n1 = outward_normal(ax, ay, bx, by)
        n2 = outward_normal(bx, by, cx, cy)

        mx = n1[0] + n2[0]
        my = n1[1] + n2[1]
        length = math.hypot(mx, my)

        if length < 1e-9:
            # 折り返し。尖らせようがないので、そのまま押し出す。
            out.append((bx + n1[0] * distance, by + n1[1] * distance))
            continue

        mx /= length
        my /= length

        # マイターの伸び。法線どうしのなす角から出る。
        cos_half = mx * n1[0] + my * n1[1]

        if cos_half < 1e-6 or (1.0 / cos_half) > miter_limit:
            # 尖りすぎ。2点に分けて切り落とす。
            out.append((bx + n1[0] * distance, by + n1[1] * distance))
            out.append((bx + n2[0] * distance, by + n2[1] * distance))
            continue

        stretch = 1.0 / cos_half
        out.append((bx + mx * distance * stretch,
                    by + my * distance * stretch))

    return out


# --------------------------------------------------------------------- 糊代

def tab_quad(ax, ay, bx, by, width, gap=TAB_GAP_MM, taper=1.0):
    """辺 a→b の外側へ出すタブ。

    戻り値は4点。[根元a, 外側a, 外側b, 根元b] の順。根元の
    a→b が折り線で、残りの3辺が裁断線になる。

    端を少し詰めるのは、角で隣のタブと触れないようにするため。
    外側を斜めに詰めるのは、折って差し込むときに引っ掛からない
    ようにするため。紙の模型の糊代は、みなこの形をしている。

    辺が短くて台形にならない場合は None。無理に作ると裏返る。
    """
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length < 1e-9 or width <= 0.0:
        return None

    ux = dx / length
    uy = dy / length
    nx, ny = outward_normal(ax, ay, bx, by)

    # 根元。両端を gap だけ詰める。
    base = min(gap, length * 0.25)
    root_ax = ax + ux * base
    root_ay = ay + uy * base
    root_bx = bx - ux * base
    root_by = by - uy * base

    inner = length - base * 2.0
    if inner <= 1e-9:
        return None

    # 外側。斜めに詰める。詰めすぎて裏返らないよう抑える。
    slant = min(width * taper, inner * 0.4)

    top_ax = root_ax + ux * slant + nx * width
    top_ay = root_ay + uy * slant + ny * width
    top_bx = root_bx - ux * slant + nx * width
    top_by = root_by - uy * slant + ny * width

    return [
        (root_ax, root_ay),
        (top_ax, top_ay),
        (top_bx, top_by),
        (root_bx, root_by),
    ]


def tab_cut_edges(quad):
    """タブの裁断線。根元（折り線）は含まない。"""
    return [
        (quad[0][0], quad[0][1], quad[1][0], quad[1][1]),
        (quad[1][0], quad[1][1], quad[2][0], quad[2][1]),
        (quad[2][0], quad[2][1], quad[3][0], quad[3][1]),
    ]


def tab_fold_edge(quad):
    """タブの根元。ここは折る線であって、切る線ではない。"""
    return (quad[0][0], quad[0][1], quad[3][0], quad[3][1])


def _crosses(p0, p1, q0, q1):
    """線分どうしが交わるか。端どうしが触れるだけなら交わらない扱い。"""

    def side(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0]))

    d1 = side(p0, p1, q0)
    d2 = side(p0, p1, q1)
    d3 = side(q0, q1, p0)
    d4 = side(q0, q1, p1)

    eps = 1e-9
    if abs(d1) < eps and abs(d2) < eps:
        return False

    straddles_first = (d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)
    straddles_second = (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    return straddles_first and straddles_second


class SegmentIndex:
    """線分をマス目に振り分けて持つ。近いものだけを取り出すため。

    タブが置けるかは、ほかの線と交わるかで決める。素直に総当たりで
    試すと、辺の数の2乗に効く。面320の型紙で 2.2 秒かかった。
    ほとんどの組は遠く離れていて、見るまでもない。

    マス目は粗くてよい。タブの大きさの数倍にしておけば、1つの
    タブが見るマスは常に数個で済む。
    """

    __slots__ = ("cell", "buckets")

    def __init__(self, cell):
        self.cell = max(float(cell), 1e-6)
        self.buckets = {}

    def _cells(self, min_x, min_y, max_x, max_y):
        cell = self.cell
        x0 = int(math.floor(min_x / cell))
        x1 = int(math.floor(max_x / cell))
        y0 = int(math.floor(min_y / cell))
        y1 = int(math.floor(max_y / cell))
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                yield (cx, cy)

    def add(self, segment):
        ax, ay, bx, by = segment
        for spot in self._cells(
            min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)
        ):
            self.buckets.setdefault(spot, []).append(segment)

    def extend(self, segments):
        for segment in segments:
            self.add(segment)

    def near(self, min_x, min_y, max_x, max_y):
        """その範囲にかかっている線分。重複は取り除く。"""
        found = {}
        for spot in self._cells(min_x, min_y, max_x, max_y):
            for segment in self.buckets.get(spot, ()):
                found[id(segment)] = segment
        return found.values()


def quad_hits(quad, obstacles, skip=None):
    """タブが、ほかの線に触れているか。

    隣り合う辺どうしだけを角度で見る方法もあるが、細いくびれの
    ように離れた辺と重なる形は拾えない。実際に交わるかを見る。

    obstacles は SegmentIndex でも、線分の並びでもよい。skip は
    見ないでおく線分（ふつうはタブ自身の根元の辺）。
    """
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]

    if isinstance(obstacles, SegmentIndex):
        nearby = obstacles.near(min(xs), min(ys), max(xs), max(ys))
    else:
        nearby = obstacles

    if skip is not None:
        nearby = [s for s in nearby if s != skip]

    edges = [
        ((x0, y0), (x1, y1)) for x0, y0, x1, y1 in tab_cut_edges(quad)
    ]

    for ox0, oy0, ox1, oy1 in nearby:
        q0 = (ox0, oy0)
        q1 = (ox1, oy1)
        for p0, p1 in edges:
            if _crosses(p0, p1, q0, q1):
                return True
    return False


def fit_tab(ax, ay, bx, by, width, obstacles,
            minimum=TAB_MIN_MM, gap=TAB_GAP_MM, skip=None):
    """置けるだけの太さでタブを作る。置けなければ None。

    希望の太さで交わるなら、細くして試す。下限まで細くしても
    交わるなら諦める。無理に置いたタブは、切ると型紙自体を
    切ってしまう。
    """
    attempt = float(width)
    while attempt >= minimum:
        quad = tab_quad(ax, ay, bx, by, attempt, gap=gap)
        if quad is not None and not quad_hits(quad, obstacles, skip):
            return quad
        attempt *= 0.6
    return None
