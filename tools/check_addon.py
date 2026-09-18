#!/usr/bin/env python3
"""Blenderアドオンの静的整合性チェック。

Blenderを起動せずに、以下のズレを検出する。

  1. パネルが参照しているが実装されていないオペレータ
  2. 実装されているがどこからも呼ばれていないオペレータ
  3. 定義されているが classes タプルに登録されていないクラス
  4. 参照されているが register されていない Scene プロパティ
  5. register されているが unregister で消されない Scene プロパティ
  6. register されているがどこからも参照されていない Scene プロパティ
  7. どこからも呼ばれていないモジュール直下の関数

使い方:
    python tools/check_addon.py truescale/unfold/__init__.py
    python tools/check_addon.py truescale/unfold/__init__.py truescale/draft/__init__.py

終了コード: 問題が1件でもあれば 1、なければ 0。

既知の限界:
  7番の判定は1段階のみで、推移的ではない。死にコードから呼ばれている
  関数は「使われている」と見なされる。到達可能性の解析ではない。
"""

import ast
import re
import sys
from pathlib import Path

# "namespace.operator_name" 形式の文字列を判定する
OPERATOR_IDNAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# Blender が bpy.types に登録するクラスの基底名
REGISTRABLE_BASES = (
    "Operator",
    "Panel",
    "Menu",
    "PropertyGroup",
    "AddonPreferences",
    "UIList",
    "Header",
)


def _joinedstr_shape(node: ast.JoinedStr):
    """f-string から固定部分だけを取り出す。

    f"mhs_{view}_{axis}_offset_x" -> ("mhs_", "_offset_x")
    前方一致・後方一致でプロパティ名を照合するために使う。
    """
    parts = [
        v.value for v in node.values
        if isinstance(v, ast.Constant) and isinstance(v.value, str)
    ]
    head = parts[0] if parts else ""
    tail = parts[-1] if len(parts) > 1 else ""
    return (head, tail)


class AddonAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.dynamic_prop_patterns = set()  # (前方一致, 後方一致)
        self.module_functions = {}   # モジュール直下の関数名 -> 行番号
        self.name_loads = set()      # 読み取られた名前（関数が使われたか判定用）
        self._class_stack = []
        self.classes = {}            # クラス名 -> {"bl_idname": str|None, "bases": list}
        self.registered_classes = [] # classes タプルに並んだクラス名
        self.referenced_operators = {}  # idname -> [行番号]
        self.called_operators = {}      # idname -> [行番号]  (bpy.ops 経由)
        self.registered_props = {}      # プロパティ名 -> 行番号
        self.unregistered_props = set() # unregister 内で言及される名前
        self.referenced_props = {}      # プロパティ名 -> [行番号]
        self._func_stack = []

    # --- クラス定義 ---

    def visit_ClassDef(self, node):
        # 多重継承（例: Operator, ExportHelper）があるので全ての基底を集める
        bases = []
        for b in node.bases:
            if isinstance(b, ast.Attribute):
                bases.append(b.attr)
            elif isinstance(b, ast.Name):
                bases.append(b.id)

        bl_idname = None
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if (isinstance(target, ast.Name)
                            and target.id == "bl_idname"
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)):
                        bl_idname = stmt.value.value

        self.classes[node.name] = {"bl_idname": bl_idname, "bases": bases}
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    # --- 代入 ---

    def visit_Assign(self, node):
        for target in node.targets:
            # classes = (...)
            if (isinstance(target, ast.Name)
                    and target.id == "classes"
                    and isinstance(node.value, (ast.Tuple, ast.List))):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Name):
                        self.registered_classes.append(elt.id)

            # bpy.types.Scene.<name> = ...
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "Scene"):
                self.registered_props[target.attr] = node.lineno

        self.generic_visit(node)

    # --- 関数（unregister の中身を拾うため / 呼び出し関係の記録） ---

    def visit_FunctionDef(self, node):
        if len(self._func_stack) == 0 and len(self._class_stack) == 0:
            # モジュール直下の関数だけを対象にする
            self.module_functions[node.name] = node.lineno
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    # --- 名前の参照（関数が使われているかの判定） ---

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.name_loads.add(node.id)
        self.generic_visit(node)

    # --- 呼び出し ---

    def visit_Call(self, node):
        func = node.func

        # layout.operator("ns.name", ...) / row.operator(...)
        if isinstance(func, ast.Attribute) and func.attr in ("operator", "operator_menu_enum"):
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and OPERATOR_IDNAME.match(value):
                    self.referenced_operators.setdefault(value, []).append(node.lineno)

        # bpy.ops.<ns>.<name>(...)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            inner = func.value
            if (isinstance(inner.value, ast.Attribute)
                    and inner.value.attr == "ops"):
                idname = f"{inner.attr}.{func.attr}"
                self.called_operators.setdefault(idname, []).append(node.lineno)

        # getattr(scene, "prop", default) / setattr(scene, "prop", value)
        if (isinstance(func, ast.Name)
                and func.id in ("getattr", "hasattr", "setattr")
                and len(node.args) >= 2):
            name_arg = node.args[1]
            if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
                self.referenced_props.setdefault(name_arg.value, []).append(node.lineno)
            elif isinstance(name_arg, ast.JoinedStr):
                # f"mhs_{view}_offset_x" のような動的な名前。
                # 前後の固定部分を取り出し、後でパターン照合に使う。
                self.dynamic_prop_patterns.add(_joinedstr_shape(name_arg))

        self.generic_visit(node)

    # --- 属性アクセス ---

    def visit_Attribute(self, node):
        # scene.<prop> / context.scene.<prop>
        base = node.value
        is_scene = (
            (isinstance(base, ast.Name) and base.id == "scene")
            or (isinstance(base, ast.Attribute) and base.attr == "scene")
        )
        if is_scene:
            self.referenced_props.setdefault(node.attr, []).append(node.lineno)
        self.generic_visit(node)

    # --- 文字列定数（unregister 内のプロパティ名リスト用） ---

    def visit_Constant(self, node):
        # unregister 本体だけでなく、そこから呼ばれる _unregister_* も見る。
        # 削除対象を接頭辞で指定する実装があるため。
        if isinstance(node.value, str) and self._func_stack:
            current = self._func_stack[-1]
            if current == "unregister" or current.lstrip("_").startswith("unregister"):
                self.unregistered_props.add(node.value)
        self.generic_visit(node)


