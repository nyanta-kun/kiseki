#!/usr/bin/env python3
"""465(二車複) と 401(2車単併用) を深掘り。券種を増やす判断材料。"""
from __future__ import annotations
import collections, json, re, statistics as st
from pathlib import Path
from an_465_expand import product_stakes

HERE = Path(__file__).resolve().parent


def load(y):
    return [json.loads(l) for l in (HERE / "an465" / f"{y}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


# ---------------- 465 ----------------
rs = [r for r in load(465) if "sample" in r["tags"]]
print(f"### 465 シュウの二車福  sample={len(rs)}")
# comment(自信度) × 点数 × 的中
tab = collections.defaultdict(lambda: [0, 0, [], []])
for r in rs:
    c = r.get("comment") or ""
    n = r["n_points_total"]
    t = tab[c]
    t[0] += 1
    if r.get("hit_row"):
        t[1] += 1
        t[3].append(r["hit_row"]["hit_odds"])
    t[2].append(n)
print("  自信度コメント × 点数 × 的中:")
for c, (n, h, pts, od) in sorted(tab.items(), key=lambda x: -x[1][0]):
    print(f"    {c:26s} n={n:3d} 点数中央={st.median(pts):.0f} 的中={h}({h/n*100:.0f}%) "
          f"的中倍率中央={st.median(od) if od else '-'}")
# 点数 × 1点賭け金
print("  点数 × 1点賭け金:", collections.Counter(
    (r["n_points_total"], r["unit_min"]) for r in rs).most_common())
# 買い目の車番が「予測勝率」上位何位か
rank_hit = collections.Counter()
for r in rs:
    t = (HERE / "raw" / "detail" / f"{r['gid']}.html").read_text(encoding="utf-8")
    k = re.search(r'<div class="YosoKenkaiTxt">(.*?)</div>', t, re.S)
    if not k:
        continue
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", k.group(1)))
    m = re.search(r"車番 選手名 予測勝率 (.*?)・シュウの二車福", txt)
    if not m:
        continue
    order = re.findall(r"(\d+) \S+ ([\d.]+)%", m.group(1))
    rank = {int(a): i + 1 for i, (a, b) in enumerate(order)}
    for row in r["rows"]:
        nums = [int(x) for x in (row["combo"] or [])]
        if len(nums) == 2 and all(x in rank for x in nums):
            rank_hit[tuple(sorted(rank[x] for x in nums))] += 1
print("  買い目2車の予測勝率順位ペア 上位12:", rank_hit.most_common(12))
tot = sum(rank_hit.values())
top12 = sum(v for k, v in rank_hit.items() if k == (1, 2))
print(f"    (1位,2位)の組み合わせ = {top12}/{tot} = {top12/max(tot,1)*100:.0f}%")
# 母集団(month2)での的中倍率相当: 払戻/賭け金
pop = [json.loads(l) for l in open(HERE / "month2.jsonl") if json.loads(l)["yid"] == 465]
ph = [r for r in pop if (r.get("payout") or 0) > 0]
print(f"  母集団 n={len(pop)} 的中={len(ph)} ({len(ph)/len(pop)*100:.1f}%)  "
      f"払戻/賭け金 中央={st.median([r['payout']/r['bet'] for r in ph if r['bet']]):.2f}倍")
band = collections.Counter()
for r in ph:
    x = r["payout"] / (r["bet"] or 1)
    for lo, hi in ((0, 1), (1, 2), (2, 5), (5, 10), (10, 1e9)):
        if lo <= x < hi:
            band[f"{lo}-{hi if hi<1e9 else '+'}"] += 1
print("  母集団 払戻/賭け金 帯:", sorted(band.items(), key=lambda x: float(x[0].split('-')[0])),
      f" (合計{len(ph)})")

# ---------------- 401 ----------------
rs = [r for r in load(401) if "sample" in r["tags"]]
print(f"\n### 401 二ノ輪大嵐  sample={len(rs)}")
g = collections.defaultdict(list)
for r in rs:
    g["+".join(r["bet_types"])].append(r)
for k, xs in g.items():
    hits = [x for x in xs if x.get("hit_row")]
    print(f"  {k}: n={len(xs)} 点数中央={st.median([x['n_points_total'] for x in xs]):.0f} "
          f"購入額中央={st.median([x['bet'] for x in xs]):.0f} 的中={len(hits)} "
          f"({len(hits)/len(xs)*100:.0f}%) 倍率中央="
          f"{st.median([x['hit_row']['hit_odds'] for x in hits]) if hits else '-'}")
    print(f"     場×種別: {collections.Counter(re.sub('^[ＳＡＬ]級 ','',x['race_name'])[:6] for x in xs).most_common(5)}")
    print(f"     公開時刻: {collections.Counter((x['published_at'] or '')[11:13] for x in xs).most_common(5)}")
    print(f"     車数(出走): {collections.Counter(len(x['marks']) for x in xs).most_common()}")
# 母集団での2車単比率の代理: 購入金額パターン
pop = [json.loads(l) for l in open(HERE / "month2.jsonl") if json.loads(l)["yid"] == 401]
print("  母集団 購入額:", collections.Counter(r["bet"] for r in pop).most_common(8))
bybet = collections.defaultdict(lambda: [0, 0])
for r in pop:
    b = bybet[r["bet"]]
    b[0] += 1
    if (r.get("payout") or 0) > 0:
        b[1] += 1
print("  母集団 購入額別 的中率:", [(k, f"{v[1]}/{v[0]}={v[1]/v[0]*100:.0f}%")
                                for k, v in sorted(bybet.items(), key=lambda x: -x[1][0])[:6]])
# サンプルの購入額 -> 券種 対応
print("  サンプル 購入額→券種:", collections.Counter(
    (r["bet"], "+".join(r["bet_types"])) for r in rs).most_common(8))
