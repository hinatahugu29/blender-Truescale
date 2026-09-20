"""オート合印の生成と削除。

シームをたどって、一定の間隔で合印を置く。手で1つずつ置くのは
現実的ではないので、まず自動で入れてから必要なところだけ足す。

■ 手動で置いたものは消さない

作り直すのはオートで入れたものだけ。手で置いた印は、その人が
意図して置いたもので、作り直しのたびに消えてはいけない。
保存時に auto の印を付けて区別している。

■ 分割数の決め方

シーンの設定から取るが、辺の長さに対して細かすぎる指定は
意味がないので上限を掛ける。
"""

from .. import debug as _debug
from ..core import objects as _objects
from ..core import units as _units
from . import interact as _interact
from . import seams as _seams
from . import storage as _storage


def division_value(scene):
    """Safely normalize auto-notch division selector to 2 / 3 / 4."""
    raw = getattr(
        scene,
        "tsunfold_auto_notch_divisions",
        "3",
    )

    try:
        value = int(str(raw).strip())
    except Exception:
        value = 3

    if value not in {2, 3, 4}:
        value = 3

    return value


def refresh(context, source_obj):
    """Rebuild only automatically generated notch annotations."""
    if source_obj is None or source_obj.type != 'MESH':
        return 0

    _seams.sync_live_seams(source_obj)

    mode = str(getattr(context.scene, "tsunfold_notch_mode", "AUTO"))
    items = _storage.load(source_obj)

    # Preserve manual notches and every other annotation type.
    items = [
        item
        for item in items
        if not (
            item.get("type") == "notch_edge"
            and bool(item.get("auto", False))
        )
    ]

    if mode != "AUTO":
        _storage.save(source_obj, items)
        return 0

    divisions = division_value(
        context.scene
    )

    count = 0

    for trail in _seams.seam_trails(source_obj):
        for edge_index, fraction in _seams.trail_mark_positions(
            context,
            source_obj,
            trail,
            divisions,
        ):
            # 色は保存しない。オート合印は常に現在のシーン設定に従う
            # （_pattern_item_color を参照）。保存すると色を変えるたびに
            # 全アノテーションの書き直しが必要になる。
            items.append({
                "type": "notch_edge",
                "edge": int(edge_index),
                "t": float(fraction),
                "auto": True,
            })
            count += 1

    _storage.save(source_obj, items)
    return count


def remove(source_obj):
    if source_obj is None:
        return 0

    items = _storage.load(source_obj)
    before = len(items)

    items = [
        item
        for item in items
        if not (
            item.get("type") == "notch_edge"
            and bool(item.get("auto", False))
        )
    ]

    _storage.save(source_obj, items)
    return before - len(items)
