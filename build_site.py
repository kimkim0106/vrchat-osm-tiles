#!/usr/bin/env python3
"""build_site.py - regions.json の地域ごとに MBTiles を作り、VMT-DT のタイルに焼いて site/ にまとめる。

  site/index.html                     人が見る用の一覧と帰属表示 (すべての版)
  site/tools/                         人が使う Web のツール (リポジトリの tools/ を Actions が写す。build_site.py は作らない)
  site/v1/index.json                  その版の地域の一覧 (String Loading で読む想定)
  site/v1/<name>/manifest.json        vmt_bake.py の manifest
  site/v1/<name>/region.json          index.json に載せる情報 (焼いた日時とコミットを含む)
  site/v1/<name>/tiles/z/x/y.png      タイル (1 タイル = 1 画像)
  site/v1/<name>/selftest.png
  site/v2/<name>/manifest.json        vmt_pack.py の manifest
  site/v2/<name>/sheets/z/k.png       シート (複数タイル = 1 画像)。v1 のタイルをまとめたもので、中身は同じ
  site/v2/<name>/sheets/z/k.bin       同じシートの生データ (regions.json の binZooms のズームだけ)
  site/v3/<name>/...                  v2 と同じ並び。POI (駅・観光地・店など) 入りのタイルをまとめたシート

v1 / v2 は VMT-DT の形式の版 (manifest の version、テクスチャのヘッダ列 1) と同じ番号。
ワールドが焼き込む URL に入るので、読み方の変わる変更は版を上げて別のディレクトリに出し、古い版も配り続ける。
v2 は v1 から作る (vmt_pack.py) ので、v1 を焼き直さない限り v2 のタイルも変わらない。
v1 / v2 は POI を入れずに焼く (vmt_bake.py --no-poi。v2 までのワールドのシェーダーは POI を地名と区別できない)。
v3 は POI 入りで焼いたタイル (配らない。work/poi/ に置いてすぐ消す) を v2 と同じ形式のシートにまとめたもの。
manifest の version は 2 (シートの並び) のままで、labelCategories (ラベルの種類の一覧) が付く。
ラベル頂点の T3.B に種類の番号が入るので、種類ごとの色分けと出し入れ (_LabelMask) のできるシェーダーで読む。
v3 は v1 とは別に焼く (同じ MBTiles から。--poi)。

焼いた地域は固定する。ワールドは作るときに manifest の頂点数を焼き込むので、同じ URL の中身を焼き直すと
頂点が増えたタイルで三角形が欠ける。Actions は焼いた地域を Release (タグ = 版) に保存し、次からはそれを戻して配る。
焼き直すのは、まだ焼いていない地域と、手で指定した地域だけ。

使い方 (手元):
  python build_site.py --tools build/tools --work build --site site --jobs 4
  python build_site.py ... --only tokyo --mbtiles tokyo=path/to/tokyo.mbtiles   # 既存の MBTiles を使う
  python build_site.py --site site --pack tokyo                                  # v1 の tokyo から v2 を作る
  python build_site.py ... --only tokyo --poi                                    # v3 (POI 入りのシート) を焼く
  python build_site.py --site site --index-only                                  # 置いてある地域から索引だけ作る
  python build_site.py --site site --index-only --prune                          # 配らない版を外してから索引を作る

regions.json の地域に "dists": ["v2"] があれば、その版だけを配る (--prune が他の版のディレクトリを site/ から外す)。
v2 は v1 から作るので v1 も焼いて Release には保存するが、Pages には載せない (サイトの容量を空けるため)。
v3 は dists に v3 がある地域 (dists が無ければすべて) だけ焼く (wants_dist)。
"releaseOnly": ["v3"] の版は、焼いて Release に保存するが配らない (--prune が外す)。
配る前に確かめたいとき (R2 の v3 など) に使い、確かめたら dists に移す (Release から戻して配る。焼き直さない)。

regions.json の地域に "host": "r2" があれば、Pages ではなく Cloudflare R2 に置く (Pages の 1 GB に入らないため)。
--r2 DIR を付けると、索引を作る前にその地域を site/ から DIR/ に移し、DIR/ にも R2 用の索引を作る。
DIR/ を R2 に上げるのは r2_upload.py。R2 は VRChat の信頼済みドメインではないので、
その地域は「Allow Untrusted URLs」をオンにした人にしか読めない。

regions.json の地域に "binGroups": {"dists": ["v3"], "zooms": [13, 14]} があれば、--bin-groups R2_URL のとき、
その版のシートを数枚ずつ縦につないだ .bin (まとめた .bin、vmt_bingroup.py) を R2 に作る (Pages の地域でも R2 に置く。
Pages は容量を生の大きさで数えるので入らない)。String Loading で 1 回に数枚取れるので、床の取得が速くなる。
R2 を読めない人 (「Allow Untrusted URLs」がオフ) は、床が 2 回失敗した後でシートごとの取得に戻る。

regions.json の地域に "wide": true があれば広域の地図 (日本全体と地方ごと。wide_regions.py が書く)。v3 だけを作る。
  "wideLabels": 地名の点 (wide_labels.py が国土数値情報から作った labels/wide_japan.json)。OpenStreetMap の地名と POI の代わり
  "boundaryLevels": 境界線はこの admin_level の陸上の線だけ (都道府県境 = [4])
  "labelVerts": 1 タイルのラベル頂点の上限 (Unity の地名の入れ物の最大と合わせる)
  "tileSize" / "tiles": 床の 1 枚の大きさ (m) と枚数。記録用 (Unity で床マップを作るときに使う)

--tools には planetiler.jar と NotoSansCJK-Regular.ttc を置く。
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORMAT = "VMT-DT"
FORMAT_VERSION = 1             # vmt_bake.py の FORMAT_VERSION と同じにする (焼いた manifest で確かめる)
DIST = f"v{FORMAT_VERSION}"    # 焼いたタイルを置くディレクトリ
SHEET_VERSION = 2              # vmt_pack.py の SHEET_VERSION
SHEET_DIST = f"v{SHEET_VERSION}"
POI_DIST = "v3"                # POI 入りのシート (形式は v2 と同じ。manifest の version も 2)
DISTS = [DIST, SHEET_DIST, POI_DIST]  # 配る版 (check_site.py が確かめる)
SITE_LIMIT = 900e6             # GitHub Pages のサイトは 1 GB まで
HOSTS = ("pages", "r2")        # regions.json の host (書かなければ pages)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd):
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


# OSM の元データ (日本の地域) を取る場所。上から順に試す。
# Geofabrik は GitHub Actions のマシンからの接続を拒むことがある (同じマシンからは何分たっても Connection refused。
# 2026-11-16 に東北の 3 地域が 3 回とも落ちた)。openstreetmap.fr は同じ名前の地域を配っている (地域の境界の線は
# Geofabrik と少し違う。pref_regions.py の area は Geofabrik の境界で決めている)。
OSM_MIRRORS = [
    ("geofabrik", "https://download.geofabrik.de/asia/{path}-latest.osm.pbf"),
    ("openstreetmap.fr", "https://download.openstreetmap.fr/extracts/asia/{path}-latest.osm.pbf"),
]
JAPAN_AREAS = {"hokkaido", "tohoku", "kanto", "chubu", "kansai", "chugoku", "shikoku", "kyushu"}


def osm_source(area):
    """
    area の OSM の元データの URL の候補 (上から順に試す)。日本の地域でなければ None (planetiler の --area に任せる)。
    地域のファイル (Geofabrik → openstreetmap.fr) の後に、全国のファイルも候補にする (planetiler が --bounds で切るので、
    焼いた結果は同じ。大きい (約 2.6 GB) ので最後)。
    """
    if area == "japan":
        paths = ["japan"]
    elif area in JAPAN_AREAS:
        paths = [f"japan/{area}", "japan"]
    else:
        return None
    return [(f"{name} の {path}", url.format(path=path)) for path in paths for name, url in OSM_MIRRORS]


def osm_cache_dir(work, area):
    """元データを置く場所。Actions は地域と日付ごとに actions/cache で残す (pages.yml)。"""
    return os.path.join(work, "osm-cache", area)


def fetch_osm(area, work):
    """
    OSM の元データを work/osm-cache/<area>/<area>.osm.pbf に置き、その URL と置き場所を返す (planetiler の --osm_url /
    --osm_path に渡す)。planetiler はファイルがあれば取り直さない (refresh_osm=false)。

    - 今日 (UTC) 取ったもの (date.txt が今日) があれば、取らずに使う (同じ日の同じ地域の都市はキャッシュから戻す)。
    - 無ければ候補を順に取る。
    - どこからも取れなければ、前の日までに取ったもの (actions/cache の restore-keys で戻した古いもの) を使う。
      それも無ければ失敗 (build_mbtiles が間をあけて試し直す)。
    URL はキャッシュのものを使うときも候補の先頭を返す (planetiler は取りに行かない)。
    """
    urls = osm_source(area)
    if urls is None:
        return None, None
    d = osm_cache_dir(work, area)
    path = os.path.join(d, f"{area}.osm.pbf")
    stamp = os.path.join(d, "date.txt")
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    have = os.path.exists(path) and os.path.getsize(path) > 0
    got = ""
    if have and os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            got = f.read().strip()
    if have and got == today:
        print(f"{area}: 今日取った元データがある (取らない)", flush=True)
        return urls[0][1], path
    os.makedirs(d, exist_ok=True)
    for name, url in urls:
        try:
            run(["curl", "-fsSL", "--retry", "4", "--retry-all-errors", "--retry-delay", "30",
                 "--connect-timeout", "30", "-A", "vrchat-osm-tiles (GitHub Actions)", "-o", path + ".part", url])
        except subprocess.CalledProcessError:
            print(f"{area}: {name} から取れなかった", flush=True)
            continue
        os.replace(path + ".part", path)
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(today + "\n")
        print(f"{area}: {name} から取った ({os.path.getsize(path) / 1e6:.0f} MB)", flush=True)
        return url, path
    if os.path.exists(path + ".part"):
        os.remove(path + ".part")
    if have:
        print(f"::warning::{area}: 元データをどこからも取れなかったので、前に取ったもの ({got or '日付不明'}) を使う", flush=True)
        return urls[0][1], path
    raise OSError(f"{area}: OSM の元データをどこからも取れなかった")


def build_mbtiles(region, tools, work, java_mem):
    out = os.path.join(work, f"{region['name']}.mbtiles")
    # 元データの取得や planetiler (水域などのほかの元データも取る) が落ちたら、間をあけて試し直す (取れたものは残る)
    for attempt in range(1, 4):
        try:
            return _planetiler(region, tools, work, java_mem, out)
        except (subprocess.CalledProcessError, OSError) as e:
            if attempt == 3:
                raise
            print(f"{region['name']}: 失敗した ({e})。{120 * attempt} 秒後に試し直す ({attempt}/3)", flush=True)
            time.sleep(120 * attempt)


def _planetiler(region, tools, work, java_mem, out):
    osm_url, osm_path = fetch_osm(region["area"], work)
    # 元データを自分で取ったときは、その URL と置き場所を渡す (planetiler が Geofabrik の索引を引かないように)
    osm = [f"--osm_url={osm_url}", f"--osm_path={osm_path}"] if osm_url else []
    # 焼くズームより上は作らない (広域の地図は日本全体を z11 までにして速くする。都市は 14 で、planetiler の既定と同じ)
    maxzoom = max(int(v) for v in region["zooms"].split("-"))
    run(["java", f"-Xmx{java_mem}", "-jar", os.path.join(tools, "planetiler.jar"),
         "--download", f"--area={region['area']}", f"--bounds={region['bbox']}", f"--maxzoom={maxzoom}", *osm,
         f"--download_dir={os.path.join(work, 'sources')}", f"--tmpdir={os.path.join(work, 'tmp')}",
         f"--output={out}", "--force"])
    return out


def region_entry(region, manifest):
    """index.json に載せる 1 地域分。region.json として地域のディレクトリにも置く。"""
    return {
        "name": region["name"],
        "title": region.get("title", region["name"]),
        "bbox": [float(v) for v in region["bbox"].split(",")],
        "zooms": region["zooms"],
        "manifest": f"{region['name']}/manifest.json",
        "tiles": len(manifest["tiles"]),
        "pngBytes": sum(t["pngBytes"] for t in manifest.get("sheets") or manifest["tiles"]),
        "binBytes": sum(t.get("binBytes", 0) for t in manifest.get("sheets") or []),
        "images": len(manifest.get("sheets") or manifest["tiles"]),
        "attribution": manifest.get("attribution", ""),
        "labelAttribution": manifest.get("labelAttribution", []),
        "built": now(),
        "commit": os.environ.get("GITHUB_SHA", ""),
    }


def write_region_json(out, entry):
    with open(os.path.join(out, "region.json"), "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=1)


def wants_dist(region, dist):
    """
    その地域でその版を作るか (焼いて Release に保存するか)。dists が無ければすべての版。
    releaseOnly の版も作る (配らないだけ)。v2 を作るなら、その元の v1 も作る。
    """
    dists = region.get("dists")
    if dists is None:
        return True
    made = set(dists) | set(region.get("releaseOnly") or [])
    return dist in made or (dist == DIST and SHEET_DIST in made)


def validate_regions(regions):
    """regions.json の中身を確かめる (Actions の plan と build_site.py の両方から呼ぶ)。"""
    names = [r["name"] for r in regions]
    if len(set(names)) != len(names):
        sys.exit("regions.json の name が重複している")
    for r in regions:
        dists = r.get("dists")
        only = r.get("releaseOnly") or []
        bad = (set(dists or []) | set(only)) - set(DISTS)
        if bad:
            sys.exit(f"{r['name']}: dists / releaseOnly に知らない版 {', '.join(sorted(bad))}")
        if only and dists is None:
            sys.exit(f"{r['name']}: releaseOnly を使うときは dists も書く (dists が無いとすべての版を配る)")
        if set(only) & set(dists or []):
            sys.exit(f"{r['name']}: 同じ版を dists と releaseOnly の両方に書いている")
        if r.get("host", "pages") not in HOSTS:
            sys.exit(f"{r['name']}: host は {' / '.join(HOSTS)} のどれか")
        if r.get("wide"):
            if set(dists or []) | set(only) != {POI_DIST}:
                sys.exit(f"{r['name']}: 広域の地図 (wide) は {POI_DIST} だけを作る (dists か releaseOnly に {POI_DIST} だけ)")
            labels = r.get("wideLabels")
            if not labels or not os.path.exists(os.path.join(HERE, labels)):
                sys.exit(f"{r['name']}: wideLabels のファイルが無い ({labels})")
            verts = r.get("labelVerts")
            if verts is not None and (verts <= 0 or verts % 90):
                sys.exit(f"{r['name']}: labelVerts は 90 の倍数")
        groups = r.get("binGroups")
        if groups is not None:
            if set(groups) != {"dists", "zooms"} or set(groups["dists"]) - {SHEET_DIST, POI_DIST} or not groups["zooms"]:
                sys.exit(f"{r['name']}: binGroups は {{\"dists\": [シートの版], \"zooms\": [ズーム]}}")


def bake_tiles(region, mbtiles, tools, out, jobs, poi):
    """vmt_bake.py で out に焼き、manifest を返す。poi が False なら POI を入れない (v1)。"""
    if os.path.exists(out):
        shutil.rmtree(out)
    cmd = [sys.executable, os.path.join(HERE, "vmt_bake.py"), mbtiles, out,
           "--bbox", region["bbox"], "--zooms", region["zooms"],
           "--font", os.path.join(tools, "NotoSansCJK-Regular.ttc"), "--no-bin", "--jobs", str(jobs)]
    if region.get("lang"):
        cmd += ["--lang", region["lang"]]
    if not poi:
        cmd.append("--no-poi")
    if region.get("wideLabels"):
        cmd += ["--wide-labels", os.path.join(HERE, region["wideLabels"])]
    if region.get("boundaryLevels"):
        cmd += ["--boundary-levels", ",".join(str(v) for v in region["boundaryLevels"])]
    if region.get("labelVerts"):
        cmd += ["--label-verts", str(region["labelVerts"])]
    run(cmd)
    shutil.rmtree(os.path.join(out, "_parts"), ignore_errors=True)
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    # 配る側ではフォントのファイル名だけ残す (ビルドした環境のパスを出さない)
    m["labelConfig"]["font"] = os.path.basename(m["labelConfig"].get("font") or "")
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    if m.get("version") != FORMAT_VERSION:
        sys.exit(f"{region['name']}: manifest の version {m.get('version')} が {FORMAT_VERSION} と違う")
    return m


def bake(region, mbtiles, tools, site, jobs):
    """v1 (POI なし、1 タイル = 1 画像) を焼く。"""
    out = os.path.join(site, DIST, region["name"])
    m = bake_tiles(region, mbtiles, tools, out, jobs, poi=False)
    write_region_json(out, region_entry(region, m))


def bake_poi(region, mbtiles, tools, site, work, jobs):
    """POI 入りのタイルを work/poi/ に焼き、シートにまとめて v3 にする (タイルは消す)。"""
    tiles = os.path.join(work, "poi", region["name"])
    m = bake_tiles(region, mbtiles, tools, tiles, jobs, poi=True)
    if len(m.get("labelCategories") or []) < 2:
        sys.exit(f"{region['name']}: POI 入りで焼いたのに labelCategories が無い")
    pack_dir(region, tiles, os.path.join(site, POI_DIST, region["name"]))
    shutil.rmtree(tiles)


def pack_dir(region, src, out):
    """vmt_pack.py で src (1 タイル = 1 画像) を out (シート) にまとめる。"""
    zooms = ",".join(str(z) for z in region.get("binZooms", []))
    run([sys.executable, os.path.join(HERE, "vmt_pack.py"), src, out] + (["--bin-zooms", zooms] if zooms else []))
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    if m.get("version") != SHEET_VERSION:
        sys.exit(f"{region['name']}: {out} の manifest の version が {SHEET_VERSION} ではない")
    write_region_json(out, region_entry(region, m))


def pack(region, site):
    """v1 の地域から v2 (シート) を作る。"""
    src = os.path.join(site, DIST, region["name"])
    if not os.path.exists(os.path.join(src, "manifest.json")):
        sys.exit(f"{region['name']}: {src} に v1 が無い (先に焼くか Release から戻す)")
    pack_dir(region, src, os.path.join(site, SHEET_DIST, region["name"]))


def prune(site, regions):
    """dists を指定した地域の、配らない版のディレクトリを site/ から外す (Release に保存した後で呼ぶ)。"""
    for r in regions:
        dists = r.get("dists")
        if dists is None:
            continue
        for dist in dist_dirs(site):
            out = os.path.join(site, dist, r["name"])
            if dist not in dists and os.path.isdir(out):
                print(f"{r['name']}: {dist} は配らない ({out} を外す)")
                shutil.rmtree(out)


def move_r2(site, r2, regions):
    """host が r2 の地域を site/ から r2/ に移す (Pages には載せない)。r2/ は毎回作り直す。"""
    if os.path.exists(r2):
        shutil.rmtree(r2)
    os.makedirs(r2)
    for r in regions:
        if r.get("host", "pages") != "r2":
            continue
        for dist in dist_dirs(site):
            src = os.path.join(site, dist, r["name"])
            if os.path.isdir(src):
                os.makedirs(os.path.join(r2, dist), exist_ok=True)
                print(f"{r['name']}: {dist} は R2 に置く ({src} -> {r2})")
                shutil.move(src, os.path.join(r2, dist, r["name"]))


def bin_groups(site, r2, regions, base_url):
    """
    binGroups を指定した地域の、その版のシートからまとめた .bin を作る (vmt_bingroup.py)。置き場所は R2 (r2/<版>/<地域>/)。
    manifest (Pages の地域なら site/ のもの) に binGroupBase と binGroups を書き足す。中身は Release のシートから毎回作り直すが、
    同じシートからは同じものができる (パスにはまとめ方のハッシュが入る)。
    """
    base_url = base_url if base_url.endswith("/") else base_url + "/"
    for r in regions:
        cfg = r.get("binGroups")
        if not cfg:
            continue
        for dist in cfg["dists"]:
            src = next((os.path.join(root, dist, r["name"]) for root in (site, r2)
                        if os.path.exists(os.path.join(root, dist, r["name"], "manifest.json"))), None)
            if src is None:
                continue  # その版を配っていない (releaseOnly など)
            run([sys.executable, os.path.join(HERE, "vmt_bingroup.py"), src,
                 "--zooms", ",".join(str(z) for z in cfg["zooms"]),
                 "--base-url", f"{base_url}{dist}/{r['name']}/", "--bins", os.path.join(r2, dist, r["name"])])


def dist_dirs(site):
    """site/ にある版のディレクトリ (v1, v2, ...) を番号順に返す。"""
    found = []
    for d in glob.glob(os.path.join(site, "v*")):
        m = re.fullmatch(r"v(\d+)", os.path.basename(d))
        if m and os.path.isdir(d):
            found.append((int(m.group(1)), os.path.basename(d)))
    return [name for _, name in sorted(found)]


def write_indexes(site):
    """置いてある地域 (region.json) から、版ごとの index.json と全体の index.html を作る。"""
    generated = now()
    sections = []
    total = 0
    for dist in dist_dirs(site):
        entries = []
        for path in sorted(glob.glob(os.path.join(site, dist, "*", "region.json"))):
            with open(path, encoding="utf-8") as f:
                entries.append(json.load(f))
        index = {"format": FORMAT, "version": int(dist[1:]), "generated": generated,
                 "commit": os.environ.get("GITHUB_SHA", ""), "regions": entries}
        with open(os.path.join(site, dist, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        total += sum(e["pngBytes"] + e.get("binBytes", 0) for e in entries)
        sections.append((dist, entries))
        print(f"{dist}: {len(entries)} regions ({', '.join(e['name'] for e in entries)})")

    blocks = []
    attributions = set()
    label_attributions = set()  # 広域の地図の地名 (国土数値情報。CC BY 4.0)
    for dist, entries in sections:
        rows = "\n".join(
            f"<tr><td>{html.escape(e['title'])}</td>"
            f"<td><a href=\"{dist}/{html.escape(e['manifest'])}\">{dist}/{html.escape(e['name'])}</a></td>"
            f"<td>{html.escape(e['zooms'])}</td><td>{e['tiles']}</td><td>{e.get('images', e['tiles'])}</td>"
            f"<td>{(e['pngBytes'] + e.get('binBytes', 0)) / 1e6:.1f} MB</td>"
            f"<td>{html.escape(e.get('built', '')[:10])}</td></tr>"
            for e in entries)
        attributions.update(e["attribution"] for e in entries if e["attribution"])
        label_attributions.update(a for e in entries for a in e.get("labelAttribution") or [])
        blocks.append(f"""<h2>形式 VMT-DT {dist[1:]} (<a href="{dist}/index.json">{dist}/index.json</a>)</h2>
