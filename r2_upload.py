#!/usr/bin/env python3
"""r2_upload.py - build_site.py --r2 DIR で分けた地域を Cloudflare R2 のバケットに上げる。

  DIR/v2/index.json, DIR/index.html     R2 用の索引 (毎回上げる)
  DIR/v2/<name>/...                     地域 (R2 にまだ無いか、--force に指定したときだけ上げる)

R2 の地域も Pages と同じく固定する (ワールドは manifest の頂点数を焼き込む)。上げ直すのは、この実行で焼いた・まとめた地域と、
R2 に manifest.json が無い地域と、R2 にまとめた .bin しか無い地域 (下) だけ。上げる順は画像と生データが先、manifest.json が最後
(途中で止まっても、manifest が無ければ次の実行で上げ直す)。

まとめた .bin (vmt_bingroup.py。DIR/<版>/<地域>/bingroups/<まとめ方のハッシュ>/z/k.bin) は、Pages の地域のものも
ここに置く (その地域のディレクトリには manifest.json と bingroups/ だけがある)。地域が R2 にあっても、manifest の
binGroups の .bin が R2 に無ければ、それだけ上げる (まとめ方を変えるとハッシュが変わり、別のパスになる)。
Pages から R2 に移した地域 (神戸・徳島、2026-11-17) は、R2 に manifest.json とまとめた .bin だけがあり、シートが無い。
DIR にその地域の画像があり、R2 に無ければ、R2 にある manifest.json に関係なく全部を上げる (images_missing)。

v4 (vmt_v4.py) は地域ごとに分けず、DIR/v4/sheets/<z>/<ハッシュ>.png / .bin と DIR/v4/bingroups/<z>/<ハッシュ>.bin に置く
(名前は中身のハッシュ)。名前が同じなら中身も同じなので、R2 に無いものだけを上げる (一覧を 1 回取って比べる。--force は効かない)。
その後で床の manifest (DIR/v4/floors/<地域>/manifest.json と region.json) を毎回上げる (シートを上げ終わってから)。
R2 から消すものは無い (古いワールドが読むファイルを残す)。ただし --delete-legacy v4 なら、v4 の下の sheets/・bingroups/・
floors/ 以外のディレクトリ (v4 を配る前の、地域ごとの置き方 v4/<地域>/) を消す (2026-11-19、利用者の判断。どのワールドも読んでいない)。

圧縮: .bin (RGB24 の生データ) と .json / .html は gzip にして Content-Encoding: gzip を付けて置く。
PNG はもともと圧縮してあるのでそのまま。Pages も .bin を gzip で返している (UnityWebRequest は自分で展開する)。

aws CLI (S3 互換 API) を使う。環境変数:
  R2_ACCOUNT_ID, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   (R2 の API トークン)
  R2_BUCKET                                                 バケット名

  python r2_upload.py r2site --force "osaka nagoya"      # 置いてあるすべての版
  python r2_upload.py r2site --force "v3/osaka"         # その版だけ
  python r2_upload.py r2site --delete-legacy v4         # v4 の地域ごとの置き方を消す
"""
import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile

CACHE = "public, max-age=3600"
TYPES = {".png": "image/png", ".bin": "application/octet-stream",
         ".json": "application/json; charset=utf-8", ".html": "text/html; charset=utf-8"}
GZIP = {".bin", ".json", ".html"}
STORE = ("sheets", "bingroups")   # 中身のハッシュの名前で置くディレクトリ (v4)
FLOORS = "floors"                 # v4 の床の manifest
BATCH_BYTES = 2e9                 # 中身のハッシュで置くファイルを、生の大きさでこれくらいずつ gzip にして上げる


def aws(*args):
    cmd = ["aws", "--endpoint-url", f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", *args]
    print("+ aws " + " ".join(args), flush=True)
    subprocess.run(cmd, check=True)


def aws_out(*args):
    """aws を動かして標準出力の JSON を返す (結果が無ければ None)。"""
    cmd = ["aws", "--endpoint-url", f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", *args,
           "--output", "json"]
    print("+ aws " + " ".join(args), flush=True)
    r = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True)
    return json.loads(r.stdout) if r.stdout.strip() else None


def list_keys(bucket, prefix):
    """R2 の prefix の下のキーの集まり (aws がページを続けて取る)。"""
    return set(aws_out("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
                       "--query", "Contents[].Key") or [])


