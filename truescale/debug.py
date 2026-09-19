"""握り潰した例外を、必要なときだけ見えるようにする。

このアドオンには「失敗しても処理を続けたい」箇所が多くある。
描画ハンドラやプロパティのコールバックで例外を投げると、
Blender のUI操作そのものを壊してしまうためで、方針としては妥当。

ただし全て `except Exception: pass` で黙って捨てていたため、
何かがおかしいときに手がかりが一切残らなかった。実際、動作が重い
原因を調べる際に「例外が出ているのか正常に遅いのか」が判別できず、
切り分けを何度もやり直すことになった。

かといって常に出力すると、毎フレーム走る描画ハンドラでコンソールが
埋まって使い物にならない。そこで既定では黙ったままにし、
環境変数で有効にしたときだけ出す。

有効にする方法（Blender を起動する前に設定する）:

    Windows (PowerShell)   $env:TRUESCALE_DEBUG = "1"
    Windows (cmd)          set TRUESCALE_DEBUG=1
    macOS / Linux          export TRUESCALE_DEBUG=1

Blender の Python コンソールから一時的に切り替えることもできる:

    import truescale.debug as d
    d.set_enabled(True)
"""

import os
import traceback

_enabled = bool(os.environ.get("TRUESCALE_DEBUG"))

# 同じ場所のエラーを何度も出さないための記録
_seen = set()


def is_enabled():
    return _enabled


def set_enabled(value):
    """実行中に出力の有無を切り替える。"""
    global _enabled
    _enabled = bool(value)
    if not _enabled:
        _seen.clear()


def reset():
    """一度出した場所をもう一度出せるようにする。"""
    _seen.clear()


def swallowed(where=""):
    """握り潰す例外を記録する。except 節から呼ぶ。

    同じ場所は1回だけ出す。毎フレーム走る箇所で同じ例外が続くと、
    出力そのものが重さの原因になるため。
    """
    if not _enabled:
        return

    if where in _seen:
        return
    _seen.add(where)

    # print_exc() は stderr へ出るため、見出しと順序が入れ替わることがある。
    # 同じ stdout にまとめて出す。
    print(
        f"[Truescale] 例外を握り潰しました: {where or '(場所不明)'}\n"
        + traceback.format_exc().rstrip()
    )
