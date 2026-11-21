#!/usr/bin/env python3
"""vmt_terrain.py - 床マップの地形 (標高) を焼いて R2 に置く (ワールド側の Issue #27)。

ワールドは床ごとに 1 本の生データ (.bin) を String Loading で取り、高さのテクスチャ (Texture2D、RGB24) と、Udon で読む
足元の高さに使う。床の範囲は v4 の床の manifest の levels[0] (base のズームとタイルの範囲) と同じ。

  焼く:   python vmt_terrain.py build OUT --public https://tiles.kimkim0106.net [--regions kobe,osaka | all] [--rebuild ...]
  上げる: python vmt_terrain.py upload OUT        (R2_ACCOUNT_ID / AWS_* / R2_BUCKET。r2_upload.py と同じ)

置き方 (R2 の公開 URL の下):
  terrain/v1/data/<SHA-256 の先頭 16 桁>.bin   標高 (下の形式)。名前は中身のハッシュなので、同じ名前なら中身も同じ
                                               (上げるのは R2 に無いものだけ。古いワールドが読むファイルは消さない)
  terrain/v1/floors/<地域>.json                 床の地形の索引 (毎回上げる)。ワールドの床を作るときに読み、bin の URL と寸法を焼き込む

小分け (索引の chunks / chunkRows): 同じ .bin を北から chunkRows 行ずつ (最後は残り) に切ったもの。名前は同じく中身のハッシュで
data/ に置く。iOS の String Loading は約 800 KB から結果を途中で切る (VRChat の Canny、2026-06) ので、ワールドはまとめた 1 本が
切れて届いたら小分けを順に取ってつなぐ。1 本は CHUNK_BYTES (256 KiB) 以下。索引に小分けが無い地域は、R2 の .bin を取って
分けるだけで標高タイルは取り直さない (--rebuild に入れたときだけ焼き直す)。

.bin の形式: 床の base タイル 1 枚 = SAMPLES x SAMPLES 点 (既定 64)。1 行 = 東西に baseW x SAMPLES 点、行は北から南へ
baseH x SAMPLES 行。1 点 3 バイト (RGB24)。値は国土地理院の dem_png と同じ: x = R·2^16 + G·2^8 + B、x < 2^23 なら x × 0.01 m、
それより大きければ (x − 2^24) × 0.01 m。データなし (海、タイルが無い所) は 0 m。点はそのマスの中心の標高で、取った標高タイルの
画素の平均 (陸の画素が半分未満の点は 0 m。海岸で標高が海に引っぱられて下がるのを防ぐ)。Texture2D.LoadRawTextureData に
そのまま渡せる (行 0 = 北の端が v = 0)。

標高は国土地理院の標高タイル (dem_png、DEM10B。https://maps.gsi.go.jp/development/ichiran.html)。使うときは出典の表示が要る
(ワールドの説明板に書く)。取ったタイルは --cache に残す。取得は curl (Python の urllib はプロキシの後ろで途中で切れることがあった)。

どの地域を焼くか: --public の terrain/v1/floors/<地域>.json がまだ無い地域と、--rebuild に指定した地域 (all で全部)。
国土地理院のデータは更新されることがあるので、焼き直すと中身 (ハッシュ) が変わりうる。そのときは新しい名前で置き、
床の索引だけが新しいものを指す (古い .bin は残るので、古いワールドはそのまま読める)。
"""
import argparse
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

DEM_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"
TILE_PX = 256
MAX_DEM_ZOOM = 14          # dem_png (DEM10B) は z14 まで
PREFIX = "terrain/v1"
ATTRIBUTION = "国土地理院 標高タイル (DEM10B)"
USER_AGENT = "vrchat-osm-tiles-terrain/1"
CHUNK_BYTES = 256 * 1024   # 小分け 1 本の上限 (iOS の String Loading が切らない大きさ)


