"""型紙のメッシュから、縫い代と糊代の線を起こす。

形そのものの計算は core.flatshape が持つ（bpy を使わない純粋な
幾何）。ここは、メッシュと設定を読んでそこへ渡し、結果を
「切る線」と「折る線」に分けて返す担当。

■ 単位はブレンダー単位（BU）のまま扱う

型紙は必ず実寸で刷る（build が拡大縮小を決してしない）。だから
紙の上のミリと実物のミリは同じ値になり、換算は1回で済む。
設定はミリで受け取り、ここで BU へ直してから幾何へ渡す。

■ タブは元メッシュの辺を単位にする

1本のシーム辺は、展開すると2つの島の境界辺になる。タブは
そのどちらか片方に付ける。両方に付ければ二重になり、貼れない。

どちらに付けるかは島番号の小さいほうを既定とする。置けなければ
反対側を試す。それでも駄目なら諦める。無理に置いたタブは、
切ると型紙自体を切ってしまう。

■ 消した印は「元メッシュの辺番号」で覚える

展開後の辺番号で覚えると、型紙を作り直した瞬間に全部消える。
展開結果は作り直しのたびに別物になるためである。元メッシュの
辺番号なら、シームと同じ場所に住むので一緒に生き残る。
元メッシュさえあれば、手で消した分まで含めて再現できる。

元メッシュを作り直す（リトポロジーする）と辺番号はずれる。
これはシームも同じ条件なので、弱点が増えるわけではない。
"""

import json

from .. import debug as _debug
from ..core import flatshape as _shape
from ..core import mapping as _mapping
from ..core import state as _state
from ..core import units as _units

# 消したタブを覚えるカスタムプロパティ（元オブジェクトに付く）。
TAB_OFF_PROP = "tsunfold_tab_off_json"


# ------------------------------------------------------------ 消した印の記録

def disabled_edges(source_obj):
    """タブを消してある元メッシュの辺番号の集合。"""
    if source_obj is None:
        return set()
    try:
        raw = source_obj.get(TAB_OFF_PROP, "[]")
        return {int(v) for v in json.loads(raw)}
    except Exception:
        _debug.swallowed("allowance.disabled_edges")
        return set()


def set_disabled_edges(source_obj, indices):
    """タブを消してある辺を書き戻す。"""
    if source_obj is None:
        return
    source_obj[TAB_OFF_PROP] = json.dumps(sorted(int(v) for v in indices))


def toggle_disabled(source_obj, source_edge_index, off=None):
    """1本の辺のタブを消す／戻す。戻り値は消した状態かどうか。"""
    current = disabled_edges(source_obj)
    index = int(source_edge_index)
    if off is None:
        off = index not in current
    if off:
        current.add(index)
    else:
        current.discard(index)
    set_disabled_edges(source_obj, current)
    return off


# ---------------------------------------------------------------- 島の輪

def island_loops(mesh, face_indices):
    """1つの島の境界を、向きを揃えた輪にして返す。

    輪は (x, y, 辺番号) の並び。辺番号を持ったまま輪にするのは、
    あとで「この境界辺は元のどのシーム辺か」を引くため。座標だけで
    つなぐと、そこが失われる。
    """
    valid = {
        int(fi) for fi in face_indices
        if 0 <= int(fi) < len(mesh.polygons)
    }

    counts = {}
    for fi in valid:
        for pair in mesh.polygons[fi].edge_keys:
            key = tuple(sorted(int(v) for v in pair))
            counts[key] = counts.get(key, 0) + 1

    edge_index = {
        tuple(sorted((int(e.vertices[0]), int(e.vertices[1])))): int(e.index)
        for e in mesh.edges
    }

    # 境界＝島の中で1つの面にしか接していない辺。
    border = [key for key, n in counts.items() if n == 1]
    if not border:
        return []

    links = {}
    for va, vb in border:
        links.setdefault(va, []).append(vb)
        links.setdefault(vb, []).append(va)

    used = set()
    loops = []

    for start in list(links):
        while True:
            first = None
            for candidate in links.get(start, ()):
                if tuple(sorted((start, candidate))) not in used:
                    first = candidate
                    break
            if first is None:
                break

            chain = [start]
            used.add(tuple(sorted((start, first))))
            previous = start
            current = first
            closed = False

            while current != start:
                chain.append(current)
                nxt = None
                for candidate in links.get(current, ()):
                    if candidate == previous:
                        continue
                    if tuple(sorted((current, candidate))) not in used:
                        nxt = candidate
                        break
                if nxt is None:
                    break
                used.add(tuple(sorted((current, nxt))))
                previous = current
                current = nxt
            else:
                closed = True

            if closed and len(chain) >= 3:
                loops.append(chain)

    out = []
    for chain in loops:
        # ここでは向きを直さない。外周か穴かは、ほかの輪と
        # 見比べないと決まらないため。_orient_rings がまとめて揃える。
        ring = []
        count = len(chain)
        for i in range(count):
            va = chain[i]
            vb = chain[(i + 1) % count]
            ring.append((
                mesh.vertices[va].co.x,
                mesh.vertices[va].co.y,
                edge_index.get(tuple(sorted((va, vb)))),
            ))
        out.append(ring)

    return _orient_rings(out)


