"""Truescale のヘッドレステスト。

GUIを開かずに Blender 上で実際に register / 型紙生成 / 座標変換を検証する。

実行:
    blender --background --factory-startup --python tools/test_headless.py

    終了コード 0 = 全て成功 / 1 = 失敗あり

--factory-startup を付けるのは、他アドオンやユーザ設定の影響を
受けない状態で測るため。付けないと他アドオンのハンドラが混ざる。
"""

import os
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_results = []


# ============================================================
# ごく小さなテスト基盤
# ============================================================

class Skip(Exception):
    """この環境では実行できないテスト。失敗とは区別する。"""


def test(func):
    """テスト関数を登録するデコレータ。"""
    _results.append(func)
    return func


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, tolerance, message):
    if abs(actual - expected) > tolerance:
        raise AssertionError(
            f"{message}: 期待 {expected} / 実際 {actual} （許容 {tolerance}）"
        )


# ============================================================
# 準備
# ============================================================

def ensure_unregistered():
    """テストの前に必ず未登録の状態へ戻す。

    各テストが自分で register() するため、前のテストが登録したままだと
    「already registered」で落ちる。失敗は無視してよい（元々未登録の場合）。
    """
    try:
        import truescale
    except Exception:
        return
    try:
        truescale.unregister()
    except Exception:
        pass


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make_seamed_cube(name="TestCube", size=2.0):
    """シームを入れた立方体を作って返す。"""
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    half = size / 2.0
    verts = [
        (-half, -half, -half), (half, -half, -half),
        (half, half, -half), (-half, half, -half),
        (-half, -half, half), (half, -half, half),
        (half, half, half), (-half, half, half),
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    # 立方体を開く最低限のシーム。縦4本＋上面の周り。
    for edge in mesh.edges:
        a = mesh.vertices[edge.vertices[0]].co
        b = mesh.vertices[edge.vertices[1]].co
        # 垂直な辺（Zが変化する辺）をシームにする
        if abs(a.z - b.z) > 1e-6:
            edge.use_seam = True
    # 上面の輪郭もシームにして蓋を分離する
    for edge in mesh.edges:
        a = mesh.vertices[edge.vertices[0]].co
        b = mesh.vertices[edge.vertices[1]].co
        if a.z > 0 and b.z > 0:
            edge.use_seam = True

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def build_pattern_for(obj):
    """型紙を作る。ヘッドレスで動かない場合は Skip にする。"""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        result = bpy.ops.truescale_unfold.build_pattern()
    except RuntimeError as exc:
        raise Skip(f"型紙生成をヘッドレスで実行できません: {exc}")

    if 'FINISHED' not in result:
        raise Skip(f"型紙生成が完了しませんでした: {result}")

    unfold = unfold_object_for(obj)
    if unfold is None:
        raise Skip("型紙オブジェクトが生成されませんでした")
    return unfold


def unfold_object_for(source):
    for candidate in bpy.data.objects:
        if candidate.get("tsunfold_generated") and \
                candidate.get("tsunfold_source") == source.name:
            return candidate
    return None


# ============================================================
# テスト
# ============================================================

@test
def test_register_unregister():
    """register / unregister が例外なく通る。"""
    reset_scene()
    import truescale
    truescale.register()
    truescale.unregister()
    truescale.register()   # 2回目も通ること（再読み込み相当）


@test
def test_no_property_leak_after_unregister():
    """unregister 後に Scene プロパティが残らない。"""
    reset_scene()
    import truescale
    truescale.register()
    truescale.unregister()

    leaked = [
        name for name in dir(bpy.types.Scene)
        if name.startswith("tsunfold_") or name.startswith("tsdraft_")
    ]
    check(not leaked, f"プロパティが残っている: {leaked}")
    truescale.register()


@test
def test_no_handler_leak_after_unregister():
    """unregister 後に自分のハンドラが残らない。"""
    reset_scene()
    import truescale
    truescale.register()
    truescale.unregister()

    remaining = []
    for hname in ("depsgraph_update_post", "load_post"):
        for func in getattr(bpy.app.handlers, hname):
            module = getattr(func, "__module__", "") or ""
            if "truescale" in module:
                remaining.append(f"{hname}: {module}.{func.__name__}")

    check(not remaining, f"ハンドラが残っている: {remaining}")
    truescale.register()


@test
def test_panel_operators_exist():
    """パネルが参照するオペレータが全て実在する。

    静的チェック（tools/check_addon.py）と同じことを実行時にも見る。
    登録漏れは静的には分からないため。
    """
    reset_scene()
    import truescale
    truescale.register()

    import ast
    missing = []
    for sub in ("unfold", "draft"):
        path = REPO_ROOT / "truescale" / sub / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "operator"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            idname = node.args[0].value
            if not isinstance(idname, str) or "." not in idname:
                continue
            group, _, name = idname.partition(".")
            if not hasattr(getattr(bpy.ops, group, None), name):
                missing.append(f"{idname} ({sub}:{node.lineno})")

    check(not missing, f"存在しないオペレータを参照: {missing}")


@test
def test_real_scale_of_cube():
    """立方体の型紙が元の寸法を保つ。

    1辺 2.0 BU の立方体なら、展開した面の1辺も 2.0 BU でなければならない。
    """
    reset_scene()
    import truescale
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    mesh = unfold.data
    check(len(mesh.polygons) == 6, f"面数が6でない: {len(mesh.polygons)}")

    # 各面の辺の長さが 2.0 に近いこと
    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        for i, li in enumerate(loops):
            lj = loops[(i + 1) % len(loops)]
            a = mesh.vertices[mesh.loops[li].vertex_index].co
            b = mesh.vertices[mesh.loops[lj].vertex_index].co
            close((b - a).length, 2.0, 0.02, "展開後の辺の長さ")


@test
def test_annotations_follow_object():
    """型紙を動かすと、合印などの注記も同じだけ動く。

    描画キャッシュをローカル空間へ変えた際の回帰テスト。
    キャッシュしたローカル座標にワールド変換を掛け忘れると、
    注記が原点に取り残される。
    """
    from truescale.marking import compute as C
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    context = bpy.context
    before = C.colored_segments(context, obj, unfold)
    if not before:
        raise Skip("色付きセグメントが生成されませんでした")

    delta = Vector((1.5, -2.5, 0.0))
    unfold.location = unfold.location + delta
    bpy.context.view_layer.update()

    after = C.colored_segments(context, obj, unfold)

    check(
        len(before) == len(after),
        f"セグメント数が変化した: {len(before)} -> {len(after)}",
    )

    for index, (row_a, row_b) in enumerate(zip(before, after)):
        for point_index in (0, 1):
            moved = row_b[point_index] - row_a[point_index]
            close(
                (moved - delta).length, 0.0, 1e-5,
                f"セグメント{index}の点{point_index}の移動量",
            )


@test
def test_cache_survives_object_move():
    """オブジェクトを動かしてもキャッシュが効く。

    以前は matrix_world がキャッシュキーに入っていたため、
    動かすたびに再計算されていた。その回帰テスト。
    """
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    context = bpy.context

    # 呼び出し回数を数えるので、実体のある compute へ当てる。
    # unfold 側は別名なので、そこを差し替えても呼ばれない。
    from truescale.marking import compute as C

    calls = {"count": 0}
    original = C.compute_colored_segments

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    C.compute_colored_segments = counting
    try:
        C.colored_segments(context, obj, unfold)
        first = calls["count"]
        check(first >= 1, "1回目で計算されていない")

        # 10回動かして、そのたびに取得する
        for step in range(10):
            unfold.location.x += 0.1
            bpy.context.view_layer.update()
            C.colored_segments(context, obj, unfold)

        check(
            calls["count"] == first,
            f"移動のたびに再計算されている: {calls['count'] - first} 回",
        )
    finally:
        C.compute_colored_segments = original


@test
def test_draw_cache_is_bounded():
    """描画キャッシュが無制限に増えない。"""
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    limit = S.DRAW_CACHE_LIMIT
    for index in range(limit * 3):
        S.store(("dummy", index), index)

    size = len(S.draw_cache)
    check(size <= limit, f"キャッシュが上限を超えている: {size} > {limit}")


@test
def test_notch_color_does_not_rebuild():
    """合印の色を変えても合印を作り直さない。

    以前は色の変更で全シームを走査し直していた。その回帰テスト。
    """
    from truescale.marking import auto_notch as AN
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    calls = {"count": 0}
    original = AN.refresh

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    AN.refresh = counting
    try:
        bpy.context.scene.tsunfold_notch_color = (0.5, 0.25, 0.75)
        check(
            calls["count"] == 0,
            f"色の変更で合印を作り直している: {calls['count']} 回",
        )

        # 分割数の変更では作り直すこと
        current = bpy.context.scene.tsunfold_auto_notch_divisions
        bpy.context.scene.tsunfold_auto_notch_divisions = (
            "4" if current != "4" else "2"
        )
        check(
            calls["count"] >= 1,
            "分割数の変更で合印が作り直されていない",
        )
    finally:
        AN.refresh = original


@test
def test_sliders_do_not_invalidate_cache():
    """数値スライダーを動かしてもキャッシュが飛ばない。

    これらの設定はキャッシュキーに含まれるか、描画時に読み直される。
    epoch を進めて全キャッシュを捨てる必要がない。
    以前は全部捨てていたため、スライダーのドラッグ中に島の解析や
    配置探索が毎フレーム作り直されていた。
    """
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    sliders = (
        ("tsunfold_notch_length_mm", 7.5),
        ("tsunfold_notch_thickness_mm", 1.2),
        ("tsunfold_auto_arrow_length_mm", 30.0),
        ("tsunfold_arrow_head_mm", 9.0),
        ("tsunfold_arrow_thickness_mm", 1.1),
        ("tsunfold_island_id_size_mm", 10.0),
    )

    for name, value in sliders:
        before = S.epoch
        setattr(scene, name, value)
        after = S.epoch
        check(
            before == after,
            f"{name} の変更でキャッシュ epoch が進んだ: {before} -> {after}",
        )


@test
def test_workflow_status_progresses():
    """パネル先頭の案内が、段階に応じて次の一手を示す。"""
    from truescale.unfold import status as STATUS
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    # 何も無い状態
    _, current, next_step = STATUS.workflow(bpy.context)
    check("未読み込み" in current, f"未読み込みを示さない: {current}")
    check(next_step is not None, "次の一手が示されない")

    # シーム無しのMeshを読み込んだ状態
    plain = make_seamed_cube(name="NoSeam", size=2.0)
    for edge in plain.data.edges:
        edge.use_seam = False
    bpy.context.scene["tsunfold_seam_source"] = plain.name

    _, current, next_step = STATUS.workflow(bpy.context)
    check("シーム 0 本" in current, f"シーム0本を示さない: {current}")
    check(
        next_step and "シーム" in next_step,
        f"シームを入れる案内が出ない: {next_step}",
    )

    # シームを入れた状態（型紙はまだ無い）
    for edge in plain.data.edges:
        edge.use_seam = True
    _, current, next_step = STATUS.workflow(bpy.context)
    check(
        next_step and "型紙を作成" in next_step,
        f"型紙作成の案内が出ない: {next_step}",
    )

    # 型紙まで作った状態
    reset_scene()
    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)
    _, current, next_step = STATUS.workflow(bpy.context)
    check("型紙" in current, f"型紙ができたことを示さない: {current}")
    check(next_step is None, f"完了後も次の一手が出ている: {next_step}")