def curl(url, out=None):
    """curl で取る。(HTTP の状態コード, 中身 or None)。out を渡すとそこに書く。--compressed で gzip の索引も展開する。"""
    args = ["curl", "-sS", "--compressed", "-A", USER_AGENT, "-w", "%{http_code}"]
    args += ["-o", out] if out else ["-o", "-"]
    r = subprocess.run(args + [url], capture_output=True)
    if out:
        code = r.stdout.decode().strip()[-3:]
        return code, None
    body, code = r.stdout[:-3], r.stdout[-3:].decode(errors="replace")
    return code, body


def fetch_json(url):
    for attempt in range(4):
        code, body = curl(url)
        if code == "200":
            return json.loads(body)
        if code == "404":
            return None
        time.sleep(2 ** attempt)
    sys.exit(f"取れなかった: {url} ({code})")


def fetch_dem(z, x, y, cache):
    """標高タイル 1 枚 (256 x 256 の m、データなしは NaN)。タイルが無ければ None。"""
    path = os.path.join(cache, str(z), str(x), f"{y}.png")
    missing = path + ".404"
    if os.path.exists(missing):
        return None
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        part = path + ".part"
        for attempt in range(5):
            code, _ = curl(DEM_URL.format(z=z, x=x, y=y), part)
            if code == "404":
                open(missing, "w").close()
                return None
            if code == "200":
                try:
                    Image.open(part).verify()
                    break
                except Exception:
                    pass
            if attempt == 4:
                sys.exit(f"標高タイルを取れなかった: z{z}/{x}/{y} ({code})")
            time.sleep(2 ** attempt)
        os.replace(part, path)
        time.sleep(0.05)  # 相手のサーバーに優しく
    with open(path, "rb") as f:
        img = Image.open(io.BytesIO(f.read())).convert("RGB")
    a = np.asarray(img, dtype=np.int64)
    v = (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]
    h = np.where(v < (1 << 23), v, v - (1 << 24)).astype(np.float64) * 0.01
    h[v == (1 << 23)] = np.nan
    return h


