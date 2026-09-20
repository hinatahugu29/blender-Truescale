#!/usr/bin/env python3
"""ある関数が、同じモジュールのどの関数に依存しているかを調べる。

分割の順番を決めるために使う。依存の少ない葉から切り出せば、
循環参照を作らずに済む。

使い方:
    python tools/deps.py truescale/unfold/__init__.py _draw_paper_guide
    python tools/deps.py truescale/unfold/__init__.py _draw_paper_guide --tree
"""

import ast
import sys
from pathlib import Path


def build_index(tree):
    """モジュール直下の関数名 -> ノード。"""
    index = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index[node.name] = node
    return index


def direct_calls(node, known):
    """そのノードが参照している、既知の関数名。"""
    used = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if child.id in known:
                used.add(child.id)
    return used


def transitive(start, index):
    """推移的な依存を集める。"""
    seen = set()
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        node = index.get(name)
        if node is None:
            continue
        for dep in direct_calls(node, index):
            if dep not in seen:
                stack.append(dep)
    seen.discard(start)
    return seen


def external_names(node):
    """モジュール外（import したもの）への参照。"""
    used = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            used.add(child.value.id)
    return used


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2

    path = Path(argv[0])
    targets = [a for a in argv[1:] if not a.startswith("--")]
    show_tree = "--tree" in argv

    tree = ast.parse(path.read_text(encoding="utf-8"))
    index = build_index(tree)

    for target in targets:
        if target not in index:
            print(f"{target}: 見つかりません")
            continue

        node = index[target]
        direct = sorted(direct_calls(node, index))
        deep = sorted(transitive(target, index))
        external = sorted(
            name for name in external_names(node)
            if name in ("bpy", "gpu", "blf", "_state", "_session",
                        "_units", "_paper", "_png", "_debug")
        )

        print("=" * 66)
        print(f"{target}  （{node.end_lineno - node.lineno + 1} 行）")
        print("=" * 66)
        print(f"  直接の依存 : {len(direct)} 個")
        if show_tree:
            for name in direct:
                print(f"      {name}")
        print(f"  推移的依存 : {len(deep)} 個")
        print(f"  外部モジュール: {', '.join(external) or 'なし'}")

        if show_tree and deep:
            print()
            print("  推移的に必要な関数:")
            for name in deep:
                size = index[name].end_lineno - index[name].lineno + 1
                print(f"      {name}  ({size} 行)")

        total = sum(
            index[name].end_lineno - index[name].lineno + 1
            for name in deep
            if name in index
        )
        print()
        print(f"  切り出すと動く総行数: {total + (node.end_lineno - node.lineno + 1)} 行")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
