"""オペレータ。ボタンを押したときに走るもの。

機能ごとにファイルを分けてある。どれも「段取り」を持つだけで、
実際の計算や描画は core / marking / export が持つ。オペレータに
計算を書くと、テストから呼べず、別のオペレータから使い回せない。

  seam     どこで切り開くか、実寸の基準合わせ
  pattern  型紙の生成と表示の切り替え
  layout   用紙への配置
  notch    合印の生成と削除
  marking  印を置く道具と、置いた印の削除
  memo     型紙に直接書く文字、島の対応付け
  export   実寸PNGの書き出し
  allowance 糊代を辺ごとに消す／戻す

クラス名と bl_idname は分割前のまま。パネルや保存済みのキーマップが
名前で参照しているので、置き場所だけを変えている。
"""

from . import allowance
from . import export
from . import layout
from . import marking
from . import memo
from . import notch
from . import pattern
from . import seam

# 登録する順。パネルより先に読まれていればよいので、並びは機能順。
classes = (
    seam.classes
    + pattern.classes
    + layout.classes
    + notch.classes
    + marking.classes
    + memo.classes
    + export.classes
    + allowance.classes
)
