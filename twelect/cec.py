"""中央選舉委員會選舉資料庫 (db.cec.gov.tw) 用戶端。

網站本身是 Nuxt SPA，所有資料都放在 /static/ 下的靜態 JSON，
路徑規則如下：

    /static/elections/configs/ELC_subjects.json          選舉種類
    /static/elections/list/ELC_{subject}.json            各屆次列表
    /static/elections/data/tickets/ELC/{subject}/{legis}/{theme}/{level}/{area}.json
    /static/webs/configs/party_colors.json               政黨顏色

其中 level 為資料層級：
    N 全國 / C 縣市 / D 鄉鎮市區 / L 村里 / T 投開票所

area 檔名為 `{prv}_{city}_{area}_{dept}_{li}.json`，五段代碼分別是
省市別、縣市別、選區、鄉鎮市區、村里。要取某縣市底下的全部村里，
使用 `{prv}_{city}_00_000_0000` 並指定 level=L。
"""

from __future__ import annotations

import json
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .net import fetch

BASE = "https://db.cec.gov.tw"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
LEVELS = {"N": "全國", "C": "縣市", "D": "鄉鎮市區", "L": "村里", "T": "投開票所"}

# 全國 22 個縣市（prv_code + city_code）。鄉鎮市長之類沒有全國彙總檔的選舉，
# 只能逐縣市試抓，抓不到的（例如直轄市沒有鄉鎮市長）就跳過。
ALL_COUNTIES = [
    ("63000", "臺北市"), ("64000", "高雄市"), ("65000", "新北市"),
    ("66000", "臺中市"), ("67000", "臺南市"), ("68000", "桃園市"),
    ("09007", "連江縣"), ("09020", "金門縣"),
    ("10002", "宜蘭縣"), ("10004", "新竹縣"), ("10005", "苗栗縣"),
    ("10007", "彰化縣"), ("10008", "南投縣"), ("10009", "雲林縣"),
    ("10010", "嘉義縣"), ("10013", "屏東縣"), ("10014", "臺東縣"),
    ("10015", "花蓮縣"), ("10016", "澎湖縣"), ("10017", "基隆市"),
    ("10018", "新竹市"), ("10020", "嘉義市"),
]


class CECError(RuntimeError):
    pass


def _cache_path(path: str) -> Path:
    return CACHE_DIR / path.lstrip("/")


def get_json(path: str, *, refresh: bool = False, retries: int = 3):
    """抓取 CEC 的靜態 JSON，並快取到 cache/ 下的同名路徑。"""
    cached = _cache_path(path)
    if cached.exists() and not refresh:
        return json.loads(cached.read_text("utf-8"))

    url = BASE + path
    last = None
    for attempt in range(retries):
        try:
            raw = fetch(url).decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise CECError(f"404 Not Found: {url}") from exc
            last = exc
        except Exception as exc:  # noqa: BLE001 - 網路錯誤一律重試
            last = exc
        time.sleep(1.5 * (attempt + 1))
    else:
        raise CECError(f"下載失敗 {url}: {last}")

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(raw, "utf-8")
    return json.loads(raw)


# --------------------------------------------------------------------------
# 選舉清單
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Election:
    """一場選舉（CEC 稱為 theme）。"""

    subject_id: str        # C1 / C2 / P0 / L0 ...
    subject_name: str      # 直轄市長 / 縣市長 ...
    legis_id: str          # 立委等次類別，其餘為 "00"
    theme_id: str
    name: str              # 111年直轄市長選舉
    vote_date: str         # 2022-11-26
    levels: tuple = field(default=())   # 可用的資料層級
    legis_name: str = ""                # 區域／平地原住民／山地原住民／不分區政黨

    @property
    def label(self) -> str:
        return f"{self.name}（{self.legis_name}）" if self.legis_name else self.name

    @property
    def year(self) -> int:
        return int(self.vote_date[:4])

    def ticket_path(self, level: str, area: str) -> str:
        return (
            f"/static/elections/data/tickets/ELC/{self.subject_id}/"
            f"{self.legis_id}/{self.theme_id}/{level}/{area}.json"
        )