def upload_store(ddir, staged, bucket, dist):
    """DIR/<版>/sheets/ と bingroups/ のうち、R2 に無いファイルを上げる (画像と生データ)。"""
    have = set()
    for kind in STORE:
        have |= list_keys(bucket, f"{dist}/{kind}/")
    todo = []
    for kind in STORE:
        for root, _, files in os.walk(os.path.join(ddir, kind)):
            for name in files:
                path = os.path.join(root, name)
                key = f"{dist}/{os.path.relpath(path, ddir)}"
                if key not in have:
                    todo.append((path, key))
    print(f"{dist}: 中身のハッシュの名前のファイル {len(todo)} 個を上げる (R2 にあるもの {len(have)} 個)", flush=True)
    batch, size = [], 0
    for item in sorted(todo, key=lambda t: t[1]) + [None]:
        if item is not None:
            batch.append(item)
            size += os.path.getsize(item[0])
        if batch and (item is None or size >= BATCH_BYTES):
            bdir = os.path.join(staged, "batch")
            for path, key in batch:
                stage_file(path, os.path.join(bdir, key))
            put_tree(bdir, bucket, "", [".png", ".bin"])
            shutil.rmtree(bdir)
            batch, size = [], 0


def upload_floors(ddir, staged, bucket, dist):
    """床の manifest と region.json を上げる (毎回。シートを上げた後で)。"""
    fdir = os.path.join(ddir, FLOORS)
    for name in sorted(os.listdir(fdir)):
        src = os.path.join(fdir, name)
        if not os.path.isdir(src):
            continue
        sdir = os.path.join(staged, dist, FLOORS, name)
        for f in ("region.json", "manifest.json"):
            if os.path.exists(os.path.join(src, f)):
                stage_file(os.path.join(src, f), os.path.join(sdir, f))
        put_jsons(sdir, bucket, f"{dist}/{FLOORS}/{name}/")
        shutil.rmtree(sdir, ignore_errors=True)


def delete_legacy(bucket, dist):
    """<版>/ の下の、sheets/・bingroups/・floors/ 以外のディレクトリを消す (v4 を配る前の、地域ごとの置き方)。"""
    keep = set(STORE) | {FLOORS}
    prefixes = aws_out("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", f"{dist}/", "--delimiter", "/",
                       "--query", "CommonPrefixes[].Prefix") or []
    for p in prefixes:
        if p.split("/")[1] not in keep:
            print(f"{p}: 前の置き方なので消す", flush=True)
            aws("s3", "rm", f"s3://{bucket}/{p}", "--recursive", "--only-show-errors")


