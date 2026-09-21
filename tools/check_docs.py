#!/usr/bin/env python3
"""開発の手引きに書いてある数字と一覧が、いまの中身と合っているかを見る。

■ なぜ要るか

手引きは「これを信じてよい」と思って読まれる文書なので、数字が1つ
でも古いと全部が疑わしくなる。実際、テスト数が 71/79 のまま 79/98
になっていて、`draft/ops/` には無い allowance が載っていた。無い
ファイルを探させる形になっていた。

これは落とし穴 3-0（同じ値を2箇所で作らない）そのもので、片方だけ
直せば黙ってずれる。人が見比べるのをやめて、突き合わせる。

■ 何を見るか

  1. 規模（ファイル数と行数）
  2. テスト件数
  3. オペレータの数と、その置き場の名前
  4. 検査ツールの表が check_addon の一覧と合っているか
  5. 地図に載っていないモジュールが無いか   ← 抜けを捕まえる

1〜3 は --fix で書き直せる。4 と 5 は、どう書くかが人の判断なので
報告だけにする。

使い方:
    python tools/check_docs.py
    python tools/check_docs.py --fix

終了コード: 0 = ズレ無し / 1 = ズレあり
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "開発の手引き.md"
PACKAGE = ROOT / "truescale"
CHECK_ADDON = ROOT / "tools" / "check_addon.py"

# 地図に載せなくてよいもの。登録だけの入口と、生成物。
MAP_EXEMPT = {"__init__.py"}


# ------------------------------------------------------------ 実際の値を測る

def source_files():
    return sorted(
        p for p in PACKAGE.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def scale():
    """ファイル数と、100行単位に丸めた行数。"""
    files = source_files()
    lines = sum(
        len(p.read_text(encoding="utf-8").splitlines()) for p in files
    )
    return len(files), int(round(lines / 100.0)) * 100


def test_counts():
    """@test の数。テストの実体と1対1なので、実行しなくても数えられる。"""
    out = []
    for name in ("test_pure.py", "test_headless.py"):
        text = (ROOT / "tools" / name).read_text(encoding="utf-8")
        out.append(len(re.findall(r"^@test$", text, re.MULTILINE)))
    return out


def operators(folder):
    """そのフォルダのオペレータ数と、置き場の名前。"""
    total = 0
    names = []
    for path in sorted((PACKAGE / folder).glob("*.py")):
        if path.name in MAP_EXEMPT:
            continue
        found = len(re.findall(
            r"^\s+bl_idname = ", path.read_text(encoding="utf-8"),
            re.MULTILINE,
        ))
        total += found
        if found:
            names.append(path.stem)
    return total, names


def check_addon_items():
    """check_addon の冒頭に並んでいる番号付きの一覧。"""
    text = CHECK_ADDON.read_text(encoding="utf-8")
    head = text.split('"""', 2)[1]
    return re.findall(r"^\s*(\d+)\. ", head, re.MULTILINE)


# ------------------------------------------------------------- 突き合わせる

class Doc:
    def __init__(self, path):
        self.path = path
        self.text = path.read_text(encoding="utf-8")
        self.problems = []
        self.fixes = 0

    def expect(self, pattern, wanted, what):
        """1つの数字（または語）を見て、違えば控える。"""
        match = re.search(pattern, self.text)
        if match is None:
            self.problems.append(f"{what}: 書いてある場所が見つからない")
            return

        found = match.group(1)
        if found == wanted:
            return

        self.problems.append(f"{what}: 「{found}」 -> 「{wanted}」")
        start, end = match.span(1)
        self.text = self.text[:start] + wanted + self.text[end:]
        self.fixes += 1

    def note(self, message):
        """直さずに報告だけするもの。"""
        self.problems.append(message)

    def save(self):
        self.path.write_text(self.text, encoding="utf-8", newline="\n")


def main(argv):
    fix = "--fix" in argv

    if not DOC.exists():
        print(f"{DOC} がありません")
        return 1

    doc = Doc(DOC)

    # 1. 規模
    files, lines = scale()
    doc.expect(r"\| 規模 \| (\d+)ファイル", str(files), "規模（ファイル数）")
    doc.expect(
        r"\| 規模 \|[^|]*約 ([\d,]+) 行",
        f"{lines:,}",
        "規模（行数）",
    )

    # 2. テスト
    pure, headless = test_counts()
    doc.expect(
        r"`test_pure\.py` (\d+)件", str(pure), "テスト（pure）"
    )
    doc.expect(
        r"`test_headless\.py` (\d+)件", str(headless), "テスト（headless）"
    )

    # 3. オペレータ。数は直せるが、名前の並びは人が決めるので報告だけ。
    for folder in ("unfold/ops", "draft/ops"):
        total, names = operators(folder)
        doc.expect(
            r"\| `" + re.escape(folder) + r"/` \| オペレータ(\d+)個",
            str(total),
            f"{folder}（数）",
        )

        written = re.search(
            r"\| `" + re.escape(folder) + r"/` \| オペレータ\d+個（([^）]*)）",
            doc.text,
        )
        if written:
            listed = {s.strip() for s in written.group(1).split("/")}
            if listed != set(names):
                missing = sorted(set(names) - listed)
                extra = sorted(listed - set(names))
                detail = []
                if extra:
                    detail.append(f"無いものが載っている: {', '.join(extra)}")
                if missing:
                    detail.append(f"載っていない: {', '.join(missing)}")
                doc.note(f"{folder}（置き場の名前）: " + " / ".join(detail))

    # 4. 検査ツールの表
    items = check_addon_items()
    rows = re.findall(r"^\| (\d+) \| ", doc.text, re.MULTILINE)
    if rows != items:
        doc.note(
            f"検査ツールの表: check_addon は {len(items)} 項目、"
            f"手引きは {len(rows)} 行"
        )

    # 5. 地図の抜け。これがいちばん見落とされる。
    for path in source_files():
        if path.name in MAP_EXEMPT:
            continue
        shown = path.relative_to(PACKAGE).as_posix()
        if f"`{shown}`" in doc.text:
            continue
        # 置き場ごと載っている場合（`unfold/ops/` など）は、それでよい
        if f"`{path.parent.relative_to(PACKAGE).as_posix()}/`" in doc.text:
            continue
        doc.note(f"地図に載っていない: {shown}")

    # ---------------------------------------------------------------- 結果

    if not doc.problems:
        print("  手引きの数字は合っています")
        return 0

    for line in doc.problems:
        print(f"  {line}")

    if fix and doc.fixes:
        doc.save()
        print(f"\n  {doc.fixes} 件を書き直しました（報告だけのものは残る）")
        return 0 if len(doc.problems) == doc.fixes else 1

    if doc.fixes:
        print("\n  --fix で数字だけ書き直せます")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
