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
              T3 = (kmin, kmax, 0)  表示する文字の倍率 s の範囲。k = 8 log2(s) + 128 が kmin..kmax のとき表示する
                   (0 / 255 は下限 / 上限なし)。倍率を上げても重ならないよう、ベイク時に倍率ごとに配置を決めてある
            文字アトラス  48 × 48 の距離画像 (SDF) を横に 42 個ずつ並べる。R チャンネルのみ使う
            ヘッダの列 9 / 10 / 11 / 12 にラベル頂点数 / ラベル開始行 / アトラス開始行 (24bit) / ラベル頂点のテクセル数
  行 H-1    列 0 にマーカー BOTTOM = (0x62, 0x6F, 0x74)。他は未使用

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
LABEL_CELL = 48          # アトラスの 1 文字のマス (px)
LABEL_FONT_PX = 30       # マスに描くときの文字サイズ (px)
LABEL_SPREAD = 6.0       # 距離画像が表す距離の幅 (px)
LABEL_COLS = TEX_W // LABEL_CELL
LABEL_VERTS_PER_POOL = 16380  # ラベル用プールメッシュの頂点数 (3 の倍数)
LABEL_STRIDE = 4         # ラベル頂点 1 つのテクセル数
LABEL_SCALE_STEPS = 24   # 重なりを調べる倍率は 2^(k/8)、k = 0..24 (1 倍から 8 倍)
LABEL_CHOME_MERGE_M = 1500.0  # 丁目違いをまとめる距離 (m)
LABEL_DEDUP_M = 3000.0        # この距離以内の同名の地名は 1 つにする (m)
# 地名の種別 -> (512px タイルでの文字サイズ px, 優先度, 周囲に空ける余白 px)。優先度は小さいほど先に置く。
# 余白を大きくすると、その種別のラベルが間引かれる (丁目などの細かい地名が埋め尽くさないようにする)
LABEL_CLASSES = {
    "city": (18, 0, 4), "town": (15, 1, 4), "village": (13, 2, 4), "suburb": (13, 3, 6),
    "quarter": (12, 4, 10), "neighbourhood": (10, 5, 26), "hamlet": (10, 6, 26),
}
LABEL_CONFIG = {"font": None, "lang": None, "enabled": True, "spacing": 1.0}
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


def _font():
    path = LABEL_CONFIG["font"]
    if path is None:
        path = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
        if path is None:
            raise RuntimeError("フォントが見つかりません。--font で指定してください")
        LABEL_CONFIG["font"] = path
    if path not in _font_cache:
        from PIL import ImageFont
        _font_cache[path] = ImageFont.truetype(path, LABEL_FONT_PX)
    return _font_cache[path]


_glyph_cache = {}


def glyph_sdf(ch):
    """1 文字の距離画像 (LABEL_CELL 四方、uint8、128 が輪郭、内側ほど大きい) と送り幅 (px)。"""
    key = (LABEL_CONFIG["font"], ch)
    if key in _glyph_cache:
        return _glyph_cache[key]
    from PIL import Image, ImageDraw, ImageFont
    from scipy import ndimage
    font = _font()
    ss = 4  # 4 倍で描いて距離を測り、縮小する
    big = ImageFont.truetype(LABEL_CONFIG["font"], LABEL_FONT_PX * ss)
    adv = font.getlength(ch)
    ascent, descent = big.getmetrics()
    size = LABEL_CELL * ss
    img = Image.new("L", (size, size), 0)
    x0 = (size - big.getlength(ch)) / 2.0
    y0 = (size - (ascent + descent)) / 2.0
    ImageDraw.Draw(img).text((x0, y0), ch, font=big, fill=255)
    inside = np.asarray(img) >= 128
    d_in = ndimage.distance_transform_edt(inside)
    d_out = ndimage.distance_transform_edt(~inside)
    sd = (d_in - d_out) / ss
    sd = sd.reshape(LABEL_CELL, ss, LABEL_CELL, ss).mean(axis=(1, 3))
    out = np.clip(np.rint(128.0 + sd * (127.0 / LABEL_SPREAD)), 0, 255).astype(np.uint8)
    _glyph_cache[key] = (out, adv)
    return _glyph_cache[key]


