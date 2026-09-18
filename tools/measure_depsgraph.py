# depsgraph ハンドラの実行時間を、アドオン別に計測する
# ------------------------------------------------------------
# Blender の Scripting ワークスペースに貼って実行してください。
#
# 何をするか:
#   Blender に登録されている depsgraph_update_post ハンドラを
#   全て時間計測用のラッパーで包み、一定時間ぶんの実行時間を集計します。
#   Truescale だけでなく、他アドオンのハンドラも同じ土俵で測ります。
#
#   オブジェクトをGキーで動かしている間、depsgraph はマウス移動ごとに
#   更新されます。そのたびに全ハンドラが呼ばれるため、
#   重いハンドラが1つあるだけで移動操作全体がカクつきます。
#
# 使い方:
#   1. 型紙を作成済みの状態にする
#   2. このスクリプトを実行する
#   3. 表示された秒数のあいだ、3Dビューでオブジェクトを G で動かし続ける
#      （重いと感じる操作を実際に行ってください）
#   4. 時間が来ると System Console に集計が出ます
#
#   ハンドラは計測後に必ず元へ戻します。
# ------------------------------------------------------------

import time

import bpy

MEASURE_SECONDS = 12.0

_stats = {}          # 表示名 -> [呼ばれた回数, 合計ミリ秒]
_originals = None    # 元のハンドラ並び
_update_count = [0]  # depsgraph 更新そのものの回数


def _label(func):
    module = getattr(func, "__module__", "") or "?"
    name = getattr(func, "__name__", "") or repr(func)
    # bl_ext.user_default.truescale.draft -> truescale.draft
    if module.startswith("bl_ext."):
        module = ".".join(module.split(".")[2:])
    return f"{module}.{name}"


def _wrap(func):
    label = _label(func)

    def wrapper(scene, depsgraph):
        start = time.perf_counter()
        try:
            return func(scene, depsgraph)
        finally:
            entry = _stats.setdefault(label, [0, 0.0])
            entry[0] += 1
            entry[1] += (time.perf_counter() - start) * 1000.0

    # @persistent が付いていたら引き継ぐ。
    # 引き継がないと、計測中にファイルを読み込んだ時に挙動が変わる。
    if getattr(func, "_bpy_persistent", False):
        wrapper = bpy.app.handlers.persistent(wrapper)

    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__module__ = getattr(func, "__module__", __name__)
    return wrapper


def _counter(scene, depsgraph):
    _update_count[0] += 1


def install():
    global _originals
    handlers = bpy.app.handlers.depsgraph_update_post
    _originals = list(handlers)

    wrapped = [_wrap(func) for func in _originals]
    handlers.clear()
    handlers.extend(wrapped)
    handlers.append(bpy.app.handlers.persistent(_counter))

    print(f"  {len(_originals)} 個のハンドラを計測対象にしました:")
    for func in _originals:
        print(f"      {_label(func)}")


def restore():
    handlers = bpy.app.handlers.depsgraph_update_post
    handlers.clear()
    if _originals is not None:
        handlers.extend(_originals)


def report():
    print()
    print("=" * 72)
    print("depsgraph ハンドラ 実行時間の集計")
    print("=" * 72)

    updates = _update_count[0]
    print(f"  depsgraph 更新回数: {updates}")

    if updates == 0:
        print()
        print("  更新が1回も起きていません。")
        print("  計測中に3Dビューでオブジェクトを動かしてください。")
        print("=" * 72)
        return

    rows = sorted(_stats.items(), key=lambda kv: kv[1][1], reverse=True)
    total_ms = sum(v[1] for v in _stats.values())

    print(f"  ハンドラ合計時間  : {total_ms:.1f} ms")
    print(f"  1更新あたり       : {total_ms / updates:.3f} ms")
    print()
    print(f"  {'ハンドラ':<52}{'回数':>7}{'合計ms':>10}{'1回ms':>9}")
    print("  " + "-" * 76)

    for label, (count, ms) in rows:
        per = ms / count if count else 0.0
        print(f"  {label:<52}{count:>7}{ms:>10.1f}{per:>9.3f}")

    print()
    if total_ms / updates > 1.0:
        worst_label, (worst_count, worst_ms) = rows[0]
        share = worst_ms / total_ms * 100.0 if total_ms else 0.0
        print(f"  最も重いハンドラ: {worst_label}")
        print(f"  ハンドラ合計の {share:.0f}% を占めています。")
        print()
        print("  移動操作中は depsgraph がマウス移動ごとに更新されるため、")
        print("  1更新あたりのミリ秒がそのまま操作のカクつきになります。")
    else:
        print("  どのハンドラも軽く、これが移動の重さの原因とは考えにくいです。")
    print("=" * 72)


class TSMEASURE_OT_depsgraph(bpy.types.Operator):
    bl_idname = "tsmeasure.depsgraph"
    bl_label = "depsgraph ハンドラを計測"

    _timer = None
    _start = 0.0

    def modal(self, context, event):
        if event.type == 'TIMER':
            elapsed = time.perf_counter() - self._start
            remain = MEASURE_SECONDS - elapsed
            if remain <= 0:
                self.finish(context)
                return {'FINISHED'}
            context.workspace.status_text_set(
                f"計測中... 残り {remain:.0f} 秒 / "
                f"オブジェクトを G で動かしてください"
            )
        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        _stats.clear()
        _update_count[0] = 0
        install()

        self._start = time.perf_counter()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        print(f"\n  計測開始（{MEASURE_SECONDS:.0f}秒）")
        print("  3Dビューでオブジェクトを G キーで動かしてください\n")
        return {'RUNNING_MODAL'}

    def finish(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)
        restore()
        report()


def run():
    try:
        bpy.utils.unregister_class(TSMEASURE_OT_depsgraph)
    except Exception:
        pass
    bpy.utils.register_class(TSMEASURE_OT_depsgraph)

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                with bpy.context.temp_override(window=window, area=area):
                    bpy.ops.tsmeasure.depsgraph('INVOKE_DEFAULT')
                return
    print("3Dビューが見つかりません。Layout ワークスペースで実行してください。")


run()
