"""並べた紙を、PDF か PNG にする。

どちらも同じ Sheet から作るので、出力の形式を変えても内容は
変わらない。違うのは書き出す先だけ。

■ 選べるようにしてある理由

PDF … 分割したとき1つのファイルにまとまる。ページの大きさを
       実寸で持つので「原寸で刷る」が確実。線のままなので軽い
PNG … 他のソフトへ持ち込みたいとき。画像として確認したいとき

分割するなら PDF のほうが確実だが、PNG でないと困る場面もある
（画像編集ソフトで手を入れる、など）ので両方残す。
"""

from . import pdf as _pdf
from . import png as _png


def to_pdf(filepath, sheets, title="Truescale"):
    """1つの PDF にまとめて書き出す。ページ数を返す。"""
    if not sheets:
        raise ValueError("書き出すものがありません")

    pages = []
    for sheet in sheets:
        page = _pdf.Page(sheet.paper_w, sheet.paper_h)
        for x0, y0, x1, y1, color, width in sheet.lines:
            page.line(x0, y0, x1, y1, width, color)
        pages.append(page)

    _pdf.write(filepath, pages, title=title)
    return len(pages)


def to_png(filepath, sheet, dpi=None):
    """1枚を PNG にする。分割時は呼び出し側が枚数分呼ぶ。

    画像は紙と同じ大きさで作る。紙より小さい画像を「用紙に合わせて」
    刷らせると拡大され、実寸が崩れる。
    """
    dpi = float(dpi or _png.PRINT_DPI)
    px_per_mm = dpi / 25.4

    width_px = int(round(sheet.paper_w * px_per_mm))
    height_px = int(round(sheet.paper_h * px_per_mm))

    buffer = _png.new_buffer(width_px, height_px)

    for x0, y0, x1, y1, color, width in sheet.lines:
        _png.draw_line(
            buffer,
            width_px,
            height_px,
            x0 * px_per_mm,
            # 画像は左上が原点なので、縦を反転する
            height_px - y0 * px_per_mm,
            x1 * px_per_mm,
            height_px - y1 * px_per_mm,
            thickness=max(1, int(round(float(width) * px_per_mm))),
            color=color,
        )

    _png.write_rgb(filepath, width_px, height_px, buffer, int(dpi))
    return (width_px, height_px)


def estimate_png_pixels(sheets, dpi=None):
    """PNG にした場合の総ピクセル数。作る前に確かめるため。

    分割して30枚となると、PNGでは合計が数億ピクセルになる。
    作り始めてから落ちるより、先に知らせたい。
    """
    from . import png as png_module

    dpi = float(dpi or png_module.PRINT_DPI)
    px_per_mm = dpi / 25.4

    total = 0
    for sheet in sheets:
        total += (
            int(round(sheet.paper_w * px_per_mm))
            * int(round(sheet.paper_h * px_per_mm))
        )
    return total
