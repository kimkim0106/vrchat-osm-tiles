#!/usr/bin/env python3
"""vmt_pack.py - VMT-DT 1 のタイル (1 タイル = 1 画像) を、VMT-DT 2 のシート (複数タイル = 1 画像) にまとめる。

VRChat の Image Loading はシーン全体で 5 秒に 1 枚しか取れない。タイルは幅 2048 で高さが数百行しかないので、
縦に積んで 2048 x 2048 までの 1 枚にまとめると、取得の回数が 1/10 以下になる。タイルの中身は 1 と同じ。

入力 : vmt_bake.py の出力 (manifest.json と tiles/z/x/y.png)
出力 : sheets/{z}/{k}.png   シート (8bit RGB)
       sheets/{z}/{k}.bin   同じ中身の生データ (RGB24、先頭行が論理行 0)。--bin-zooms のズームだけ。
                           String Loading で取って LoadRawTextureData する用 (Image Loading と並行して取れる)
       manifest.json        version 2。tiles[i] に sheet (sheets の番号) と row (シート内の開始行) を足し、sheets を足す

シートの形式 (VMT-DT 2)。幅 2048、高さ H <= 2048。論理行 r = 0..H-1。
  行 0      ヘッダ (検出用の項目は VMT-DT 1 のタイルと同じ位置)
              列 0        マーカー TOP = (0x56, 0x4D, 0x54)
              列 1        (形式バージョン 2, 0, 0)
              列 2        プローブ (128, 128, 128)
              列 3        タイル数 (24bit)
              列 4        (z, 0, 0)
              列 7 / 8    シートの高さ / 幅 (24bit)
              列 16..271  較正ランプ
  行 1      列 i = i 番目のタイルの開始行 (24bit)。列 2047 は行タグ (0xA5, 1, 0x5A)
  行 2      列 i = i 番目のタイルの行数 (24bit)。  列 2047 は行タグ (0xA5, 2, 0x5A)
  行 3..    タイル (VMT-DT 1 のタイルをそのまま。タイル内の行番号は開始行からの相対)
  行 H-1    列 0 にマーカー BOTTOM = (0x62, 0x6F, 0x74)

描く側は、シートのヘッダで色空間と上下反転を判定し、タイルを読むときだけ開始行を足す (VmtCore.cginc の row0)。

まとめ方: ズームごとに、タイルを Z 順 (Morton 順) に並べて詰める。近いタイルが同じシートに入り、
z14 は同じ z13 の子 4 枚が並ぶ。足元から取る床マップでも、無駄に遠くのタイルを取りにくい。

使い方:
  python vmt_pack.py site/v1/tokyo site/v2/tokyo --bin-zooms 13
"""
import argparse
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

from vmt_bake import MARK_BOTTOM, MARK_TOP, PROBE, RAMP_COL, TEX_W, MAX_H, ramp_value

SHEET_VERSION = 2
SHEET_HEADER_ROWS = 3
SHEET_CAPACITY = MAX_H - SHEET_HEADER_ROWS - 1  # タイルに使える行数


def morton(x, y):
    """(x, y) を交互にビットを並べた Z 順の番号にする。"""
    m = 0
    for b in range(16):
        m |= ((x >> b) & 1) << (2 * b) | ((y >> b) & 1) << (2 * b + 1)
    return m


def int24(v):
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def load_tile(path):
    img = Image.open(path)
    if img.mode != "RGB":
        sys.exit(f"{path}: RGB ではない ({img.mode})")
    a = np.asarray(img, dtype=np.uint8)
    if a.shape[1] != TEX_W:
        sys.exit(f"{path}: 幅が {TEX_W} ではない")
    return a


def plan_sheets(manifest):
    """
    シートごとのタイル番号の並びを返す。ズームごとに、親タイル (1 段粗いズーム) の子 4 枚を 1 組にして、
    組を親の Z 順に詰める。組はシートをまたがない (入らなければ次のシートから)。
    床マップは足元の base 3 x 3 の子 (detail 36 枚) を取るので、組を割らないほうが取るシートが少ない。
    """
    tiles = manifest["tiles"]
    sheets = []
    for lv in manifest["levels"]:
        groups = {}
        # そのズームのタイル (範囲の一部だけのこともある。VMT-DT v4 は持ち主の地域が持つタイルだけを焼く)
        for i in (i for i, t in enumerate(tiles) if t["z"] == lv["z"]):
            t = tiles[i]
            if t["height"] > SHEET_CAPACITY:
                sys.exit(f"タイル {i} の高さ {t['height']} がシートに入らない")
            groups.setdefault((t["x"] >> 1, t["y"] >> 1), []).append(i)
        px0, py0 = lv["minX"] >> 1, lv["minY"] >> 1
        order = sorted(groups, key=lambda p: morton(p[0] - px0, p[1] - py0))
        cur, used = [], 0
        for p in order:
            members = sorted(groups[p], key=lambda i: (tiles[i]["y"], tiles[i]["x"]))
            h = sum(tiles[i]["height"] for i in members)
            if cur and used + h > SHEET_CAPACITY:
                sheets.append((lv["z"], cur))
                cur, used = [], 0
            if h > SHEET_CAPACITY:  # 子 4 枚で 1 枚に入らないときだけ、組を割る
                for i in members:
                    if used + tiles[i]["height"] > SHEET_CAPACITY:
                        sheets.append((lv["z"], cur))
                        cur, used = [], 0
                    cur.append(i)
                    used += tiles[i]["height"]
                continue
            cur.extend(members)
            used += h
        if cur:
            sheets.append((lv["z"], cur))
    return sheets


