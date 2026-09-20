"""型紙を PDF として書き出す。

外部ライブラリは使わない。Blender に入っている zlib だけで足りる。

■ なぜ PNG だけでは足りないか

  1枚にまとまる … 分割すると30枚になることがある。PNGだと30回
                  印刷ダイアログを開くことになる
  紙の寸法を持つ … PDF はページの大きさを実寸（ポイント）で持つ。
                  PNG の pHYs より確実に「原寸で刷る」が通る
  線のまま残る   … 拡大しても粗くならない。ファイルも桁違いに小さい

■ 文字はどうするか

PDF に日本語を埋めるにはフォントの埋め込みが要り、これは大仕事。
しかしこのアドオンは既に、文字を Blender のフォント機能で
メッシュ化して輪郭線にする仕組みを持っている（export.outline）。
その線をそのまま PDF の線として描けば、フォントを埋め込まずに
日本語が出る。

文字は選択もコピーもできなくなるが、型紙にそれは要らない。

■ 座標の単位

PDF は 1/72 インチ（ポイント）。左下が原点で、Blender と同じ向き。
ミリからの換算は 1 mm = 72/25.4 pt。ここが実寸の要なので、
換算は1箇所に閉じている。
"""

import zlib

# 1 ミリは何ポイントか。PDF の長さの単位は 1/72 インチ。
PT_PER_MM = 72.0 / 25.4


def mm_to_pt(mm):
    """ミリをポイントへ。実寸の要なので、換算はここだけ。"""
    return float(mm) * PT_PER_MM


class Page:
    """1ページ分の描画内容。

    座標はミリで受け取り、左下を原点とする。ポイントへの換算は
    書き出すときにまとめて行う。
    """

    def __init__(self, width_mm, height_mm):
        self.width_mm = float(width_mm)
        self.height_mm = float(height_mm)
        self._parts = []
        self._color = None
        self._width = None

    def _use(self, color, width_mm):
        """色と線幅は、変わったときだけ書く。

        線1本ごとに書くと、30枚の型紙では内容が数倍に膨らむ。
        """
        rgb = (
            round(float(color[0]), 4),
            round(float(color[1]), 4),
            round(float(color[2]), 4),
        )
        if rgb != self._color:
            self._parts.append(f"{rgb[0]} {rgb[1]} {rgb[2]} RG")
            self._color = rgb

        pt = round(mm_to_pt(width_mm), 4)
        if pt != self._width:
            self._parts.append(f"{pt} w")
            self._width = pt

    def line(self, x0, y0, x1, y1, width_mm=0.3, color=(0.0, 0.0, 0.0)):
        """線を1本引く。座標はミリ、左下が原点。"""
        self._use(color, width_mm)
        self._parts.append(
            f"{mm_to_pt(x0):.4f} {mm_to_pt(y0):.4f} m "
            f"{mm_to_pt(x1):.4f} {mm_to_pt(y1):.4f} l S"
        )

    def polyline(self, points, width_mm=0.3, color=(0.0, 0.0, 0.0)):
        """続いた線を引く。線をまたぐ継ぎ目が出ないので輪郭に向く。"""
        if len(points) < 2:
            return
        self._use(color, width_mm)
        x, y = points[0]
        body = [f"{mm_to_pt(x):.4f} {mm_to_pt(y):.4f} m"]
        for x, y in points[1:]:
            body.append(f"{mm_to_pt(x):.4f} {mm_to_pt(y):.4f} l")
        body.append("S")
        self._parts.append(" ".join(body))

    def rect(self, x, y, w, h, width_mm=0.3, color=(0.0, 0.0, 0.0)):
        """枠を描く。塗らない。"""
        self._use(color, width_mm)
        self._parts.append(
            f"{mm_to_pt(x):.4f} {mm_to_pt(y):.4f} "
            f"{mm_to_pt(w):.4f} {mm_to_pt(h):.4f} re S"
        )

    def content(self):
        """このページの内容をバイト列で返す。

        線の端は丸めておく。角のある端だと、細い線の継ぎ目に
        隙間が見えることがある。
        """
        head = "1 J 1 j"
        return ("\n".join([head] + self._parts) + "\n").encode("ascii")


def _obj(number, body):
    return f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"


def build(pages, title="Truescale"):
    """ページの一覧から PDF のバイト列を作る。

    参照表（xref）は各オブジェクトの先頭バイト位置を並べたもの。
    ここがずれると、読み手によっては開けない。位置は組み立てながら
    実際に数えるので、後から内容を足してもずれない。
    """
    if not pages:
        raise ValueError("ページがありません")

    out = bytearray(b"%PDF-1.4\n")
    # 2バイト目以降にバイナリを示す印。テキスト転送で壊れないように。
    out += b"%\xe2\xe3\xcf\xd3\n"

    offsets = {}
    page_count = len(pages)

    # 1 = カタログ、2 = ページの親。ページ本体は 3 から2つずつ使う。
    page_ids = [3 + i * 2 for i in range(page_count)]
    content_ids = [4 + i * 2 for i in range(page_count)]
    info_id = 3 + page_count * 2

    offsets[1] = len(out)
    out += _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    offsets[2] = len(out)
    out += _obj(
        2,
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii"),
    )

    for page, page_id, content_id in zip(pages, page_ids, content_ids):
        width_pt = mm_to_pt(page.width_mm)
        height_pt = mm_to_pt(page.height_mm)

        offsets[page_id] = len(out)
        out += _obj(
            page_id,
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {width_pt:.4f} {height_pt:.4f}] "
                f"/Contents {content_id} 0 R "
                f"/Resources << >> >>"
            ).encode("ascii"),
        )

        raw = page.content()
        packed = zlib.compress(raw, 9)

        offsets[content_id] = len(out)
        out += f"{content_id} 0 obj\n".encode("ascii")
        out += (
            f"<< /Length {len(packed)} /Filter /FlateDecode >>\nstream\n"
        ).encode("ascii")
        out += packed
        out += b"\nendstream\nendobj\n"

    safe_title = "".join(
        c for c in str(title) if 32 <= ord(c) < 127 and c not in "()\\"
    )
    offsets[info_id] = len(out)
    out += _obj(
        info_id,
        f"<< /Title ({safe_title}) /Producer (Truescale Unfold) >>".encode(
            "ascii"
        ),
    )

    total = info_id + 1
    xref_at = len(out)
    out += f"xref\n0 {total}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for number in range(1, total):
        out += f"{offsets[number]:010d} 00000 n \n".encode("ascii")

    out += (
        f"trailer\n<< /Size {total} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")

    return bytes(out)


def write(filepath, pages, title="Truescale"):
    """PDF をファイルへ書き出す。"""
    data = build(pages, title=title)
    with open(filepath, "wb") as handle:
        handle.write(data)
    return len(data)
