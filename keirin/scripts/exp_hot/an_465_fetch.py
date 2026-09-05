#!/usr/bin/env python3
"""対照群（的中率型）5人の詳細取得・解析。 yid: 465/354/401/585/350

出力: an465/<yid>.jsonl  （1行=1商品）
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

from fetch_goods import fetch_detail
from parse_hot import parse_bets

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw" / "detail"
OUT = HERE / "an465"
OUT.mkdir(exist_ok=True)

YIDS = {465: 10, 354: 12, 401: 12, 585: 13, 350: 1}
WIN = ("20260820", "20260905")

MARK = {
    "Icon_Honmei": "◎", "Icon_Taikou": "○", "Icon_Kurosan": "▲",
    "Icon_Osae": "△", "Icon_Renka": "×", "Icon_Chuui": "注",
}


def parse_extra(gid: str) -> dict:
    t = (RAW / f"{gid}.html").read_text(encoding="utf-8")
    out: dict = {}
    m = re.search(r'<table class="YosoShirushiTable01">(.*?)</table>', t, re.S)
    marks = []
    if m:
        for tr in re.split(r"<tr>", m.group(1))[1:]:
            ic = re.search(r'Icon_Shirushi (Icon_\w+)', tr)
            nu = re.search(r'<span class="Num Waku\d+">(\d+)</span>', tr)
            pt = re.search(r'RaceCardCell01">.*?([\d.]+)</span>', tr, re.S)
            if ic and nu:
                marks.append({"mark": MARK.get(ic.group(1), ic.group(1)),
                              "num": int(nu.group(1)),
                              "point": float(pt.group(1)) if pt else None})
    out["marks"] = marks
    k = re.search(r'<h2 class="YosoKenkaiTitle">(.*?)</h2>\s*'
                  r'<div class="YosoKenkaiTxt">(.*?)</div>', t, re.S)
    if k:
        out["kenkai_title"] = re.sub(r"<[^>]+>", "", k.group(1)).strip()
        out["kenkai"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", k.group(2))).strip()
    # ライン数（並び予想の DeployBox 数）
    dw = re.search(r'<div class="DeployYosoWrap">(.*?)</section>', t, re.S)
    out["n_lines"] = len(re.findall(r'class="DeployBox', dw.group(1))) if dw else None
    out["n_entries"] = len(re.findall(r'class="Shaban_InBox"', dw.group(1))) if dw else None
    return out


def main() -> None:
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None
    items: dict[int, list[dict]] = {y: [] for y in YIDS}
    for line in open(HERE / "month2.jsonl", encoding="utf-8"):
        d = json.loads(line)
        if d["yid"] in items:
            items[d["yid"]].append(d)
    for yid, stride in YIDS.items():
        if only and yid != only:
            continue
        all_ = items[yid]
        win = [x for x in all_ if WIN[0] <= x["date"] <= WIN[1]]
        samp = win[::stride] if stride > 1 else all_
        hi = [x for x in all_ if (x.get("payout") or 0) >= 100_000]
        seen, tasks = set(), []
        for tag, xs in (("sample", samp), ("hi", hi)):
            for x in xs:
                if x["gid"] in seen:
                    # 既に sample にいる高額 → tag を両方立てる
                    for t0 in tasks:
                        if t0["gid"] == x["gid"]:
                            t0["tags"].append(tag)
                    continue
                seen.add(x["gid"])
                y = dict(x)
                y["tags"] = [tag]
                tasks.append(y)
        print(f"== yid {yid}: sample {len(samp)} / hi {len(hi)} / unique {len(tasks)}", flush=True)
        rows = []
        for i, it in enumerate(tasks):
            try:
                fetch_detail(it["gid"])
                b = parse_bets(RAW / f"{it['gid']}.html")
                it.update({k: b.get(k) for k in
                           ("total_bet", "n_points_total", "unit_min", "unit_max",
                            "bet_types")})
                it["payout_detail"] = b.get("payout")
                it["rows"] = b["rows"]
                it["hit_row"] = b["hit"]
                it.update(parse_extra(it["gid"]))
                rows.append(it)
            except Exception as exc:                                   # noqa: BLE001
                print(f"!! {it['gid']}: {exc}", flush=True)
            if (i + 1) % 25 == 0:
                print(f"   {i+1}/{len(tasks)}", flush=True)
        p = OUT / f"{yid}.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                     encoding="utf-8")
        print(f"-> {p} {len(rows)}件", flush=True)


if __name__ == "__main__":
    main()
