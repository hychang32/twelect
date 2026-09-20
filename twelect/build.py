"""把中選會得票資料與村里界圖組裝成前端要用的資料包。

兩份資料的顆粒度不完全一致，中間隔了一層「投開票單位 (unit)」：

* 連江縣的開票是以數個村合併計票的（代碼像 09007010A01，區名寫成
  「復興村、福沃村」），一個單位對應多個村里；
* 界圖是 2021/09 版，2022 選舉前後有些縣市調整了里界（臺東市、桃園、
  新北等），代碼對不上時改以「同一鄉鎮內的村里名稱」比對。

票數掛在 unit 上、幾何掛在 village 上，村里只記錄自己屬於哪個 unit，
這樣合併計票的村里在地圖上同色，往上加總時也不會被重複計算。
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass

from . import cec, geo, palette

# 縣市的慣用排序（北到南，離島殿後），選區下拉選單照這個順序排
COUNTY_ORDER = [
    "63000", "65000", "10017", "68000", "10018", "10004", "10005", "66000",
    "10007", "10008", "10009", "10020", "10010", "67000", "64000", "10013",
    "10002", "10015", "10014", "10016", "09020", "09007",
]

# 兩份資料的用字差異
_NORM = str.maketrans({"台": "臺", "豊": "豐", "舘": "館", "峯": "峰", "羗": "羌"})


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name).translate(_NORM)


@dataclass
class BuildResult:
    payload: dict
    warnings: list[str]


def _unit_code(row: dict) -> str:
    """CEC 五段代碼中，能唯一識別一個投開票單位的部分。"""
    return f"{row['prv_code']}{row['city_code']}{row['dept_code']}{row['li_code']}"


def _village_code(row: dict) -> str:
    """推測對應的國土測繪 11 碼村里代碼（合併計票的單位推不出來）。"""
    return f"{row['prv_code']}{row['city_code']}{row['dept_code']}{row['li_code'][1:]}"


def collect_units(elections: list[cec.Election], *, refresh=False, log=None):
    """依投票日先後抓資料；同一縣市若有重行選舉，以較晚的那場為準。

    總統副總統選舉裡，中選會把正、副總統各記成一列且票數相同，
    照單全收會讓每張票算兩次，所以只留正總統那列，副手併進候選人名字。
    """
    units: dict[str, dict] = {}
    mates: dict[tuple[str, int], str] = {}
    for election in sorted(elections, key=lambda e: e.vote_date):
        if log:
            log(f"抓取 {election.name}（{election.vote_date}）村里資料…")
        rows = cec.village_rows(election, refresh=refresh, log=log)
        touched = {r["prv_code"] + r["city_code"] for r in rows}
        for code in [c for c in units if c[:5] in touched]:
            units.pop(code)
        for row in rows:
            if (row.get("is_vice") or " ").strip() == "Y":
                mates.setdefault((election.theme_id, row["cand_no"]), row["cand_name"])
                continue
            row = dict(row, _theme=election.theme_id)
            unit = units.setdefault(
                _unit_code(row),
                {
                    "name": row["area_name"],
                    "county": row["prv_code"] + row["city_code"],
                    "town": row["prv_code"] + row["city_code"] + row["dept_code"],
                    "area": row["area_code"],
                    "guess": _village_code(row),
                    "rows": {},
                },
            )
            unit["rows"][row["cand_id"]] = row
    return units, mates


def match_units(units: dict[str, dict], village_props: list[dict]):
    """把投開票單位對到村里界圖上的一個或多個村里。"""
    by_code = {p["VILLCODE"]: p for p in village_props}
    by_name: dict[tuple[str, str], str] = {}
    for p in village_props:
        by_name[(p["TOWNCODE"], _norm(p["VILLNAME"]))] = p["VILLCODE"]

    mapping: dict[str, list[str]] = {}
    unmatched: list[tuple[str, str]] = []
    for code, unit in units.items():
        if unit["guess"] in by_code:
            mapping[code] = [unit["guess"]]
            continue
        hits = []
        for part in re.split(r"[、,，]", unit["name"]):
            part = _norm(part)
            if not part:
                continue
            hit = by_name.get((unit["town"], part))
            # 村／里 用字不同時再試一次
            if hit is None and part[-1:] in "村里":
                other = part[:-1] + ("里" if part[-1] == "村" else "村")
                hit = by_name.get((unit["town"], other))
            if hit:
                hits.append(hit)
        if hits:
            mapping[code] = hits
        else:
            unmatched.append((code, unit["name"]))
    return mapping, unmatched


def build_payload(
    elections: list[cec.Election],
    *,
    title: str | None = None,
    refresh: bool = False,
    log=None,
) -> BuildResult:
    warnings: list[str] = []
    units, mates = collect_units(elections, refresh=refresh, log=log)

    if log:
        log("載入村里界圖…")
    topo = geo.load_topology()
    arcs = geo.decode_arcs(topo)
    village_feats = geo.features(topo, arcs, "villages")
    town_feats = geo.features(topo, arcs, "towns")
    county_feats = geo.features(topo, arcs, "counties")
    layout = geo.build_layout(village_feats)

    mapping, unmatched = match_units(units, [p for p, _ in village_feats])

    # ---- 政黨 / 候選人索引 ----
    cec_colors = cec.party_colors(refresh=refresh)
    parties: dict[int, int] = {}
    party_list: list[dict] = []
    cands: dict[int, int] = {}
    cand_list: list[dict] = []
    cand_totals: list[int] = []

    def party_idx(code: int, name: str) -> int:
        if code not in parties:
            parties[code] = len(party_list)
            party_list.append(
                {
                    "code": code,
                    "name": name,
                    "color": palette.resolve(code, cec_colors),
                    "ind": palette.is_independent(code),
                }
            )
        return parties[code]

    def cand_idx(row: dict) -> int:
        cid = row["cand_id"]
        if cid not in cands:
            mate = mates.get((row.get("_theme"), row["cand_no"]))
            cands[cid] = len(cand_list)
            cand_list.append(
                {
                    "name": row["cand_name"] + (f"・{mate}" if mate else ""),
                    "p": party_idx(int(row["party_code"]), row["party_name"]),
                    "no": row["cand_no"],
                }
            )
            cand_totals.append(0)
        return cands[cid]

    # ---- 行政區索引 ----
    county_names: dict[str, int] = {}
    counties: list[dict] = []
    town_names: dict[str, int] = {}
    towns: list[dict] = []
    for props, _ in village_feats:
        cc, tc = props["COUNTYCODE"], props["TOWNCODE"]
        if cc not in county_names:
            county_names[cc] = len(counties)
            counties.append({"code": cc, "name": props["COUNTYNAME"]})
        if tc not in town_names:
            town_names[tc] = len(towns)
            towns.append({"code": tc, "name": props["TOWNNAME"], "c": county_names[cc]})

    # ---- 投開票單位 ----
    unit_list: list[dict] = []
    unit_meta: list[dict] = []
    village_unit: dict[str, int] = {}
    for code, unit in units.items():
        targets = mapping.get(code) or []
        votes = []
        for row in sorted(unit["rows"].values(), key=lambda r: r["cand_no"]):
            idx = cand_idx(row)
            votes.append([idx, row["ticket_num"]])
            cand_totals[idx] += row["ticket_num"]
        ui = len(unit_list)
        unit_list.append(
            {
                "n": unit["name"],
                "t": town_names.get(unit["town"], -1),
                "s": votes,
                "m": len(targets),          # 合併計票的村里數，0 表示畫不出來
            }
        )
        unit_meta.append(unit)
        for villcode in targets:
            village_unit[villcode] = ui

    # ---- 選區 ----
    # 一位候選人只會登記在自己的選區，所以「共用候選人」的投開票單位必定同屬
    # 一個選區。用聯集尋找把單位串起來，縣市長、鄉鎮市長、區域立委、議員
    # 都不必個別寫規則就能自動推得。
    parent = list(range(len(unit_list)))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    seen_cand: dict[int, int] = {}
    for ui, u in enumerate(unit_list):
        for ci, _ in u["s"]:
            if ci in seen_cand:
                _union(ui, seen_cand[ci])
            else:
                seen_cand[ci] = ui

    groups: dict[int, list[int]] = {}
    for ui, u in enumerate(unit_list):
        if u["s"]:
            groups.setdefault(_find(ui), []).append(ui)

    def _district_name(members: list[int]) -> str:
        cs = {unit_meta[i]["county"] for i in members}
        if len(cs) != 1:
            return "全國"
        code = next(iter(cs))
        cname = counties[county_names[code]]["name"] if code in county_names else code
        areas = {unit_meta[i]["area"] for i in members} - {"00"}
        if areas:
            return f"{cname}第{int(sorted(areas)[0])}選區"
        tws = {unit_meta[i]["town"] for i in members}
        if len(tws) == 1:
            tw = next(iter(tws))
            tname = towns[town_names[tw]]["name"] if tw in town_names else ""
            if len(members) == 1:
                return f"{cname}{tname}{unit_list[members[0]]['n']}"
            return f"{cname}{tname}"
        return cname

    raw_districts = []
    for members in groups.values():
        raw_districts.append(
            {
                "name": _district_name(members),
                "county": unit_meta[members[0]]["county"],
                "area": sorted({unit_meta[i]["area"] for i in members})[0],
                "members": members,
            }
        )
    order = {c: i for i, c in enumerate(COUNTY_ORDER)}
    raw_districts.sort(key=lambda d: (order.get(d["county"], 99), d["area"], d["name"]))

    districts: list[dict] = []
    for di, d in enumerate(raw_districts):
        for ui in d["members"]:
            unit_list[ui]["k"] = di
        districts.append(
            {
                "n": d["name"],
                "c": county_names.get(d["county"], -1),
                "u": len(d["members"]),
                "b": [math.inf, math.inf, -math.inf, -math.inf],
            }
        )
    for u in unit_list:
        u.setdefault("k", -1)

    # ---- 村里幾何（順便累積各選區的外接矩形，給聚焦縮放用）----
    villages = []
    for props, polys in village_feats:
        code = props["VILLCODE"]
        tf = layout.transform_for(props["TOWNCODE"])
        ui = village_unit.get(code, -1)
        if ui >= 0 and unit_list[ui]["k"] >= 0:
            bb = districts[unit_list[ui]["k"]]["b"]
            x0, y0, x1, y1 = geo.path_bbox(polys, tf)
            bb[0] = min(bb[0], x0); bb[1] = min(bb[1], y0)
            bb[2] = max(bb[2], x1); bb[3] = max(bb[3], y1)
        villages.append(
            {
                "v": code,
                "n": props["VILLNAME"],
                "t": town_names[props["TOWNCODE"]],
                "d": geo.to_path(polys, tf),
                "u": ui,
            }
        )

    for d in districts:
        if all(map(math.isfinite, d["b"])):
            d["b"] = [round(v, 1) for v in d["b"]]
        else:                       # 選區完全沒對到界線時，退回整張畫布
            d["b"] = [0.0, 0.0, layout.width, layout.height]

    orphan = [v["v"] for v in villages if v["u"] < 0]
    if orphan:
        names = [
            f"{counties[towns[v['t']]['c']]['name']}{towns[v['t']]['name']}{v['n']}"
            for v in villages if v["u"] < 0
        ][:4]
        warnings.append(
            f"{len(orphan)} 個村里在界圖上有、但這場選舉查無得票（{'、'.join(names)} 等）；"
            "界圖為 2021 年版，選後若有里界調整就會對不上，地圖以斜線標示。"
        )
    if unmatched:
        warnings.append(
            f"{len(unmatched)} 個投開票單位找不到對應的村里界（{'、'.join(n for _, n in unmatched[:4])} 等），"
            "其票數仍計入鄉鎮市區以上的合計，但地圖上畫不出來。"
        )

    # ---- 界線疊圖（索引對齊 towns / counties，前端也拿來當 hover 外框）----
    town_borders = [""] * len(towns)
    for props, polys in town_feats:
        idx = town_names.get(props["TOWNCODE"])
        if idx is not None:
            town_borders[idx] = geo.to_path(
                polys, layout.transform_for(props["TOWNCODE"]), tol=0.45
            )

    towns_by_county: dict[str, list] = {}
    for props, polys in town_feats:
        towns_by_county.setdefault(props["COUNTYCODE"], []).append((props, polys))

    county_borders = [""] * len(counties)
    for props, polys in county_feats:
        code = props["COUNTYCODE"]
        idx = county_names.get(code)
        if idx is None:
            continue
        members = towns_by_county.get(code, [])
        keys = {geo.TOWN_TO_INSET.get(p["TOWNCODE"]) for p, _ in members}
        if len(keys) <= 1:
            tc = members[0][0]["TOWNCODE"] if members else ""
            county_borders[idx] = geo.to_path(polys, layout.transform_for(tc), tol=0.45)
        else:
            # 連江、金門橫跨兩個離島小圖，縣界改用其轄下鄉鎮的外框拼起來
            county_borders[idx] = "".join(
                geo.to_path(pg, layout.transform_for(p["TOWNCODE"]), tol=0.45)
                for p, pg in members
            )

    ordered = sorted(elections, key=lambda e: e.vote_date)
    payload = {
        "meta": {
            "title": title or " + ".join(e.name for e in ordered),
            "elections": [
                {"name": e.name, "date": e.vote_date, "subject": e.subject_name}
                for e in ordered
            ],
            "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "中央選舉委員會選舉資料庫 db.cec.gov.tw／內政部國土測繪中心村里界圖 2021.09（taiwan-atlas）",
            "warnings": warnings,
        },
        "layout": {"w": layout.width, "h": layout.height, "boxes": layout.boxes},
        "parties": party_list,
        "cands": [dict(c, total=cand_totals[i]) for i, c in enumerate(cand_list)],
        "counties": counties,
        "towns": towns,
        "districts": districts,
        "units": unit_list,
        "villages": villages,
        "borders": {"county": county_borders, "town": town_borders},
    }
    return BuildResult(payload, warnings)