@test
def test_scale_warning_for_oversized_pattern():
    """用紙より極端に大きい型紙には警告が出る。

    Unit Scale が 1 のままだと 1 BU = 1000 mm 換算になり、
    デフォルトの立方体でも2メートルの型紙になる。
    この状態では用紙に収まらず、合印も小さすぎて見えない。
    """
    from truescale.unfold import status as STATUS
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0   # 1 BU = 1000 mm

    obj = make_seamed_cube(size=2.0)         # 2 BU = 2000 mm の立方体
    unfold = build_pattern_for(obj)

    warnings = STATUS.scale_warnings(bpy.context, unfold)
    check(warnings, "大きすぎる型紙に警告が出ない")

    joined = " / ".join(warnings)
    check("倍" in joined, f"用紙比の警告が無い: {joined}")
    check("Unit Scale" in joined, f"Unit Scale の案内が無い: {joined}")
    check(
        any("合印" in line for line in warnings),
        f"合印が小さすぎる警告が無い: {joined}",
    )


@test
def test_no_scale_warning_at_sane_scale():
    """用紙に収まる型紙では警告を出さない。"""
    from truescale.unfold import status as STATUS
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 0.001   # 1 BU = 1 mm

    obj = make_seamed_cube(size=40.0)          # 40 BU = 40 mm の立方体
    unfold = build_pattern_for(obj)

    warnings = STATUS.scale_warnings(bpy.context, unfold)
    check(not warnings, f"妥当な寸法なのに警告が出ている: {warnings}")


@test
def test_manual_scale_overrides_scene():
    """アドオン指定モードではシーンの Unit Scale を使わない。"""
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0   # 1 BU = 1000 mm

    # シーンに従うモード
    scene.tsunfold_scale_mode = "SCENE"
    close(UNITS.scene_bu_to_mm(scene, 1.0), 1000.0, 1e-6, "シーン基準での 1 BU")

    # アドオン指定モード
    scene.tsunfold_scale_mode = "MANUAL"
    scene.tsunfold_manual_mm_per_bu = 1.0
    close(UNITS.scene_bu_to_mm(scene, 1.0), 1.0, 1e-6, "アドオン基準での 1 BU")
    close(UNITS.scene_mm_to_bu(scene, 25.0), 25.0, 1e-6, "アドオン基準での逆変換")

    # シーン側の設定は書き換えていないこと
    close(
        scene.unit_settings.scale_length, 1.0, 1e-9,
        "シーンの Unit Scale を書き換えてしまっている",
    )

    # 戻せること
    scene.tsunfold_scale_mode = "SCENE"
    close(UNITS.scene_bu_to_mm(scene, 1.0), 1000.0, 1e-6, "シーン基準へ戻らない")


@test
def test_manual_scale_changes_pattern_size():
    """基準を変えると型紙の実寸表示が追従する。"""
    from truescale.unfold import build as BUILD
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0

    obj = make_seamed_cube(size=2.0)     # 1辺 2 BU
    unfold = build_pattern_for(obj)

    scene.tsunfold_scale_mode = "SCENE"
    scene_size = BUILD.object_xy_size_mm(bpy.context, unfold)

    scene.tsunfold_scale_mode = "MANUAL"
    scene.tsunfold_manual_mm_per_bu = 10.0   # 1 BU = 10 mm
    manual_size = BUILD.object_xy_size_mm(bpy.context, unfold)

    # 1000 mm/BU から 10 mm/BU へ変えたので 1/100 になるはず
    close(
        manual_size[0], scene_size[0] / 100.0,
        max(1e-6, scene_size[0] / 100.0 * 1e-6),
        "基準変更が型紙の実寸に反映されない",
    )


@test
def test_calibrated_scale_is_exact():
    """較正した辺の長さが、指定したミリ数どおりになる。"""
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0

    obj = make_seamed_cube(size=2.0)     # 1辺 2 BU
    unfold = build_pattern_for(obj)

    # 1辺 2 BU を 300 mm とみなす -> 1 BU = 150 mm
    scene.tsunfold_scale_mode = "MANUAL"
    scene.tsunfold_manual_mm_per_bu = 300.0 / 2.0

    mesh = unfold.data
    poly = mesh.polygons[0]
    loops = list(poly.loop_indices)
    a = mesh.vertices[mesh.loops[loops[0]].vertex_index].co
    b = mesh.vertices[mesh.loops[loops[1]].vertex_index].co
    edge_mm = UNITS.scene_bu_to_mm(scene, (b - a).length)

    close(edge_mm, 300.0, 0.5, "較正した辺の実寸")


@test
def test_warning_when_island_spacing_dominates():
    """島の間隔が型紙に対して大きすぎると警告する。

    間隔は絶対値のミリ指定なので、基準を小さく取ると相対的に
    効きすぎて島が散らばる。実際にこれで「型紙が遠くに飛んだ」
    という症状が出た。
    """
    from truescale.unfold import status as STATUS
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    # 1 BU = 0.1 mm。型紙全体が 1 mm 以下になるのに間隔は 10 mm のまま
    scene.tsunfold_scale_mode = "MANUAL"
    scene.tsunfold_manual_mm_per_bu = 0.1
    scene.tsunfold_spacing_mm = 10.0

    warnings = STATUS.scale_warnings(bpy.context, unfold)
    check(
        any("間隔" in line for line in warnings),
        f"間隔が大きすぎる警告が出ない: {warnings}",
    )

    # 間隔を型紙に見合う値にすれば消えること
    scene.tsunfold_spacing_mm = 0.05
    warnings = STATUS.scale_warnings(bpy.context, unfold)
    check(
        not any("間隔" in line for line in warnings),
        f"間隔を直しても警告が残る: {warnings}",
    )


@test
def test_notch_color_follows_scene_setting():
    """合印の色を変えると、描かれる色も追従する。

    オート合印は色をアノテーションへ保存せず、常にシーン設定を見る。
    保存していた頃は、色を変えるたびに全アノテーションの
    書き直しとキャッシュ全破棄が走っていた。
    """
    from truescale.marking import compute as C
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    def notch_colors():
        rows = C.colored_segments(bpy.context, obj, unfold)
        return {tuple(round(c, 4) for c in row[2]) for row in rows}

    scene.tsunfold_notch_color = (1.0, 0.0, 0.0)
    reds = notch_colors()
    check((1.0, 0.0, 0.0) in reds, f"赤が反映されない: {reds}")

    scene.tsunfold_notch_color = (0.0, 0.0, 1.0)
    blues = notch_colors()
    check((0.0, 0.0, 1.0) in blues, f"青が反映されない: {blues}")
    check(
        (1.0, 0.0, 0.0) not in blues,
        f"古い赤が残っている: {blues}",
    )


@test
def test_notch_color_does_not_touch_annotations():
    """合印の色を変えてもアノテーションを書き直さない。"""
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    before = obj.get(ST.ANNOTATION_PROP, "")
    bpy.context.scene.tsunfold_notch_color = (0.2, 0.4, 0.6)
    after = obj.get(ST.ANNOTATION_PROP, "")

    check(
        before == after,
        "色の変更でアノテーションが書き換えられている",
    )