def bake(level, samples, cache):
    """床の base タイルの範囲 (manifest の levels[0]) の標高 (m、北の行から) と、使った標高タイルのズーム。"""
    bz, bx0, by0, bw, bh = level["z"], level["minX"], level["minY"], level["w"], level["h"]
    n = samples
    k = int(math.log2(n)) + 1                 # base タイル 1 枚 = 2n 画素 (平均して n 点にする)
    fz = min(bz + k - 8, MAX_DEM_ZOOM)
    px = TILE_PX << (fz - bz) if fz >= bz else TILE_PX >> (bz - fz)  # base タイル 1 枚の画素数 (一辺)
    if px < n:
        sys.exit(f"z{fz} の標高タイルでは base タイル 1 枚に {px} 画素しかない")
    if fz >= bz:
        s = 1 << (fz - bz)
        tx0, ty0, tx1, ty1 = bx0 * s, by0 * s, (bx0 + bw) * s, (by0 + bh) * s
        off_x = off_y = 0
    else:
        s = 1 << (bz - fz)
        tx0, ty0 = bx0 // s, by0 // s
        tx1, ty1 = -(-(bx0 + bw) // s), -(-(by0 + bh) // s)
        off_x, off_y = (bx0 - tx0 * s) * px, (by0 - ty0 * s) * px
    cols, rows = tx1 - tx0, ty1 - ty0
    big = np.full((rows * TILE_PX, cols * TILE_PX), np.nan)
    got = 0
    for j in range(rows):
        for i in range(cols):
            t = fetch_dem(fz, tx0 + i, ty0 + j, cache)
            if t is not None:
                big[j * TILE_PX:(j + 1) * TILE_PX, i * TILE_PX:(i + 1) * TILE_PX] = t
                got += 1
    area = big[off_y:off_y + bh * px, off_x:off_x + bw * px]
    f = px // n
    blocks = area.reshape(bh * n, f, bw * n, f)
    valid = ~np.isnan(blocks)
    cnt = valid.sum(axis=(1, 3))
    tot = np.where(valid, blocks, 0.0).sum(axis=(1, 3))
    hgt = np.where(cnt * 2 >= f * f, tot / np.maximum(cnt, 1), 0.0)
    return np.maximum(hgt, -100.0), fz, got, cols * rows


def encode(hgt):
    q = np.round(hgt / 0.01).astype(np.int64)
    q = np.where(q < 0, q + (1 << 24), q)
    return np.stack([(q >> 16) & 255, (q >> 8) & 255, q & 255], axis=-1).astype(np.uint8).tobytes()


def write_chunks(data, width, height, datadir, public):
    """data を北から行で切って datadir に置く。(1 本の行数, URL の並び)。"""
    row_bytes = width * 3
    if len(data) != row_bytes * height:
        sys.exit(f"大きさが違う: {len(data)} / {row_bytes * height}")
    n = max(1, math.ceil(len(data) / CHUNK_BYTES))
    rows = math.ceil(height / n)
    while rows * row_bytes > CHUNK_BYTES and rows > 1:
        rows -= 1
    urls = []
    for r0 in range(0, height, rows):
        part = data[r0 * row_bytes:min(height, r0 + rows) * row_bytes]
        key = f"{hashlib.sha256(part).hexdigest()[:16]}.bin"
        with open(os.path.join(datadir, key), "wb") as f:
            f.write(part)
        urls.append(f"{public}/{PREFIX}/data/{key}")
    return rows, urls


def add_chunks(index, datadir, public):
    """R2 にある索引に小分けを足す (.bin を取って分けるだけ)。"""
    tmp = os.path.join(datadir, "_whole.tmp")
    code, _ = curl(index["url"], tmp)
    with open(tmp, "rb") as f:
        data = f.read()
    os.remove(tmp)
    if code != "200" or hashlib.sha256(data).hexdigest() != index["sha256"]:
        sys.exit(f"{index['region']}: {index['url']} を取れなかったか中身が違う ({code}, {len(data)} バイト)")
    index["chunkRows"], index["chunks"] = write_chunks(data, index["width"], index["height"], datadir, public)
    return index


def cmd_build(args):
    public = args.public.rstrip("/")
    with open("regions.json", encoding="utf-8") as f:
        names = [r["name"] for r in json.load(f)["regions"]]
    wanted = names if args.regions == "all" else [s for s in args.regions.split(",") if s]
    unknown = [s for s in wanted if s not in names]
    if unknown:
        sys.exit(f"regions.json に無い地域: {unknown}")
    rebuild = set(names if args.rebuild == "all" else [s for s in args.rebuild.split(",") if s])
    datadir = os.path.join(args.out, PREFIX, "data")
    floordir = os.path.join(args.out, PREFIX, "floors")
    os.makedirs(datadir, exist_ok=True)
    os.makedirs(floordir, exist_ok=True)
    made = 0
    for name in wanted:
        have = None if name in rebuild else fetch_json(f"{public}/{PREFIX}/floors/{name}.json")
        if have is not None:
            if have.get("chunks"):
                print(f"{name}: R2 にあるので焼かない", flush=True)
                continue
            index = add_chunks(have, datadir, public)
            with open(os.path.join(floordir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=1)
            made += 1
            print(f"{name}: R2 の .bin を小分けにした ({len(index['chunks'])} 本、{index['chunkRows']} 行ずつ)", flush=True)
            continue
        manifest = fetch_json(f"{public}/v4/floors/{name}/manifest.json")
        if manifest is None:
            print(f"{name}: 床の manifest が無いので飛ばす", flush=True)
            continue
        level = manifest["levels"][0]
        t0 = time.time()
        hgt, fz, got, total = bake(level, args.samples, args.cache)
        data = encode(hgt)
        digest = hashlib.sha256(data).hexdigest()
        key = f"{digest[:16]}.bin"
        with open(os.path.join(datadir, key), "wb") as f:
            f.write(data)
        land = hgt[hgt > 0]
        index = {
            "version": 1,
            "region": name,
            "url": f"{public}/{PREFIX}/data/{key}",
            "bytes": len(data),
            "sha256": digest,
            "encoding": "gsi-dem-rgb24",  # dem_png と同じ 24 bit、0.01 m。行 0 = 北の端
            "width": int(level["w"] * args.samples),
            "height": int(level["h"] * args.samples),
            "samplesPerTile": args.samples,
            "baseZoom": level["z"], "baseMinX": level["minX"], "baseMinY": level["minY"],
            "baseW": level["w"], "baseH": level["h"],
            "demZoom": fz,
            "max": round(float(hgt.max()), 2), "min": round(float(hgt.min()), 2),
            "p99": round(float(np.percentile(land, 99)), 2) if land.size else 0.0,
            "landFraction": round(float(land.size / hgt.size), 3),
            "attribution": ATTRIBUTION,
        }
        index["chunkRows"], index["chunks"] = write_chunks(data, index["width"], index["height"], datadir, public)
        with open(os.path.join(floordir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        made += 1
        print(f"{name}: {index['width']} x {index['height']}、最高 {index['max']} m、標高タイル z{fz} {got}/{total} 枚、"
              f"{len(data)} バイト → {key} ({time.time() - t0:.1f} 秒)", flush=True)
    print(f"焼いた地域 {made}")


def cmd_upload(args):
    import r2_upload  # aws CLI と gzip の手順は R2 の地域と同じ
    bucket = os.environ["R2_BUCKET"]
    root = os.path.join(args.out, PREFIX)
    staged = os.path.join(args.out, "_staged")
    have = r2_upload.list_keys(bucket, f"{PREFIX}/data/")
    todo = []
    for name in sorted(os.listdir(os.path.join(root, "data"))):
        if not name.endswith(".bin"):
            continue
        key = f"{PREFIX}/data/{name}"
        if key not in have:
            todo.append((os.path.join(root, "data", name), os.path.join(staged, key)))
    print(f"標高 {len(todo)} 本を上げる (R2 にあるもの {len(have)} 本)", flush=True)
    if todo:
        r2_upload.stage_files(todo)
        r2_upload.put_tree(os.path.join(staged, PREFIX, "data"), bucket, f"{PREFIX}/data/", [".bin"])
    # 索引はデータを上げた後で (索引が指す .bin が無い時間を作らない)
    floors = os.path.join(root, "floors")
    names = sorted(os.listdir(floors))
    for name in names:
        r2_upload.stage_file(os.path.join(floors, name), os.path.join(staged, PREFIX, "floors", name))
    if names:
        r2_upload.put_tree(os.path.join(staged, PREFIX, "floors"), bucket, f"{PREFIX}/floors/", [".json"])
    print(f"床の索引 {len(names)} 個を上げた", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="標高を焼く")
    b.add_argument("out")
    b.add_argument("--public", required=True, help="R2 の公開 URL (床の manifest を取り、索引の URL に使う)")
    b.add_argument("--regions", default="all", help="焼く地域 (カンマ区切り、all で全部)")
    b.add_argument("--rebuild", default="", help="R2 にあっても焼き直す地域 (カンマ区切り、all で全部)")
    b.add_argument("--samples", type=int, default=64, help="base タイル 1 枚の一辺の点の数")
    b.add_argument("--cache", default="build/dem-cache", help="取った標高タイルの置き場所")
    u = sub.add_parser("upload", help="焼いたものを R2 に上げる")
    u.add_argument("out")
    args = ap.parse_args()
    if args.cmd == "build":
        cmd_build(args)
    else:
        cmd_upload(args)


if __name__ == "__main__":
    main()