<table><tr><th>地域</th><th>manifest</th><th>ズーム</th><th>タイル</th><th>画像</th><th>容量</th><th>焼いた日</th></tr>
{rows}
</table>""")
    page = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>vrchat-osm-tiles</title>
<style>body{{font-family:system-ui,sans-serif;max-width:50rem;margin:2rem auto;padding:0 1rem;line-height:1.6}}
table{{border-collapse:collapse}}td,th{{border-bottom:1px solid #ccc;padding:.25rem .75rem;text-align:left}}</style></head>
<body><h1>vrchat-osm-tiles</h1>
<p>VRChat のワールドで地図を描くためのデータテクスチャです。画像として見るものではありません。</p>
{chr(10).join(blocks)}
<p>地図データ: {html.escape(" / ".join(sorted(attributions)) or "© OpenStreetMap contributors")}
(<a href="https://www.openstreetmap.org/copyright">ODbL</a>)。文字: Noto Sans CJK (SIL Open Font License 1.1)。</p>
<p>ツール: <a href="tools/ink-geojson/">ペンの線の取り出し</a> (VRChat のログから、設定タブレットで書き出した GeoJSON を取り出す)。</p>
{"<p>広域の地図の地名: " + html.escape(" / ".join(sorted(label_attributions))) + " (CC BY 4.0)。</p>" if label_attributions else ""}
<p>生成: {generated}</p>
</body></html>
"""
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    return total


