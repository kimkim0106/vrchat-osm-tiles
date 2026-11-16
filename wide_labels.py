#!/usr/bin/env python3
"""wide_labels.py - 広域の地図 (regions.json の "wideLabels") に載せる地名の点を、国土数値情報から作る。

  python wide_labels.py <N03-20260101.geojson> <N03-20260101_prefecture.geojson> <N02-25_Station.geojson> [-o labels/wide_japan.json]

出力はリポジトリに入れる (Actions では国土数値情報を取らない。元データは全国の行政区域だけで 800 MB ある)。
vmt_bake.py --wide-labels がこれを読み、地名の候補にする (種類 kind ごとの字の大きさと優先度は vmt_bake.py の WIDE_KINDS)。

- 都道府県 (pref)      N03 の都道府県の面 (_prefecture.geojson)。点は面積で重みを付けた重心 (それが陸の上になければ、
                       いちばん大きい面 (島) の内側の真ん中)。市区町村の面を寄せ集めると本島が市町村ごとに分かれていて、
                       沖縄県が西表島に出たので、都道府県の面を使う
- 政令市 (designated)  行政区のある市。点は行政区の面から都道府県と同じ決め方
- 県庁所在地 (capital) OFFICES (pref_regions.py) の県庁所在地。政令市ならそちら (東京都は特別区なので無い)
- 市・特別区 (city)    名前が「市」で終わるものと東京都の「区」
- 町・村 (town)        北方領土の 6 村 (色丹村・泊村・留夜別村・留別村・紗那村・蘂取村) も N03 に入っている
- 行政区 (ward)        政令市の区
  市区町村の点は、その市区町村でいちばん大きい面の内側の真ん中 (polylabel。縁からいちばん遠い点)
- 駅 (station_major / station)
                       N02 の駅。同じ駅名で STATION_MERGE_M 以内のものを 1 つにまとめ (事業者をまたぐ)、点はホームの線の
                       真ん中の平均。新幹線の駅か、乗り入れる路線が 3 つ以上なら station_major。order は路線の数の多い順
                       (駅グループのコード N02_005g でまとめると、東京駅が JR と丸ノ内線で 2 つに分かれた)
- 島 (island)          MANUAL (低いズームの OpenStreetMap のデータに無い島。北方領土・竹島・南鳥島・沖ノ鳥島・尖閣諸島)
- 小さい島 (island_minor)
                       MANUAL_MINOR (東京都の島嶼部の島。伊豆・小笠原の地図のため)。町村より後に置く (町村の名前を先に出す)。
                       島 1 つで 1 町村のもの (八丈島と八丈町など) は、名前の位置が重なって出られないので足さない
  POSITION の市区町村は、点を役場のある島にする (小笠原村はいちばん大きい面が硫黄島なので、そのままだと硫黄島に出る)

order は同じ kind の中で先に置く順 (小さいほど先)。市区町村は面積の大きい順、駅は路線の多い順。

データの出典 (出力の "attribution" に入れ、manifest の帰属表示に足す):
  「国土数値情報（行政区域データ）」（国土交通省）、「国土数値情報（鉄道データ）」（国土交通省）。どちらも CC BY 4.0。
"""
import argparse
import heapq
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pref_regions import OFFICES  # noqa: E402

