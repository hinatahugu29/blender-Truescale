"""オペレータ。ボタンを押したときに走るもの。

  bbox    寸法の箱を作る・消す・表示を切り替える
  view    視点の切り替えと、作図用表示の適用／復帰
  export  図面の書き出し

クラス名と bl_idname は分割前のまま。パネルや保存済みの
キーマップが名前で参照している。
"""

from . import bbox
from . import export
from . import view

classes = bbox.classes + view.classes + export.classes
