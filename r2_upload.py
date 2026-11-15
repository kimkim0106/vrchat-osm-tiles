#!/usr/bin/env python3
"""r2_upload.py - build_site.py --r2 DIR で分けた地域を Cloudflare R2 のバケットに上げる。

  DIR/v2/index.json, DIR/index.html     R2 用の索引 (毎回上げる)
  DIR/v2/<name>/...                     地域 (R2 にまだ無いか、--force に指定したときだけ上げる)

R2 の地域も Pages と同じく固定する (ワールドは manifest の頂点数を焼き込む)。上げ直すのは、この実行で焼いた・まとめた地域と、
R2 に manifest.json が無い地域だけ。上げる順は画像と生データが先、manifest.json が最後
(途中で止まっても、manifest が無ければ次の実行で上げ直す)。

まとめた .bin (vmt_bingroup.py。DIR/<版>/<地域>/bingroups/<まとめ方のハッシュ>/z/k.bin) は、Pages の地域のものも
ここに置く (その地域のディレクトリには manifest.json と bingroups/ だけがある)。地域が R2 にあっても、manifest の
binGroups の .bin が R2 に無ければ、それだけ上げる (まとめ方を変えるとハッシュが変わり、別のパスになる)。

圧縮: .bin (RGB24 の生データ) と .json / .html は gzip にして Content-Encoding: gzip を付けて置く。
PNG はもともと圧縮してあるのでそのまま。Pages も .bin を gzip で返している (UnityWebRequest は自分で展開する)。

aws CLI (S3 互換 API) を使う。環境変数:
  R2_ACCOUNT_ID, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   (R2 の API トークン)
  R2_BUCKET                                                 バケット名

  python r2_upload.py r2site --force "osaka nagoya"      # 置いてあるすべての版
  python r2_upload.py r2site --force "v3/osaka"         # その版だけ
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


def aws(*args, check=True):
    cmd = ["aws", "--endpoint-url", f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", *args]
    print("+ aws " + " ".join(args), flush=True)
    return subprocess.run(cmd, check=check, stdout=subprocess.DEVNULL if not check else None).returncode


def exists(bucket, key):
    return aws("s3api", "head-object", "--bucket", bucket, "--key", key, check=False) == 0


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
            for name in sorted(os.listdir(ddir)):
                rdir = os.path.join(ddir, name)
                if not os.path.isdir(rdir):
                    continue
                prefix = f"{dist}/{name}/"
                sdir = os.path.join(staged, dist, name)
                if name in force or f"{dist}/{name}" in force or not exists(bucket, prefix + "manifest.json"):
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


def missing_bin_groups(rdir, bucket, prefix):
    """manifest にまとめた .bin があり、その最初と最後が R2 に無いか。"""
    with open(os.path.join(rdir, "manifest.json"), encoding="utf-8") as f:
        groups = json.load(f).get("binGroups") or []
    return any(not exists(bucket, prefix + g["bin"]) for g in groups[:1] + groups[-1:])

if __name__ == "__main__":
    main()