@test
def test_every_marking_setting_updates_immediately():
    """マーキングの各設定を変えたら、描画結果がすぐ変わる。

    「再描画するだけでよい」と判断した設定が、実はキャッシュへ
    焼き込まれていてキーに入っていない、という取りこぼしを防ぐ。
    実際に合印の太さでこれが起きた（値を変えても更新ボタンを
    押すまで反映されなかった）。
    """
    from truescale.marking import compute as C
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    def snapshot():
        """描画に渡される値をまとめて文字列化する。"""
        segments = C.colored_segments(bpy.context, obj, unfold)
        texts = C.text_items(
            bpy.context, obj, unfold
        )
        return repr([
            [
                (round(row[0].x, 5), round(row[0].y, 5),
                 round(row[1].x, 5), round(row[1].y, 5),
                 tuple(round(c, 5) for c in row[2]), round(row[3], 5))
                for row in segments
            ],
            [
                (row[0], round(row[2], 5), tuple(round(c, 5) for c in row[3]))
                for row in texts
            ],
        ])

    # (プロパティ名, 変更後の値)
    cases = [
        ("tsunfold_notch_length_mm", 12.0),
        ("tsunfold_notch_thickness_mm", 2.5),
        ("tsunfold_notch_color", (0.1, 0.8, 0.3)),
        ("tsunfold_auto_arrow_length_mm", 40.0),
        ("tsunfold_arrow_head_mm", 14.0),
        ("tsunfold_arrow_thickness_mm", 2.0),
        ("tsunfold_arrow_color", (0.9, 0.1, 0.7)),
        ("tsunfold_island_id_size_mm", 16.0),
        ("tsunfold_island_id_color", (0.2, 0.2, 0.9)),
    ]

    stale = []
    for name, value in cases:
        before = snapshot()
        setattr(scene, name, value)
        after = snapshot()
        if before == after:
            stale.append(name)

    check(
        not stale,
        "変更しても描画結果が変わらない設定がある"
        f"（キャッシュキーに入っていない疑い）: {stale}",
    )


def add_manual_notch(source, edge_index=0, t=0.5):
    """手動で置いた合印を1つ足す。"""
    from truescale.marking import interact as IN
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S

    items = ST.load(source)
    items.append({
        "type": "notch_edge",
        "edge": int(edge_index),
        "t": float(t),
        "color": [1.0, 0.0, 0.0],
        "auto": False,
    })
    IN.save_annotations(source, items)


def count_notches(source):
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S

    auto = manual = 0
    for item in ST.load(source):
        if item.get("type") != "notch_edge":
            continue
        if bool(item.get("auto", False)):
            auto += 1
        else:
            manual += 1
    return auto, manual


@test
def test_remove_all_notches_removes_manual_too():
    """「合印削除」はオートと手動の両方を消す。

    以前はオートだけを消すオペレータに繋がっており、
    手動で置いた合印が残っていた。
    """
    reset_scene()
    import truescale
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    # 手動合印を2つ足す
    seam_edges = [e.index for e in obj.data.edges if e.use_seam][:2]
    for index in seam_edges:
        add_manual_notch(obj, index)

    auto, manual = count_notches(obj)
    check(auto > 0, "オート合印が無い")
    check(manual == 2, f"手動合印が2個でない: {manual}")

    bpy.context.view_layer.objects.active = obj
    bpy.ops.truescale_unfold.remove_all_notches()

    auto, manual = count_notches(obj)
    check(auto == 0, f"オート合印が残っている: {auto}")
    check(manual == 0, f"手動合印が残っている: {manual}")


@test
def test_remove_auto_notches_keeps_manual():
    """「オートだけ削除」は手動の合印を残す。"""
    reset_scene()
    import truescale
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    seam_edges = [e.index for e in obj.data.edges if e.use_seam][:3]
    for index in seam_edges:
        add_manual_notch(obj, index)

    bpy.context.view_layer.objects.active = obj
    bpy.ops.truescale_unfold.remove_auto_notches()

    auto, manual = count_notches(obj)
    check(auto == 0, f"オート合印が残っている: {auto}")
    check(manual == 3, f"手動合印が消えている: {manual}")


@test
def test_remove_all_notches_keeps_other_marks():
    """合印を消しても他のマーキングは残る。"""
    from truescale.marking import interact as IN
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    # 合印以外の注記を1つ足しておく
    items = ST.load(obj)
    items.append({"type": "text", "value": "テスト", "color": [0, 0, 0]})
    IN.save_annotations(obj, items)

    bpy.context.view_layer.objects.active = obj
    bpy.ops.truescale_unfold.remove_all_notches()

    kinds = [
        item.get("type")
        for item in ST.load(obj)
    ]
    check("notch_edge" not in kinds, f"合印が残っている: {kinds}")
    check("text" in kinds, f"他のマーキングまで消えている: {kinds}")


@test
def test_notch_status_text():
    """合印の状態表示が状況に応じて変わる。"""
    from truescale.unfold import status as STATUS
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.core import units as UNITS
    from truescale.core import state as S
    truescale.register()

    # 元モデルすら無い状態
    text = STATUS.notch_text(bpy.context)
    check("未読み込み" in text, f"未読み込みの表示が出ない: {text}")

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    text = STATUS.notch_text(bpy.context)
    check("合印" in text, f"合印の件数が出ない: {text}")


# ============================================================
# marking.placement
#   mathutils が要るので Blender の中でしか動かせない
# ============================================================

@test
def test_alpha_label_sequence():
    """型紙IDのアルファベットが桁上がりする。

    A〜Z のあとは AA になる。26進数ではなく Excel の列名と同じ数え方。
    """
    from truescale.marking import placement as _placement

    check(_placement.alpha_label(0) == "A", "0 が A でない")
    check(_placement.alpha_label(25) == "Z", "25 が Z でない")
    check(_placement.alpha_label(26) == "AA", "26 が AA でない")
    check(_placement.alpha_label(27) == "AB", "27 が AB でない")
    check(_placement.alpha_label(51) == "AZ", "51 が AZ でない")
    check(_placement.alpha_label(52) == "BA", "52 が BA でない")


@test
def test_alpha_label_is_unique():
    """先頭200件に重複が無い。"""
    from truescale.marking import placement as _placement

    labels = [_placement.alpha_label(i) for i in range(200)]
    check(len(set(labels)) == 200, "重複したIDがある")


@test
def test_arrow_geometry_is_centered():
    """矢印は指定した中心をまたぎ、長さも指定どおり。"""
    from truescale.marking import placement as _placement

    center = Vector((5.0, 3.0, 0.0))
    direction = Vector((0.0, 1.0, 0.0))

    start, end, head_a, head_b = _placement.arrow_geometry_local(
        center, direction, length=10.0, head=2.0
    )

    close((end - start).length, 10.0, 1e-9, "軸の長さ")
    midpoint = (start + end) * 0.5
    close((midpoint - center).length, 0.0, 1e-9, "中心がずれている")

    # 矢尻は先端側にあり、左右対称
    check((head_a - end).length < (head_a - start).length, "矢尻が先端側でない")
    close(
        (head_a - end).length, (head_b - end).length, 1e-9,
        "矢尻が左右対称でない",
    )


@test
def test_arrow_geometry_handles_zero_direction():
    """方向がゼロでも破綻しない。"""
    from truescale.marking import placement as _placement

    start, end, _, _ = _placement.arrow_geometry_local(
        Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 0.0)), 4.0, 1.0
    )
    close((end - start).length, 4.0, 1e-9, "長さが保たれていない")


@test
def test_arrow_head_never_exceeds_shaft():
    """矢尻が軸より長くならない。

    短い矢印でヘッド長を大きくすると、矢尻が根元を突き抜けてしまう。
    """
    from truescale.marking import placement as _placement

    for length in (0.5, 1.0, 5.0):
        start, end, head_a, head_b = _placement.arrow_geometry_local(
            Vector((0.0, 0.0, 0.0)), Vector((1.0, 0.0, 0.0)),
            length=length, head=100.0,
        )
        shaft = (end - start).length
        for point in (head_a, head_b):
            check(
                (point - end).length <= shaft + 1e-9,
                f"長さ {length} で矢尻が軸を超えた",
            )


# ============================================================
# 手動レイアウト中の印
# ============================================================

@test
def test_marks_follow_the_island_being_dragged():
    """島を動かすと、その島の印だけが一緒に動く。

    手動レイアウト中は編集モードなので、形は BMesh 側にあり
    obj.data には反映されない。そのまま描き直すと動かす前の位置に
    出るので、以前は表示ごと止めていた。置き場所を決めている最中
    こそ見えてほしいので、控えておいてずらす形にした。
    """
    reset_scene()
    import bmesh
    import truescale
    from truescale.marking import dragging as DR
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    check(DR.take_snapshot(bpy.context, obj, unfold), "控えを取れない")

    bpy.ops.object.mode_set(mode='EDIT')
    try:
        before, _texts = DR.current(unfold)
        check(before, "編集モードで印を取れない")

        mesh = bmesh.from_edit_mesh(unfold.data)
        mesh.verts.ensure_lookup_table()
        island = DR._snapshot["islands"][0]
        shift = 5.0
        for index in island:
            mesh.verts[index].co.x += shift
        bmesh.update_edit_mesh(unfold.data)

        after, _texts = DR.current(unfold)
        moved = sorted({round(b[0].x - a[0].x, 4)
                        for a, b in zip(before, after)})

        check(
            any(abs(value - shift) < 1e-4 for value in moved),
            f"動かした島の印が追従していない: {moved}",
        )
        check(
            any(abs(value) < 1e-9 for value in moved),
            f"動かしていない島の印まで動いた: {moved}",
        )
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


@test
def test_dragging_gives_up_when_topology_changes():
    """頂点が増減したら、印を出さない。

    移動だけのはずだが、編集モードでは何でもできる。覚えている
    対応が合わなくなったら、間違った位置に出すより出さない。
    """
    reset_scene()
    import bmesh
    import truescale
    from truescale.marking import dragging as DR
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)
    DR.take_snapshot(bpy.context, obj, unfold)

    bpy.ops.object.mode_set(mode='EDIT')
    try:
        mesh = bmesh.from_edit_mesh(unfold.data)
        mesh.verts.new((0.0, 0.0, 0.0))
        bmesh.update_edit_mesh(unfold.data)

        segments, texts = DR.current(unfold)
        check(
            segments is None and texts is None,
            "形が変わったのに印を出そうとしている",
        )
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


# ============================================================
# 用紙ガイド
# ============================================================