def _orient_rings(rings):
    """輪の集まりを、外周＝反時計回り・穴＝時計回りに揃える。

    どちらが外周でどちらが穴かの判断は core.flatshape が持つ。
    ここでやると同じ判断が2箇所になり、いつか食い違う。
    """
    if not rings:
        return []

    shapes = [[(x, y) for x, y, _e in ring] for ring in rings]
    windings = _shape.loop_windings(shapes)

    out = []
    for ring, pts, want_ccw in zip(rings, shapes, windings):
        is_ccw = _shape.loop_area(pts) >= 0.0
        out.append(ring if is_ccw == want_ccw else _reverse_ring(ring))
    return out


def _reverse_ring(ring):
    """輪の向きを返す。辺番号は「次の点との間の辺」なので付け替える。"""
    count = len(ring)
    flipped = list(reversed(ring))
    return [
        (flipped[i][0], flipped[i][1], flipped[(i + 1) % count][2])
        for i in range(count)
    ]


def ring_segments(ring):
    """輪を (ax, ay, bx, by) の辺の並びへ。"""
    count = len(ring)
    return [
        (ring[i][0], ring[i][1], ring[(i + 1) % count][0],
         ring[(i + 1) % count][1])
        for i in range(count)
    ]


# ------------------------------------------------------------------ 組み立て

class Allowance:
    """縫い代と糊代の結果。座標は型紙オブジェクトのローカル（BU）。

    cut       切る線。輪郭と同じ太さで実線にする
    fold      折る線。破線にする。ここを切られると台無しになる
    suppress  元の外周から外す辺（頂点番号の組）。タブの根元は
              折り線なので、元の実線が残っていると切られてしまう
    stitched  元の外周を縫い線として破線にするか。縫い代を付けた
              ときだけ真。外側が裁断線、内側が縫い線になる
    """

    __slots__ = ("cut", "fold", "suppress", "stitched")

    def __init__(self, cut=None, fold=None, suppress=None, stitched=False):
        self.cut = cut or []
        self.fold = fold or []
        self.suppress = suppress or set()
        self.stitched = bool(stitched)

    def __bool__(self):
        return bool(self.cut or self.fold or self.suppress)


def settings(scene):
    """設定を読む。値はミリ。"""
    return {
        "tab": bool(getattr(scene, "tsunfold_tab_enable", False)),
        "tab_mm": float(getattr(scene, "tsunfold_tab_width_mm", 6.0)),
        "seam": bool(getattr(scene, "tsunfold_seam_enable", False)),
        "seam_mm": float(getattr(scene, "tsunfold_seam_width_mm", 10.0)),
    }


def enabled(scene):
    conf = settings(scene)
    return conf["tab"] or conf["seam"]


def _seam_source_edges(source_obj):
    """元メッシュで、シームが立っている辺番号の集合。"""
    return {
        int(e.index) for e in source_obj.data.edges if bool(e.use_seam)
    }


def _tab_candidates(mesh, rings_by_island, flat_edge_source, seam_edges):
    """シーム辺ごとに、タブを置ける場所の一覧を作る。

    戻り値は 元の辺番号 -> [(島番号, 辺の座標)] 。ふつうは2つで、
    その片方だけにタブを置く。1つしかない辺は、貼り合わせる相手が
    いないので（元の立体の縁）タブを付けない。
    """
    found = {}

    for island_index, rings in rings_by_island.items():
        for ring in rings:
            count = len(ring)
            for i in range(count):
                edge_index = ring[i][2]
                if edge_index is None:
                    continue
                if not (0 <= edge_index < len(flat_edge_source)):
                    continue

                source_edge = int(flat_edge_source[edge_index])
                if source_edge not in seam_edges:
                    continue

                ax, ay, _e = ring[i]
                bx, by, _e2 = ring[(i + 1) % count]
                found.setdefault(source_edge, []).append(
                    (island_index, (ax, ay, bx, by), edge_index)
                )

    return found


