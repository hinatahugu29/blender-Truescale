"""三面図を1枚の用紙にまとめる。

正面・上面・側面を撮り、縮尺を揃えて1枚へ並べる。

■ 三面の縮尺は必ず揃える

面ごとに「紙に収まるように」倍率を決めると、三面がばらばらの
縮尺になる。図面として読めなくなるので、一番大きい面に合わせた
1つの縮尺を全面へ使う。用紙に収まらなければ縮尺を落とす。

■ 位置を揃える

正面図と上面図は横方向が、正面図と側面図は縦方向が対応する。
並べるときにその対応を保つ。ばらばらに置くと、同じ寸法が
どこを指しているのか追えない。

■ 文字はビットマップで描く

用紙は生のピクセル配列として組み立てるので、blf は使えない
（blf は画面へ描くもの）。Blender のフォント機能で一度画像に
してから貼る。それも使えない環境向けに、簡易なビットマップ
フォントを持っている。
"""

import math
import os
from pathlib import Path
import blf
import bpy

from ... import debug as _pkg_debug
from ...core import paper as _paper
from .. import bbox as _bbox
from .. import viewstate as _viewstate
from .. import dimension as _dimension
from .. import keys as _keys
from .. import labels as _labels
from . import capture as _capture


_TSDRAFT_BITMAP_FONT = {
    'S': ("11111","10000","10000","11111","00001","00001","11111"),
    'C': ("11111","10000","10000","10000","10000","10000","11111"),
    'A': ("01110","10001","10001","11111","10001","10001","10001"),
    'L': ("10000","10000","10000","10000","10000","10000","11111"),
    'E': ("11111","10000","10000","11110","10000","10000","11111"),
    '0': ("01110","10001","10011","10101","11001","10001","01110"),
    '1': ("00100","01100","00100","00100","00100","00100","01110"),
    '2': ("01110","10001","00001","00010","00100","01000","11111"),
    '3': ("11110","00001","00001","01110","00001","00001","11110"),
    '4': ("00010","00110","01010","10010","11111","00010","00010"),
    '5': ("11111","10000","10000","11110","00001","00001","11110"),
    '6': ("01110","10000","10000","11110","10001","10001","01110"),
    '7': ("11111","00001","00010","00100","01000","01000","01000"),
    '8': ("01110","10001","10001","01110","10001","10001","01110"),
    '9': ("01110","10001","10001","01111","00001","00001","01110"),
    ':': ("00000","00100","00100","00000","00100","00100","00000"),
    '.': ("00000","00000","00000","00000","00000","00100","00100"),
    ' ': ("00000","00000","00000","00000","00000","00000","00000"),
}


def tsdraft_sheet_scale_denominator(scene):
    scale_key = getattr(scene, 'tsdraft_sheet_scale', '1_1')
    fixed = {
        '1_1': 1.0,
        '1_2': 2.0,
        '1_5': 5.0,
        '1_10': 10.0,
    }
    if scale_key == 'CUSTOM':
        return max(1.0, float(getattr(scene, 'tsdraft_sheet_custom_scale', 1.0)))
    return fixed.get(scale_key, 1.0)