ATTRIBUTION = [
    "「国土数値情報（行政区域データ）」（国土交通省）（https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html）を加工して作成",
    "「国土数値情報（鉄道データ）」（国土交通省）（https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html）を加工して作成",
]
# 手で足す島の名前 (名前, 経度, 緯度)。位置は島 (群島はその真ん中あたり) で、地理院地図で確かめたもの
MANUAL = [
    ("択捉島", 147.75, 44.95),
    ("国後島", 145.95, 44.10),
    ("色丹島", 146.75, 43.80),
    ("歯舞群島", 146.15, 43.45),
    ("竹島", 131.8687, 37.2414),
    ("尖閣諸島", 123.53, 25.75),
    ("南鳥島", 153.9806, 24.2867),
    ("沖ノ鳥島", 136.0819, 20.4256),
]
# 東京都の島嶼部の島 (名前, 経度, 緯度)。町村の名前と重ならないよう、町村の点 (島のまん中か役場) から少しずらしてある
MANUAL_MINOR = [
    ("新島", 139.265, 34.41), ("式根島", 139.215, 34.325), ("鳥島", 140.305, 30.483),
    ("聟島列島", 142.15, 27.65), ("父島", 142.205, 27.055), ("母島", 142.16, 26.64),
    ("北硫黄島", 141.235, 25.43), ("硫黄島", 141.32, 24.775), ("南硫黄島", 141.457, 24.23),
]
# 点を役場のある島にする市区町村 (コード -> 経度, 緯度)
POSITION = {
    "13421": (142.19, 27.09),  # 小笠原村 (村役場は父島)
}
SIMPLIFY_DEG = 0.0005      # 面を間引く幅 (約 50 m)。点を決めるだけなので粗くてよい
POLYLABEL_PRECISION = 0.002  # polylabel の精度 (約 200 m)
MAJOR_LINES = 3            # この数以上の路線が乗り入れる駅は station_major
STATION_MERGE_M = 500.0    # 同じ駅名でこの距離 (m) 以内のホームは 1 つの駅にする


# ---------------------------------------------------------------- 面の計算 (経度は緯度の cos を掛けて距離をそろえる)

def ring_area(ring):
    a = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i - 1]
        x2, y2 = ring[i]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def simplify(ring, tol):
    """Douglas-Peucker (閉じたリングは端点を残す)。"""
    if len(ring) <= 4:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = ring[i]
        bx, by = ring[j]
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            px, py = ring[k]
            d = abs(dy * (px - ax) - dx * (py - ay)) / ln if ln > 0 else math.hypot(px - ax, py - ay)
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    out = [p for p, k in zip(ring, keep) if k]
    return out if len(out) >= 4 else ring


def point_in_ring(x, y, ring):
    c = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx or dy:
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        if t > 1:
            ax, ay = bx, by
        elif t > 0:
            ax, ay = ax + dx * t, ay + dy * t
    return (px - ax) ** 2 + (py - ay) ** 2


def signed_dist(x, y, poly):
    """多角形 (外周と穴のリスト) の縁までの距離。中なら正、外なら負。"""
    inside = False
    best = float("inf")
    for ring in poly:
        if point_in_ring(x, y, ring):
            inside = not inside
        for i in range(len(ring)):
            ax, ay = ring[i - 1]
            bx, by = ring[i]
            best = min(best, seg_dist2(x, y, ax, ay, bx, by))
    return (1 if inside else -1) * math.sqrt(best)


def polylabel(poly, precision):
    """縁からいちばん遠い内側の点 (mapbox/polylabel と同じ方法)。"""
    xs = [p[0] for p in poly[0]]
    ys = [p[1] for p in poly[0]]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    w, h = maxx - minx, maxy - miny
    size = min(w, h)
    if size == 0:
        return minx, miny
    q = []

    def push(x, y, half):
        d = signed_dist(x, y, poly)
        heapq.heappush(q, (-(d + half * math.sqrt(2)), d, x, y, half))

    half = size / 2
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            push(x + half, y + half, half)
            y += size
        x += size
    # 重心も候補にする (細長い形で格子が粗いとき)
    a = ring_area(poly[0])
    if a:
        cx = cy = 0.0
        r = poly[0]
        for i in range(len(r)):
            x1, y1 = r[i - 1]
            x2, y2 = r[i]
            f = x1 * y2 - x2 * y1
            cx += (x1 + x2) * f
            cy += (y1 + y2) * f
        best = (signed_dist(cx / (6 * a), cy / (6 * a), poly), cx / (6 * a), cy / (6 * a))
    else:
        best = (-float("inf"), minx, miny)
    bb = (signed_dist(minx + w / 2, miny + h / 2, poly), minx + w / 2, miny + h / 2)
    if bb[0] > best[0]:
        best = bb
    while q:
        neg_max, d, x, y, half = heapq.heappop(q)
        if d > best[0]:
            best = (d, x, y)
        if -neg_max - best[0] <= precision:
            continue
        half /= 2
        for ox in (-half, half):
            for oy in (-half, half):
                push(x + ox, y + oy, half)
    return best[1], best[2]


