"""PNGの書き出しと、ピクセルバッファへの描画。

Blender に依存しない。標準ライブラリだけで動くので、
Blenderを起動せずに単体でテストできる。

実寸で印刷するために pHYs チャンクへ解像度を書き込む。
これが無いと、画像ビューアや印刷ソフトが「1ピクセル=1/72インチ」
などと勝手に解釈して、出力される物理サイズが変わってしまう。
"""

import binascii
import struct
import zlib

# 印刷用の既定解像度。実寸出力の基準。
PRINT_DPI = 300

# 1インチ = 25.4 mm
MM_PER_INCH = 25.4


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
    """バッファへ直線を引く（Bresenham）。

    色は 0.0〜1.0 の RGB で受け取る。太さは中心からの半径で広げる。
    範囲外のピクセルは書き込まない。
    """
    x0 = int(round(x0))
    y0 = int(round(y0))
    x1 = int(round(x1))
    y1 = int(round(y1))

    rgb = bytes(
        max(0, min(255, int(round(float(c) * 255.0))))
        for c in color[:3]
    )

    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    radius = max(0, thickness // 2)

    while True:
        for oy in range(-radius, radius + 1):
            yy = y0 + oy
            if yy < 0 or yy >= height:
                continue

            for ox in range(-radius, radius + 1):
                xx = x0 + ox
                if xx < 0 or xx >= width:
                    continue

                pos = (yy * width + xx) * 3
                buf[pos:pos + 3] = rgb

        if x0 == x1 and y0 == y1:
            break

        e2 = 2 * err

        if e2 >= dy:
            err += dy
            x0 += sx

        if e2 <= dx:
            err += dx
            y0 += sy


def _chunk(chunk_type, data):
    """PNGのチャンクを組み立てる（長さ + 種別 + 中身 + CRC）。"""
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(data, crc) & 0xffffffff
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_rgb(width, height, rgb_buffer, dpi=PRINT_DPI):
    """RGBバッファを PNG のバイト列にする。

    pHYs チャンクに解像度を入れる。単位は「メートルあたりのピクセル数」
    なので、dpi から換算する。
    """
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    pixels_per_meter = int(round(dpi / 0.0254))
    phys = struct.pack(">IIB", pixels_per_meter, pixels_per_meter, 1)

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        # 各行の先頭にフィルタ種別（0 = なし）
        raw.append(0)
        start = y * stride
        raw.extend(rgb_buffer[start:start + stride])

    compressed = zlib.compress(bytes(raw), 6)

    return (
        signature
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"pHYs", phys)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def write_rgb(filepath, width, height, rgb_buffer, dpi=PRINT_DPI):
    """RGBバッファを PNG ファイルとして書き出す。

    filepath は pathlib.Path を想定する。
    """
    filepath.write_bytes(encode_rgb(width, height, rgb_buffer, dpi))
