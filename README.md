# vrchat-osm-tiles

OpenStreetMap のベクタータイルを、VRChat のワールドで描くための「データテクスチャ」(VMT-DT v1) に焼き、
GitHub Pages で配るリポジトリです。タイルは見た目は画像ですが、中身は頂点・色・地名・文字の形のデータです。
描く側 (UdonSharp とシェーダー) はワールドのプロジェクトにあります。

## 配信 URL

```
https://kimkim0106.github.io/vrchat-osm-tiles/v1/index.json                  地域の一覧
https://kimkim0106.github.io/vrchat-osm-tiles/v1/<地域>/manifest.json        タイルの並びと大きさ
https://kimkim0106.github.io/vrchat-osm-tiles/v1/<地域>/tiles/<z>/<x>/<y>.png
https://kimkim0106.github.io/vrchat-osm-tiles/v2/index.json
https://kimkim0106.github.io/vrchat-osm-tiles/v2/<地域>/manifest.json        タイルがどのシートの何行目にあるか
https://kimkim0106.github.io/vrchat-osm-tiles/v2/<地域>/sheets/<z>/<k>.png   シート (複数タイル = 1 画像)
https://kimkim0106.github.io/vrchat-osm-tiles/v2/<地域>/sheets/<z>/<k>.bin   同じシートの生データ (binZooms のズームだけ)
```

ワールドはこの URL をアップロード時に焼き込みます (Udon は実行時に VRCUrl を作れない)。
**リポジトリ名・地域の `name`・パスの形は変えないでください。** 変えると公開済みのワールドで地図が出なくなります。

`v1` / `v2` は VMT-DT の形式の版です (manifest と index.json の `version`、テクスチャのヘッダ列 1 と同じ番号)。
ワールドの読み方が変わる変更は形式の版を上げて次のディレクトリに出し、古い版も配り続けます。

## 焼いた地域は固定する

ワールドは作るときに manifest の頂点数を焼き込みます。同じ URL の中身を焼き直すと、頂点が増えたタイルで三角形が欠けます
(同じ日に OSM を取り直しただけでも、東京 563 枚のうち 10 枚で増えた)。そのため、一度焼いた地域は勝手に焼き直しません。

