#!/usr/bin/env python3
"""vmt_bake.py - ベクタタイル (MVT) を VRChat 向けデータテクスチャに焼くオフラインベイカー。

入力 : .mbtiles (OpenMapTiles スキーマ、gzip 圧縮の MVT)
出力 : タイルごとに
         tiles/{z}/{x}/{y}.png  8bit RGB の PNG   (Image Loading 用)
         tiles/{z}/{x}/{y}.bin  生の RGBA32       (String Loading + LoadRawTextureData 用)
       と manifest.json、selftest.png / selftest.bin

依存 : numpy, Pillow, mapbox_earcut, scipy   (pip install numpy pillow mapbox-earcut scipy)

データテクスチャの形式 (VMT-DT v1)。幅は常に 2048、論理行 r = 0..H-1。
  行 0      ヘッダ
              列 0        マーカー TOP  = (0x56, 0x4D, 0x54)
              列 1        (形式バージョン, 0, 0)
              列 2        プローブ      = (128, 128, 128)
              列 3        頂点レコード数 (24bit: R 上位, G 中位, B 下位)
              列 4        (z, 0, 0)
              列 5 / 6    タイル x / y (24bit)
              列 7 / 8    テクスチャの高さ / 幅 (24bit)。リサイズ検出用
              列 16..271  較正ランプ i -> (i, 255 - i, (7 i + 3) & 255)
  行 1      パレット色      列 s = スタイル s の色 (sRGB バイト)
  行 2      パレット設定    列 s = (線幅 [1/8 px @512px タイル], 種別 0=面 1=線, 0)
            行 1 と行 2 の列 2047 は行タグ (0xA5, 行番号, 0x5A)。行ずれ検出用
  行 3..    頂点レコード。1 レコード = 2 テクセル、三角形は 3 レコード (インデックスなし)
              T0 = 12bit x, 12bit y を RGB 24bit に詰める
              T1 = (スタイル, nx * 63 + 128, ny * 63 + 128)   面は nx = ny = 0
  ラベル (任意)。頂点レコードの次の行から:
            ラベル頂点  1 頂点 = 4 テクセル (ヘッダ列 12。0 なら古い形式の 3)。文字 1 つが四角形 1 枚 (6 頂点)。
                        縁取り用、本体用の順に並ぶ
              T0 = 基準点 (地名の位置) の x, y。頂点レコードと同じ 12bit × 2
              T1 = 基準点からのずれ (タイル単位 + 2048) の x, y
              T2 = 文字アトラス内の位置。上位 12bit = 縁取りフラグ × 2048 + u、下位 12bit = v
              T3 = (kmin, kmax, 種類)  表示する文字の倍率 s の範囲。k = 8 log2(s) + 128 が kmin..kmax のとき表示する
                   (0 / 255 は下限 / 上限なし)。倍率を変えても重ならないよう、ベイク時に倍率ごとに配置を決めてある。
                   種類は 0 = 地名、1.. = POI (POI_CATEGORIES)。シェーダーは _LabelMask のビットで種類ごとに出し入れし、
                   種類ごとの色で描く。0 しか無い古いタイルもそのまま読める
            文字アトラス  32 × 32 の距離画像 (SDF) を横に 63 個ずつ並べる (古いタイルは 48 × 48 を 42 個)。R チャンネルのみ使う
            ヘッダの列 9 / 10 / 11 / 12 にラベル頂点数 / ラベル開始行 / アトラス開始行 (24bit) / ラベル頂点のテクセル数
  行 H-1    列 0 にマーカー BOTTOM = (0x62, 0x6F, 0x74)。他は未使用

形式 2 (--tile-format 2。VMT-DT v4 の配信で使う。Issue #19)。形式 1 からラベルだけが変わる (ヘッダ列 1 = 2):
  ラベルの字の形はタイルに持たず、ワールドが実行時に作る共有の字のアトラスから読む。字はどれも Unicode の文字コードで持ち、
  ワールドが「文字コード → アトラスのマス」の表で引く (どの字を描けるかはワールドのフォント (VRChat のクライアントの TMP の
  フォールバック) が決める。中国語・韓国語や JIS の外の字 (麵、𠮷 など) もそのまま載せる。2026-11-20 までは字の一覧
  (glyphs_v4.txt) に無い字だけタイルの小さなアトラスに入れていた)。manifest の sharedGlyphs = タイルが指す字 (ワールドはこれだけを作る)。
  字の幅とインクの真ん中はベイカーのフォント (Noto Sans CJK JP) で測る。フォントに無い字 (タイ文字など) は全角の幅で、
  送り幅の箱の真ん中に置く。1 字ずつ同じ幅のマスに描くので、字をつなげたり形を変えたりする文字 (アラビア文字・タイ文字・
  デーヴァナーガリーなど) は正しく描けない (対象外)。
  ラベルは言語ごとの塊 (--langs の順。最大 2) に分かれ、シェーダーが選ぶ。
  字レコード  1 字 = 4 テクセル。縁取りと本体の 2 枚の四角形 (12 頂点) を、シェーダーが頂点の番号から作る
              (塊の字数 N のとき、頂点 v < 6N は字 v / 6 の縁取り、残りは字 (v - 6N) / 6 の本体。縁取りを先に全部描く)
              T0 = 基準点 (地名の位置) の x, y。頂点レコードと同じ 12bit × 2
              T1 = 字の箱の中心の、基準点からの横のずれ (タイル単位 + 2048。北が上の向きで右が正) と、字の箱の一辺
                   (タイル単位、10bit)。頂点レコードと同じ 12bit × 2。縦のずれは無い (箱の中心は基準点の高さ)
              T2 = 字 24bit (R = 上位 8bit)。最上位のビット (0x800000) が 0 なら Unicode の文字コード (21bit)、
                   1 なら残りの 23bit がタイルのアトラスの番号 (2026-11-20 より前に焼いたタイル。今のベイカーは書かない)
                   0x400000 のビットが立っていれば道路番号の看板 (Issue #83。字ではない): ビット 20..21 = 形 (0 角の丸い長方形 /
                   1 おにぎり / 2 六角形 / 3 下が丸い形)、ビット 10..17 = 看板の中心の縦のずれ / 高さ x 128 + 128、ビット 0..9 = 幅 / 高さ x 128。
                   箱の一辺 (T1) は看板の高さ。看板は縁取りの段で描き (同じラベルの字の縁取りは描かない)、字は白で描く。
                   看板を知らないシェーダーは表に無い文字コードとして描かない
              T3 = (kmin, kmax, 種類)。形式 1 と同じ
              字の箱はアトラスの 1 マス (32 px) 全体。字の形は箱の横の真ん中にインクの箱の真ん中、縦は基準線を
              (32 - (ascent + descent)) / 2 + ascent の高さに置く (TextMeshPro の <mspace> と同じ。共有のアトラスもこの置き方)
  ヘッダ      列 9 / 10  塊 0 の字数 / 開始行 (24bit)
              列 11      タイルのアトラスの開始行 (無ければ 0。今のベイカーはいつも 0)。32 × 32 のマスを 1 段 63 個 (形式 1 と同じ)
              列 12      字レコードのテクセル数 (4)
              列 13 / 14 塊 1 の字数 / 開始行 (塊 1 が無ければ開始行 0。字数 0 でも開始行があれば「その言語のラベルは無い」)

座標は MVT のタイル単位 (extent 4096)。[-256, 4352) を 12bit に量子化する。
アルファは使わない (PNG は RGB、.bin は A = 255 固定)。
"""
import argparse
import gzip
import json
import math
import os
import sqlite3
import re
import struct
import sys
import unicodedata

import numpy as np

# ---------------------------------------------------------------- 形式の定数

TEX_W = 2048
MAX_H = 2048
FORMAT_VERSION = 1
MARK_TOP = (0x56, 0x4D, 0x54)
MARK_BOTTOM = (0x62, 0x6F, 0x74)
PROBE = (128, 128, 128)
RAMP_COL = 16
DATA_ROW = 3
EXTENT = 4096
QMIN = -256.0
QRANGE = 4608.0
NORMAL_SCALE = 63.0
MITER_LIMIT = 2.0
VERTS_PER_CHUNK = 196608  # プールメッシュ 1 枚で描ける頂点レコード数 (65536 三角形)

# ラベル
# アトラスはタイルごとに持つので、POI を入れるとタイルの高さの多くを占める (z14 で 1 枚 300 字ほど)。
# 48 px (30 px の字) から 32 px (20 px の字) に下げて、行数を半分以下にした
LABEL_CELL = 32          # アトラスの 1 文字のマス (px)
LABEL_FONT_PX = 20       # マスに描くときの文字サイズ (px)
LABEL_SPREAD = 4.0       # 距離画像が表す距離の幅 (px)。字の大きさとの比は 48 px のときと同じ
# 1 段に並べる字数。u (マスの右端を含む) は 2048 未満でなければならない (2048 以上は縁取りフラグと区別できない) ので、
# マスが 2048 をちょうど割り切るときは 1 つ減らす (32 px なら 63 字)
LABEL_COLS = (TEX_W - 1) // LABEL_CELL
LABEL_VERTS_PER_POOL = 16380  # ラベル用プールメッシュの頂点数の既定 (Unity の地名の入れ物の段階が 15 なので 90 の倍数。--label-verts で変える)
LABEL_STRIDE = 4         # ラベル頂点 1 つのテクセル数 (形式 2 では字レコード 1 つのテクセル数)
MAX_LANGS = 2            # 形式 2 の言語の塊の数の上限 (ヘッダ列 9..10 と 13..14)
LABEL_SCALE_MIN = -24    # 重なりを調べる倍率は 2^(k/8)、k = LABEL_SCALE_MIN..LABEL_SCALE_STEPS (1/8 倍から 8 倍)
LABEL_SCALE_STEPS = 24   # 1 倍未満は床を拡大したとき (文字の倍率を zoom で割る) に使い、拡大するほど多く出る
LABEL_CHOME_MERGE_M = 1500.0  # 丁目違いをまとめる距離 (m)
LABEL_DEDUP_M = 3000.0        # この距離以内の同名の地名は 1 つにする (m)
# 地名の種別 -> (512px タイルでの文字サイズ px, 優先度, 周囲に空ける余白 px)。優先度は小さいほど先に置く。
# 余白を大きくすると、その種別のラベルが間引かれる (丁目などの細かい地名が埋め尽くさないようにする)
LABEL_CLASSES = {
    "city": (18, 0, 4), "town": (15, 1, 4), "village": (13, 2, 4), "suburb": (13, 3, 6),
    "quarter": (12, 4, 10), "neighbourhood": (10, 5, 26), "hamlet": (10, 6, 26),
}
# POI (OpenMapTiles の poi レイヤー) の種類。番号はラベル頂点の T3.B で、シェーダーの _LabelMask のビット番号
# (0 は地名)。size / pri / margin は LABEL_CLASSES と同じ意味で、pri は地名と同じ尺度 (小さいほど先に置く)。
# dedup = この距離 (m) 以内の同名の POI は 1 つにする (同じ駅の別の路線、同じ施設の点と面など)。
# 色はシェーダー (VmtCore.cginc の VmtLabelCategoryColor) にある
POI_CATEGORIES = {
    # 駅は z14 (足元の詳細) では地名より先に置く (pri14。「東京都」に押されて東京駅が出ない、などを防ぐ)。
    # z13 以下は数が多い (東京で 700) ので地名の後にする
    1: {"key": "station", "title": "駅", "size": 12, "pri": 2.5, "pri14": -0.5, "margin": 6, "dedup": 600.0},
    2: {"key": "sight", "title": "観光・名所", "size": 11, "pri": 3.5, "margin": 6, "dedup": 300.0},
    3: {"key": "worship", "title": "神社・寺", "size": 10, "pri": 4.3, "margin": 6, "dedup": 150.0},
    4: {"key": "park", "title": "公園", "size": 10, "pri": 4.6, "margin": 6, "dedup": 150.0},
    5: {"key": "public", "title": "公共施設", "size": 9, "pri": 4.8, "margin": 4, "dedup": 150.0},
    6: {"key": "lodging", "title": "宿泊", "size": 9, "pri": 5.2, "margin": 4, "dedup": 100.0},
    7: {"key": "food", "title": "飲食", "size": 9, "pri": 5.6, "margin": 3, "dedup": 50.0},
    8: {"key": "shop", "title": "買い物", "size": 9, "pri": 5.8, "margin": 3, "dedup": 50.0},
    # 道路番号 (Issue #83。transportation_name レイヤーの線の真ん中に番号だけを置く)。拡大すると高速 → 国道 → 都道府県道の順に出るよう、
    # 数字は漢字より背が低いので、字の大きさは地名・POI より大きくする。
    # 優先度に加えて kcap (この k より大きい倍率 = 縮小した状態では出さない) を持つ。minz = このズームより下のタイルには載せない
    # 同じ番号の候補は線の切れ目ごとに残し (dedup は上下線の重なりを消す程度)、置くときに repeat (512px のタイルでの px。倍率に比例)
    # 以内に同じ番号があれば置かない。1 つ目がほかのラベルとぶつかっても、近くの別の候補が置けるように
    9: {"key": "road_expressway", "title": "高速道路", "size": 14, "pri": 1.5, "margin": 10, "dedup": 300.0, "repeat": 220},
    10: {"key": "road_national", "title": "国道", "size": 14, "pri": 2.8, "margin": 10, "dedup": 300.0, "repeat": 220},
    11: {"key": "road_prefectural", "title": "都道府県道", "size": 13, "pri": 4.9, "margin": 10, "dedup": 300.0,
         "repeat": 220, "minz": 14, "kcap": 4},
}
ROAD_CATEGORIES = (9, 10, 11)
# 道路番号の看板 (Issue #83)。番号の前に看板のレコードを 1 つ置き、シェーダーが種類の色の図形を描いて、その上に番号を白で描く。
# 寸法は字の大きさ (em = size px) を単位にし、番号のインクの中心が原点、+y が下。形ごとに:
#   0 = 角の丸い長方形 (NEXCO などの高速の E 番号)、1 = おにぎり (国道。角の丸い逆三角で辺がふくらむ)、
#   2 = 横長の六角形 (都道府県道)、3 = 上が平らで下が丸い形 (首都高・阪神高速などの都市高速)
#   pad = 番号の幅に足す幅、minw = 最小の幅、h = 高さ、yoff = 看板の中心の縦のずれ (おにぎりは番号を気持ち上に置く。シェーダーの VMT_ONIGIRI_YOFF と同じ割合)
ROAD_PLATES = {
    0: {"pad": 0.6, "minw": 1.3, "h": 1.2, "yoff": 0.0},
    1: {"pad": 1.0, "minw": 2.0, "h": 1.95, "yoff": 0.14},
    2: {"pad": 1.0, "minw": 1.9, "h": 1.3, "yoff": 0.0},
    3: {"pad": 0.7, "minw": 1.5, "h": 1.5, "yoff": 0.14},
}
ROAD_DEFAULT_PLATE = {9: 0, 10: 1, 11: 2}  # 種類ごとの看板の形 (高速は路線網で 0 と 3 を分ける。road_category)
PLATE_FLAG = 0x400000  # 字レコードの T2 のこのビットが立っていれば看板 (文字コードは 0x10FFFF まで、0x800000 は古いタイルのアトラスの番号)


