"""パネルで変更できる設定を全部洗い出し、変更コストを測る。

実行:
    blender --background --factory-startup --python tools/audit_settings.py

各設定について次を調べる。

  コールバック    値を変えたときに走る update コールバックの時間
  キャッシュ破棄  1回の変更で描画キャッシュの epoch がいくつ進むか
                  （進むと島の解析や配置探索が次の再描画で作り直される）
  再描画まで      キャッシュ破棄を含めた、次の描画に必要な実時間
  反映            値を変えたときに描画へ渡る内容が実際に変わるか

「反映」が × のものは、値を変えても画面が変わらない取りこぼし。
「キャッシュ破棄」が 0 でないものは、変更のたびに重い再計算を招く。
"""

import sys
import time
from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPEAT = 20


# ============================================================
# 準備
# ============================================================

def build_scene():
    """シームを入れた立方体から型紙を作る。"""
    bpy.ops.wm.read_factory_settings(use_empty=True)

    import truescale
    try:
        truescale.unregister()
    except Exception:
        pass
    truescale.register()

    mesh = bpy.data.meshes.new("Cube_Mesh")
    h = 1.0
    mesh.from_pydata(
        [(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
         (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)],
        [],
        [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
         (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
    )
    mesh.update()
    obj = bpy.data.objects.new("Cube", mesh)
    bpy.context.collection.objects.link(obj)

    for edge in mesh.edges:
        a = mesh.vertices[edge.vertices[0]].co
        b = mesh.vertices[edge.vertices[1]].co
        if abs(a.z - b.z) > 1e-6 or (a.z > 0 and b.z > 0):
            edge.use_seam = True

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.truescale_unfold.build_pattern()

    unfold = None
    for candidate in bpy.data.objects:
        if candidate.get("tsunfold_generated"):
            unfold = candidate

    return obj, unfold


def panel_properties():
    """パネルが prop() で出している Scene プロパティ名を集める。"""
    import ast

    names = []
    seen = set()
    for sub in ("unfold", "draft"):
        path = REPO_ROOT / "truescale" / sub / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "prop"):
                continue
            if len(node.args) < 2:
                continue
            target, name_node = node.args[0], node.args[1]
            if not isinstance(name_node, ast.Constant):
                continue
            if not isinstance(name_node.value, str):
                continue
            # scene を対象にしたものだけ
            is_scene = (
                (isinstance(target, ast.Name) and target.id == "scene")
                or (isinstance(target, ast.Attribute) and target.attr == "scene")
            )
            if not is_scene:
                continue
            name = name_node.value
            if name not in seen:
                seen.add(name)
                names.append((sub, name))
    return names


def alternate_value(scene, name):
    """その設定を「別の値」に変えるための値を作る。"""
    try:
        prop = scene.bl_rna.properties[name]
    except Exception:
        return None

    current = getattr(scene, name)
    kind = prop.type

    if kind == 'BOOLEAN':
        return not current
    if kind == 'INT':
        upper = getattr(prop, "hard_max", 2 ** 31)
        return current + 1 if current + 1 <= upper else current - 1
    if kind == 'FLOAT':
        if getattr(prop, "array_length", 0):
            return tuple(
                min(1.0, max(0.0, v + 0.31)) % 1.0 for v in current
            )
        upper = getattr(prop, "hard_max", 1e30)
        candidate = current * 1.17 + 0.13
        return candidate if candidate <= upper else current * 0.5
    if kind == 'ENUM':
        items = [item.identifier for item in prop.enum_items]
        for item in items:
            if item != current:
                return item
        return None
    if kind == 'STRING':
        return (current or "") + "x"
    return None


# ============================================================
# 計測
# ============================================================

def snapshot(context, source, unfold):
    """描画へ渡る内容をまとめて文字列にする。"""
    from truescale import unfold as U

    try:
        segments = U._pattern_flat_colored_segments(context, source, unfold)
        texts = U._pattern_auto_flat_oriented_text_items(context, source, unfold)
    except Exception as exc:
        return f"error:{exc}"

    return repr([
        [
            (round(r[0].x, 5), round(r[0].y, 5),
             round(r[1].x, 5), round(r[1].y, 5),
             tuple(round(c, 5) for c in r[2]), round(r[3], 5))
            for r in segments
        ],
        [
            (r[0], round(r[2], 5), tuple(round(c, 5) for c in r[3]))
            for r in texts
        ],
    ])


def measure(name, source, unfold):
    from truescale import unfold as U

    context = bpy.context
    scene = context.scene

    value = alternate_value(scene, name)
    if value is None:
        return None

    original = getattr(scene, name)

    # 反映されるか
    before_draw = snapshot(context, source, unfold)
    try:
        setattr(scene, name, value)
    except Exception as exc:
        return {"name": name, "error": str(exc)}
    after_draw = snapshot(context, source, unfold)
    responds = before_draw != after_draw

    # コールバックの時間と epoch の進み方
    epoch_before = U._pattern_cache_epoch
    start = time.perf_counter()
    for index in range(REPEAT):
        try:
            setattr(scene, name, alternate_value(scene, name))
        except Exception:
            break
    callback_ms = (time.perf_counter() - start) / REPEAT * 1000.0
    epoch_delta = (U._pattern_cache_epoch - epoch_before) / REPEAT

    # キャッシュ破棄込みで、次の描画に必要な時間
    start = time.perf_counter()
    for index in range(REPEAT):
        setattr(scene, name, alternate_value(scene, name))
        snapshot(context, source, unfold)
    total_ms = (time.perf_counter() - start) / REPEAT * 1000.0

    try:
        setattr(scene, name, original)
    except Exception:
        pass

    return {
        "name": name,
        "callback_ms": callback_ms,
        "epoch": epoch_delta,
        "total_ms": total_ms,
        "responds": responds,
    }


def main():
    source, unfold = build_scene()
    if unfold is None:
        print("型紙を作れませんでした")
        return

    rows = []
    skipped = []
    errors = []

    for sub, name in panel_properties():
        if not hasattr(bpy.context.scene, name):
            skipped.append((name, "未登録"))
            continue
        result = measure(name, source, unfold)
        if result is None:
            skipped.append((name, "値を変えられない"))
            continue
        if "error" in result:
            errors.append((name, result["error"]))
            continue
        result["sub"] = sub
        rows.append(result)

    rows.sort(key=lambda r: r["total_ms"], reverse=True)

    print()
    print("=" * 94)
    print("設定変更のコスト監査")
    print("=" * 94)
    print(f"  各設定を {REPEAT} 回変更した平均。立方体の型紙（6面）で計測。")
    print()
    print(f"  {'設定':<38}{'反映':>5}{'破棄':>6}{'CB ms':>9}{'描画込 ms':>11}")
    print("  " + "-" * 90)

    for row in rows:
        mark = "○" if row["responds"] else "×"
        print(
            f"  {row['name']:<38}{mark:>5}"
            f"{row['epoch']:>6.1f}{row['callback_ms']:>9.3f}{row['total_ms']:>11.3f}"
        )

    print()
    stale = [r["name"] for r in rows if not r["responds"]]
    invalidating = [r["name"] for r in rows if r["epoch"] > 0]
    heavy = [r["name"] for r in rows if r["total_ms"] > 5.0]

    print(f"  反映されない設定 : {len(stale)} 件")
    for name in stale:
        print(f"      {name}")
    print(f"  キャッシュを捨てる設定 : {len(invalidating)} 件")
    for name in invalidating:
        print(f"      {name}")
    print(f"  描画込みで5ms超 : {len(heavy)} 件")
    for name in heavy:
        print(f"      {name}")

    if skipped:
        print()
        print(f"  計測対象外 : {len(skipped)} 件")
        for name, why in skipped:
            print(f"      {name}  ({why})")
    if errors:
        print()
        print(f"  エラー : {len(errors)} 件")
        for name, why in errors:
            print(f"      {name}  {why}")

    print("=" * 94)


main()