def main():
    ap = argparse.ArgumentParser(description="regions.json の地域を焼いて Pages 用の site/ を作る")
    ap.add_argument("--regions", default=os.path.join(HERE, "regions.json"))
    ap.add_argument("--tools", default="build/tools", help="planetiler.jar と NotoSansCJK-Regular.ttc の場所")
    ap.add_argument("--work", default="build", help="MBTiles と planetiler の元データの置き場")
    ap.add_argument("--site", default="site")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--java-mem", default="8g")
    ap.add_argument("--only", action="append", help="この名前の地域だけ焼く (複数可)")
    ap.add_argument("--poi", action="store_true", help="v1 の代わりに v3 (POI 入りのシート) を焼く")
    ap.add_argument("--mbtiles", action="append", default=[], help="name=path。planetiler を回さずこの MBTiles を使う")
    ap.add_argument("--zooms", default=None, help="regions.json のズームを上書きする (試し焼き用)")
    ap.add_argument("--pack", action="append", default=[], help="この名前の地域の v1 から v2 を作る (複数可)")
    ap.add_argument("--index-only", action="store_true", help="焼かずに、置いてある地域から索引だけ作る")
    ap.add_argument("--prune", action="store_true", help="regions.json の dists に無い版を site/ から外す (Release に保存した後で)")
    ap.add_argument("--r2", default=None, help="host が r2 の地域をこのディレクトリに移し、ここにも索引を作る (Release に保存した後で)")
    ap.add_argument("--bin-groups", default=None, metavar="R2_URL",
                    help="binGroups の地域のまとめた .bin を --r2 のディレクトリに作る。R2 の公開 URL を渡す")
    args = ap.parse_args()
    os.makedirs(args.site, exist_ok=True)

    with open(args.regions, encoding="utf-8") as f:
        regions = json.load(f)["regions"]
    validate_regions(regions)
    names = [r["name"] for r in regions]
    unknown = set(args.only or []) | set(args.pack)
    unknown -= set(names)
    if unknown:
        sys.exit(f"regions.json に無い地域: {', '.join(sorted(unknown))}")

    if args.pack:
        for r in regions:
            if r["name"] in args.pack:
                pack(r, args.site)
    elif not args.index_only:
        given = dict(s.split("=", 1) for s in args.mbtiles)
        os.makedirs(args.work, exist_ok=True)
        baked = 0
        for r in regions:
            if args.only and r["name"] not in args.only:
                continue
            if args.zooms:
                r = dict(r, zooms=args.zooms)
            mbtiles = given.get(r["name"]) or build_mbtiles(r, args.tools, args.work, args.java_mem)
            if args.poi:
                bake_poi(r, mbtiles, args.tools, args.site, args.work, args.jobs)
            else:
                bake(r, mbtiles, args.tools, args.site, args.jobs)
            baked += 1
        if not baked:
            sys.exit("焼く地域がない")

    if args.prune:
        prune(args.site, regions)
    if args.bin_groups and not args.r2:
        sys.exit("--bin-groups には --r2 が要る")
    if args.r2:
        move_r2(args.site, args.r2, regions)
        if args.bin_groups:
            bin_groups(args.site, args.r2, regions, args.bin_groups)
        r2_total = write_indexes(args.r2)
        print(f"r2 total {r2_total / 1e6:.1f} MB -> {args.r2}")
    total = write_indexes(args.site)
    print(f"site total {total / 1e6:.1f} MB -> {args.site}")
    # 容量は Pages に載せる形 (--prune / --r2 の後) で確かめる。焼いた直後の site/ には配らない版や R2 の地域も入っている
    if args.index_only and total > SITE_LIMIT:
        sys.exit("GitHub Pages のサイトは 1 GB まで。地域かズームを減らす")


if __name__ == "__main__":
    main()