- 焼いた地域は Release (タグ = 版。[v1](https://github.com/kimkim0106/vrchat-osm-tiles/releases/tag/v1)、[v2](https://github.com/kimkim0106/vrchat-osm-tiles/releases/tag/v2)) に `<地域>.tar.gz` として保存します。
- v2 は v1 を `vmt_pack.py` でまとめて作ります。v1 を焼き直すと v2 も作り直します。
- Actions は毎回すべての版の Release を戻し、まだ無い地域だけを焼いて足します。スクリプトを直して push しても、焼いた地域は変わりません。
- `regions.json` から消しても、Release に残っている地域は配り続けます (古いワールドのため)。止めるには Release のファイルを消します。
- OSM のデータを新しくするときは、Actions を手動実行して `rebuild` に地域名 (または `all`) を入れます。
  その地域を使うワールドは、新しい manifest で床マップやボードを作り直して上げ直してください。

| 版 | 中身 |
|---|---|
| v1 | タイル 1 枚 = 画像 1 枚 |
| v2 | 複数のタイルを 2048 × 2048 までの 1 枚 (シート) にまとめる。タイルの中身は v1 と同じ。形式は `vmt_pack.py` の冒頭 |

## VRChat の制約と、このリポジトリでの対応

| 制約 | 対応 |
|---|---|
| Image Loading はリダイレクトを許さない | ファイルを直接指す `https://` の URL だけを使う。独自ドメインは設定しない (`github.io` から 301 になる)。配信後に `check_site.py` が「リダイレクトなしで 200」を確かめる |
| 信頼済みドメインでないと既定では読めない | `*.github.io` は Image Loading・String Loading の両方で信頼済み (`raw.githubusercontent.com` は対象外) |
| 画像は 2048 × 2048 まで | タイルの幅は 2048 固定、高さは数百行 |
| 画像はシーン全体で 5 秒に 1 枚 | v2 で複数タイルを 1 枚にまとめる (東京 563 タイル → 47 枚)。親タイルの子 4 枚を割らずに Z 順で詰めるので、足元の 36 タイルは中央値 5 枚に入る |
| 文字列もシーン全体で 5 秒に 1 回 (画像とは別) | `binZooms` のズームのシートは生データ (.bin、RGB24) も置き、String Loading で画像と並行して取れるようにする。生データは PNG の 3〜4 倍の大きさなので、床の全体に使うズームだけにする |

GitHub Pages はサイト 1 GB まで、帯域は月 100 GB が目安です。`build_site.py` は合計 900 MB を超えると止まります。

## 地域を足す

`regions.json` に足して main に push すると、Actions がその地域だけ焼いて Release に保存し、配ります。

```json
{"name": "osaka", "title": "大阪", "area": "kansai", "bbox": "135.40,34.60,135.60,34.75", "zooms": "10-14", "lang": "ja", "binZooms": [13]}
```

- `area` は planetiler の `--area` (Geofabrik の地域名)。`bbox` は 西,南,東,北 の経緯度。
- `name` は URL の一部になるので英小文字で。
- `binZooms` は v2 で生データも置くズーム (床マップの全体に使うズーム)。東京の z13 で約 80 MB。
- `dists` を書くと、その版だけを Pages に載せる (例 `["v2"]`)。v2 は v1 から作るので v1 も焼いて Release に保存するが、
  サイトには載せない。v1 を読むのは卓上ボード (VmtMap) だけなので、東京より後の地域は v2 だけにしてサイトの容量を空けている。

今の地域 (床マップに合わせて、どれも z13 で 10 × 11 枚 = 床 20 m × 22 m):

| name | 地域 | 中心 | 配る版 |
|---|---|---|---|
| tokyo | 東京 | (東京駅は中心から東へ 0.5 枚、南へ 0.3 枚) | v1, v2 |
| kobe | 神戸 | JR 三ノ宮駅 (z13 のタイルの境目にそろえたので、駅は中心から東へ 0.45 枚、北へ 0.2 枚) | v2 |
| tokushima | 徳島 | 徳島駅 (駅は中心から西へ 0.2 枚、北へ 0.1 枚) | v2 |

## 手元で焼く

Java 21 と Python 3 が要ります。

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p build/tools
curl -fsSL -o build/tools/planetiler.jar https://github.com/onthegomap/planetiler/releases/download/v0.10.2/planetiler.jar
curl -fsSL -o build/tools/NotoSansCJK-Regular.ttc https://github.com/notofonts/noto-cjk/raw/Sans2.004/Sans/OTC/NotoSansCJK-Regular.ttc
.venv/bin/python build_site.py --tools build/tools --work build --site site --jobs 4
```

既にある MBTiles を使うときは `--mbtiles tokyo=path/to/tokyo.mbtiles`、試しに一部のズームだけ焼くときは `--zooms 10-11`。
Release の地域を手元に戻すには `gh release download v1 -p '*.tar.gz'` で取って `site/` に展開し、`build_site.py --site site --index-only` で索引を作ります。

配信を確かめる:

```bash
python3 check_site.py https://kimkim0106.github.io/vrchat-osm-tiles/
```

## 形式の版

`vmt_bake.py` の先頭に VMT-DT 1 (タイル)、`vmt_pack.py` の先頭に VMT-DT 2 (シート) の形式が書いてあります。
タイルの形式の版は `vmt_bake.py` と `build_site.py` の `FORMAT_VERSION`、シートは `vmt_pack.py` と `build_site.py` の `SHEET_VERSION`、
それと描く側のシェーダーで合っている必要があります (`build_site.py` は作った manifest の版が違うと止まる)。
Pages はデプロイのたびにサイト全体を入れ替えますが、Actions は毎回すべての版の Release を戻すので、
v2 を出した後も v1 は焼き直さずにそのまま配り続けられます。

## ライセンス

- コード: MIT (`LICENSE`)
- 地図データ: © OpenStreetMap contributors、[ODbL](https://www.openstreetmap.org/copyright)。ワールド内にも帰属表示を出してください。
  元データは [Geofabrik](https://download.geofabrik.de/) の抽出を [Planetiler](https://github.com/onthegomap/planetiler) で OpenMapTiles スキーマにしたもの。
- タイルに含まれる文字の形: [Noto Sans CJK](https://github.com/notofonts/noto-cjk) (SIL Open Font License 1.1)
