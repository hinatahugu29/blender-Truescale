"""平面化した型紙と、元モデルの対応表。

型紙を作るとき、UVの切れ目で頂点が分割されるため、型紙の頂点数は
元モデルより多くなる。そのままでは「型紙のこの辺は元モデルのどの辺か」
が分からず、合印の位置や接続先IDを決められない。

そこで生成時に対応表を作り、型紙オブジェクトのカスタムプロパティへ
JSON で持たせている。ここはその読み出し担当。

  頂点 … 型紙の頂点 -> 元モデルの頂点
  辺   … 型紙の辺   -> 元モデルの辺
  面   … 型紙の面   -> 元モデルの面

一覧はインデックスの配列で、型紙側の番号が添字になる。
"""

import json

from .. import debug as _debug


def read_indices(unfold_obj):
    try:
        vert_src = json.loads(
            unfold_obj.get("tsunfold_flat_vertex_source_json", "[]")
        )
        face_src = json.loads(
            unfold_obj.get("tsunfold_flat_face_source_json", "[]")
        )
    except Exception:
        return [], []

    return vert_src, face_src


def flat_vertex_to_source(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("tsunfold_flat_vertex_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def flat_edge_to_source(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("tsunfold_flat_edge_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def flat_face_to_source(unfold_obj):
    try:
        values = json.loads(
            unfold_obj.get("tsunfold_flat_face_source_json", "[]")
        )
        return [int(v) for v in values]
    except Exception:
        return []


def flat_edge_faces(unfold_obj):
    mesh = unfold_obj.data
    lookup = {
        tuple(sorted((int(e.vertices[0]), int(e.vertices[1])))): int(e.index)
        for e in mesh.edges
    }
    result = {int(e.index): [] for e in mesh.edges}

    for poly in mesh.polygons:
        for key in poly.edge_keys:
            idx = lookup.get(tuple(sorted(key)))
            if idx is not None:
                result[idx].append(int(poly.index))

    return result
