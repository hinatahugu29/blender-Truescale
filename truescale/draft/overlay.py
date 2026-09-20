"""3Dビューへの重ね描き。

箱の枠、寸法の数字、どの面図かの見出しを描く。

■ 毎フレーム走る

重い計算を置くとビュー操作そのものが重くなる。ここでやるのは
座標を画面へ落として文字を置くところまでで、寸法の値は
dimension が持つ。

■ ハンドルは driver_namespace に置く

外すときに同じハンドルを渡す必要がある。モジュール直下に持つと、
Blender がモジュールを読み直したときに失われ、外せない描画が
残り続ける。残ると、アドオンを無効にしても描画が呼ばれる。

■ 旧バージョンのハンドラも外す

以前の接頭辞で登録されたものが残っていることがある。見つけたら
外す。残っていると、同じ絵が二重に描かれる。

■ 実寸のまま出すための倍率

画面上の1ミリが何ピクセルかを、正射投影のズームから求める。
透視投影では奥行きで変わってしまうので、面図として扱わない。
"""

import blf
import math
import mathutils
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy_extras import view3d_utils

from .. import debug as _debug
from . import bbox as _bbox
from . import dimension as _dimension
from . import keys as _keys
from . import viewstate as _viewstate


def draw_bbox_overlay():
    context = bpy.context

    if context.area is None or context.area.type != 'VIEW_3D':
        return

    rv3d = context.region_data
    if rv3d is None:
        return

    scene = context.scene
    dark_place = getattr(scene, "tsdraft_dark_place", False)

    # クイック非表示時は、各ビュー設定より先にBOX全体を止める。
    if not getattr(scene, "tsdraft_show_bbox", True) and not dark_place:
        return

    mode = scene.tsdraft_frame_mode
    if mode == 'NONE' and not dark_place:
        return

    view_key, view_dir = _viewstate.get_view_key_from_rv3d(rv3d)

    explicit_user_mode = bool(
        getattr(scene, "tsdraft_user_view_mode", False)
    )

    # 普通の斜めデフォルトビューは内部判定がuserでも、
    # BOX＋寸法作成直後はグローバル表示を優先して見せる。
    # 「任意」ボタンを明示的に押した時だけuser個別設定を使う。
    if explicit_user_mode:
        view_key = "user"
        if (
            not getattr(scene, "tsdraft_show_bbox_user", True)
            and not dark_place
        ):
            return
    elif view_key != "user":
        if (
            not getattr(scene, f"tsdraft_show_bbox_{view_key}", True)
            and not dark_place
        ):
            return

    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)
    if bbox_obj is None:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()

    if dark_place:
        shader.uniform_float("color", _keys.DARK_LINE)
        gpu.state.line_width_set(2.5)
    else:
        frame_color = scene.tsdraft_frame_color
        shader.uniform_float(
            "color",
            (
                frame_color[0],
                frame_color[1],
                frame_color[2],
                frame_color[3]
            )
        )
        gpu.state.line_width_set(scene.tsdraft_frame_width)

    # -----------------------------------------------------
    # お遊び：「なんかずっと暗いとこ」檻表示
    # -----------------------------------------------------
    if dark_place:
        verts_local = [v.co.copy() for v in bbox_obj.data.vertices]
        if len(verts_local) >= 8:
            xs = [v.x for v in verts_local]
            ys = [v.y for v in verts_local]
            zs = [v.z for v in verts_local]

            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            zmin, zmax = min(zs), max(zs)

            segments_local = []

            # 上下の外周
            rects = (
                (zmin, ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))),
                (zmax, ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))),
            )
            for z, pts in rects:
                for i in range(4):
                    x1, y1 = pts[i]
                    x2, y2 = pts[(i + 1) % 4]
                    segments_local.append(((x1, y1, z), (x2, y2, z)))

            # 四隅の縦柱
            for x in (xmin, xmax):
                for y in (ymin, ymax):
                    segments_local.append(((x, y, zmin), (x, y, zmax)))

            # 前後面の縦格子
            bar_count_x = 8
            for i in range(1, bar_count_x):
                t = i / bar_count_x
                x = xmin + (xmax - xmin) * t
                segments_local.append(((x, ymin, zmin), (x, ymin, zmax)))
                segments_local.append(((x, ymax, zmin), (x, ymax, zmax)))

            # 左右面の縦格子
            bar_count_y = 5
            for i in range(1, bar_count_y):
                t = i / bar_count_y
                y = ymin + (ymax - ymin) * t
                segments_local.append(((xmin, y, zmin), (xmin, y, zmax)))
                segments_local.append(((xmax, y, zmin), (xmax, y, zmax)))

            coords = []
            indices = []

            for a_local, b_local in segments_local:
                a = bbox_obj.matrix_world @ mathutils.Vector(a_local)
                b = bbox_obj.matrix_world @ mathutils.Vector(b_local)
                base = len(coords)
                coords.extend((tuple(a), tuple(b)))
                indices.append((base, base + 1))

            if coords:
                batch = batch_for_shader(
                    shader,
                    'LINES',
                    {"pos": coords},
                    indices=indices
                )
                batch.draw(shader)

        gpu.state.line_width_set(1.0)
        return

    # -----------------------------------------------------
    # 通常の枠表示
    # -----------------------------------------------------
    if mode == 'BOX':
        # BBoxメッシュ自身が持つ12本の辺をそのまま描画する。
        # ワールド座標差から「1軸だけ違う頂点」を推測すると、
        # オブジェクト回転時に本来の辺でもXYZすべてが変化して
        # 辺判定から漏れ、縦線などが欠けることがある。
        verts = [bbox_obj.matrix_world @ v.co for v in bbox_obj.data.vertices]
        edges = [tuple(edge.vertices) for edge in bbox_obj.data.edges]

        if verts and edges:
            coords = [(v.x, v.y, v.z) for v in verts]
            batch = batch_for_shader(
                shader,
                'LINES',
                {"pos": coords},
                indices=edges
            )
            batch.draw(shader)

    elif mode == 'LINES':
        # 「寸法線のみ」は文字の表示ON/OFFとは独立して描画する。
        # 以前は tsdraft_show_dimensions_* がOFFだと線まで消えていたため、
        # 任意ビューや通常の斜めビューで「寸法線のみ」を選ぶと
        # 何も表示されない状態になっていた。
        data = bpy.app.driver_namespace.get(_keys.DATA_KEY, [])
        if not data:
            gpu.state.line_width_set(1.0)
            return

    
        axis_vectors = {
            "X": Vector((1.0, 0.0, 0.0)),
            "Y": Vector((0.0, 1.0, 0.0)),
            "Z": Vector((0.0, 0.0, 1.0)),
        }

        segments = []

        for item in data:
            axis_name = item.get("axis", "X")

            if axis_name in axis_vectors:
                axis_world = bbox_obj.matrix_world.to_3x3() @ axis_vectors[axis_name]
                if axis_world.length > 0:
                    axis_world.normalize()

                    if abs(axis_world.dot(view_dir)) >= 0.965:
                        continue

            a = item.get("world_a")
            b = item.get("world_b")

            if a is None or b is None:
                continue

            segments.append((tuple(a), tuple(b)))

        if segments:
            coords = []
            indices = []

            for a, b in segments:
                base = len(coords)
                coords.extend((a, b))
                indices.append((base, base + 1))

            batch = batch_for_shader(
                shader,
                'LINES',
                {"pos": coords},
                indices=indices
            )
            batch.draw(shader)

    gpu.state.line_width_set(1.0)


