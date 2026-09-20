"""実寸で書き出すオペレータ。

画面に見えているものではなく、紙に出るものを描く。

  1枚で出す   … 用紙に収まる型紙。PNG も PDF も選べる
  分割して出す … 収まらない型紙。紙をまたいで分け、貼り合わせる

実寸で出すことが目的なので、収まらないからといって縮めることは
決してしない。縮んだ型紙は、黙って間違ったものを刷ることになる。
"""

import bpy
from pathlib import Path

from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from ... import debug as _debug
from ...core import geometry as _geometry
from ...core import objects as _objects
from ...core import paper as _paper
from ...core import session as _session
from ...core import state as _state
from ...core import units as _units
from ...core import view as _view
from ...export import outline as _outline
from ...export import collect as _collect
from ...export import png as _png
from ...export import render as _render
from ...export import sheets as _sheets
from ...export import tiling as _tiling
from ...marking import auto_notch as _auto_notch
from ...marking import compute as _compute
from ...marking import interact as _interact
from ...marking import seams as _seams
from ...marking import source as _source
from ...marking import storage as _storage
from ...marking import symmetry as _symmetry
from ...marking import tools as _tools
from .. import build as _build


def _overlay():
    """描画。循環importを避けるため、呼ばれた時に読み込む。"""
    from ... import overlay
    return overlay


def _paper_for(context, drawing):
    """いま選ばれている用紙の寸法。向きの自動も見る。"""
    return _outline.paper_dimensions(
        context.scene, drawing.width_mm, drawing.height_mm
    )


def tile_plan(context):
    """いまの型紙を分割するとどうなるか。書き出さずに調べる。

    パネルが枚数を出すのにも使う。押す前に何枚になるか分かれば、
    30枚だと気付いた時点で用紙を変えられる。
    """
    drawing = _collect.pattern_lines(context)
    if drawing is None:
        return None, None

    paper_w, paper_h = _paper_for(context, drawing)
    scene = context.scene

    plan = _tiling.plan(
        drawing.width_mm,
        drawing.height_mm,
        paper_w,
        paper_h,
        margin=float(getattr(scene, "tsunfold_tile_margin_mm", 8.0)),
        overlap=float(getattr(scene, "tsunfold_tile_overlap_mm", 15.0)),
    )
    return drawing, plan


class TSUNFOLD_OT_export_sheets(bpy.types.Operator, ExportHelper):
    """用紙に合わせて書き出す。収まらなければ分割する。"""

    bl_idname = "truescale_unfold.export_sheets"
    bl_label = "実寸で書き出し"
    bl_description = (
        "選択した型紙を実寸で書き出します。用紙に収まらない場合は"
        "分割し、貼り合わせるための目印を入れます"
    )

    filename_ext = ""
    filter_glob: StringProperty(default="*.pdf;*.png", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _outline.can_export(context)

    def invoke(self, context, event):
        obj = context.active_object
        suffix = (
            ".pdf"
            if str(getattr(context.scene, "tsunfold_export_format", "PDF"))
            == "PDF"
            else ".png"
        )
        self.filename_ext = suffix
        name = bpy.path.clean_name(obj.name) + suffix

        last_dir = context.scene.get(_session.LAST_EXPORT_DIR, "")
        if last_dir and Path(last_dir).exists():
            self.filepath = str(Path(last_dir) / name)
        else:
            self.filepath = name

        return super().invoke(context, event)

    def execute(self, context):
        scene = context.scene
        drawing, plan = tile_plan(context)

        if drawing is None:
            self.report({'ERROR'}, "書き出せる線がありません。")
            return {'CANCELLED'}
        if plan is None:
            self.report(
                {'ERROR'},
                "余白と重ねしろが用紙に対して大きすぎます。",
            )
            return {'CANCELLED'}

        margin = float(getattr(scene, "tsunfold_tile_margin_mm", 8.0))
        made = _sheets.single(
            drawing, plan.paper_w, plan.paper_h, margin=margin
        )
        if made is None:
            made = _sheets.tiled(drawing, plan)

        if not made:
            self.report({'ERROR'}, "書き出す紙がありません。")
            return {'CANCELLED'}

        path = Path(bpy.path.abspath(self.filepath))
        fmt = str(getattr(scene, "tsunfold_export_format", "PDF"))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if fmt == "PDF":
                written = self._write_pdf(path, made)
            else:
                written = self._write_png(path, made)
        except Exception as exc:
            self.report({'ERROR'}, f"書き出せませんでした: {exc}")
            return {'CANCELLED'}

        scene[_session.LAST_EXPORT_DIR] = str(path.parent)

        size = f"{drawing.width_mm:.0f}×{drawing.height_mm:.0f} mm"
        if len(made) == 1:
            self.report({'INFO'}, f"{size} を1枚で書き出しました（{written}）")
        else:
            self.report(
                {'INFO'},
                f"{size} を {plan.describe()} に分けました（{written}）",
            )
        return {'FINISHED'}

    def _write_pdf(self, path, made):
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        pages = _render.to_pdf(path, made, title=path.stem)
        return f"{path.name} / {pages} ページ"

    def _write_png(self, path, made):
        # 作り始めてから落ちるより、先に断る
        total = _render.estimate_png_pixels(made)
        if total > 400_000_000:
            raise MemoryError(
                f"PNG {len(made)} 枚は大きすぎます（合計 {total / 1e6:.0f} 百万画素）。"
                "PDF を選ぶか、用紙を大きくしてください"
            )

        if len(made) == 1:
            target = path.with_suffix(".png")
            _render.to_png(target, made[0])
            return target.name

        stem = path.with_suffix("").name
        for sheet in made:
            target = path.with_name(f"{stem}_{sheet.label}.png")
            _render.to_png(target, sheet)
        return f"{stem}_*.png / {len(made)} 枚"


classes = (
    TSUNFOLD_OT_export_sheets,
)
