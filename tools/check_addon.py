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
  8. 未定義のまま読まれている名前（実行時に NameError になる）
  9. ローカル変数が同名の関数を隠している（UnboundLocalError）
 10. @persistent が付いていないハンドラ
 11. return などの後ろに置かれて実行されないコード
 12. くだけた言い回し（利用者が読む文章に混ざったもの）
 13. 用語の揺れ（縮率 → 縮尺 など）
 14. ミリの値に unit='LENGTH'（画面には「8 m」と出る）

使い方:
    python tools/check_addon.py truescale/unfold/__init__.py
    python tools/check_addon.py truescale/unfold/__init__.py truescale/draft/__init__.py

終了コード: 問題が1件でもあれば 1、なければ 0。

既知の限界:
  7番の判定は1段階のみで、推移的ではない。死にコードから呼ばれている
  関数は「使われている」と見なされる。到達可能性の解析ではない。

  7番は truescale パッケージの中しか見ない。tools/ のテストだけが
  使っている関数は「未使用」と出る。実際にそれを信じて消し、
  テストを壊したことがある。消す前に tools/ も見ること。

  8番は名前だけを見て、属性は見ない。moduleA.name のように参照した
  先が消えても気付けない。そちらは読み込みテストで拾う。
"""

import ast
import builtins
import symtable
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


def _looks_like_prop_name(text):
    """アドオンのプロパティ名らしい文字列か。

    接頭辞と、識別子として成り立つことだけを見る。厳しくすると
    拾い漏れ、緩くすると無関係な文字列まで数える。
    """
    return (
        text.startswith(("tsunfold_", "tsdraft_"))
        and text.isidentifier()
    )


class AddonAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.string_constants = {}   # 定数名 -> 文字列（定数経由の参照を追うため）
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
        self.literal_props = set()      # 文字列で書かれたプロパティ名
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
            #
            # 分割後は classes = ops.classes + (Panel,) のように
            # 足し算で組み立てる。タプルだけを見ていると、足された
            # 側のクラスが「登録されていない」と誤って出る。
            if isinstance(target, ast.Name) and target.id == "classes":
                for sub in ast.walk(node.value):
                    if isinstance(sub, (ast.Tuple, ast.List)):
                        for elt in sub.elts:
                            # モジュール経由（prefs.Foo）でも並べられる
                            if isinstance(elt, ast.Name):
                                self.registered_classes.append(elt.id)
                            elif isinstance(elt, ast.Attribute):
                                self.registered_classes.append(elt.attr)

            # モジュール直下の NAME = "文字列"
            if (isinstance(target, ast.Name)
                    and not self._func_stack
                    and not self._class_stack
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                self.string_constants[target.id] = node.value.value

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
            elif isinstance(name_arg, ast.Name):
                # getattr(scene, PROP) のように定数を経由する形。
                # 解決できなければ何もしない（見逃す側に倒す）。
                resolved = self.string_constants.get(name_arg.id)
                if resolved:
                    self.referenced_props.setdefault(resolved, []).append(
                        node.lineno
                    )
            elif isinstance(name_arg, ast.JoinedStr):
                # f"mhs_{view}_offset_x" のような動的な名前。
                # 前後の固定部分を取り出し、後でパターン照合に使う。
                self.dynamic_prop_patterns.add(_joinedstr_shape(name_arg))

        self.generic_visit(node)

    # --- 属性アクセス ---

    def visit_Attribute(self, node):
        # 他モジュール越しの関数参照も「使っている」と数える。
        # 分割後は _host()._pattern_x() のような呼び方が出るため。
        self.name_loads.add(node.attr)

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

        # プロパティ名そのものが書かれていれば、使われていると見なす。
        #
        # scene.<名前> でも prop(scene, "名前") でもない渡し方がある。
        # パネルの折りたたみは名前を関数の引数にしており、その先で
        # getattr と prop に使う。形を追うと切りが無いので、名前が
        # 書いてあること自体を参照として数える。
        if isinstance(node.value, str) and _looks_like_prop_name(node.value):
            self.literal_props.add(node.value)

        self.generic_visit(node)


def undefined_names(path):
    """そのファイルで未定義のまま読まれている名前を返す。

    分割で関数を別ファイルへ移すとき、呼び出しは書き換えても
    モジュール直下の変数への参照を取り残すことがある。描画側は
    例外を握り潰すので、動かしても気付けない。

    symtable が各スコープの「グローバルとして読まれている名前」を
    返すので、モジュール直下にも組み込みにも無いものを拾う。
    """
    source = path.read_text(encoding="utf-8")
    try:
        table = symtable.symtable(source, str(path), "exec")
    except SyntaxError:
        return []

    defined = set(dir(builtins))
    for sym in table.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_parameter():
            defined.add(sym.get_name())
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defined.add(node.name)

    # symtable は行番号を持たないので、関数ごとに AST から引く。
    # 名前だけでは探す手間が変わらないため、どの関数の中かも出す。
    tree = ast.parse(source)
    scope_lines = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            seen = {}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    seen.setdefault(sub.id, sub.lineno)
            scope_lines[(node.name, node.lineno)] = seen

    found = set()

    def lookup(scope_name, name):
        for (n, _), seen in scope_lines.items():
            if n == scope_name and name in seen:
                return seen[name]
        return 0

    def walk(scope):
        for sym in scope.get_symbols():
            name = sym.get_name()
            if name in defined:
                continue
            if sym.is_global() and sym.is_referenced():
                where = scope.get_name()
                if where == "top":
                    found.add((name, 0, ""))
                else:
                    found.add((name, lookup(where, name), where))
        for child in scope.get_children():
            walk(child)

    walk(table)
    return sorted(
        f"{name}  (行 {line} / {where})" if where else f"{name}"
        for name, line, where in found
    )


def shadowed_functions(path):
    """モジュール直下の関数を、ローカル変数が隠している箇所を返す。

    分割で関数名を短くしたとき、呼び出し側に同じ名前のローカル変数が
    あると、Python はその関数の中の名前を全てローカル扱いにする。
    代入の右辺で読んだ時点で UnboundLocalError になる。

        def variants(scene): ...

        def apply(context):
            variants = variants(context.scene)   # ここで落ちる

    呼び出しがある場合だけ報告する。同名のローカル変数があるだけなら
    動くので、そこまで挙げると多すぎて読まれなくなる。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    module_functions = {
        n.name for n in tree.body if isinstance(n, ast.FunctionDef)
    }
    found = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        assigned = {
            sub.id
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)
        }
        for name in sorted((assigned & module_functions) - {node.name}):
            call = next(
                (
                    sub
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == name
                ),
                None,
            )
            if call is not None:
                found.append(
                    f"{name}  (行 {call.lineno} / {node.name} のローカル変数が隠している)"
                )

    return found


