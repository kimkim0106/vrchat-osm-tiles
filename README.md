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
https://kimkim0106.github.io/vrchat-osm-tiles/v3/<地域>/...                  v2 と同じ形。POI (駅・観光地・店など) 入り
```

`regions.json` で `"host": "r2"` の地域は Pages の容量 (1 GB) に入らないので、Cloudflare R2 に置きます。

```
<R2 の公開 URL>/v2/index.json
<R2 の公開 URL>/v2/<地域>/manifest.json                                       形は Pages の v2 と同じ
```

**R2 は VRChat の信頼済みドメインではありません。** R2 の地域は、VRChat の設定で「Allow Untrusted URLs」をオンにした人にしか読めません。
`.bin` と `.json` は gzip にして `Content-Encoding: gzip` を付けて置きます (UnityWebRequest が展開する)。

ワールドはこの URL をアップロード時に焼き込みます (Udon は実行時に VRCUrl を作れない)。
**リポジトリ名・地域の `name`・パスの形は変えないでください。** 変えると公開済みのワールドで地図が出なくなります。

`v1` / `v2` は VMT-DT の形式の版です (manifest と index.json の `version`、テクスチャのヘッダ列 1 と同じ番号)。
ワールドの読み方が変わる変更は形式の版を上げて次のディレクトリに出し、古い版も配り続けます。
`v3` はシートの形式は v2 と同じ (manifest の `version` は 2) で、POI を入れたものです (index.json の `version` は 3)。
v2 までのワールドのシェーダーは POI を地名と区別できないので、別のディレクトリにしています。

## 焼いた地域は固定する

ワールドは作るときに manifest の頂点数を焼き込みます。同じ URL の中身を焼き直すと、頂点が増えたタイルで三角形が欠けます
(同じ日に OSM を取り直しただけでも、東京 563 枚のうち 10 枚で増えた)。そのため、一度焼いた地域は勝手に焼き直しません。

- 焼いた地域は Release (タグ = 版。[v1](https://github.com/kimkim0106/vrchat-osm-tiles/releases/tag/v1)、[v2](https://github.com/kimkim0106/vrchat-osm-tiles/releases/tag/v2)、[v3](https://github.com/kimkim0106/vrchat-osm-tiles/releases/tag/v3)) に `<地域>.tar.gz` として保存します。
- v2 は v1 を `vmt_pack.py` でまとめて作ります。v1 を焼き直すと v2 も作り直します。
- v3 は v1 とは別に、POI 入りで焼いたタイル (配らない) をまとめて作ります。v1 / v2 は POI を入れずに焼きます (`vmt_bake.py --no-poi`)。
- Actions は Release の一覧を見て、まだ無い地域だけを焼いて足します (`plan_build.py`)。スクリプトを直して push しても、焼いた地域は変わりません。
- Actions の job は plan (焼く地域を決め、Release が無ければ作る) → bake (1 地域 = 1 job の matrix。Geofabrik に断られないよう同時に 3 つまで)
  → publish (Release から配る版を戻して索引を作り、R2 と Pages に出す) → deploy → check。
  bake の地域が 1 つ失敗しても、ほかの地域は焼いて Release に保存します。そのときは publish に進まないので、「Re-run failed jobs」で続けます。
- `regions.json` から消しても、Release に残っている地域は配り続けます (古いワールドのため)。止めるには Release のファイルを消します。
- OSM のデータを新しくするときは、Actions を手動実行して `rebuild` に地域名 (または `all`) を入れます。
  その地域を使うワールドは、新しい manifest で床マップやボードを作り直して上げ直してください。

| 版 | 中身 |
|---|---|
| v1 | タイル 1 枚 = 画像 1 枚 |
| v2 | 複数のタイルを 2048 × 2048 までの 1 枚 (シート) にまとめる。タイルの中身は v1 と同じ。形式は `vmt_pack.py` の冒頭 |
| v3 | v2 と同じ形のシート。地名に加えて POI (駅・観光・名所・神社・寺・公園・公共施設・宿泊・飲食・買い物) を載せる。ラベルの種類の番号はラベル頂点の T3.B (`vmt_bake.py` の `POI_CATEGORIES`)、一覧は manifest の `labelCategories`。文字アトラスのマスは 32 px |

## VRChat の制約と、このリポジトリでの対応

| 制約 | 対応 |
|---|---|
| Image Loading はリダイレクトを許さない | ファイルを直接指す `https://` の URL だけを使う。独自ドメインは設定しない (`github.io` から 301 になる)。配信後に `check_site.py` が「リダイレクトなしで 200」を確かめる |
| 信頼済みドメインでないと既定では読めない | `*.github.io` は Image Loading・String Loading の両方で信頼済み (`raw.githubusercontent.com` は対象外)。R2 (`r2.dev` も独自ドメインも) は対象外 |
| 画像は 2048 × 2048 まで | タイルの幅は 2048 固定、高さは数百行 |
| 画像はシーン全体で 5 秒に 1 枚 | v2 で複数タイルを 1 枚にまとめる (東京 563 タイル → 47 枚)。親タイルの子 4 枚を割らずに Z 順で詰めるので、足元の 36 タイルは中央値 5 枚に入る |
| 文字列もシーン全体で 5 秒に 1 回 (画像とは別) | `binZooms` のズームのシートは生データ (.bin、RGB24) も置き、String Loading で画像と並行して取れるようにする。生データは PNG の 3〜4 倍の大きさなので、床の全体に使うズームだけにする。さらに `binGroups` の地域は、シート数枚を縦につないだ「まとめた .bin」を R2 に置き、1 回に数枚取る (下の「まとめた .bin」) |

