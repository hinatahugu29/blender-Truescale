#!/usr/bin/env python3
"""truescale/ を Blender Extension としてインストール可能な ZIP に固める。

blender_manifest.toml が ZIP の直下に来る形にする。
これを間違えるとBlenderがアドオンとして認識しない。

使い方:
    python tools/build.py              # dist/truescale-<version>.zip を作る
    python tools/build.py --check      # ZIPを作らず内容の妥当性だけ確認する
"""

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "truescale"
DIST = ROOT / "dist"

# ZIPに含めないもの
EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def read_version(manifest: Path) -> str:
    text = manifest.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("blender_manifest.toml に version が見つかりません")
    return match.group(1)


def collect_files(source: Path):
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        yield path


def main(argv):
    check_only = "--check" in argv

    manifest = SOURCE / "blender_manifest.toml"
    if not manifest.exists():
        raise SystemExit(f"{manifest} がありません")

    version = read_version(manifest)
    files = list(collect_files(SOURCE))

    # 最低限の妥当性チェック
    names = {p.relative_to(SOURCE).as_posix() for p in files}
    problems = []
    for required in ("blender_manifest.toml", "__init__.py", "LICENSE"):
        if required not in names:
            problems.append(f"必須ファイルがありません: {required}")

    if problems:
        for p in problems:
            print("  NG:", p)
        return 1

    print(f"バージョン : {version}")
    print(f"ファイル数 : {len(files)}")
    total = sum(p.stat().st_size for p in files)
    print(f"合計サイズ : {total / 1024:.1f} KB")
    for path in files:
        rel = path.relative_to(SOURCE).as_posix()
        print(f"    {rel}  ({path.stat().st_size:,} bytes)")

    if check_only:
        print("\n--check 指定のため ZIP は作成していません")
        return 0

    DIST.mkdir(exist_ok=True)
    out = DIST / f"truescale-{version}.zip"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            # ZIP直下が blender_manifest.toml になるよう、
            # truescale/ からの相対パスで格納する
            zf.write(path, path.relative_to(SOURCE).as_posix())

    print(f"\n作成しました: {out}  ({out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
