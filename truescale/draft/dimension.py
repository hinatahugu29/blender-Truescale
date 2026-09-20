"""寸法の値と、図面に出す文字。

■ 表示する文字は上書きできる

自動で出るのは実測値だが、図面では「およそ」「max」のように
手で書き換えたいことがある。上書きが入っていればそちらを出す。
空文字は「消したい」ではなく「未入力」として扱い、実測値へ戻す。

■ どの面図にどの軸を出すか

正面図では奥行きが見えないので、その寸法を書いても読めない。
面図ごとに意味のある2軸だけを対象にする。

  上面図 … X と Y
  正面図 … X と Z
  側面図 … Y と Z

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


def tsdraft_svg_dimension_axes(view_key):
    """その面図が出す寸法の軸。横方向・縦方向の順。

    順序を持たせているのは、書き出し側が「横の寸法」「縦の寸法」と
    して使うため。集合で返していたので、順序に頼っている側が
    たまたま動いているだけの状態だった。
    """
    return {
        "top": ("X", "Y"),
        "front": ("X", "Z"),
        "side": ("Y", "Z"),
    }.get(view_key, ())


def tsdraft_dimension_axis_enabled(scene, view_key, axis_name):
    """Return whether a dimension axis should be shown for a drawing view."""
    axis_name = str(axis_name).upper()
    valid_axes = tsdraft_svg_dimension_axes(view_key)
    if not valid_axes:
        return True
    if axis_name not in valid_axes:
        return False
    return bool(getattr(
        scene,
        f"tsdraft_show_dimension_{view_key}_{axis_name.lower()}",
        True
    ))


def tsdraft_svg_view_label(view_key):
    return {
        "top": "上面",
        "front": "前面",
        "side": "側面",
        "user": "任意",
    }.get(view_key, view_key)