_CHOME = re.compile(r"^(.+?)[一二三四五六七八九十〇0-9０-９]+丁目$")


def _unit_m(z, lat):
    """ズーム z でのタイル単位 1 つの長さ (m)。"""
    return 40075016.686 * math.cos(math.radians(lat)) / (1 << z) / EXTENT


def label_candidates(db, z, tiles):
    """ズーム z の全タイルから地名の候補を集める。座標はズーム z 全体でのタイル単位 (タイル x * 4096 + タイル内 x)。"""
    lang = LABEL_CONFIG["lang"]
    cands = []
    for tx, ty in tiles:
        data = read_tile(db, z, tx, ty)
        layer = decode_mvt(data, only=("place",)).get("place") if data else None
        if not layer:
            continue
        scale = EXTENT / float(layer["extent"])
        for feat in layer["features"]:
            if feat["type"] != 1 or not feat["geom"]:
                continue
            props = feat["props"]
            cls = LABEL_CLASSES.get(props.get("class"))
            name = (props.get("name:" + lang) if lang else None) or props.get("name")
            if cls is None or not name:
                continue
            x, y = feat["geom"][0][0][0] * scale, feat["geom"][0][0][1] * scale
            if not (0 <= x < EXTENT and 0 <= y < EXTENT):  # 余白にある点は隣のタイルが持っている
                continue
            rank = props.get("rank", 99)
            cands.append({"pri": cls[1], "rank": rank if isinstance(rank, (int, float)) else 99, "name": name,
                          "x": tx * EXTENT + x, "y": ty * EXTENT + y,
                          "size": cls[0], "margin": cls[2] * LABEL_CONFIG["spacing"]})
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


def merge_labels(cands, unit_m):
    """「永田町一丁目」「永田町二丁目」のような丁目違いを近いものどうし 1 つの町名にまとめ (位置は平均)、
    近くにある同名の地名は優先度の高い 1 つだけ残す。タイルをまたいでも行う。"""
    out = []
    chome = {}
    for c in cands:
        m = _CHOME.match(c["name"])
        if m:
            chome.setdefault(m.group(1), []).append(c)
        else:
            out.append(c)
    for base, members in chome.items():
        for idx in _cluster([(c["x"], c["y"]) for c in members], LABEL_CHOME_MERGE_M / unit_m):
            group = [members[i] for i in idx]
            first = min(group, key=lambda c: (c["pri"], c["rank"]))
            merged = dict(first, name=base)
            merged["x"] = sum(c["x"] for c in group) / len(group)
            merged["y"] = sum(c["y"] for c in group) / len(group)
            out.append(merged)
    out.sort(key=lambda c: (c["pri"], c["rank"], c["name"], c["x"], c["y"]))
    kept = []
    limit = LABEL_DEDUP_M / unit_m
    for c in out:
        if any(k["name"] == c["name"] and math.hypot(k["x"] - c["x"], k["y"] - c["y"]) <= limit for k in kept):
            continue
        kept.append(c)
    return kept


def place_labels(cands):
    """優先度の高いものから置き、重なるものを捨てる。これを倍率 2^(k/8) (k = 0..LABEL_SCALE_STEPS) ごとに行い、
    各ラベルが表示される倍率の範囲 (k の最小と最大が続いている区間) を "kmin" / "kmax" に入れる。
    一度も置けないものは捨てる。文字は 90° 単位で回るので、横向きと縦向きの両方で重ならないことを確かめる。"""
    px = EXTENT / 512.0
    n = len(cands)
    if n == 0:
        return []
    xs = np.array([c["x"] for c in cands])
    ys = np.array([c["y"] for c in cands])
    hw = np.empty(n)
    hh = np.empty(n)
    mg = np.empty(n)
    for i, c in enumerate(cands):
        k = c["size"] / float(LABEL_FONT_PX)
        c["advs"] = [glyph_sdf(ch)[1] * k for ch in c["name"]]
        hw[i] = sum(c["advs"]) / 2 * px
        hh[i] = c["size"] * 0.6 * px
        mg[i] = c["margin"] * px
    steps = LABEL_SCALE_STEPS + 1
    shown = np.zeros((n, steps), dtype=bool)
    for k in range(steps):
        s = 2.0 ** (k / 8.0)
        placed = np.zeros(n, dtype=bool)
        for i in range(n):
            j = placed
            if j.any():
                dx = np.abs(xs[j] - xs[i])
                dy = np.abs(ys[j] - ys[i])
                # 既に置いたラベルの箱 (余白なし) と、自分の箱を余白ぶん広げたもの
                w = (hw[j] + hw[i] + mg[i]) * s
                h = (hh[j] + hh[i] + mg[i]) * s
                if np.any(((dx < w) & (dy < h)) | ((dx < h) & (dy < w))):
                    continue
            placed[i] = True
        shown[:, k] = placed
    out = []
    for i, c in enumerate(cands):
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
        c["kmin"] = 0 if best[0] == 0 else 128 + best[0]
        c["kmax"] = 255 if best[1] == steps - 1 else 128 + best[1]
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
        print(f"z{z}: labels {count} (candidates {len(cands)})", flush=True)
    return plan


