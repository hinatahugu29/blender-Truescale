"""3Dビューの再描画要求。

設定を変えただけでは画面は描き直されない。値を書き換えたあとに
ここを呼んで初めて反映される。呼び忘れると「変えたのに変わらない」
という形で現れる。

重い処理はしないので、迷ったら呼んでよい。
"""

import bpy


def tag_redraw():
    """開いている3Dビューすべてに再描画を要求する。"""
    wm = bpy.context.window_manager if bpy.context else None
    if not wm:
        return

    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
