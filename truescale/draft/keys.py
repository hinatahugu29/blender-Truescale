"""描画ハンドラなどを置く場所の名前と、ダーク表示の配色。

■ なぜ bpy.app.driver_namespace に置くか

描画ハンドラの「ハンドル」は、外すときに同じものを渡す必要がある。
モジュール直下の変数に持つと、Blender がモジュールを読み直した
ときに失われ、外せないハンドラが残る。driver_namespace は
Blender 側が持つので、読み直しをまたいで残る。

■ 旧キーは書き換えないこと

LEGACY_* は、過去のバージョンが実際に残していった文字列。
後始末のためだけに持っている。今の接頭辞に合わせて一括置換すると、
古いファイルに残ったハンドラを外せなくなる。

  MHS_ … 最初期の接頭辞
  MHP_ … Truescale へ改称する前の接頭辞
"""


HANDLER_KEY = "TSDRAFT_SIZE_LABEL_HANDLER"
BBOX_HANDLER_KEY = "TSDRAFT_BBOX_DRAW_HANDLER"
VIEW_LABEL_HANDLER_KEY = "TSDRAFT_VIEW_LABEL_HANDLER"
DATA_KEY = "TSDRAFT_SIZE_LABEL_DATA"
SOURCE_KEY = "TSDRAFT_SIZE_SOURCE_NAME"
VIEW_STATE_KEY = "TSDRAFT_VIEW_STATE"
BBOX_NAME = "サイズ用バウンディングボックス"

# 旧バージョンが driver_namespace に残したキー。後始末のためだけに持つ。
# ここは「過去に実際に使われていた文字列」でなければ意味がないので、
# 接頭辞の一括置換の対象にしてはいけない。
#   MHS_ … 最初期の接頭辞
#   MHP_ … Truescale へ改称する前の接頭辞
LEGACY_HANDLER_KEYS = (
    "MHS_SIZE_LABEL_HANDLER",
    "MHS_BBOX_DRAW_HANDLER",
    "MHS_VIEW_LABEL_HANDLER",
    "MHP_SIZE_LABEL_HANDLER",
    "MHP_BBOX_DRAW_HANDLER",
    "MHP_VIEW_LABEL_HANDLER",
)
LEGACY_DATA_KEY = "MHP_SIZE_LABEL_DATA"
LEGACY_SOURCE_KEY = "MHP_SIZE_SOURCE_NAME"
EXPORT_CONTEXT_KEY = "TSDRAFT_EXPORT_VIEW_CONTEXT"
ZOOM_SYNC_STATE_KEY = "TSDRAFT_ORTHO_ZOOM_SYNC_STATE"
AUTO_FOLLOW_SIGNATURE_KEY = "TSDRAFT_AUTO_FOLLOW_SIGNATURE"
AUTO_FOLLOW_GUARD_KEY = "TSDRAFT_AUTO_FOLLOW_GUARD"
LAST_EXPORT_DIR_KEY = "TSDRAFT_LAST_EXPORT_DIR"
DARK_VIEW_STATE_KEY = "TSDRAFT_DARK_VIEW_STATE"
DARK_BG = (0.004, 0.012, 0.028)
DARK_GRAD_TOP = (0.045, 0.145, 0.220)
DARK_GRAD_BOTTOM = (0.006, 0.018, 0.032)
DARK_LINE = (0.32, 0.34, 0.37, 1.0)
DARK_TEXT = (0.72, 0.88, 0.96, 1.0)
