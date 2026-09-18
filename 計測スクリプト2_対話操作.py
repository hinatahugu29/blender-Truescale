# 型紙ヘルパー 対話操作の性能計測（U-04 検証用）
# ------------------------------------------------------------
# 使い方:
#   1. 型紙を作成済みの状態にする（元モデルを選択しておく）
#   2. Scripting ワークスペースで実行
#   3. System Console で結果を確認
#
# 何をするか:
#   「合印の色」（重いコールバック）と「矢印の色」（軽いコールバック）を
#   それぞれ20回変更し、所要時間を比較します。
#   カラーピッカーをドラッグした時に起きることの再現です。
#
# アドオン本体は一切変更しません。色設定は最後に元へ戻します。
# ------------------------------------------------------------

import bpy
import cProfile
import pstats
import io
import time

scene = bpy.context.scene
N = 20

print("=" * 70)
print("型紙ヘルパー 対話操作の性能計測")
print("=" * 70)

orig_notch = tuple(scene.pattern_helper_notch_color)
orig_arrow = tuple(scene.pattern_helper_arrow_color)
print(f"合印モード : {scene.pattern_helper_notch_mode}")
print("-" * 70)

# --- A. 矢印の色（軽いコールバック: _pattern_setting_updated）---
t0 = time.perf_counter()
for i in range(N):
    scene.pattern_helper_arrow_color = (i / float(N), 0.2, 0.2)
t1 = time.perf_counter()
arrow_time = t1 - t0
print(f"[A] 矢印の色 {N}回 : {arrow_time:.3f} 秒  ({arrow_time / N * 1000:.1f} ms/回)")

# --- B. 合印の色（重いコールバック: _pattern_notch_setting_updated）---
t0 = time.perf_counter()
for i in range(N):
    scene.pattern_helper_notch_color = (i / float(N), 0.2, 0.2)
t1 = time.perf_counter()
notch_time = t1 - t0
print(f"[B] 合印の色 {N}回 : {notch_time:.3f} 秒  ({notch_time / N * 1000:.1f} ms/回)")

print("-" * 70)
if arrow_time > 0:
    print(f"合印は矢印の約 {notch_time / arrow_time:.1f} 倍のコスト")
print("-" * 70)

# --- C. 合印モードを「なし」にして再計測（早期リターン経路）---
prev_mode = scene.pattern_helper_notch_mode
try:
    scene.pattern_helper_notch_mode = "NONE"
except Exception:
    try:
        scene.pattern_helper_notch_mode = "MANUAL"
    except Exception:
        pass

if scene.pattern_helper_notch_mode != prev_mode:
    t0 = time.perf_counter()
    for i in range(N):
        scene.pattern_helper_notch_color = (i / float(N), 0.5, 0.5)
    t1 = time.perf_counter()
    off_time = t1 - t0
    print(f"[C] 合印の色 {N}回（方式=なし）: {off_time:.3f} 秒  ({off_time / N * 1000:.1f} ms/回)")
    print("    → [B]より大幅に速ければ _pattern_refresh_auto_notches が主因と確定")
    scene.pattern_helper_notch_mode = prev_mode
else:
    print("[C] 合印モードを切り替えられなかったためスキップ")

print("-" * 70)
print("合印の色 変更1回ぶんの内訳（他アドオンのハンドラも含む）")
print("-" * 70)

profiler = cProfile.Profile()
profiler.enable()
scene.pattern_helper_notch_color = (0.3, 0.3, 0.3)
profiler.disable()

stream = io.StringIO()
pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(25)
print(stream.getvalue())

# --- 後始末 ---
scene.pattern_helper_notch_color = orig_notch
scene.pattern_helper_arrow_color = orig_arrow
print("色設定を元に戻しました")
print("=" * 70)
