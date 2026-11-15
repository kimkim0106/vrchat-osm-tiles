#!/usr/bin/env python3
"""vmt_bingroup.py - VMT-DT 2 のシートを何枚か縦につないだ生データ (まとめた .bin) を作り、manifest に書き足す。

VRChat の String Loading はシーン全体で 5 秒に 1 回しか取れない (Image Loading とは別)。シートの .bin (1 枚 6〜12 MB) を
1 本ずつ取ると、足元の z14 のように枚数が多いところで回数が足りない。まとめた .bin は数枚分を 1 回で取る。

まとめた .bin の中身: シートの生データ (RGB24、先頭行が論理行 0) をシートの順に縦につないだもの。描く側は全体を 1 枚の
Texture2D (幅 2048、高さ = 合計の行数) にして、各タイルの開始行にシートの開始行 (binGroupRow) を足して読む。
シェーダーの判定はテクスチャ先頭のヘッダ (先頭のシートの行 0) で行うので、先頭のシートのヘッダの列 7 (高さ) だけを
まとめた高さに書き換える。ほかの行はシートのまま。高さは max-rows (既定 8192。Quest でも作れる大きさ) まで。

まとめ方: ズームごとに、シートの順 (vmt_pack.py が Z 順に詰めた順) に、高さの合計が max-rows に収まるだけ続けてまとめる。
近いシートが同じまとめに入る (東京 v3 の床の足元 3 x 3 マスなら、どこに立っても中央値 3 回、多くて 6 回で取れる)。

manifest に足すもの (タイルの中身と頂点数は変えない):
  binGroupBase              まとめた .bin を置く URL (シートと別の場所に置ける。末尾は /)
  binGroups[g]              {"z", "bin": "bingroups/<まとめ方のハッシュ>/z/k.bin", "sheets": [シートの番号...], "height", "binBytes"}
  sheets[s].binGroup        シートが入っているまとめの番号 (入っていなければ書かない)
  sheets[s].binGroupRow     まとめの中でシートが始まる行

使い方:
  python vmt_bingroup.py site/v3/tokyo --zooms 13,14 --base-url https://tiles.example.net/v3/tokyo/ --bins r2site/v3/tokyo
      site/v3/tokyo/manifest.json に書き足し、r2site/v3/tokyo/bingroups/<ハッシュ>/z/k.bin と manifest.json (同じもの) を書く。
      src と --bins は同じでもよい (R2 に置く地域)。
      --bins を付けなければ manifest だけ書き足す (中身はシートの大きさだけで決まるので、.bin を作らなくても同じになる)。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

from vmt_bake import TEX_W

DEFAULT_MAX_ROWS = 8192
HEIGHT_COL = 7  # ヘッダの列 7 = テクスチャの高さ (24bit)


def plan_groups(sheets, zooms, max_rows):
    """
    シートの番号の並びを、まとめごとに返す [(z, [s, ...]), ...]。1 枚だけのまとめは作らない (シートの .bin と同じなので)。
    ズームごとに、シートの順に高さの合計が max_rows に収まるだけ続けてまとめる。
    (z12 のタイルごとにそろえる案も試したが、東京 v3 の床の足元 3 x 3 マスで取得の回数の中央値が 3 回から 4 回に増えた。
    取るシートの枚数は 15 枚から 12 枚に減るが、効くのは 5 秒に 1 回の回数のほう)
    """
    out = []
    for z in sorted(zooms):
        cur, rows = [], 0
        for s, e in enumerate(sheets):
            if e["z"] != z:
                continue
            if cur and rows + e["height"] > max_rows:
                out.append((z, cur))
                cur, rows = [], 0
            cur.append(s)
            rows += e["height"]
        if cur:
            out.append((z, cur))
    return [(z, members) for z, members in out if len(members) > 1]


def sheet_rows(src, e):
    """シートの生データ (高さ x 2048 x 3)。.bin があればそれを、無ければ PNG を読む。"""
    if e.get("bin") and os.path.exists(os.path.join(src, e["bin"])):
        a = np.fromfile(os.path.join(src, e["bin"]), dtype=np.uint8)
        return a.reshape(e["height"], TEX_W, 3)
    img = Image.open(os.path.join(src, e["png"]))
    if img.mode != "RGB":
        sys.exit(f"{e['png']}: RGB ではない ({img.mode})")
    a = np.asarray(img, dtype=np.uint8)
    if a.shape != (e["height"], TEX_W, 3):
        sys.exit(f"{e['png']}: 大きさ {a.shape} が manifest と違う")
    return a


def int24(v):
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def main():
    ap = argparse.ArgumentParser(description="シートを縦につないだ .bin を作り、manifest に書き足す")
    ap.add_argument("src", help="vmt_pack.py の出力 (manifest.json と sheets/ のあるディレクトリ)")
    ap.add_argument("--zooms", required=True, help="まとめるズーム (例: 13,14)")
    ap.add_argument("--base-url", required=True, help="まとめた .bin を置く URL (末尾は /)")
    ap.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    ap.add_argument("--bins", default=None, help="まとめた .bin を書くディレクトリ (無ければ manifest だけ書き足す)")
    args = ap.parse_args()
    zooms = {int(v) for v in args.zooms.split(",") if v.strip()}
    if not args.base_url.startswith("https://") and not args.base_url.startswith("http://127.0.0.1"):
        sys.exit("--base-url は https:// (手元の確認だけ http://127.0.0.1)")
    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"

    path = os.path.join(args.src, "manifest.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if m.get("format") != "VMT-DT" or m.get("version") != 2:
        sys.exit("入力は VMT-DT 2 (シート) の manifest であること")
    sheets = m["sheets"]
    for e in sheets:
        e.pop("binGroup", None)
        e.pop("binGroupRow", None)
        if e["height"] > args.max_rows:
            sys.exit(f"{e['png']}: 高さ {e['height']} が --max-rows {args.max_rows} を超える")

    plan = plan_groups(sheets, zooms, args.max_rows)
    # まとめ方 (どのシートを何行ずつ) のハッシュをパスに入れる。ワールドはまとめの高さを焼き込むので、
    # まとめ方を変えたら別のパスに置き、古いワールドの読むファイルは残す (r2_upload.py は消さない)
    key = json.dumps([[z, members, [sheets[s]["height"] for s in members]] for z, members in plan])
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    groups = []
    if args.bins and os.path.exists(os.path.join(args.bins, "bingroups")):
        shutil.rmtree(os.path.join(args.bins, "bingroups"))
    counts = {}
    for z, members in plan:
        k = counts.get(z, 0)
        counts[z] = k + 1
        rel = f"bingroups/{digest}/{z}/{k}.bin"
        rows = 0
        for s in members:
            sheets[s]["binGroup"] = len(groups)
            sheets[s]["binGroupRow"] = rows
            rows += sheets[s]["height"]
        groups.append({"z": z, "bin": rel, "sheets": members, "height": rows, "binBytes": rows * TEX_W * 3})
        if args.bins:
            parts = [sheet_rows(args.src, sheets[s]) for s in members]
            data = np.concatenate(parts, axis=0)
            data[0, HEIGHT_COL] = int24(rows)  # 先頭のヘッダの高さを、まとめた高さにする (シェーダーの大きさの検査)
            out = os.path.join(args.bins, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            data.tofile(out)
        print(f"{rel}: sheets {','.join(map(str, members))}, {rows} rows, {rows * TEX_W * 3 / 1e6:.1f} MB",
              flush=True)

    if groups:
        m["binGroupBase"] = base
        m["binGroups"] = groups
    else:
        m.pop("binGroupBase", None)
        m.pop("binGroups", None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    if args.bins:
        os.makedirs(args.bins, exist_ok=True)
        dst = os.path.join(args.bins, "manifest.json")
        if not os.path.exists(dst) or not os.path.samefile(path, dst):
            shutil.copyfile(path, dst)
    total = sum(b["binBytes"] for b in groups)
    print(f"{len(groups)} groups, {total / 1e6:.1f} MB -> {base}")


if __name__ == "__main__":
    main()