def build_labels(planned, z, tx, ty):
    """plan_labels で決めたラベルの頂点を作る。戻り値は (ラベル頂点 (n, 9), アトラス画像 (rows, 2048) または None, ラベル数)。

    ラベル頂点 = 基準点 x, y / ずれ x, y (いずれもタイル単位) / アトラス u, v (px) / 縁取りフラグ /
    表示する倍率の範囲 (kmin, kmax)。プールメッシュに収まらない分は優先度の低いものから捨てる。
    """
    empty = (np.zeros((0, 9)), None, 0)
    if not planned:
        return empty
    px = EXTENT / 512.0
    glyphs = {}
    halo_quads, text_quads = [], []
    count = 0
    corners = [(0, 0), (1, 0), (0, 1), (1, 0), (1, 1), (0, 1)]
    for lab in planned:
        chars = [ch for ch in lab["name"] if not ch.isspace()]
        if len(halo_quads) + len(text_quads) + 12 * len(chars) > LABEL_VERTS_PER_POOL:
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
                                    lab["kmin"], lab["kmax"]))
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


def build_texture(records, style, z, x, y, labels=None, atlas=None):
    """頂点レコードとスタイルから (H, 2048, 3) の uint8 配列 (論理行 0 が先頭) を作る。"""
    n = len(records)
    data_rows = (2 * n + TEX_W - 1) // TEX_W
    nl = 0 if labels is None or atlas is None else len(labels)
    label_row = DATA_ROW + data_rows
    label_rows = (LABEL_STRIDE * nl + TEX_W - 1) // TEX_W
    atlas_row = label_row + label_rows
    atlas_rows = atlas.shape[0] if nl else 0
    h = atlas_row + atlas_rows + 1
    if h > MAX_H:
        raise ValueError(f"三角形が多すぎます ({n // 3})。高さ {h} > {MAX_H}")
    tex = np.zeros((h, TEX_W, 3), dtype=np.uint8)
    # ヘッダ
    tex[0, 0] = MARK_TOP
    tex[0, 1] = (FORMAT_VERSION, 0, 0)
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
        tex[label_row:label_row + label_rows] = flat.reshape(label_rows, TEX_W, 3)
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
    labels, atlas, count = build_labels(planned, z, x, y)
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
    args = ap.parse_args(argv)

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
    LABEL_CONFIG.update(font=args.font, lang=args.lang, enabled=not args.no_labels, spacing=args.label_spacing)
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

    # ラベルはズームごとに全タイルまとめて配置する (タイルをまたぐ重なりと重複を除くため)
    plan = plan_labels(db, levels, lat)
    LABEL_CONFIG["font"] = LABEL_CONFIG["font"] or next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    ctx = (args.mbtiles, args.out, style, dict(LABEL_CONFIG), args.no_bin, args.dry_run, plan)
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
                        "spacing": LABEL_CONFIG["spacing"]},
        "levels": levels, "tiles": tiles,
        "styles": [r["name"] for r in style],
        "attribution": source_attribution or "© OpenStreetMap contributors",
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"{len(tiles)} tiles -> {args.out}")


if __name__ == "__main__":
    main()
