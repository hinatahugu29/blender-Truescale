"""展開でどれだけ縮んだかを、辺ごとの倍率から数字にする。

■ どこから来る数字か

型紙の実寸は「元の辺の実長 ÷ UV上の辺の長さ」を全辺で取り、その
中央値を倍率として決めている（unfold/build.py）。この比は辺ごとに
ばらつく。ばらつきが、そのまま展開の歪みになる。

倍率を決めたあと、比のリストは捨てられていた。捨てずに見れば
歪み率になる。新しく計算するものは何もない。

■ 縮む側にしか間違えない

平面へ投影する以上、傾いた辺は必ず短く写る。だから比は中央値より
大きい側へ伸びる。実測でも最小は 0〜-5%、最大だけが +80% を超えた。

  実測（Smart UV Project、中央値を 0% として）
    立方体   ±0.0%
    円柱     -0.0% 〜 +80%
    UV球     -3.8% 〜 +85%
    Suzanne  -5.5% 〜 +293%

つまり型紙は実物より小さく出る。切って組むと「少し足りない」に
なる。余る方向には外れない。

■ 最大値は出さない

1本の辺が極端でも実害は小さい。なのに最大値だけが独り歩きする。
半分以上の辺は 4% 程度に収まっていて、大きいのは一部だった。

出すのは中央値と上位5%。「シームを足すべきか」の判断に効くのは
こちらで、最大値ではない。
"""

# 縮みの目安。切る前に気づける言い方にする。
FINE_PCT = 5.0    # ここまでは気にしなくていい
ROUGH_PCT = 15.0  # ここを超えるとシームが足りない


def _percentile(sorted_values, fraction):
    """並べ済みの列から、下から fraction の位置の値を取る。

    補間はしない。辺の本数が数千あるので、隣とほぼ変わらない。
    """
    if not sorted_values:
        return 0.0
    index = int(len(sorted_values) * fraction)
    return sorted_values[min(index, len(sorted_values) - 1)]


def stats(ratios, scale):
    """辺ごとの倍率から、縮み率（％）を出す。

    ratios  辺ごとの「実長 ÷ UV長」
    scale   そのうち採用した倍率（＝中央値）

    返すのは (中央, 上位5%) の％。測れないときは None。
    """
    if not ratios or not scale or scale <= 0.0:
        return None

    # 中央値からどれだけ外れているか。縮む側しか出ないが、
    # 絶対値を取っておく。UV を手で作れば逆も起こりうる。
    off = sorted(abs(float(r) / float(scale) - 1.0) * 100.0 for r in ratios)

    return (_percentile(off, 0.5), _percentile(off, 0.95))


def of(unfold_obj):
    """型紙に記録された縮み率を読む。無ければ None。

    作る前の型紙、古い型紙には入っていない。その場合は黙る。
    測っていないのに 0% と出すと、歪んでいないと読める。
    """
    if unfold_obj is None:
        return None

    made = unfold_obj.get("tsunfold_distortion")
    if made is None or len(made) < 2:
        return None

    try:
        return (float(made[0]), float(made[1]))
    except (TypeError, ValueError):
        return None


def verdict(high_pct):
    """上位5%の縮みから、どうすべきかを一言で。

    判断に使うのは上位5%のほう。中央値が小さくても、一部が大きく
    縮んでいればそこで合わなくなる。
    """
    if high_pct < FINE_PCT:
        return ""
    if high_pct < ROUGH_PCT:
        return "曲面が大きめです。紙ならシームを足すと合いやすくなります"
    return "シームが足りません。曲面を1枚で展開しようとしています"


def text(made):
    """パネルと同じ文言。数字の見せ方を1か所に寄せるため。

    made は stats() の返り値。None なら空。
    """
    if not made:
        return ""
    middle, high = made
    return f"縮み 中央 {middle:.1f}% / 大きいところ {high:.1f}%"