class Shape:
    """1 つの地物 (都道府県・市区町村) の面。座標は (経度 * cos(緯度0), 緯度) にしてある。"""

    def __init__(self, lat0):
        self.k = math.cos(math.radians(lat0))
        self.polys = []  # [(面積, [外周, 穴, ...])]

    def add(self, rings):
        rs = [simplify([(x * self.k, y) for x, y in r], SIMPLIFY_DEG) for r in rings]
        a = abs(ring_area(rs[0])) - sum(abs(ring_area(r)) for r in rs[1:])
        if a > 0:
            self.polys.append((a, rs))

    def area(self):
        return sum(a for a, _ in self.polys)

    def label_point(self, centroid_first):
        """centroid_first なら面積で重みを付けた重心 (陸の上なら)、そうでなければいちばん大きい面の polylabel。"""
        if not self.polys:
            return None
        if centroid_first:
            sx = sy = sa = 0.0
            for a, rs in self.polys:
                r = rs[0]
                ra = ring_area(r)
                if not ra:
                    continue
                cx = cy = 0.0
                for i in range(len(r)):
                    x1, y1 = r[i - 1]
                    x2, y2 = r[i]
                    f = x1 * y2 - x2 * y1
                    cx += (x1 + x2) * f
                    cy += (y1 + y2) * f
                sx += cx / (6 * ra) * a
                sy += cy / (6 * ra) * a
                sa += a
            if sa:
                x, y = sx / sa, sy / sa
                if any(signed_dist(x, y, rs) > 0 for _, rs in self.polys):
                    return x / self.k, y
        _, rs = max(self.polys, key=lambda p: p[0])
        x, y = polylabel(rs, POLYLABEL_PRECISION)
        return x / self.k, y


def polygons(geom):
    if geom is None:
        return []
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


# ---------------------------------------------------------------- 行政区域

