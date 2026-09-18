# Truescale の内部状態をダンプする
# ------------------------------------------------------------
# Scripting ワークスペースで実行してください。すぐ終わります。
#
# 裏で modal オペレータが動きっぱなしになっていないかなど、
# 「見えない状態」を確認するためのものです。
# 何も変更しません。読み取りだけです。
# ------------------------------------------------------------

import bpy

scene = bpy.context.scene

print("=" * 72)
print("Truescale 内部状態")
print("=" * 72)

# --- modal が動いているかどうかに直結するフラグ ---
print("  [ modal / モード ]")
flags = [
    ("tsunfold_modal_running", "マーキング modal 実行中"),
    ("tsunfold_marking_session_active", "マーキング session 中"),
    ("tsunfold_manual_layout_active", "手動レイアウト中"),
    ("tsunfold_marking_finish_requested", "終了要求"),
    ("tsunfold_seam_source", "読み込み中の元モデル"),
    ("tsunfold_seam_preview_ready", "シームプレビュー"),
]
for key, jp in flags:
    print(f"      {jp:<28} {key} = {scene.get(key, '(未設定)')!r}")

print()
print("  [ ツール状態（登録プロパティ）]")
for prop, jp in (
    ("tsunfold_active_tool", "選択中のマーキングツール"),
    ("tsunfold_correspondence_mode", "対応確認モード"),
    ("tsunfold_notch_mode", "合印の方式"),
    ("tsunfold_arrow_mode", "矢印の方式"),
    ("tsunfold_lightweight_view", "軽量ビュー"),
    ("tsunfold_preview", "印刷プレビュー"),
    ("tsunfold_show_paper", "用紙ガイド"),
):
    print(f"      {jp:<28} {prop} = {getattr(scene, prop, '(未登録)')!r}")

# --- 描画ハンドラ ---
print()
print("  [ 描画ハンドラ ]")
import sys
for name, module in list(sys.modules.items()):
    path = (getattr(module, "__file__", None) or "").replace("\\", "/")
    if path.endswith("/truescale/unfold/__init__.py"):
        for attr in ("_draw_handle", "_pattern_draw_handle", "_pattern_text_handle"):
            state = "登録あり" if getattr(module, attr, None) is not None else "なし"
            print(f"      unfold.{attr:<24} {state}")
        epoch = getattr(module, "_pattern_cache_epoch", None)
        cache = getattr(module, "_pattern_draw_cache", None)
        print(f"      キャッシュ epoch            {epoch}")
        print(f"      キャッシュ件数              {len(cache) if cache is not None else '?'}")

# --- Blenderのハンドラ登録数 ---
print()
print("  [ Blender ハンドラ登録数 ]")
for hname in ("depsgraph_update_post", "depsgraph_update_pre", "frame_change_post",
              "load_post", "save_pre", "undo_post", "redo_post"):
    handlers = getattr(bpy.app.handlers, hname, None)
    if handlers is None:
        continue
    if len(handlers):
        print(f"      {hname:<24} {len(handlers)} 個")
        for func in handlers:
            mod = getattr(func, "__module__", "?") or "?"
            if mod.startswith("bl_ext."):
                mod = ".".join(mod.split(".")[2:])
            print(f"          {mod}.{getattr(func, '__name__', '?')}")

# --- シーン規模 ---
print()
print("  [ シーン ]")
print(f"      オブジェクト数              {len(bpy.data.objects)}")
print(f"      メッシュ数                  {len(bpy.data.meshes)}")
print(f"      マテリアル数                {len(bpy.data.materials)}")
print(f"      Unit Scale                  {scene.unit_settings.scale_length}")
print(f"      Undo ステップ数（設定）     {bpy.context.preferences.edit.undo_steps}")
print(f"      Undo メモリ上限 MB          {bpy.context.preferences.edit.undo_memory_limit}")
print(f"      グローバルUndo              {bpy.context.preferences.edit.use_global_undo}")

# --- 重い型紙オブジェクト ---
print()
print("  [ 生成された型紙 ]")
found = False
for obj in bpy.data.objects:
    if obj.get("unfold_helper_generated") or obj.get("tsunfold_generated"):
        found = True
        mesh = obj.data
        print(f"      {obj.name}")
        print(f"          頂点 {len(mesh.vertices)} / 辺 {len(mesh.edges)} / 面 {len(mesh.polygons)}")
        for key in obj.keys():
            value = obj[key]
            if isinstance(value, str) and len(value) > 200:
                print(f"          {key}: 文字列 {len(value):,} 文字")
if not found:
    print("      （見つかりません）")

print("=" * 72)
