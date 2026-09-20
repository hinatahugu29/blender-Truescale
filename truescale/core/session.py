"""シーンに持たせる作業中の状態。

Scene のカスタムプロパティ（scene["..."]）として保持しているもの。
「いまどのモデルを読み込んでいるか」「modal が動いているか」といった、
UIに出さない作業中の情報を扱う。

■ なぜここへ集めるか

これらは登録プロパティ（bpy.props）と違い、型も既定値も持たない
文字列キーの辞書として散在していた。キー名が直書きされていたため、
どこで何が読み書きされているかを追うのが難しく、接頭辞の一括置換で
壊しかけたこともある。

キー名をここへ集約し、読み書きも関数経由にすることで、
将来まとめて登録プロパティへ移す際の変更点を1箇所に閉じ込める。

■ 登録プロパティとの違い

登録プロパティ … UIに出す設定。型と既定値があり、unregister で消える
ここのキー    … 作業中の状態。.blend に残るが、ファイルを開いた時に
                リセットされるもの（load_post で初期化している）
"""

# --- 読み込み中のモデル ---
SEAM_SOURCE = "tsunfold_seam_source"
SEAM_PREVIEW_READY = "tsunfold_seam_preview_ready"

# --- マーキングの作業状態 ---
MODAL_RUNNING = "tsunfold_modal_running"
MARKING_SESSION_ACTIVE = "tsunfold_marking_session_active"
MARKING_FINISH_REQUESTED = "tsunfold_marking_finish_requested"
MARKING_PREV_ACTIVE = "tsunfold_marking_prev_active"
MARKING_PREV_SELECTED_JSON = "tsunfold_marking_prev_selected_json"
MARKING_PREV_MODE = "tsunfold_marking_prev_mode"

# tsunfold_active_tool は登録プロパティ（StringProperty）なので、
# ここには含めない。属性として読み書きするのが正で、
# 辞書アクセスは登録前に呼ばれた場合の保険としてのみ使われている。

# --- レイアウト・表示 ---
MANUAL_LAYOUT_ACTIVE = "tsunfold_manual_layout_active"
DISPLAY_MODE = "tsunfold_display_mode"

# --- 印刷プレビュー中に退避する情報 ---
PREVIEW_PREV_ACTIVE = "tsunfold_preview_prev_active"
PREVIEW_SOURCE_NAME = "tsunfold_preview_source_name"
PREVIEW_SOURCE_HIDE_GET = "tsunfold_preview_source_hide_get"
PREVIEW_SOURCE_HIDE_VIEWPORT = "tsunfold_preview_source_hide_viewport"

# --- その他 ---
LAST_EXPORT_DIR = "tsunfold_last_export_dir"


# ファイルを開いたときに初期化するもの。
# 前回の作業状態が残っていると、modal が動いていないのに
# 動いていることになるなど、辻褄の合わない状態になる。
RESET_ON_LOAD = {
    MARKING_SESSION_ACTIVE: False,
    MARKING_FINISH_REQUESTED: False,
    MARKING_PREV_ACTIVE: "",
    MARKING_PREV_SELECTED_JSON: "[]",
    MARKING_PREV_MODE: "OBJECT",
    SEAM_SOURCE: "",
    SEAM_PREVIEW_READY: False,
    MODAL_RUNNING: False,
    MANUAL_LAYOUT_ACTIVE: False,
}


def get(scene, key, default=None):
    """値を読む。未設定なら default。"""
    return scene.get(key, default)


def set_value(scene, key, value):
    """値を書く。"""
    scene[key] = value


def clear(scene, key):
    """キーごと消す。"""
    if key in scene:
        del scene[key]


def reset_on_load(scene):
    """ファイルを開いたときの初期化。"""
    for key, value in RESET_ON_LOAD.items():
        scene[key] = value


def all_keys():
    """このモジュールが管理するキーの一覧。検証用。"""
    return [
        value
        for name, value in globals().items()
        if name.isupper() and isinstance(value, str) and value.startswith("tsunfold_")
    ]