def plate_dims(lab):
    """道路番号の看板の (幅, 高さ, 縦のずれ, 形) (512px のタイルでの px)。看板の無いラベルは None。"""
    if lab.get("cat") not in ROAD_DEFAULT_PLATE:
        return None
    shape = lab.get("plate", ROAD_DEFAULT_PLATE[lab["cat"]])
    pl = ROAD_PLATES[shape]
    em = float(lab["size"])
    tw = sum(lab["advs"]) / em
    return max(tw + pl["pad"], pl["minw"]) * em, pl["h"] * em, pl["yoff"] * em, shape
_ROAD_EXPRESSWAY_REF = re.compile(r"^[A-Z]{0,2}[0-9]{0,3}[A-Z]?$")
_ROAD_NUMBER_REF = re.compile(r"^[0-9]{1,3}$")
_URBAN_EXPRESSWAYS = ("首都高速", "阪神高速", "名古屋高速", "福岡高速", "北九州高速", "広島高速", "京都高速")
_ROAD_NAME_SUFFIX = ("道路", "自動車道", "線", "バイパス", "道")  # 路線の名前らしい終わり (出入口のランプの「錦糸町」「和光IC」などと分ける)
POI_MAX_CHARS = 16  # これより長い名前の POI は載せない
_POI_SKIP_SUFFIX = ("交番", "駐在所")  # 数が多く、観光の役に立たない
_POI_SIGHT = {"attraction", "museum", "zoo", "aquarium", "theme_park", "castle", "monument", "viewpoint"}
_POI_PUBLIC = {"town_hall", "police", "fire_station", "college"}
_POI_FOOD = {"restaurant", "cafe", "fast_food", "bar", "beer", "ice_cream", "bakery"}
_POI_SHOP = {"shop", "grocery", "clothing_store", "alcohol_shop", "pharmacy", "jewelry", "music", "mobile_phone",
             "department_store"}


def poi_category(props):
    """poi レイヤーの地物の種類 (POI_CATEGORIES の番号)。載せないものは None。"""
    cls, sub = props.get("class"), props.get("subclass")
    if cls == "railway":
        return 1 if sub in ("station", "subway", "halt", None) else None
    if cls in _POI_SIGHT or (cls == "art_gallery" and sub == "museum"):
        return 2
    if cls == "place_of_worship":
        return 3
    if cls in ("park", "garden"):
        return 4
    if cls in _POI_PUBLIC or (cls == "library" and sub == "library") or (cls == "post" and sub == "post_office") \
            or (cls == "hospital" and sub == "hospital"):
        return 5
    if cls == "lodging":
        return 6
    if cls in _POI_FOOD:
        return 7
    if cls in _POI_SHOP or (cls == "library" and sub == "books"):
        return 8
    return None


def road_category(props):
    """transportation_name レイヤーの線の道路番号の (種類, 番号, 看板の形)。載せないものは None。
    路線網 (route_1_network) があればそれで決め、無ければ class で決める (日本の OSM では trunk = 国道、primary = 主要地方道、
    secondary = 一般の都道府県道)。番号が複数 ("4;5") なら最初のもの (高速は E で始まる番号を優先する。淡路 SA のランプの
    "28;E28" が「28」の都市高速の看板になっていた)。"""
    ref = props.get("route_1_ref") or props.get("ref")
    if not isinstance(ref, str):
        return None
    refs = [r.strip() for r in ref.split(";") if r.strip()]
    if not refs:
        return None
    ref = refs[0]
    net = props.get("route_1_network") or ""
    cls = props.get("class")
    if net == "JP:national":
        cat = 10
    elif net.startswith("JP:prefectural"):
        cat = 11
    elif net.startswith("JP:E") or "高速" in net or cls == "motorway":
        cat = 9
    elif cls == "trunk":
        cat = 10
    elif cls in ("primary", "secondary"):
        cat = 11
    else:
        return None
    if cat == 9:
        ref = next((r for r in refs if r.startswith("E")), ref)
    if cls not in ("motorway", "trunk", "primary", "secondary"):
        return None  # 区道の番号 (tertiary の "L98" など) や、建設中の道路
    if not (_ROAD_EXPRESSWAY_REF if cat == 9 else _ROAD_NUMBER_REF).match(ref):
        return None
    shape = ROAD_DEFAULT_PLATE[cat]
    if cat == 9 and not ref.startswith("E"):
        # 都市高速 (首都高・阪神高速・名古屋高速など) は下が丸い看板、NEXCO などの E 番号は長方形
        name = props.get("name") or ""
        if net:
            if not net.startswith("JP:E") and "高速" in net:
                shape = 3
        elif any(name.startswith(n) for n in _URBAN_EXPRESSWAYS):
            shape = 3
        elif name and name.endswith(_ROAD_NAME_SUFFIX):
            # 路線網の無い、高速道路の扱いの道路。番号が数字だけなら国道のバイパスなど (浜手バイパス = 国道 2 号、中山道 = 17 号)
            # とみなしておにぎりにする (利用者の希望)。それ以外は長方形のまま
            if _ROAD_NUMBER_REF.match(ref):
                return 10, ref, ROAD_DEFAULT_PLATE[10]
        else:
            # 出入口のランプ (motorway_link。OpenMapTiles では class が motorway のまま) は出さない。「錦糸町」「和光IC」
            # 「霞が関出口」などの名前のものは番号が出口の番号 (首都高の 701 など) のことが多く、名前の無いもの (阪神高速 3 号・
            # 31 号、淡路 SA) は都市高速か NEXCO かを決められない (本線に同じ番号の看板がある)
            return None
    return cat, ref, shape


def _line_midpoint(parts):
    """線 (部分ごとの点の列) の長さで真ん中の点。いちばん長い部分の上に取る。"""
    best, best_len = None, 0.0
    for pts in parts:
        segs = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)]
        total = sum(segs)
        if total > best_len:
            best, best_len = (pts, segs, total), total
    if best is None:
        return None
    pts, segs, total = best
    half = total / 2.0
    for i, d in enumerate(segs):
        if half <= d and d > 0:
            t = half / d
            return pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
        half -= d
    return pts[-1]


def label_categories():
    """manifest に書くラベルの種類の一覧 (0 = 地名)。"""
    return [{"id": 0, "key": "place", "title": "地名"}] + \
        [{"id": k, "key": v["key"], "title": v["title"]} for k, v in sorted(POI_CATEGORIES.items())]


LABEL_CONFIG = {"font": None, "lang": None, "enabled": True, "spacing": 1.0, "poi": True,
                "verts": LABEL_VERTS_PER_POOL, "wide": None,
                # 形式 2: langs = 言語の塊の順
                "format": 1, "langs": None}

# 広域の地図 (--wide-labels) の地名。地名の点は wide_labels.py が国土数値情報から作ったもの (市区町村・駅・手で足す島) で、
# OpenStreetMap からは島・海・湖の名前だけを取る (日本語の名前があり、元の名前がハングルやキリル文字でないもの)。
# kind -> (種類 (0 地名 / 1 駅), 512px タイルでの文字サイズ px, 優先度 (小さいほど先), 余白 px)
WIDE_KINDS = {
    "pref": (0, 20, 0, 6),
    "designated": (0, 16, 1, 4),
    "capital": (0, 16, 1, 4),
    "city": (0, 14, 2, 4),
    "island": (0, 13, 2, 4),
    "station_major": (1, 12, 3, 4),
    "town": (0, 12, 4, 4),
    "ward": (0, 11, 5, 4),
    "station": (1, 11, 6, 3),
    "island_minor": (0, 12, 7, 4),
    "osm": (0, 12, 7, 4),
}
WIDE_OSM_PLACE = {"island"}                                  # place レイヤーの class
WIDE_OSM_WATER = {"ocean", "sea", "bay", "strait", "lake"}   # water_name レイヤーの class


def _foreign_script(name):
    """ハングルかキリル文字を含むか (広域の地図で、日本の外の地名を載せないため)。"""
    return any("\uac00" <= ch <= "\ud7af" or "\u1100" <= ch <= "\u11ff" or "\u3130" <= ch <= "\u318f"
               or "\u0400" <= ch <= "\u04ff" for ch in name)


FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def ramp_value(i):
    return (i, 255 - i, (7 * i + 3) & 255)


# ---------------------------------------------------------------- MVT デコード (最小限)