GitHub Pages はサイト 1 GB まで、帯域は月 100 GB が目安です。`build_site.py` は合計 900 MB を超えると止まります。

## まとめた .bin

床マップは画像 (PNG) と文字列 (.bin) の 2 つの窓口で取り、どちらも 5 秒に 1 回しか取れません。東京 v3 では足元の z14 のシートが
9 枚あり、画像の窓口だけで取ると全体がそろうまで 46 秒ほどかかりました (ClientSim で計測)。

`regions.json` の `"binGroups": {"dists": ["v3"], "zooms": [13, 14]}` の地域は、その版のシートを Z 順に 8192 行まで縦につないだ
.bin を `vmt_bingroup.py` で作り、R2 に置きます (`<R2 の公開 URL>/<版>/<地域>/bingroups/<まとめ方のハッシュ>/<z>/<k>.bin`)。

- Pages の地域でも R2 に置きます。Pages はサイトの容量を生の大きさで数えるので入りません (東京 v3 で生 702 MB、R2 では gzip で約 165 MB)。
- manifest に `binGroupBase` と `binGroups` を書き足します。タイルとシートの中身 (頂点数) は変わりません。
- 描く側はまとめた .bin を 1 枚のテクスチャ (高さ 8192 行まで) にして、シートの開始行 (`binGroupRow`) を足して読みます。
  先頭のシートのヘッダの高さだけを、まとめた高さに書き換えてあります (シェーダーの大きさの検査に合わせる)。
- R2 は信頼済みドメインではないので、「Allow Untrusted URLs」がオフの人は読めません。床は続けて 2 回失敗したら、まとめを使わずに
  シートごとの取得に戻ります (今までと同じ速さ)。
- 中身は Release のシートから publish のたびに作り直します (同じシートからは同じものができる)。まとめ方を変えるとパスのハッシュが変わり、
  古いワールドの読むファイルは R2 に残ります。
- ClientSim での計測 (東京 v3、手元のサーバー): 全体と足元がそろうまで 15.7 秒 (まとめなし 45.8 秒)。

## 地域を足す

`regions.json` に足して main に push すると、Actions がその地域だけ焼いて Release に保存し、配ります。

```json
{"name": "osaka", "title": "大阪", "area": "kansai", "bbox": "135.40,34.60,135.60,34.75", "zooms": "10-14", "lang": "ja", "binZooms": [13]}
```

- `area` は planetiler の `--area` (Geofabrik の地域名)。`bbox` は 西,南,東,北 の経緯度。
- `name` は URL の一部になるので英小文字で。
- `binZooms` は v2 で生データも置くズーム (床マップの全体に使うズーム)。東京の z13 で約 80 MB。
- `binGroups` はまとめた .bin を R2 に作る版とズーム (上の「まとめた .bin」)。R2 の Secrets / Variables が無ければ作らない。
- `dists` を書くと、その版だけを Pages に載せる (例 `["v2"]`)。v2 は v1 から作るので v1 も焼いて Release に保存するが、
  サイトには載せない。v1 を読むのは卓上ボード (VmtMap) だけなので、東京より後の地域は v2 だけにしてサイトの容量を空けている。
