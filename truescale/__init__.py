"""Truescale — Blenderのジオメトリを実寸のまま紙へ出すためのツール群。

2つの機能をまとめて提供する。

  unfold … シーム付きモデルから実寸の型紙（展開図）を作る
  draft  … Bounding Box・寸法表示・三面図シートを作る

どちらも3DビューのNパネル「Truescale」タブに、
それぞれのパネルとして並ぶ。

前身:
  型紙ヘルパー カスタムシーン v1.5.6  → unfold
  造形ヘルパー v2.4.13                → draft
"""

import traceback

# bpy を使うサブモジュールは register() のときに読み込む。
#
# ここで即座に import すると、Blender非依存のモジュール
# （export.png など）を取り出すだけでも bpy が必要になり、
# Blenderを起動せずに単体でテストできなくなる。
_MODULES = ()


def _load_modules():
    """登録対象のサブモジュールを読み込む。登録順に返す。"""
    global _MODULES
    if not _MODULES:
        from . import unfold
        from . import draft
        _MODULES = (unfold, draft)
    return _MODULES


def register():
    registered = []
    try:
        for module in _load_modules():
            module.register()
            registered.append(module)
    except Exception:
        # 途中で失敗したら、それまでに登録した分を巻き戻してから送出する。
        # 半端に登録された状態で放置すると、次回の有効化が失敗する。
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:
                traceback.print_exc()
        raise


def unregister():
    for module in reversed(_load_modules()):
        try:
            module.unregister()
        except Exception:
            # 片方の解除失敗で、もう片方が解除されないのを防ぐ。
            # 握り潰さずに内容は必ず出す。
            traceback.print_exc()
