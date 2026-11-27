#!/usr/bin/env python3
"""wide_regions.py - 広域の地図 (日本全体と地方ごと) の地域を regions.json に書く。

  python wide_regions.py <N03-20260101.geojson>             regions.json を書き換える (広域の地域を足す・直す。ほかの地域はそのまま)
  python wide_regions.py <N03-20260101.geojson> --dry-run   書き換えずに、範囲とズームを出す

範囲は国土数値情報の行政区域 (N03) の市区町村の面から決める (所属未定地 (コードの下 3 桁が 000) は数えない)。
地方の分け方は設定タブレットの地方 (Unity の VmtTabletSetup.RegionGroupNames) に合わせ、九州・沖縄だけ 2 つに分ける。
  - 関東は小笠原村 (13421。南鳥島・沖ノ鳥島を含む) を入れない (日本全体の地図にだけ出る)
  - 奄美群島 (奄美市と大島郡) とトカラ列島 (十島村) は沖縄の地図に入れる。沖縄の地図は横幅で大きさが決まるので、
    北へ伸ばしても縮まない (九州に入れると九州が細長くなり、1 枚 2.75 m → 1.57 m)
  - 東京都の島嶼部 (伊豆諸島と小笠原諸島) の地図を別に作る (利用者の案、2026-11-17)。南鳥島と沖ノ鳥島は入れない
    (入れると床の 1 m が約 97 km になり、日本全体 (154 km) とあまり変わらない)。小笠原村の中のこの 2 島は面ごとに外す
ズーム: 床は 20 m x 22 m。base のズーム z のタイル数を w x h として、1 枚の大きさは min(20 / w, 22 / h) m。
  日本全体は z7 (12 x 12、1.67 m)。地方は 1 枚が MIN_TILE m 以上になるいちばん大きいズーム。焼くのは z と z + 1 (足元の詳細)。
  tileSize (1 枚の大きさ) は記録用 (Unity で床マップを作るときに渡す)。焼くときには使わない。

地名は wide_labels.py の点 (labels/wide_japan.json)、境界線は都道府県境 (admin_level 4) の陸上の線だけ、
1 タイルのラベル頂点の上限は LABEL_VERTS (都市の床の 6 倍。関東を含むタイルは自治体の名前だけで 1,200 字ほどになる)。
シートごとの .bin (binZooms) は作らない (Pages の容量を空けるため。関東地方で PNG 23 MB に対して .bin 25 MB)。
String Loading で取るのは R2 のまとめた .bin (binGroups。PNG から作る) だけで、R2 を読めない人は PNG を取る。
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS = os.path.join(HERE, "regions.json")
FLOOR_W, FLOOR_H = 20.0, 22.0
MIN_TILE = 1.4
JAPAN_ZOOM = 7
LABEL_VERTS = 98280  # 2026-11-24 に 49140 から上げた (日本全体の関東・近畿のタイルが埋まり、山・川を足すと駅が落ちるため)
AMAMI = ["46222"] + [f"465{i}" for i in range(23, 36)]  # 奄美市と大島郡の町村
TOKARA = ["46304"]                                      # 十島村
OGASAWARA = ["13421"]
TOKYO_ISLANDS = ["13361", "13362", "13363", "13364", "13381", "13382", "13401", "13402", "13421"]  # 大島町〜小笠原村


def not_far_islands(b):
    """南鳥島 (東経 150 度より東) と沖ノ鳥島 (北緯 21 度より南) の面でない。"""
    return b[2] < 150.0 and b[1] > 21.0


# (name, title, 都道府県コード, 入れない市区町村, 足す市区町村[, 面を選ぶ関数])
WIDE = [
    ("wide-japan", "日本全体", [f"{i:02d}" for i in range(1, 48)], [], []),
    ("wide-hokkaido", "北海道地方", ["01"], [], []),
    ("wide-tohoku", "東北地方", ["02", "03", "04", "05", "06", "07"], [], []),
    ("wide-kanto", "関東地方", ["08", "09", "10", "11", "12", "13", "14"], OGASAWARA, []),
    ("wide-chubu", "中部地方", ["15", "16", "17", "18", "19", "20", "21", "22", "23"], [], []),
    ("wide-kinki", "近畿地方", ["24", "25", "26", "27", "28", "29", "30"], [], []),
    ("wide-chugoku", "中国地方", ["31", "32", "33", "34", "35"], [], []),
    ("wide-shikoku", "四国地方", ["36", "37", "38", "39"], [], []),
    ("wide-kyushu", "九州地方", ["40", "41", "42", "43", "44", "45", "46"], AMAMI + TOKARA, []),
    ("wide-okinawa", "沖縄・奄美", ["47"], [], AMAMI + TOKARA),
    ("wide-izu-ogasawara", "伊豆・小笠原", [], [], TOKYO_ISLANDS, not_far_islands),
]


def tile_x(lon, z):
    return (lon + 180.0) / 360.0 * (1 << z)


def tile_y(lat, z):
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (1 << z)


def tiles(bbox, z):
    """vmt_bake.py と同じ数え方 (z + 1 の範囲を覆う z のタイル)。"""
    w, s, e, n = bbox
    x0, x1 = int(math.floor(tile_x(w, z + 1))) >> 1, int(math.floor(tile_x(e, z + 1))) >> 1
    y0, y1 = int(math.floor(tile_y(n, z + 1))) >> 1, int(math.floor(tile_y(s, z + 1))) >> 1
    return x1 - x0 + 1, y1 - y0 + 1


def muni_boxes(path):
    """{市区町村コード: [面ごとの範囲 [西, 南, 東, 北], ...]}"""
    print(f"読む: {path}", flush=True)
    with open(path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    boxes = {}
    for ft in feats:
        code = ft["properties"].get("N03_007")
        g = ft["geometry"]
        if not code or code.endswith("000") or not g:
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            xs = [p[0] for p in poly[0]]
            ys = [p[1] for p in poly[0]]
            boxes.setdefault(code, []).append([min(xs), min(ys), max(xs), max(ys)])
    return boxes


def wide_regions(boxes):
    out = []
    for name, title, prefs, drop, add, *piece in WIDE:
        keep = piece[0] if piece else (lambda b: True)
        bs = [b for c, bb in boxes.items() if (c[:2] in prefs and c not in drop) or c in add for b in bb if keep(b)]
        # 外へ 0.001 度 (約 100 m) 広げて小数 4 桁に丸める (端の島がタイルの外に出ないように)
        bbox = [math.floor((min(b[0] for b in bs) - 0.001) * 1e4) / 1e4, math.floor((min(b[1] for b in bs) - 0.001) * 1e4) / 1e4,
                math.ceil((max(b[2] for b in bs) + 0.001) * 1e4) / 1e4, math.ceil((max(b[3] for b in bs) + 0.001) * 1e4) / 1e4]
        if name == "wide-japan":
            z = JAPAN_ZOOM
        else:
            z = max(z for z in range(JAPAN_ZOOM, 12) if min(FLOOR_W / tiles(bbox, z)[0], FLOOR_H / tiles(bbox, z)[1]) >= MIN_TILE)
        w, h = tiles(bbox, z)
        size = math.floor(min(FLOOR_W / w, FLOOR_H / h) * 1000) / 1000
        out.append({
            "name": name, "title": title, "wide": True, "area": "japan",
            "bbox": ",".join(f"{v:.4f}" for v in bbox),
            "zooms": f"{z}-{z + 1}", "tiles": [w, h], "tileSize": size,
            "lang": "ja", "wideLabels": "labels/wide_japan.json", "boundaryLevels": [4], "labelVerts": LABEL_VERTS,
            # まず Release に保存するだけにする (配る前に大きさと見た目を確かめる)。確かめたら dists に移す
            "dists": [], "releaseOnly": ["v3"],
            "binGroups": {"dists": ["v3"], "zooms": [z, z + 1]},
        })
        print(f"{name:14s} {title:6s} z{z}-{z + 1} {w:2d} x {h:2d} 枚, 1 枚 {size:.2f} m "
              f"(床 {w * size:.1f} x {h * size:.1f} m), bbox {out[-1]['bbox']}")
    return out


def main():
    ap = argparse.ArgumentParser(description="広域の地図の地域を regions.json に書く")
    ap.add_argument("n03", help="N03 (行政区域) の全国の GeoJSON")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    wide = wide_regions(muni_boxes(args.n03))
    if args.dry_run:
        return
    with open(REGIONS, encoding="utf-8") as f:
        data = json.load(f)
    old = {r["name"]: r for r in data["regions"]}
    names = {r["name"] for r in wide}
    regions = [r for r in data["regions"] if r["name"] not in names]
    for r in wide:
        prev = old.get(r["name"])
        if prev:  # 配る版は手で決めたものを残す (dists / releaseOnly)
            r["dists"], r["releaseOnly"] = prev.get("dists", r["dists"]), prev.get("releaseOnly", r["releaseOnly"])
            if not r["releaseOnly"]:
                del r["releaseOnly"]
        regions.append(r)
    data["regions"] = regions
    with open(REGIONS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(wide)} 地域を {REGIONS} に書いた")


if __name__ == "__main__":
    main()
