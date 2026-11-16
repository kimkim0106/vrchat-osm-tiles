#!/usr/bin/env python3
"""pref_regions.py - 47 都道府県の県庁所在地の地域を regions.json に書く (都道府県庁を中心にする)。

  python pref_regions.py               regions.json を書き換える (県庁の地域を足す・範囲を直す。ほかの地域はそのまま)
  python pref_regions.py --dry-run     書き換えずに、変わる地域を出す
  python pref_regions.py --check-areas Geofabrik の地域の境界 (.poly) を取ってきて、範囲がどの地域にかかるかを数える
                                       (AREAS を決めるときに使う。ネットワークが要る)

範囲 (今までの地域と同じ決め方。床マップは z13 で 10 x 11 枚 = 床 20 m x 22 m):
  県庁の z13 のタイル座標を (X, Y) として、横は round(X) - 5 から 10 枚、縦は floor(Y) - 5 から 11 枚。
  bbox はその四辺を 0.25 枚ずつ内側に寄せた経緯度 (小数 4 桁)。県庁は中心から最大で半タイル (約 2.4 km) ずれる。
  前からある 10 地域 (STATION_CENTERED) は駅中心のまま、bbox と area を変えない (pref / office / center だけ書く)。

地域の名前 (`name`) は県庁所在地のローマ字 (今までの 10 地域と同じ。URL の一部なので変えない)。
`pref` (都道府県名) と `office` (県庁の [経度, 緯度]) は記録用で、焼くときには使わない。

一度焼いた地域は、ここで範囲を直しても焼き直さない (Release にあるものを配り続ける)。焼き直すには Actions を手動実行して
`rebuild` に地域名を入れる。焼き直した地域を使うワールドは、新しい manifest で床マップを作り直して上げ直す。
"""
import argparse
import json
import math
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS = os.path.join(HERE, "regions.json")

# (JIS コード, 都道府県, 県庁所在地 (title), name, 経度, 緯度)
# 都道府県庁の位置は OpenStreetMap (2026-11-16 に Overpass で取得。本庁舎の建物か townhall の点)。
OFFICES = [
    (1, "北海道", "札幌", "sapporo", 141.34686, 43.06430),
    (2, "青森県", "青森", "aomori", 140.74019, 40.82447),
    (3, "岩手県", "盛岡", "morioka", 141.15229, 39.70369),
    (4, "宮城県", "仙台", "sendai", 140.87207, 38.26858),
    (5, "秋田県", "秋田", "akita", 140.10238, 39.71862),
    (6, "山形県", "山形", "yamagata", 140.36343, 38.24067),
    (7, "福島県", "福島", "fukushima", 140.46778, 37.75011),
    (8, "茨城県", "水戸", "mito", 140.44691, 36.34190),
    (9, "栃木県", "宇都宮", "utsunomiya", 139.88356, 36.56581),
    (10, "群馬県", "前橋", "maebashi", 139.06067, 36.39103),
    (11, "埼玉県", "さいたま", "saitama", 139.64905, 35.85703),
    (12, "千葉県", "千葉", "chiba", 140.12283, 35.60449),
    (13, "東京都", "東京", "tokyo", 139.69171, 35.68950),
    (14, "神奈川県", "横浜", "yokohama", 139.64231, 35.44792),
    (15, "新潟県", "新潟", "niigata", 139.02343, 37.90269),
    (16, "富山県", "富山", "toyama", 137.21130, 36.69529),
    (17, "石川県", "金沢", "kanazawa", 136.62572, 36.59465),
    (18, "福井県", "福井", "fukui", 136.22172, 36.06522),
    (19, "山梨県", "甲府", "kofu", 138.56849, 35.66411),
    (20, "長野県", "長野", "nagano", 138.18089, 36.65131),
    (21, "岐阜県", "岐阜", "gifu", 136.72382, 35.39134),
    (22, "静岡県", "静岡", "shizuoka", 138.38303, 34.97679),
    (23, "愛知県", "名古屋", "nagoya", 136.90651, 35.18022),
    (24, "三重県", "津", "tsu", 136.50893, 34.73021),
    (25, "滋賀県", "大津", "otsu", 135.86918, 35.00437),
    (26, "京都府", "京都", "kyoto", 135.75561, 35.02125),
    (27, "大阪府", "大阪", "osaka", 135.51988, 34.68633),
    (28, "兵庫県", "神戸", "kobe", 135.18386, 34.69170),
    (29, "奈良県", "奈良", "nara", 135.83286, 34.68515),
    (30, "和歌山県", "和歌山", "wakayama", 135.16758, 34.22602),
    (31, "鳥取県", "鳥取", "tottori", 134.23833, 35.50333),
    (32, "島根県", "松江", "matsue", 133.05080, 35.47157),
    (33, "岡山県", "岡山", "okayama", 133.93492, 34.66175),
    (34, "広島県", "広島", "hiroshima", 132.45999, 34.39629),
    (35, "山口県", "山口", "yamaguchi", 131.47140, 34.18586),
    (36, "徳島県", "徳島", "tokushima", 134.55945, 34.06577),
    (37, "香川県", "高松", "takamatsu", 134.04397, 34.34014),
    (38, "愛媛県", "松山", "matsuyama", 132.76567, 33.84161),
    (39, "高知県", "高知", "kochi", 133.53092, 33.55971),
    (40, "福岡県", "福岡", "fukuoka", 130.41810, 33.60632),
    (41, "佐賀県", "佐賀", "saga", 130.29905, 33.24947),
    (42, "長崎県", "長崎", "nagasaki", 129.86761, 32.74998),
    (43, "熊本県", "熊本", "kumamoto", 130.74174, 32.79041),
    (44, "大分県", "大分", "oita", 131.61254, 33.23833),
    (45, "宮崎県", "宮崎", "miyazaki", 131.42388, 31.91104),
    (46, "鹿児島県", "鹿児島", "kagoshima", 130.55801, 31.56019),
    (47, "沖縄県", "那覇", "naha", 127.68098, 26.21236),
]

