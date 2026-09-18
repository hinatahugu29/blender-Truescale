# 型紙ヘルパー 描画側解析の性能計測（H-02 検証用）
# ------------------------------------------------------------
# 使い方:
#   1. 型紙を作成済みの状態にする
#   2. Scripting ワークスペースで実行
#   3. System Console で結果を確認
#
# 何をするか:
#   描画ハンドラが毎フレーム使う3つの解析関数を
#   「キャッシュなし」「キャッシュあり」で計測し、差を見ます。
#   色ドラッグ中はキャッシュが毎フレーム破棄されるため、
#   「キャッシュなし」の値が実際の1フレームあたりのコストになります。
#
# アドオン本体は一切変更しません。読み取りと計測のみです。
# ------------------------------------------------------------

import bpy
import sys
import time
import cProfile
import pstats
import io

# --- アドオンのモジュールを探す ---
mod = None
for name, m in list(sys.modules.items()):
    f = getattr(m, "__file__", None) or ""
    if f.replace("\\", "/").endswith("unfold_helper/__init__.py"):
        mod = m
        break

print("=" * 70)
print("型紙ヘルパー 描画側解析の性能計測")
print("=" * 70)

if mod is None:
    print("!! unfold_helper モジュールが見つかりません（アドオンは有効ですか？）")
else:
    ctx = bpy.context
    source = mod._pattern_seam_source(ctx)
    if source is None:
        source = mod._pattern_source_object_from_context(ctx)
    unfold = mod._pattern_unfold_for_source(source) if source else None

    print(f"元モデル : {source.name if source else 'なし'}")
    print(f"型紙     : {unfold.name if unfold else 'なし'}")

    if source is None or unfold is None:
        print("!! 型紙を作成してから実行してください")
    else:
        m = unfold.data
        print(f"型紙の 頂点/辺/面 : {len(m.vertices)} / {len(m.edges)} / {len(m.polygons)}")
        print("-" * 70)

        targets = [
            ("型紙IDの島内配置探索", mod._pattern_auto_flat_oriented_text_items),
            ("矢印の島内配置探索  ", mod._pattern_auto_arrow_segments),
            ("合印など色付き線    ", mod._pattern_flat_colored_segments),
        ]

        print(f"{'処理':<22} {'キャッシュなし':>14} {'キャッシュあり':>14}")
        print("-" * 70)

        total_cold = 0.0
        for label, fn in targets:
            # キャッシュなし
            mod._pattern_invalidate_layout_cache()
            t0 = time.perf_counter()
            try:
                fn(ctx, source, unfold)
            except Exception as exc:
                print(f"{label}  エラー: {exc}")
                continue
            cold = time.perf_counter() - t0

            # キャッシュあり（直後にもう一度）
            t0 = time.perf_counter()
            try:
                fn(ctx, source, unfold)
            except Exception:
                pass
            warm = time.perf_counter() - t0

            total_cold += cold
            print(f"{label:<22} {cold*1000:>11.2f} ms {warm*1000:>11.2f} ms")

        print("-" * 70)
        print(f"{'合計（キャッシュなし）':<22} {total_cold*1000:>11.2f} ms")
        print()
        print(f"  → 色ドラッグ中はこれが毎フレーム走ります。")
        print(f"  → 16.6ms を超えていれば 60fps を維持できません。")
        print(f"     現在: 1フレームあたり約 {total_cold*1000:.1f} ms "
              f"（理論上 約 {1.0/total_cold if total_cold > 0 else 999:.0f} fps が上限）")
        print("-" * 70)
        print("キャッシュなし1回ぶんの内訳（上位20件）")
        print("-" * 70)

        mod._pattern_invalidate_layout_cache()
        profiler = cProfile.Profile()
        profiler.enable()
        for _, fn in targets:
            try:
                fn(ctx, source, unfold)
            except Exception:
                pass
        profiler.disable()

        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(20)
        print(stream.getvalue())

print("=" * 70)