def tsdraft_sheet_layout_aligned(view_data, page_w, page_h, margin=12.0, gutter=10.0, footer=12.0):
    """
    Conventional three-view layout using BBox FRAME positions as the alignment reference:
      TOP directly above FRONT, same frame left/right.
      SIDE directly left of FRONT, same frame top/bottom.
    Cropped PNG margins are allowed to differ without breaking projection alignment.
    """
    top = view_data['top']
    front = view_data['front']
    side = view_data['side']

    # Frame-space origin: front frame top-left = (0, top frame height + gutter).
    front_frame_x = 0.0
    front_frame_y = top['frame_h_mm'] + gutter
    top_frame_x = 0.0
    top_frame_y = 0.0
    side_frame_x = -(side['frame_w_mm'] + gutter)
    side_frame_y = front_frame_y

    frame_origins = {
        'top': (top_frame_x, top_frame_y),
        'front': (front_frame_x, front_frame_y),
        'side': (side_frame_x, side_frame_y),
    }

    # Convert frame origins to image top-left positions using each image's crop margins.
    raw = {}
    for key, item in view_data.items():
        fx, fy = frame_origins[key]
        raw[key] = (
            fx - item['left_mm'],
            fy - item['top_mm'],
        )

    min_x = min(raw[k][0] for k in raw)
    min_y = min(raw[k][1] for k in raw)
    max_x = max(raw[k][0] + view_data[k]['image_w_mm'] for k in raw)
    max_y = max(raw[k][1] + view_data[k]['image_h_mm'] for k in raw)

    content_w = max_x - min_x
    content_h = max_y - min_y

    avail_w = page_w - margin * 2.0
    avail_h = page_h - margin * 2.0 - footer
    if content_w > avail_w + 1e-6 or content_h > avail_h + 1e-6:
        return None, (content_w, content_h + footer)

    offset_x = (page_w - content_w) * 0.5 - min_x
    offset_y = margin + (avail_h - content_h) * 0.5 - min_y

    positions = {
        key: (raw[key][0] + offset_x, raw[key][1] + offset_y)
        for key in raw
    }
    return positions, (content_w, content_h + footer)


def tsdraft_draw_bitmap_text(canvas, text, x, y_top, scale=3):
    """Tiny dependency-free black bitmap label, top-left coordinate."""
    h, w, _ = canvas.shape
    x = int(x)
    y_top = int(y_top)
    cursor = x
    for ch in text.upper():
        glyph = _TSDRAFT_BITMAP_FONT.get(ch, _TSDRAFT_BITMAP_FONT[' '])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != '1':
                    continue
                x0 = cursor + gx * scale
                y0_top = y_top + gy * scale
                # Canvas uses Blender bottom-up rows.
                y0 = h - (y0_top + scale)
                x1 = min(w, x0 + scale)
                y1 = min(h, y0 + scale)
                if x1 > x0 and y1 > y0 and x0 >= 0 and y0 >= 0:
                    canvas[y0:y1, x0:x1, 0:3] = 0.0
                    canvas[y0:y1, x0:x1, 3] = 1.0
        cursor += 6 * scale


def tsdraft_sheet_text_rgba(text, font_size_px, color_rgba):
    """Render one text label to an RGBA numpy array using Blender's default font."""
    import numpy as np
    import imbuf

    font_id = 0
    font_size_px = max(8, int(round(font_size_px)))
    blf.size(font_id, font_size_px)
    text_w, text_h = blf.dimensions(font_id, text)

    pad = max(4, int(round(font_size_px * 0.28)))
    width = max(1, int(math.ceil(text_w)) + pad * 2)
    height = max(1, int(math.ceil(text_h)) + pad * 2)

    ib = imbuf.new((width, height), planes=32, buffer_type='BYTE')
    try:
        # Explicit transparent background.
        with ib.with_buffer(write=True) as buf:
            flat = buf.cast('B')
            flat[:] = bytes(len(flat))

        blf.size(font_id, font_size_px)
        blf.color(
            font_id,
            float(color_rgba[0]),
            float(color_rgba[1]),
            float(color_rgba[2]),
            float(color_rgba[3]),
        )
        blf.position(font_id, pad, pad, 0)

        with blf.bind_imbuf(font_id, ib, display_name="sRGB"):
            blf.draw_buffer(font_id, text)

        with ib.with_buffer() as buf:
            arr = np.asarray(buf, dtype=np.uint8).copy()
        return arr.astype(np.float32) / 255.0
    finally:
        try:
            ib.free()
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_sheet_text_rgba")