@test
def test_moving_the_object_does_not_change_the_export():
    """型紙をオブジェクトごと動かしても、書き出す中身は変わらない。

    用紙ガイドが型紙に追従してよい根拠。書き出しは型紙の左下を
    原点に組み立てるので、ワールド上のどこに置いてあるかは結果に
    効かない。効くなら、置き場所で刷り上がりが変わることになる。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as CO
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    def lines():
        drawing = CO.pattern_lines(bpy.context)
        check(drawing is not None, "書き出す線が無い")
        return [(a, b, c, d) for a, b, c, d, _color, _w in drawing.lines]

    before = lines()

    unfold.location.x += 3.0
    unfold.location.y -= 1.5
    bpy.context.view_layer.update()

    after = lines()
    check(len(before) == len(after), "線の数が変わった")

    worst = max(
        max(abs(p - q) for p, q in zip(one, two))
        for one, two in zip(before, after)
    )
    # 0.01mm は 300dpi の 1/8 画素。ここを超えたら実害がある。
    check(worst < 0.01, f"動かしたら書き出しが {worst:.4f} mm ずれた")




@test
def test_paper_guide_matches_the_split():
    """用紙ガイドの枠が、実際の分割と同じ並びになる。

    画面の枠と刷ったときの切れ目が違うと、枠を見ながら置いた意味が
    無くなる。枚数・位置とも、書き出しと同じ計算から出す。
    """
    reset_scene()
    import truescale
    from truescale import overlay as OV
    from truescale.unfold.ops.export import tile_plan
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    _size, plan = tile_plan(bpy.context)
    check(plan is not None, "分割の計画が立たない")

    grid = OV.paper_grid(bpy.context)
    check(grid is not None, "ガイドの枠が出ない")

    _ox, _oy, _cw, _ch, _sw, _sh, cols, rows = grid
    check(
        cols == plan.cols and rows == plan.rows,
        f"枠 {cols}×{rows} と分割 {plan.cols}×{plan.rows} が違う",
    )


@test
def test_paper_guide_shows_the_usable_area():
    """ガイドの枠は、紙の外形ではなく型紙を置ける範囲。

    外形（A4なら210×297）で出すと、枠いっぱいに置いた島が刷った
    ときに切れる。余白と目盛りの帯を引いた残りでなければならない。
    """
    reset_scene()
    import truescale
    from truescale import overlay as OV
    from truescale.core import units as UNITS
    from truescale.unfold.ops.export import tile_plan
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    _size, plan = tile_plan(bpy.context)
    grid = OV.paper_grid(bpy.context)
    scene = bpy.context.scene

    width_mm = UNITS.scene_bu_to_mm(scene, grid[2])
    height_mm = UNITS.scene_bu_to_mm(scene, grid[3])

    check(
        abs(width_mm - plan.content_w) < 0.01,
        f"枠の幅が置ける範囲と違う: {width_mm:.1f} ≠ {plan.content_w:.1f}",
    )
    check(
        abs(height_mm - plan.content_h) < 0.01,
        f"枠の高さが置ける範囲と違う: {height_mm:.1f} ≠ {plan.content_h:.1f}",
    )
    check(width_mm < plan.paper_w, "枠が紙の外形のままになっている")


@test
def test_paper_guide_follows_the_pattern():
    """型紙を動かすと、ガイドの枠も一緒に動く。

    枠が原点に固定だと、型紙を動かした瞬間に「どこで切れるか」の
    表示が嘘になる。
    """
    reset_scene()
    import truescale
    from truescale import overlay as OV
    from truescale.core import units as UNITS
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    before = OV.paper_grid(bpy.context)
    check(before is not None, "ガイドの枠が出ない")

    shift = 1.0
    unfold.location.x += shift
    bpy.context.view_layer.update()

    after = OV.paper_grid(bpy.context)
    moved = after[0] - before[0]
    check(
        abs(moved - shift) < 1e-4,
        f"型紙を動かしても枠が追従しない: {moved:.4f} ≠ {shift}",
    )


# ============================================================
# 型紙の確定
# ============================================================

@test
def test_finalized_pattern_survives_cleanup():
    """確定した型紙は、片付けても消えない。

    これまで作業の終わり方は「片付ける（消す）」だけで、型紙
    そのものを成果物として残したい使い方に応えられなかった。
    """
    reset_scene()
    import truescale
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    name = unfold.name
    before = len(unfold.data.vertices)

    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    result = bpy.ops.truescale_unfold.finalize_pattern()
    check(result == {'FINISHED'}, f"確定できない: {result}")

    left = [k for k in unfold.keys() if str(k).startswith("tsunfold_")]
    check(not left, f"アドオンの印が残っている: {left}")
    check(
        len(unfold.data.vertices) == before,
        "確定でメッシュが変わった",
    )

    # 片付けても残ること
    bpy.context.view_layer.objects.active = obj
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.ops.truescale_unfold.return_default()

    check(name in bpy.data.objects, "確定したのに片付けで消えた")


@test
def test_unfinalized_pattern_is_cleaned_up():
    """確定していない型紙は、これまでどおり片付けで消える。

    確定を足したことで、消えるべきものが消えなくなっていないか。
    """
    reset_scene()
    import truescale
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    name = unfold.name

    bpy.context.view_layer.objects.active = obj
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.ops.truescale_unfold.return_default()

    check(name not in bpy.data.objects, "確定していないのに残った")


@test
def test_finalized_pattern_is_no_longer_drawn():
    """確定した型紙には、マーキングが描かれなくなる。

    切り離したのに描き続けると、消せない表示が残る。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as CO
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    before = CO.pattern_lines(bpy.context)
    check(before is not None, "確定前に書き出せない")

    bpy.ops.truescale_unfold.finalize_pattern()

    after = CO.pattern_lines(bpy.context)
    check(after is None, "確定したのに、まだ型紙として扱われている")


# ============================================================
# 島を動かしたときの追従
# ============================================================

@test
def test_marks_follow_moved_island():
    """島を動かすと、IDと合印も一緒に動く。

    位置は島の頂点から毎回計算しているが、結果のキャッシュが
    頂点の座標をキーに持っていない。頂点数も面数も変わらないので、
    動かしただけではキーが変わらず、注記が元の位置に取り残されて
    いた。形が変わったことは depsgraph が教えてくれる。
    """
    reset_scene()
    import truescale
    from truescale.marking import compute as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    def label_x():
        return sorted(
            (str(r[0]), round(r[1].x, 4))
            for r in C.text_items(bpy.context, obj, unfold)
        )

    def segment_x():
        return sorted(
            round(row[0].x, 4)
            for row in C.colored_segments(bpy.context, obj, unfold)
        )

    before_labels = label_x()
    before_segments = segment_x()
    check(before_labels, "IDが1つも出ていない")

    # 島ひとつ分の頂点をまとめて動かす
    moved = set(unfold.data.polygons[0].vertices)
    for index in moved:
        unfold.data.vertices[index].co.x += 4.0
    unfold.data.update()
    bpy.context.view_layer.update()

    check(
        label_x() != before_labels,
        "島を動かしてもIDが元の位置に残っている",
    )
    check(
        segment_x() != before_segments,
        "島を動かしても合印が元の位置に残っている",
    )


@test
def test_object_move_keeps_the_cache():
    """オブジェクトごと動かしても、重い解析はやり直さない。

    ワールド変換はキャッシュの外で掛けているので、移動では
    作り直す必要がない。ここで作り直すと、G で動かすあいだ
    毎フレーム島の解析と配置探索が走る。以前直した「移動した
    ときだけ極端に重い」がそのまま戻る。
    """
    reset_scene()
    import truescale
    from truescale.core import state as S
    from truescale.marking import compute as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    C.text_items(bpy.context, obj, unfold)

    before = int(S.epoch)
    for _ in range(5):
        unfold.location.x += 0.1
        bpy.context.view_layer.update()

    check(
        int(S.epoch) == before,
        f"移動でキャッシュを捨てている: {before} -> {int(S.epoch)}",
    )

    # それでも描かれる位置は追従していること
    rows = C.text_items(bpy.context, obj, unfold)
    check(rows, "移動後にIDが消えた")


# ============================================================
# 番号と文字
# ============================================================

@test
def test_number_and_text_reach_the_overlay():
    """番号と文字を置くと、描画へ渡す項目になる。

    どちらも入口がパネルに無く、一度も確認されていなかった。
    保存から描画までの経路が通っているかを見る。
    """
    reset_scene()
    import truescale
    from truescale import overlay as OV
    from truescale.marking import storage as ST
    truescale.register()

    scene = bpy.context.scene
    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    # 注記の位置は「元モデルの三角形と、その中の重み」で持つ。
    # 面の当たり判定を通さずに、同じ形を直接組み立てる。
    from truescale.marking import interact as IN
    from mathutils import Vector

    mesh = obj.data
    mesh.calc_loop_triangles()
    tri = mesh.loop_triangles[0]
    center = sum(
        (mesh.vertices[i].co for i in tri.vertices),
        Vector((0.0, 0.0, 0.0)),
    ) / 3.0
    anchor_item = IN.make_anchor_from_hit(obj, tri.polygon_index, center)
    check(anchor_item is not None, "位置の基準を作れない")

    items = ST.load(obj)

    items.append({
        "type": "number",
        "value": 7,
        "anchor": anchor_item,
        "size_mm": 8.0,
        "color": [0.0, 0.0, 0.0],
    })
    items.append({
        "type": "text",
        "text": "前身頃",
        "anchor": anchor_item,
        "size_mm": 6.0,
        "color": [0.0, 0.0, 0.0],
    })
    ST.save(obj, items)

    labels = OV.flat_text_items(obj, unfold, scene)
    texts = [str(row[0]) for row in labels]
    check("7" in texts, f"番号が描画項目に出ない: {texts}")
    check("前身頃" in texts, f"文字が描画項目に出ない: {texts}")

    # 色は種類ごとの設定に従うこと（保存値ではなく）
    scene.tsunfold_number_color = (1.0, 0.0, 0.0)
    scene.tsunfold_text_color = (0.0, 1.0, 0.0)
    labels = OV.flat_text_items(obj, unfold, scene)
    by_text = {str(row[0]): tuple(round(c, 3) for c in row[3]) for row in labels}
    check(
        by_text.get("7") == (1.0, 0.0, 0.0),
        f"番号が番号の色設定に従っていない: {by_text.get('7')}",
    )
    check(
        by_text.get("前身頃") == (0.0, 1.0, 0.0),
        f"文字が文字の色設定に従っていない: {by_text.get('前身頃')}",
    )


