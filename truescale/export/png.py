"""PNGの書き出しと、ピクセルバッファへの描画。

Blender に依存しない。標準ライブラリだけで動くので、
Blenderを起動せずに単体でテストできる。

実寸で印刷するために pHYs チャンクへ解像度を書き込む。
これが無いと、画像ビューアや印刷ソフトが「1ピクセル=1/72インチ」
などと勝手に解釈して、出力される物理サイズが変わってしまう。

書き込む口は2つある。

  encode_rgb  ピクセルの並びから PNG を作る（型紙側）
  patch_dpi   すでにある PNG を書き換える（三面図側）

三面図側は Blender にビューポートを撮らせるので、出来上がった
ファイルへ後から入れるしかない。用途は違うが、dpi から
「メートルあたりのピクセル数」を出すところは同じなので、
そこは共有する。以前は両側に別々に書かれていた。
"""

import binascii
import struct
import zlib

# 印刷用の既定解像度。実寸出力の基準。
PRINT_DPI = 300

# 1インチ = 25.4 mm
MM_PER_INCH = 25.4

# PNG の先頭8バイト。これで PNG かどうかを見分ける。
SIGNATURE = bytes([137, 80, 78, 71, 13, 10, 26, 10])


def mm_to_pixels(mm, dpi=PRINT_DPI):
    """ミリメートルをピクセル数へ。"""
    return float(mm) * dpi / MM_PER_INCH


def pixels_to_mm(pixels, dpi=PRINT_DPI):
    """ピクセル数をミリメートルへ。"""
    return float(pixels) * MM_PER_INCH / dpi


def new_buffer(width, height, fill=255):
    """RGB のピクセルバッファを作る。既定は白。"""
    return bytearray(bytes([fill]) * (width * height * 3))


def draw_line(buf, width, height, x0, y0, x1, y1, thickness=1,
              color=(0.0, 0.0, 0.0)):
    """バッファへ直線を引く。

    色は 0.0〜1.0 の RGB で受け取る。太さは線の向きに対して直角へ
    広げる。範囲外のピクセルは書き込まない。

    以前は線上の各ピクセルへ正方形のブラシを置いていた。斜めの線
    では対角方向に広がり、指定した太さの最大1.41倍になっていた。
    実寸で刷るための機能なので、指定と実際がずれるのは避ける。
    """
    rgb = bytes(
        max(0, min(255, int(round(float(c) * 255.0))))
        for c in color[:3]
    )

    ax = float(x0)
    ay = float(y0)
    bx = float(x1)
    by = float(y1)

    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy

    half = max(0.5, float(thickness) / 2.0)

    # 塗る必要があるのは線を囲む矩形の中だけ。端の丸みの分だけ広げる。
    pad = int(half) + 1
    min_x = max(0, int(min(ax, bx)) - pad)
    max_x = min(width - 1, int(max(ax, bx)) + pad)
    min_y = max(0, int(min(ay, by)) - pad)
    max_y = min(height - 1, int(max(ay, by)) + pad)

    if min_x > max_x or min_y > max_y:
        return

    half_sq = half * half

    for py in range(min_y, max_y + 1):
        row = py * width
        for px in range(min_x, max_x + 1):
            # 線分までの距離。端では端点までの距離になる。
            if length_sq <= 1e-12:
                t_along = 0.0
            else:
                t_along = ((px - ax) * dx + (py - ay) * dy) / length_sq
                if t_along < 0.0:
                    t_along = 0.0
                elif t_along > 1.0:
                    t_along = 1.0

            near_x = ax + dx * t_along
            near_y = ay + dy * t_along
            off_x = px - near_x
            off_y = py - near_y

            if off_x * off_x + off_y * off_y <= half_sq:
                pos = (row + px) * 3
                buf[pos:pos + 3] = rgb


def _chunk(chunk_type, data):
    """PNGのチャンクを組み立てる（長さ + 種別 + 中身 + CRC）。"""
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(data, crc) & 0xffffffff
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _phys_chunk(dpi):
    """pHYs チャンク。単位は「メートルあたりのピクセル数」。

    dpi から換算する式をここ1つに置く。2箇所に書くと、片方だけ
    直したときに同じ画像が違う大きさで刷られる。
    """
    pixels_per_meter = int(round(float(dpi) / 0.0254))
    return _chunk(b"pHYs", struct.pack(">IIB",
                                       pixels_per_meter,
                                       pixels_per_meter,
                                       1))


def patch_dpi(data, dpi):
    """すでにある PNG のバイト列へ、解像度を入れ直す。

    Blender に撮らせた PNG には解像度が入っていない（あるいは
    意図と違う）。入っていなければ IHDR の直後へ入れ、入っていれば
    差し替える。PNG でなければ None。

    ファイルではなくバイト列を受けるのは、Blender を起動せずに
    試せるようにするため。
    """
    if not data.startswith(SIGNATURE):
        return None

    out = bytearray(data[:8])
    pos = 8
    done = False

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            break

        if kind == b"pHYs":
            # 古いものは捨てる。1つだけ入れ直す。
            if not done:
                out += _phys_chunk(dpi)
                done = True
        else:
            out += data[pos:end]
            if kind == b"IHDR" and not done:
                out += _phys_chunk(dpi)
                done = True

        pos = end

    return bytes(out) if done else None


def encode_rgb(width, height, rgb_buffer, dpi=PRINT_DPI):
    """RGBバッファを PNG のバイト列にする。

    pHYs チャンクに解像度を入れる。単位は「メートルあたりのピクセル数」
    なので、dpi から換算する。
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        # 各行の先頭にフィルタ種別（0 = なし）
        raw.append(0)
        start = y * stride
        raw.extend(rgb_buffer[start:start + stride])

    compressed = zlib.compress(bytes(raw), 6)

    return (
        SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _phys_chunk(dpi)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def write_rgb(filepath, width, height, rgb_buffer, dpi=PRINT_DPI):
    """RGBバッファを PNG ファイルとして書き出す。

    filepath は文字列でも pathlib.Path でもよい。呼び出し側で
    どちらを使っているか揃っていないので、ここで吸収する。
    """
    with open(str(filepath), "wb") as handle:
        handle.write(encode_rgb(width, height, rgb_buffer, dpi))