def ensure_bbox_draw_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(_keys.BBOX_HANDLER_KEY)

    if handler is None:
        handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_bbox_overlay,
            (),
            'WINDOW',
            'POST_VIEW'
        )
        namespace[_keys.BBOX_HANDLER_KEY] = handler


def remove_bbox_draw_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(_keys.BBOX_HANDLER_KEY)

    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        except Exception:
            _debug.swallowed("draft.remove_bbox_draw_handler")

    namespace[_keys.BBOX_HANDLER_KEY] = None


def tsdraft_sync_ortho_zoom(space):
    """
    Keep Top / Front / Side view_distance synchronized in Quad View.
    User view stays independent.
    """
    try:
        quadviews = list(space.region_quadviews)
    except Exception:
        return

    if not quadviews:
        return

    orthos = []
    for rv in quadviews:
        key, _ = _viewstate.get_view_key_from_rv3d(rv)
        if key in {"top", "front", "side"}:
            orthos.append((key, rv))

    if len(orthos) < 2:
        return

    ns = bpy.app.driver_namespace
    prev = ns.get(_keys.ZOOM_SYNC_STATE_KEY)

    current = {key: float(rv.view_distance) for key, rv in orthos}

    # First run after entering Quad View:
    # use the front view as master if available, otherwise first ortho view.
    if not isinstance(prev, dict) or not prev:
        master_value = None

        for key, rv in orthos:
            if key == "front":
                master_value = float(rv.view_distance)
                break

        if master_value is None:
            master_value = float(orthos[0][1].view_distance)

        for _, rv in orthos:
            rv.view_distance = master_value

        ns[_keys.ZOOM_SYNC_STATE_KEY] = {
            key: master_value
            for key, _ in orthos
        }
        return

    # Detect which pane changed compared with previous draw.
    changed = []
    for key, rv in orthos:
        value = float(rv.view_distance)
        old = prev.get(key)

        if old is not None and abs(value - float(old)) > 1e-6:
            changed.append((key, value))

    if changed:
        # Last changed pane becomes master.
        master_value = changed[-1][1]

        for _, rv in orthos:
            rv.view_distance = master_value

        ns[_keys.ZOOM_SYNC_STATE_KEY] = {
            key: master_value
            for key, _ in orthos
        }
    else:
        ns[_keys.ZOOM_SYNC_STATE_KEY] = current