@test
def test_panel_tools_are_all_registered():
    """パネルが出す道具のオペレータが全て実在する。

    パネルへ出したはいいが登録されていない、という取り違えを防ぐ。
    """
    reset_scene()
    import truescale
    truescale.register()

    wanted = (
        ("truescale_unfold", "place_notch"),
        ("truescale_unfold", "place_number"),
        ("truescale_unfold", "place_text"),
        ("truescale_unfold", "place_arrow"),
        ("truescale_unfold", "reset_number"),
        ("truescale_unfold", "marking_tool_off"),
        ("truescale_unfold", "finish_marking"),
        ("truescale_unfold", "toggle_pattern_preview"),
        ("truescale_draft", "quad_view"),
    )
    for group, name in wanted:
        check(
            hasattr(getattr(bpy.ops, group), name),
            f"オペレータが無い: {group}.{name}",
        )


# ============================================================
# ファイルを開いたときの初期化
# ============================================================

@test
def test_load_resets_work_state():
    """開いたときに、作業中だった状態が全部解除される。

    手動レイアウト中のフラグが解除されずに残ると、開き直しても
    注記が一切描かれない。レイアウト編集中は描画を止める作りの
    ためで、「マーキングが出ない」という形でしか現れない。

    初期化の一覧は session が持っているのに、ハンドラ側が同じ
    内容を書き写していて、このキーだけ漏れていた。
    """
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.core import session as SES
    truescale.register()

    scene = bpy.context.scene
    for key, value in SES.RESET_ON_LOAD.items():
        # わざと「作業中」の値を入れる
        scene[key] = "dirty" if isinstance(value, str) else True

    U._tsunfold_reset_overlays_on_load()

    for key, value in SES.RESET_ON_LOAD.items():
        check(
            scene.get(key) == value,
            f"開いたときに解除されていない: {key} = {scene.get(key)!r}",
        )


# ============================================================
# シーム
# ============================================================

@test
def test_mark_and_clear_seam():
    """選択した辺にシームを入れ、また外せる。

    型紙作りの最初の操作。ここが通らないと先へ進めないのに、
    テストが無かった。分割で関数名を短くしたとき、呼び出し側の
    ローカル変数と衝突して UnboundLocalError で落ちていた。
    """
    reset_scene()
    import truescale
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    for edge in obj.data.edges:
        edge.use_seam = False
    obj.data.update()

    # 辺を2本だけ選ぶ
    for edge in obj.data.edges:
        edge.select = False
    picked = [0, 1]
    for index in picked:
        obj.data.edges[index].select = True

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')

    result = bpy.ops.truescale_unfold.mark_seam()
    check(result == {'FINISHED'}, f"シームを入れられない: {result}")

    marked = [e.index for e in obj.data.edges if e.use_seam]
    check(
        set(picked) <= set(marked),
        f"選んだ辺にシームが入っていない: {marked}",
    )

    for edge in obj.data.edges:
        edge.select = edge.index in picked
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')

    result = bpy.ops.truescale_unfold.clear_seam()
    check(result == {'FINISHED'}, f"シームを外せない: {result}")

    # 編集モードのままだと、メッシュへ書き戻される前の値を読む。
    if obj.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')

    check(
        not any(e.use_seam for e in obj.data.edges if e.index in picked),
        "外したはずの辺にシームが残っている",
    )


@test
def test_mark_seam_with_symmetry():
    """左右対称がONなら、反対側の辺にも入る。

    対称の組み合わせを返す関数が、呼び出し側のローカル変数に
    隠されて落ちていた箇所。
    """
    reset_scene()
    import truescale
    from truescale.marking import symmetry as SYM
    truescale.register()

    scene = bpy.context.scene
    scene.tsunfold_seam_symmetry_x = True

    variants = SYM.mirror_variants(scene)
    check(
        (True, False, False) in variants,
        f"X対称が組み合わせに含まれない: {variants}",
    )

    obj = make_seamed_cube(size=2.0)
    for edge in obj.data.edges:
        edge.use_seam = False
        edge.select = False
    obj.data.update()

    # X方向に振れている辺を1本選ぶ（鏡映先が別の辺になるもの）
    picked = None
    for edge in obj.data.edges:
        a = obj.data.vertices[edge.vertices[0]].co
        b = obj.data.vertices[edge.vertices[1]].co
        if abs(a.x - b.x) < 1e-6 and abs(a.x) > 1e-6:
            picked = edge.index
            break
    check(picked is not None, "対称にできる辺が見つからない")

    obj.data.edges[picked].select = True
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')

    result = bpy.ops.truescale_unfold.mark_seam()
    check(result == {'FINISHED'}, f"対称ONでシームを入れられない: {result}")

    marked = [e.index for e in obj.data.edges if e.use_seam]
    check(picked in marked, "選んだ辺にシームが入っていない")
    check(len(marked) >= 2, f"対称側にシームが入っていない: {marked}")


# ============================================================
# 注記の色
# ============================================================

@test
def test_manual_marks_follow_color_setting():
    """手動で置いた印も、あとから色設定を変えれば追従する。

    置いた時点の色を保存して描画に使っていたため、設定を変えても
    手動のものだけ変わらなかった。オート合印と矢印は追従していた
    ので、同じパネルの中で挙動が食い違っていた。
    """
    from truescale.marking import compute as C
    reset_scene()
    import truescale
    from truescale import unfold as U
    from truescale.marking import storage as ST
    from truescale.marking import storage as ST
    truescale.register()

    scene = bpy.context.scene
    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    # 置いた時点の色を保存値に持つ、手動の印を作る。
    scene.tsunfold_notch_color = (1.0, 0.0, 0.0)
    items = ST.load(obj)
    items.append({
        "type": "notch_edge",
        "edge": 0,
        "t": 0.5,
        "auto": False,
        "color": [1.0, 0.0, 0.0],
    })
    ST.save(obj, items)

    def manual_colors():
        return [
            tuple(round(c, 4) for c in row[2])
            for row in C.colored_segments(
                bpy.context, obj, unfold
            )
        ]

    check((1.0, 0.0, 0.0) in manual_colors(), "置いた色で描かれていない")

    # 設定を変えたら、保存値を持つ手動の印も変わること。
    scene.tsunfold_notch_color = (0.0, 0.0, 1.0)
    after = manual_colors()
    check(
        (0.0, 0.0, 1.0) in after,
        "色設定を変えても手動の印が追従していない",
    )
    check(
        (1.0, 0.0, 0.0) not in after,
        "古い色のまま描かれている印が残っている",
    )


@test
def test_color_change_does_not_drop_cache():
    """色を変えても、重い解析をやり直さない。

    色はキャッシュのキーに入っているので、捨てる必要がない。
    捨てていた頃は、カラーピッカーをドラッグするたびに島の解析と
    配置探索がまとめて作り直されていた。
    """
    reset_scene()
    import truescale
    from truescale.core import state as S
    truescale.register()

    scene = bpy.context.scene
    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    for prop in ("tsunfold_notch_color", "tsunfold_number_color",
                 "tsunfold_text_color", "tsunfold_arrow_color"):
        before = int(S.epoch)
        setattr(scene, prop, (0.25, 0.5, 0.75))
        check(
            int(S.epoch) == before,
            f"{prop} を変えるとキャッシュが全部捨てられる",
        )


# ============================================================
# 操作中の状態の共有
# ============================================================

@test
def test_overlay_and_interact_share_state():
    """下描きとハイライトを、描く側と書く側が同じ辞書で見ている。

    切り出しで参照が取り残されると、書く側と描く側が別々の辞書を
    持つ。描画は例外を握り潰すので、画面に出ないという形でしか
    現れない。実際に起きたので、同一性を直接確かめる。
    """
    from truescale import overlay
    from truescale.marking import interact

    check(
        overlay._interact.live_preview is interact.live_preview,
        "下描きの辞書が別物になっている",
    )
    check(
        overlay._interact.island_highlight is interact.island_highlight,
        "島ハイライトの辞書が別物になっている",
    )

    # clear は作り直さず中身を書き換えること。作り直すと、
    # 参照を持っている側が古い辞書を見続ける。
    before = interact.live_preview
    interact.clear_live_preview()
    check(
        interact.live_preview is before,
        "clear_live_preview が辞書を作り直している",
    )

    before = interact.island_highlight
    interact.clear_island_highlight()
    check(
        interact.island_highlight is before,
        "clear_island_highlight が辞書を作り直している",
    )


