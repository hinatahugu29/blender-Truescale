"""実寸PNGの書き出しオペレータ。

画面に見えているものではなく、紙に出るものを描く。線を集めるのは
export.outline、ピクセルにするのは export.png。

実寸で出すことが目的なので、用紙に収まらないときは縮めずに
そのまま大きな画像として出す。300dpi の pHYs を埋めるので、
印刷側が原寸で刷れる。
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
from ...export import png as _png
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


class TSUNFOLD_OT_export_png(bpy.types.Operator, ExportHelper):
    bl_idname = "truescale_unfold.export_png"
    bl_label = "実寸PNGを書き出し"
    bl_description = "選択した展開図を実寸PNGとして300dpiで書き出します"

    filename_ext = ".png"
    filter_glob: StringProperty(default="*.png", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _outline.can_export(context)

    def invoke(self, context, event):
        obj = context.active_object
        filename = bpy.path.clean_name(obj.name) + ".png"

        last_dir = context.scene.get(_session.LAST_EXPORT_DIR, "")
        if last_dir and Path(last_dir).exists():
            self.filepath = str(Path(last_dir) / filename)
        else:
            self.filepath = filename

        return super().invoke(context, event)

    def execute(self, context):
        obj = context.active_object
        scene = context.scene

        segments = _outline.current_finish_segments(context)
        bbox = _outline.bbox(segments)

        if bbox is None:
            self.report({'ERROR'}, "印刷できる外周線がありません。")
            return {'CANCELLED'}

        min_x, min_y, max_x, max_y = bbox
        bu_to_mm = _units.scene_scale_to_meters(scene) * 1000.0

        shape_w_mm = (max_x - min_x) * bu_to_mm
        shape_h_mm = (max_y - min_y) * bu_to_mm

        paper_w_mm, paper_h_mm = _outline.paper_dimensions(
            scene,
            shape_w_mm,
            shape_h_mm,
        )

        if shape_w_mm > paper_w_mm + 1e-6 or shape_h_mm > paper_h_mm + 1e-6:
            self.report(
                {'ERROR'},
                f"展開図 {shape_w_mm:.1f}×{shape_h_mm:.1f} mm は "
                f"{_paper.scene_display_name(scene)} に収まりません。"
            )
            return {'CANCELLED'}

        px_per_mm = _png.PRINT_DPI / 25.4
        width_px = int(round(paper_w_mm * px_per_mm))
        height_px = int(round(paper_h_mm * px_per_mm))

        total_pixels = width_px * height_px
        if total_pixels > 160_000_000:
            self.report({'ERROR'}, f"画像が大きすぎます ({width_px}×{height_px}px)")
            return {'CANCELLED'}

        try:
            buffer = bytearray(b"\xff" * (width_px * height_px * 3))
        except MemoryError:
            self.report({'ERROR'}, "PNG作成用のメモリを確保できませんでした。")
            return {'CANCELLED'}

        # 1) Pattern outline: always black.
        for (ax, ay), (bx, by) in segments:
            x1_mm = _units.scene_bu_to_mm(scene, ax)
            y1_mm = _units.scene_bu_to_mm(scene, ay)
            x2_mm = _units.scene_bu_to_mm(scene, bx)
            y2_mm = _units.scene_bu_to_mm(scene, by)

            x1 = x1_mm * px_per_mm
            y1 = height_px - (y1_mm * px_per_mm)
            x2 = x2_mm * px_per_mm
            y2 = height_px - (y2_mm * px_per_mm)

            _png.draw_line(
                buffer,
                width_px,
                height_px,
                x1,
                y1,
                x2,
                y2,
                thickness=2,
                color=(0.0, 0.0, 0.0),
            )

        source = _objects.source_from_context(context)
        unfold = _objects.unfold_for_source(source) if source else None

        # 2) Colored geometric annotations.
        if source is not None and unfold is not None:
            for wa, wb, color, width_mm in _compute.colored_segments(
                context,
                source,
                unfold,
            ):
                x1 = _units.scene_bu_to_mm(scene, wa.x) * px_per_mm
                y1 = height_px - (_units.scene_bu_to_mm(scene, wa.y) * px_per_mm)
                x2 = _units.scene_bu_to_mm(scene, wb.x) * px_per_mm
                y2 = height_px - (_units.scene_bu_to_mm(scene, wb.y) * px_per_mm)

                _png.draw_line(
                    buffer,
                    width_px,
                    height_px,
                    x1,
                    y1,
                    x2,
                    y2,
                    thickness=max(
                        1,
                        int(round(float(width_mm) * px_per_mm)),
                    ),
                    color=color,
                )

            # 3) Number / arbitrary text as Blender FONT outline geometry.
            for text, world_pos, size_mm, color in _overlay().flat_text_items(
                source,
                unfold,
                context.scene,
            ):
                for wa, wb in _outline.text_segments(
                    context,
                    text,
                    world_pos,
                    size_mm,
                ):
                    x1 = _units.scene_bu_to_mm(scene, wa.x) * px_per_mm
                    y1 = height_px - (_units.scene_bu_to_mm(scene, wa.y) * px_per_mm)
                    x2 = _units.scene_bu_to_mm(scene, wb.x) * px_per_mm
                    y2 = height_px - (_units.scene_bu_to_mm(scene, wb.y) * px_per_mm)

                    _png.draw_line(
                        buffer,
                        width_px,
                        height_px,
                        x1,
                        y1,
                        x2,
                        y2,
                        thickness=2,
                        color=color,
                    )


            # 4) Flat-only memo text.
            for text, world_pos, size_mm, color, angle in _overlay().flat_memo_text_items(
                unfold
            ):
                for wa, wb in _outline.text_segments(
                    context,
                    text,
                    world_pos,
                    size_mm,
                    angle,
                ):
                    x1 = _units.scene_bu_to_mm(scene, wa.x) * px_per_mm
                    y1 = height_px - (_units.scene_bu_to_mm(scene, wa.y) * px_per_mm)
                    x2 = _units.scene_bu_to_mm(scene, wb.x) * px_per_mm
                    y2 = height_px - (_units.scene_bu_to_mm(scene, wb.y) * px_per_mm)

                    _png.draw_line(
                        buffer,
                        width_px,
                        height_px,
                        x1,
                        y1,
                        x2,
                        y2,
                        thickness=2,
                        color=color,
                    )

            # 5) Automatic island IDs / connection labels with orientation.
            if bool(
                getattr(
                    scene,
                    "tsunfold_auto_island_ids",
                    True,
                )
            ):
                for (
                    text,
                    world_pos,
                    size_mm,
                    color,
                    angle,
                    _edge_locked,
                ) in _compute.text_items(
                    context,
                    source,
                    unfold,
                ):
                    for wa, wb in _outline.text_segments(
                        context,
                        text,
                        world_pos,
                        size_mm,
                        angle,
                    ):
                        x1 = _units.scene_bu_to_mm(scene, wa.x) * px_per_mm
                        y1 = height_px - (_units.scene_bu_to_mm(scene, wa.y) * px_per_mm)
                        x2 = _units.scene_bu_to_mm(scene, wb.x) * px_per_mm
                        y2 = height_px - (_units.scene_bu_to_mm(scene, wb.y) * px_per_mm)

                        _png.draw_line(
                            buffer,
                            width_px,
                            height_px,
                            x1,
                            y1,
                            x2,
                            y2,
                            thickness=2,
                            color=color,
                        )

        filepath = Path(bpy.path.abspath(self.filepath))
        if filepath.suffix.lower() != ".png":
            filepath = filepath.with_suffix(".png")

        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            _png.write_rgb(filepath, width_px, height_px, buffer, _png.PRINT_DPI)
        except Exception as exc:
            self.report({'ERROR'}, f"PNGを書き出せませんでした: {exc}")
            return {'CANCELLED'}

        # Remember the directory used for the latest successful export.
        context.scene[_session.LAST_EXPORT_DIR] = str(filepath.parent)

        self.report(
            {'INFO'},
            f"実寸PNGを書き出しました / {_png.PRINT_DPI}dpi / 次回もこの保存先を開きます"
        )
        return {'FINISHED'}


classes = (
    TSUNFOLD_OT_export_png,
)