def draw_view_label():
    context = bpy.context

    if context.area is None or context.area.type != 'VIEW_3D':
        return

    rv3d = context.region_data
    region = context.region
    if rv3d is None or region is None:
        return

    scene = context.scene

    # 図面表示中だけ独自ビュー名を表示
    if not getattr(scene, "tsdraft_drawing_mode", False):
        return

    view_key, _ = _viewstate.get_view_key_from_rv3d(rv3d)

    # -----------------------------------------------------
    # HARD ZOOM LOCK
    # 現在描画中のpane自身でBBoxを投影し、
    # 画面の80%を超えるほどズームインされたら即座に戻す。
    # 任意ビューは対象外。
    # -----------------------------------------------------
    if view_key in {"top", "front", "side"}:
        bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)

        if bbox_obj is not None:
            try:
                corners_world = [
                    bbox_obj.matrix_world @ Vector(corner)
                    for corner in bbox_obj.bound_box
                ]

                projected = []
                for world_co in corners_world:
                    p = view3d_utils.location_3d_to_region_2d(
                        region,
                        rv3d,
                        world_co
                    )
                    if p is not None:
                        projected.append(p)

                if len(projected) >= 4:
                    xs = [p.x for p in projected]
                    ys = [p.y for p in projected]

                    bbox_w = max(xs) - min(xs)
                    bbox_h = max(ys) - min(ys)

                    # 10% margin on every side.
                    usable_w = max(1.0, float(region.width) * 0.74)
                    usable_h = max(1.0, float(region.height) * 0.74)

                    factor = max(
                        bbox_w / usable_w,
                        bbox_h / usable_h,
                        1.0
                    )

                    if factor > 1.0005:
                        rv3d.view_distance *= factor

                        # New safe zoom becomes the master value for the 3 ortho panes.
                        safe_distance = float(rv3d.view_distance)

                        try:
                            quadviews = list(context.area.spaces.active.region_quadviews)
                        except Exception:
                            quadviews = []

                        sync_state = {}
                        for other_rv in quadviews:
                            other_key, _ = _viewstate.get_view_key_from_rv3d(other_rv)
                            if other_key in {"top", "front", "side"}:
                                other_rv.view_distance = safe_distance
                                sync_state[other_key] = safe_distance

                        if sync_state:
                            bpy.app.driver_namespace[_keys.ZOOM_SYNC_STATE_KEY] = sync_state

            except Exception:
                _debug.swallowed("draft.draw_view_label")

    # Normal 3-view zoom synchronization after the hard clamp.
    try:
        tsdraft_sync_ortho_zoom(context.area.spaces.active)
    except Exception:
        _debug.swallowed("draft.draw_view_label")

    labels = {
        "top": "上面",
        "front": "前面",
        "side": "側面",
        "user": "任意",
    }

    label = labels.get(view_key, "任意")

    font_id = 0
    size = 28

    blf.size(font_id, size)
    blf.color(font_id, 0.18, 0.18, 0.18, 1.0)
    blf.position(font_id, 28, region.height - size - 24, 0)
    blf.draw(font_id, label)


def ensure_view_label_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(_keys.VIEW_LABEL_HANDLER_KEY)

    if handler is None:
        handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_view_label,
            (),
            'WINDOW',
            'POST_PIXEL'
        )
        namespace[_keys.VIEW_LABEL_HANDLER_KEY] = handler