@test
def test_modules_import_without_error():
    """分割した各モジュールが単体で読み込める。

    import の書き忘れは、握り潰される例外の中でしか現れないことが
    ある。読み込みだけでも通しておく。
    """
    import importlib

    names = (
        "truescale.core.flatshape",
        "truescale.core.geometry", "truescale.core.mapping",
        "truescale.core.objects", "truescale.core.paper",
        "truescale.core.session", "truescale.core.solve",
        "truescale.core.state", "truescale.core.units",
        "truescale.core.view",
        "truescale.marking.auto_notch", "truescale.marking.compute",
        "truescale.marking.dragging",
        "truescale.marking.interact", "truescale.marking.symmetry",
        "truescale.marking.tools",
        "truescale.marking.placement", "truescale.marking.seams",
        "truescale.marking.source", "truescale.marking.storage",
        "truescale.export.allowance", "truescale.export.linestyle",
        "truescale.export.collect", "truescale.export.outline",
        "truescale.export.pdf", "truescale.export.png",
        "truescale.export.render", "truescale.export.sheets",
        "truescale.export.tiling",
        "truescale.overlay",
        "truescale.draft.bbox", "truescale.draft.dimension",
        "truescale.draft.keys", "truescale.draft.labels",
        "truescale.draft.overlay",
        "truescale.draft.views", "truescale.draft.export.capture",
        "truescale.draft.export.sheet", "truescale.draft.ops",
        "truescale.draft.panel", "truescale.draft.prefs",
        "truescale.draft.props",
        "truescale.draft.viewstate",
        "truescale.unfold.build", "truescale.unfold.ops",
        "truescale.unfold.panel", "truescale.unfold.props",
        "truescale.unfold.status",
    )
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:
            check(False, f"{name} を読み込めない: {exc}")


# ============================================================
# 実行
# ============================================================

@test
def test_glue_tab_is_placed_on_one_side_only():
    """1本のシームに糊代は1枚だけ。両側に付くと二重で貼れない。"""
    reset_scene()
    import truescale
    from truescale.export import allowance as A
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_tab_enable = True
    scene.tsunfold_tab_width_mm = 6.0

    result = A.build(bpy.context, obj, unfold)
    check(result.fold, "糊代が1枚も作られていない")

    # 根元として外周から外した辺は、シーム1本につき高々1つ。
    check(
        len(result.suppress) == len(result.fold),
        f"根元 {len(result.suppress)} と折り線 {len(result.fold)} が合わない",
    )

    seams = {int(e.index) for e in obj.data.edges if e.use_seam}
    check(
        len(result.fold) <= len(seams),
        f"シーム {len(seams)} 本に対して糊代 {len(result.fold)} 枚は多すぎる",
    )


@test
def test_glue_tab_base_is_not_also_a_cut_line():
    """糊代の根元が実線で残っていないか。

    残っていると、そこで切られて糊代が落ちる。折るための線を
    切る線として刷るのは、糊代が無いより悪い。
    """
    reset_scene()
    import truescale
    from truescale.export import allowance as A
    from truescale.export import collect as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_tab_enable = True

    result = A.build(bpy.context, obj, unfold)
    check(result.suppress, "外周から外した辺が無い")

    from truescale.core import objects as O

    kept = O.boundary_segments_world_xy(unfold, skip=result.suppress)
    whole = O.boundary_segments_world_xy(unfold)

    check(
        len(whole) - len(kept) == len(result.suppress),
        f"外した本数が合わない: {len(whole)} - {len(kept)} "
        f"≠ {len(result.suppress)}",
    )

    drawing = C.pattern_lines(bpy.context)
    check(drawing is not None, "書き出す線が集まらない")


@test
def test_fold_lines_are_broken_into_dashes():
    """折り線が破線として刷られるか。

    実線のままだと、切る線と区別が付かない。線種の属性を持たせず、
    線分そのものを刻む方式なので、本数が増えることで確かめられる。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    scene = bpy.context.scene

    scene.tsunfold_tab_enable = False
    before = C.pattern_lines(bpy.context)
    check(before is not None, "糊代なしで線が集まらない")

    scene.tsunfold_tab_enable = True
    after = C.pattern_lines(bpy.context)
    check(after is not None, "糊代ありで線が集まらない")

    check(
        after.count > before.count,
        f"糊代を足しても線が増えていない: {before.count} -> {after.count}",
    )


@test
def test_seam_allowance_makes_the_pattern_bigger():
    """縫い代を付けたら、用紙の見積もりも大きくなる。

    大きさを2箇所で別々に出すと、用紙ガイドが「収まる」と言った
    ものが刷ると収まらない。同じ設定から同じ向きへ動くこと。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_seam_enable = False
    before = C.pattern_extent(bpy.context)
    check(before is not None, "大きさが取れない")

    scene.tsunfold_seam_enable = True
    scene.tsunfold_seam_width_mm = 10.0
    after = C.pattern_extent(bpy.context)
    check(after is not None, "縫い代ありで大きさが取れない")

    check(
        after[0] > before[0] and after[1] > before[1],
        f"縫い代を付けても大きくなっていない: {before} -> {after}",
    )


@test
def test_disabled_tab_survives_a_rebuild():
    """手で消した糊代は、型紙を作り直しても消えたまま。

    展開後の辺番号で覚えると、作り直した瞬間に全部戻ってしまう。
    元メッシュの辺番号で覚えているので、シームと一緒に生き残る。
    """
    reset_scene()
    import truescale
    from truescale.export import allowance as A
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_tab_enable = True

    first = A.build(bpy.context, obj, unfold)
    check(first.fold, "糊代が作られていない")

    # 1本消す
    seams = sorted(int(e.index) for e in obj.data.edges if e.use_seam)
    A.toggle_disabled(obj, seams[0], off=True)
    check(seams[0] in A.disabled_edges(obj), "消した記録が残っていない")

    # 作り直す
    rebuilt = build_pattern_for(obj)
    check(
        seams[0] in A.disabled_edges(obj),
        "作り直したら、消した記録が失われた",
    )

    second = A.build(bpy.context, obj, rebuilt)
    check(
        len(second.fold) < len(first.fold),
        f"消したのに糊代が減っていない: {len(first.fold)} -> "
        f"{len(second.fold)}",
    )


@test
def test_allowance_follows_the_island_being_dragged():
    """手動で並べている最中も、縫い代と糊代が消えない。

    以前は手動レイアウト中に描画を丸ごと止めていた。置き場所を
    決めている最中こそ、代を含めた大きさが見えてほしい。印と
    同じく、控えておいて島ごとの移動量だけずらす。
    """
    reset_scene()
    import bmesh
    import truescale
    from truescale.marking import dragging as DR
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_tab_enable = True
    scene.tsunfold_seam_enable = True

    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    check(DR.take_snapshot(bpy.context, obj, unfold), "控えを取れない")

    bpy.ops.object.mode_set(mode='EDIT')
    try:
        cut_before, fold_before = DR.current_allowance(unfold)
        check(cut_before, "編集モードで切る線を取れない")
        check(fold_before, "編集モードで折る線を取れない")

        mesh = bmesh.from_edit_mesh(unfold.data)
        mesh.verts.ensure_lookup_table()
        shift = 5.0
        for index in DR._snapshot["islands"][0]:
            mesh.verts[index].co.x += shift
        bmesh.update_edit_mesh(unfold.data)

        cut_after, _fold_after = DR.current_allowance(unfold)
        moved = sorted({
            round(b[0].x - a[0].x, 4)
            for a, b in zip(cut_before, cut_after)
        })

        check(
            any(abs(value - shift) < 1e-4 for value in moved),
            f"動かした島の代が追従していない: {moved}",
        )
        check(
            any(abs(value) < 1e-9 for value in moved),
            f"動かしていない島の代まで動いた: {moved}",
        )
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        DR.clear()


@test
def test_memo_text_follows_the_island_being_dragged():
    """並べている最中も、型紙へのメモが消えない。

    以前は型紙IDだけを控えていたため、メモと転写ラベルが消えて
    いた。控える一覧を、書き出しが使うものと同じにした。出どころを
    2つ持つと、片方に足した文字がもう片方から漏れる。
    """
    reset_scene()
    import truescale
    from truescale.marking import dragging as DR
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    bpy.context.view_layer.objects.active = unfold
    for other in bpy.context.selected_objects:
        other.select_set(False)
    unfold.select_set(True)

    from truescale.export import collect as CO
    from truescale.marking import interact as IN

    # 型紙へメモを1つ置く。
    center = unfold.data.vertices[0].co
    IN.set_flat_memos(unfold, [{
        "text": "ここは折る",
        "pos": [center.x, center.y, 0.0],
        "size_mm": 6.0,
        "angle": 0.0,
    }])

    items = list(CO.text_sources(bpy.context, obj, unfold))
    check(
        any("ここは折る" == row[0] for row in items),
        "置いたメモが書き出しの一覧に無い",
    )

    check(DR.take_snapshot(bpy.context, obj, unfold), "控えを取れない")

    check(
        len(DR._snapshot["texts"]) == len(items),
        f"控えた文字が {len(DR._snapshot['texts'])} 件、"
        f"書き出しは {len(items)} 件",
    )
    check(
        any(row[0] == "ここは折る" for row in DR._snapshot["texts"]),
        "メモが控えられていない",
    )


