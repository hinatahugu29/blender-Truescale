"""寸法の値と、図面に出す文字。

■ 表示する文字は上書きできる

自動で出るのは実測値だが、図面では「およそ」「max」のように
手で書き換えたいことがある。上書きが入っていればそちらを出す。
空文字は「消したい」ではなく「未入力」として扱い、実測値へ戻す。

■ 単位

mm で持ち、表示のときだけ cm / m へ直す。計算のたびに単位を
またぐと誤差が乗るため、保持は mm に固定する。
"""

import bpy

from . import keys as _keys


def dimension_length_mm(item):
    """Return stored dimension length in millimeters, including older data."""
    if "length_mm" in item:
        return float(item["length_mm"])

    # Backward compatibility for 2.4.x data stored only as "12.3 mm"
    text = str(item.get("text", "")).strip()
    try:
        return float(text.replace("mm", "").strip())
    except Exception:
        return 0.0


def format_dimension_value(length_mm, unit):
    if unit == 'M':
        return f"{length_mm / 1000.0:.3f} m"
    if unit == 'CM':
        return f"{length_mm / 10.0:.2f} cm"
    return f"{length_mm:.1f} mm"


def get_dimension_text(scene, item):
    return format_dimension_value(
        dimension_length_mm(item),
        scene.tsdraft_dimension_unit
    )


def get_axis_dimension_text(scene, axis, fallback=None):
    data = bpy.app.driver_namespace.get(_keys.DATA_KEY, [])
    for item in data:
        if item.get("axis") == axis:
            return get_dimension_text(scene, item)

    return fallback if fallback is not None else axis