# planetiler の --area (Geofabrik の地域)。都道府県から決まる地域で、範囲に隣の地域の陸地が入るものは japan (全国)。
# 1 つの地域だけ取ると、隣の地域の陸地は海岸線だけで道路などが無くなる (前の徳島の淡路島)。
#   岐阜 = 三重の北端 (いなべ) / 鳥取 = 兵庫の西端 / 岡山 = 香川の直島 / 高松 = 岡山の玉野
#   (駅中心のままの名古屋は japan、徳島は shikoku (淡路島の道路などが無い) のまま。下の STATION_CENTERED)
# 津 (伊勢湾) と松山 (瀬戸内海) は隣の地域の .poly にかかるが海だけなので、そのまま。
GEOFABRIK = {
    "hokkaido": [1],
    "tohoku": [2, 3, 4, 5, 6, 7],
    "kanto": [8, 9, 10, 11, 12, 13, 14],
    "chubu": [15, 16, 17, 18, 19, 20, 21, 22, 23],
    "kansai": [24, 25, 26, 27, 28, 29, 30],
    "chugoku": [31, 32, 33, 34, 35],
    "shikoku": [36, 37, 38, 39],
    "kyushu": [40, 41, 42, 43, 44, 45, 46, 47],
}
JAPAN = {"gifu", "tottori", "okayama", "takamatsu"}

# 駅を中心にしたまま変えない地域 (県庁所在地の地域より前からあるもの)。bbox と area は regions.json のものをそのまま使う。
# 範囲を変えると、manifest を焼き込んだ公開中のワールドで床が崩れる (タイルの並びが変わる)。ワールドが実行時に manifest を
# 読むようになったら、県庁中心に焼き直してよい (そのときはここから外して rebuild)。
STATION_CENTERED = {
    "tokyo": "東京駅", "kobe": "三ノ宮駅", "tokushima": "徳島駅", "osaka": "大阪駅", "nagoya": "名古屋駅",
    "fukuoka": "博多駅", "sapporo": "札幌駅", "sendai": "仙台駅", "hiroshima": "広島駅", "niigata": "新潟駅",
}

# Pages に置く地域 (今までどおり。ほかは R2。Pages はサイト 1 GB まで)
PAGES = {"tokyo", "kobe", "tokushima"}

# 新しく足す地域の既定 (今の地域と同じ)
DEFAULTS = {
    "zooms": "10-14",
    "lang": "ja",
    "binZooms": [13],
    "dists": ["v3"],
    "binGroups": {"dists": ["v3"], "zooms": [13, 14]},
}

Z = 13
COLS, ROWS = 10, 11
INSET = 0.25


def tile_x(lon):
    return (lon + 180.0) / 360.0 * 2 ** Z


def tile_y(lat):
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * 2 ** Z


def tile_lon(x):
    return x / 2 ** Z * 360.0 - 180.0


def tile_lat(y):
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / 2 ** Z))))


def bbox_of(lon, lat):
    """県庁を中心にした範囲 (西, 南, 東, 北) と、県庁の中心からのずれ (タイル)。"""
    x0 = round(tile_x(lon)) - COLS // 2
    y0 = math.floor(tile_y(lat)) - ROWS // 2
    w, e = tile_lon(x0 + INSET), tile_lon(x0 + COLS - INSET)
    n, s = tile_lat(y0 + INSET), tile_lat(y0 + ROWS - INSET)
    off = (tile_x(lon) - (x0 + COLS / 2), tile_y(lat) - (y0 + ROWS / 2))
    return (w, s, e, n), off


