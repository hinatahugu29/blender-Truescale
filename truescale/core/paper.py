"""用紙サイズの定義と解決。

寸法は mm。向きは「幅 × 高さ」で表す。

カスタム用紙は入力値をそのまま使い、縦横の自動入れ替えを行わない。
A判・B判は名前が縦向きの寸法を指すので、横向きの指定で入れ替える。

■ 用紙の一覧は、この表ひとつだけ

以前は同じ表が3箇所にあった。ここと、型紙側の EnumProperty と、
三面図側の書き出し。三面図側には A5 と B判が無く、同じ「用紙
サイズ」という名前で選べるものが違っていた。

EnumProperty に渡す項目もここから作る。表に1行足せば両方に出る。

■ プロパティ名は呼ぶ側が渡す

型紙側と三面図側で設定の名前が違う（tsunfold_ と tsdraft_）。
どちらの設定を見るかを引数で受け取ることで、解決の手順そのものは
1つで済む。
"""

# 縦向き（幅 × 高さ）の寸法
SIZES_MM = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
    "B5": (182.0, 257.0),
    "B4": (257.0, 364.0),
}

# 面積の小さい順。「収まる最小の用紙」を探すときに使う。
SIZES_BY_AREA = sorted(SIZES_MM.items(), key=lambda kv: kv[1][0] * kv[1][1])

PAPER_SIZE_PROP = "tsunfold_paper_size"
ORIENTATION_PROP = "tsunfold_orientation"
CUSTOM_WIDTH_PROP = "tsunfold_custom_paper_width_mm"
CUSTOM_HEIGHT_PROP = "tsunfold_custom_paper_height_mm"


def enum_items(include_custom=True):
    """EnumProperty へ渡す用紙の一覧。

    表から作る。手で並べ直すと、片方にだけ用紙が増える。
    """
    items = [
        (name, name, f"{width:.0f} × {height:.0f} mm")
        for name, (width, height) in SIZES_MM.items()
    ]
    if include_custom:
        items.append(("CUSTOM", "カスタム", "幅と高さをmmで指定"))
    return items


def fits(width_mm, height_mm, paper_w, paper_h):
    """指定の用紙に収まるか。縦横どちらの向きでもよい。"""
    return (
        (width_mm <= paper_w and height_mm <= paper_h)
        or (width_mm <= paper_h and height_mm <= paper_w)
    )


def smallest_fitting(width_mm, height_mm):
    """収まる最小の定形用紙の名前。無ければ None。"""
    for name, (paper_w, paper_h) in SIZES_BY_AREA:
        if fits(width_mm, height_mm, paper_w, paper_h):
            return name
    return None


def oriented(width_mm, height_mm, orientation):
    """向きの指定に合わせて幅と高さを入れ替える。"""
    if orientation == "LANDSCAPE":
        return max(width_mm, height_mm), min(width_mm, height_mm)
    if orientation == "PORTRAIT":
        return min(width_mm, height_mm), max(width_mm, height_mm)
    return width_mm, height_mm


def base_dimensions_mm(scene, size_prop=PAPER_SIZE_PROP,
                       custom_width_prop=CUSTOM_WIDTH_PROP,
                       custom_height_prop=CUSTOM_HEIGHT_PROP,
                       default_custom=(600.0, 900.0)):
    """向きを当てる前の用紙寸法（幅, 高さ）。

    三面図側は「収まる向きを自動で選ぶ」ので、向きを当てる前の
    寸法が要る。プロパティ名を引数で受けるのは、型紙側と
    三面図側で設定の名前が違うため。
    """
    key = str(getattr(scene, size_prop, "A4"))

    if key == "CUSTOM":
        return (
            max(1.0, float(getattr(
                scene, custom_width_prop, default_custom[0]
            ))),
            max(1.0, float(getattr(
                scene, custom_height_prop, default_custom[1]
            ))),
        )

    return SIZES_MM.get(key, SIZES_MM["A4"])


def scene_dimensions_mm(scene):
    """シーンの設定から用紙寸法（幅, 高さ）を返す。"""
    key = str(getattr(scene, PAPER_SIZE_PROP, "A4"))
    base_w, base_h = base_dimensions_mm(scene)

    if key == "CUSTOM":
        # カスタムは入力どおり。縦横の自動入れ替えはしない。
        return base_w, base_h

    orientation = str(getattr(scene, ORIENTATION_PROP, "PORTRAIT"))
    return oriented(base_w, base_h, orientation)


def scene_display_name(scene):
    """パネル表示用の用紙名。"""
    key = str(getattr(scene, PAPER_SIZE_PROP, "A4"))
    if key == "CUSTOM":
        width, height = scene_dimensions_mm(scene)
        return f"カスタム {width:.0f}×{height:.0f} mm"
    return key