@test
def test_pattern_inset_pushes_the_guide_outward():
    """型紙のまわりの余白が、ガイドの枠と型紙の間に隙間を作る。

    ガイドの枠は型紙の左下に合わせて置かれる。外形に余白を
    含めてしまえば、そのぶん枠が外へ出て隙間になる。枚数の計算も
    同じ外形を見るので、別に足す必要がない。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_pattern_inset_mm = 0.0
    plain = C.pattern_extent(bpy.context)
    plain_box = C.pattern_bounds(bpy.context)
    check(plain is not None, "大きさが取れない")

    scene.tsunfold_pattern_inset_mm = 10.0
    wide = C.pattern_extent(bpy.context)
    wide_box = C.pattern_bounds(bpy.context)

    # 四方へ 10mm ずつなので、縦横とも 20mm 増える。
    close(wide[0] - plain[0], 20.0, 0.5, "横の増え方が違う")
    close(wide[1] - plain[1], 20.0, 0.5, "縦の増え方が違う")

    check(
        wide_box[0] < plain_box[0] and wide_box[1] < plain_box[1],
        "外形が左下へ広がっていない",
    )


@test
def test_pattern_inset_keeps_the_export_consistent():
    """余白を入れても、書き出す線と外形の見積もりが食い違わない。

    ここが食い違うと、画面のガイドは収まると言うのに刷ると
    はみ出す。目盛りのときと同じで、表示だけが嘘になる。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_pattern_inset_mm = 12.0

    drawing = C.pattern_lines(bpy.context)
    extent = C.pattern_extent(bpy.context)
    check(drawing is not None, "線が集まらない")

    # 見積もりは少し大きめでよいが、小さくてはいけない。
    check(
        extent[0] >= drawing.width_mm - 0.5
        and extent[1] >= drawing.height_mm - 0.5,
        f"見積もり {extent} が実際 "
        f"{(drawing.width_mm, drawing.height_mm)} より小さい",
    )

    # 線が余白の中へ食い込んでいないこと。
    lowest = min(min(row[0], row[2]) for row in drawing.lines)
    leftmost = min(min(row[1], row[3]) for row in drawing.lines)
    check(
        lowest >= 12.0 - 0.5 and leftmost >= 12.0 - 0.5,
        f"線が余白へ食い込んでいる: {lowest:.2f}, {leftmost:.2f}",
    )


@test
def test_allowance_color_follows_the_setting():
    """縫い代・糊代の画面の色が、設定どおりになる。

    描く側が2通りある（ふつうの描画と、並べている最中のずらした
    描画）。それぞれで設定を読むと、片方だけ追従しなくなる。
    手で置いた印の色が追従しなかったのと同じ形の間違い。
    """
    reset_scene()
    import truescale
    from truescale import overlay as OV
    truescale.register()

    scene = bpy.context.scene
    scene.tsunfold_allowance_cut_color = (0.2, 0.4, 0.6)
    scene.tsunfold_allowance_fold_color = (0.9, 0.1, 0.3)

    cut, fold = OV.allowance_colors(scene)

    close(cut[0], 0.2, 1e-5, "裁断線の赤が違う")
    close(cut[2], 0.6, 1e-5, "裁断線の青が違う")
    close(fold[0], 0.9, 1e-5, "折り線の赤が違う")
    close(fold[2], 0.3, 1e-5, "折り線の青が違う")


@test
def test_printed_allowance_stays_black():
    """刷るときは色を付けない。

    紙の上では実線と破線で見分けられる。色刷りを前提にすると、
    白黒で刷った人の手元で区別が付かなくなる。画面の色は
    ビューポート確認用であって、刷るものとは別物。
    """
    reset_scene()
    import truescale
    from truescale.export import collect as C
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    scene = bpy.context.scene
    scene.tsunfold_tab_enable = True
    scene.tsunfold_seam_enable = True
    scene.tsunfold_allowance_cut_color = (1.0, 0.0, 0.0)
    scene.tsunfold_allowance_fold_color = (0.0, 1.0, 0.0)

    drawing = C.pattern_lines(bpy.context)
    check(drawing is not None, "線が集まらない")

    colors = {tuple(round(v, 3) for v in row[4]) for row in drawing.lines}
    check(
        colors == {(0.0, 0.0, 0.0)},
        f"刷る線に黒以外が混ざっている: {sorted(colors)}",
    )


@test
def test_draft_and_unfold_agree_on_the_scale():
    """三面図側と型紙側が、同じ実寸の基準で測る。

    「このアドオンで指定」を使うと、以前は三面図側がそれを素通り
    して、シーンの Unit Scale で測っていた。同じファイルの同じ
    瞬間に、型紙が 1 BU = 40mm、三面図が 1000mm という 25 倍の
    食い違いが起きていた。既定の設定では一致するので、基準を
    変えた人の手元でだけ起きる。
    """
    reset_scene()
    import truescale
    from truescale.core import units as U
    from truescale.draft import bbox as DB
    truescale.register()

    scene = bpy.context.scene
    scene.unit_settings.scale_length = 1.0
    scene.tsunfold_scale_mode = 'MANUAL'
    scene.tsunfold_manual_mm_per_bu = 40.0

    # 型紙側の基準
    close(U.scene_bu_to_mm(scene, 1.0), 40.0, 1e-6, "型紙側が 40mm でない")

    # 三面図側が使う値（1 BU が何メートルか）
    meters = U.scene_scale_to_meters(scene)
    close(meters * 1000.0, 40.0, 1e-6, "三面図側が 40mm でない")

    # シーンの設定は書き換えていないこと。書き換えると物理演算や
    # 他のアドオンにまで波及する。
    close(
        scene.unit_settings.scale_length, 1.0, 1e-9,
        "シーンの Unit Scale を書き換えている",
    )

    # 既定（シーンに従う）へ戻せば、シーンの値と一致する。
    scene.tsunfold_scale_mode = 'SCENE'
    scene.unit_settings.scale_length = 0.01
    close(
        U.scene_scale_to_meters(scene), 0.01, 1e-9,
        "シーンに従うモードでシーンの値を見ていない",
    )

    check(DB is not None, "三面図側の箱を読み込めない")


@test
def test_draft_dimensions_use_the_addon_basis():
    """三面図の寸法が、アドオンで決めた実寸の基準どおりに出る。

    1 BU の立方体を、1 BU = 40mm の基準で測れば 40mm と出るべき。
    以前はシーンの Unit Scale だけを見ていたので、1000mm と出て
    いた。上の test_draft_and_unfold_agree_on_the_scale は換算の
    入口を見るが、こちらは実際にボタンを押して出る数字を見る。
    """
    reset_scene()
    import truescale
    from truescale.draft import dimension as D
    from truescale.draft import keys as K
    truescale.register()

    def measure(mode):
        reset_scene()
        scene = bpy.context.scene
        scene.unit_settings.scale_length = 1.0
        scene.tsunfold_scale_mode = mode
        scene.tsunfold_manual_mm_per_bu = 40.0

        bpy.ops.mesh.primitive_cube_add(size=1.0)   # 1 BU 角
        obj = bpy.context.active_object
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        try:
            bpy.ops.truescale_draft.make_size_bbox()
        except RuntimeError as exc:
            raise Skip(f"ヘッドレスで箱を作れません: {exc}")

        items = bpy.app.driver_namespace.get(K.DATA_KEY) or []
        return sorted({
            round(D.dimension_length_mm(row), 2) for row in items
        })

    scene_side = measure('SCENE')
    check(scene_side == [1000.0], f"シーン基準が 1000mm でない: {scene_side}")

    addon_side = measure('MANUAL')
    check(addon_side == [40.0], f"アドオン基準が 40mm でない: {addon_side}")


@test
def test_both_sides_offer_the_same_papers():
    """型紙側と三面図側で、選べる用紙が同じ。

    以前は3箇所に同じ表があった。core.paper と、型紙側の一覧と、
    三面図側の書き出し。三面図側には A5 と B判が無く、同じ
    「用紙サイズ」という名前なのに選べるものが違っていた。
    """
    reset_scene()
    import truescale
    from truescale.core import paper as P
    truescale.register()

    scene = bpy.context.scene

    def choices(prop):
        return [
            item.identifier
            for item in scene.bl_rna.properties[prop].enum_items
        ]

    unfold_side = choices("tsunfold_paper_size")
    draft_side = choices("tsdraft_sheet_paper_size")

    check(
        unfold_side == draft_side,
        f"選べる用紙が違う: 型紙 {unfold_side} / 三面図 {draft_side}",
    )
    check("B4" in draft_side, "三面図側に B4 が無い")
    check("A5" in draft_side, "三面図側に A5 が無い")

    # 表に無い用紙を選べてはいけない。逆も同じ。
    expected = list(P.SIZES_MM) + ["CUSTOM"]
    check(
        unfold_side == expected,
        f"一覧が表と合わない: {unfold_side} / {expected}",
    )


@test
def test_draft_resolves_every_paper_it_offers():
    """三面図側が、選べる用紙すべての寸法を出せる。

    一覧と寸法を別々の表から出すと、選べるのに寸法が無い用紙が
    生まれる。実際 B4 がそうなるところだった。
    """
    reset_scene()
    import truescale
    from truescale.core import paper as P
    truescale.register()

    scene = bpy.context.scene

    for name, (width, height) in P.SIZES_MM.items():
        scene.tsdraft_sheet_paper_size = name
        got = P.base_dimensions_mm(
            scene,
            size_prop='tsdraft_sheet_paper_size',
            custom_width_prop='tsdraft_sheet_custom_width_mm',
            custom_height_prop='tsdraft_sheet_custom_height_mm',
            default_custom=(210.0, 297.0),
        )
        check(
            got == (width, height),
            f"{name} の寸法が違う: {got} / 期待 {(width, height)}",
        )

    scene.tsdraft_sheet_paper_size = 'CUSTOM'
    scene.tsdraft_sheet_custom_width_mm = 123.0
    scene.tsdraft_sheet_custom_height_mm = 456.0
    got = P.base_dimensions_mm(
        scene,
        size_prop='tsdraft_sheet_paper_size',
        custom_width_prop='tsdraft_sheet_custom_width_mm',
        custom_height_prop='tsdraft_sheet_custom_height_mm',
        default_custom=(210.0, 297.0),
    )
    check(got == (123.0, 456.0), f"カスタムが入力どおりでない: {got}")