def build(context, source_obj, unfold_obj):
    """縫い代と糊代の線を作る。結果は使い回す。

    タブを1枚ずつ置けるか確かめるので、面320の型紙で 340ms かかる。
    書き出しのときだけなら構わないが、画面にも出すので毎フレームは
    無理である。形も設定も変わらなければ結果は同じなので、控えて
    おく。

    キーに matrix_world は入れない。入れると、型紙を動かすだけで
    毎フレーム作り直しになる。ビューを回すだけでは再現せず、
    「移動したときだけ極端に重い」という分かりにくい症状になる
    （実際にやった）。形はローカルで計算しているので、位置は
    結果に影響しない。
    """
    if source_obj is None or unfold_obj is None:
        return Allowance()
    if unfold_obj.type != 'MESH':
        return Allowance()

    conf = settings(context.scene)
    mesh = unfold_obj.data

    key = (
        "allowance",
        source_obj.name,
        mesh.name,
        len(mesh.vertices),
        len(mesh.edges),
        len(mesh.polygons),
        conf["tab"], round(conf["tab_mm"], 4),
        conf["seam"], round(conf["seam_mm"], 4),
        tuple(sorted(disabled_edges(source_obj))),
        int(_state.epoch),
    )

    found = _state.get(key)
    if found is not None:
        return found

    made = _build(context, source_obj, unfold_obj)
    _state.store(key, made)
    return made


def _build(context, source_obj, unfold_obj):
    """本体。タブは、元メッシュのシーム辺1本につき1枚。

    置く側は島番号の小さいほうを既定とし、置けなければ反対側を
    試す。どちらも駄目なら諦める。無理に置いたタブは、切ると
    型紙自体を切ってしまう。
    """
    from ..core import geometry as _geometry

    empty = Allowance()

    if source_obj is None or unfold_obj is None:
        return empty
    if unfold_obj.type != 'MESH':
        return empty

    scene = context.scene
    conf = settings(scene)
    if not (conf["tab"] or conf["seam"]):
        return empty

    mesh = unfold_obj.data
    if not mesh.polygons:
        return empty

    tab_bu = _units.scene_mm_to_bu(scene, conf["tab_mm"])
    seam_bu = _units.scene_mm_to_bu(scene, conf["seam_mm"])
    gap_bu = _units.scene_mm_to_bu(scene, _shape.TAB_GAP_MM)
    min_bu = _units.scene_mm_to_bu(scene, _shape.TAB_MIN_MM)

    islands = _geometry.face_islands(mesh)
    rings_by_island = {}
    for index, faces in enumerate(islands):
        rings = island_loops(mesh, faces)
        if rings:
            rings_by_island[index] = rings

    if not rings_by_island:
        return empty

    # 型紙のすべての境界線。タブが何かに触れていないかを見るため。
    # マス目へ振り分けておく。総当たりだと辺の数の2乗に効き、
    # 面320の型紙で 2.2 秒かかった。
    obstacles = _shape.SegmentIndex(max(tab_bu, seam_bu, 1e-6) * 4.0)
    for rings in rings_by_island.values():
        for ring in rings:
            obstacles.extend(ring_segments(ring))

    cut = []
    fold = []
    suppress = set()

    # --- 縫い代 --------------------------------------------------------
    if conf["seam"] and seam_bu > 0.0:
        for rings in rings_by_island.values():
            for ring in rings:
                points = [(x, y) for x, y, _e in ring]
                grown = _shape.offset_loop(points, seam_bu)
                count = len(grown)
                for i in range(count):
                    x0, y0 = grown[i]
                    x1, y1 = grown[(i + 1) % count]
                    cut.append((x0, y0, x1, y1))

    # --- 糊代 ----------------------------------------------------------
    if conf["tab"] and tab_bu > 0.0:
        off = disabled_edges(source_obj)
        seam_edges = _seam_source_edges(source_obj) - off
        flat_edge_source = _mapping.flat_edge_to_source(unfold_obj)

        candidates = _tab_candidates(
            mesh, rings_by_island, flat_edge_source, seam_edges
        )

        for source_edge in sorted(candidates):
            spots = candidates[source_edge]
            if len(spots) < 2:
                # 貼り合わせる相手がいない。元の立体の縁。
                continue

            spots = sorted(spots, key=lambda item: item[0])

            quad = None
            chosen = None
            for island_index, (ax, ay, bx, by), edge_index in spots[:2]:
                quad = _shape.fit_tab(
                    ax, ay, bx, by, tab_bu, obstacles,
                    minimum=min_bu, gap=gap_bu,
                    skip=(ax, ay, bx, by),
                )
                if quad is not None:
                    chosen = (island_index, edge_index)
                    break

            if quad is None:
                continue

            tab_edges = _shape.tab_cut_edges(quad)
            cut.extend(tab_edges)
            fold.append(_shape.tab_fold_edge(quad))

            # 置いたタブも、次のタブにとっては障害になる。
            obstacles.extend(tab_edges)

            # タブの根元は折り線。元の実線が残っていると切られる。
            edge = mesh.edges[chosen[1]]
            suppress.add(
                tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
            )

    return Allowance(
        cut=cut,
        fold=fold,
        suppress=suppress,
        stitched=bool(conf["seam"] and seam_bu > 0.0),
    )
