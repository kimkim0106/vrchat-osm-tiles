#!/usr/bin/env python3
"""vmt_v4.py - VMT-DT v4: 地域をまたいで同じタイルを 1 回だけ焼き、1 か所に置く (vrchat-osm の Issue #19 の続き)。

将来、広域・地方・都市をシームレスに切り替えられるように、v4 はタイル (z, x, y) ごとに中身を 1 つに決める。

  - ズームごとの作り方: z7〜z11 (WIDE_MAX_ZOOM まで) は広域の作り方 (地名は国土数値情報の点、境界は県境だけ、日本語だけ)、
    z12 から上は都市の作り方 (OpenStreetMap の地名と POI、日本語と英語)。今は広域の地域が z7〜z11、都市の地域が z13・z14
    (CITY_ZOOMS。床が読むズームだけ) なので、地域の種類とズームが一致する (validate が確かめる)。
  - 持ち主: 焼いた地域は、焼いたタイルの一覧 (持ち物の一覧。Release の <地域>.own.json.gz) を残す。一覧にあるタイルは
    その地域が持ち主 (焼き直すときも同じタイルを焼く)。どの一覧にも無いタイルは、範囲に含む一覧の無い地域を
    (1) Pages の地域 ("host" が r2 でない) が先、(2) regions.json の並び、の順に並べた最初の地域が持ち主。
    持ち主だけがそのタイルを焼く (<持ち主>/own/。配るときは中身のハッシュの名前で置き直す)。焼いた地域の持ち物は変わらないので、地域を足しても、
    一部の地域だけ焼けて実行し直しても、焼いたタイルは変わらない (Release に地域があるかで決めると、焼けなかった地域の
    分を焼いた隣の地域の持ち物とみなして穴が空く。Codex のレビューで指摘)。
    Pages を先にするのは、信頼済みのドメインで読める地域 (東京) の床が、R2 (「Allow Untrusted URLs」が要る) の
    シートを指さないようにするため (焼いた後で、v4 はどの地域も R2 に置くことにした (build_site.DIST_HOSTS)。
    今は持ち主の順を決めるだけで、置き場所には効かない。焼いたタイルの持ち主は変えない)。
  - ラベル: 持ち主の境目で重ならないよう、地域をまたいでズームごとにまとめて置く。手順は 3 段:
      candidates  地域ごとに、持ち物のタイルのラベルの候補を MBTiles から集める (並行)
      labels      この実行で焼く地域の候補をすべて合わせて置く (1 回)。Release にある地域の置いたラベル (<地域>.labels.json.gz)
                  は動かさず、先に置いてあるものとして扱う。ラベルは基準点のあるタイルの持ち主の分になる
      bake        地域ごとに、持ち物のタイルを置いたラベルで焼き、シートにまとめる (並行)
    丁目のまとめと同名の除去の距離は、候補ごとにその緯度で測る (地域の中心の緯度ではなく)。
  - 焼いた形: site/v4/<地域>/own/ が持ち物 (manifest.json = シートの manifest (タイルは持ち物だけ)、sheets/、labels.json.gz)。
    Release (タグ v4) の <地域>.tar.gz はこの own/ と、置いたラベル <地域>.labels.json.gz。持ち主は焼く単位で、配る URL には出ない。
  - 配る形 (publish。2026-11-19 に地域ごとのディレクトリから変えた。利用者の判断): 地域をまたいで共有するファイルは
    ズームごとのディレクトリに、中身のハッシュの名前で置く (v4/sheets/<z>/<ハッシュ>.png / .bin、v4/bingroups/<z>/<ハッシュ>.bin)。
    同じ中身なら同じ URL、焼き直して中身が変われば別の URL になるので、古いワールドが読むファイルは上書きされない。
    床が読む manifest は v4/floors/<地域>/manifest.json (範囲のタイルを全部並べ、シートと まとめた .bin は絶対 URL。
    binGroupBase は "")。範囲の中に、配っていない地域が持つタイルがあると止まる。
使い方 (手元):
  python vmt_v4.py candidates --region tokyo --mbtiles tokyo=MapData/tokyo.mbtiles --out work/v4/cands
  python vmt_v4.py labels --cands work/v4/cands --out work/v4/plans
  python vmt_v4.py bake --region tokyo --mbtiles tokyo=MapData/tokyo.mbtiles --plans work/v4/plans --site site --tools TOOLS
  (配る形と索引は build_site.py --index-only が作る (中で publish を呼ぶ))
--inventory には、焼いた地域の持ち物の一覧をまとめたもの ({地域: [[z, x, y], ...]}、inventory コマンドが Release の
<地域>.own.json.gz から作る) を渡す。Actions は plan job で作ったものを、どの段にも同じものを渡す。無ければ、まだ焼いた地域が無い。
地域の定義 (regions.json の 1 件) は own/manifest.json の sourceRegion に残す (regions.json から消した地域の床の manifest も作れるように)。
"""
import argparse
import glob
import gzip
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_site as b  # noqa: E402


