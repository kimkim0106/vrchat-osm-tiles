#!/usr/bin/env python3
"""check_site.py - 配信中のサイトを VRChat と同じ条件で確かめる。

VRChat の Image Loading はリダイレクトを許さない (リダイレクトされるとエラー)。
各版 (v1, v2, ...) の index.json、各地域の manifest.json、タイルの一部に HEAD / GET を送り、
リダイレクトせずに 200 が返ること、タイルが image/png であることを確かめる。

  python check_site.py https://<user>.github.io/<repo>/
  python check_site.py --dists v2 https://<R2 の公開 URL>/     # R2 (v2 だけ置く)

UnityWebRequest と同じく Accept-Encoding: gzip を付けて取り、gzip で返ってきたら展開して読む。
"""
import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.request

from build_site import DISTS


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 3xx を HTTPError のまま返す


opener = urllib.request.build_opener(NoRedirect)
UA = {"User-Agent": "vrchat-osm-tiles-check", "Accept-Encoding": "gzip"}


def fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers=UA)
    try:
        with opener.open(req, timeout=30) as r:
            body = r.read() if method == "GET" else b""
            if body and r.headers.get("Content-Encoding", "") == "gzip":
                body = gzip.decompress(body)
            return r.status, r.headers.get("Content-Type", ""), body, ""
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), b"", e.headers.get("Location", "")


def check(url, method, want_type, retries):
    for i in range(retries):
        code, ctype, body, loc = fetch(url, method)
        if code == 200 and ctype.startswith(want_type):
            return body
        if 300 <= code < 400:
            # リダイレクトは待っても直らないので即失敗にする
            sys.exit(f"NG {url}: {code} -> {loc} (VRChat はリダイレクトを読めない)")
        print(f"  {url}: {code} {ctype} (再試行 {i + 1}/{retries})", flush=True)
        time.sleep(10)
    sys.exit(f"NG {url}: {code} {ctype}")


def check_dist(base, version):
    """1 つの版の index.json と、各地域の manifest、画像の一部 (と生データ) を確かめる。"""
    index = json.loads(check(base + "index.json", "GET", "application/json", 12))
    if index.get("version") != version:
        sys.exit(f"NG {base}index.json: version {index.get('version')} が {version} と違う")
    print(f"OK {base}index.json ({len(index['regions'])} regions, {index.get('commit', '')[:7]})")
    for region in index["regions"]:
        murl = base + region["manifest"]
        manifest = json.loads(check(murl, "GET", "application/json", 6))
        print(f"OK {murl}")
        root = murl.rsplit("/", 1)[0] + "/"
        images = manifest.get("sheets") or manifest["tiles"]  # 形式 2 はシート、1 はタイル
        picks = {0, len(images) // 2, len(images) - 1}
        if "sheets" not in manifest:
            for lv in manifest["levels"]:
                picks.add(lv["offset"])
        def url_of(path):
            return path if path.startswith("https://") else root + path  # v4 は共有のシート (v4/sheets/) を絶対 URL で指す

        for i in sorted(picks):
            url = url_of(images[i]["png"])
            check(url, "HEAD", "image/png", 6)
            print(f"OK {url}")
        bins = [s for s in manifest.get("sheets") or [] if s.get("bin")]
        for s in bins[:1] + bins[-1:]:
            url = url_of(s["bin"])
            check(url, "HEAD", "application/octet-stream", 6)
            print(f"OK {url}")
        # まとめた .bin (vmt_bingroup.py。Pages の地域でも R2 に置く)
        groups = manifest.get("binGroups") or []
        for g in groups[:1] + groups[-1:]:
            url = manifest["binGroupBase"] + g["bin"]
            check(url, "HEAD", "application/octet-stream", 6)
            print(f"OK {url}")


def main():
    ap = argparse.ArgumentParser(description="配信中のサイトをリダイレクトなしで読めるか確かめる")
    ap.add_argument("base")
    ap.add_argument("--dists", default=",".join(DISTS), help="確かめる版 (カンマ区切り)")
    args = ap.parse_args()
    base = args.base
    if not base.startswith("https://"):
        sys.exit("https:// の URL を渡す (http:// は https:// にリダイレクトされる)")
    if not base.endswith("/"):
        base += "/"
    for dist in args.dists.split(","):
        check_dist(base + dist + "/", int(dist[1:]))
    print("すべてリダイレクトなしで 200")


if __name__ == "__main__":
    main()
