#!/usr/bin/env python3
"""check_site.py - 配信中のサイトを VRChat と同じ条件で確かめる。

VRChat の Image Loading はリダイレクトを許さない (リダイレクトされるとエラー)。
v<形式の版>/index.json、各地域の manifest.json、タイルの一部に HEAD / GET を送り、
リダイレクトせずに 200 が返ること、タイルが image/png であることを確かめる。

  python check_site.py https://<user>.github.io/<repo>/
"""
import json
import sys
import time
import urllib.error
import urllib.request

from build_site import DIST, FORMAT_VERSION


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 3xx を HTTPError のまま返す


opener = urllib.request.build_opener(NoRedirect)
UA = {"User-Agent": "vrchat-osm-tiles-check"}


def fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers=UA)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), (r.read() if method == "GET" else b""), ""
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


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    base = sys.argv[1]
    if not base.startswith("https://"):
        sys.exit("https:// の URL を渡す (http:// は https:// にリダイレクトされる)")
    if not base.endswith("/"):
        base += "/"
    base += DIST + "/"

    index = json.loads(check(base + "index.json", "GET", "application/json", 12))
    if index.get("version") != FORMAT_VERSION:
        sys.exit(f"NG {base}index.json: version {index.get('version')} が {FORMAT_VERSION} と違う")
    print(f"OK {base}index.json ({len(index['regions'])} regions, {index.get('commit', '')[:7]})")
    for region in index["regions"]:
        murl = base + region["manifest"]
        manifest = json.loads(check(murl, "GET", "application/json", 6))
        print(f"OK {murl}")
        root = murl.rsplit("/", 1)[0] + "/"
        tiles = manifest["tiles"]
        picks = {0, len(tiles) // 2, len(tiles) - 1}
        for lv in manifest["levels"]:
            picks.add(lv["offset"])
        for i in sorted(picks):
            url = root + tiles[i]["png"]
            check(url, "HEAD", "image/png", 6)
            print(f"OK {url}")
    print("すべてリダイレクトなしで 200")


if __name__ == "__main__":
    main()