def pref_labels(path):
    print(f"読む: {path}", flush=True)
    with open(path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    prefs = {}
    for ft in feats:
        pref = ft["properties"].get("N03_001")
        polys = polygons(ft["geometry"])
        if not pref or not polys:
            continue
        for poly in polys:
            prefs.setdefault(pref, Shape(polys[0][0][0][1])).add(poly)
    out = []
    for pref, sh in prefs.items():
        x, y = sh.label_point(True)
        out.append({"name": pref, "kind": "pref", "lon": x, "lat": y, "order": 0})
    print(f"  都道府県 {len(out)}", flush=True)
    return out


def admin_labels(path):
    print(f"読む: {path}", flush=True)
    with open(path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    print(f"  {len(feats)} 面", flush=True)
    capitals = {(pref, title + "市") for _, pref, title, _, _, _ in OFFICES if pref != "東京都"}
    cities, munis = {}, {}
    for ft in feats:
        p = ft["properties"]
        pref, city, ward, code = p.get("N03_001"), p.get("N03_004"), p.get("N03_005"), p.get("N03_007")
        polys = polygons(ft["geometry"])
        if not pref or not polys:
            continue
        lat0 = polys[0][0][0][1]
        for poly in polys:
            if not city or not code or city.endswith("所属未定地"):
                continue
            if ward:
                cities.setdefault((pref, city), Shape(lat0)).add(poly)
                key, name, kind = code, ward, "ward"
            else:
                key, name = code, city
                if (pref, city) in capitals:
                    kind = "capital"
                elif city.endswith("市") or (pref == "東京都" and city.endswith("区")):
                    kind = "city"
                else:
                    kind = "town"
            m = munis.setdefault(key, {"name": name, "kind": kind, "pref": pref, "shape": Shape(lat0)})
            m["shape"].add(poly)
    out = []
    for (pref, city), sh in cities.items():
        x, y = sh.label_point(True)
        out.append({"name": city, "kind": "designated", "lon": x, "lat": y, "order": -sh.area()})
    for code, m in sorted(munis.items()):
        pt = POSITION.get(code) or m["shape"].label_point(False)
        if pt:
            out.append({"name": m["name"], "kind": m["kind"], "lon": pt[0], "lat": pt[1], "order": -m["shape"].area(),
                        "code": code})
    kinds = {}
    for e in out:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"  行政区域のラベル {len(out)} {kinds}", flush=True)
    return out


# ---------------------------------------------------------------- 駅

def station_labels(path):
    print(f"読む: {path}", flush=True)
    with open(path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    byname = {}
    for ft in feats:
        p = ft["properties"]
        c = ft["geometry"]["coordinates"]
        n = len(c)
        mid = c[n // 2] if n % 2 else [(c[n // 2 - 1][0] + c[n // 2][0]) / 2, (c[n // 2 - 1][1] + c[n // 2][1]) / 2]
        byname.setdefault(p["N02_005"], []).append((mid, (p["N02_004"], p["N02_003"]), p["N02_002"] == "1"))
    out = []
    for name, items in byname.items():
        # 同じ名前で STATION_MERGE_M 以内につながるホームを 1 つの駅にする (単連結)
        parent = list(range(len(items)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i][0], items[j][0]
                kx = 111320.0 * math.cos(math.radians(a[1]))
                if math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110540.0) <= STATION_MERGE_M:
                    parent[find(i)] = find(j)
        clusters = {}
        for i in range(len(items)):
            clusters.setdefault(find(i), []).append(items[i])
        for members in clusters.values():
            lines = len({m[1] for m in members})
            major = any(m[2] for m in members) or lines >= MAJOR_LINES
            out.append({"name": name + "駅", "kind": "station_major" if major else "station",
                        "lon": sum(m[0][0] for m in members) / len(members),
                        "lat": sum(m[0][1] for m in members) / len(members), "order": -lines})
    kinds = {}
    for e in out:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"  駅 {len(feats)} 件 -> ラベル {len(out)} {kinds}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description="広域の地図の地名の点を国土数値情報から作る")
    ap.add_argument("n03", help="N03 (行政区域) の全国の GeoJSON")
    ap.add_argument("n03_pref", help="N03 の都道府県の面の GeoJSON (N03-20260101_prefecture.geojson)")
    ap.add_argument("n02", help="N02 (鉄道) の駅の GeoJSON (N02-25_Station.geojson)")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "labels", "wide_japan.json"))
    args = ap.parse_args()
    labels = pref_labels(args.n03_pref) + admin_labels(args.n03) + station_labels(args.n02)
    labels += [{"name": n, "kind": "island", "lon": x, "lat": y, "order": 0} for n, x, y in MANUAL]
    labels += [{"name": n, "kind": "island_minor", "lon": x, "lat": y, "order": 0} for n, x, y in MANUAL_MINOR]
    for e in labels:
        e["lon"], e["lat"] = round(e["lon"], 5), round(e["lat"], 5)
        e["order"] = round(e["order"], 6)
    labels.sort(key=lambda e: (e["kind"], e["order"], e["name"], e["lon"], e["lat"]))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write('{"format": "wide-labels", "version": 1,\n "attribution": ' + json.dumps(ATTRIBUTION, ensure_ascii=False)
                + ',\n "sources": ' + json.dumps([os.path.basename(args.n03), os.path.basename(args.n03_pref), os.path.basename(args.n02)])
                + ',\n "labels": [\n')
        f.write(",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in labels))
        f.write("\n]}\n")
    print(f"{len(labels)} labels -> {args.out}")


if __name__ == "__main__":
    main()
