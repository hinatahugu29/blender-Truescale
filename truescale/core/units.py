"""ミリメートルと Blender Unit の相互変換。

このアドオンの寸法計算は、すべてここを通る。単位換算の唯一の入口。

■ 基準の決まり方

既定ではシーンの Unit Scale をそのまま使う。
ただしシーンの設定が制作意図と食い違っている場合、Blender側を直すと
物理演算・他アドオン・他の作業にも波及してしまう。そのため
「このアドオンの中だけで基準を決める」モードを用意している。

  tsunfold_scale_mode
    SCENE   シーンの Unit Scale をそのまま使う（既定）
    MANUAL  シーンを見ず tsunfold_manual_mm_per_bu だけで換算する

どちらの場合も scene.unit_settings は読むだけで、書き換えない。

■ 関数の使い分け

純粋な数値変換（mm_to_bu / bu_to_mm）は、換算値を引数で受け取る。
Blender に依存しないので単体でテストできる。

scene_ が付くものはシーンから換算値を解決する。
"""

# プロパティ名はアドオン全体で共有する。
# いまは unfold が register しているが、基準はアドオン全体のものなので、
# 将来 draft 側も同じ値を使えるようにここへ集約しておく。
SCALE_MODE_PROP = "tsunfold_scale_mode"
MANUAL_MM_PER_BU_PROP = "tsunfold_manual_mm_per_bu"

# 換算値が取れなかったときの既定。1 BU = 1 m として扱う。
DEFAULT_MM_PER_BU = 1000.0


# ------------------------------------------------------------
# 純粋な変換（Blender非依存）
# ------------------------------------------------------------

def mm_to_bu(mm, mm_per_bu):
    """ミリメートルを Blender Unit へ。"""
    if mm_per_bu <= 0.0:
        mm_per_bu = DEFAULT_MM_PER_BU
    return float(mm) / mm_per_bu


def bu_to_mm(bu, mm_per_bu):
    """Blender Unit をミリメートルへ。"""
    if mm_per_bu <= 0.0:
        mm_per_bu = DEFAULT_MM_PER_BU
    return float(bu) * mm_per_bu


def meters_to_mm_per_bu(scale_length):
    """Unit Scale（1 BU が何メートルか）を mm/BU へ。"""
    if scale_length <= 0.0:
        return DEFAULT_MM_PER_BU
    return float(scale_length) * 1000.0


def mm_per_bu_to_meters(mm_per_bu):
    """mm/BU を Unit Scale（1 BU が何メートルか）へ。"""
    if mm_per_bu <= 0.0:
        mm_per_bu = DEFAULT_MM_PER_BU
    return float(mm_per_bu) / 1000.0


# ------------------------------------------------------------
# シーンからの解決（Blender依存）
# ------------------------------------------------------------

def scene_unit_scale_raw(scene):
    """シーンが持っている Unit Scale をそのまま返す。"""
    try:
        scale = float(scene.unit_settings.scale_length)
    except Exception:
        scale = 1.0
    return scale if scale > 0.0 else 1.0


def scene_mm_per_bu(scene):
    """このシーンで 1 Blender Unit が何ミリに相当するか。

    アドオンの基準モードを見て決める。寸法計算はここが起点。
    """
    mode = str(getattr(scene, SCALE_MODE_PROP, "SCENE"))

    if mode == "MANUAL":
        try:
            manual = float(getattr(scene, MANUAL_MM_PER_BU_PROP, DEFAULT_MM_PER_BU))
        except Exception:
            manual = DEFAULT_MM_PER_BU
        if manual > 0.0:
            return manual

    return meters_to_mm_per_bu(scene_unit_scale_raw(scene))


def scene_scale_to_meters(scene):
    """1 Blender Unit が何メートルか。"""
    return mm_per_bu_to_meters(scene_mm_per_bu(scene))


def scene_unit_summary(scene):
    """(1 BU のメートル数, 1 BU のミリ数) を返す。パネル表示用。"""
    mm_per_bu = scene_mm_per_bu(scene)
    return mm_per_bu_to_meters(mm_per_bu), mm_per_bu


def scene_mm_to_bu(scene, mm):
    return mm_to_bu(mm, scene_mm_per_bu(scene))


def scene_bu_to_mm(scene, bu):
    return bu_to_mm(bu, scene_mm_per_bu(scene))