def area_of(jis, name):
    if name in JAPAN:
        return "japan"
    return next(a for a, codes in GEOFABRIK.items() if jis in codes)


def entry_for(office, old):
    jis, pref, title, name, lon, lat = office
    (w, s, e, n), _ = bbox_of(lon, lat)
    r = dict(old) if old else {"name": name, "title": title}
    r["name"] = name
    r.setdefault("title", title)
    r["pref"] = pref
    r["office"] = [lon, lat]
    if name in STATION_CENTERED and old:
        r["center"] = STATION_CENTERED[name]  # 範囲は駅中心のまま (bbox と area を変えない)
    else:
        r["area"] = area_of(jis, name)
        r["bbox"] = f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}"
    for k, v in DEFAULTS.items():
        r.setdefault(k, json.loads(json.dumps(v)))
    if name in PAGES:
        r.pop("host", None)
    else:
        r["host"] = "r2"
    # キーの並びをそろえる (読みやすさのため。中身は変わらない)
    order = ["name", "title", "pref", "office", "center", "area", "bbox", "zooms", "lang", "binZooms", "dists", "releaseOnly",
             "binGroups", "host"]
    return {k: r[k] for k in order if k in r} | {k: v for k, v in r.items() if k not in order}


def update(regions):
    by_name = {r["name"]: r for r in regions}
    pref_names = [o[3] for o in OFFICES]
    out = [entry_for(o, by_name.get(o[3])) for o in OFFICES]
    out += [r for r in regions if r["name"] not in pref_names]  # 県庁以外の地域はそのまま後ろに
    return out


def report(old, new):
    by_name = {r["name"]: r for r in old}
    for r in new:
        o = by_name.get(r["name"])
        if o is None:
            print(f"足す     {r['name']:11} {r.get('pref', ''):5} {r['bbox']}  {r['area']}")
            continue
        changed = [k for k in ("bbox", "area") if o.get(k) != r.get(k)]
        if changed:
            print(f"変える   {r['name']:11} {r.get('pref', ''):5} " +
                  ", ".join(f"{k} {o.get(k)} -> {r.get(k)}" for k in changed) + "  (焼き直すには rebuild)")


def read_poly(text):
    rings, cur, hole = [], None, False
    for line in text.split("\n")[1:]:
        line = line.strip()
        if not line:
            continue
        if line == "END":
            if cur is not None:
                rings.append((hole, cur))
                cur = None
            continue
        if cur is None:
            hole, cur = line.startswith("!"), []
            continue
        a, b = line.split()
        cur.append((float(a), float(b)))
    return rings


def _inside(ring, x, y):
    c = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def _contains(rings, x, y):
    return (any(_inside(r, x, y) for h, r in rings if not h)
            and not any(_inside(r, x, y) for h, r in rings if h))


def check_areas():
    """範囲の中に 30 x 30 の点を置き、Geofabrik のどの地域の .poly に入るかを数える。.poly は海にも少し広がっているので、
    2 つ以上の地域にかかったら、その点が陸か海かを地図で確かめて JAPAN に入れるか決める。"""
    polys = {}
    for a in GEOFABRIK:
        req = urllib.request.Request(f"https://download.geofabrik.de/asia/japan/{a}.poly",
                                     headers={"User-Agent": "vmt-region-tool/1.0"})
        with urllib.request.urlopen(req) as f:
            polys[a] = read_poly(f.read().decode())
    n = 30
    for jis, pref, title, name, lon, lat in OFFICES:
        if name in STATION_CENTERED:
            print(f"{jis:2} {name:11} 駅中心のまま (調べない)")
            continue
        (w, s, e, nn), off = bbox_of(lon, lat)
        count = {}
        for i in range(n):
            for j in range(n):
                px, py = w + (e - w) * (i + 0.5) / n, s + (nn - s) * (j + 0.5) / n
                for a, rings in polys.items():
                    if _contains(rings, px, py):
                        count[a] = count.get(a, 0) + 1
        mark = "  <- 2 地域" if len(count) > 1 else ""
        print(f"{jis:2} {name:11} {area_of(jis, name):8} ずれ ({off[0]:+.2f}, {off[1]:+.2f}) "
              f"{dict(sorted(count.items(), key=lambda kv: -kv[1]))}{mark}")


def main():
    ap = argparse.ArgumentParser(description="県庁所在地の地域を regions.json に書く")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-areas", action="store_true")
    args = ap.parse_args()
    if args.check_areas:
        check_areas()
        return
    with open(REGIONS, encoding="utf-8") as f:
        doc = json.load(f)
    new = update(doc["regions"])
    report(doc["regions"], new)
    if args.dry_run:
        return
    doc["regions"] = new
    with open(REGIONS, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{REGIONS} に {len(new)} 地域を書いた")


if __name__ == "__main__":
    sys.exit(main())
