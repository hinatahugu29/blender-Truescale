#!/usr/bin/env python3
"""到達できないコードを取り除く。

classes に登録されていないクラスと、そこからしか呼ばれていない
モジュール直下の関数を、変化しなくなるまで繰り返し削除する。

使い方:
    python tools/prune_dead_code.py truescale/unfold/__init__.py
    python tools/prune_dead_code.py --dry-run truescale/unfold/__init__.py

安全のため、次のものは削除しない。
  - classes タプルに載っているクラス
  - register / unregister
  - 名前が文字列としてファイル内に現れるもの（動的参照の可能性）
  - --keep で明示したもの
"""

import ast
import re
import sys
from pathlib import Path

ENTRY_POINTS = {"register", "unregister"}

REGISTRABLE_BASES = (
    "Operator", "Panel", "Menu", "PropertyGroup",
    "AddonPreferences", "UIList", "Header",
)


def parse(path):
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source)


def registered_class_names(tree):
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name)
                    and target.id == "classes"
                    and isinstance(node.value, (ast.Tuple, ast.List))):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
    return names


def top_level_defs(tree):
    """モジュール直下のクラスと関数を (種類, 名前, ノード) で返す。"""
    result = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            result.append(("class", node.name, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(("func", node.name, node))
    return result


def name_loads_outside(tree, excluded_nodes):
    """指定ノードの外側で読み取られている名前を集める。"""
    excluded = set()
    for node in excluded_nodes:
        for child in ast.walk(node):
            excluded.add(id(child))

    loads = set()
    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.add(node.id)
    return loads


def string_literals(source):
    """ファイル中の文字列リテラルに現れる語。動的参照の保険。"""
    words = set()
    for match in re.finditer(r"[\"']([A-Za-z_][A-Za-z0-9_.]*)[\"']", source):
        words.add(match.group(1))
        words.add(match.group(1).rpartition(".")[2])
    return words


def node_span(source_lines, node):
    """デコレータとコメントを含めた削除範囲（0始まり, 終端含まず）。"""
    start = node.lineno - 1
    for decorator in getattr(node, "decorator_list", []):
        start = min(start, decorator.lineno - 1)

    # 直前の連続したコメント行も一緒に落とす
    while start > 0:
        previous = source_lines[start - 1].strip()
        if previous.startswith("#"):
            start -= 1
        else:
            break

    end = node.end_lineno
    # 後続の空行も詰める
    while end < len(source_lines) and not source_lines[end].strip():
        end += 1
    return start, end


def prune_once(path, keep, verbose=True):
    source, tree = parse(path)
    lines = source.split("\n")

    registered = registered_class_names(tree)
    literals = string_literals(source)

    victims = []

    # 1) 登録されていない登録対象クラス
    for kind, name, node in top_level_defs(tree):
        if kind != "class":
            continue
        bases = [
            b.attr if isinstance(b, ast.Attribute) else
            (b.id if isinstance(b, ast.Name) else "")
            for b in node.bases
        ]
        if not any(b in REGISTRABLE_BASES for b in bases):
            continue
        if name in registered or name in keep:
            continue
        victims.append((kind, name, node))

    # 2) どこからも読み取られていないモジュール直下の関数
    if not victims:
        defs = top_level_defs(tree)
        nodes_by_name = {name: node for _, name, node in defs}
        for kind, name, node in defs:
            if kind != "func":
                continue
            if name in ENTRY_POINTS or name in keep:
                continue
            # 自分自身の中での参照は数えない（再帰対策）
            loads = name_loads_outside(tree, [node])
            if name in loads:
                continue
            if name in literals:
                # 文字列として現れる = 動的に呼ばれている可能性
                continue
            victims.append((kind, name, node))

    if not victims:
        return 0, []

    spans = sorted(
        (node_span(lines, node) for _, _, node in victims),
        reverse=True,
    )
    for start, end in spans:
        del lines[start:end]

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return len(victims), [f"{kind} {name}" for kind, name, _ in victims]


def main(argv):
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]

    keep = set()
    if "--keep" in argv:
        index = argv.index("--keep")
        keep = set(argv[index + 1].split(","))
        argv = argv[:index] + argv[index + 2:]

    if not argv:
        print(__doc__)
        return 2

    path = Path(argv[0])
    total = 0
    removed_all = []

    for round_number in range(1, 31):
        if dry_run:
            source, tree = parse(path)
            before = len(source.split("\n"))
        count, removed = prune_once(path, keep)
        if count == 0:
            break
        total += count
        removed_all.extend(removed)
        print(f"  第{round_number}巡: {count} 個削除")
        for name in removed:
            print(f"      {name}")
        if dry_run:
            print("  --dry-run なのでここで中断（1巡だけ実行済み）")
            break

    source = path.read_text(encoding="utf-8")
    print()
    print(f"  合計 {total} 個を削除")
    print(f"  残り {len(source.splitlines())} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