def _varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _fields(buf):
    """protobuf メッセージを (フィールド番号, ワイヤ型, 値) の列として読む。"""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, pos = _varint(buf, pos)
        elif wt == 1:
            val = buf[pos:pos + 8]
            pos += 8
        elif wt == 2:
            ln, pos = _varint(buf, pos)
            val = buf[pos:pos + ln]
            pos += ln
        elif wt == 5:
            val = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield num, wt, val


def _packed_uint32(buf):
    out = []
    pos = 0
    end = len(buf)
    while pos < end:
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


def _zigzag(n):
    return (n >> 1) ^ -(n & 1)


def _value(buf):
    for num, wt, val in _fields(buf):
        if num == 1:
            return bytes(val).decode("utf-8", "replace")
        if num == 2:
            return struct.unpack("<f", val)[0]
        if num == 3:
            return struct.unpack("<d", val)[0]
        if num in (4, 5):
            return val
        if num == 6:
            return _zigzag(val)
        if num == 7:
            return bool(val)
    return None


def _geometry(cmds, gtype):
    """ジオメトリコマンドを [[(x, y), ...], ...] に展開する (点列のリスト)。"""
    parts = []
    cur = []
    x = y = 0
    i = 0
    n = len(cmds)
    while i < n:
        cmd = cmds[i]
        i += 1
        cid, cnt = cmd & 7, cmd >> 3
        if cid == 1:  # MoveTo
            for _ in range(cnt):
                x += _zigzag(cmds[i])
                y += _zigzag(cmds[i + 1])
                i += 2
                if cur:
                    parts.append(cur)
                cur = [(x, y)]
        elif cid == 2:  # LineTo
            for _ in range(cnt):
                x += _zigzag(cmds[i])
                y += _zigzag(cmds[i + 1])
                i += 2
                cur.append((x, y))
        elif cid == 7:  # ClosePath
            pass
    if cur:
        parts.append(cur)
    return parts


def _layer_name(buf):
    for num, wt, val in _fields(buf):
        if num == 1:
            return bytes(val).decode("utf-8")
    return ""


def decode_mvt(data, only=None):
    """MVT を {レイヤー名: {"extent": int, "features": [{"type", "props", "geom"}]}} にする。
    only にレイヤー名の組を渡すと、それ以外のレイヤーは読まない。"""
    layers = {}
    for num, wt, val in _fields(memoryview(data)):
        if num != 3:
            continue
        if only is not None and _layer_name(val) not in only:
            continue
        name = ""
        extent = 4096
        keys = []
        values = []
        raw_features = []
        for n2, w2, v2 in _fields(val):
            if n2 == 1:
                name = bytes(v2).decode("utf-8")
            elif n2 == 2:
                raw_features.append(v2)
            elif n2 == 3:
                keys.append(bytes(v2).decode("utf-8"))
            elif n2 == 4:
                values.append(_value(v2))
            elif n2 == 5:
                extent = v2
        feats = []
        for rf in raw_features:
            tags = []
            gtype = 0
            geom = []
            for n3, w3, v3 in _fields(rf):
                if n3 == 2:
                    tags = _packed_uint32(v3)
                elif n3 == 3:
                    gtype = v3
                elif n3 == 4:
                    geom = _packed_uint32(v3)
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)}
            feats.append({"type": gtype, "props": props, "geom": _geometry(geom, gtype)})
        layers[name] = {"extent": extent, "features": feats}
    return layers


# ---------------------------------------------------------------- スタイル

# 既定スタイル (OpenMapTiles スキーマ向け)。上から順に描く。
# width は 512px 表示のタイルでのピクセル幅。
DEFAULT_STYLE = [
    {"name": "background", "type": "background", "color": "#f2efe9"},
    {"name": "landcover-farmland", "layer": "landcover", "class": ["farmland"], "type": "fill", "color": "#eef0d5"},
    {"name": "landcover-grass", "layer": "landcover", "class": ["grass"], "type": "fill", "color": "#d8e8c8"},
    {"name": "landcover-wood", "layer": "landcover", "class": ["wood"], "type": "fill", "color": "#bfdcae"},
    {"name": "landcover-wetland", "layer": "landcover", "class": ["wetland"], "type": "fill", "color": "#d3e6db"},
    {"name": "landcover-sand", "layer": "landcover", "class": ["sand"], "type": "fill", "color": "#f5e9c6"},
    {"name": "landcover-ice", "layer": "landcover", "class": ["ice", "rock"], "type": "fill", "color": "#eef3f5"},
    {"name": "landuse-residential", "layer": "landuse", "class": ["residential", "suburb", "neighbourhood"], "type": "fill", "color": "#ebe7e0", "maxzoom": 12},
    {"name": "landuse-industrial", "layer": "landuse", "class": ["industrial", "commercial", "retail", "railway"], "type": "fill", "color": "#e6e1e6"},
    {"name": "landuse-civic", "layer": "landuse", "class": ["school", "university", "college", "hospital", "kindergarten"], "type": "fill", "color": "#f0e8d0"},
    {"name": "landuse-green", "layer": "landuse", "class": ["cemetery", "pitch", "playground", "stadium", "track"], "type": "fill", "color": "#cfe3bf"},
    {"name": "park", "layer": "park", "type": "fill", "color": "#c8e6b4"},
    {"name": "water", "layer": "water", "type": "fill", "color": "#a6cbe3"},
    {"name": "waterway", "layer": "waterway", "type": "line", "color": "#a6cbe3", "width": 1.5},
    {"name": "aeroway-area", "layer": "aeroway", "type": "fill", "color": "#dadae0"},
    {"name": "aeroway-line", "layer": "aeroway", "type": "line", "color": "#c8c8d2", "width": 3.0},
    {"name": "road-area", "layer": "transportation", "type": "fill", "color": "#f4f2ee", "minzoom": 13},
    {"name": "building", "layer": "building", "type": "fill", "color": "#d9d0c9", "minzoom": 13},
    {"name": "road-path", "layer": "transportation", "class": ["path", "track"], "type": "line", "color": "#cdbba3", "width": 1.0, "minzoom": 14},
    {"name": "road-service", "layer": "transportation", "class": ["service"], "type": "line", "color": "#ffffff", "width": 1.5, "minzoom": 14},
    {"name": "road-minor", "layer": "transportation", "class": ["minor"], "type": "line", "color": "#ffffff", "width": 3.0, "minzoom": 12},
    {"name": "rail", "layer": "transportation", "class": ["rail", "transit"], "type": "line", "color": "#9a9a9a", "width": 1.5},
    {"name": "road-tertiary", "layer": "transportation", "class": ["tertiary"], "type": "line", "color": "#ffffff", "width": 4.0},
    {"name": "road-secondary", "layer": "transportation", "class": ["secondary"], "type": "line", "color": "#fdf2b3", "width": 5.0},
    {"name": "road-primary", "layer": "transportation", "class": ["primary"], "type": "line", "color": "#fde293", "width": 6.0},
    {"name": "road-trunk", "layer": "transportation", "class": ["trunk"], "type": "line", "color": "#fbc77a", "width": 7.0},
    {"name": "road-motorway", "layer": "transportation", "class": ["motorway"], "type": "line", "color": "#f9a55a", "width": 8.0},
    {"name": "boundary", "layer": "boundary", "type": "line", "color": "#9e7bb5", "width": 1.5, "max_admin_level": 4},
]


def _hex(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def load_style(path):
    if not path:
        return DEFAULT_STYLE
    with open(path, "r", encoding="utf-8") as f:
        style = json.load(f)
    if not style or style[0].get("type") != "background":
        raise ValueError("style の先頭は type=background にしてください")
    if len(style) > 256:
        raise ValueError("スタイルは 256 個まで")
    return style


def _rule_matches(rule, props, gtype, zoom):
    if zoom < rule.get("minzoom", 0) or zoom > rule.get("maxzoom", 99):
        return False
    want = 3 if rule["type"] == "fill" else 2
    if gtype != want:
        return False
    if "class" in rule and props.get("class") not in rule["class"]:
        return False
    if "max_admin_level" in rule:
        lvl = props.get("admin_level")
        if lvl is None or lvl > rule["max_admin_level"]:
            return False
    if "admin_levels" in rule:  # --boundary-levels: この段の陸上の線だけ (海の上の線と係争中の線は描かない)
        if props.get("admin_level") not in rule["admin_levels"] or props.get("maritime") or props.get("disputed"):
            return False
    return True


# ---------------------------------------------------------------- ジオメトリ

def _clip_ring(ring, lo, hi):
    """Sutherland-Hodgman で軸平行の箱 [lo, hi]^2 にリングを切る。"""
    def clip(pts, axis, bound, keep_less):
        out = []
        n = len(pts)
        for i in range(n):
            a = pts[i]
            b = pts[(i + 1) % n]
            ina = a[axis] <= bound if keep_less else a[axis] >= bound
            inb = b[axis] <= bound if keep_less else b[axis] >= bound
            if ina:
                out.append(a)
            if ina != inb:
                t = (bound - a[axis]) / (b[axis] - a[axis])
                p = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
                p[axis] = bound
                out.append((p[0], p[1]))
        return out
    pts = ring
    for axis in (0, 1):
        pts = clip(pts, axis, lo, False)
        if len(pts) < 3:
            return []
        pts = clip(pts, axis, hi, True)
        if len(pts) < 3:
            return []
    return pts


def _clip_line(line, lo, hi):
    """折れ線を箱 [lo, hi]^2 に切り、内側の区間のリストを返す (Liang-Barsky)。"""
    out = []
    cur = []
    for i in range(len(line) - 1):
        x0, y0 = line[i]
        x1, y1 = line[i + 1]
        dx, dy = x1 - x0, y1 - y0
        t0, t1 = 0.0, 1.0
        ok = True
        for p, q in ((-dx, x0 - lo), (dx, hi - x0), (-dy, y0 - lo), (dy, hi - y0)):
            if p == 0:
                if q < 0:
                    ok = False
                    break
            else:
                t = q / p
                if p < 0:
                    if t > t1:
                        ok = False
                        break
                    t0 = max(t0, t)
                else:
                    if t < t0:
                        ok = False
                        break
                    t1 = min(t1, t)
        if not ok:
            if len(cur) >= 2:
                out.append(cur)
            cur = []
            continue
        a = (x0 + dx * t0, y0 + dy * t0)
        b = (x0 + dx * t1, y0 + dy * t1)
        if not cur:
            cur = [a]
        cur.append(b)
        if t1 < 1.0:
            out.append(cur)
            cur = []
    if len(cur) >= 2:
        out.append(cur)
    return out


def _ring_area(ring):
    a = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a * 0.5


def polygon_triangles(rings, scale, lo, hi):
    """MVT のポリゴン (リング列) を三角形の頂点配列 (n*3, 2) にする。"""
    import mapbox_earcut as earcut

    polys = []
    cur = None
    for ring in rings:
        pts = [(p[0] * scale, p[1] * scale) for p in ring]
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        area = _ring_area(pts)
        if area == 0:
            continue
        if any(not (lo <= p[0] <= hi and lo <= p[1] <= hi) for p in pts):
            pts = _clip_ring(pts, lo, hi)
            if len(pts) < 3:
                continue
        if area > 0 or cur is None:  # 正の面積 = 外周 (MVT 仕様)
            cur = [pts]
            polys.append(cur)
        else:
            cur.append(pts)
    out = []
    for poly in polys:
        verts = np.array([p for ring in poly for p in ring], dtype=np.float64)
        ends = np.cumsum([len(r) for r in poly]).astype(np.uint32)
        idx = earcut.triangulate_float64(verts, ends)
        if len(idx):
            out.append(verts[idx])
    if not out:
        return np.zeros((0, 2))
    return np.concatenate(out)


def line_records(lines, scale, lo, hi):
    """折れ線を四角形に展開する。戻り値は (n*3, 4) = x, y, nx, ny。

    法線は miter ベクトル (長さは MITER_LIMIT まで)。線幅はシェーダーが掛ける。
    """
    out = []
    for raw in lines:
        pts0 = [(p[0] * scale, p[1] * scale) for p in raw]
        if any(not (lo <= p[0] <= hi and lo <= p[1] <= hi) for p in pts0):
            segs = _clip_line(pts0, lo, hi)
        else:
            segs = [pts0]
        for seg in segs:
            pts = [seg[0]]
            for p in seg[1:]:
                if p != pts[-1]:
                    pts.append(p)
            if len(pts) < 2:
                continue
            p = np.array(pts, dtype=np.float64)
            d = p[1:] - p[:-1]
            ln = np.hypot(d[:, 0], d[:, 1])
            d = d / ln[:, None]
            sn = np.stack([-d[:, 1], d[:, 0]], axis=1)  # 区間の左法線
            n = len(p)
            vn = np.zeros((n, 2))
            vn[0] = sn[0]
            vn[-1] = sn[-1]
            if n > 2:
                m = sn[:-1] + sn[1:]
                ml = np.hypot(m[:, 0], m[:, 1])
                good = ml > 1e-6
                m[good] = m[good] / ml[good, None]
                m[~good] = sn[1:][~good]
                cosv = np.clip((m * sn[1:]).sum(axis=1), 1.0 / MITER_LIMIT, 1.0)
                vn[1:-1] = m / cosv[:, None]
            a, b = p[:-1], p[1:]
            na, nb = vn[:-1], vn[1:]
            quad = np.stack([
                np.concatenate([a, na], axis=1), np.concatenate([a, -na], axis=1), np.concatenate([b, nb], axis=1),
                np.concatenate([a, -na], axis=1), np.concatenate([b, -nb], axis=1), np.concatenate([b, nb], axis=1),
            ], axis=1)
            out.append(quad.reshape(-1, 4))
    if not out:
        return np.zeros((0, 4))
    return np.concatenate(out)


# ---------------------------------------------------------------- ラベル

_font_cache = {}


def _font_path():
    """ラベルのフォントのパス。--font が無ければ FONT_CANDIDATES から探して LABEL_CONFIG に入れる。"""
    path = LABEL_CONFIG["font"]
    if path is None:
        path = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
        if path is None:
            raise RuntimeError("フォントが見つかりません。--font で指定してください")
        LABEL_CONFIG["font"] = path
    return path


def _font(size=None, basic=False):
    """ラベルのフォント。basic なら Pillow の BASIC レイアウト (raqm を使わない。1 文字ずつ描くので結果はほぼ同じ)。"""
    path = _font_path()
    size = size or LABEL_FONT_PX
    key = (path, size, basic)
    if key not in _font_cache:
        from PIL import ImageFont
        engine = ImageFont.Layout.BASIC if basic else None
        _font_cache[key] = ImageFont.truetype(path, size, layout_engine=engine)
    return _font_cache[key]


# 地名に入っていても字にならないもの (1 文字ずつ描くので、出すと崩れた字やトーフになる。raqm が 1 文字だけでは
# 組めずに落ちることもある): 制御文字・書式文字 (ゼロ幅結合子など)・サロゲート・私用・未割り当て・行区切り、
# 結合文字 (NFC で合成できなかったもの)、異体字セレクタ。
_LABEL_DROP_CATS = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Mn", "Me"}
_HALFWIDTH_MARKS = {"゙": "゛", "゚": "゜"}


def clean_label(name):
    """ラベルの名前を NFC にし、字にならない文字を除く。空白の並びは 1 つにする。"""
    name = unicodedata.normalize("NFC", name)
    if LABEL_CONFIG.get("format", 1) >= 2:
        # 半角カナは全角にする (1 字ずつマスに描くので、半角の濁点・半濁点を前の字に重ねられない。ｶﾞ は ガ になる)。
        # 前の字と合成できなかった濁点・半濁点は結合文字のままだと消えるので ゛゜ にする
        name = re.sub("[｡-ﾟ]+", lambda m: unicodedata.normalize("NFKC", m.group()), name)
        name = unicodedata.normalize("NFC", name)
        name = "".join(_HALFWIDTH_MARKS.get(ch, ch) for ch in name)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in _LABEL_DROP_CATS)
    return " ".join(name.split())


