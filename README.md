# vrchat-osm-tiles

OpenStreetMap のベクタータイルを、VRChat のワールドで描くための「データテクスチャ」(VMT-DT v1) に焼き、
GitHub Pages で配るリポジトリです。タイルは見た目は画像ですが、中身は頂点・色・地名・文字の形のデータです。
描く側 (UdonSharp とシェーダー) はワールドのプロジェクトにあります。

## 配信 URL

```
https://kimkim0106.github.io/vrchat-osm-tiles/v1/index.json                  地域の一覧
https://kimkim0106.github.io/vrchat-osm-tiles/v1/<地域>/manifest.json        タイルの並びと大きさ
https://kimkim0106.github.io/vrchat-osm-tiles/v1/<地域>/tiles/<z>/<x>/<y>.png
```

ワールドはこの URL をアップロード時に焼き込みます (Udon は実行時に VRCUrl を作れない)。
**リポジトリ名・地域の `name`・パスの形は変えないでください。** 変えると公開済みのワールドで地図が出なくなります。

`v1` は VMT-DT の形式の版です (manifest と index.json の `version`、テクスチャのヘッダ列 1 と同じ番号)。
ワールドの読み方が変わる変更は形式の版を上げて次のディレクトリに出し、古い版も配り続けます。

## 焼いた地域は固定する

ワールドは作るときに manifest の頂点数を焼き込みます。同じ URL の中身を焼き直すと、頂点が増えたタイルで三角形が欠けます
(同じ日に OSM を取り直しただけでも、東京 563 枚のうち 10 枚で増えた)。そのため、一度焼いた地域は勝手に焼き直しません。

- 焼いた地域は Release (タグ = 版。[v1](https://github.com/kimkim0106/vrchat-osm-tiles/releases/tag/v1)) に `<地域>.tar.gz` として保存します。
- Actions は毎回すべての版の Release を戻し、まだ無い地域だけを焼いて足します。スクリプトを直して push しても、焼いた地域は変わりません。
- `regions.json` から消しても、Release に残っている地域は配り続けます (古いワールドのため)。止めるには Release のファイルを消します。
- OSM のデータを新しくするときは、Actions を手動実行して `rebuild` に地域名 (または `all`) を入れます。
  その地域を使うワールドは、新しい manifest で床マップやボードを作り直して上げ直してください。

| 版 | 中身 |
|---|---|
| v1 | タイル 1 枚 = 画像 1 枚 |
| v2 (予定) | 複数のタイルを 2048 × 2048 の 1 枚にまとめる |

## VRChat の制約と、このリポジトリでの対応

| 制約 | 対応 |
|---|---|
| Image Loading はリダイレクトを許さない | ファイルを直接指す `https://` の URL だけを使う。独自ドメインは設定しない (`github.io` から 301 になる)。配信後に `check_site.py` が「リダイレクトなしで 200」を確かめる |
| 信頼済みドメインでないと既定では読めない | `*.github.io` は Image Loading・String Loading の両方で信頼済み (`raw.githubusercontent.com` は対象外) |
| 画像は 2048 × 2048 まで | タイルの幅は 2048 固定、高さは数百行 |
| 画像はシーン全体で 5 秒に 1 枚 | v1 は未対応。複数タイルを 1 枚にまとめる v2 で対応する予定 |

GitHub Pages はサイト 1 GB まで、帯域は月 100 GB が目安です。`build_site.py` は合計 900 MB を超えると止まります。

## 地域を足す

`regions.json` に足して main に push すると、Actions がその地域だけ焼いて Release に保存し、配ります。

```json
{"name": "osaka", "title": "大阪", "area": "kansai", "bbox": "135.40,34.60,135.60,34.75", "zooms": "10-14", "lang": "ja"}
```

- `area` は planetiler の `--area` (Geofabrik の地域名)。`bbox` は 西,南,東,北 の経緯度。
- `name` は URL の一部になるので英小文字で。

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

`vmt_bake.py` の先頭に VMT-DT の形式が書いてあります。形式の版は `vmt_bake.py` と `build_site.py` の
`FORMAT_VERSION`、描く側のシェーダーの 3 か所で合っている必要があります (`build_site.py` は焼いた manifest の版が違うと止まる)。
Pages はデプロイのたびにサイト全体を入れ替えますが、Actions は毎回すべての版の Release を戻すので、
v2 を出した後も v1 は焼き直さずにそのまま配り続けられます。

## ライセンス

- コード: MIT (`LICENSE`)
- 地図データ: © OpenStreetMap contributors、[ODbL](https://www.openstreetmap.org/copyright)。ワールド内にも帰属表示を出してください。
  元データは [Geofabrik](https://download.geofabrik.de/) の抽出を [Planetiler](https://github.com/onthegomap/planetiler) で OpenMapTiles スキーマにしたもの。
- タイルに含まれる文字の形: [Noto Sans CJK](https://github.com/notofonts/noto-cjk) (SIL Open Font License 1.1)