def list_subjects(refresh: bool = False) -> list[dict]:
    return get_json("/static/elections/configs/ELC_subjects.json", refresh=refresh)


def list_elections(subject_id: str, refresh: bool = False) -> list[Election]:
    groups = get_json(f"/static/elections/list/ELC_{subject_id}.json", refresh=refresh)
    subject = next(
        (s for s in list_subjects() if s["subject_id"] == subject_id), None
    )
    subject_name = subject["subject_name"] if subject else subject_id
    legis_names = {
        t["type_id"]: t["type_name"] for t in (subject or {}).get("legislator_types", [])
    }
    out, seen = [], set()
    for group in groups:
        for item in group.get("theme_items", []):
            if not item.get("has_data") or item["theme_id"] in seen:
                continue
            seen.add(item["theme_id"])
            out.append(
                Election(
                    subject_id=item["subject_id"],
                    subject_name=subject_name,
                    legis_id=item.get("legislator_type_id") or "00",
                    theme_id=item["theme_id"],
                    name=item["theme_name"],
                    vote_date=item["vote_date"],
                    levels=tuple(item.get("data_tckt_seq") or ()),
                    legis_name=legis_names.get(item.get("legislator_type_id") or "", ""),
                )
            )
    out.sort(key=lambda e: (e.vote_date, e.name), reverse=True)
    return out


# --------------------------------------------------------------------------
# 得票資料
# --------------------------------------------------------------------------

def _rows(payload: dict) -> list[dict]:
    return [row for bucket in payload.values() for row in bucket]


def counties(election: Election, refresh: bool = False) -> list[tuple[str, str]]:
    """回傳這場選舉涵蓋的縣市 [(代碼, 名稱)]，代碼為 5 碼（prv+city）。

    多數選舉有全國彙總的縣市層級檔可以一次拿到清單；鄉鎮市長、山地原住民
    區長這類沒有全國彙總的，就逐縣市試抓村里檔，404 表示該縣市沒有這場選舉。
    """
    try:
        payload = get_json(
            election.ticket_path("C", "00_000_00_000_0000"), refresh=refresh
        )
    except CECError:
        pass
    else:
        found: dict[str, str] = {}
        for row in _rows(payload):
            found.setdefault(row["prv_code"] + row["city_code"], row["area_name"])
        if found:
            return sorted(found.items())

    def probe(item):
        code, name = item
        try:
            get_json(_village_path(election, code), refresh=refresh)
        except CECError:
            return None
        return (code, name)

    with ThreadPoolExecutor(max_workers=6) as pool:
        hits = [x for x in pool.map(probe, ALL_COUNTIES) if x]
    return sorted(hits)


def _village_path(election: Election, county_code: str) -> str:
    return election.ticket_path("L", f"{county_code[:2]}_{county_code[2:]}_00_000_0000")


def village_rows(
    election: Election, refresh: bool = False, workers: int = 6, log=None
) -> list[dict]:
    """抓取整場選舉的村里層級得票（每個縣市一個 JSON 檔）。"""
    if "L" not in election.levels:
        raise CECError(f"{election.name} 沒有村里層級資料（可用：{election.levels}）")

    targets = counties(election, refresh=refresh)

    def one(item):
        code, name = item
        data = get_json(_village_path(election, code), refresh=refresh)
        if log:
            log(f"  {name} ({code})")
        return _rows(data)

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(one, targets):
            out.extend(rows)
    return out


def party_colors(refresh: bool = False) -> dict[int, str]:
    data = get_json("/static/webs/configs/party_colors.json", refresh=refresh)
    return {int(p["party_code"]): "#" + p["color_code"] for p in data}