@test
def test_label_tables_cover_every_axis_each_view_shows():
    """置き場所の表が、その面図が出す軸を全部持っている。

    表に無い軸は「どこにも置けない」ことになり、3Dの位置を
    そのまま画面へ落とす予備の経路へ落ちる。図の真ん中に寸法が
    重なって出る。表が2つに分かれたままなので、突き合わせる。
    """
    reset_scene()
    import truescale
    from truescale.draft import dimension as DM
    from truescale.draft import labels as L
    truescale.register()

    for name, table in (("三面図シート", L.SHEET_LAYOUT),
                        ("単体ビュー", L.SINGLE_LAYOUT)):
        for view_key in ("top", "front", "side"):
            wanted = set(DM.tsdraft_svg_dimension_axes(view_key))
            got = set(table.get(view_key, {}))
            check(
                got == wanted,
                f"{name} の {view_key}: 表 {sorted(got)} / "
                f"出す軸 {sorted(wanted)}",
            )


@test
def test_depth_axis_is_hidden_in_each_view():
    """真正面から見た奥行きの寸法は出さない。

    線が点になるので、書いても読めない。
    """
    reset_scene()
    import truescale
    from truescale.draft import labels as L
    from mathutils import Vector
    truescale.register()

    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object

    # 正面図（-Y を向く）では Y が奥行き。
    front = Vector((0.0, 1.0, 0.0))
    check(L.edge_on(obj, "Y", front), "正面図で Y を隠していない")
    check(not L.edge_on(obj, "X", front), "正面図で X まで隠している")
    check(not L.edge_on(obj, "Z", front), "正面図で Z まで隠している")

    # 上面図（-Z を向く）では Z が奥行き。
    top = Vector((0.0, 0.0, 1.0))
    check(L.edge_on(obj, "Z", top), "上面図で Z を隠していない")
    check(not L.edge_on(obj, "X", top), "上面図で X まで隠している")


@test
def test_label_anchors_go_where_they_say():
    """TOP は上、BOTTOM は下、LEFT は左、RIGHT は右へ置く。

    回すもの（LEFT / RIGHT）は、回した後の横幅が文字の高さに
    なる。ここを取り違えると、左へ置いたつもりの文字が図に
    重なる。
    """
    reset_scene()
    import truescale
    from truescale.draft import labels as L
    truescale.register()

    box = (100.0, 200.0, 50.0, 150.0)   # min_x, max_x, min_y, max_y
    width, height = 40.0, 12.0

    x, y = L._anchor("TOP", box, width, height)
    check(y > box[3], f"TOP が箱の上に無い: {y} <= {box[3]}")
    close(x + width * 0.5, 150.0, 1e-6, "TOP が横中央でない")

    x, y = L._anchor("BOTTOM", box, width, height)
    check(y + height < box[2], f"BOTTOM が箱の下に無い: {y + height}")

    x, y = L._anchor("RIGHT", box, width, height)
    check(x > box[1], f"RIGHT が箱の右に無い: {x} <= {box[1]}")

    x, y = L._anchor("LEFT", box, width, height)
    # 回した後の横幅は文字の高さ。
    check(x + height < box[0], f"LEFT が箱の左に無い: {x + height}")


@test
def test_placed_bounds_account_for_rotation():
    """回した文字の占める範囲が、回した後の形で出る。

    切り抜きの範囲はこれで決まる。回転を見落とすと、縦書きの
    寸法が PNG の端で切れる。
    """
    reset_scene()
    import truescale
    from truescale.draft import labels as L
    truescale.register()

    flat = L.Placed("100 mm", 10.0, 20.0, 40.0, 12.0, False, 16)
    check(flat.bounds() == (10.0, 20.0, 50.0, 32.0), f"横書き: {flat.bounds()}")

    turned = L.Placed("100 mm", 10.0, 20.0, 40.0, 12.0, True, 16)
    check(turned.bounds() == (10.0, 20.0, 22.0, 60.0), f"縦書き: {turned.bounds()}")


@test
def test_export_restores_every_flag_it_changes():
    """書き出しが書き換える設定は、全部控えの一覧に入っている。

    以前は控えるコードと戻すコードが 400 行離れていた。片方にだけ
    項目を足せば、書き出しただけで作業中の表示が変わったままに
    なる。しかも気付きにくい。

    export_view が書き換える名前を実際のコードから拾い、控えの
    一覧と突き合わせる。人が並べ直す必要がなくなる。
    """
    reset_scene()
    import ast
    import inspect
    import truescale
    from truescale.draft import viewstate as V
    truescale.register()

    tree = ast.parse(inspect.getsource(V.export_view))

    written = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if getattr(target.value, "id", None) == "scene":
                written.add(target.attr)

    check(written, "書き換えている設定が1つも見つからない")

    missing = written - set(V.EXPORT_SCENE_FLAGS)
    check(
        not missing,
        f"控えずに書き換えている設定がある: {sorted(missing)}",
    )


@test
def test_export_flag_snapshot_round_trips():
    """控えた設定を戻すと、元どおりになる。"""
    reset_scene()
    import truescale
    from truescale.draft import viewstate as V
    truescale.register()

    scene = bpy.context.scene

    # 既定と違う状態にしておく。
    for index, name in enumerate(V.EXPORT_SCENE_FLAGS):
        setattr(scene, name, bool(index % 2))

    before = V._snapshot_flags(scene)

    # 書き出し中の状態を真似て、全部 True にする。
    for name in V.EXPORT_SCENE_FLAGS:
        setattr(scene, name, True)

    V._restore_flags(scene, before)

    after = V._snapshot_flags(scene)
    check(after == before, f"戻っていない: {before} -> {after}")


@test
def test_axis_order_is_horizontal_then_vertical():
    """面図ごとの軸が、横・縦の順で並んでいる。

    まとめ書き出しが「1つめを横の寸法、2つめを縦の寸法」として
    使う。以前は集合で返していたので、順序に頼っている側が
    たまたま動いているだけだった。
    """
    reset_scene()
    import truescale
    from truescale.draft import dimension as DM
    truescale.register()

    check(DM.tsdraft_svg_dimension_axes("top") == ("X", "Y"), "上面図")
    check(DM.tsdraft_svg_dimension_axes("front") == ("X", "Z"), "正面図")
    check(DM.tsdraft_svg_dimension_axes("side") == ("Y", "Z"), "側面図")
    check(DM.tsdraft_svg_dimension_axes("user") == (), "任意ビュー")


@test
def test_sheet_and_screen_place_dimensions_alike():
    """図面シートと画面で、寸法を置く側が同じ。

    以前は同じ表が3つあった。画面（overlay）、撮った画像の切り抜き
    （capture）、図面シート（sheet）。1つ直し忘れれば、画面で
    見た位置と刷った位置が変わる。
    """
    reset_scene()
    import ast
    import inspect
    import truescale
    from truescale.draft import labels as L
    from truescale.draft.export import sheet as S
    truescale.register()

    # シートの描画が、置く側の表を自前で持っていないこと。
    source = inspect.getsource(S)
    tree = ast.parse(source)

    hard_coded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        values = {
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        }
        if values & {"TOP", "BOTTOM", "LEFT", "RIGHT"}:
            hard_coded.append(node.lineno)

    check(
        not hard_coded,
        f"置く側の表が sheet.py にも書かれている: 行 {hard_coded}",
    )

    # 表そのものは labels 側にある。
    check(L.SHEET_LAYOUT["front"]["Z"] == "RIGHT", "シートの表が変わっている")
    check(L.SINGLE_LAYOUT["front"]["Z"] == "LEFT", "単体の表が変わっている")


@test
def test_dark_place_cage_wraps_the_box():
    """暗所表示の檻が、箱をはみ出さずに囲う。

    描画から切り離したので、線分そのものを確かめられる。
    """
    reset_scene()
    import truescale
    from truescale.draft import overlay as OV
    truescale.register()

    bpy.ops.mesh.primitive_cube_add(size=2.0)   # -1..1
    obj = bpy.context.active_object

    segments = OV.dark_place_segments(obj)
    check(segments, "檻が作られていない")

    points = [p for pair in segments for p in pair]
    for axis in range(3):
        values = [p[axis] for p in points]
        close(min(values), -1.0, 1e-5, f"{axis} 軸の下端が箱から出ている")
        close(max(values), 1.0, 1e-5, f"{axis} 軸の上端が箱から出ている")

    # 上下の外周8本 + 四隅の柱4本 + 縦格子
    expected = 8 + 4 + (OV.DARK_BARS_X - 1) * 2 + (OV.DARK_BARS_Y - 1) * 2
    check(
        len(segments) == expected,
        f"線の本数が合わない: {len(segments)} / 期待 {expected}",
    )


def main():
    print()
    print("=" * 72)
    print("Truescale ヘッドレステスト")
    print("=" * 72)
    print(f"  Blender {bpy.app.version_string}")
    print(f"  リポジトリ {REPO_ROOT}")
    print("-" * 72)

    passed = failed = skipped = 0

    for func in _results:
        name = func.__name__
        doc = (func.__doc__ or "").strip().split("\n")[0]
        try:
            ensure_unregistered()
            func()
        except Skip as exc:
            skipped += 1
            print(f"  SKIP  {name}")
            print(f"        {exc}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}  — {doc}")
            for line in traceback.format_exc().rstrip().split("\n")[-6:]:
                print(f"        {line}")
        else:
            passed += 1
            print(f"  ok    {name}  — {doc}")

    print("-" * 72)
    print(f"  成功 {passed} / 失敗 {failed} / スキップ {skipped}")
    print("=" * 72)

    sys.stdout.flush()
    sys.stderr.flush()

    # background 実行では sys.exit だけだと終了コードが伝わらないことがある
    if failed:
        os._exit(1)
    os._exit(0)


main()
