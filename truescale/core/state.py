"""描画用キャッシュの持ち主。

型紙の解析結果（島の抽出、型紙IDや矢印の配置探索、合印のセグメント）は
作るのに時間がかかるので、使い回せるあいだはキャッシュする。
その置き場と無効化をここへ集約する。

■ なぜ1箇所に集めるか

キャッシュはこれから分かれていく overlay（描画）と marking（生成）の
両方が触る。モジュールごとに持つと互いを import し合う形になるため、
どちらからも一方向に依存できる場所へ置く。

■ epoch の考え方

キャッシュのキーには必ず epoch を含める。型紙そのものや注記が変わったら
epoch を進め、それ以前のキーを一括で無効にする。

逆に、ビューの回転やオブジェクトの移動では epoch を進めてはいけない。
これらは計算結果を変えないので、進めると毎フレーム作り直しになる。
実際それで「オブジェクトを動かすと極端に重い」という不具合が出た。

bpy には依存しない。再描画の要求は呼び出し側で行う。
"""

# キャッシュ世代。進めると、これを含む全てのキーが外れる。
epoch = 0

# 種別ごとの計算結果。キーは呼び出し側が組み立てる。
draw_cache = {}

# 島の抽出結果。型紙の形が変わらない限り使い回せる。
island_cache = {
    "key": None,
    "records": None,
    "face_to_island": None,
    "adjacency": None,
}

# 面の2D座標を素のタプルへ展開したもの。内外判定から数万回引かれる。
flat_poly_cache = {"key": None, "polys": None}

# 辺 -> その辺に接する面の一覧。合印を描くたびに使う。
flat_edge_face_cache = {"key": None, "faces": None}

# draw_cache の上限。超えたら古い順に捨てる。
DRAW_CACHE_LIMIT = 64


def invalidate():
    """型紙や注記が変わったときに呼ぶ。全キャッシュを無効にする。

    再描画の要求は含まない。bpy に依存させないため、
    呼び出し側で別途行うこと。
    """
    global epoch, draw_cache, island_cache, flat_poly_cache
    global flat_edge_face_cache

    epoch += 1
    draw_cache = {}
    island_cache = {
        "key": None,
        "records": None,
        "face_to_island": None,
        "adjacency": None,
    }
    flat_poly_cache = {"key": None, "polys": None}
    flat_edge_face_cache = {"key": None, "faces": None}


def store(key, value):
    """描画キャッシュへ保存する。上限を超えたら古いものから捨てる。

    全消しにすると、ある設定のキャッシュが埋まったときに
    無関係なキャッシュまで巻き添えで消える。
    dict は挿入順を保つので、先頭から落とせば古い順になる。
    """
    global draw_cache

    overflow = len(draw_cache) - DRAW_CACHE_LIMIT + 1
    if overflow > 0:
        for old_key in list(draw_cache)[:overflow]:
            draw_cache.pop(old_key, None)

    draw_cache[key] = value
    return value


def get(key):
    """描画キャッシュから取り出す。無ければ None。"""
    return draw_cache.get(key)


def stats():
    """現在の状態。デバッグと検証用。"""
    return {
        "epoch": epoch,
        "draw_cache": len(draw_cache),
        "island_cached": island_cache["key"] is not None,
        "flat_poly_cached": flat_poly_cache["key"] is not None,
        "flat_edge_face_cached": flat_edge_face_cache["key"] is not None,
    }
