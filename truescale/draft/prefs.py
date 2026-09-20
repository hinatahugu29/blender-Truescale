"""アドオン設定（ユーザー設定に出るもの）。

シーンごとではなく、このアドオン全体に効く設定。

■ bl_idname はアドオン本体のパッケージ名

サブパッケージ名（...draft）ではなく、アドオン本体の名前で
なければ Blender が設定画面へ結び付けられない。このモジュールは
<アドオン>.draft.prefs として読み込まれるので、末尾を2つ落とす。
"""

import bpy

from .. import debug as _debug


# AddonPreferences の bl_idname は、サブパッケージ名ではなく
# アドオン本体のパッケージ名でなければならない。
# このモジュールは <アドオン>.draft として読み込まれるので、
# 末尾の ".draft" を落としたものが本体のパッケージ名になる。
ADDON_PACKAGE = __package__.rpartition(".")[0].rpartition(".")[0]


def get_addon_preferences(context=None):
    context = context or bpy.context
    try:
        addon = context.preferences.addons.get(ADDON_PACKAGE)
        if addon is not None:
            return addon.preferences
    except Exception:
        _debug.swallowed("draft.get_addon_preferences")
    return None


class TSDRAFT_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    show_dark_place_button: bpy.props.BoolProperty(
        name="暗所表示のボタンを出す",
        description="暗所表示の切り替えボタンをパネルに出します",
        default=True
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="表示")
        layout.prop(self, "show_dark_place_button")