_glyph_cache = {}
_cmap_cache = {}


def font_has(ch):
    """ベイカーのフォント (TTC なら 1 つ目の書体。Pillow と同じ) に ch の字があるか。"""
    path = _font_path()  # --font が無いときも、_font と同じフォントを見る (Codex のレビューで指摘)
    if path not in _cmap_cache:
        from fontTools.ttLib import TTCollection, TTFont
        with open(path, "rb") as f:
            ttc = f.read(4) == b"ttcf"
        font = TTCollection(path, lazy=True).fonts[0] if ttc else TTFont(path, lazy=True)
        _cmap_cache[path] = set(font.getBestCmap())
    return ord(ch) in _cmap_cache[path]


def glyph_advance(ch):
    """字の送り幅 (LABEL_FONT_PX の字で px)。形式 2 では、フォントに無い字は全角 (.notdef の幅によらない)。"""
    if LABEL_CONFIG.get("format", 1) >= 2 and not font_has(ch):
        return float(LABEL_FONT_PX)
    return glyph_sdf(ch)[1]


def glyph_sdf(ch, ink=False):
    """1 文字の距離画像 (LABEL_CELL 四方、uint8、128 が輪郭、内側ほど大きい) と送り幅 (px)。
    横は、ink なら字の形 (インクの箱) の真ん中を、そうでなければ送り幅の箱の真ん中をマスの真ん中に置く (形式 2 は ink)。"""
    key = (LABEL_CONFIG["font"], ch, ink)
    if key in _glyph_cache:
        return _glyph_cache[key]
    from PIL import Image, ImageDraw
    from scipy import ndimage
    ss = 4  # 4 倍で描いて距離を測り、縮小する
    size = LABEL_CELL * ss
    img = adv = None
    for basic in (False, True):
        font, big = _font(basic=basic), _font(LABEL_FONT_PX * ss, basic=basic)
        try:
            adv = font.getlength(ch)
            ascent, descent = big.getmetrics()
            img = Image.new("L", (size, size), 0)
            if ink:
                x0 = size / 2.0 - glyph_ink_center(ch) * ss
            else:
                x0 = (size - big.getlength(ch)) / 2.0
            y0 = (size - (ascent + descent)) / 2.0
            ImageDraw.Draw(img).text((x0, y0), ch, font=big, fill=255)
            break
        except (RuntimeError, OSError, ValueError, UnicodeError) as e:
            # raqm が 1 文字だけでは組めないことがある (2026-11-16 に横浜の z14 で raqm_layout() failed)
            print(f"warning: U+{ord(ch):04X} ({unicodedata.name(ch, '?')}) を"
                  f"{'BASIC' if basic else ' raqm '}で描けない: {e}", file=sys.stderr, flush=True)
            img = adv = None
    if img is None:  # どちらでも描けなければ空白にする
        img, adv = Image.new("L", (size, size), 0), LABEL_FONT_PX * 0.5
    inside = np.asarray(img) >= 128
    d_in = ndimage.distance_transform_edt(inside)
    d_out = ndimage.distance_transform_edt(~inside)
    sd = (d_in - d_out) / ss
    sd = sd.reshape(LABEL_CELL, ss, LABEL_CELL, ss).mean(axis=(1, 3))
    out = np.clip(np.rint(128.0 + sd * (127.0 / LABEL_SPREAD)), 0, 255).astype(np.uint8)
    _glyph_cache[key] = (out, adv)
    return _glyph_cache[key]


_ink_cache = {}


def glyph_ink_center(ch):
    """字の形 (インクの箱) の横の真ん中の、ペンの位置からの距離 (LABEL_FONT_PX の字で px)。形の無い字は送り幅の半分。"""
    key = (LABEL_CONFIG["font"], ch)
    if key not in _ink_cache and LABEL_CONFIG.get("format", 1) >= 2 and not font_has(ch):
        _ink_cache[key] = glyph_advance(ch) / 2.0  # フォントに無い字は送り幅の箱の真ん中 (ワールドのフォントの字をそこに描く)
    if key not in _ink_cache:
        from PIL import Image, ImageDraw
        c = None
        ss = 4  # 4 倍で描いて測る (getbbox は送り幅の箱を返すので、描いた画素の範囲を見る)
        size = LABEL_FONT_PX * ss
        for basic in (False, True):
            try:
                img = Image.new("L", (size * 3, size * 3), 0)
                ImageDraw.Draw(img).text((size, size), ch, font=_font(size, basic=basic), fill=255)
                box = img.getbbox()
                c = ((box[0] + box[2]) / 2.0 - size) / ss if box else glyph_sdf(ch)[1] / 2.0
                break
            except (RuntimeError, OSError, ValueError, UnicodeError):
                continue
        _ink_cache[key] = c if c is not None else glyph_sdf(ch)[1] / 2.0
    return _ink_cache[key]


_CHOME = re.compile(r"^(.+?)[一二三四五六七八九十〇0-9０-９]+丁目$")


def _unit_m(z, lat):
    """ズーム z でのタイル単位 1 つの長さ (m)。"""
    return 40075016.686 * math.cos(math.radians(lat)) / (1 << z) / EXTENT


def label_candidates(db, z, tiles):
    """ズーム z の全タイルから地名と POI の候補を集める。座標はズーム z 全体でのタイル単位 (タイル x * 4096 + タイル内 x)。"""
    lang = LABEL_CONFIG["lang"]
    layers = ("place", "poi") if LABEL_CONFIG.get("poi", True) else ("place",)
    roads = LABEL_CONFIG.get("poi", True)
    cands = []
    for tx, ty in tiles:
        data = read_tile(db, z, tx, ty)
        if not data:
            continue
        decoded = decode_mvt(data, only=layers + (("transportation_name",) if roads else ()))
        if roads:
            road_candidates(decoded.get("transportation_name"), z, tx, ty, cands)
        for lname in layers:
            layer = decoded.get(lname)
            if not layer:
                continue
            scale = EXTENT / float(layer["extent"])
            for feat in layer["features"]:
                if feat["type"] != 1 or not feat["geom"]:
                    continue
                props = feat["props"]
                if lang == "en":
                    # 英語の名前が無ければ OSM のローマ字表記 (name:ja-Latn)、それも無ければ元の名前 (日本語)。
                    # OpenMapTiles の name:latin は使わない (漢字を中国語の読みでローマ字にする。「神保町」が "shén bǎo tǐng")
                    name = props.get("name:en") or props.get("name:ja-Latn") or props.get("name")
                else:
                    name = (props.get("name:" + lang) if lang else None) or props.get("name")
                name = clean_label(name) if isinstance(name, str) else None
                if not name:
                    continue
                if lname == "place":
                    cls = LABEL_CLASSES.get(props.get("class"))
                    if cls is None:
                        continue
                    cat, size, pri, margin = 0, cls[0], cls[1], cls[2]
                else:
                    cat = poi_category(props)
                    if cat is None:
                        continue
                    pc = POI_CATEGORIES[cat]
                    if cat == 1 and lang == "ja" and not name.endswith("駅"):
                        name += "駅"
                    if cat == 1 and lang == "en" and not name.endswith(("Station", "Sta.")):
                        name += " Sta."
                    # 英字は幅が全角の半分ほどなので、字数の上限を倍にする (載せる POI の数を日本語とそろえる)
                    limit = POI_MAX_CHARS * 2 if lang == "en" else POI_MAX_CHARS
                    if len(name) > limit or name.endswith(_POI_SKIP_SUFFIX) \
                            or str(props.get("name", "")).endswith(_POI_SKIP_SUFFIX):
                        continue
                    size, pri, margin = pc["size"], pc.get("pri14", pc["pri"]) if z >= 14 else pc["pri"], pc["margin"]
                x, y = feat["geom"][0][0][0] * scale, feat["geom"][0][0][1] * scale
                if not (0 <= x < EXTENT and 0 <= y < EXTENT):  # 余白にある点は隣のタイルが持っている
                    continue
                rank = props.get("rank", 99)
                cands.append({"pri": pri, "rank": rank if isinstance(rank, (int, float)) else 99, "name": name,
                              "cat": cat, "x": tx * EXTENT + x, "y": ty * EXTENT + y,
                              "size": size, "margin": margin * LABEL_CONFIG["spacing"]})
    return cands


