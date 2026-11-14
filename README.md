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
データの更新 (OSM の取り直し) では版を変えません。同じ URL のまま中身が新しくなり、既存のワールドにも届きます。

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

`regions.json` に足して main に push すると、Actions が焼いて配ります。

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

配信を確かめる:

```bash
python3 check_site.py https://kimkim0106.github.io/vrchat-osm-tiles/
```

## 形式の版

`vmt_bake.py` の先頭に VMT-DT の形式が書いてあります。形式の版は `vmt_bake.py` と `build_site.py` の
`FORMAT_VERSION`、描く側のシェーダーの 3 か所で合っている必要があります (`build_site.py` は焼いた manifest の版が違うと止まる)。
Pages はデプロイのたびにサイト全体を入れ替えるので、v2 を出した後も v1 を毎回いっしょに焼く必要があります
(v1 の焼き方を固定する方法は v2 を作るときに決める)。

## ライセンス

- コード: MIT (`LICENSE`)
- 地図データ: © OpenStreetMap contributors、[ODbL](https://www.openstreetmap.org/copyright)。ワールド内にも帰属表示を出してください。
  元データは [Geofabrik](https://download.geofabrik.de/) の抽出を [Planetiler](https://github.com/onthegomap/planetiler) で OpenMapTiles スキーマにしたもの。
- タイルに含まれる文字の形: [Noto Sans CJK](https://github.com/notofonts/noto-cjk) (SIL Open Font License 1.1)
