# ビューポート再描画コストの切り分け（Blender再起動なし）
# ------------------------------------------------------------
# Blender の Scripting ワークスペースに貼って実行してください。
#
# 何をするか:
#   ビューポートを連続再描画して1フレームあたりの時間を測ります。
#   その計測を、Truescale の描画ハンドラを「付けたまま」と
#   「一時的に外した状態」の両方で行い、差を出します。
#
#   差が大きい → このアドオンの描画が重さの原因
#   差が小さい → 原因は他（他アドオン・シーン・GPU設定など）
#
#   ハンドラは計測後に必ず元へ戻します。
#   アドオンのコードは一切変更しません。
#
# 事前準備:
#   型紙を作成済みの状態にして、3Dビューが見えるようにしてください。
# ------------------------------------------------------------

import sys
import time

import bpy

PHASE_SECONDS = 4.0


# --- Truescale のモジュールを探す ---------------------------------

def find_modules():
    found = {}
    for name, module in list(sys.modules.items()):
        path = (getattr(module, "__file__", None) or "").replace("\\", "/")
        if path.endswith("/truescale/unfold/__init__.py"):
            found["unfold"] = module
        elif path.endswith("/truescale/draft/__init__.py"):
            found["draft"] = module
    return found


# どの描画関数を、どの保持場所へ、どの種別で登録し直すか。
# アドオン側の register() と同じ引数にしてある。
# (保持場所の種類, 保持キー, 描画関数名, リージョン種別)
UNFOLD_HANDLERS = (
    ("global", "_draw_handle", "_draw_paper_guide", 'POST_VIEW'),
    ("global", "_pattern_draw_handle", "_draw_pattern_marks_3d", 'POST_VIEW'),
    ("global", "_pattern_text_handle", "_draw_pattern_text_2d", 'POST_PIXEL'),
)

DRAFT_HANDLERS = (
    ("namespace", "HANDLER_KEY", "draw_size_labels", 'POST_PIXEL'),
    ("namespace", "BBOX_HANDLER_KEY", "draw_bbox_overlay", 'POST_VIEW'),
    ("namespace", "VIEW_LABEL_HANDLER_KEY", "draw_view_label", 'POST_PIXEL'),
)


def detach_handlers(modules):
    """描画ハンドラを外す。戻すのに必要な情報を返す。"""
    removed = []
    namespace = bpy.app.driver_namespace

    for module_key, table in (("unfold", UNFOLD_HANDLERS), ("draft", DRAFT_HANDLERS)):
        module = modules.get(module_key)
        if module is None:
            continue

        for holder_kind, holder_key, func_name, region in table:
            if holder_kind == "global":
                handle = getattr(module, holder_key, None)
            else:
                key = getattr(module, holder_key, None)
                handle = namespace.get(key) if key else None

            if handle is None:
                continue

            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            except Exception as exc:
                print(f"  外せませんでした: {func_name} ({exc})")
                continue

            if holder_kind == "global":
                setattr(module, holder_key, None)
            else:
                namespace[getattr(module, holder_key)] = None

            removed.append((module, holder_kind, holder_key, func_name, region))

    return removed


def reattach_handlers(removed):
    """外した描画ハンドラを登録し直す。

    draw_handler_remove で解除したハンドルは再利用できないため、
    描画関数をモジュールから取り直して登録し直す。
    引数はアドオン側の register() と同じにしてある。
    """
    namespace = bpy.app.driver_namespace
    restored = 0

    for module, holder_kind, holder_key, func_name, region in removed:
        func = getattr(module, func_name, None)
        if func is None:
            print(f"  戻せませんでした: {func_name} が見つかりません")
            continue

        try:
            handle = bpy.types.SpaceView3D.draw_handler_add(
                func, (), 'WINDOW', region
            )
        except Exception as exc:
            print(f"  戻せませんでした: {func_name} ({exc})")
            continue

        if holder_kind == "global":
            setattr(module, holder_key, handle)
        else:
            namespace[getattr(module, holder_key)] = handle
        restored += 1

    return restored


def measure(label, duration=PHASE_SECONDS):
    """指定秒数のあいだ連続再描画し、1フレームあたりの時間を返す。"""
    samples = []

    def sample_cb():
        samples.append(time.perf_counter())

    handle = bpy.types.SpaceView3D.draw_handler_add(
        sample_cb, (), 'WINDOW', 'POST_PIXEL'
    )
    try:
        areas = [
            area
            for window in bpy.context.window_manager.windows
            for area in window.screen.areas
            if area.type == 'VIEW_3D'
        ]
        if not areas:
            print("  3Dビューが見つかりません")
            return None

        start = time.perf_counter()
        while time.perf_counter() - start < duration:
            for area in areas:
                area.tag_redraw()
            # 再描画をBlenderに処理させる
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')

    if len(samples) < 3:
        print(f"  {label}: サンプル不足（{len(samples)}件）")
        return None

    deltas = sorted(
        (samples[i] - samples[i - 1]) * 1000.0
        for i in range(1, len(samples))
    )
    median = deltas[len(deltas) // 2]
    print(
        f"  {label:<28} 中央値 {median:7.2f} ms"
        f"  ({1000.0 / median if median else 0:6.1f} fps 相当)"
        f"  フレーム数 {len(samples)}"
    )
    return median


def main():
    print("=" * 72)
    print("ビューポート再描画コストの切り分け")
    print("=" * 72)

    modules = find_modules()
    if not modules:
        print("Truescale が読み込まれていません。アドオンを有効にしてください。")
        return

    print(f"検出したモジュール: {', '.join(sorted(modules))}")
    scene = bpy.context.scene
    print(f"読み込み中の元モデル: {scene.get('tsunfold_seam_source', '（なし）')}")
    print(f"シーン内オブジェクト数: {len(bpy.data.objects)}")
    print("-" * 72)

    with_handlers = measure("ハンドラあり（現状）")

    print("\n  描画ハンドラを一時的に外します...")
    removed = detach_handlers(modules)
    print(f"  外した数: {len(removed)}")

    try:
        without_handlers = measure("ハンドラなし")
    finally:
        # 何があっても必ず戻す
        print("\n  描画ハンドラを戻します...")
        restored = reattach_handlers(removed)
        print(f"  戻した数: {restored} / {len(removed)}")
        for area in (
            area
            for window in bpy.context.window_manager.windows
            for area in window.screen.areas
            if area.type == 'VIEW_3D'
        ):
            area.tag_redraw()

    print("\n" + "-" * 72)
    if with_handlers and without_handlers:
        diff = with_handlers - without_handlers
        print(f"  Truescale の描画コスト: {diff:.2f} ms / フレーム")
        if without_handlers > 0:
            ratio = diff / with_handlers * 100.0
            print(f"  全体に占める割合      : {ratio:.1f} %")
        print()
        if diff < 1.0:
            print("  判定: このアドオンの描画は重さの原因ではありません。")
            print("        他アドオン・シーン構成・GPU設定を疑ってください。")
        elif diff < 5.0:
            print("  判定: 多少の負荷はありますが、主因とは言えません。")
        else:
            print("  判定: このアドオンの描画が重さの主因です。")
            print("        描画ハンドラの中身を最適化する価値があります。")

    print("=" * 72)


main()
