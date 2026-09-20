"""產生 GitHub Pages 用的索引頁。

掃描 docs/ 底下的地圖 HTML，從每個檔案抽出標題與選舉資訊，列成一頁。
地圖是自包含的單檔，Pages 只要把它們當靜態檔送出去就好。
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import re
from pathlib import Path

MARKER = '<script id="payload" type="application/json">'
REPO_URL = "https://github.com/hychang32/twelect"


def read_meta(path: Path) -> dict:
    """只讀檔頭需要的部分，不要為了幾個欄位把 4MB 全載進來。"""
    with path.open("r", encoding="utf-8") as fh:
        head = fh.read(400_000)
    title = ""
    m = re.search(r"<title>(.*?)</title>", head, re.S)
    if m:
        title = html.unescape(m.group(1)).strip()
    meta = {}
    i = head.find(MARKER)
    if i >= 0:
        body = head[i + len(MARKER):]
        j = body.find(',"layout":')
        if j > 0:
            try:
                meta = json.loads(body[:j] + "}").get("meta", {})
            except json.JSONDecodeError:
                meta = {}
    return {
        "file": path.name,
        "key": path.stem,
        "title": title or path.stem,
        "elections": meta.get("elections", []),
        "generated": meta.get("generated", ""),
        "bytes": path.stat().st_size,
    }


def _card(item: dict) -> str:
    dates = "、".join(
        f"{html.escape(e['name'])}（{e['date']}）" for e in item["elections"]
    ) or "—"
    return f"""      <li>
        <a class="card" href="{html.escape(item['file'])}">
          <span class="key">{html.escape(item['key'])}</span>
          <span class="name">{html.escape(item['title'])}</span>
          <span class="meta">{dates}</span>
          <span class="meta dim">{item['bytes'] / 1e6:.1f} MB · 產生於 {html.escape(item['generated'])}</span>
        </a>
      </li>"""


def build_index(docs: Path) -> Path:
    items = [
        read_meta(p)
        for p in sorted(docs.glob("*.html"))
        if p.name != "index.html"
    ]
    items.sort(
        key=lambda it: (it["elections"][0]["date"] if it["elections"] else ""),
        reverse=True,
    )
    cards = "\n".join(_card(it) for it in items) or "      <li class=\"empty\">還沒有產生任何地圖。</li>"
    page = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台灣選舉結果村里地圖</title>
<style>
:root{{
  --bg:#f7f6f3; --surface:#fff; --ink:#1a1c1f; --ink2:#565c64; --ink3:#878d95;
  --line:#e4e2dd; --line2:#cbc8c2; --accent:#1f5fd0;
}}
@media (prefers-color-scheme:dark){{
  :root{{ --bg:#111315; --surface:#191c1f; --ink:#e9eaec; --ink2:#a6acb3; --ink3:#797f87;
         --line:#292d32; --line2:#3a3f46; --accent:#7aa5f5; }}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;
  -webkit-font-smoothing:antialiased;}}
.wrap{{max-width:760px;margin:0 auto;padding:48px 20px 64px}}
h1{{font-size:26px;line-height:1.3;margin:0 0 8px;letter-spacing:-.01em}}
.lede{{margin:0 0 28px;color:var(--ink2);font-size:14.5px}}
h2{{font-size:13px;color:var(--ink3);font-weight:600;margin:32px 0 10px;letter-spacing:.02em}}
ul{{list-style:none;margin:0;padding:0;display:grid;gap:10px}}
.card{{display:grid;gap:2px;padding:14px 16px;border:1px solid var(--line);border-radius:12px;
  background:var(--surface);text-decoration:none;color:inherit;transition:border-color .12s}}
.card:hover{{border-color:var(--line2)}}
.key{{font-size:11.5px;color:var(--ink3);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
.name{{font-size:16px;font-weight:650;letter-spacing:-.01em}}
.meta{{font-size:12.5px;color:var(--ink2)}}
.meta.dim{{color:var(--ink3);font-size:11.5px}}
.empty{{color:var(--ink3);font-size:14px}}
pre{{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;overflow-x:auto;font-size:12.5px;line-height:1.6}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
footer{{margin-top:36px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--ink3);line-height:1.8}}
a{{color:var(--accent)}}
</style>
</head>
<body>
<div class="wrap">
  <h1>台灣選舉結果村里地圖</h1>
  <p class="lede">把中央選舉委員會的得票資料抓到村里層級，疊上內政部村里界圖，
  依各村里最高票候選人的政黨上色，得票率越高顏色越深。每張圖都是單一 HTML 檔，
  含選區聚焦、行政層級切換與表格檢視。</p>

  <h2>地圖</h2>
  <ul>
{cards}
  </ul>

  <h2>自己產生其他場次</h2>
  <pre><code>git clone {REPO_URL}.git
cd twelect
python3 -m twelect list --year 2020
python3 -m twelect build 2020-P0 --open</code></pre>

  <footer>
    資料來源：中央選舉委員會選舉資料庫 db.cec.gov.tw ／
    內政部國土測繪中心村里界圖 2021.09（taiwan-atlas）。<br>
    原始碼：<a href="{REPO_URL}">{REPO_URL}</a>　·　
    索引頁更新於 {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
  </footer>
</div>
</body>
</html>
"""
    out = docs / "index.html"
    out.write_text(page, "utf-8")
    return out