def remove_view_label_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(_keys.VIEW_LABEL_HANDLER_KEY)

    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        except Exception:
            _debug.swallowed("draft.remove_view_label_handler")

    namespace[_keys.VIEW_LABEL_HANDLER_KEY] = None


def tsdraft_view_px_per_mm(region, rv3d, bbox_obj, view_key, unit_scale):
    if bbox_obj is None:
        return 1.0

    corners = [bbox_obj.matrix_world @ v.co for v in bbox_obj.data.vertices]
    projected = []
    for co in corners:
        p2 = view3d_utils.location_3d_to_region_2d(region, rv3d, co)
        if p2 is not None:
            projected.append((float(p2.x), float(p2.y)))

    if len(projected) < 4:
        return 1.0

    xs = [p[0] for p in projected]
    ys = [p[1] for p in projected]
    px_w = max(xs) - min(xs)
    px_h = max(ys) - min(ys)

    if view_key == "top":
        vals_u = [co.x for co in corners]
        vals_v = [co.y for co in corners]
    elif view_key == "front":
        vals_u = [co.x for co in corners]
        vals_v = [co.z for co in corners]
    elif view_key == "side":
        vals_u = [co.y for co in corners]
        vals_v = [co.z for co in corners]
    else:
        vals_u = [co.x for co in corners]
        vals_v = [co.z for co in corners]

    mm_w = (max(vals_u) - min(vals_u)) * unit_scale * 1000.0
    mm_h = (max(vals_v) - min(vals_v)) * unit_scale * 1000.0

    vals = []
    if mm_w > 1e-9 and px_w > 1:
        vals.append(px_w / mm_w)
    if mm_h > 1e-9 and px_h > 1:
        vals.append(px_h / mm_h)

    return sum(vals) / len(vals) if vals else 1.0


