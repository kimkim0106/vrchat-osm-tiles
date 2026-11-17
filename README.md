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

## ツール

```
https://kimkim0106.github.io/vrchat-osm-tiles/tools/ink-geojson/             ペンの線の取り出し
```

ワールドの設定タブレットの「書き出す」で出した GeoJSON (地図用のペンで書いた線) を、VRChat のログ (`output_log_*.txt`) か
Unity の `Editor.log` から取り出すページです。ログはブラウザの中だけで読み、どこにも送りません。
中身はリポジトリの `tools/ink-geojson/index.html` で、
Actions が `tools/` をそのままサイトに写します。`tools/` を変えて main に push すると、地域は焼かずに配り直します。

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

今の地域は 47 都道府県の県庁所在地です (床マップに合わせて、どれも z13 で 10 × 11 枚 = 床 20 m × 22 m)。
`pref_regions.py` が県庁の位置 (OpenStreetMap) から範囲を決めて `regions.json` に書きます。

- 範囲は中心を含む z13 のタイルを中心に 10 × 11 枚 (横は中心のタイル座標を四捨五入して 5 枚引いた所から、縦は切り捨てて 5 枚引いた所から)。
  四辺を 0.25 枚ずつ内側に寄せた経緯度を `bbox` に書く。中心は最大で半タイルずれる (下の表の「中心」)。
- 中心は都道府県庁。ただし前からある 10 地域 (東京・神戸・徳島・大阪・名古屋・福岡・札幌・仙台・広島・新潟) は駅中心のまま
  (`pref_regions.py` の `STATION_CENTERED`。regions.json の `center` に駅名)。範囲を変えて焼き直すと、manifest を焼き込んだ
  公開中のワールドで床が崩れる (タイルの並びが変わる) ため。ワールドが実行時に manifest を読むようになったら、県庁中心に焼き直せる。
- `area` は都道府県の Geofabrik の地域。範囲に隣の地域の陸地が入るものは `japan` (1 つの地域だけ取ると、その陸地に道路などが無くなる)。
  確かめるには `python pref_regions.py --check-areas`。徳島は shikoku のままなので、淡路島に道路などが無い。

