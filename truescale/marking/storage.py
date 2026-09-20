"""型紙の注記（合印・型紙ID・矢印・メモ）の保存と読み出し。

注記は元モデルのカスタムプロパティに JSON でまとめて持たせる。
型紙オブジェクトではなく元モデル側に置くのは、型紙を作り直しても
手動で付けた印が残るようにするため。

■ 1件の形

    {"type": "notch_edge", "edge": 12, "t": 0.5, "auto": True}
    {"type": "text", "value": "前身頃", "color": [0, 0, 0], ...}

type ごとに持つ項目が違う。共通するのは type だけ。

■ 色の扱い

オート合印は色を保存しない。常に現在のシーン設定に従う。
保存すると、色を変えるたびに全件の書き直しが必要になり、
カラーピッカーのドラッグ中に毎フレーム JSON の全書き出しが走る。

手動で置いた印は個別の色を持てるので、保存値をそのまま使う。
"""

import json

from .. import debug as _debug

# 元モデルのカスタムプロパティ名
ANNOTATION_PROP = "tsunfold_annotations_json"

NOTCH = "notch_edge"


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


def item_color(item, auto_notch_color=None):
    """描画用に、注記1件の色をタプルで返す。

    0.0〜1.0 に収める。保存値が壊れていても描画を止めない。

    オート合印は保存値を持たないので、呼び出し側が渡す
    auto_notch_color（現在のシーン設定）を使う。
    """
    if (
        auto_notch_color is not None
        and item.get("type") == NOTCH
        and bool(item.get("auto", False))
    ):
        source = auto_notch_color
    else:
        source = item.get("color", [0.0, 0.0, 0.0])

    try:
        return (
            max(0.0, min(1.0, float(source[0]))),
            max(0.0, min(1.0, float(source[1]))),
            max(0.0, min(1.0, float(source[2]))),
        )
    except Exception:
        return (0.0, 0.0, 0.0)