def handlers_without_persistent(path):
    """bpy.app.handlers へ足している関数のうち、@persistent が無いもの。

    関数を別ファイルへ移すとき、装飾子の行を含め忘れると移動先で
    装飾子が外れる。構文としては通るので気付けない。@persistent が
    無いハンドラは .blend を開いた時点で Blender が一覧から外すため、
    「保存して開き直すと効かない」という形でしか現れない。

    load_post だけは例外にしない。開いた直後の初期化そのものなので、
    外れると最初の1回が走らない。
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except SyntaxError:
        return []

    decorated = set()
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
            for dec in node.decorator_list:
                name = getattr(dec, "id", None) or getattr(dec, "attr", None)
                if name == "persistent":
                    decorated.add(node.name)

    appended = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_node = node.func
        if not (isinstance(func_node, ast.Attribute)
                and func_node.attr in ("append", "insert")):
            continue
        if "handlers" not in ast.dump(func_node.value):
            continue
        for arg in node.args:
            name = getattr(arg, "id", None) or getattr(arg, "attr", None)
            if name:
                appended.setdefault(name, node.lineno)

    return sorted(
        f"{name}  (行 {lineno})"
        for name, lineno in appended.items()
        if name in defined and name not in decorated
    )


def unreachable_code(path):
    """return などの後ろに置かれて、決して実行されない文を返す。

    コードを挿し込む位置を誤ると起きる。構文としては正しいので、
    実行しても例外が出ない。静かに効かなくなるだけ。

    実際、文字の回転を戻す処理が return の後ろへ回り、Blender 本体の
    UIまで傾いたことがある。0番フォントの状態は共有なので、戻し
    忘れると画面全体に及ぶ。

    同じブロックの中だけを見る。if/else の分岐をまたいだ到達性は
    追わない（そこまで見ると誤検出が増える）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    found = []
    stoppers = (ast.Return, ast.Raise, ast.Continue, ast.Break)

    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue

            for index, statement in enumerate(block[:-1]):
                if not isinstance(statement, stoppers):
                    continue

                after = block[index + 1]
                kind = type(statement).__name__.lower()
                found.append(
                    f"行 {after.lineno} は {kind}（行 {statement.lineno}）"
                    f"の後ろで、実行されない"
                )
                break

    return sorted(set(found))


