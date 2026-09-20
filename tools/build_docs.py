#!/usr/bin/env python3
"""docs/*.md から、1枚の HTML マニュアルを作る。

  python tools/build_docs.py

出力は2つ。中身は同じ。

  docs/manual.html      リポジトリで見る用
  truescale/manual.html ZIP に入れて配る用

■ なぜ生成するのか

HTML を手で書くと、Markdown と2つの文書になる。片方だけ直せば食い違い、
どちらが最新か分からなくなる。このプロジェクトで7回やった間違いなので、
文書でも繰り返さない。

**HTML は生成物。直に編集しないこと。** 元は docs/*.md。

■ Markdown の全部は解釈しない

使っている記法だけを扱う。見出し、段落、箇条書き、表、コードブロック、
引用、区切り線、強調、リンク、インラインコード。それ以外が要るように
なったら、ここへ足す。既製のライブラリを入れないのは、Blender に同梱の
Python だけで動かしたいため（アドオン本体と同じ方針）。
"""

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# マニュアルに入れる文書。利用者が読むものだけ。
# 開発の手引きとレビューメモは入れない（読む相手が違う）。
PAGES = (
    ("つかいかた.md", "つかいかた"),
    ("こまったとき.md", "こまったとき"),
)

TITLE = "Truescale マニュアル"

OUTPUTS = (
    DOCS / "manual.html",
    ROOT / "truescale" / "manual.html",
)


# ---------------------------------------------------------------- 行内の記法

def inline(text):
    """行の中の記法を HTML へ。

    先にエスケープしてから記法を当てる。順番が逆だと、生成した
    タグ自身がエスケープされる。
    """
    out = html.escape(text)

    # コードは中身を守りたいので、いったん退避する。
    holes = []

    def stash(match):
        holes.append(match.group(1))
        return f"\x00{len(holes) - 1}\x00"

    out = re.sub(r"`([^`]+)`", stash, out)

    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: '<a href="{}">{}</a>'.format(
            _link(m.group(2)), m.group(1)
        ),
        out,
    )

    for index, code in enumerate(holes):
        out = out.replace(f"\x00{index}\x00", f"<code>{code}</code>")

    return out


def _link(href):
    """文書どうしのリンクを、1枚の HTML の中の飛び先へ直す。"""
    if href.startswith("#"):
        return href
    name = href.split("/")[-1]
    for source, _label in PAGES:
        if name == source:
            return "#" + slug(source)
    return href


def slug(text):
    """見出しやファイル名から、飛び先の名前を作る。"""
    base = text.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^\w぀-ヿ一-鿿-]+", "-", base)
    return cleaned.strip("-").lower() or "section"


# ------------------------------------------------------------------- 本文

def render(lines, prefix):
    """Markdown の行の並びを HTML へ。見出しの一覧も返す。"""
    out = []
    toc = []
    index = 0
    count = len(lines)

    while index < count:
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        # コードブロック
        if stripped.startswith("```"):
            body = []
            index += 1
            while index < count and not lines[index].strip().startswith("```"):
                body.append(html.escape(lines[index]))
                index += 1
            index += 1
            out.append("<pre><code>" + "\n".join(body) + "</code></pre>")
            continue

        # 区切り線
        if re.fullmatch(r"-{3,}", stripped):
            out.append("<hr>")
            index += 1
            continue

        # 見出し
        heading = re.match(r"(#{1,4})\s+(.*)", stripped)
        if heading:
            level = len(heading.group(1)) + 1   # ページ見出しが h2 なので1つ下げる
            text = heading.group(2)
            anchor = f"{prefix}-{slug(text)}"
            out.append(
                f'<h{level} id="{anchor}">{inline(text)}</h{level}>'
            )
            if level <= 3:
                toc.append((level, anchor, text))
            index += 1
            continue

        # 表
        if stripped.startswith("|"):
            rows = []
            while index < count and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip())
                index += 1
            out.append(table(rows))
            continue

        # 引用
        if stripped.startswith(">"):
            body = []
            while index < count and lines[index].strip().startswith(">"):
                body.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append("<blockquote>" + inline(" ".join(body)) + "</blockquote>")
            continue

        # 箇条書き。字下げした行は、その項目の続きとして繋ぐ。
        # 繋がないと、折り返しただけの行が別の段落として出る。
        if re.match(r"[-*]\s+", stripped):
            items = []
            while index < count:
                row = lines[index]
                if re.match(r"\s*[-*]\s+", row):
                    items.append(re.sub(r"^\s*[-*]\s+", "", row).strip())
                elif items and row.startswith((" ", "	")) and row.strip():
                    items[-1] += " " + row.strip()
                else:
                    break
                index += 1
            out.append(
                "<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>"
            )
            continue

        # 段落。空行まで続ける。
        body = []
        while index < count and lines[index].strip():
            nxt = lines[index].strip()
            if (nxt.startswith(("|", ">", "```", "#"))
                    or re.match(r"[-*]\s+", nxt)
                    or re.fullmatch(r"-{3,}", nxt)):
                break
            body.append(nxt)
            index += 1
        if body:
            out.append("<p>" + inline(" ".join(body)) + "</p>")
        else:
            index += 1

    return "\n".join(out), toc


def table(rows):
    """| で区切った表を HTML へ。2行目の区切りは読み飛ばす。"""
    def cells(row):
        return [c.strip() for c in row.strip("|").split("|")]

    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]] if len(rows) > 2 else []

    parts = ["<table>"]

    # 「| | |」のように見出しが空の表は、2列の対応表として使っている。
    # 空の見出し行を出すと、意味のない灰色の帯が乗るだけ。
    if any(c for c in head):
        parts.append("<thead><tr>")
        parts += [f"<th>{inline(c)}</th>" for c in head]
        parts.append("</tr></thead>")

    parts.append("<tbody>")

    for row in body:
        parts.append("<tr>")
        parts += [f"<td>{inline(c)}</td>" for c in row]
        parts.append("</tr>")

    parts.append("</tbody></table>")
    return "".join(parts)