def draw_size_labels():
    _bbox.tsdraft_import_legacy_state()
    context = bpy.context

    if context.area is None or context.area.type != 'VIEW_3D':
        return

    region = context.region
    rv3d = context.region_data

    if region is None or rv3d is None:
        return

    scene = context.scene

    if not scene.tsdraft_show_dimensions:
        return

    namespace = bpy.app.driver_namespace

    # 三面図シートでは、寸法文字を最終シート上で直接描画する。
    # ビューポート文字をスクショへ焼くと回転・クロップが不安定なので、
    # シート用一時PNGでは文字だけ抑止する。
    if namespace.get("TSDRAFT_SHEET_SUPPRESS_DIM_TEXT", False):
        return

    source_name = namespace.get(_keys.SOURCE_KEY)
    data = namespace.get(_keys.DATA_KEY, [])

    if source_name and bpy.data.objects.get(source_name) is None:
        return

    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)
    if bbox_obj is None or not data:
        return

    font_id = 0
    font_size = scene.tsdraft_font_size
    font_color = scene.tsdraft_font_color

    if getattr(scene, "tsdraft_dark_place", False):
        draw_color = _keys.DARK_TEXT
    else:
        draw_color = font_color

    blf.size(font_id, font_size)
    blf.color(
        font_id,
        draw_color[0],
        draw_color[1],
        draw_color[2],
        draw_color[3]
    )

    # Blenderの現在ビュー方向（画面から奥へ向かう方向）
    view_key, view_dir = _viewstate.get_view_key_from_rv3d(rv3d)

    explicit_user_mode = bool(
        getattr(scene, "tsdraft_user_view_mode", False)
    )

    # 「任意」ボタンを押した時だけuser個別設定を使用。
    # 通常の斜めデフォルトビューはグローバル寸法表示を使う。
    if explicit_user_mode:
        view_key = "user"

    axis_vectors = {
        "X": Vector((1.0, 0.0, 0.0)),
        "Y": Vector((0.0, 1.0, 0.0)),
        "Z": Vector((0.0, 0.0, 1.0)),
    }

    # -----------------------------------------------------
    # 三面図では、BBoxを毎回画面へ投影して寸法文字の基準位置を作る。
    # これにより、オブジェクト寸法変更・BBox追従・ビュー倍率変更があっても
    # 「上中央」「左中央」という画面上の関係を維持できる。
    # -----------------------------------------------------
    projected_bbox = []
    if view_key in {"top", "front", "side"}:
        try:
            bbox_world = [
                bbox_obj.matrix_world @ Vector(corner)
                for corner in bbox_obj.bound_box
            ]
            for world_co in bbox_world:
                p = view3d_utils.location_3d_to_region_2d(
                    region,
                    rv3d,
                    world_co
                )
                if p is not None:
                    projected_bbox.append(p)
        except Exception:
            projected_bbox = []

    bbox_screen = None
    if projected_bbox:
        bx = [p.x for p in projected_bbox]
        by = [p.y for p in projected_bbox]
        bbox_screen = (
            min(bx),
            max(bx),
            min(by),
            max(by),
        )

    # 各三面図で、画面横方向 / 縦方向に対応する寸法軸。
    # 横寸法はBOX上中央、縦寸法はBOX左中央へ自動配置。
    sheet_layout_mode = bool(
        bpy.app.driver_namespace.get("TSDRAFT_SHEET_LAYOUT_MODE", False)
    )

    if sheet_layout_mode:
        # 三面図シートでは、寸法を図同士の隙間ではなく外周側へ逃がす。
        auto_axis_layout = {
            "top": {
                "X": "TOP",
                "Y": "LEFT",
            },
            "front": {
                "X": "BOTTOM",
                "Z": "RIGHT",
            },
            "side": {
                "Y": "BOTTOM",
                "Z": "LEFT",
            },
        }
    else:
        # 単体ビューは従来どおり上＋左。
        auto_axis_layout = {
            "top": {
                "X": "TOP",
                "Y": "LEFT",
            },
            "front": {
                "X": "TOP",
                "Z": "LEFT",
            },
            "side": {
                "Y": "TOP",
                "Z": "LEFT",
            },
        }

    for item in data:
        axis_name = item.get("axis", "X")

        if axis_name in axis_vectors:
            axis_world = bbox_obj.matrix_world.to_3x3() @ axis_vectors[axis_name]
            if axis_world.length > 0:
                axis_world.normalize()

                # ビュー方向と寸法軸がほぼ平行なら、その寸法は奥行きなので隠す。
                if abs(axis_world.dot(view_dir)) >= 0.965:
                    continue

        axis = axis_name.lower()

        # ビューごとの寸法表示ON/OFF。
        # 明示的な任意ビューだけ user 個別設定を使う。
        # 普通の斜めデフォルトビューはグローバル表示を優先する。
        if explicit_user_mode:
            if not getattr(scene, "tsdraft_show_dimensions_user", True):
                continue
        elif view_key != "user":
            if not getattr(
                scene,
                f"tsdraft_show_dimensions_{view_key}",
                True
            ):
                continue
            if not _dimension.tsdraft_dimension_axis_enabled(scene, view_key, axis_name):
                continue

        unit_scale = scene.unit_settings.scale_length or 1.0
        px_per_mm = tsdraft_view_px_per_mm(
            region, rv3d, bbox_obj, view_key, unit_scale
        )

        # 既存の細かい位置調整は「自動配置位置からの追加オフセット」として残す。
        vx_mm = getattr(scene, f"tsdraft_{view_key}_{axis}_offset_x_mm", 0.0)
        vy_mm = getattr(scene, f"tsdraft_{view_key}_{axis}_offset_y_mm", 0.0)
        vx = vx_mm * px_per_mm
        vy = vy_mm * px_per_mm

        display_text = _dimension.get_dimension_text(scene, item)

        requested_font_size = max(1, int(font_size))
        blf.size(font_id, requested_font_size)
        text_width, text_height = blf.dimensions(font_id, display_text)

        # pane端やBlender UI帯へ文字が食い込まないよう、
        # 実際の文字高さに応じて安全余白を広げる。
        safe_margin_x = max(
            20.0,
            float(text_height) * 0.75
        )
        safe_margin_y = max(
            32.0,
            float(text_height) * 1.15
        )

        available_w = max(
            1.0,
            float(region.width) - safe_margin_x * 2.0
        )
        available_h = max(
            1.0,
            float(region.height) - safe_margin_y * 2.0
        )

        # 極端な文字サイズだけ、paneに収まる範囲まで自動縮小。
        if text_width > available_w or text_height > available_h:
            scale = min(
                available_w / max(1.0, float(text_width)),
                available_h / max(1.0, float(text_height)),
                1.0
            )
            effective_size = max(8, int(requested_font_size * scale))
            blf.size(font_id, effective_size)
            text_width, text_height = blf.dimensions(font_id, display_text)

        layout_type = auto_axis_layout.get(view_key, {}).get(axis_name)

        rotate_vertical_text = layout_type in {"LEFT", "RIGHT"}

        if bbox_screen is not None and layout_type in {"TOP", "BOTTOM", "LEFT", "RIGHT"}:
            bbox_min_x, bbox_max_x, bbox_min_y, bbox_max_y = bbox_screen

            # 文字サイズに応じてBOXから少し離す。
            gap = max(10.0, float(text_height) * 0.35)

            if layout_type == "TOP":
                text_x = ((bbox_min_x + bbox_max_x) * 0.5) - (text_width * 0.5)
                text_y = bbox_max_y + gap

            elif layout_type == "BOTTOM":
                text_x = ((bbox_min_x + bbox_max_x) * 0.5) - (text_width * 0.5)
                text_y = bbox_min_y - gap - text_height

            elif layout_type == "RIGHT":
                # 90°回転後の見た目:
                # 横幅=text_height / 高さ=text_width
                text_x = bbox_max_x + gap
                text_y = ((bbox_min_y + bbox_max_y) * 0.5) - (text_width * 0.5)

            else:  # LEFT
                # 90°回転後の横幅は text_height。
                text_x = bbox_min_x - gap - text_height
                text_y = ((bbox_min_y + bbox_max_y) * 0.5) - (text_width * 0.5)

            text_x += vx
            text_y += vy

        else:
            # 任意ビューなどは従来の3Dアンカー方式を維持。
            pos_2d = view3d_utils.location_3d_to_region_2d(
                region,
                rv3d,
                item["location"]
            )

            if pos_2d is None:
                continue

            text_x = pos_2d.x - (text_width * 0.5) + vx
            text_y = pos_2d.y - (text_height * 0.5) + vy

        # 最後に画面内へクランプ。
        # 縦寸法は90°回転後の見た目サイズで判定する。
        visual_w = float(text_height) if rotate_vertical_text else float(text_width)
        visual_h = float(text_width) if rotate_vertical_text else float(text_height)

        max_x = max(
            safe_margin_x,
            float(region.width) - visual_w - safe_margin_x
        )
        max_y = max(
            safe_margin_y,
            float(region.height) - visual_h - safe_margin_y
        )

        text_x = min(max(text_x, safe_margin_x), max_x)
        text_y = min(max(text_y, safe_margin_y), max_y)

        if rotate_vertical_text:
            # BLFは指定位置を基準に反時計回りへ回転するため、
            # 見た目の左下が text_x/text_y に来るようXを右へずらす。
            try:
                blf.enable(font_id, blf.ROTATION)
                blf.rotation(font_id, math.radians(90.0))
                blf.position(
                    font_id,
                    text_x + float(text_height),
                    text_y,
                    0
                )
                blf.draw(font_id, display_text)
            finally:
                try:
                    blf.rotation(font_id, 0.0)
                    blf.disable(font_id, blf.ROTATION)
                except Exception:
                    _debug.swallowed("draft.draw_size_labels")
        else:
            blf.position(
                font_id,
                text_x,
                text_y,
                0
            )
            blf.draw(font_id, display_text)