def road_candidates(layer, z, tx, ty, cands):
    """道路番号の候補 (POI_CATEGORIES の 9..11) を cands に足す。同じ番号の近いものは merge_labels が 1 つにする。"""
    if not layer:
        return
    scale = EXTENT / float(layer["extent"])
    for feat in layer["features"]:
        if feat["type"] != 2 or not feat["geom"]:
            continue
        rc = road_category(feat["props"])
        if rc is None:
            continue
        cat, ref, shape = rc
        pc = POI_CATEGORIES[cat]
        if z < pc.get("minz", 0):
            continue
        mid = _line_midpoint(feat["geom"])
        if mid is None:
            continue
        x, y = mid[0] * scale, mid[1] * scale
        if not (0 <= x < EXTENT and 0 <= y < EXTENT):  # 余白にある線は隣のタイルが持っている
            continue
        c = {"pri": pc["pri"], "rank": 99, "name": ref, "cat": cat, "x": tx * EXTENT + x, "y": ty * EXTENT + y,
             "size": pc["size"], "margin": pc["margin"] * LABEL_CONFIG["spacing"]}
        if "kcap" in pc:
            c["kcap"] = pc["kcap"]
        c["repeat"] = pc["repeat"]
        if shape != ROAD_DEFAULT_PLATE[cat]:
            c["plate"] = shape
        cands.append(c)


def _unit_y(z, y):
    """ズーム z 全体でのタイル単位の y の緯度での、タイル単位 1 つの長さ (m)。"""
    n = (1 << z) * EXTENT
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return _unit_m(z, lat)