# -------------------------------------------------------------------- 体裁

STYLE = """
:root {
  --bg: #fbfaf8;
  --panel: #ffffff;
  --ink: #23201c;
  --soft: #6a635b;
  --line: #e2ddd5;
  --accent: #8a5a2b;
  --code-bg: #f3f0ea;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #17161a;
    --panel: #1e1d22;
    --ink: #e8e4dd;
    --soft: #a29a90;
    --line: #34313a;
    --accent: #d8a56a;
    --code-bg: #26242b;
  }
}
:root[data-theme="dark"] {
  --bg: #17161a;
  --panel: #1e1d22;
  --ink: #e8e4dd;
  --soft: #a29a90;
  --line: #34313a;
  --accent: #d8a56a;
  --code-bg: #26242b;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Hiragino Sans", "Noto Sans JP", "Yu Gothic UI",
               system-ui, sans-serif;
  line-height: 1.85;
  font-size: 15px;
}

.wrap {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 40px;
  max-width: 1080px;
  margin: 0 auto;
  padding: 0 16px 96px;
}

nav {
  position: sticky;
  top: 0;
  align-self: start;
  max-height: 100vh;
  overflow-y: auto;
  padding: 32px 0;
  font-size: 13.5px;
}
nav .brand {
  font-size: 19px;
  font-weight: 700;
  letter-spacing: .02em;
  margin-bottom: 4px;
}
nav .sub { color: var(--soft); font-size: 12.5px; margin-bottom: 20px; }
nav a {
  display: block;
  color: var(--soft);
  text-decoration: none;
  padding: 3px 0;
  border-left: 2px solid transparent;
  padding-left: 10px;
}
nav a:hover { color: var(--accent); border-left-color: var(--accent); }
nav a.lvl2 { font-weight: 600; color: var(--ink); margin-top: 14px; }
nav a.lvl3 { padding-left: 22px; }

main { padding: 32px 0 0; min-width: 0; }

h1 { font-size: 30px; letter-spacing: .01em; margin: 0 0 6px; }
h2 {
  font-size: 23px;
  margin: 56px 0 14px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--line);
}
h3 { font-size: 18px; margin: 36px 0 10px; }
h4 { font-size: 15.5px; margin: 26px 0 8px; color: var(--accent); }

p { margin: 12px 0; }
ul { margin: 12px 0; padding-left: 22px; }
li { margin: 5px 0; }

a { color: var(--accent); }

code {
  background: var(--code-bg);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
  font-size: .88em;
}
pre {
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px 16px;
  overflow-x: auto;
  line-height: 1.6;
}
pre code { background: none; padding: 0; font-size: 13px; }

blockquote {
  margin: 16px 0;
  padding: 12px 16px;
  border-left: 3px solid var(--accent);
  background: var(--panel);
  border-radius: 0 8px 8px 0;
  color: var(--soft);
}
blockquote strong { color: var(--ink); }

table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  text-align: left;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
th { font-weight: 600; background: var(--code-bg); }
tbody tr:last-child td { border-bottom: none; }

hr { border: none; border-top: 1px solid var(--line); margin: 40px 0; }

.note {
  color: var(--soft);
  font-size: 12.5px;
  margin-top: 64px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

@media (max-width: 820px) {
  .wrap { grid-template-columns: 1fr; gap: 0; }
  nav {
    position: static;
    max-height: none;
    padding: 24px 0 8px;
    border-bottom: 1px solid var(--line);
  }
  main { padding-top: 20px; }
}
"""


def build(version):
    sections = []
    nav = []

    for source, label in PAGES:
        path = DOCS / source
        if not path.exists():
            raise SystemExit(f"{path} がありません")

        lines = path.read_text(encoding="utf-8").split("\n")

        # 先頭の h1 はページの見出しとして使うので、本文からは外す。
        if lines and lines[0].startswith("# "):
            lines = lines[1:]

        anchor = slug(source)
        body, toc = render(lines, anchor)

        sections.append(
            f'<section><h2 id="{anchor}">{html.escape(label)}</h2>\n{body}\n</section>'
        )

        nav.append(f'<a class="lvl2" href="#{anchor}">{html.escape(label)}</a>')
        for level, target, text in toc:
            if level == 3:
                nav.append(
                    f'<a class="lvl3" href="#{target}">{html.escape(text)}</a>'
                )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(TITLE)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
<nav>
<div class="brand">Truescale</div>
<div class="sub">v{html.escape(version)} マニュアル</div>
{chr(10).join(nav)}
</nav>
<main>
<h1>{html.escape(TITLE)}</h1>
<p>Blender のモデルを、実寸のまま紙へ出すためのアドオンです。</p>
{chr(10).join(sections)}
<p class="note">
このページは <code>docs/*.md</code> から <code>tools/build_docs.py</code> が
作っています。直に編集しても次の生成で消えます。直すときは Markdown のほうを。
</p>
</main>
</div>
</body>
</html>
"""


def read_version():
    manifest = ROOT / "truescale" / "blender_manifest.toml"
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        manifest.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group(1) if match else "0.0.0"


def main():
    page = build(read_version())

    for target in OUTPUTS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8", newline="\n")
        print(f"  作成しました: {target.relative_to(ROOT)}  "
              f"({len(page.encode('utf-8')) / 1024:.1f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