def tsdraft_sheet_alpha_blit(canvas, rgba, x0, y0):
    """Alpha-composite RGBA onto Blender-style bottom-up float canvas."""
    x0 = int(round(x0))
    y0 = int(round(y0))
    h, w = rgba.shape[0], rgba.shape[1]
    ch, cw = canvas.shape[0], canvas.shape[1]

    sx0 = 0
    sy0 = 0
    sx1 = w
    sy1 = h

    if x0 < 0:
        sx0 = -x0
        x0 = 0
    if y0 < 0:
        sy0 = -y0
        y0 = 0

    x1 = min(cw, x0 + (sx1 - sx0))
    y1 = min(ch, y0 + (sy1 - sy0))
    if x1 <= x0 or y1 <= y0:
        return

    sx1 = sx0 + (x1 - x0)
    sy1 = sy0 + (y1 - y0)

    src = rgba[sy0:sy1, sx0:sx1, :]
    dst = canvas[y0:y1, x0:x1, :]

    alpha = src[..., 3:4]
    dst[..., :3] = src[..., :3] * alpha + dst[..., :3] * (1.0 - alpha)
    dst[..., 3:4] = 1.0


def tsdraft_export_canvas_rgba(scene):
    mode = getattr(scene, 'tsdraft_export_background', 'WHITE')
    custom = getattr(scene, 'tsdraft_export_background_color', (1.0, 1.0, 1.0))
    rgb = _viewstate.tsdraft_background_color(mode, custom)
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0)


def tsdraft_new_background_canvas(np, height, width, rgba):
    canvas = np.empty((int(height), int(width), 4), dtype=np.float32)
    canvas[..., 0] = rgba[0]
    canvas[..., 1] = rgba[1]
    canvas[..., 2] = rgba[2]
    canvas[..., 3] = rgba[3]
    return canvas


def tsdraft_sheet_draw_dimension_labels(canvas, scene, positions, view_data, sheet_dpi, page_h_mm):
    """
    Draw sheet dimensions directly on the final raster.
    Horizontal dimensions remain horizontal.
    Vertical dimensions are raster-rotated 90 degrees, so no viewport BLF
    rotation/cropping ambiguity can occur.
    """
    import numpy as np

    namespace = bpy.app.driver_namespace
    data = namespace.get(_keys.DATA_KEY, [])
    if not data:
        return

    axis_text = {}
    for item in data:
        axis = str(item.get("axis", "")).upper()
        if axis in {"X", "Y", "Z"}:
            axis_text[axis] = _dimension.get_dimension_text(scene, item)

    if not axis_text:
        return

    # 図面シート文字は物理mm基準で描くが、UIの「文字サイズ」に素直に追従させる。
    # 20px -> 約3.0mm を基準に比例。極端値だけ広めに安全制限する。
    design_px = max(4.0, float(getattr(scene, "tsdraft_font_size", 20)))
    font_mm = min(12.0, max(1.2, design_px * 0.15))
    font_px = max(6.0, font_mm / 25.4 * float(sheet_dpi))

    color = getattr(scene, "tsdraft_font_color", (0.05, 0.05, 0.05, 1.0))
    gap_mm = max(1.2, font_mm * 0.42)

    # どの軸をどちら側へ置くかは draft.labels が持つ。ここにも同じ
    # 表があった。画面と、撮った画像の切り抜きと、このシートで
    # 3つになる。1つ直し忘れれば、画面と刷ったもので寸法の位置が
    # 変わる。
    for view_key, sides in _labels.SHEET_LAYOUT.items():
        if view_key not in positions or view_key not in view_data:
            continue

        dim_specs = tuple(sides.items())

        image_x_mm, image_y_top_mm = positions[view_key]
        vd = view_data[view_key]

        frame_left_mm = image_x_mm + vd["left_mm"]
        frame_top_mm = image_y_top_mm + vd["top_mm"]
        frame_right_mm = frame_left_mm + vd["frame_w_mm"]
        frame_bottom_mm = frame_top_mm + vd["frame_h_mm"]

        for axis, side in dim_specs:
            text = axis_text.get(axis)
            if not text:
                continue

            label = tsdraft_sheet_text_rgba(text, font_px, color)

            if side in {"LEFT", "RIGHT"}:
                # np.rot90 rotates the actual glyph raster. This is deterministic
                # even if viewport BLF rotation behaves differently by version.
                label = np.rot90(label, k=1).copy()

            label_h, label_w = label.shape[0], label.shape[1]
            label_w_mm = label_w / sheet_dpi * 25.4
            label_h_mm = label_h / sheet_dpi * 25.4

            if side == "TOP":
                x_mm = (frame_left_mm + frame_right_mm) * 0.5 - label_w_mm * 0.5
                y_top_mm = frame_top_mm - gap_mm - label_h_mm

            elif side == "BOTTOM":
                x_mm = (frame_left_mm + frame_right_mm) * 0.5 - label_w_mm * 0.5
                y_top_mm = frame_bottom_mm + gap_mm

            elif side == "LEFT":
                x_mm = frame_left_mm - gap_mm - label_w_mm
                y_top_mm = (frame_top_mm + frame_bottom_mm) * 0.5 - label_h_mm * 0.5

            else:  # RIGHT
                x_mm = frame_right_mm + gap_mm
                y_top_mm = (frame_top_mm + frame_bottom_mm) * 0.5 - label_h_mm * 0.5

            x_px = x_mm / 25.4 * sheet_dpi
            # Convert top-down sheet coordinate to bottom-up canvas coordinate.
            y_px = (page_h_mm - y_top_mm - label_h_mm) / 25.4 * sheet_dpi

            tsdraft_sheet_alpha_blit(canvas, label, x_px, y_px)


