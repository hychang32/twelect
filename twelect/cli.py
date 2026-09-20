"""命令列介面。

    python3 -m twelect list                 列出可選的選舉
    python3 -m twelect list --year 2022     只看某一年
    python3 -m twelect build 2022-MAYOR     抓資料並產生地圖
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import build as build_mod
from . import cec, render, site

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# 把性質相同、同年舉行的選舉併成一張圖。例如 2022 年的縣市長是
# 「直轄市長(C1)」「縣市長(C2)」兩場分開辦，還有嘉義市因候選人過世而
# 延到 12/18 的重行選舉，合起來才是完整的全國地圖。
COMBOS = {
    "MAYOR": {
        "subjects": ("C1", "C2"),
        "label": "縣市長（含直轄市長）",
    },
}


def catalog(refresh: bool = False) -> dict[str, dict]:
    """建立 key -> {elections, label, date} 的選舉目錄。"""
    entries: dict[str, dict] = {}
    by_base: dict[str, list[cec.Election]] = {}
    for subject in cec.list_subjects(refresh=refresh):
        sid = subject["subject_id"]
        try:
            elections = cec.list_elections(sid, refresh=refresh)
        except cec.CECError:
            continue
        for e in elections:
            by_base.setdefault(f"{e.year}-{e.subject_id}", []).append(e)

    for base, group in by_base.items():
        group.sort(key=lambda e: e.vote_date)
        for i, e in enumerate(group):
            key = base if i == 0 else f"{base}{chr(ord('a') + i)}"
            entries[key] = {"elections": [e], "label": e.label, "date": e.vote_date,
                            "levels": e.levels}

    for name, spec in COMBOS.items():
        years: dict[int, list[cec.Election]] = {}
        for base, group in by_base.items():
            year, sid = base.split("-")
            if sid in spec["subjects"]:
                years.setdefault(int(year), []).extend(group)
        for year, group in years.items():
            if len({e.subject_id for e in group}) < 2:
                continue
            key = f"{year}-{name}"
            entries[key] = {
                "elections": sorted(group, key=lambda e: e.vote_date),
                "label": f"{year} 年{spec['label']}．全國合併（{len(group)} 場）",
                "date": min(e.vote_date for e in group),
                "levels": ("L",),
            }
    return entries


def cmd_list(args) -> int:
    entries = catalog(refresh=args.refresh)
    rows = sorted(entries.items(), key=lambda kv: (kv[1]["date"], kv[0]), reverse=True)
    if args.year:
        rows = [r for r in rows if r[1]["date"].startswith(str(args.year))]
    if args.keyword:
        rows = [r for r in rows if args.keyword in r[1]["label"]]
    if not args.all and not args.year and not args.keyword:
        rows = [r for r in rows if r[1]["date"] >= "2010-01-01"]
        print("（只列出 2010 年以後；加 --all 看全部，或用 --year / --keyword 篩選）\n")
    print(f"{'鍵值':<16}{'投票日':<13}{'村里':<6}選舉")
    print("-" * 78)
    for key, info in rows:
        has_l = "有" if "L" in (info["levels"] or ()) else "—"
        print(f"{key:<16}{info['date']:<13}{has_l:<6}{info['label']}")
    print(f"\n共 {len(rows)} 筆。產生地圖：python3 -m twelect build <鍵值>")
    return 0


def cmd_build(args) -> int:
    entries = catalog(refresh=args.refresh)
    key = args.election.upper() if args.election.upper() in entries else args.election
    if key not in entries:
        print(f"找不到選舉鍵值「{args.election}」。先執行 python3 -m twelect list 查看。", file=sys.stderr)
        matches = [k for k, v in entries.items() if args.election in k or args.election in v["label"]]
        if matches:
            print("可能是：" + "、".join(sorted(matches)[:10]), file=sys.stderr)
        return 1

    info = entries[key]
    print(f"選舉：{info['label']}")
    result = build_mod.build_payload(
        info["elections"],
        title=args.title or (info["label"] if len(info["elections"]) > 1 else info["elections"][0].label),
        refresh=args.refresh,
        log=print,
    )
    if args.site:
        out = Path(args.output) if args.output else DOCS / f"{key}.html"
    else:
        out = Path(args.output) if args.output else ROOT / "out" / f"{key}.html"
    render.render(result.payload, out)
    size = out.stat().st_size / 1e6
    print(f"\n完成：{out}（{size:.1f} MB，{len(result.payload['villages'])} 個村里）")
    for w in result.warnings:
        print(f"※ {w}")
    if args.site:
        index = site.build_index(out.parent)
        print(f"索引頁：{index}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_site(args) -> int:
    docs = Path(args.docs) if args.docs else DOCS
    if not docs.exists():
        print(f"找不到 {docs}", file=sys.stderr)
        return 1
    index = site.build_index(docs)
    maps = sorted(p.name for p in docs.glob("*.html") if p.name != "index.html")
    print(f"{index}（{len(maps)} 張地圖：{'、'.join(maps)}）")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="twelect", description="台灣選舉結果村里層級地圖"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="列出可選的選舉")
    pl.add_argument("--year", type=int, help="只看某一年（西元）")
    pl.add_argument("--keyword", help="選舉名稱關鍵字")
    pl.add_argument("--all", action="store_true", help="含 2010 年以前")
    pl.add_argument("--refresh", action="store_true", help="忽略快取重新下載")
    pl.set_defaults(func=cmd_list)

    pb = sub.add_parser("build", help="抓取村里資料並產生地圖")
    pb.add_argument("election", help="選舉鍵值，例如 2022-MAYOR")
    pb.add_argument("-o", "--output", help="輸出檔案路徑")
    pb.add_argument("--title", help="自訂標題")
    pb.add_argument("--open", action="store_true", help="產生後直接開啟")
    pb.add_argument("--refresh", action="store_true", help="忽略快取重新下載")
    pb.add_argument("--site", action="store_true",
                    help="輸出到 docs/ 並更新 GitHub Pages 索引頁")
    pb.set_defaults(func=cmd_build)

    ps = sub.add_parser("site", help="重新產生 docs/index.html")
    ps.add_argument("--docs", help="docs 目錄路徑")
    ps.set_defaults(func=cmd_site)

    args = p.parse_args(argv)
    return args.func(args)
