"""線の種類を、線そのものの形で表す。

切る線と折る線は別のものとして刷らなければならない。折る線を
実線で刷ると、そこで切られる。糊代を足すなら、その根元は必ず
折り線になるので、これが前提になる。

■ なぜ「破線という属性」にしなかったか

PDF には破線の指定（d 演算子）がある。しかしそれを使うと、
線の並びに「種類」という列がもう1つ増え、集める側・切る側・
並べる側・PNG 側・PDF 側の5箇所を通すことになる。しかも PNG 側は
ラスタなので、結局そこで破線を自前で刻む処理が要る。同じことを
2通りに書くと、いつか食い違う。

ここで線分そのものを刻んでしまえば、下流はただの線として扱える。
切り取りも、タイルへの割り振りも、PNG も PDF も、何も変えずに
正しく動く。100mm の折り線が17本に増えるが、その程度は
どちらの出力でも問題にならない。

■ 刻みは必ず2本以上にする

1本だと隙間が無く、実線と同じものが出てくる。折り線が実線で
刷られると、そこで切られてタブが落ちる。破線にした意味が無い。

短い線では刻みが 4mm より細かくなるが、それでよい。長さを
守ることより、実線と見分けが付くことのほうが大事。

■ 端は必ず「線」で終わらせる

折り線の両端は、たいてい角である。そこが隙間で終わると、
どこで折るのかが分からなくなる。刻みの長さを少し伸び縮みさせて、
線で始まり線で終わるように合わせる。指定した 4mm ちょうどには
ならないが、読み手にとって大事なのは端のほうである。
"""

import math

# 折り線の刻み（ミリ）。長い線と短い線を描き分けたいわけではなく、
# 実線と区別が付けばよいので、はっきり見える長さにする。
FOLD_DASH_MM = 4.0
FOLD_GAP_MM = 2.0

# これより短い線は刻まない。
#
# 以前は 3mm にしていたが、糊代の根元は両端を詰めるぶん短くなる
# ので、短い辺では 2mm ほどの折り線ができる。それを実線で刷ると
# 切られる。300dpi なら 0.5mm の刻みでも 6 ピクセルあって見分けが
# 付くので、下げた。ここより短い線は、刻んでも刻まなくても点。
MIN_DASHED_MM = 1.5


def dashed(x0, y0, x1, y1, dash_mm=FOLD_DASH_MM, gap_mm=FOLD_GAP_MM):
    """1本の線を、破線の形をした線の一覧へ刻む。

    座標はミリ。戻り値は (x0, y0, x1, y1) の一覧。短すぎる線は
    刻まず、そのまま1本で返す。
    """
    dx = float(x1) - float(x0)
    dy = float(y1) - float(y0)
    length = math.hypot(dx, dy)

    if length < MIN_DASHED_MM or dash_mm <= 0.0:
        return [(x0, y0, x1, y1)]

    period = float(dash_mm) + max(0.0, float(gap_mm))

    # 線で始まり線で終わる本数。n本の線と n-1 個の隙間で埋める。
    #
    # 下限は2本。1本だと隙間が無く、実線と見分けが付かない。
    # 刻みが 4mm + 2mm なので、7mm くらいまでの線が全部そうなって
    # いた。折り線が実線で刷られると、そこで切られる。曲面の
    # シームでは辺が短くなりがちなので、たいていの糊代が当たる。
    count = max(2, int(round((length + gap_mm) / period)))

    span = count * dash_mm + (count - 1) * gap_mm
    scale = length / span if span > 0.0 else 1.0
    step_on = dash_mm * scale
    step_off = gap_mm * scale

    ux = dx / length
    uy = dy / length

    out = []
    at = 0.0
    for _ in range(count):
        end = min(length, at + step_on)
        out.append((
            x0 + ux * at,
            y0 + uy * at,
            x0 + ux * end,
            y0 + uy * end,
        ))
        at = end + step_off

    return out


def dashed_line(x0, y0, x1, y1, color, width_mm,
                dash_mm=FOLD_DASH_MM, gap_mm=FOLD_GAP_MM):
    """折り線を、集める側がそのまま足せる形で返す。

    戻り値は (x0, y0, x1, y1, 色, 太さmm) の一覧。collect が
    持っている線の並びと同じ組なので、足すだけでよい。
    """
    return [
        (ax, ay, bx, by, color, width_mm)
        for ax, ay, bx, by in dashed(x0, y0, x1, y1, dash_mm, gap_mm)
    ]