def _tile_units(lon, lat, z):
    """経緯度をズーム z 全体でのタイル単位にする。"""
    n = (1 << z) * EXTENT
    r = math.radians(lat)
    return (lon + 180.0) / 360.0 * n, (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n


def wide_candidates(db, z, tiles):
    """広域の地図の地名の候補 (LABEL_CONFIG["wide"] の点と、OpenStreetMap の島・海・湖)。優先度順に並べて返す。"""
    inside = set(tiles)
    cands = []
    names = set()
    for e in LABEL_CONFIG["wide"]:
        x, y = _tile_units(e["lon"], e["lat"], z)
        if (int(x // EXTENT), int(y // EXTENT)) not in inside:
            continue
        cat, size, pri, margin = WIDE_KINDS[e["kind"]]
        name = clean_label(e["name"])
        names.add(name)
        cands.append({"pri": pri, "rank": e.get("order", 0), "name": name, "cat": cat, "x": x, "y": y,
                      "size": size, "margin": margin * LABEL_CONFIG["spacing"]})
    lang = LABEL_CONFIG["lang"] or "ja"
    cat, size, pri, margin = WIDE_KINDS["osm"]
    seen = set()
    for tx, ty in tiles:
        data = read_tile(db, z, tx, ty)
        if not data:
            continue
        decoded = decode_mvt(data, only=("place", "water_name"))
        for lname, classes in (("place", WIDE_OSM_PLACE), ("water_name", WIDE_OSM_WATER)):
            layer = decoded.get(lname)
            if not layer:
                continue
            scale = EXTENT / float(layer["extent"])
            for feat in layer["features"]:
                props = feat["props"]
                if feat["type"] not in (1, 2) or not feat["geom"] or props.get("class") not in classes:
                    continue
                local = props.get("name")
                name = props.get("name:" + lang)
                if not isinstance(name, str) or (isinstance(local, str) and _foreign_script(local)):
                    continue
                name = clean_label(name)
                if not name or name in names or name in seen:
                    continue
                # 点ならその点、線 (湖の中心線) なら最初の線の真ん中の点
                line = feat["geom"][0]
                x, y = line[len(line) // 2][0] * scale, line[len(line) // 2][1] * scale
                if not (0 <= x < EXTENT and 0 <= y < EXTENT):
                    continue
                seen.add(name)
                cands.append({"pri": pri, "rank": props.get("rank", 99) if isinstance(props.get("rank"), (int, float)) else 99,
                              "name": name, "cat": cat, "x": tx * EXTENT + x, "y": ty * EXTENT + y,
                              "size": size, "margin": margin * LABEL_CONFIG["spacing"], "osm": True})
    cands.sort(key=lambda c: (c["pri"], c["rank"], c["name"], c["x"], c["y"]))
    return cands


def _cluster(points, limit):
    """距離 limit 以内でつながる点をまとめる (単連結)。戻り値は添字のリストのリスト。"""
    n = len(points)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1]) <= limit:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def merge_labels(cands, unit_m, z=None, fixed=()):
    """「永田町一丁目」「永田町二丁目」のような丁目違いを近いものどうし 1 つの町名にまとめ (位置は平均)、
    近くにある同名の地名は優先度の高い 1 つだけ残す。タイルをまたいでも行う。
    unit_m が None なら、距離は候補ごとにその緯度で測る (ズーム z。地域をまたいで同じ結果にするため。VMT-DT v4)。
    fixed は先に置いてある (動かせない) ラベル。それと同名で近いものは捨てる。"""
    if unit_m is None:
        def unit(c):
            return _unit_y(z, c["y"])
    else:
        def unit(c):
            return unit_m
    out = []
    chome = {}
    for c in cands:
        m = _CHOME.match(c["name"]) if c["cat"] == 0 else None
        if m:
            chome.setdefault(m.group(1), []).append(c)
        else:
            out.append(c)
    for base, members in chome.items():
        for idx in _cluster([(c["x"], c["y"]) for c in members], LABEL_CHOME_MERGE_M / unit(members[0])):
            group = [members[i] for i in idx]
            first = min(group, key=lambda c: (c["pri"], c["rank"]))
            merged = dict(first, name=base)
            merged["x"] = sum(c["x"] for c in group) / len(group)
            merged["y"] = sum(c["y"] for c in group) / len(group)
            out.append(merged)
    # 同じ種類の中では、近くに同名の点が多いもの (駅なら乗り入れる路線が多いもの) を先に置く
    groups = {}
    for c in out:
        groups.setdefault((c["cat"], "".join(c["name"].split())), []).append(c)
    for (cat, _), members in groups.items():
        for c in members:
            limit = (LABEL_DEDUP_M if cat == 0 else POI_CATEGORIES[cat]["dedup"]) / unit(c)
            c["lines"] = sum(1 for d in members if math.hypot(d["x"] - c["x"], d["y"] - c["y"]) <= limit)
    out.sort(key=lambda c: (c["pri"], -c["lines"] if c["cat"] else 0, c["rank"], c["name"], c["x"], c["y"]))
    kept = []
    seen = {}  # (種類, 名前) -> 残したものの位置。駅と地名が同名でも、種類が違えば両方残す (片方を消して出せるように)
    for c in fixed:
        seen.setdefault((c["cat"], "".join(c["name"].split())), []).append((c["x"], c["y"]))
    for c in out:
        limit = (LABEL_DEDUP_M if c["cat"] == 0 else POI_CATEGORIES[c["cat"]]["dedup"]) / unit(c)
        same = seen.setdefault((c["cat"], "".join(c["name"].split())), [])  # 空白の有無の違いは同名とみなす
        if any(math.hypot(x - c["x"], y - c["y"]) <= limit for x, y in same):
            continue
        same.append((c["x"], c["y"]))
        kept.append(c)
    return kept


def place_labels(cands, fixed=()):
    """優先度の高いものから置き、重なるものを捨てる。これを倍率 2^(k/8) (k = LABEL_SCALE_MIN..LABEL_SCALE_STEPS) ごとに行い、
    各ラベルが表示される倍率の範囲 (k の最小と最大が続いている区間) を "kmin" / "kmax" に入れる。
    一度も置けないものは捨てる。文字は 90° 単位で回るので、横向きと縦向きの両方で重ならないことを確かめる。
    種類 (地名 / POI) をまたいで判定する。種類を消しても、それに押されて置けなかったものは出ない (重ならないことを優先)。
    候補が多い (POI は 1 ズームで十数万) ので、置いたラベルを格子に入れて近くだけ調べる。
    fixed は先に置いてある (動かせない) ラベル (kmin / kmax 付き)。その倍率の範囲では、候補より先に格子に入れる (VMT-DT v4)。"""
    px = EXTENT / 512.0
    if not cands:
        return []
    fixed = list(fixed)
    nf = len(fixed)
    allc = fixed + list(cands)
    n = len(allc)
    cands = allc
    xs = [c["x"] for c in cands]
    ys = [c["y"] for c in cands]
    hw = [0.0] * n
    hh = [0.0] * n
    mg = [0.0] * n
    rp = [0.0] * n  # 同じ種類・同じ名前をこの距離 (倍率 1 のとき) 以内に置かない (道路番号)
    for i, c in enumerate(cands):
        k = c["size"] / float(LABEL_FONT_PX)
        c["advs"] = [glyph_advance(ch) * k for ch in c["name"]]
        hw[i] = sum(c["advs"]) / 2 * px
        hh[i] = c["size"] * 0.6 * px
        plate = plate_dims(c)
        if plate is not None:  # 道路番号は看板の大きさで重なりを調べる
            hw[i] = plate[0] / 2 * px
            hh[i] = (plate[1] / 2 + abs(plate[2])) * px
        mg[i] = c["margin"] * px
        rp[i] = c.get("repeat", 0) * px
    # 箱の半径 (向きを問わない)。重なりうるのは中心どうしが (r_j + r_i + 余白_i) 未満のときだけ
    rad = [max(hw[i], hh[i]) for i in range(n)]
    rmax = max(rad)
    cell0 = 2.0 * sorted(rad)[int(n * 0.9)] + max(mg)
    ks = list(range(LABEL_SCALE_MIN, LABEL_SCALE_STEPS + 1))
    shown = np.zeros((n, len(ks)), dtype=bool)

    def k_index(v, last):
        return (0 if v == 0 else v - 128 - LABEL_SCALE_MIN) if not last else \
            (len(ks) - 1 if v == 255 else v - 128 - LABEL_SCALE_MIN)

    for si, k in enumerate(ks):
        s = 2.0 ** (k / 8.0)
        cell = cell0 * s
        grid = {}
        for i in range(nf):
            c = cands[i]
            if k_index(c["kmin"], False) <= si <= k_index(c["kmax"], True):
                grid.setdefault((int(xs[i] // cell), int(ys[i] // cell)), []).append(i)
        for i in range(nf, n):
            if k > cands[i].get("kcap", LABEL_SCALE_STEPS):  # 道路番号の都道府県道などは、拡大したときだけ
                continue
            gx, gy = int(xs[i] // cell), int(ys[i] // cell)
            r = int(math.ceil((rad[i] + rmax + max(mg[i], rp[i])) * s / cell))
            hit = False
            for cx in range(gx - r, gx + r + 1):
                for cy in range(gy - r, gy + r + 1):
                    for j in grid.get((cx, cy), ()):
                        dx = abs(xs[j] - xs[i])
                        dy = abs(ys[j] - ys[i])
                        if rp[i] and cands[j]["cat"] == cands[i]["cat"] and cands[j]["name"] == cands[i]["name"] \
                                and dx * dx + dy * dy < (rp[i] * s) ** 2:
                            hit = True
                            break
                        # 既に置いたラベルの箱 (余白なし) と、自分の箱を余白ぶん広げたもの
                        w = (hw[j] + hw[i] + mg[i]) * s
                        h = (hh[j] + hh[i] + mg[i]) * s
                        if (dx < w and dy < h) or (dx < h and dy < w):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                continue
            grid.setdefault((gx, gy), []).append(i)
            shown[i, si] = True
    steps = len(ks)
    out = []
    for i, c in enumerate(cands):
        if i < nf:
            continue
        row = shown[i]
        best, start = None, None
        for k in range(steps + 1):
            if k < steps and row[k]:
                if start is None:
                    start = k
            elif start is not None:
                if best is None or k - start > best[1] - best[0] + 1:
                    best = (start, k - 1)
                start = None
        if best is None:
            continue
        c["kmin"] = 0 if best[0] == 0 else 128 + ks[best[0]]
        c["kmax"] = 255 if best[1] == steps - 1 else 128 + ks[best[1]]
        out.append(c)
    return out


def plan_labels(db, levels, lat):
    """全ズームのラベル配置を決め、{(z, x, y): [ラベル, ...]} (優先度順) を返す。"""
    plan = {}
    if not LABEL_CONFIG["enabled"]:
        return plan
    for lv in levels:
        z = lv["z"]
        tiles = [(lv["minX"] + i, lv["minY"] + j) for j in range(lv["h"]) for i in range(lv["w"])]
        if LABEL_CONFIG["wide"] is not None:  # 点はもうまとめてある (wide_labels.py)
            cands = wide_candidates(db, z, tiles)
        else:
            cands = merge_labels(label_candidates(db, z, tiles), _unit_m(z, lat))
        placed = place_labels(cands)
        inside = set(tiles)
        count = 0
        for c in placed:
            key = (int(c["x"] // EXTENT), int(c["y"] // EXTENT))
            if key not in inside:  # 丁目をまとめて範囲の外に出たもの
                continue
            plan.setdefault((z,) + key, []).append(c)
            count += 1
        cats = {}
        for c in placed:
            cats[c["cat"]] = cats.get(c["cat"], 0) + 1
        print(f"z{z}: labels {count} (candidates {len(cands)}) by category {dict(sorted(cats.items()))}", flush=True)
    return plan


def label_advs(lab):
    """ラベルの字ごとの送り幅 (px)。place_labels が入れるものと同じ (外から渡した配置 (--plan) に足す)。"""
    k = lab["size"] / float(LABEL_FONT_PX)
    return [glyph_advance(ch) * k for ch in lab["name"]]


def build_labels(planned, z, tx, ty, max_atlas_rows=MAX_H):
    """plan_labels で決めたラベルの頂点を作る。戻り値は (ラベル頂点 (n, 10), アトラス画像 (rows, 2048) または None, ラベル数)。

    ラベル頂点 = 基準点 x, y / ずれ x, y (いずれもタイル単位) / アトラス u, v (px) / 縁取りフラグ /
    表示する倍率の範囲 (kmin, kmax) / 種類。プールメッシュに収まらない分と、文字アトラスが max_atlas_rows 行を
    超える分 (テクスチャの高さの上限) は優先度の低いものから捨てる。
    """
    empty = (np.zeros((0, 10)), None, 0)
    max_glyphs = (max_atlas_rows // LABEL_CELL) * LABEL_COLS
    if not planned:
        return empty
    px = EXTENT / 512.0
    glyphs = {}
    halo_quads, text_quads = [], []
    count = 0
    corners = [(0, 0), (1, 0), (0, 1), (1, 0), (1, 1), (0, 1)]
    for lab in planned:
        chars = [ch for ch in lab["name"] if not ch.isspace()]
        if len(halo_quads) + len(text_quads) + 12 * len(chars) > LABEL_CONFIG["verts"]:
            continue
        if len(glyphs) + len(set(ch for ch in chars if ch not in glyphs)) > max_glyphs:
            continue
        x, y = lab["x"] - tx * EXTENT, lab["y"] - ty * EXTENT
        k = lab["size"] / float(LABEL_FONT_PX)
        width = sum(lab["advs"])
        pen = -width / 2
        for ch, adv in zip(lab["name"], lab["advs"]):
            if not ch.isspace():
                g = glyphs.setdefault(ch, len(glyphs))
                u0, v0 = (g % LABEL_COLS) * LABEL_CELL, (g // LABEL_COLS) * LABEL_CELL
                left = (pen + adv / 2 - LABEL_CELL * k / 2) * px
                top = -LABEL_CELL * k / 2 * px
                w = LABEL_CELL * k * px
                c = LABEL_CELL
                for flag, dst in ((1, halo_quads), (0, text_quads)):
                    for cx, cy in corners:
                        dst.append((x, y, left + cx * w, top + cy * w, u0 + cx * c, v0 + cy * c, flag,
                                    lab["kmin"], lab["kmax"], lab.get("cat", 0)))
            pen += adv
        count += 1
    if not glyphs:
        return empty
    rows = -(-len(glyphs) // LABEL_COLS) * LABEL_CELL
    atlas = np.zeros((rows, TEX_W), dtype=np.uint8)
    for ch, g in glyphs.items():
        u0, v0 = (g % LABEL_COLS) * LABEL_CELL, (g // LABEL_COLS) * LABEL_CELL
        atlas[v0:v0 + LABEL_CELL, u0:u0 + LABEL_CELL] = glyph_sdf(ch)[0]
    return np.array(halo_quads + text_quads, dtype=np.float64), atlas, count


def build_glyph_labels(planned, z, tx, ty, used=None):
    """形式 2: plan_labels で決めた 1 言語のラベルの字レコードを作る。

    戻り値は (字レコード (n, 9) = 基準点 x, y / 箱の中心のずれ x, y (タイル単位) / 字 (Unicode の文字コード) / 箱の一辺 (タイル単位) /
    kmin / kmax / 種類, ラベル数)。使った字を used に足す。プールメッシュ (字数 x 12 頂点) に収まらない分は優先度の低いものから捨てる。
    """
    if not planned:
        return np.zeros((0, 9)), 0
    px = EXTENT / 512.0
    out = []
    count = 0
    for lab in planned:
        chars = [ch for ch in lab["name"] if not ch.isspace()]
        plate = plate_dims(lab)
        if 12 * (len(out) + len(chars) + (plate is not None)) > LABEL_CONFIG["verts"]:
            continue
        x, y = lab["x"] - tx * EXTENT, lab["y"] - ty * EXTENT
        k = lab["size"] / float(LABEL_FONT_PX)
        side = LABEL_CELL * k * px
        if plate is not None:
            # 看板のレコード: 箱の一辺 = 看板の高さ、字 = PLATE_FLAG | 形 << 20 | (縦のずれ / 高さ * 128 + 128) << 10 | 幅 / 高さ * 128
            pw, ph, py, shape = plate
            ratio = int(min(max(round(pw / ph * 128), 1), 1023))
            yq = int(min(max(round(py / ph * 128) + 128, 0), 255))
            g = PLATE_FLAG | (shape << 20) | (yq << 10) | ratio
            out.append((x, y, 0.0, 0.0, g, ph * px, lab["kmin"], lab["kmax"], lab.get("cat", 0)))
        pen = -sum(lab["advs"]) / 2
        for ch, adv in zip(lab["name"], lab["advs"]):
            if not ch.isspace():
                g = ord(ch)  # 文字コード (ワールドの表でアトラスのマスを引く)
                if used is not None:
                    used.add(ch)
                cx = (pen + glyph_ink_center(ch) * k) * px
                out.append((x, y, cx, 0.0, g, side, lab["kmin"], lab["kmax"], lab.get("cat", 0)))
            pen += adv
        count += 1
    return np.array(out, dtype=np.float64).reshape(-1, 9), count


def encode_glyph_records(recs):
    """形式 2 の字レコード (n, 9) を (n * LABEL_STRIDE, 3) のテクセルにする。"""
    n = len(recs)
    flat = np.zeros((LABEL_STRIDE * n, 3), dtype=np.uint8)
    if n == 0:
        return flat
    off = np.clip(np.rint(recs[:, 2]) + 2048, 0, 4095).astype(np.int64)
    g = recs[:, 4].astype(np.int64)
    side = np.clip(np.rint(recs[:, 5]), 1, 1023).astype(np.int64)
    st = LABEL_STRIDE
    flat[0::st] = pack12(quantize_pos(recs[:, 0]), quantize_pos(recs[:, 1]))
    flat[1::st] = pack12(off, side)
    flat[2::st, 0] = (g >> 16) & 255
    flat[2::st, 1] = (g >> 8) & 255
    flat[2::st, 2] = g & 255
    flat[3::st, 0] = recs[:, 6].astype(np.uint8)
    flat[3::st, 1] = recs[:, 7].astype(np.uint8)
    flat[3::st, 2] = recs[:, 8].astype(np.uint8)
    return flat


def pack12(qx, qy):
    """12bit の値 2 つを RGB 3 バイトに詰める。"""
    out = np.zeros((len(qx), 3), dtype=np.uint8)
    out[:, 0] = qx >> 4
    out[:, 1] = ((qx & 15) << 4) | (qy >> 8)
    out[:, 2] = qy & 255
    return out


# ---------------------------------------------------------------- 量子化とテクスチャ

def quantize_pos(v):
    q = np.rint((np.asarray(v, dtype=np.float64) - QMIN) * (4095.0 / QRANGE))
    return np.clip(q, 0, 4095).astype(np.int64)


def quantize_normal(n):
    q = np.rint(np.asarray(n, dtype=np.float64) * NORMAL_SCALE) + 128
    return np.clip(q, 2, 254).astype(np.int64)


def build_records(layers, style, zoom):
    """スタイル順に頂点レコード (n, 5) = qx, qy, style, qnx, qny を作る。"""
    lo, hi = QMIN, QMIN + QRANGE - 2.0
    chunks = []
    stats = {}
    for sid, rule in enumerate(style):
        if rule["type"] == "background":
            quad = np.array([[0, 0], [EXTENT, 0], [0, EXTENT], [EXTENT, 0], [EXTENT, EXTENT], [0, EXTENT]], dtype=np.float64)
            rec = np.column_stack([quantize_pos(quad[:, 0]), quantize_pos(quad[:, 1]),
                                   np.full(6, sid), np.full(6, 128), np.full(6, 128)])
            chunks.append(rec)
            stats[rule["name"]] = 2
            continue
        layer = layers.get(rule["layer"])
        if not layer:
            continue
        scale = EXTENT / float(layer["extent"])
        parts = []
        for feat in layer["features"]:
            if not _rule_matches(rule, feat["props"], feat["type"], zoom):
                continue
            if rule["type"] == "fill":
                tri = polygon_triangles(feat["geom"], scale, lo, hi)
                if len(tri):
                    parts.append(np.column_stack([tri, np.zeros((len(tri), 2))]))
            else:
                rec = line_records(feat["geom"], scale, lo, hi)
                if len(rec):
                    parts.append(rec)
        if not parts:
            continue
        arr = np.concatenate(parts)
        rec = np.column_stack([quantize_pos(arr[:, 0]), quantize_pos(arr[:, 1]), np.full(len(arr), sid),
                               quantize_normal(arr[:, 2]), quantize_normal(arr[:, 3])])
        chunks.append(rec)
        stats[rule["name"]] = len(arr) // 3
    return np.concatenate(chunks).astype(np.int64), stats


def build_texture(records, style, z, x, y, labels=None, atlas=None, blocks=None):
    """頂点レコードとスタイルから (H, 2048, 3) の uint8 配列 (論理行 0 が先頭) を作る。
    形式 2 は labels の代わりに blocks (言語ごとの字レコード (n, 9) のリスト) を渡す。atlas はタイルのアトラス (無ければ None)。"""
    n = len(records)
    data_rows = (2 * n + TEX_W - 1) // TEX_W
    label_row = DATA_ROW + data_rows
    if blocks is not None:
        block_rows = []
        row = label_row
        for b in blocks:
            r = (LABEL_STRIDE * len(b) + TEX_W - 1) // TEX_W
            block_rows.append((row, r))
            row += r
        atlas_row = row
        atlas_rows = atlas.shape[0] if atlas is not None else 0
        nl = 0
        label_rows = 0
    else:
        nl = 0 if labels is None or atlas is None else len(labels)
        label_rows = (LABEL_STRIDE * nl + TEX_W - 1) // TEX_W
        atlas_row = label_row + label_rows
        atlas_rows = atlas.shape[0] if nl else 0
    h = atlas_row + atlas_rows + 1
    if h > MAX_H:
        raise ValueError(f"三角形が多すぎます ({n // 3})。高さ {h} > {MAX_H}")
    tex = np.zeros((h, TEX_W, 3), dtype=np.uint8)
    # ヘッダ
    tex[0, 0] = MARK_TOP
    tex[0, 1] = (2 if blocks is not None else FORMAT_VERSION, 0, 0)
    tex[0, 2] = PROBE
    tex[0, 3] = ((n >> 16) & 255, (n >> 8) & 255, n & 255)
    tex[0, 4] = (z, 0, 0)
    tex[0, 5] = ((x >> 16) & 255, (x >> 8) & 255, x & 255)
    tex[0, 6] = ((y >> 16) & 255, (y >> 8) & 255, y & 255)
    tex[0, 7] = ((h >> 16) & 255, (h >> 8) & 255, h & 255)
    tex[0, 8] = ((TEX_W >> 16) & 255, (TEX_W >> 8) & 255, TEX_W & 255)
    for i in range(256):
        tex[0, RAMP_COL + i] = ramp_value(i)
    tex[1, TEX_W - 1] = (0xA5, 1, 0x5A)
    tex[2, TEX_W - 1] = (0xA5, 2, 0x5A)
    # パレット
    for sid, rule in enumerate(style):
        tex[1, sid] = _hex(rule["color"])
        if rule["type"] == "line":
            w8 = int(round(rule.get("width", 1.0) * 8))
            tex[2, sid] = (max(1, min(255, w8)), 1, 0)
    # レコード
    if n:
        flat = np.zeros((data_rows * TEX_W, 3), dtype=np.uint8)
        qx, qy = records[:, 0], records[:, 1]
        flat[0:2 * n:2, 0] = qx >> 4
        flat[0:2 * n:2, 1] = ((qx & 15) << 4) | (qy >> 8)
        flat[0:2 * n:2, 2] = qy & 255
        flat[1:2 * n:2, 0] = records[:, 2]
        flat[1:2 * n:2, 1] = records[:, 3]
        flat[1:2 * n:2, 2] = records[:, 4]
        tex[DATA_ROW:DATA_ROW + data_rows] = flat.reshape(data_rows, TEX_W, 3)
    if nl:
        tex[0, 9] = ((nl >> 16) & 255, (nl >> 8) & 255, nl & 255)
        tex[0, 10] = ((label_row >> 16) & 255, (label_row >> 8) & 255, label_row & 255)
        tex[0, 11] = ((atlas_row >> 16) & 255, (atlas_row >> 8) & 255, atlas_row & 255)
        tex[0, 12] = (LABEL_STRIDE, 0, 0)
        flat = np.zeros((label_rows * TEX_W, 3), dtype=np.uint8)
        off = np.clip(np.rint(labels[:, 2:4]) + 2048, 0, 4095).astype(np.int64)
        u = labels[:, 4].astype(np.int64) + labels[:, 6].astype(np.int64) * 2048
        v = labels[:, 5].astype(np.int64)
        st, end = LABEL_STRIDE, LABEL_STRIDE * nl
        flat[0:end:st] = pack12(quantize_pos(labels[:, 0]), quantize_pos(labels[:, 1]))
        flat[1:end:st] = pack12(off[:, 0], off[:, 1])
        flat[2:end:st] = pack12(u, v)
        flat[3:end:st, 0] = labels[:, 7].astype(np.uint8)
        flat[3:end:st, 1] = labels[:, 8].astype(np.uint8)
        flat[3:end:st, 2] = labels[:, 9].astype(np.uint8)
        tex[label_row:label_row + label_rows] = flat.reshape(label_rows, TEX_W, 3)
        tex[atlas_row:atlas_row + atlas_rows, :, 0] = atlas
    if blocks is not None:
        def put24(col, v):
            tex[0, col] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        for li, (b, (row, r)) in enumerate(zip(blocks, block_rows)):
            put24(9 if li == 0 else 13, len(b))
            put24(10 if li == 0 else 14, row)
            if r:
                flat = np.zeros((r * TEX_W, 3), dtype=np.uint8)
                flat[:LABEL_STRIDE * len(b)] = encode_glyph_records(b)
                tex[row:row + r] = flat.reshape(r, TEX_W, 3)
        tex[0, 12] = (LABEL_STRIDE, 0, 0)
        if atlas_rows:
            put24(11, atlas_row)
            tex[atlas_row:atlas_row + atlas_rows, :, 0] = atlas
    tex[h - 1, 0] = MARK_BOTTOM
    return tex


def write_png(path, tex):
    from PIL import Image
    Image.fromarray(tex, "RGB").save(path, optimize=True)


def write_raw(path, tex):
    """RGBA32 の生データ。先頭行が論理行 0 (LoadRawTextureData では y = 0 になる)。"""
    h, w, _ = tex.shape
    rgba = np.full((h, w, 4), 255, dtype=np.uint8)
    rgba[:, :, :3] = tex
    with open(path, "wb") as f:
        f.write(rgba.tobytes())


# ---------------------------------------------------------------- タイル入出力

def lonlat_to_tile(lon, lat, z):
    n = 1 << z
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n
    return int(x), int(y)


def read_tile(db, z, x, y):
    tms_y = (1 << z) - 1 - y
    row = db.execute("select tile_data from tiles where zoom_level=? and tile_column=? and tile_row=?",
                     (z, x, tms_y)).fetchone()
    if row is None:
        return None
    data = bytes(row[0])
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def bake_tile(db, style, z, x, y, planned=None):
    data = read_tile(db, z, x, y)
    layers = decode_mvt(data) if data else {}
    records, stats = build_records(layers, style, z)
    # 文字アトラスに使える行 = 高さの上限 - ヘッダ類 - 頂点レコード - ラベル頂点 (プールいっぱいのとき) - 末尾の行
    data_rows = (2 * len(records) + TEX_W - 1) // TEX_W
    label_rows = (LABEL_STRIDE * LABEL_CONFIG["verts"] + TEX_W - 1) // TEX_W
    if LABEL_CONFIG["format"] >= 2:
        # planned は言語ごとのラベルのリスト。字はどれも文字コードで持つ (タイルのアトラスは作らない)
        langs = LABEL_CONFIG["langs"]
        used = set()
        blocks, counts = [], []
        for li in range(len(langs)):
            recs, count = build_glyph_labels((planned or {}).get(li), z, x, y, used)
            blocks.append(recs)
            counts.append(count)
        stats["_labels"] = counts[0]
        stats["_labelsByLang"] = counts
        stats["_labelVertices"] = 12 * max(len(b) for b in blocks)
        stats["_labelVerticesByLang"] = [12 * len(b) for b in blocks]
        stats["_sharedGlyphs"] = "".join(sorted(used))
        tex = build_texture(records, style, z, x, y, blocks=blocks)
        return tex, records, stats
    labels, atlas, count = build_labels(planned, z, x, y, MAX_H - DATA_ROW - data_rows - label_rows - 1)
    stats["_labels"] = count
    stats["_labelVertices"] = len(labels)
    tex = build_texture(records, style, z, x, y, labels, atlas)
    return tex, records, stats


_worker = {}


def _worker_init(ctx):
    mbtiles, out, style, label_config, no_bin, dry_run, plan = ctx
    LABEL_CONFIG.update(label_config)
    _worker.update(db=sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True), out=out, style=style,
                   no_bin=no_bin, dry_run=dry_run, plan=plan)


def _worker_bake(job):
    """1 タイルを焼いてファイルに書き、manifest 用の情報を返す。"""
    z, tx, ty = job
    tex, records, stats = bake_tile(_worker["db"], _worker["style"], z, tx, ty, _worker["plan"].get(job))
    n = len(records)
    info = {
        "z": z, "x": tx, "y": ty,
        "png": f"tiles/{z}/{tx}/{ty}.png", "bin": f"tiles/{z}/{tx}/{ty}.bin",
        "vertices": n, "triangles": n // 3, "height": int(tex.shape[0]),
        "chunks": max(1, -(-n // VERTS_PER_CHUNK)),
        "labels": stats["_labels"], "labelVertices": stats["_labelVertices"],
        "binBytes": tex.shape[0] * TEX_W * 4,
    }
    if "_labelsByLang" in stats:
        info["labelsByLang"] = stats["_labelsByLang"]
        info["labelVerticesByLang"] = stats["_labelVerticesByLang"]
        info["_sharedGlyphs"] = stats["_sharedGlyphs"]  # manifest の sharedGlyphs にまとめる
    if _worker["dry_run"]:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray(tex, "RGB").save(buf, format="PNG", optimize=True)
        info["pngBytes"] = buf.tell()
        return info
    d = os.path.join(_worker["out"], "tiles", str(z), str(tx))
    os.makedirs(d, exist_ok=True)
    png = os.path.join(d, f"{ty}.png")
    write_png(png, tex)
    if not _worker["no_bin"]:
        write_raw(os.path.join(d, f"{ty}.bin"), tex)
    info["pngBytes"] = os.path.getsize(png)
    print(f"z{z} {tx}/{ty}: {n // 3:7d} tris, {stats['_labels']:3d} labels, {tex.shape[0]:4d} rows, "
          f"png {info['pngBytes'] / 1024:7.0f} KB", flush=True)
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="MVT を VRChat 用データテクスチャに焼く")
    ap.add_argument("mbtiles")
    ap.add_argument("out")
    ap.add_argument("--center", default=None, help="中心の経度,緯度 (例: 8.5417,47.3769)。--radius と組で使う")
    ap.add_argument("--bbox", default=None, help="焼く範囲 西,南,東,北 (経度緯度)。--center / --radius の代わり")
    ap.add_argument("--part", default=None, help="k/n: タイルを n 組に分けた k 番目 (0 始まり) だけ焼く。"
                    "全組がそろった時点で manifest を書く。時間制限のある環境で分けて実行するため")
    ap.add_argument("--jobs", type=int, default=1, help="並列に焼くプロセス数")
    ap.add_argument("--no-bin", action="store_true", help=".bin (String Loading 用) を書かない")
    ap.add_argument("--dry-run", action="store_true", help="ファイルを書かず、規模だけ集計する")
    ap.add_argument("--zooms", default="14", help="焼くズーム (例: 12-14 または 14)")
    ap.add_argument("--radius", type=int, default=1, help="最大ズームで中心タイルから何枚先まで焼くか")
    ap.add_argument("--style", default=None, help="スタイル JSON (省略時は内蔵の既定スタイル)")
    ap.add_argument("--font", default=None, help="ラベルのフォント (.ttf / .ttc)。省略時は既知の場所から探す")
    ap.add_argument("--lang", default=None, help="ラベルの言語 (例: ja)。その言語の名前が無い地名は現地語になる")
    ap.add_argument("--label-spacing", type=float, default=1.0,
                    help="ラベルの周囲に空ける余白の倍率。大きくすると疎に、小さくすると密になる (既定 1.0)")
    ap.add_argument("--no-labels", action="store_true", help="ラベルを焼かない")
    ap.add_argument("--no-poi", action="store_true", help="POI (駅・観光地・店など) をラベルに入れない (地名だけ)")
    ap.add_argument("--wide-labels", default=None, metavar="JSON",
                    help="広域の地図: 地名を wide_labels.py の点 (市区町村・駅・島) と OpenStreetMap の島・海・湖の名前にする")
    ap.add_argument("--boundary-levels", default=None, metavar="N[,N]",
                    help="境界線はこの admin_level の陸上の線だけ描く (海の上の線と係争中の線は描かない)。例: 4")
    ap.add_argument("--label-verts", type=int, default=LABEL_VERTS_PER_POOL,
                    help=f"1 タイルのラベル頂点の上限 (既定 {LABEL_VERTS_PER_POOL}。90 の倍数)。Unity の地名の入れ物の最大と合わせる")
    ap.add_argument("--tile-format", type=int, default=1, choices=(1, 2),
                    help="タイルの形式。2 = ラベルの字を Unicode の文字コードで持ち (ワールドの共有の字のアトラスから読む)、言語ごとの塊 (--langs) にする (v4)")
    ap.add_argument("--langs", default=None, metavar="L[,L]",
                    help="形式 2 のラベルの言語の塊 (例: ja,en。最大 2)。省略時は --lang だけ。広域の地図 (--wide-labels) は最初の言語だけ")
    ap.add_argument("--tiles", default=None, metavar="JSON",
                    help="範囲のうち、このタイル ([[z, x, y], ...]) だけを焼く (VMT-DT v4 の持ち主の分。manifest の tiles もこれだけ)")
    ap.add_argument("--plan", default=None, metavar="JSON",
                    help="ラベルの配置を自分で決めず、これを使う ({\"z/x/y\": [[言語 0 のラベル...], ...]}。vmt_v4.py が地域をまたいで決める)")
    args = ap.parse_args(argv)
    if args.label_verts <= 0 or args.label_verts % 90:
        ap.error("--label-verts は 90 の倍数 (Unity の地名の入れ物は 15 段階で、1 段は 6 の倍数)")

    if args.bbox:
        w, s_, e, n_ = [float(v) for v in args.bbox.split(",")]
        lon, lat = (w + e) / 2, (s_ + n_) / 2
    elif args.center:
        lon, lat = [float(v) for v in args.center.split(",")]
    else:
        ap.error("--center か --bbox のどちらかを指定してください")
    if "-" in args.zooms:
        z0, z1 = [int(v) for v in args.zooms.split("-")]
    else:
        z0 = z1 = int(args.zooms)
    style = load_style(args.style)
    if args.boundary_levels:
        levels_wanted = [int(v) for v in args.boundary_levels.split(",")]
        for rule in style:
            if rule.get("layer") == "boundary":
                rule.pop("max_admin_level", None)
                rule["admin_levels"] = levels_wanted
    wide = None
    wide_attribution = []
    if args.wide_labels:
        with open(args.wide_labels, encoding="utf-8") as f:
            w_json = json.load(f)
        if w_json.get("format") != "wide-labels":
            ap.error(f"{args.wide_labels} は wide_labels.py の出力ではない")
        wide = w_json["labels"]
        wide_attribution = w_json.get("attribution", [])
        unknown = {e["kind"] for e in wide} - set(WIDE_KINDS)
        if unknown:
            ap.error(f"{args.wide_labels} に知らない kind: {', '.join(sorted(unknown))}")
    LABEL_CONFIG.update(font=args.font, lang=args.lang, enabled=not args.no_labels, spacing=args.label_spacing,
                        poi=not args.no_poi and wide is None, verts=args.label_verts, wide=wide)
    if args.tile_format >= 2:
        langs = [v for v in (args.langs or args.lang or "ja").split(",") if v]
        if len(langs) > MAX_LANGS:
            ap.error(f"--langs は {MAX_LANGS} つまで")
        if wide is not None and len(langs) > 1:
            print(f"広域の地図の地名は {langs[0]} だけなので、{', '.join(langs[1:])} の塊は作らない", flush=True)
            langs = langs[:1]
        LABEL_CONFIG.update(format=2, langs=langs, lang=langs[0])
    db = sqlite3.connect(f"file:{args.mbtiles}?mode=ro", uri=True)
    if not args.dry_run:
        os.makedirs(args.out, exist_ok=True)

    # 最大ズームの範囲を決め、下位ズームはそれを覆う矩形にする
    if args.bbox:
        x0, y0 = lonlat_to_tile(w, n_, z1)
        x1, y1 = lonlat_to_tile(e, s_, z1)
    else:
        cx, cy = lonlat_to_tile(lon, lat, z1)
        x0, x1, y0, y1 = cx - args.radius, cx + args.radius, cy - args.radius, cy + args.radius
    levels = []
    jobs = []
    for z in range(z0, z1 + 1):
        s = z1 - z
        lx0, lx1, ly0, ly1 = x0 >> s, x1 >> s, y0 >> s, y1 >> s
        levels.append({"z": z, "minX": lx0, "minY": ly0, "w": lx1 - lx0 + 1, "h": ly1 - ly0 + 1, "offset": len(jobs)})
        for ty in range(ly0, ly1 + 1):
            for tx in range(lx0, lx1 + 1):
                jobs.append((z, tx, ty))

    if args.tiles:
        with open(args.tiles, encoding="utf-8") as f:
            wanted = {tuple(t) for t in json.load(f)}
        unknown = wanted - set(jobs)
        if unknown:
            ap.error(f"--tiles に範囲の外のタイルがある: {sorted(unknown)[:3]}")
        jobs = [j for j in jobs if j in wanted]
        for lv in levels:  # offset は、そのズームの最初のタイルの番号 (無ければ次のズームの先頭)
            lv["offset"] = next((i for i, j in enumerate(jobs) if j[0] >= lv["z"]), len(jobs))

    # ラベルはズームごとに全タイルまとめて配置する (タイルをまたぐ重なりと重複を除くため)
    if args.plan:
        LABEL_CONFIG["font"] = LABEL_CONFIG["font"] or next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
        with open(args.plan, encoding="utf-8") as f:
            given = json.load(f)
        plan = {}
        job_set = set(jobs)
        for key, per_lang in given.items():
            z, x, y = (int(v) for v in key.split("/"))
            if (z, x, y) not in job_set:
                continue
            for labs in per_lang:
                for lab in labs:
                    lab["advs"] = label_advs(lab)
            if LABEL_CONFIG["format"] >= 2:
                plan[(z, x, y)] = {li: labs for li, labs in enumerate(per_lang)}
            else:
                plan[(z, x, y)] = per_lang[0]
    elif LABEL_CONFIG["format"] >= 2:
        # 言語ごとに配置する (字の幅が違うので、重なりの判定も別)。plan[(z, x, y)] = {言語の番号: ラベルのリスト}
        plan = {}
        for li, lang in enumerate(LABEL_CONFIG["langs"]):
            LABEL_CONFIG["lang"] = lang
            print(f"labels: {lang}", flush=True)
            for key, labs in plan_labels(db, levels, lat).items():
                plan.setdefault(key, {})[li] = labs
        LABEL_CONFIG["lang"] = LABEL_CONFIG["langs"][0]
    else:
        plan = plan_labels(db, levels, lat)
    LABEL_CONFIG["font"] = LABEL_CONFIG["font"] or next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    ctx = (args.mbtiles, args.out, style, dict(LABEL_CONFIG, wide=None), args.no_bin, args.dry_run, plan)
    todo = jobs
    part_dir = os.path.join(args.out, "_parts")
    if args.part:
        pk, pn = [int(v) for v in args.part.split("/")]
        todo = jobs[pk::pn]
    if args.jobs > 1:
        import multiprocessing
        with multiprocessing.Pool(args.jobs, initializer=_worker_init, initargs=(ctx,)) as pool:
            tiles = pool.map(_worker_bake, todo, chunksize=4)
    else:
        _worker_init(ctx)
        tiles = [_worker_bake(j) for j in todo]
    if args.part:
        os.makedirs(part_dir, exist_ok=True)
        with open(os.path.join(part_dir, f"{pk}_{pn}.json"), "w", encoding="utf-8") as f:
            json.dump(tiles, f)
        parts = [os.path.join(part_dir, f"{k}_{pn}.json") for k in range(pn)]
        missing = [k for k, q in enumerate(parts) if not os.path.exists(q)]
        if missing:
            print(f"part {pk}/{pn} done ({len(tiles)} tiles). 残り: {missing}")
            return
        merged = {}
        for q in parts:
            for t in json.load(open(q, encoding="utf-8")):
                merged[(t["z"], t["x"], t["y"])] = t
        tiles = [merged[j] for j in jobs]
    LABEL_CONFIG["font"] = LABEL_CONFIG["font"] or next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    shared_glyphs = set()
    for t in tiles:
        shared_glyphs.update(t.pop("_sharedGlyphs", ""))

    if args.dry_run:
        for lv in levels:
            ts = [t for t in tiles if t["z"] == lv["z"]]
            tri = [t["triangles"] for t in ts]
            print(f"z{lv['z']}: {len(ts):4d} tiles ({lv['w']}x{lv['h']}), tris max {max(tri):7d} / median {sorted(tri)[len(tri) // 2]:7d}, "
                  f"chunks max {max(t['chunks'] for t in ts)}, rows max {max(t['height'] for t in ts)}, "
                  f"png total {sum(t['pngBytes'] for t in ts) / 1e6:7.1f} MB (max {max(t['pngBytes'] for t in ts) / 1e6:.2f}), "
                  f"bin total {sum(t['binBytes'] for t in ts) / 1e6:7.1f} MB, labels {sum(t['labels'] for t in ts)}")
        return

    # 自己診断用 (ジオメトリは背景の 2 三角形だけ)
    rec, _ = build_records({}, style, 0)
    st = build_texture(rec, style, 0, 0, 0)
    write_png(os.path.join(args.out, "selftest.png"), st)
    if not args.no_bin:
        write_raw(os.path.join(args.out, "selftest.bin"), st)

    # 元データの帰属表示 (mbtiles の metadata)。ワールド内に表示する文言の元にする
    row = db.execute("select value from metadata where name='attribution'").fetchone()
    source_attribution = re.sub(r"<[^>]+>", "", row[0]).replace("&copy;", "©").strip() if row else ""
    manifest = {
        "format": "VMT-DT", "version": FORMAT_VERSION, "width": TEX_W,
        "vertsPerChunk": VERTS_PER_CHUNK, "center": [lon, lat],
        "labelConfig": {"font": LABEL_CONFIG["font"], "lang": LABEL_CONFIG["lang"], "enabled": LABEL_CONFIG["enabled"],
                        "spacing": LABEL_CONFIG["spacing"], "poi": LABEL_CONFIG["poi"],
                        "wide": os.path.basename(args.wide_labels) if args.wide_labels else None,
                        "boundaryLevels": args.boundary_levels},
        # 広域の地図は地名 (0) と駅 (1)。種類の番号は POI と同じ (タブレットの「駅」のチェックで出し入れできる)
        "labelCategories": label_categories()[:2] if wide is not None
        else label_categories() if LABEL_CONFIG["poi"] else label_categories()[:1],
        # 1 タイルのラベル頂点の上限。Unity は地名の入れ物をこの大きさまで用意する (タイルの labelVertices で選ぶ)
        "labelVertsPerPool": LABEL_CONFIG["verts"],
        "levels": levels, "tiles": tiles,
        "styles": [r["name"] for r in style],
        "attribution": source_attribution or "© OpenStreetMap contributors",
    }
    if wide_attribution:
        manifest["labelAttribution"] = wide_attribution
    if LABEL_CONFIG["format"] >= 2:
        # version は manifest の形 (1 = タイル 1 枚ずつ。vmt_pack.py のシートは 2) のままにし、タイルの形式は tileFormat に書く
        manifest["tileFormat"] = 2
        manifest["labelLanguages"] = LABEL_CONFIG["langs"]
        # タイルが文字コードで指す字 (共有の字のアトラスから読む字)。ワールドは床の字としてこれだけを作る
        manifest["sharedGlyphs"] = "".join(sorted(shared_glyphs))
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"{len(tiles)} tiles -> {args.out}")


if __name__ == "__main__":
    main()