- v3 は `dists` か `releaseOnly` に `v3` がある地域 (`dists` が無ければすべて) だけ焼く。
- `releaseOnly` に書いた版は、焼いて Release に保存するが配らない (例 `"dists": ["v2"], "releaseOnly": ["v3"]`)。
  配る前に確かめたいときに使い、確かめたら `dists` に移す (Release から戻して配る。焼き直さない)。
- 今はどの地域も v3 だけを配る (`"dists": ["v3"]`)。v1 / v2 は Release に残っている。ワールドの床は v3 を読み、
  v1 を読む卓上ボード VmtMap は使っていない。R2 から外した v2 のファイルは R2 に残っている (r2_upload.py は消さない。
  消すなら Cloudflare の画面か aws CLI で `v2/` を消す)。

今の地域 (床マップに合わせて、どれも z13 で 10 × 11 枚 = 床 20 m × 22 m):

| name | 地域 | 中心 | 配る版 |
|---|---|---|---|
| tokyo | 東京 | (東京駅は中心から東へ 0.5 枚、南へ 0.3 枚) | v3 |
| kobe | 神戸 | JR 三ノ宮駅 (z13 のタイルの境目にそろえたので、駅は中心から東へ 0.45 枚、北へ 0.2 枚) | v3 |
| tokushima | 徳島 | 徳島駅 (駅は中心から西へ 0.2 枚、北へ 0.1 枚) | v3 |
| osaka | 大阪 | 大阪駅 (東へ 0.3 枚、北へ 0.4 枚) | v3 (R2) |
| nagoya | 名古屋 | 名古屋駅 (西へ 0.2 枚、北へ 0.4 枚)。三重県 (Geofabrik では kansai) にかかるので `area` は japan | v3 (R2) |
| fukuoka | 福岡 | 博多駅 (西へ 0.2 枚、南へ 0.2 枚) | v3 (R2) |
| sapporo | 札幌 | 札幌駅 (西へ 0.5 枚、北へ 0.5 枚) | v3 (R2) |
| sendai | 仙台 | 仙台駅 (西へ 0.1 枚、北へ 0.1 枚) | v3 (R2) |
| hiroshima | 広島 | 広島駅 (西へ 0.45 枚) | v3 (R2) |
| niigata | 新潟 | 新潟駅 (東へ 0.4 枚、北へ 0.1 枚) | v3 (R2) |

範囲は駅を含む z13 のタイルを中心に 10 × 11 枚とし、四辺を 0.25 枚ずつ内側に寄せた経緯度を `bbox` に書いています。

### R2 の設定

`host` が `r2` の地域を上げるには、このリポジトリの Settings > Secrets and variables > Actions に次を登録します。
無いときは焼いて Release に保存するところまで行い、R2 には上げません (警告が出ます)。

| 種類 | 名前 | 中身 |
|---|---|---|
| Secret | `R2_ACCOUNT_ID` | Cloudflare のアカウント ID |
| Secret | `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | R2 の API トークン (そのバケットの Object Read & Write) |
| Variable | `R2_BUCKET` | バケット名 |
| Variable | `R2_PUBLIC_URL` | バケットの公開 URL (`https://pub-….r2.dev/` か独自ドメイン)。配信後の確認に使う |

R2 に上げ直すのは、その実行で焼いた (まとめた) 版と地域と、R2 に `manifest.json` が無い地域だけです。

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

`vmt_bake.py` の先頭に VMT-DT 1 (タイル)、`vmt_pack.py` の先頭に VMT-DT 2 (シート) の形式が書いてあります
(v3 は VMT-DT 2 のシートで、ラベル頂点の T3.B に種類が入る)。
タイルの形式の版は `vmt_bake.py` と `build_site.py` の `FORMAT_VERSION`、シートは `vmt_pack.py` と `build_site.py` の `SHEET_VERSION`、
それと描く側のシェーダーで合っている必要があります (`build_site.py` は作った manifest の版が違うと止まる)。
Pages はデプロイのたびにサイト全体を入れ替えますが、Actions は毎回すべての版の Release を戻すので、
v2 を出した後も v1 は焼き直さずにそのまま配り続けられます。

## ライセンス

- コード: MIT (`LICENSE`)
- 地図データ: © OpenStreetMap contributors、[ODbL](https://www.openstreetmap.org/copyright)。ワールド内にも帰属表示を出してください。
  元データは [Geofabrik](https://download.geofabrik.de/) の抽出を [Planetiler](https://github.com/onthegomap/planetiler) で OpenMapTiles スキーマにしたもの。
- タイルに含まれる文字の形: [Noto Sans CJK](https://github.com/notofonts/noto-cjk) (SIL Open Font License 1.1)
