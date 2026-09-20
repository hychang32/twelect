"""村里界圖 TopoJSON 的解碼、投影與 SVG 路徑輸出。

邊界來源：taiwan-atlas (https://www.npmjs.com/package/taiwan-atlas)，
內政部國土測繪中心村里界圖的 TopoJSON 版本，同一份檔案裡就含有
villages / towns / counties / nation 四層，共用同一組 arc。

金門、連江、澎湖離本島太遠，直接照經緯度畫會讓整張圖有一半是海，
所以這三縣獨立縮放成左側的小圖 (inset)。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .net import fetch

ATLAS_URL = "https://cdn.jsdelivr.net/npm/taiwan-atlas@2021.9.20/villages-10t.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ATLAS_FILE = DATA_DIR / "villages-10t.json"

# 獨立成小圖 (inset) 的離島群組。
# 金門的烏坵鄉、連江的東引鄉離本群還有 100 公里以上，併在同一個框裡會讓
# 金門本島小到看不見，所以各自獨立一格；weight 是小圖欄裡分配高度的權重。
INSET_GROUPS = [
    {"key": "matsu", "label": "連江縣", "towns": ("09007010", "09007020", "09007030"), "weight": 1.0},
    {"key": "dongyin", "label": "連江縣東引鄉", "towns": ("09007040",), "weight": 0.42},
    {"key": "kinmen", "label": "金門縣", "towns": ("09020010", "09020020", "09020030", "09020040", "09020050"), "weight": 1.35},
    {"key": "wuqiu", "label": "金門縣烏坵鄉", "towns": ("09020060",), "weight": 0.32},
    {"key": "penghu", "label": "澎湖縣", "towns": ("10016010", "10016020", "10016030", "10016040", "10016050", "10016060"), "weight": 1.7},
]

TOWN_TO_INSET = {t: g["key"] for g in INSET_GROUPS for t in g["towns"]}

CANVAS_H = 1400.0      # 主圖高度（SVG 單位）
INSET_W = 290.0        # 小圖欄寬
INSET_GAP = 18.0
PAD = 16.0
MAX_INSET_ZOOM = 4.0   # 小圖相對主圖的最大放大倍率


# --------------------------------------------------------------------------
# TopoJSON
# --------------------------------------------------------------------------

def download_atlas(refresh: bool = False) -> Path:
    if ATLAS_FILE.exists() and not refresh:
        return ATLAS_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ATLAS_FILE.write_bytes(fetch(ATLAS_URL, timeout=180))
    return ATLAS_FILE


def load_topology(refresh: bool = False) -> dict:
    return json.loads(download_atlas(refresh).read_text("utf-8"))


def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    """把量化 + delta 編碼的 arc 還原成經緯度座標串。"""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def _ring(arcs, idxs) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in idxs:
        seg = arcs[~i][::-1] if i < 0 else arcs[i]
        pts.extend(seg[1:] if pts else seg)
    return pts


def features(topo: dict, arcs, name: str) -> list[tuple[dict, list[list[list]]]]:
    """回傳 [(properties, [polygon, ...])]，polygon = [外環, 內環...]。"""
    out = []
    for geom in topo["objects"][name]["geometries"]:
        kind = geom.get("type")
        if kind == "Polygon":
            polys = [[_ring(arcs, r) for r in geom["arcs"]]]
        elif kind == "MultiPolygon":
            polys = [[_ring(arcs, r) for r in poly] for poly in geom["arcs"]]
        else:
            continue
        out.append((geom.get("properties", {}), polys))
    return out


# --------------------------------------------------------------------------
# 投影與版面
# --------------------------------------------------------------------------

def mercator(lon: float, lat: float) -> tuple[float, float]:
    x = math.radians(lon)
    y = -math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


@dataclass
class Transform:
    scale: float
    dx: float
    dy: float

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = mercator(lon, lat)
        return x * self.scale + self.dx, y * self.scale + self.dy


def _bbox(polys_by_group) -> tuple[float, float, float, float]:
    mnx = mny = math.inf
    mxx = mxy = -math.inf
    for polys in polys_by_group:
        for poly in polys:
            for ring in poly:
                for lon, lat in ring:
                    x, y = mercator(lon, lat)
                    mnx = min(mnx, x); mxx = max(mxx, x)
                    mny = min(mny, y); mxy = max(mxy, y)
    return mnx, mny, mxx, mxy


@dataclass
class Layout:
    width: float
    height: float
    main: Transform
    insets: dict[str, Transform]
    boxes: list[dict]

    def transform_for(self, town_code: str) -> Transform:
        """依鄉鎮市區代碼挑投影；離島小圖以外一律用主圖。"""
        key = TOWN_TO_INSET.get((town_code or "")[:8])
        return self.insets.get(key, self.main) if key else self.main


def build_layout(village_features) -> Layout:
    """算出主圖 + 離島小圖的版面配置。"""
    groups: dict[str, list] = {"main": []}
    for props, polys in village_features:
        key = TOWN_TO_INSET.get(props.get("TOWNCODE", ""), "main")
        groups.setdefault(key, []).append(polys)

    mnx, mny, mxx, mxy = _bbox(groups["main"])
    main_h = CANVAS_H - 2 * PAD
    scale = main_h / (mxy - mny)
    main_w = (mxx - mnx) * scale

    present = [g for g in INSET_GROUPS if g["key"] in groups]
    left = (INSET_W + INSET_GAP) if present else 0.0
    width = left + main_w + 2 * PAD
    height = CANVAS_H
    main = Transform(scale, PAD + left - mnx * scale, PAD - mny * scale)

    insets: dict[str, Transform] = {}
    boxes: list[dict] = []
    if present:
        avail = height - 2 * PAD - INSET_GAP * (len(present) - 1)
        total_w = sum(g["weight"] for g in present)
        y = PAD
        for g in present:
            box_h = avail * g["weight"] / total_w
            pad_in = 12.0
            label_h = 20.0
            w = INSET_W - 2 * pad_in
            h = box_h - 2 * pad_in - label_h
            a, b, c, d = _bbox(groups[g["key"]])
            span_x = max(c - a, 1e-9)
            span_y = max(d - b, 1e-9)
            s = min(w / span_x, h / span_y, scale * MAX_INSET_ZOOM)
            dx = PAD + INSET_W / 2 - (a + c) / 2 * s
            dy = y + pad_in + label_h + (h - span_y * s) / 2 - b * s
            insets[g["key"]] = Transform(s, dx, dy)
            boxes.append(
                {
                    "key": g["key"],
                    "label": g["label"],
                    "x": round(PAD, 1),
                    "y": round(y, 1),
                    "w": round(INSET_W, 1),
                    "h": round(box_h, 1),
                    "zoom": round(s / scale, 2),
                }
            )
            y += box_h + INSET_GAP
    return Layout(round(width, 1), round(height, 1), main, insets, boxes)


# --------------------------------------------------------------------------
# SVG 路徑
# --------------------------------------------------------------------------

def _perp(pts, i, j, tol):
    """Douglas-Peucker 的遞迴部分，回傳要保留的索引集合。"""
    keep = {i, j}
    stack = [(i, j)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for k in range(a + 1, b):
            px, py = pts[k]
            if norm < 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, bi = d, k
        if best > tol:
            keep.add(bi)
            stack.append((a, bi))
            stack.append((bi, b))
    return keep


def _simplify(pts, tol: float):
    """封閉環的 Douglas-Peucker。

    直接對首尾相同的環跑 DP，基線長度為 0，所有點的距離都算成 0，
    整個環會被削成兩個點。先從起點找出最遠的點把環切成兩段再各自簡化。
    """
    if len(pts) < 4 or tol <= 0:
        return pts
    closed = pts[0] == pts[-1]
    ring = pts[:-1] if closed else pts
    if not closed:
        keep = _perp(ring, 0, len(ring) - 1, tol)
        return [p for i, p in enumerate(ring) if i in keep]
    ax, ay = ring[0]
    far = max(range(1, len(ring)), key=lambda k: (ring[k][0] - ax) ** 2 + (ring[k][1] - ay) ** 2)
    keep = _perp(ring, 0, far, tol) | _perp(ring, far, len(ring) - 1, tol)
    keep.add(len(ring) - 1)
    out = [p for i, p in enumerate(ring) if i in keep]
    return out + [out[0]]


def path_bbox(polys, tf: Transform) -> tuple[float, float, float, float]:
    """多邊形投影後的外接矩形（SVG 座標）。"""
    mnx = mny = math.inf
    mxx = mxy = -math.inf
    for poly in polys:
        for ring in poly:
            for lon, lat in ring:
                x, y = tf(lon, lat)
                mnx = min(mnx, x); mxx = max(mxx, x)
                mny = min(mny, y); mxy = max(mxy, y)
    return mnx, mny, mxx, mxy


def to_path(polys, tf: Transform, *, precision: int = 1, tol: float = 0.0) -> str:
    """把多邊形投影成 SVG path 字串。"""
    out = []
    for poly in polys:
        for ring in poly:
            pts = [tf(lon, lat) for lon, lat in ring]
            if tol:
                pts = _simplify(pts, tol)
            seen: list[tuple[float, float]] = []
            for x, y in pts:
                p = (round(x, precision), round(y, precision))
                if not seen or p != seen[-1]:
                    seen.append(p)
            if len(seen) < 3:
                continue
            if seen[0] == seen[-1]:
                seen.pop()
            if len(seen) < 3:
                continue
            fmt = f"%.{precision}f"
            out.append(
                "M"
                + "L".join(f"{fmt % x} {fmt % y}" for x, y in seen)
                + "Z"
            )
    return "".join(out)