def exists(bucket, key):
    """R2 にそのキーがあるか。無いとき (404) は aws が出す「[ERROR] ... Not Found」を表示しない (想定どおりなので)。
    ほかのエラー (認証やネットワーク) はそのまま表示する。"""
    args = ("s3api", "head-object", "--bucket", bucket, "--key", key)
    cmd = ["aws", "--endpoint-url", f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", *args]
    print("+ aws " + " ".join(args), flush=True)
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode == 0:
        return True
    if "(404)" not in r.stderr and "Not Found" not in r.stderr:
        print(r.stderr.strip(), file=sys.stderr, flush=True)
    return False


def stage(src, dst):
    """src の木を dst に写す。GZIP の拡張子は gzip にする (名前はそのまま)。"""
    for root, _, files in os.walk(src):
        for name in files:
            s = os.path.join(root, name)
            stage_file(s, os.path.join(dst, os.path.relpath(s, src)))


def stage_file(s, d):
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if os.path.splitext(s)[1] in GZIP:
        with open(s, "rb") as f:
            data = f.read()
        with open(d, "wb") as f:
            f.write(gzip.compress(data, compresslevel=9, mtime=0))
    else:
        shutil.copyfile(s, d)


def put_tree(local, bucket, prefix, exts):
    """local の木のうち exts の拡張子のファイルを s3://bucket/prefix/ に上げる (拡張子ごとに型を付ける)。"""
    for ext in exts:
        args = ["s3", "cp", local, f"s3://{bucket}/{prefix}", "--recursive", "--only-show-errors",
                "--exclude", "*", "--include", f"*{ext}",
                "--content-type", TYPES[ext], "--cache-control", CACHE]
        if ext in GZIP:
            args += ["--content-encoding", "gzip"]
        aws(*args)


def main():
    ap = argparse.ArgumentParser(description="build_site.py --r2 で分けた地域を R2 に上げる")
    ap.add_argument("dir")
    ap.add_argument("--delete-legacy", default="", help="この版 (v4) の、中身のハッシュで置く前の地域ごとの置き方を消す")
    ap.add_argument("--force", default="", help="R2 にあっても上げ直す地域 (空白かカンマ区切り。v3/osaka のように版も付けると、その版だけ)")
    args = ap.parse_args()
    for k in ("R2_ACCOUNT_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "R2_BUCKET"):
        if not os.environ.get(k):
            sys.exit(f"環境変数 {k} が無い")
    bucket = os.environ["R2_BUCKET"]
    force = set(args.force.replace(",", " ").split())

    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, "r2")
        for dist in sorted(os.listdir(args.dir)):
            ddir = os.path.join(args.dir, dist)
            if not os.path.isdir(ddir):
                continue
            if os.path.isdir(os.path.join(ddir, FLOORS)):
                # v4: 中身のハッシュの名前で置いたファイル → 床の manifest
                upload_store(ddir, staged, bucket, dist)
                upload_floors(ddir, staged, bucket, dist)
                if dist == args.delete_legacy:
                    delete_legacy(bucket, dist)
            for name in sorted(os.listdir(ddir)):
                rdir = os.path.join(ddir, name)
                if not os.path.isdir(rdir):
                    continue
                if not os.path.exists(os.path.join(rdir, "manifest.json")):
                    continue  # v4 の sheets/ など (上で上げた)
                prefix = f"{dist}/{name}/"
                sdir = os.path.join(staged, dist, name)
                if name in force or f"{dist}/{name}" in force or not exists(bucket, prefix + "manifest.json") \
                        or images_missing(rdir, bucket, prefix):
                    print(f"{prefix}: 上げる")
                    stage(rdir, sdir)  # gzip にするのは上げる地域だけ
                    put_tree(sdir, bucket, prefix, [".png", ".bin"])
                    put_jsons(sdir, bucket, prefix)
                elif missing_bin_groups(rdir, bucket, prefix):
                    # 地域はあるが、まとめた .bin (まとめ方ごとに別のパス) がまだ無い。それだけ上げる
                    print(f"{prefix}: まとめた .bin を上げる")
                    stage(os.path.join(rdir, "bingroups"), os.path.join(sdir, "bingroups"))
                    put_tree(os.path.join(sdir, "bingroups"), bucket, prefix + "bingroups/", [".bin"])
                    stage_file(os.path.join(rdir, "manifest.json"), os.path.join(sdir, "manifest.json"))
                    put_jsons(sdir, bucket, prefix)
                else:
                    print(f"{prefix}: R2 にある (上げない)")
                shutil.rmtree(sdir, ignore_errors=True)
            index = os.path.join(ddir, "index.json")
            if os.path.exists(index):
                stage_file(index, os.path.join(staged, dist, "index.json"))
                aws("s3", "cp", os.path.join(staged, dist, "index.json"), f"s3://{bucket}/{dist}/index.json", "--only-show-errors",
                    "--content-type", TYPES[".json"], "--content-encoding", "gzip", "--cache-control", "no-cache")
        page = os.path.join(args.dir, "index.html")
        if os.path.exists(page):
            stage_file(page, os.path.join(staged, "index.html"))
            aws("s3", "cp", os.path.join(staged, "index.html"), f"s3://{bucket}/index.html", "--only-show-errors",
                "--content-type", TYPES[".html"], "--content-encoding", "gzip", "--cache-control", "no-cache")


def put_jsons(sdir, bucket, prefix):
    """region.json (あれば) と manifest.json を上げる。manifest.json は最後 (これが「上げ終わった」印)。"""
    for name in ("region.json", "manifest.json"):
        path = os.path.join(sdir, name)
        if os.path.exists(path):
            aws("s3", "cp", path, f"s3://{bucket}/{prefix}{name}", "--only-show-errors",
                "--content-type", TYPES[".json"], "--content-encoding", "gzip", "--cache-control", CACHE)


def images_missing(rdir, bucket, prefix):
    """DIR の地域に画像 (シートかタイル) があり、その最初と最後が R2 に無いか。
    Pages の地域 (DIR には manifest.json と bingroups/ だけ) は画像が無いので False。"""
    with open(os.path.join(rdir, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    images = [e["png"] for e in (m.get("sheets") or m.get("tiles") or [])]
    ends = images[:1] + images[-1:]
    if not ends or not all(os.path.exists(os.path.join(rdir, p)) for p in ends):
        return False
    return any(not exists(bucket, prefix + p) for p in ends)


def missing_bin_groups(rdir, bucket, prefix):
    """manifest にまとめた .bin があり、その最初と最後が R2 に無いか。"""
    with open(os.path.join(rdir, "manifest.json"), encoding="utf-8") as f:
        groups = json.load(f).get("binGroups") or []
    return any(not exists(bucket, prefix + g["bin"]) for g in groups[:1] + groups[-1:])

if __name__ == "__main__":
    main()
