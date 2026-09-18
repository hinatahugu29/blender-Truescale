# ビューポート再描画速度の実測
# ------------------------------------------------------------
# 使い方:
#   1. Layout ワークスペース（3Dビューが見える状態）で実行
#   2. 約5秒間、自動で再描画を繰り返します
#   3. 終わると System Console に結果が出ます
#
# 比較のしかた:
#   A) 型紙ヘルパー 有効 + 型紙あり   で実行
#   B) 型紙ヘルパー 有効 + 型紙削除後 で実行
#   C) 型紙ヘルパー 無効             で実行
#   → どこで遅くなるかが分かります
#
# アドオンには一切触りません。計測のみです。
# ------------------------------------------------------------

import bpy
import time

DURATION = 5.0

_samples = []
_handle = None


def _sample_cb():
    _samples.append(time.perf_counter())


class VIEWPORT_OT_measure_fps(bpy.types.Operator):
    bl_idname = "viewport.measure_fps"
    bl_label = "ビューポート描画速度を計測"

    _timer = None
    _start = 0.0

    def modal(self, context, event):
        if event.type == 'TIMER':
            if time.perf_counter() - self._start >= DURATION:
                self._finish(context)
                return {'FINISHED'}
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        if event.type == 'ESC':
            self._finish(context)
            return {'CANCELLED'}
        return {'PASS_THROUGH'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        global _handle, _samples
        _samples = []
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _sample_cb, (), 'WINDOW', 'POST_PIXEL'
        )
        self._start = time.perf_counter()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.001, window=context.window)
        wm.modal_handler_add(self)
        self.report({'INFO'}, f"{DURATION:.0f}秒間 計測中...")
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        global _handle
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        if _handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
            _handle = None

        print("=" * 70)
        print("ビューポート再描画速度の実測")
        print("=" * 70)

        n = len(_samples)
        if n < 3:
            print(f"!! サンプルが足りません（{n}件）。3Dビューが見える状態で実行してください")
        else:
            deltas = [
                (_samples[i] - _samples[i - 1]) * 1000.0
                for i in range(1, n)
            ]
            deltas.sort()
            total = (_samples[-1] - _samples[0])
            avg = sum(deltas) / len(deltas)
            med = deltas[len(deltas) // 2]
            p95 = deltas[int(len(deltas) * 0.95)]

            scene = bpy.context.scene
            src = scene.get("pattern_helper_seam_source", "")
            print(f"読み込み中の元モデル : {src if src else '（なし）'}")
            print("-" * 70)
            print(f"フレーム数   : {n}")
            print(f"計測時間     : {total:.2f} 秒")
            print(f"平均FPS      : {(n - 1) / total:.1f} fps")
            print("-" * 70)
            print(f"1フレーム 中央値 : {med:7.2f} ms")
            print(f"1フレーム 平均   : {avg:7.2f} ms")
            print(f"1フレーム 最遅5% : {p95:7.2f} ms")
            print(f"1フレーム 最速   : {deltas[0]:7.2f} ms")
            print(f"1フレーム 最遅   : {deltas[-1]:7.2f} ms")
            print("-" * 70)
            if med < 8:
                print("判定: 軽い（120fps相当以上）")
            elif med < 17:
                print("判定: 普通（60fps相当）")
            elif med < 33:
                print("判定: やや重い（30fps相当）")
            else:
                print("判定: 重い（30fps未満）← 体感でカクつくレベル")
        print("=" * 70)


def _run():
    try:
        bpy.utils.unregister_class(VIEWPORT_OT_measure_fps)
    except Exception:
        pass
    bpy.utils.register_class(VIEWPORT_OT_measure_fps)

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                with bpy.context.temp_override(window=window, area=area):
                    bpy.ops.viewport.measure_fps('INVOKE_DEFAULT')
                print(f"計測開始（{DURATION:.0f}秒）... 終わるまでお待ちください")
                return
    print("!! 3Dビューが見つかりません。Layout ワークスペースで実行してください")


_run()