def tsdraft_add_dimension_labels_to_exact_png(scene, filepath, view_key, info):
    """
    Add dimension text to an already exported exact-size PNG.
    Used by '3面まとめて書き出し' so vertical dimension text is rotated
    and the canvas can grow to avoid clipping.
    """
    import numpy as np

    # 出す軸も、置く側も、表は1つだけ。ここにも同じものがあった。
    axes = _dimension.tsdraft_svg_dimension_axes(view_key)
    if not axes:
        return

    sides = _labels.SINGLE_LAYOUT.get(view_key, {})

    data = bpy.app.driver_namespace.get(_keys.DATA_KEY, [])
    axis_text = {}
    for item in data:
        axis = str(item.get("axis", "")).upper()
        if axis in axes:
            axis_text[axis] = _dimension.get_dimension_text(scene, item)

    horizontal_text = axis_text.get(axes[0])
    vertical_text = axis_text.get(axes[1])
    if not horizontal_text and not vertical_text:
        return

    image = bpy.data.images.load(filepath, check_existing=False)
    try:
        src_w, src_h = int(image.size[0]), int(image.size[1])
        src = np.empty(src_w * src_h * 4, dtype=np.float32)
        image.pixels.foreach_get(src)
        src = src.reshape((src_h, src_w, 4))

        bbox_left = float(info.get("bbox_left_px", 0.0))
        bbox_right = float(src_w) - float(info.get("bbox_right_px", 0.0))
        bbox_bottom = float(info.get("bbox_bottom_px", 0.0))
        bbox_top = float(src_h) - float(info.get("bbox_top_px", 0.0))

        font_px = max(4.0, float(getattr(scene, "tsdraft_font_size", 20)))
        color = getattr(scene, "tsdraft_font_color", (0.05, 0.05, 0.05, 1.0))
        gap_px = max(8.0, font_px * 0.40)
        outer_pad = max(8.0, font_px * 0.35)

        labels = []

        # 置く側は表から引く。以前は「1つめの軸を上、2つめを左」と
        # 決め打ちで、表と一致しているのは偶然だった。
        for axis, text in ((axes[0], horizontal_text),
                           (axes[1], vertical_text)):
            if not text:
                continue

            side = sides.get(axis, "TOP")
            label = tsdraft_sheet_text_rgba(text, font_px, color)

            if side in {"LEFT", "RIGHT"}:
                # 実際に画素を回す。ビューポートの文字の回転が
                # バージョンで変わっても影響を受けない。
                label = np.rot90(label, k=1).copy()

            lh, lw = label.shape[0], label.shape[1]

            if side == "TOP":
                x = (bbox_left + bbox_right) * 0.5 - lw * 0.5
                y = bbox_top + gap_px
            elif side == "BOTTOM":
                x = (bbox_left + bbox_right) * 0.5 - lw * 0.5
                y = bbox_bottom - gap_px - lh
            elif side == "RIGHT":
                x = bbox_right + gap_px
                y = (bbox_bottom + bbox_top) * 0.5 - lh * 0.5
            else:  # LEFT
                x = bbox_left - gap_px - lw
                y = (bbox_bottom + bbox_top) * 0.5 - lh * 0.5

            labels.append((label, x, y))

        min_x = min([0.0] + [x for _lab, x, _y in labels]) - outer_pad
        min_y = min([0.0] + [y for _lab, _x, y in labels]) - outer_pad
        max_x = max([float(src_w)] + [x + lab.shape[1] for lab, x, _y in labels]) + outer_pad
        max_y = max([float(src_h)] + [y + lab.shape[0] for lab, _x, y in labels]) + outer_pad

        shift_x = max(0, int(math.ceil(-min_x)))
        shift_y = max(0, int(math.ceil(-min_y)))
        out_w = max(1, int(math.ceil(max_x + shift_x)))
        out_h = max(1, int(math.ceil(max_y + shift_y)))

        canvas = tsdraft_new_background_canvas(
            np,
            out_h,
            out_w,
            tsdraft_export_canvas_rgba(scene)
        )
        canvas[shift_y:shift_y + src_h, shift_x:shift_x + src_w, :] = src

        for label, x, y in labels:
            tsdraft_sheet_alpha_blit(
                canvas,
                label,
                x + shift_x,
                y + shift_y
            )

        out_image = bpy.data.images.new(
            name="TSDRAFT_Batch_Dimensioned_View",
            width=out_w,
            height=out_h,
            alpha=False,
            float_buffer=False
        )
        try:
            out_image.pixels.foreach_set(canvas.ravel())
            out_image.file_format = 'PNG'
            out_image.filepath_raw = filepath
            out_image.save()
        finally:
            bpy.data.images.remove(out_image)

        # Expanding the canvas must not change the BBox's physical scale.
        if not _capture.tsdraft_patch_png_dpi(filepath, float(info["dpi"])):
            raise RuntimeError("まとめ書き出しPNGへDPI情報を書き戻せませんでした")

    finally:
        try:
            bpy.data.images.remove(image)
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_add_dimension_labels_to_exact_png")