def _vb():
    """vmt_bake (numpy と Pillow が要る。Actions の plan job には入れていないので、要るときだけ読む)。"""
    import vmt_bake
    return vmt_bake


def lonlat_to_tile(lon, lat, z):
    """vmt_bake.lonlat_to_tile と同じ。"""
    n = 1 << z
    r = math.radians(lat)
    return int((lon + 180.0) / 360.0 * n), int((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n)

DIST = b.GLYPH_DIST
HASH_LEN = 16           # 配るファイルの名前にする中身の SHA-256 の桁数 (16 進)
CITY_ZOOMS = (13, 14)   # 都市の地域が焼くズーム (床の全体と足元)。z10〜z12 は今の床が読まないので焼かない
WIDE_MAX_ZOOM = 11      # これ以下のズームは広域の作り方、これより上は都市の作り方
# 焼いたタイルの中身を決める設定。同じズームのタイルは、どの地域が焼いても同じ設定でないといけない
STYLE_KEYS = ("lang", "binZooms", "boundaryLevels", "labelVerts", "wideLabels")
# 床が読む manifest に持ち主の manifest から写す項目 (持ち主どうしで同じであること)
# (字は文字コードで持つので、床の manifest には持ち主の sharedGlyphs を合わせて載せる。2026-11-20 より前に焼いた持ち主の
#  manifest には、焼いたときの字の一覧の記録 glyphSet もある。床の manifest には写さない)
COMMON_KEYS = ("format", "version", "width", "vertsPerChunk", "labelConfig", "labelCategories", "labelVertsPerPool",
               "styles", "attribution", "labelAttribution", "tileVersion", "tileFormat", "labelLanguages")


# ---------------------------------------------------------------- 範囲と持ち主

def zooms_of(region):
    """v4 で焼くズーム。"""
    if region.get("wide"):
        z0, z1 = (int(v) for v in region["zooms"].split("-"))
        return tuple(range(z0, z1 + 1))
    return CITY_ZOOMS


def levels_of(region):
    """vmt_bake.py --bbox と同じ決め方の、ズームごとの矩形 [{"z", "minX", "minY", "w", "h"}]。"""
    zs = zooms_of(region)
    w, s, e, n = (float(v) for v in region["bbox"].split(","))
    z1 = max(zs)
    x0, y0 = lonlat_to_tile(w, n, z1)
    x1, y1 = lonlat_to_tile(e, s, z1)
    out = []
    for z in zs:
        sh = z1 - z
        out.append({"z": z, "minX": x0 >> sh, "minY": y0 >> sh, "w": (x1 >> sh) - (x0 >> sh) + 1,
                    "h": (y1 >> sh) - (y0 >> sh) + 1})
    return out


def tiles_of(region):
    """範囲のタイル [(z, x, y)] (vmt_bake.py の並び)。"""
    return [(lv["z"], lv["minX"] + i, lv["minY"] + j) for lv in levels_of(region)
            for j in range(lv["h"]) for i in range(lv["w"])]


def v4_regions(regions):
    return [r for r in regions if b.wants_dist(r, DIST)]


def validate(regions):
    """v4 の地域の作り方がズームごとにそろっているか確かめる。"""
    rs = v4_regions(regions)
    for r in rs:
        zs = zooms_of(r)
        if r.get("wide") and max(zs) > WIDE_MAX_ZOOM:
            sys.exit(f"{r['name']}: 広域の地図は z{WIDE_MAX_ZOOM} まで (v4 はそれより上を都市の作り方にする)")
        if not r.get("wide") and min(zs) <= WIDE_MAX_ZOOM:
            sys.exit(f"{r['name']}: 都市の地域は z{WIDE_MAX_ZOOM + 1} から")
    for kind in (True, False):
        same = {json.dumps([r.get(k) for k in STYLE_KEYS]) for r in rs if bool(r.get("wide")) == kind}
        if len(same) > 1:
            sys.exit(f"v4 の{'広域' if kind else '都市'}の地域で {', '.join(STYLE_KEYS)} がそろっていない "
                     f"(同じタイルをどの地域が焼いても同じ中身にするため)")


def owners(regions, inventory):
    """{(z, x, y): 持ち主の地域の名前}。持ち物の一覧にあるタイルはその地域。残りは、一覧の無い地域を Pages → R2、
    regions.json の並びの順に並べて最初に含む地域。"""
    out = {}
    for name, tiles in sorted(inventory.items()):
        for t in tiles:
            t = tuple(t)
            if t in out:
                sys.exit(f"v4 のタイル {t} が {out[t]} と {name} の両方の持ち物の一覧にある")
            out[t] = name
    rs = [r for r in v4_regions(regions) if r["name"] not in inventory]
    for r in sorted(rs, key=lambda r: r.get("host", "pages") == "r2"):  # 安定なので並びは保つ
        for t in tiles_of(r):
            out.setdefault(t, r["name"])
    return out


def owned(regions, inventory, name):
    if name in inventory:
        return sorted((tuple(t) for t in inventory[name]), key=lambda t: (t[0], t[2], t[1]))
    own = owners(regions, inventory)
    region = next(r for r in regions if r["name"] == name)
    return [t for t in tiles_of(region) if own[t] == name]


def load_inventory(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_regions(path):
    with open(path, encoding="utf-8") as f:
        regions = json.load(f)["regions"]
    b.validate_regions(regions)
    validate(regions)
    return regions


def find(regions, name):
    r = next((r for r in regions if r["name"] == name), None)
    if r is None or not b.wants_dist(r, DIST):
        sys.exit(f"{name}: regions.json で v4 を作る地域ではない")
    return r


def dump_gz(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def load_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def label_setup(region, tools=None):
    """vmt_bake の LABEL_CONFIG をこの地域の作り方にし、言語の並びを返す。"""
    vb = _vb()
    wide = None
    if region.get("wide"):
        with open(os.path.join(HERE, region["wideLabels"]), encoding="utf-8") as f:
            wide = json.load(f)["labels"]
    langs = [b.GLYPH_LANGS.split(",")[0]] if wide is not None else b.GLYPH_LANGS.split(",")
    font = os.path.join(tools, "NotoSansCJK-Regular.ttc") if tools else None
    # format は 2 (名前の正規化 (半角カナを全角に、など) が形式 2 だけで効く。clean_label)
    vb.LABEL_CONFIG.update(font=font if font and os.path.exists(font) else None, lang=langs[0], enabled=True,
                           spacing=1.0, poi=wide is None, wide=wide, format=b.GLYPH_TILE_FORMAT, langs=langs)
    return langs


def mbtiles_for(region, given, tools, work, java_mem):
    path = dict(s.split("=", 1) for s in given).get(region["name"])
    return path or b.build_mbtiles(region, tools, work, java_mem)


# ---------------------------------------------------------------- 1. 候補

def candidates(region, mbtiles, tiles):
    """持ち物のタイルのラベルの候補。{"region", "wide", "langs", "zooms": {z: [言語ごとの候補のリスト]}}。"""
    langs = label_setup(region)
    vb = _vb()
    db = sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True)
    zooms = {}
    for z in zooms_of(region):
        mine = [(x, y) for (tz, x, y) in tiles if tz == z]
        per_lang = []
        for lang in langs:
            vb.LABEL_CONFIG["lang"] = lang
            if region.get("wide"):
                cands = vb.wide_candidates(db, z, mine) if mine else []
            else:
                cands = vb.label_candidates(db, z, mine) if mine else []
            per_lang.append(cands)
        zooms[str(z)] = per_lang
        print(f"{region['name']} z{z}: {len(mine)} tiles, candidates {[len(c) for c in per_lang]}", flush=True)
    return {"region": region["name"], "wide": bool(region.get("wide")), "langs": langs, "zooms": zooms}


# ---------------------------------------------------------------- 2. 配置

LABEL_FIELDS = ("name", "x", "y", "size", "margin", "cat", "pri", "rank", "kmin", "kmax")
# あるときだけ渡す欄 (道路番号。Issue #83 (ワールド側)): plate = 看板の形 (種類の既定と違うとき)、repeat / kcap = 置き方、
# rot = 線の名前 (道路名・路線名。ワールド側 Issue #86) の向きの番号 + 1
LABEL_OPTIONAL_FIELDS = ("plate", "repeat", "kcap", "rot")


def place_all(regions, inventory, cand_files, frozen_files, tools):
    """
    候補をすべて合わせてズーム・言語ごとに置き、{地域: {"z/x/y": [言語ごとのラベルのリスト]}} を返す (この実行で焼く地域の分)。
    frozen_files は Release にある地域の置いたラベル (同じ形)。動かさず、先に置いてあるものとして扱う。
    """
    vb = _vb()
    own = owners(regions, inventory)
    groups = {}   # (wide, z, 言語の番号) -> [候補]。広域か都市かはズームで決まる (WIDE_MAX_ZOOM)
    baking = set()
    for path in cand_files:
        c = load_gz(path)
        baking.add(c["region"])
        for z, per_lang in c["zooms"].items():
            if c["wide"] != (int(z) <= WIDE_MAX_ZOOM):
                sys.exit(f"{c['region']}: z{z} の候補の作り方 ({'広域' if c['wide'] else '都市'}) がズームと合わない")
            for li, cands in enumerate(per_lang):
                groups.setdefault((c["wide"], int(z), li), []).extend(cands)
    fixed = {}
    for path in frozen_files:
        name = os.path.basename(path).split(".")[0]
        if name in baking:
            continue  # 焼き直す地域の前の配置は使わない
        # 広域か都市かはズームで決める (regions.json から消した地域も、同じズームの候補と合わせて重なりと同名を調べる。
        # 地域の定義で決めると、消した広域の地域が都市とみなされた。Codex のレビューで指摘)
        for key, per_lang in load_gz(path).items():
            z = int(key.split("/")[0])
            for li, labs in enumerate(per_lang):
                fixed.setdefault((z <= WIDE_MAX_ZOOM, z, li), []).extend(labs)
    plans = {n: {} for n in baking}
    for key in sorted(set(groups) | set(fixed)):
        wide, z, li = key
        cands, fx = groups.get(key, []), fixed.get(key, [])
        # 字の幅を測るフォントの設定 (その作り方の地域が regions.json に無ければ、どれか)
        v4r = v4_regions(regions)
        label_setup(next((r for r in v4r if bool(r.get("wide")) == wide), v4r[0]), tools)
        if wide:
            # 国土数値情報の点と同名・先に出た同名の OpenStreetMap の島・海・湖は出さない (wide_candidates と同じ。地域をまたいで)
            names = {c["name"] for c in cands if not c.get("osm")} | {c["name"] for c in fx}
            kept, seen = [], set()
            for c in sorted(cands, key=lambda c: (c["pri"], c["rank"], c["name"], c["x"], c["y"])):
                if c.get("osm"):
                    if c["name"] in names or c["name"] in seen:
                        continue
                    seen.add(c["name"])
                kept.append(c)
            merged = kept
        else:
            merged = vb.merge_labels(cands, None, z, fixed=fx)
        placed = vb.place_labels(merged, fixed=fx)
        count, dropped = 0, 0
        for c in placed:
            t = (z, int(c["x"] // vb.EXTENT), int(c["y"] // vb.EXTENT))
            owner = own.get(t)
            if owner not in plans:  # 範囲の外 (丁目のまとめで外に出た) か、Release にある地域のタイル (動かさない)
                dropped += 1
                continue
            per_lang = plans[owner].setdefault(f"{t[0]}/{t[1]}/{t[2]}", [[] for _ in range(li + 1)])
            while len(per_lang) <= li:
                per_lang.append([])
            lab = {k: c[k] for k in LABEL_FIELDS}
            lab.update({k: c[k] for k in LABEL_OPTIONAL_FIELDS if k in c})
            per_lang[li].append(lab)
            count += 1
        print(f"{'wide' if wide else 'city'} z{z} lang {li}: candidates {len(cands)}, fixed {len(fx)}, "
              f"placed {count} (dropped {dropped})", flush=True)
    return plans


# ---------------------------------------------------------------- 3. 焼く

def bake(region, regions, inventory, mbtiles, plan_path, site, tools, work, jobs):
    """持ち物のタイルを焼いて site/v4/<地域>/own/ にまとめ、持ち物の一覧 (<地域>.own.json.gz の中身) を返す。"""
    name = region["name"]
    tiles = owned(regions, inventory, name)
    out_dir = os.path.join(site, DIST, name)
    own_dir = os.path.join(out_dir, "own")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(own_dir)
    plan = load_gz(plan_path) if os.path.exists(plan_path) else {}
    if tiles:
        tw = os.path.join(work, "v4", "tiles", name)
        lists = os.path.join(work, "v4", "lists")
        os.makedirs(lists, exist_ok=True)
        tiles_json = os.path.join(lists, f"{name}.tiles.json")
        plan_json = os.path.join(lists, f"{name}.plan.json")
        with open(tiles_json, "w", encoding="utf-8") as f:
            json.dump([list(t) for t in tiles], f)
        with open(plan_json, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        zs = zooms_of(region)
        m = b.bake_tiles(dict(region, zooms=f"{min(zs)}-{max(zs)}"), mbtiles, tools, tw, jobs, poi=True, glyph=True,
                         extra=["--tiles", tiles_json, "--plan", plan_json])
        if len(m["tiles"]) != len(tiles):
            sys.exit(f"{name}: 焼いたタイル {len(m['tiles'])} が持ち物 {len(tiles)} と違う")
        zooms = ",".join(str(z) for z in region.get("binZooms", []))
        shutil.rmtree(own_dir)
        b.run([sys.executable, os.path.join(HERE, "vmt_pack.py"), tw, own_dir] + (["--bin-zooms", zooms] if zooms else []))
        shutil.rmtree(tw)
    else:
        # 範囲のタイルを全部ほかの地域が持っている
        with open(os.path.join(own_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"format": b.FORMAT, "version": b.SHEET_VERSION, "tiles": [], "sheets": [], "levels": levels_of(region),
                       "sharedGlyphs": ""}, f, ensure_ascii=False, indent=1)
    # 地域の定義を残す (regions.json から消しても、床の manifest を作り続けられるように)
    path = os.path.join(own_dir, "manifest.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    m["sourceRegion"] = region
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    dump_gz(os.path.join(own_dir, "labels.json.gz"), plan)
    print(f"{name}: v4 の持ち物 {len(tiles)} タイル (範囲 {len(tiles_of(region))}) -> {own_dir}", flush=True)
    return {"region": name, "tiles": [list(t) for t in tiles]}


# ---------------------------------------------------------------- 4. 床が読む manifest

def content_key(kind, z, path, cache):
    """中身のハッシュで決めた置き場所 (v4/ の下の <kind>/<z>/<ハッシュ>.<拡張子>)。同じ中身なら同じ名前になる。"""
    if path not in cache:
        if not os.path.exists(path):
            sys.exit(f"{path}: 無い")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        cache[path] = f"{kind}/{z}/{h.hexdigest()[:HASH_LEN]}{os.path.splitext(path)[1]}"
    return cache[path]


def own_dirs(site):
    """{地域: own のディレクトリ}。Release から site/v4/<地域>/own/ に戻した持ち物。"""
    out = {}
    for path in sorted(glob.glob(os.path.join(site, DIST, "*", "own", "manifest.json"))):
        d = os.path.dirname(path)
        out[os.path.basename(os.path.dirname(d))] = d
    return out


def put_file(src, root, key):
    """src を root/v4/<key> に置く (同じファイルシステムならハードリンク。中身で名前が決まるので、あれば同じもの)。"""
    dst = os.path.join(root, DIST, key)
    if os.path.exists(dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def publish(regions, site, r2, pages_url, r2_url):
    """
    Release から戻した持ち物 (site/v4/<地域>/own/) から、配る形を作る:
      <置き場所>/v4/sheets/<z>/<ハッシュ>.png / .bin     シート (持ち主の own/ のもの。名前は中身の SHA-256 の先頭 16 桁)
      <R2>/v4/bingroups/<z>/<ハッシュ>.bin              まとめた .bin (Pages の床のものも R2)
      <置き場所>/v4/floors/<地域>/manifest.json         床が読む manifest (範囲のタイルを全部並べ、シートは絶対 URL)
      <置き場所>/v4/floors/<地域>/region.json
    置き場所は床ごと (地域の host)。R2 にはどの床も置く (Pages の床も、R2 の URL で指す写しを置く)。
    Pages には Pages の床が指すシートだけを置く。r2 を渡さない (手元で --r2 なしに作る) ときは、R2 の分も site/ に置く。
    作り終えたら持ち物のディレクトリ (site/v4/<地域>/) は消す (配らない)。
    """
    dirs = own_dirs(site)
    if not dirs:
        return
    by_name = {r["name"]: r for r in regions}
    cache, batches, where = {}, {}, {}
    for name, d in dirs.items():
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        for s in m["sheets"]:
            s["pngKey"] = content_key("sheets", s["z"], os.path.join(d, s["png"]), cache)
            if s.get("bin"):
                s["binKey"] = content_key("sheets", s["z"], os.path.join(d, s["bin"]), cache)
        for g in m.get("binGroups") or []:
            g["binKey"] = content_key("bingroups", g["z"], os.path.join(d, g["bin"]), cache)
        batches[name] = (m, d)
        for i, t in enumerate(m["tiles"]):
            k = (t["z"], t["x"], t["y"])
            if k in where:
                sys.exit(f"v4 のタイル {k} を {where[k][0]} と {name} の両方が持っている")
            where[k] = (name, i)
    roots = {"pages": site, "r2": r2 or site}
    bases = {"pages": pages_url, "r2": r2_url}
    for name in dirs:
        # regions.json から消した地域も、残した定義で床の manifest を作り続ける
        r = by_name.get(name) or batches[name][0].get("sourceRegion")
        if r is None:
            sys.exit(f"{name}: regions.json に無く、持ち物の manifest に sourceRegion も無い")
        # (regions.json から消した地域は残した定義の hosts。無ければ v4 の既定の置き場所。焼いた後で hosts を変えた地域は、
        # regions.json から消すと置き場所が変わりうる)
        host = b.host_of(r, DIST)
        for h in [host] + (["r2"] if host != "r2" and r2 else []):
            if not bases[h]:
                sys.exit(f"{name}: {h} の URL が無い (--pages-url / --r2-url (配るときは --bin-groups))")
            manifest, sheet_files, group_files = region_manifest(r, batches, where, url_of(bases[h]), url_of(r2_url))
            if group_files and not r2_url:
                sys.exit(f"{name}: まとめた .bin を指す R2 の URL が無い")
            for src, key in sheet_files:
                put_file(src, roots[h], key)
            for src, key in group_files:
                put_file(src, roots["r2"], key)
            out = os.path.join(roots[h], DIST, b.GLYPH_FLOORS, name)
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=1)
            entry = b.region_entry(r, manifest)
            entry["manifest"] = f"{b.GLYPH_FLOORS}/{name}/manifest.json"
            entry["ownTiles"] = len(batches[name][0]["tiles"])
            entry["owners"] = manifest["owners"]
            b.write_region_json(out, entry)
            print(f"{name}: v4 の床の manifest ({h}、{len(manifest['tiles'])} タイル、シート {len(manifest['sheets'])} 枚、"
                  f"持ち主 {', '.join(manifest['owners'])})", flush=True)
    for name in dirs:
        shutil.rmtree(os.path.join(site, DIST, name))


def url_of(base):
    return (base.rstrip("/") + f"/{DIST}/") if base else None


def region_manifest(region, batches, where, sheet_base, group_base):
    """床が読む manifest と、それが指すファイル [(元のパス, 置き場所)] (シートと まとめた .bin) を返す。"""
    levels = levels_of(region)
    tiles, sheets, groups = [], [], []
    sheet_files, group_files = [], []
    sheet_index, group_index = {}, {}
    common = None
    for lv in levels:
        lv["offset"] = len(tiles)
        for j in range(lv["h"]):
            for i in range(lv["w"]):
                k = (lv["z"], lv["minX"] + i, lv["minY"] + j)
                if k not in where:
                    sys.exit(f"{region['name']}: v4 のタイル {k} の持ち主が配られていない")
                owner, ti = where[k]
                m, d = batches[owner]
                c = {key: m.get(key) for key in COMMON_KEYS}
                if common is None:
                    common = c
                elif c != common:
                    diff = [key for key in COMMON_KEYS if c[key] != common[key] and key != "attribution"]
                    if diff:
                        sys.exit(f"{region['name']}: 持ち主 {owner} の manifest の {', '.join(diff)} がほかと違う")
                t = dict(m["tiles"][ti])
                sk = (owner, t["sheet"])
                if sk not in sheet_index:
                    src = m["sheets"][t["sheet"]]
                    e = {key: v for key, v in src.items()
                         if key not in ("tiles", "binGroup", "binGroupRow", "pngKey", "binKey")}
                    e["png"] = sheet_base + src["pngKey"]
                    sheet_files.append((os.path.join(d, src["png"]), src["pngKey"]))
                    if src.get("bin"):
                        e["bin"] = sheet_base + src["binKey"]
                        sheet_files.append((os.path.join(d, src["bin"]), src["binKey"]))
                    e["owner"] = owner
                    e["tiles"] = []
                    if "binGroup" in src:
                        gk = (owner, src["binGroup"])
                        if gk not in group_index:
                            g0 = m["binGroups"][src["binGroup"]]
                            g = {key: v for key, v in g0.items() if key != "binKey"}
                            g["bin"] = group_base + g0["binKey"]
                            g["owner"] = owner
                            g["sheets"] = []
                            group_index[gk] = len(groups)
                            groups.append(g)
                            group_files.append((os.path.join(d, g0["bin"]), g0["binKey"]))
                        e["binGroup"] = group_index[gk]
                        e["binGroupRow"] = src["binGroupRow"]
                        groups[group_index[gk]]["sheets"].append(len(sheets))
                    sheet_index[sk] = len(sheets)
                    sheets.append(e)
                t["sheet"] = sheet_index[sk]
                sheets[t["sheet"]]["tiles"].append(len(tiles))
                tiles.append(t)
    w, s, e_, n = (float(v) for v in region["bbox"].split(","))
    out = {key: v for key, v in common.items() if v is not None}
    used = sorted({e["owner"] for e in sheets})
    # 字は文字コードで持つ。床が読むタイルが共有の字のアトラスから読む字 (持ち主の sharedGlyphs を合わせたもの。少し多め)
    out["sharedGlyphs"] = "".join(sorted(set("".join(batches[o][0].get("sharedGlyphs", "") for o in used))))
    out.update({"center": [(w + e_) / 2, (s + n) / 2], "levels": levels, "tiles": tiles, "sheets": sheets,
                "owners": sorted({e["owner"] for e in sheets}, key=lambda o: (o != region["name"], o))})
    if groups:
        out["binGroupBase"] = ""  # binGroups[g].bin は絶対 URL
        out["binGroups"] = groups
    return out, sheet_files, group_files


# ---------------------------------------------------------------- コマンド

def main():
    ap = argparse.ArgumentParser(description="VMT-DT v4 を地域をまたいで重複なく焼く")
    ap.add_argument("mode", choices=["candidates", "labels", "bake", "owned", "inventory"])
    ap.add_argument("--regions", default=os.path.join(HERE, "regions.json"))
    ap.add_argument("--inventory", default=None,
                    help="焼いた地域の持ち物の一覧 ({地域: [[z, x, y], ...]})。どの段にも同じものを渡す。inventory: 書き出す先")
    ap.add_argument("--owns", default=None, help="inventory: <地域>.own.json.gz のあるディレクトリ")
    ap.add_argument("--region", help="candidates / bake / owned: 地域の名前")
    ap.add_argument("--mbtiles", action="append", default=[], help="name=path。無ければ planetiler で作る")
    ap.add_argument("--tools", default="build/tools")
    ap.add_argument("--work", default="build")
    ap.add_argument("--java-mem", default="8g")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--site", default="site")
    ap.add_argument("--cands", default=None, help="labels: 候補のディレクトリ (<地域>.json.gz)")
    ap.add_argument("--frozen", default=None, help="labels: Release にある地域の置いたラベル (<地域>.labels.json.gz) のディレクトリ")
    ap.add_argument("--plans", default=None, help="labels: 書き出す / bake: 読むディレクトリ (<地域>.json.gz)")
    ap.add_argument("--out", default=None, help="candidates: 書き出すディレクトリ")
    args = ap.parse_args()
    regions = load_regions(args.regions)
    if args.mode == "inventory":
        inv = {}
        for path in sorted(glob.glob(os.path.join(args.owns, "*.own.json.gz"))):
            o = load_gz(path)
            inv[o["region"]] = o["tiles"]
        with open(args.inventory, "w", encoding="utf-8") as f:
            json.dump(inv, f, separators=(",", ":"))
        owners(regions, inv)  # 一覧どうしが重なっていないか
        print(f"持ち物の一覧: {len(inv)} 地域、{sum(len(v) for v in inv.values())} タイル -> {args.inventory}")
        return
    inventory = load_inventory(args.inventory)

    if args.mode == "owned":
        r = find(regions, args.region)
        t = owned(regions, inventory, r["name"])
        print(f"{r['name']}: 範囲 {len(tiles_of(r))} タイルのうち {len(t)} タイルを持つ")
    elif args.mode == "candidates":
        r = find(regions, args.region)
        mb = mbtiles_for(r, args.mbtiles, args.tools, args.work, args.java_mem)
        dump_gz(os.path.join(args.out, f"{r['name']}.json.gz"), candidates(r, mb, owned(regions, inventory, r["name"])))
    elif args.mode == "labels":
        cand_files = sorted(glob.glob(os.path.join(args.cands, "*.json.gz")))
        frozen = sorted(glob.glob(os.path.join(args.frozen, "*.labels.json.gz"))) if args.frozen else []
        missing = set(inventory) - {os.path.basename(p).split(".")[0] for p in frozen} - \
            {os.path.basename(p).split(".")[0] for p in cand_files}
        if missing:
            sys.exit(f"Release にある地域の置いたラベルが無い: {', '.join(sorted(missing))}")
        plans = place_all(regions, inventory, cand_files, frozen, args.tools)
        for name, plan in plans.items():
            dump_gz(os.path.join(args.plans, f"{name}.json.gz"), plan)
    elif args.mode == "bake":
        r = find(regions, args.region)
        mb = mbtiles_for(r, args.mbtiles, args.tools, args.work, args.java_mem)
        own = bake(r, regions, inventory, mb, os.path.join(args.plans, f"{r['name']}.json.gz"), args.site, args.tools,
                   args.work, args.jobs)
        if args.out:  # 持ち物の一覧 (Release の <地域>.own.json.gz)
            dump_gz(os.path.join(args.out, f"{r['name']}.own.json.gz"), own)


if __name__ == "__main__":
    main()
