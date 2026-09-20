"""把資料包塞進 HTML 樣板，產生單檔、可離線開啟的互動地圖。"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "template.html"


def render(payload: dict, out: Path) -> Path:
    html = TEMPLATE.read_text("utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 避免字串裡的 </script> 提前結束 script 區塊
    data = data.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = html.replace("__TITLE__", payload["meta"]["title"]).replace("__DATA__", data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, "utf-8")
    return out