def tsdraft_remove_legacy_draw_handlers():
    """Remove stale pre-Printable/old-version handlers that can double-draw labels."""
    namespace = bpy.app.driver_namespace

    for key in _keys.LEGACY_HANDLER_KEYS:
        handler = namespace.get(key)
        if handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
            except Exception:
                _debug.swallowed("draft.tsdraft_remove_legacy_draw_handlers")
            namespace[key] = None


def ensure_draw_handler():
    namespace = bpy.app.driver_namespace

    # First kill known stale handlers from older series/versions.
    tsdraft_remove_legacy_draw_handlers()

    old_handler = namespace.get(_keys.HANDLER_KEY)
    if old_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(old_handler, 'WINDOW')
        except Exception:
            _debug.swallowed("draft.ensure_draw_handler")

    handler = bpy.types.SpaceView3D.draw_handler_add(
        draw_size_labels,
        (),
        'WINDOW',
        'POST_PIXEL'
    )
    namespace[_keys.HANDLER_KEY] = handler


def remove_draw_handler():
    namespace = bpy.app.driver_namespace
    handler = namespace.get(_keys.HANDLER_KEY)

    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                handler,
                'WINDOW'
            )
        except Exception:
            _debug.swallowed("draft.remove_draw_handler")

    namespace[_keys.HANDLER_KEY] = None