| name | 都道府県 | title | 中心 (範囲の中心から) | area | 置き場所 |
|---|---|---|---|---|---|
| sapporo | 北海道 | 札幌 | 札幌駅 (西へ 0.5 枚、北へ 0.5 枚) | hokkaido | R2 |
| aomori | 青森県 | 青森 | 県庁 (西へ 0.4 枚、南へ 0.2 枚) | tohoku | R2 |
| morioka | 岩手県 | 盛岡 | 県庁 (西へ 0.0 枚、北へ 0.4 枚) | tohoku | R2 |
| sendai | 宮城県 | 仙台 | 仙台駅 (西へ 0.1 枚、北へ 0.1 枚) | tohoku | R2 |
| akita | 秋田県 | 秋田 | 県庁 (東へ 0.1 枚、南へ 0.2 枚) | tohoku | R2 |
| yamagata | 山形県 | 山形 | 県庁 (東へ 0.0 枚、南へ 0.4 枚) | tohoku | R2 |
| fukushima | 福島県 | 福島 | 県庁 (東へ 0.4 枚、北へ 0.4 枚) | tohoku | R2 |
| mito | 茨城県 | 水戸 | 県庁 (西へ 0.1 枚、北へ 0.3 枚) | kanto | R2 |
| utsunomiya | 栃木県 | 宇都宮 | 県庁 (東へ 0.1 枚、南へ 0.4 枚) | kanto | R2 |
| maebashi | 群馬県 | 前橋 | 県庁 (東へ 0.4 枚、南へ 0.4 枚) | kanto | R2 |
| saitama | 埼玉県 | さいたま | 県庁 (西へ 0.2 枚、南へ 0.4 枚) | kanto | R2 |
| chiba | 千葉県 | 千葉 | 県庁 (西へ 0.4 枚、南へ 0.5 枚) | kanto | R2 |
| tokyo | 東京都 | 東京 | 東京駅 (東へ 0.5 枚、南へ 0.3 枚) | kanto | Pages |
| yokohama | 神奈川県 | 横浜 | 県庁 (西へ 0.4 枚、北へ 0.1 枚) | kanto | R2 |
| niigata | 新潟県 | 新潟 | 新潟駅 (東へ 0.4 枚、北へ 0.1 枚) | chubu | R2 |
| toyama | 富山県 | 富山 | 県庁 (東へ 0.3 枚、北へ 0.3 枚) | chubu | R2 |
| kanazawa | 石川県 | 金沢 | 県庁 (西へ 0.0 枚、北へ 0.4 枚) | chubu | R2 |
| fukui | 福井県 | 福井 | 県庁 (西へ 0.2 枚、北へ 0.5 枚) | chubu | R2 |
| kofu | 山梨県 | 甲府 | 県庁 (東へ 0.2 枚、北へ 0.2 枚) | chubu | R2 |
| nagano | 長野県 | 長野 | 県庁 (東へ 0.4 枚、北へ 0.0 枚) | chubu | R2 |
| gifu | 岐阜県 | 岐阜 | 県庁 (東へ 0.2 枚、南へ 0.4 枚) | japan | R2 |
| shizuoka | 静岡県 | 静岡 | 県庁 (西へ 0.0 枚、北へ 0.0 枚) | chubu | R2 |
| nagoya | 愛知県 | 名古屋 | 名古屋駅 (西へ 0.2 枚、北へ 0.4 枚) | japan | R2 |
| tsu | 三重県 | 津 | 県庁 (東へ 0.3 枚、北へ 0.2 枚) | kansai | R2 |
| otsu | 滋賀県 | 大津 | 県庁 (西へ 0.2 枚、南へ 0.2 枚) | kansai | R2 |
| kyoto | 京都府 | 京都 | 県庁 (東へ 0.2 枚、北へ 0.3 枚) | kansai | R2 |
| osaka | 大阪府 | 大阪 | 大阪駅 (東へ 0.3 枚、北へ 0.4 枚) | kansai | R2 |
| kobe | 兵庫県 | 神戸 | 三ノ宮駅 (東へ 0.45 枚、北へ 0.2 枚) | kansai | R2 (2026-11-17 に Pages から移した) |
| nara | 奈良県 | 奈良 | 県庁 (西へ 0.0 枚、南へ 0.1 枚) | kansai | R2 |
| wakayama | 和歌山県 | 和歌山 | 県庁 (西へ 0.2 枚、北へ 0.3 枚) | kansai | R2 |
| tottori | 鳥取県 | 鳥取 | 県庁 (西へ 0.3 枚、南へ 0.3 枚) | japan | R2 |
| matsue | 島根県 | 松江 | 県庁 (西へ 0.4 枚、南へ 0.2 枚) | chugoku | R2 |
| okayama | 岡山県 | 岡山 | 県庁 (西へ 0.2 枚、北へ 0.3 枚) | japan | R2 |
| hiroshima | 広島県 | 広島 | 広島駅 (西へ 0.45 枚) | chugoku | R2 |
| yamaguchi | 山口県 | 山口 | 県庁 (西へ 0.3 枚、北へ 0.2 枚) | chugoku | R2 |
| tokushima | 徳島県 | 徳島 | 徳島駅 (西へ 0.2 枚、北へ 0.1 枚) | shikoku | R2 (2026-11-17 に Pages から移した) |
| takamatsu | 香川県 | 高松 | 県庁 (東へ 0.2 枚、北へ 0.4 枚) | japan | R2 |
| matsuyama | 愛媛県 | 松山 | 県庁 (東へ 0.2 枚、南へ 0.3 枚) | shikoku | R2 |
| kochi | 高知県 | 高知 | 県庁 (西へ 0.4 枚、北へ 0.0 枚) | shikoku | R2 |
| fukuoka | 福岡県 | 福岡 | 博多駅 (西へ 0.2 枚、南へ 0.2 枚) | kyushu | R2 |
| saga | 佐賀県 | 佐賀 | 県庁 (東へ 0.0 枚、南へ 0.5 枚) | kyushu | R2 |
| nagasaki | 長崎県 | 長崎 | 県庁 (東へ 0.2 枚、南へ 0.0 枚) | kyushu | R2 |
| kumamoto | 熊本県 | 熊本 | 県庁 (東へ 0.1 枚、北へ 0.1 枚) | kyushu | R2 |
| oita | 大分県 | 大分 | 県庁 (西へ 0.1 枚、北へ 0.2 枚) | kyushu | R2 |
| miyazaki | 宮崎県 | 宮崎 | 県庁 (西へ 0.4 枚、北へ 0.4 枚) | kyushu | R2 |
| kagoshima | 鹿児島県 | 鹿児島 | 県庁 (西へ 0.1 枚、北へ 0.0 枚) | kyushu | R2 |
| naha | 沖縄県 | 那覇 | 県庁 (東へ 0.5 枚、南へ 0.1 枚) | kyushu | R2 |

### 広域の地図 (日本全体と地方ごと)

`"wide": true` の地域は、日本全体と地方ごとの床マップです (`wide_regions.py` が `regions.json` に書く)。設計は
vrchat-osm の Issue #18。Pages で配る (`"dists": ["v3"]`、2026-11-17 から。11 地域で 398 MB、東京と合わせて 766 MB)。
`wide_regions.py` で足した地域は最初 `"releaseOnly": ["v3"]` (Release に保存するだけ) で、確かめてから `dists` に移す。