def tsdraft_make_three_view_sheet_png(scene, filepath, exported):
    """Compose three exact-size PNGs into a clean, aligned, print-scale PNG sheet."""
    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError("図面シートPNGの作成に必要な NumPy を読み込めませんでした") from exc

    denominator = tsdraft_sheet_scale_denominator(scene)
    paper = getattr(scene, 'tsdraft_sheet_paper_size', 'A4')
    orientation = getattr(scene, 'tsdraft_sheet_orientation', 'AUTO')

    # 用紙の表は core.paper が持つ。ここにも同じ表があり、A5 と
    # B判が抜けていた。選べる一覧と、実際に使う寸法が別々の表から
    # 出てくると、選べるのに寸法が無い用紙が生まれる。
    base_w, base_h = _paper.base_dimensions_mm(
        scene,
        size_prop='tsdraft_sheet_paper_size',
        custom_width_prop='tsdraft_sheet_custom_width_mm',
        custom_height_prop='tsdraft_sheet_custom_height_mm',
        default_custom=(210.0, 297.0),
    )

    view_data = {}
    source_effective_dpis = []
    for key, item in exported.items():
        info = item['info']
        src_dpi = max(1.0, float(info['dpi']))
        mm_per_px = 25.4 / src_dpi / denominator

        frame_w = float(info['bbox_width_mm']) / denominator
        frame_h = float(info['bbox_height_mm']) / denominator

        view_data[key] = {
            'image_w_mm': float(info['image_w_px']) * mm_per_px,
            'image_h_mm': float(info['image_h_px']) * mm_per_px,
            'frame_w_mm': frame_w,
            'frame_h_mm': frame_h,
            'left_mm': float(info.get('bbox_left_px', 0.0)) * mm_per_px,
            'right_mm': float(info.get('bbox_right_px', 0.0)) * mm_per_px,
            'top_mm': float(info.get('bbox_top_px', 0.0)) * mm_per_px,
            'bottom_mm': float(info.get('bbox_bottom_px', 0.0)) * mm_per_px,
        }
        source_effective_dpis.append(src_dpi * denominator)

    if orientation == 'AUTO':
        candidates = [
            ('PORTRAIT', min(base_w, base_h), max(base_w, base_h)),
            ('LANDSCAPE', max(base_w, base_h), min(base_w, base_h)),
        ]
    elif orientation == 'LANDSCAPE':
        candidates = [('LANDSCAPE', max(base_w, base_h), min(base_w, base_h))]
    else:
        candidates = [('PORTRAIT', min(base_w, base_h), max(base_w, base_h))]

    chosen = None
    required = None
    for orient_name, page_w, page_h in candidates:
        positions, needed = tsdraft_sheet_layout_aligned(view_data, page_w, page_h)
        if positions is not None:
            chosen = (orient_name, page_w, page_h, positions)
            break
        required = needed

    if chosen is None:
        need_w, need_h = required or (0.0, 0.0)
        raise RuntimeError(
            f'{paper}・1:{denominator:g} では三面図が収まりませんでした'
            f'（必要目安 {need_w + 24.0:.1f}×{need_h + 24.0:.1f}mm）。'
            '用紙を大きくするか、縮尺の N を大きくしてください（1:2 → 1:5 など）'
        )

    orient_name, page_w, page_h, positions = chosen

    # 図面シートは印刷品質を優先。
    # 元ビューポートのDPIでページ全体を低解像度化しない。
    # A4/A3/A2は最大300dpi、A1/A0/巨大カスタムだけ
    # 約36MPを上限に自動でDPIを下げてメモリを守る。
    page_area_in2 = max(1e-6, (page_w / 25.4) * (page_h / 25.4))
    memory_safe_dpi = math.sqrt(36_000_000.0 / page_area_in2)
    sheet_dpi = max(96.0, min(300.0, memory_safe_dpi))

    page_px_w = max(1, int(round(page_w / 25.4 * sheet_dpi)))
    page_px_h = max(1, int(round(page_h / 25.4 * sheet_dpi)))
    canvas = tsdraft_new_background_canvas(
        np,
        page_px_h,
        page_px_w,
        tsdraft_export_canvas_rgba(scene)
    )

    loaded_images = []
    try:
        for key in ('top', 'side', 'front'):
            item = exported[key]
            x_mm, y_top_mm = positions[key]
            w_mm = view_data[key]['image_w_mm']
            h_mm = view_data[key]['image_h_mm']

            dst_w = max(1, int(round(w_mm / 25.4 * sheet_dpi)))
            dst_h = max(1, int(round(h_mm / 25.4 * sheet_dpi)))

            image = bpy.data.images.load(item['path'], check_existing=False)
            loaded_images.append(image)
            image.scale(dst_w, dst_h)

            src = np.empty(dst_w * dst_h * 4, dtype=np.float32)
            image.pixels.foreach_get(src)
            src = src.reshape((dst_h, dst_w, 4))

            x0 = int(round(x_mm / 25.4 * sheet_dpi))
            y0 = int(round((page_h - y_top_mm - h_mm) / 25.4 * sheet_dpi))
            x1 = min(page_px_w, x0 + dst_w)
            y1 = min(page_px_h, y0 + dst_h)
            if x1 <= x0 or y1 <= y0:
                continue

            src = src[:y1 - y0, :x1 - x0, :]
            dst = canvas[y0:y1, x0:x1, :]
            alpha = src[..., 3:4]
            dst[..., :3] = src[..., :3] * alpha + dst[..., :3] * (1.0 - alpha)
            dst[..., 3:4] = 1.0

        # Sheet dimensions are drawn here, not baked into the viewport screenshots.
        tsdraft_sheet_draw_dimension_labels(
            canvas,
            scene,
            positions,
            view_data,
            sheet_dpi,
            page_h
        )

        # Keep scale notation on the actual sheet.
        label = f"SCALE 1:{denominator:g}"
        label_scale = max(2, int(round(sheet_dpi / 100.0)))
        label_x = int(round(12.0 / 25.4 * sheet_dpi))
        label_y_top = int(round((page_h - 10.0) / 25.4 * sheet_dpi))
        tsdraft_draw_bitmap_text(canvas, label, label_x, label_y_top, label_scale)

        out_image = bpy.data.images.new(
            name="TSDRAFT_Three_View_Sheet",
            width=page_px_w,
            height=page_px_h,
            alpha=False,
            float_buffer=False
        )
        try:
            out_image.pixels.foreach_set(canvas.ravel())
            out_image.file_format = 'PNG'
            out_image.filepath_raw = filepath

            # save(), not save_render(): avoid applying the scene view transform a second time.
            out_image.save()
        finally:
            bpy.data.images.remove(out_image)

        if not _capture.tsdraft_patch_png_dpi(filepath, sheet_dpi):
            raise RuntimeError("図面シートPNGへDPI情報を書き込めませんでした")
    finally:
        for image in loaded_images:
            try:
                bpy.data.images.remove(image)
            except Exception:
                _pkg_debug.swallowed("draft.tsdraft_make_three_view_sheet_png")

    return {
        'paper': paper,
        'orientation': orient_name,
        'page_w_mm': page_w,
        'page_h_mm': page_h,
        'scale_denominator': denominator,
        'dpi': sheet_dpi,
        'pixel_width': page_px_w,
        'pixel_height': page_px_h,
    }


