#!/usr/bin/env python3
"""month2.jsonl だけで出せる運用面（本数・時刻・レース種別・場）を集計する。"""
from __future__ import annotations
import collections, json, re, statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = {465: "シュウの二車福", 354: "大河原修司", 401: "二ノ輪大嵐",
         585: "Equine Genius", 350: "鈴木誠"}

rows = collections.defaultdict(list)
for line in open(HERE / "month2.jsonl", encoding="utf-8"):
    d = json.loads(line)
    if d["yid"] in NAMES:
        rows[d["yid"]].append(d)


def grade(rn: str) -> str:
    for k in ("決勝", "準決勝", "特別選抜", "特選", "選抜", "予選", "初日", "一般"):
        if k in rn:
            return k
    return "その他"


def cls(rn: str) -> str:
    if "ガールズ" in rn:
        return "ガールズ"
    m = re.match(r"([ＳＡ])級", rn)
    if m:
        return m.group(1) + "級"
    if "Ｌ級" in rn:
        return "Ｌ級"
    return "?"


for yid, name in NAMES.items():
    rs = rows[yid]
    days = collections.Counter(r["date"] for r in rs)
    hrs = collections.Counter()
    lead = []
    for r in rs:
        p = r.get("published_at") or ""
        m = re.match(r"(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2})", p)
        if m:
            hrs[int(m.group(4))] += 1
    print(f"\n### {yid} {name}  n={len(rs)}  日数={len(days)}  件/日 中央={st.median(days.values()):.1f} "
          f"min={min(days.values())} max={max(days.values())}")
    print("  公開時刻(時):", " ".join(f"{h}時:{c}" for h, c in sorted(hrs.items())))
    print("  購入額:", collections.Counter(r["bet"] for r in rs).most_common(5))
    print("  種別:", collections.Counter(grade(r["race_name"]) for r in rs).most_common())
    print("  クラス:", collections.Counter(cls(r["race_name"]) for r in rs).most_common())
    print("  場 上位8:", collections.Counter(r["venue"] for r in rs).most_common(8))
    print("  R番号:", sorted(collections.Counter(r["race_no"] for r in rs).items()))
    # 的中/払戻
    hit = [r for r in rs if (r.get("payout") or 0) > 0]
    pays = sorted(r["payout"] for r in hit)
    inv = sum(r["bet"] or 0 for r in rs)
    pay = sum(r["payout"] or 0 for r in rs)
    def q(p):
        return pays[min(len(pays) - 1, int(len(pays) * p))] if pays else None
    print(f"  的中 {len(hit)}/{len(rs)}={len(hit)/len(rs)*100:.1f}%  ROI={pay/inv*100:.1f}%  "
          f"払戻 p10={q(.1)} 中央={q(.5)} p90={q(.9)} max={pays[-1] if pays else None}")
    # 実効的中（払戻>賭け金）
    eff = [r for r in rs if (r.get("payout") or 0) > (r["bet"] or 0)]
    print(f"  ガミ抜き的中 {len(eff)}/{len(rs)}={len(eff)/len(rs)*100:.1f}%  "
          f"（ガミ {len(hit)-len(eff)}件 = 的中の {(len(hit)-len(eff))/max(len(hit),1)*100:.1f}%）")
