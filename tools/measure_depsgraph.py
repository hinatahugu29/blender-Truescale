# depsgraph ハンドラの実行時間を、アドオン別に計測する
# ------------------------------------------------------------
# Blender の Scripting ワークスペースに貼って実行してください。
#
# 使い方は2段階です。
#
#   1回目の実行 … 計測を開始する（すぐ戻ります）
#   → Layout へ移り、重いと感じる移動を実際に行う
#   2回目の実行 … 計測を止めて、System Console に集計を出す
#
# 何を測るか:
#   Blender に登録されている depsgraph_update_post ハンドラを
#   全て時間計測用のラッパーで包みます。Truescale だけでなく
#   他アドオンのハンドラも同じ土俵で測ります。
#
#   オブジェクトを動かしている間、depsgraph はマウス移動ごとに
#   更新されます。そのたびに全ハンドラが呼ばれるため、
#   重いハンドラが1つあれば移動操作全体がカクつきます。
#
# 実装上の注意:
#   Blender のテキストエディタは実行のたびに新しい __main__ を作るため、
#   モジュールのグローバル変数は再実行で作り直されてしまう。
#   登録済みのラッパーは古い変数を掴んだままになり、集計が空になる。
#   そのため計測状態は bpy.app.driver_namespace に置いている。
# ------------------------------------------------------------

import inspect
import time

import bpy

STATE_KEY = "TSMEASURE_DEPSGRAPH_STATE"


def label_of(func):
    module = getattr(func, "__module__", "") or "?"
    name = getattr(func, "__name__", "") or repr(func)
    # bl_ext.user_default.truescale.draft -> truescale.draft
    if module.startswith("bl_ext."):
        module = ".".join(module.split(".")[2:])
    return f"{module}.{name}"


def param_count(func):
    """ハンドラが受け取る引数の数を調べる。

    Blender のハンドラには (scene) だけを取るものと
    (scene, depsgraph) を取るものがある。決め打ちで渡すと
    TypeError になり、相手のハンドラを壊してしまう。
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return 2

    for param in params.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            return 2

    positional = [
        p for p in params.values()
        if p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    return len(positional)


def make_wrapper(func, state):
    label = label_of(func)
    takes_depsgraph = param_count(func) >= 2
    stats = state["stats"]

    def wrapper(scene, depsgraph):
        state["updates_partial"] += 1
        start = time.perf_counter()
        try:
            if takes_depsgraph:
                return func(scene, depsgraph)
            return func(scene)
        finally:
            entry = stats.setdefault(label, [0, 0.0])
            entry[0] += 1
            entry[1] += (time.perf_counter() - start) * 1000.0

    if getattr(func, "_bpy_persistent", False):
        wrapper = bpy.app.handlers.persistent(wrapper)

    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__module__ = getattr(func, "__module__", __name__)
    # 復元と、二重ラップの検出に使う
    wrapper._tsmeasure_original = func
    return wrapper


def unwrap_all():
    """過去の計測が残していたラッパーを全て剥がす。

    計測を中断した場合などにラッパーが残ることがある。
    そのまま再度包むと多重ラップになるので、毎回先に剥がす。
    """
    handlers = bpy.app.handlers.depsgraph_update_post
    cleaned = []
    removed = 0

    for func in list(handlers):
        original = func
        while hasattr(original, "_tsmeasure_original"):
            original = original._tsmeasure_original
            removed += 1
        # 計測用カウンタそのものは復元対象に含めない
        if getattr(original, "_tsmeasure_counter", False):
            continue
        cleaned.append(original)

    handlers.clear()
    handlers.extend(cleaned)
    return removed


def start():
    peeled = unwrap_all()
    if peeled:
        print(f"  前回のラッパー {peeled} 個を剥がしました")

    handlers = bpy.app.handlers.depsgraph_update_post
    state = {
        "stats": {},
        "updates_partial": 0,
        "started": time.perf_counter(),
        "originals": list(handlers),
    }

    wrapped = [make_wrapper(func, state) for func in state["originals"]]
    handlers.clear()
    handlers.extend(wrapped)

    bpy.app.driver_namespace[STATE_KEY] = state

    print("=" * 72)
    print("depsgraph ハンドラ 計測開始")
    print("=" * 72)
    print(f"  計測対象 {len(state['originals'])} 個:")
    for func in state["originals"]:
        args = "scene, depsgraph" if param_count(func) >= 2 else "scene"
        print(f"      {label_of(func)}  ({args})")
    print()
    print("  Layout へ移り、重いと感じる移動を実際に行ってください。")
    print("  終わったら、このスクリプトをもう一度実行すると集計が出ます。")
    print("=" * 72)


def stop():
    state = bpy.app.driver_namespace.get(STATE_KEY)
    unwrap_all()
    bpy.app.driver_namespace.pop(STATE_KEY, None)

    stats = state["stats"]
    elapsed = time.perf_counter() - state["started"]

    # 各ハンドラの呼ばれた回数の最大値が、depsgraph 更新の回数にあたる
    updates = max((v[0] for v in stats.values()), default=0)

    print()
    print("=" * 72)
    print("depsgraph ハンドラ 実行時間の集計")
    print("=" * 72)
    print(f"  計測時間          : {elapsed:.1f} 秒")
    print(f"  depsgraph 更新回数: {updates}")

    if updates == 0:
        print()
        print("  更新が1回も起きていません。")
        print("  計測を開始したあとに、3Dビューでオブジェクトを動かしてください。")
        print("=" * 72)
        return

    rows = sorted(stats.items(), key=lambda kv: kv[1][1], reverse=True)
    total_ms = sum(v[1] for v in stats.values())

    print(f"  ハンドラ合計時間  : {total_ms:.1f} ms")
    print(f"  1更新あたり       : {total_ms / updates:.3f} ms")
    print()
    print(f"  {'ハンドラ':<54}{'回数':>7}{'合計ms':>10}{'1回ms':>9}")
    print("  " + "-" * 78)
    for name, (count, ms) in rows:
        per = ms / count if count else 0.0
        print(f"  {name:<54}{count:>7}{ms:>10.1f}{per:>9.3f}")

    per_update = total_ms / updates
    print()
    if per_update > 1.0:
        worst_name, (_, worst_ms) = rows[0]
        share = worst_ms / total_ms * 100.0 if total_ms else 0.0
        print(f"  最も重いハンドラ: {worst_name}（合計の {share:.0f}%）")
        print()
        print("  移動中は depsgraph がマウス移動ごとに更新されるため、")
        print("  1更新あたりのミリ秒がそのまま操作のカクつきになります。")
    else:
        print("  どのハンドラも軽く、これが移動の重さの原因とは考えにくいです。")
        print("  depsgraph ハンドラ以外（Blender本体の transform、")
        print("  対象メッシュそのものの重さ、GPU）を疑ってください。")
    print("=" * 72)


if bpy.app.driver_namespace.get(STATE_KEY) is None:
    start()
else:
    stop()