```bash
python wide_regions.py <N03-20260101.geojson>        # 範囲とズームを決めて regions.json に書く
python wide_labels.py <N03-20260101.geojson> <N03-20260101_prefecture.geojson> <N02-25_Station.geojson>   # labels/wide_japan.json
```

- 元データは都市と同じ Geofabrik の日本 (`area` は `japan`)。Geofabrik の日本の範囲には北方領土と竹島が入っていない
  (陸の形は全世界の海岸線のデータから出る。道路などは無い)。周りの国も陸の形だけになる。
- 範囲は国土数値情報の行政区域 (N03) の市区町村の面から決める。地方は設定タブレットの 8 地方で、九州・沖縄だけ 2 つに分ける。
  関東に小笠原村は入れない。奄美群島とトカラ列島は沖縄の地図に入れる (九州に入れると九州が細長くなる)。
  東京都の島嶼部 (伊豆諸島と小笠原諸島) は別の 1 枚 (伊豆・小笠原)。南鳥島と沖ノ鳥島は日本全体にだけ出る
  (入れると床の 1 m が約 97 km になり、日本全体の 154 km とあまり変わらない)。
- ズームは床 (20 m × 22 m) の 1 枚が 1.4 m 以上になるいちばん大きいもの。日本全体は z7。焼くのは base と base + 1 (足元の詳細)。
- 地名は OpenStreetMap ではなく `labels/wide_japan.json` (国土数値情報から作った点) を使う (`vmt_bake.py --wide-labels`)。
  OpenMapTiles の低いズームには駅 (POI は z11 から) も町村もそろっていないため。都道府県・市区町村 (北方領土の 6 村も N03 にある)・
  政令市の区・駅 (N02。同じ駅名で 500 m 以内は事業者をまたいで 1 つ)・手で足す島 (北方 4 島・竹島・尖閣諸島・南鳥島・沖ノ鳥島と、
  東京都の島嶼部の島 (父島・母島・硫黄島など。町村より後に置く))。小笠原村の名前は村役場のある父島に置く。
  OpenStreetMap からは島・海・湖の名前だけ (日本語の名前があり、元の名前がハングルやキリル文字でないもの)。
  重なったときの優先順は 都道府県 > 県庁所在地・政令市 > 市・特別区・手で足す島 > 主な駅 (新幹線か 3 路線以上) > 町村 > 政令市の区 >
  そのほかの駅 (路線の多い順) > 島・海・湖 (`vmt_bake.py` の `WIDE_KINDS`)。
- 境界線は都道府県境の陸上の線だけ (`boundaryLevels`。国境・海の上の線・係争中の線は描かない)。
- 1 タイルのラベル頂点の上限は 49140 (`labelVerts`。都市の 3 倍)。Unity の地名の入れ物もこの大きさまで用意する。
- 地名の出典 (manifest の `labelAttribution`。CC BY 4.0): 「国土数値情報（行政区域データ）」「国土数値情報（鉄道データ）」（国土交通省）。

| name | title | ズーム | 枚数 | 1 枚 |
|---|---|---|---|---|
| wide-japan | 日本全体 | 7-8 | 12 × 12 | 1.67 m |
| wide-hokkaido | 北海道地方 | 9-10 | 14 × 9 | 1.43 m |
| wide-tohoku | 東北地方 | 9-10 | 6 × 10 | 2.20 m |
| wide-kanto | 関東地方 | 9-10 | 5 × 9 | 2.44 m |
| wide-chubu | 中部地方 | 9-10 | 7 × 8 | 2.75 m |
| wide-kinki | 近畿地方 | 10-11 | 9 × 9 | 2.22 m |
| wide-chugoku | 中国地方 | 10-11 | 12 × 14 | 1.57 m |
| wide-shikoku | 四国地方 | 10-11 | 9 × 7 | 2.22 m |
| wide-kyushu | 九州地方 | 9-10 | 6 × 8 | 2.75 m |
| wide-okinawa | 沖縄・奄美 | 9-10 | 13 × 10 | 1.54 m |
| wide-izu-ogasawara | 伊豆・小笠原 | 8-9 | 4 × 10 | 2.20 m |

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
- 広域の地図の地名 (`labels/wide_japan.json` とそれを焼いたタイル): 「[国土数値情報（行政区域データ）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html)」
  「[国土数値情報（鉄道データ）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html)」（国土交通省）を加工して作成 (CC BY 4.0)。
  ワールド内の帰属表示にも足してください (manifest の `labelAttribution`)。
- タイルに含まれる文字の形: [Noto Sans CJK](https://github.com/notofonts/noto-cjk) (SIL Open Font License 1.1)
