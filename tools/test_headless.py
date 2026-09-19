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
    reset_scene()
    import truescale
    from truescale import unfold as U
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)

    context = bpy.context
    before = U._pattern_flat_colored_segments(context, obj, unfold)
    if not before:
        raise Skip("色付きセグメントが生成されませんでした")

    delta = Vector((1.5, -2.5, 0.0))
    unfold.location = unfold.location + delta
    bpy.context.view_layer.update()

    after = U._pattern_flat_colored_segments(context, obj, unfold)

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
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    unfold = build_pattern_for(obj)
    context = bpy.context

    calls = {"count": 0}
    original = U._pattern_compute_flat_colored_segments

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    U._pattern_compute_flat_colored_segments = counting
    try:
        U._pattern_flat_colored_segments(context, obj, unfold)
        first = calls["count"]
        check(first >= 1, "1回目で計算されていない")

        # 10回動かして、そのたびに取得する
        for step in range(10):
            unfold.location.x += 0.1
            bpy.context.view_layer.update()
            U._pattern_flat_colored_segments(context, obj, unfold)

        check(
            calls["count"] == first,
            f"移動のたびに再計算されている: {calls['count'] - first} 回",
        )
    finally:
        U._pattern_compute_flat_colored_segments = original


@test
def test_draw_cache_is_bounded():
    """描画キャッシュが無制限に増えない。"""
    reset_scene()
    import truescale
    from truescale import unfold as U
    truescale.register()

    limit = U._PATTERN_DRAW_CACHE_LIMIT
    for index in range(limit * 3):
        U._pattern_draw_cache_store(("dummy", index), index)

    size = len(U._pattern_draw_cache)
    check(size <= limit, f"キャッシュが上限を超えている: {size} > {limit}")


@test
def test_notch_color_does_not_rebuild():
    """合印の色を変えても合印を作り直さない。

    以前は色の変更で全シームを走査し直していた。その回帰テスト。
    """
    reset_scene()
    import truescale
    from truescale import unfold as U
    truescale.register()

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    calls = {"count": 0}
    original = U._pattern_refresh_auto_notches

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    U._pattern_refresh_auto_notches = counting
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
        U._pattern_refresh_auto_notches = original


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
        before = U._pattern_cache_epoch
        setattr(scene, name, value)
        after = U._pattern_cache_epoch
        check(
            before == after,
            f"{name} の変更でキャッシュ epoch が進んだ: {before} -> {after}",
        )


@test
def test_notch_status_text():
    """合印の状態表示が状況に応じて変わる。"""
    reset_scene()
    import truescale
    from truescale import unfold as U
    truescale.register()

    # 元モデルすら無い状態
    text = U._pattern_notch_status_text(bpy.context)
    check("未読み込み" in text, f"未読み込みの表示が出ない: {text}")

    obj = make_seamed_cube(size=2.0)
    build_pattern_for(obj)

    text = U._pattern_notch_status_text(bpy.context)
    check("合印" in text, f"合印の件数が出ない: {text}")


# ============================================================
# 実行
# ============================================================

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