def analyze(path: Path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    analyzer = AddonAnalyzer()
    analyzer.visit(tree)

    defined_operators = {
        info["bl_idname"]: name
        for name, info in analyzer.classes.items()
        if info["bl_idname"] and "Operator" in info["bases"]
    }

    registrable = {
        name: info
        for name, info in analyzer.classes.items()
        if any(b in REGISTRABLE_BASES for b in info["bases"])
    }

    # アドオン固有のプロパティだけを対象にするため、接頭辞を推定する
    prefixes = set()
    for prop in analyzer.registered_props:
        head = prop.split("_")[0]
        if head:
            prefixes.add(head)

    def is_addon_prop(name):
        return any(name.startswith(p + "_") for p in prefixes)

    problems = []

    # 1. 参照されているが実装されていないオペレータ
    missing = {
        idname: lines
        for idname, lines in analyzer.referenced_operators.items()
        if idname not in defined_operators
    }
    # bpy.ops 経由の呼び出しも対象にする（Blender標準のものは除く）
    known_namespaces = {idname.split(".")[0] for idname in defined_operators}
    for idname, lines in analyzer.called_operators.items():
        namespace = idname.split(".")[0]
        if namespace in known_namespaces and idname not in defined_operators:
            missing.setdefault(idname, []).extend(lines)

    if missing:
        problems.append(("実装されていないオペレータを参照している", [
            f"{idname}  (行 {', '.join(str(n) for n in sorted(set(lines)))})"
            for idname, lines in sorted(missing.items())
        ]))

    # 2. 実装されているが呼ばれていないオペレータ
    used = set(analyzer.referenced_operators) | set(analyzer.called_operators)
    unused = sorted(
        f"{idname}  ({defined_operators[idname]})"
        for idname in defined_operators
        if idname not in used
    )
    if unused:
        problems.append(("実装されているがどこからも呼ばれていないオペレータ", unused))

    # 3. 登録されていないクラス
    unregistered = sorted(
        f"{name}  ({', '.join(info['bases'])})"
        for name, info in registrable.items()
        if name not in analyzer.registered_classes
    )
    if unregistered:
        problems.append(("classes に登録されていないクラス", unregistered))

    # 4. 参照されているが register されていないプロパティ
    undeclared = sorted(
        f"{name}  (行 {', '.join(str(n) for n in sorted(set(lines))[:5])})"
        for name, lines in analyzer.referenced_props.items()
        if is_addon_prop(name) and name not in analyzer.registered_props
    )
    if undeclared:
        problems.append(("register されていない Scene プロパティを参照している", undeclared))

    # 5. unregister で消されないプロパティ
    #    接頭辞をまとめて消す実装（delattr を prefix で回すもの）にも対応する
    def deleted_by_prefix(name):
        return any(
            s.endswith("_") and name.startswith(s) and name != s
            for s in analyzer.unregistered_props
        )

    leaked = sorted(
        name for name in analyzer.registered_props
        if name not in analyzer.unregistered_props and not deleted_by_prefix(name)
    )
    if leaked:
        problems.append(("unregister で削除されない Scene プロパティ", leaked))

    # 6. 参照されていないプロパティ
    #    f-string で動的に組み立てられる名前は前方／後方一致で救済する
    def matched_dynamically(name):
        return any(
            name.startswith(head) and name.endswith(tail)
            for head, tail in analyzer.dynamic_prop_patterns
        )

    orphan = sorted(
        name for name in analyzer.registered_props
        if name not in analyzer.referenced_props and not matched_dynamically(name)
    )
    if orphan:
        problems.append(("register されているがどこからも参照されていないプロパティ", orphan))

    # 7. どこからも呼ばれていないモジュール直下の関数
    #    分割リファクタリング時に、死にコードと一緒に落とせる候補を洗い出す。
    #    register / unregister は Blender が直接呼ぶので除外する。
    ENTRY_POINTS = {"register", "unregister"}
    dead_functions = sorted(
        f"{name}  (行 {lineno})"
        for name, lineno in analyzer.module_functions.items()
        if name not in analyzer.name_loads and name not in ENTRY_POINTS
    )
    if dead_functions:
        problems.append(
            ("どこからも呼ばれていないモジュール直下の関数", dead_functions)
        )

    stats = {
        "行数": len(source.splitlines()),
        "クラス": len(analyzer.classes),
        "登録対象クラス": len(registrable),
        "オペレータ": len(defined_operators),
        "Sceneプロパティ": len(analyzer.registered_props),
    }
    return stats, problems


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2

    exit_code = 0
    for arg in argv[1:]:
        path = Path(arg)
        print("=" * 72)
        print(path)
        print("=" * 72)

        if not path.exists():
            print("  ファイルが見つかりません")
            exit_code = 1
            continue

        stats, problems = analyze(path)
        print("  " + " / ".join(f"{k}: {v}" for k, v in stats.items()))
        print()

        if not problems:
            print("  問題は見つかりませんでした")
        for title, items in problems:
            print(f"  [{len(items)}] {title}")
            for item in items:
                print(f"        {item}")
            print()
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