def tsdraft_build_three_view_sheet(context, filepath):
    """Shared builder for preview and final export."""
    source_obj = _bbox.tsdraft_resolve_source_object(context)
    if source_obj is None:
        raise RuntimeError('元オブジェクトを選択してください')
    if bpy.data.objects.get(_keys.BBOX_NAME) is None:
        raise RuntimeError('先にBOX＋寸法を作成してください')

    out_dir = os.path.dirname(filepath) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    # 2.4.13: intermediate view PNGs belong in the OS temp area, not beside
    # the user's exported drawing.  The old .tsdraft_sheet_temp directory could
    # survive on Windows when a file handle was released a little late.
    import tempfile
    import shutil
    temp_dir = Path(tempfile.mkdtemp(prefix='zoukei_helper_sheet_'))

    exported = {}
    namespace = bpy.app.driver_namespace
    old_sheet_layout_mode = namespace.get("TSDRAFT_SHEET_LAYOUT_MODE", False)
    old_suppress_dim_text = namespace.get("TSDRAFT_SHEET_SUPPRESS_DIM_TEXT", False)
    namespace["TSDRAFT_SHEET_LAYOUT_MODE"] = True
    namespace["TSDRAFT_SHEET_SUPPRESS_DIM_TEXT"] = True

    try:
        for key, label in (('top', '上面'), ('front', '前面'), ('side', '側面')):
            tmp = temp_dir / f'__tsdraft_{os.getpid()}_{key}.png'
            info = _capture.tsdraft_export_viewport_exact_png(
                context,
                str(tmp),
                key,
                fit_padding_ratio=0.60,
                suppress_dimension_text=True
            )
            exported[key] = {'path': str(tmp), 'info': info, 'label': label}
        return tsdraft_make_three_view_sheet_png(context.scene, filepath, exported)
    finally:
        namespace["TSDRAFT_SHEET_LAYOUT_MODE"] = old_sheet_layout_mode
        namespace["TSDRAFT_SHEET_SUPPRESS_DIM_TEXT"] = old_suppress_dim_text
        for item in exported.values():
            try:
                Path(item['path']).unlink(missing_ok=True)
            except Exception:
                _pkg_debug.swallowed("draft.tsdraft_build_three_view_sheet")
        # Remove the whole temporary work tree.  ignore_errors=True is
        # intentional here: export success must not be turned into an error
        # merely because Windows releases a temporary image handle late.
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            _pkg_debug.swallowed("draft.tsdraft_build_three_view_sheet")
