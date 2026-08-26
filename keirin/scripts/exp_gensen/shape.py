"""厳選AIマスター(614)の商品の形と公開実績を集計する（DB不要）。"""
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

R = [json.loads(l) for l in (Path("gensen/parsed.jsonl")).open(encoding="utf-8")]
if len(sys.argv) >= 3:
    lo, hi = sys.argv[1], sys.argv[2]
    R = [r for r in R if lo <= r["date"] <= hi]
R.sort(key=lambda r: (r["date"], r["race_id"]))
print(f"件数 {len(R)}  {R[0]['date']}〜{R[-1]['date']}  日数 {len(set(r['date'] for r in R))}"
      f"  平均 {len(R)/len(set(r['date'] for r in R)):.1f}件/日")

# 券種
bt = Counter(l["bet_type"] for r in R for l in r["legs"])
print("券種:", dict(bt))
npts = Counter(sum(l["n_points"] or 0 for l in r["legs"]) for r in R)
print("点数分布:", dict(sorted(npts.items())))
print("平均点数: %.2f" % (sum(sum(l["n_points"] or 0 for l in r["legs"]) for r in R)/len(R)))
bets = Counter(r.get("total_bet") for r in R)
print("投資額分布:", dict(sorted(bets.items(), key=lambda kv: -kv[1])[:8]))
print("tier:", dict(Counter(r.get("tier") for r in R)))
print("n_entries:", dict(Counter(r.get("n_entries") for r in R)))
print("race_type top:", dict(Counter(r.get("race_type") for r in R).most_common(12)))
print("cls:", dict(Counter(r.get("cls") for r in R).most_common(8)))

# 成績
settled = [r for r in R if r.get("payout") is not None and r.get("total_bet")]
inv = sum(r["total_bet"] for r in settled); pay = sum(r["payout"] for r in settled)
hits = [r for r in settled if r["payout"] > 0]
print(f"\n[実績] 採点 {len(settled)}件  的中 {len(hits)} ({len(hits)/len(settled)*100:.2f}%)"
      f"  投資 {inv:,}  払戻 {pay:,}  ROI {pay/inv*100:.1f}%")
gami = [h for h in hits if h["payout"] < h["total_bet"]]
print(f"  ガミ {len(gami)}件  表示的中(ガミ除く) {(len(hits)-len(gami))/len(settled)*100:.2f}%")
if hits:
    ps = sorted(h["payout"] for h in hits)
    print(f"  払戻 中央 {median(ps):,.0f}  最大 {ps[-1]:,}")
    for t in (30_000, 50_000, 100_000, 150_000, 300_000, 500_000):
        n = sum(1 for p in ps if p >= t)
        print(f"    {t//10000}万円以上 {n}件  {n/len(settled)*100:.2f}%/件  "
              f"{n/len(set(r['date'] for r in settled)):.3f}件/日")
# tier別
print("\n[tier別]")
for tv in sorted({r.get("tier") for r in settled}, key=str):
    g = [r for r in settled if r.get("tier") == tv]
    i2 = sum(r["total_bet"] for r in g); p2 = sum(r["payout"] for r in g)
    h2 = [r for r in g if r["payout"] > 0]
    print(f"  {str(tv):10s} n={len(g):5d} 的中 {len(h2)/len(g)*100:5.2f}%  ROI {p2/i2*100:6.1f}%")
# 点数別
print("\n[点数別]")
for k in sorted(npts):
    g = [r for r in settled if sum(l['n_points'] or 0 for l in r['legs']) == k]
    if not g: continue
    i2 = sum(r["total_bet"] for r in g); p2 = sum(r["payout"] for r in g)
    h2 = [r for r in g if r["payout"] > 0]
    print(f"  {k}点 n={len(g):5d} 的中 {len(h2)/len(g)*100:5.2f}%  ROI {p2/i2*100:6.1f}%")
# 月別
print("\n[月別] 件/日 的中 ROI")
bym = defaultdict(list)
for r in settled: bym[r["date"][:6]].append(r)
for m in sorted(bym):
    g = bym[m]; i2 = sum(r["total_bet"] for r in g); p2 = sum(r["payout"] for r in g)
    h2 = [r for r in g if r["payout"] > 0]
    d = len(set(r["date"] for r in g))
    print(f"  {m}  {len(g)/d:5.1f}  {len(h2)/len(g)*100:5.2f}%  {p2/i2*100:6.1f}%   n={len(g)}")
