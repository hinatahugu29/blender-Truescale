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
import mathutils
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy_extras import view3d_utils

from .. import debug as _debug
from . import bbox as _bbox
from . import labels as _labels
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
    # 暗所表示。背景を暗くして、箱の枠を目立たせる。
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


def draw_size_labels():
    """寸法の文字を描く。

    どこへ置くかは draft.labels が決める。書き出しの切り抜き範囲も
    同じ計算から出すので、画面で見えている文字が PNG で切れる
    ことはない。以前はここと capture に同じ計算が2つあり、
    手で揃え続ける前提になっていた（そして揃っていなかった）。
    """
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

    # 三面図シートでは、寸法をシートの上で直接描く。ビューポートの
    # 文字を撮影画像へ焼くと、回転と切り抜きが安定しない。
    if _labels.text_suppressed():
        return

    namespace = bpy.app.driver_namespace
    source_name = namespace.get(_keys.SOURCE_KEY)
    if source_name and bpy.data.objects.get(source_name) is None:
        return

    bbox_obj = bpy.data.objects.get(_keys.BBOX_NAME)
    data = _labels.label_data()
    if bbox_obj is None or not data:
        return

    font_id = 0
    if getattr(scene, "tsdraft_dark_place", False):
        color = _keys.DARK_TEXT
    else:
        color = scene.tsdraft_font_color

    blf.color(font_id, color[0], color[1], color[2], color[3])

    view_key, view_dir, explicit = _labels.current_view_key(scene, rv3d)

    _labels.draw(
        _labels.layout(
            scene, region, rv3d, bbox_obj, data,
            view_key, view_dir,
            explicit_user_mode=explicit,
            font_id=font_id,
        ),
        font_id=font_id,
    )


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