def build_sheet(z, tiles, arrays):
    n = len(tiles)
    h = SHEET_HEADER_ROWS + sum(arrays[i].shape[0] for i in tiles) + 1
    sheet = np.zeros((h, TEX_W, 3), dtype=np.uint8)
    sheet[0, 0] = MARK_TOP
    sheet[0, 1] = (SHEET_VERSION, 0, 0)
    sheet[0, 2] = PROBE
    sheet[0, 3] = int24(n)
    sheet[0, 4] = (z, 0, 0)
    sheet[0, 7] = int24(h)
    sheet[0, 8] = int24(TEX_W)
    for i in range(256):
        sheet[0, RAMP_COL + i] = ramp_value(i)
    sheet[1, TEX_W - 1] = (0xA5, 1, 0x5A)
    sheet[2, TEX_W - 1] = (0xA5, 2, 0x5A)
    rows = {}
    r = SHEET_HEADER_ROWS
    for k, i in enumerate(tiles):
        a = arrays[i]
        sheet[r:r + a.shape[0]] = a
        sheet[1, k] = int24(r)
        sheet[2, k] = int24(a.shape[0])
        rows[i] = r
        r += a.shape[0]
    sheet[h - 1, 0] = MARK_BOTTOM
    return sheet, rows


def main():
    ap = argparse.ArgumentParser(description="VMT-DT 1 のタイルを VMT-DT 2 のシートにまとめる")
    ap.add_argument("src", help="vmt_bake.py の出力 (manifest.json のあるディレクトリ)")
    ap.add_argument("out")
    ap.add_argument("--bin-zooms", default="", help="生データ (.bin) も書くズーム (例: 13 または 12,13)")
    args = ap.parse_args()
    bin_zooms = {int(v) for v in args.bin_zooms.split(",") if v.strip()}

    with open(os.path.join(args.src, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("format") != "VMT-DT" or manifest.get("version") != 1:
        sys.exit("入力は VMT-DT 1 の manifest であること")
    tiles = manifest["tiles"]
    arrays = []
    for t in tiles:
        a = load_tile(os.path.join(args.src, t["png"]))
        if a.shape[0] != t["height"]:
            sys.exit(f"{t['png']}: 高さ {a.shape[0]} が manifest の {t['height']} と違う")
        arrays.append(a)

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    sheet_entries = []
    tile_sheet = [None] * len(tiles)
    tile_row = [None] * len(tiles)
    for k, (z, members) in enumerate(plan_sheets(manifest)):
        sheet, rows = build_sheet(z, members, arrays)
        d = os.path.join(args.out, "sheets", str(z))
        os.makedirs(d, exist_ok=True)
        n_in_zoom = sum(1 for e in sheet_entries if e["z"] == z)
        png = f"sheets/{z}/{n_in_zoom}.png"
        Image.fromarray(sheet, "RGB").save(os.path.join(args.out, png), optimize=True)
        # 書いた PNG を読み直して、タイルが元と同じか確かめる
        back = np.asarray(Image.open(os.path.join(args.out, png)), dtype=np.uint8)
        for i in members:
            r = rows[i]
            if not np.array_equal(back[r:r + arrays[i].shape[0]], arrays[i]):
                sys.exit(f"{png}: タイル {i} が元と一致しない")
        entry = {"z": z, "png": png, "height": int(sheet.shape[0]), "tiles": members,
                 "pngBytes": os.path.getsize(os.path.join(args.out, png))}
        if z in bin_zooms:
            bin_path = f"sheets/{z}/{n_in_zoom}.bin"
            with open(os.path.join(args.out, bin_path), "wb") as f:
                f.write(sheet.tobytes())
            entry["bin"] = bin_path
            entry["binBytes"] = os.path.getsize(os.path.join(args.out, bin_path))
        sheet_entries.append(entry)
        for i in members:
            tile_sheet[i] = k
            tile_row[i] = rows[i]
        print(f"{png}: {len(members):3d} tiles, {sheet.shape[0]:4d} rows, png {entry['pngBytes'] / 1e6:.2f} MB"
              + (f", bin {entry['binBytes'] / 1e6:.2f} MB" if "bin" in entry else ""), flush=True)

    out_tiles = []
    for i, t in enumerate(tiles):
        e = {k: v for k, v in t.items() if k not in ("png", "bin", "pngBytes", "binBytes")}
        e["sheet"] = tile_sheet[i]
        e["row"] = tile_row[i]
        out_tiles.append(e)
    out = dict(manifest)
    out["version"] = SHEET_VERSION
    out["tileVersion"] = manifest["version"]
    out["tiles"] = out_tiles
    out["sheets"] = sheet_entries
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    total_png = sum(e["pngBytes"] for e in sheet_entries)
    total_bin = sum(e.get("binBytes", 0) for e in sheet_entries)
    print(f"{len(tiles)} tiles -> {len(sheet_entries)} sheets, png {total_png / 1e6:.1f} MB, bin {total_bin / 1e6:.1f} MB -> {args.out}")


if __name__ == "__main__":
    main()
