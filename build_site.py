#!/usr/bin/env python3
"""build_site.py - regions.json の地域ごとに MBTiles を作り、VMT-DT のタイルに焼いて site/ にまとめる。

  site/index.html                     人が見る用の一覧と帰属表示
  site/v1/index.json                  地域の一覧 (String Loading で読む想定)
  site/v1/<name>/manifest.json        vmt_bake.py の manifest
  site/v1/<name>/tiles/z/x/y.png      タイル
  site/v1/<name>/selftest.png

v1 は VMT-DT の形式の版 (vmt_bake.py の FORMAT_VERSION、manifest の version、テクスチャのヘッダ列 1) と同じ番号。
ワールドが焼き込む URL に入るので、読み方の変わる変更 (複数タイルを 1 枚にまとめる、など) は形式の版を上げて
別のディレクトリ (v2) に出し、古い版も配り続ける。

使い方 (手元):
  python build_site.py --tools build/tools --work build --site site --jobs 4
  python build_site.py ... --only tokyo --mbtiles tokyo=path/to/tokyo.mbtiles   # 既存の MBTiles を使う

--tools には planetiler.jar と NotoSansCJK-Regular.ttc を置く。
"""
import argparse
import datetime
import html
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FORMAT = "VMT-DT"
FORMAT_VERSION = 1             # vmt_bake.py の FORMAT_VERSION と同じにする (焼いた manifest で確かめる)
DIST = f"v{FORMAT_VERSION}"    # URL のディレクトリ


def run(cmd):
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def build_mbtiles(region, tools, work, java_mem):
    out = os.path.join(work, f"{region['name']}.mbtiles")
    run(["java", f"-Xmx{java_mem}", "-jar", os.path.join(tools, "planetiler.jar"),
         "--download", f"--area={region['area']}", f"--bounds={region['bbox']}",
         f"--download_dir={os.path.join(work, 'sources')}", f"--tmpdir={os.path.join(work, 'tmp')}",
         f"--output={out}", "--force"])
    return out


def bake(region, mbtiles, tools, site, jobs):
    out = os.path.join(site, DIST, region["name"])
    if os.path.exists(out):
        shutil.rmtree(out)
    cmd = [sys.executable, os.path.join(HERE, "vmt_bake.py"), mbtiles, out,
           "--bbox", region["bbox"], "--zooms", region["zooms"],
           "--font", os.path.join(tools, "NotoSansCJK-Regular.ttc"), "--no-bin", "--jobs", str(jobs)]
    if region.get("lang"):
        cmd += ["--lang", region["lang"]]
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
    return {
        "name": region["name"],
        "title": region.get("title", region["name"]),
        "bbox": [float(v) for v in region["bbox"].split(",")],
        "zooms": region["zooms"],
        "manifest": f"{region['name']}/manifest.json",
        "tiles": len(m["tiles"]),
        "pngBytes": sum(t["pngBytes"] for t in m["tiles"]),
        "attribution": m.get("attribution", ""),
    }


def write_index(site, entries):
    index = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": os.environ.get("GITHUB_SHA", ""),
        "regions": entries,
    }
    with open(os.path.join(site, DIST, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    rows = "\n".join(
        f"<tr><td>{html.escape(e['title'])}</td>"
        f"<td><a href=\"{DIST}/{html.escape(e['manifest'])}\">{DIST}/{html.escape(e['name'])}</a></td>"
        f"<td>{e['zooms']}</td><td>{e['tiles']}</td><td>{e['pngBytes'] / 1e6:.1f} MB</td></tr>"
        for e in entries)
    attributions = sorted({e["attribution"] for e in entries if e["attribution"]})
    page = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>vrchat-osm-tiles</title>
<style>body{{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;line-height:1.6}}
table{{border-collapse:collapse}}td,th{{border-bottom:1px solid #ccc;padding:.25rem .75rem;text-align:left}}</style></head>
<body><h1>vrchat-osm-tiles</h1>
<p>VRChat のワールドで地図を描くためのデータテクスチャです。画像として見るものではありません。</p>
<p>形式 VMT-DT {FORMAT_VERSION}: <a href="{DIST}/index.json">{DIST}/index.json</a></p>
<table><tr><th>地域</th><th>manifest</th><th>ズーム</th><th>タイル</th><th>容量</th></tr>
{rows}
</table>
<p>地図データ: {html.escape(" / ".join(attributions) or "© OpenStreetMap contributors")}
(<a href="https://www.openstreetmap.org/copyright">ODbL</a>)。文字: Noto Sans CJK (SIL Open Font License 1.1)。</p>
<p>生成: {index['generated']}</p>
</body></html>
"""
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description="regions.json の地域を焼いて Pages 用の site/ を作る")
    ap.add_argument("--regions", default=os.path.join(HERE, "regions.json"))
    ap.add_argument("--tools", default="build/tools", help="planetiler.jar と NotoSansCJK-Regular.ttc の場所")
    ap.add_argument("--work", default="build", help="MBTiles と planetiler の元データの置き場")
    ap.add_argument("--site", default="site")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--java-mem", default="8g")
    ap.add_argument("--only", action="append", help="この名前の地域だけ焼く (複数可)")
    ap.add_argument("--mbtiles", action="append", default=[], help="name=path。planetiler を回さずこの MBTiles を使う")
    ap.add_argument("--zooms", default=None, help="regions.json のズームを上書きする (試し焼き用)")
    args = ap.parse_args()

    with open(args.regions, encoding="utf-8") as f:
        regions = json.load(f)["regions"]
    names = [r["name"] for r in regions]
    if len(set(names)) != len(names):
        sys.exit("regions.json の name が重複している")
    given = dict(s.split("=", 1) for s in args.mbtiles)
    os.makedirs(args.site, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    entries = []
    for r in regions:
        if args.only and r["name"] not in args.only:
            continue
        if args.zooms:
            r = dict(r, zooms=args.zooms)
        mbtiles = given.get(r["name"]) or build_mbtiles(r, args.tools, args.work, args.java_mem)
        entries.append(bake(r, mbtiles, args.tools, args.site, args.jobs))
    if not entries:
        sys.exit("焼く地域がない")
    write_index(args.site, entries)
    total = sum(e["pngBytes"] for e in entries)
    print(f"{len(entries)} regions, {sum(e['tiles'] for e in entries)} tiles, {total / 1e6:.1f} MB -> {args.site}")
    if total > 900e6:
        sys.exit("GitHub Pages のサイトは 1 GB まで。地域かズームを減らす")


if __name__ == "__main__":
    main()