# 利用者に見せる文章に混ざってはいけない言い回し。
#
# 元の作者が入れたものが33箇所あった。エラーが出た人が読む文章に
# 「〜してクレメンス」「〜んかったンゴ」が並んでいた。一度直しても、
# 書き足すときに混ざる。
CASUAL_WORDS = (
    "クレメンス",
    "ンゴ",
    "ですぞ",
    "でござ",
    "なんかずっと",
    "お遊び",
    "スクショ",
    "めっちゃ",
    "やばい",
)


# 図面で使う語に揃えるもの。くだけているのではなく、用語が違う。
#
#   縮率 … 設定の中身は 1:N の N なので、縮尺が正しい
WRONG_TERMS = {
    "縮率": "縮尺",
}


def millimetre_props_with_length_unit(path):
    """ミリの値なのに unit='LENGTH' を付けているプロパティを返す。

    unit='LENGTH' を付けると、Blender は中身をシーンの長さ単位
    （既定はメートル）として表示する。ミリとして使っている値に
    付けると、8 が「8 m」と出る。

    実際、用紙の余白と重ねしろがそうなっていた。画面には「1 m」と
    出ているのに、中身は 1mm として使われる。目盛りのときと同じで、
    表示だけが嘘になる型の間違いは、使う人が気付けない。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    found = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        unit = None
        for keyword in node.keywords:
            if keyword.arg == "unit" and isinstance(keyword.value, ast.Constant):
                unit = keyword.value.value
        if unit != "LENGTH":
            continue

        # 代入先の名前に _mm が付いていれば、ミリの値である。
        parent = None
        for owner in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                for statement in getattr(owner, field, []) or []:
                    if not isinstance(statement, ast.Assign):
                        continue
                    if statement.value is node:
                        parent = statement
        if parent is None:
            continue

        names = []
        for target in parent.targets:
            names.append(
                target.attr if isinstance(target, ast.Attribute)
                else getattr(target, "id", "")
            )

        if any(name.endswith("_mm") for name in names):
            found.append(
                f"行 {node.lineno}: {names[0]} はミリの値だが "
                f"unit='LENGTH' が付いている（画面に「8 m」と出る）"
            )

    return sorted(set(found))


def wrong_terms(path):
    """使うべきでない用語を含む行を返す。"""
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return []

    found = []
    for number, line in enumerate(source.split(chr(10)), 1):
        for wrong, right in WRONG_TERMS.items():
            if wrong in line:
                found.append(
                    f"行 {number}: 「{wrong}」は「{right}」へ  "
                    f"{line.strip()[:50]}"
                )
                break
    return found


def casual_wording(path):
    """くだけた言い回しを含む文字列とコメントを返す。"""
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return []

    found = []
    for number, line in enumerate(source.split(chr(10)), 1):
        stripped = line.strip()
        for word in CASUAL_WORDS:
            if word in line:
                found.append(f"行 {number}: {stripped[:60]}  ← {word}")
                break
    return found


def collect_references(paths):
    """複数ファイルから、名前とプロパティの参照だけを集める。

    分割後は、あるファイルで register したプロパティを
    別のファイルが参照する。1ファイルだけ見ていると誤検出する。

    パネルを別ファイルへ出すと、オペレータの呼び出し元
    （layout.operator("ns.name")）もそちらへ移る。これも集めないと
    「実装されているが呼ばれていない」が全件誤検出になる。
    """
    props = set()
    patterns = set()
    names = set()
    operators = set()
    defined = set()
    registered = set()
    literals = set()

    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        analyzer = AddonAnalyzer()
        analyzer.visit(tree)
        props.update(analyzer.referenced_props)
        patterns.update(analyzer.dynamic_prop_patterns)
        names.update(analyzer.name_loads)
        operators.update(analyzer.referenced_operators)
        operators.update(analyzer.called_operators)
        defined.update(
            info["bl_idname"]
            for info in analyzer.classes.values()
            if info["bl_idname"] and "Operator" in info["bases"]
        )
        registered.update(analyzer.registered_classes)
        literals.update(analyzer.literal_props)

    return props, patterns, names, operators, defined, registered, literals


def analyze(path: Path, extra=None):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    analyzer = AddonAnalyzer()
    analyzer.visit(tree)

    # 他のファイルからの参照も合わせる
    if extra is not None:
        extra_props, extra_patterns, extra_names, extra_ops, _, _, _ = extra
        for name in extra_props:
            analyzer.referenced_props.setdefault(name, [])
        analyzer.dynamic_prop_patterns.update(extra_patterns)
        analyzer.name_loads.update(extra_names)

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

    # 登録は別のファイルで行うことがある（パネルと登録本体のように）。
    # 文字列で名前が書かれていれば「使われている」と見なす。
    # ただし Scene プロパティである証拠にはならないので、
    # 「参照しているのに未登録」の判定には混ぜない。
    known_literals = set(analyzer.literal_props)
    if extra is not None:
        known_literals |= extra[6]

    registered_anywhere = set(analyzer.registered_classes)
    if extra is not None:
        registered_anywhere |= extra[5]

    problems = []

    # 1. 参照されているが実装されていないオペレータ
    #
    # 実装は隣のファイルにあることがある（パネルと ops/ のように）。
    # 参照と同じく、実装もパッケージ全体から集めて判定する。
    implemented = set(defined_operators)
    if extra is not None:
        implemented |= extra[4]

    missing = {
        idname: lines
        for idname, lines in analyzer.referenced_operators.items()
        if idname not in implemented
    }
    # bpy.ops 経由の呼び出しも対象にする（Blender標準のものは除く）
    known_namespaces = {idname.split(".")[0] for idname in implemented}
    for idname, lines in analyzer.called_operators.items():
        namespace = idname.split(".")[0]
        if namespace in known_namespaces and idname not in implemented:
            missing.setdefault(idname, []).extend(lines)

    if missing:
        problems.append(("実装されていないオペレータを参照している", [
            f"{idname}  (行 {', '.join(str(n) for n in sorted(set(lines)))})"
            for idname, lines in sorted(missing.items())
        ]))

    # 2. 実装されているが呼ばれていないオペレータ
    # 呼び出し元は他のファイル（パネルなど）にもある。
    used = set(analyzer.referenced_operators) | set(analyzer.called_operators)
    if extra is not None:
        used |= extra[3]
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
        if name not in registered_anywhere
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
        if name not in analyzer.referenced_props
        and name not in known_literals
        and not matched_dynamically(name)
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

    terms = wrong_terms(path)
    if terms:
        problems.append(("用語の揺れ", terms))

    wrong_unit = millimetre_props_with_length_unit(path)
    if wrong_unit:
        problems.append(
            ("ミリの値に unit='LENGTH'（画面の単位が嘘になる）", wrong_unit)
        )

    casual = casual_wording(path)
    if casual:
        problems.append(
            ("くだけた言い回し（利用者が読む文章）", casual)
        )

    dead = unreachable_code(path)
    if dead:
        problems.append(
            ("到達しないコード（挿し込む位置の誤り）", dead)
        )

    unsafe_handlers = handlers_without_persistent(path)
    if unsafe_handlers:
        problems.append(
            ("@persistent が付いていないハンドラ（開き直すと効かない）",
             unsafe_handlers)
        )

    shadowed = shadowed_functions(path)
    if shadowed:
        problems.append(
            ("ローカル変数が同名の関数を隠している（UnboundLocalError）",
             shadowed)
        )

    missing_names = undefined_names(path)
    if missing_names:
        problems.append(
            ("未定義のまま読まれている名前（実行時に NameError）", missing_names)
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

    paths = [Path(a) for a in argv[1:]]

    # 同じパッケージの他のファイルも参照元として数える。
    #
    # パッケージの外へは出ないこと。以前は __init__.py を持つ限り
    # 上へ辿っていたため、リポジトリ直下に置いた古い計測スクリプトまで
    # 参照元に数え、旧プレフィクスの呼び出しを誤って報告していた。
    package_files = []
    for path in paths:
        root = path.parent
        while (root.parent / "__init__.py").exists() and root.parent != root:
            root = root.parent
        package_files.extend(root.rglob("*.py"))

    extra = collect_references(sorted(set(package_files)))

    exit_code = 0

    # 未定義の名前だけは、引数以外のファイルも見る。分割で起きる
    # 取り残しは「移動先」に現れるため、引数のファイルだけでは
    # 見逃す。実際 overlay で起きた。
    others = [
        p for p in sorted(set(package_files))
        if p.resolve() not in {q.resolve() for q in paths}
    ]
    stray = []
    for p in others:
        for item in undefined_names(p):
            stray.append(f"{p}: {item}")
        for item in shadowed_functions(p):
            stray.append(f"{p}: {item}")
        for item in unreachable_code(p):
            stray.append(f"{p}: {item}")
        for item in millimetre_props_with_length_unit(p):
            stray.append(f"{p}: {item}")
        for item in casual_wording(p):
            stray.append(f"{p}: {item}")
        for item in wrong_terms(p):
            stray.append(f"{p}: {item}")
    if stray:
        print("=" * 72)
        print("未定義のまま読まれている名前（実行時に NameError）")
        print("=" * 72)
        for item in stray:
            print("  " + item)
        print()
        exit_code = 1

    for arg in argv[1:]:
        path = Path(arg)
        print("=" * 72)
        print(path)
        print("=" * 72)

        if not path.exists():
            print("  ファイルが見つかりません")
            exit_code = 1
            continue

        stats, problems = analyze(path, extra)
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
