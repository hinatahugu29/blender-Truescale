"""型紙の注記（合印・型紙ID・矢印・メモ）の保存と読み出し。

注記は元モデルのカスタムプロパティに JSON でまとめて持たせる。
型紙オブジェクトではなく元モデル側に置くのは、型紙を作り直しても
手動で付けた印が残るようにするため。

■ 1件の形

    {"type": "notch_edge", "edge": 12, "t": 0.5, "auto": True}
    {"type": "text", "value": "前身頃", "color": [0, 0, 0], ...}

type ごとに持つ項目が違う。共通するのは type だけ。

■ 色の扱い

描くときの色は、保存値ではなく種類ごとのシーン設定から引く。
「合印の色」を変えたら合印が全部変わる、という読み方に合わせる。

色を保存値に持たせると、設定を変えたときに全件を書き直すことに
なる。カラーピッカーをドラッグしている間ずっと JSON の全書き出しが
走るので、保存はせず、描くときに引く。

置いた時点の色は今も保存しているが、描画には使わない。古い
ファイルを開いたときの手掛かりとして残してある。
"""

import json

from .. import debug as _debug

# 元モデルのカスタムプロパティ名
ANNOTATION_PROP = "tsunfold_annotations_json"

NOTCH = "notch_edge"
NUMBER = "number"
TEXT = "text"
ARROW = "arrow"

# 種類ごとの色設定。描くときはここから引く。
COLOR_PROP = {
    NOTCH: "tsunfold_notch_color",
    NUMBER: "tsunfold_number_color",
    TEXT: "tsunfold_text_color",
    ARROW: "tsunfold_arrow_color",
}


def load(source_obj):
    """注記の一覧を返す。壊れていたら空にする。"""
    if source_obj is None:
        return []

    raw = source_obj.get(ANNOTATION_PROP, "[]")
    try:
        data = json.loads(raw)
    except Exception:
        _debug.swallowed("marking.storage.load")
        return []

    return data if isinstance(data, list) else []


def save(source_obj, annotations, on_changed=None):
    """注記の一覧を書き込む。

    on_changed には、書き込み後に呼ぶ処理（キャッシュ破棄など）を渡す。
    この層は Blender の描画やキャッシュを知らないので、
    呼び出し側から受け取る形にしている。
    """
    if source_obj is None:
        return

    source_obj[ANNOTATION_PROP] = json.dumps(
        annotations,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        source_obj.data.update()
    except Exception:
        _debug.swallowed("marking.storage.save")

    if on_changed is not None:
        on_changed()


def raw(source_obj):
    """保存されている JSON 文字列そのもの。キャッシュキー用。"""
    if source_obj is None:
        return "[]"
    return source_obj.get(ANNOTATION_PROP, "[]")


def count_notches(source_obj):
    """(オート, 手動) の合印の数。"""
    auto = manual = 0
    for item in load(source_obj):
        if item.get("type") != NOTCH:
            continue
        if bool(item.get("auto", False)):
            auto += 1
        else:
            manual += 1
    return auto, manual


def without_notches(annotations, auto_only=False):
    """合印を取り除いた一覧を返す。

    auto_only が True なら、自動生成したものだけを取り除く。
    """
    result = []
    for item in annotations:
        if item.get("type") == NOTCH:
            if not auto_only or bool(item.get("auto", False)):
                continue
        result.append(item)
    return result


def normalize_color(value):
    """保存用に色を整える。JSON へ入れるのでリストで返す。

    桁を丸めるのは、同じ色なのに文字列が変わってキャッシュキーが
    毎回ずれるのを避けるため。
    """
    return [
        round(float(value[0]), 5),
        round(float(value[1]), 5),
        round(float(value[2]), 5),
    ]


def scene_item_color(item, scene=None):
    """注記1件の色を、種類ごとのシーン設定から決める。

    「合印の色」を変えたら、既に置いてある合印も変わる。設定が
    種類ごとに並んでいる以上、そう読まれる。置いた時点の色を
    保存値から使っていた頃は、オート合印と矢印だけが設定に追従し、
    手動で置いたものだけ変わらない、という食い違いになっていた。

    設定が取れないときだけ保存値へ落とす。古いファイルや、
    プロパティが未登録の状態でも描けるようにするため。
    """
    prop = COLOR_PROP.get(item.get("type"))
    source = None
    if scene is not None and prop:
        source = getattr(scene, prop, None)
    if source is None:
        source = item.get("color", [0.0, 0.0, 0.0])
    return item_color(item, source)


def item_color(item, color_source=None):
    """色を 0.0〜1.0 のタプルに整える。

    値が壊れていても描画を止めない。どの色を使うかは
    scene_item_color が決める。
    """
    source = color_source
    if source is None:
        source = item.get("color", [0.0, 0.0, 0.0])

    try:
        return (
            max(0.0, min(1.0, float(source[0]))),
            max(0.0, min(1.0, float(source[1]))),
            max(0.0, min(1.0, float(source[2]))),
        )
    except Exception:
        return (0.0, 0.0, 0.0)
