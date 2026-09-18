# 型紙ヘルパー 性能計測スクリプト
# ------------------------------------------------------------
# 使い方:
#   1. 元モデル（シーム付き）を選択した状態にする
#   2. Blender の Scripting ワークスペースを開く
#   3. 新規テキストとしてこれを貼り付け、[スクリプト実行]
#   4. System Console（Window > Toggle System Console）に結果が出る
#
# アドオン本体は一切変更しません。計測のみです。
# ------------------------------------------------------------

import bpy
import cProfile
import pstats
import io
import time

obj = bpy.context.active_object

print("=" * 70)
print("型紙ヘルパー 性能計測")
print("=" * 70)

if obj is None or obj.type != 'MESH':
    print("!! Meshオブジェクトを選択してから実行してください")
else:
    mesh = obj.data
    seams = sum(1 for e in mesh.edges if e.use_seam)

    print(f"対象           : {obj.name}")
    print(f"頂点 / 辺 / 面 : {len(mesh.vertices)} / {len(mesh.edges)} / {len(mesh.polygons)}")
    print(f"シーム辺       : {seams}")
    print(f"Object Scale   : {tuple(round(v, 6) for v in obj.scale)}")
    print(f"Unit Scale     : {bpy.context.scene.unit_settings.scale_length}")
    print(f"モディファイア : {[m.type for m in obj.modifiers]}")
    print("-" * 70)

    if seams == 0:
        print("!! シームがありません。先にシームを設定してください")
    else:
        # --- 1. モデル読み込み ---
        t0 = time.perf_counter()
        try:
            bpy.ops.pattern_helper.load_seamed_object()
        except Exception as exc:
            print(f"読み込み失敗: {exc}")
        t1 = time.perf_counter()
        print(f"[1] モデルの読み込み : {t1 - t0:.3f} 秒")

        # --- 2. 型紙作成をプロファイル付きで実行 ---
        profiler = cProfile.Profile()
        t0 = time.perf_counter()
        profiler.enable()
        try:
            bpy.ops.pattern_helper.build_pattern()
        except Exception as exc:
            print(f"型紙作成failed: {exc}")
        profiler.disable()
        t1 = time.perf_counter()

        print(f"[2] 型紙を作成/更新  : {t1 - t0:.3f} 秒")
        print("-" * 70)
        print("内訳（累積時間の多い順 / 上位25件）")
        print("-" * 70)

        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative").print_stats(25)
        print(stream.getvalue())

print("=" * 70)
print("計測終了")
print("=" * 70)
