#!/usr/bin/env python3
"""plan_build.py - Actions の plan job と publish job が使う。Release にある地域から、焼く地域と戻す地域を決める。

Release (タグ = 版) には <地域>.tar.gz が並ぶ。その一覧を JSON で渡す:
  {"v1": ["tokyo", "kobe", ...], "v2": [...], "v3": [...], "v4": [...]}

  python plan_build.py released > released.json
      その一覧を gh で集める (ダウンロードはしない)。

  python plan_build.py plan released.json --rebuild "kobe,osaka"
      焼く地域を決める。出力 (JSON):
        matrix    {"include": [{"name": "kobe", "build": true, "pack": true, "poi": true, "glyph": true}, ...]}
                  build = v1 を焼く (POI なし)、pack = v2 を v1 からまとめる、poi = v3 を焼く、
                  glyph = v4 (タイルの形式 2) のラベルの候補を集める (bake job。焼くのは glyph job)
        glyph_matrix  {"include": [{"name": "kobe"}, ...]}  v4 を焼く地域 (glyph job。vmt_v4.py の 3 段目)
        glyph_count   その数
        glyph_released  Release (v4) にある地域 (カンマ区切り)。v4 の持ち主を決めるのに、どの job にも同じものを渡す
        made      この実行で作る版と地域 (v3/kobe のように。空白区切り。R2 に上げ直す)
        releases  焼いた地域を保存する版のうち、まだ Release が無いもの (plan job が作る)
        count     matrix の地域の数

  python plan_build.py restore released.json
      Pages / R2 に出すために戻す (tag, name) を 1 行ずつ出す。regions.json の dists に無い版は戻さない
      (配らないので。releaseOnly の版もここで外れる)。regions.json に無い地域は、Release にある限りすべて戻す
      (regions.json から消しても、古いワールドのために配り続ける)。

  python plan_build.py dists site
      site/ の版のうち、地域が 1 つでもあるもの (カンマ区切り。配信後に check_site.py で確かめる版)。

焼く条件 (build_site.py の wants_dist が「作る版」を決める):
  v1   その地域で v1 を作り、v1 が Release に無いか、rebuild に指定した
  v2   その地域で v2 を作り、v1 を焼くか、v2 が Release に無い
  v3   その地域で v3 を作り、v3 が Release に無いか、rebuild に指定した
  v4   その地域で v4 を作り、v4 が Release に無いか、rebuild に指定した。焼くのはその地域が持ち主のタイルだけ (vmt_v4.py)。
       v4 の地域を焼き直すと、その地域のタイルを指すほかの地域の床も変わる (頂点数を焼き込んだワールドは作り直す)
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_site as b  # noqa: E402


def load_regions(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["regions"]


def plan(regions, released, rebuild):
    have = {d: set(released.get(d) or []) for d in b.DISTS}
    names = {r["name"] for r in regions}
    forced = set(n.strip() for n in rebuild.split(",") if n.strip())
    if "all" in forced:
        forced = set(names)
    unknown = forced - names
    if unknown:
        sys.exit(f"rebuild に regions.json に無い地域: {', '.join(sorted(unknown))}")
    include = []
    for r in regions:
        n = r["name"]
        build = pack = poi = glyph = False
        if b.wants_dist(r, b.DIST):
            build = n in forced or n not in have[b.DIST]
        if b.wants_dist(r, b.SHEET_DIST):
            pack = build or n not in have[b.SHEET_DIST]
        if b.wants_dist(r, b.POI_DIST):
            poi = n in forced or n not in have[b.POI_DIST]
        if b.wants_dist(r, b.GLYPH_DIST):
            glyph = n in forced or n not in have[b.GLYPH_DIST]
        if pack and not build and n not in have[b.DIST]:
            sys.exit(f"{n}: v2 を作るのに v1 が Release に無い")  # wants_dist が v2 なら v1 も作るので、ここには来ない
        if build or pack or poi or glyph:
            include.append({"name": n, "build": build, "pack": pack, "poi": poi, "glyph": glyph})
    made = [f"{d}/{e['name']}" for e in include
            for d, k in ((b.DIST, "build"), (b.SHEET_DIST, "pack"), (b.POI_DIST, "poi"), (b.GLYPH_DIST, "glyph"))
            if e[k]]
    needed = {m.split("/")[0] for m in made}
    releases = [d for d in b.DISTS if d in needed and d not in released]
    glyph = [{"name": e["name"]} for e in include if e["glyph"]]
    return {"matrix": {"include": include}, "made": " ".join(made), "releases": releases, "count": len(include),
            "glyph_matrix": {"include": glyph}, "glyph_count": len(glyph),
            "glyph_released": ",".join(sorted(have[b.GLYPH_DIST]))}


def restore(regions, released):
    by_name = {r["name"]: r for r in regions}
    out = []
    for dist in sorted(released, key=lambda d: int(d[1:])):
        for n in sorted(released[dist]):
            r = by_name.get(n)
            if r is not None and r.get("dists") is not None and dist not in r["dists"]:
                continue
            out.append((dist, n))
    return out


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def list_released():
    """Release (タグ v<数字>) ごとの <地域>.tar.gz の地域名を集める (ダウンロードはしない)。"""
    tags = gh("release", "list", "--limit", "100", "--json", "tagName",
              "--jq", '.[].tagName | select(test("^v[0-9]+$"))').split()
    return {t: sorted(n[:-len(".tar.gz")] for n in gh("release", "view", t, "--json", "assets",
                                                         "--jq", ".assets[].name").split()
                      if n.endswith(".tar.gz"))
            for t in tags}


def present_dists(site):
    """site/ の版のうち、索引に地域が 1 つでもあるもの (配信後に確かめる版)。"""
    out = []
    for dist in b.dist_dirs(site):
        path = os.path.join(site, dist, "index.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if json.load(f)["regions"]:
                    out.append(dist)
    return out


def main():
    ap = argparse.ArgumentParser(description="Release の地域から、焼く地域と戻す地域を決める")
    ap.add_argument("mode", choices=["released", "plan", "restore", "dists"])
    ap.add_argument("path", nargs="?",
                    help="plan / restore: Release にある地域 ({版: [地域, ...]}) の JSON。dists: site のディレクトリ")
    ap.add_argument("--regions", default=os.path.join(HERE, "regions.json"))
    ap.add_argument("--rebuild", default="", help="焼き直す地域 (カンマ区切り、all で全部)")
    args = ap.parse_args()
    if args.mode == "released":
        print(json.dumps(list_released()))
        return
    if not args.path:
        ap.error(f"{args.mode} には path が要る")
    if args.mode == "dists":
        print(",".join(present_dists(args.path)))
        return
    regions = load_regions(args.regions)
    b.validate_regions(regions)
    with open(args.path, encoding="utf-8") as f:
        released = json.load(f)
    if args.mode == "plan":
        print(json.dumps(plan(regions, released, args.rebuild), ensure_ascii=False))
    else:
        for dist, n in restore(regions, released):
            print(dist, n)


if __name__ == "__main__":
    main()
