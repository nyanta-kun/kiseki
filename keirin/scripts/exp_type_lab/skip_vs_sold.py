#!/usr/bin/env python3
"""「買った側」と「見送った側」を同じ台で突き合わせる（2026-09-23）。

母集団は `type_lab_picks`（mode='live'）＝**その朝に実際に組んだ買い目**。
売ったかどうかは `netkeirin_submissions`、見送りの理由は `submission_skips`。
🔴 `picks_history`（旧ランクの候補）は使わない。
"""
from __future__ import annotations
import argparse, statistics, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.database import get_connection

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="2026-09-16")
ap.add_argument("--end", default="2026-09-22")
a = ap.parse_args()

with get_connection() as c:
    picks = [dict(r) for r in c.execute(
        "SELECT race_key, plan_key, race_date, n_legs, budget, hit, payout, "
        "       COALESCE(void_refund, 0) AS void_refund, pred_mean_payout, type_label "
        "FROM type_lab_picks WHERE mode = 'live' AND race_date BETWEEN ? AND ? "
        "  AND hit IS NOT NULL", (a.start, a.end))]
    sold = {(dict(r)["race_key"], dict(r)["rank_key"]) for r in c.execute(
        "SELECT ns.race_key, ns.rank_key FROM netkeirin_submissions ns "
        "JOIN wt_races wr ON wr.race_key = ns.race_key "
        "WHERE ns.deleted_at IS NULL AND ns.status IN ('submitted','published') "
        "  AND wr.race_date BETWEEN ? AND ?", (a.start, a.end))}
    skips = {}
    for r in c.execute(
            "SELECT race_key, rank_key, reason_code FROM submission_skips "
            "WHERE race_date BETWEEN ? AND ?", (a.start, a.end)):
        d = dict(r)
        skips.setdefault((d["race_key"], d["rank_key"]), d["reason_code"])

days = len({p["race_date"] for p in picks})


def summarize(rows):
    n = len(rows)
    if not n:
        return None
    bet = sum(int(r["budget"] or 0) - int(r["void_refund"] or 0) for r in rows)
    pay = sum(int(r["payout"] or 0) for r in rows)
    hits = [int(r["payout"] or 0) for r in rows if r["hit"]]
    net = [v for v, r in zip(hits, [r for r in rows if r["hit"]])
           if v > int(r["budget"] or 0) - int(r["void_refund"] or 0)]
    return dict(n=n, perday=n / days, hit=len(hits) / n,
                net=len(net) / n, roi=pay / bet if bet else 0,
                med=statistics.median(hits) if hits else 0,
                mx=max(hits) if hits else 0,
                b4=sum(1 for v in hits if v >= 40_000))


def line(label, s):
    if not s:
        return f"{label:<26} (0件)"
    return (f"{label:<26}{s['n']:>5}{s['perday']:>7.1f}{s['hit']:>9.1%}{s['net']:>9.1%}"
            f"{s['roi']:>8.1%}{s['med']:>9,.0f}{s['mx']:>10,.0f}{s['b4']:>6}")


HEAD = (f"{'':<26}{'件':>5}{'件/日':>7}{'素の的中':>9}{'表示的中':>9}{'ROI':>8}"
        f"{'中央払戻':>9}{'最高払戻':>10}{'4万+':>6}")

# ── ① 売った ↔ 見送った（売る予定だったものだけ） ────────────────
sold_rows = [p for p in picks if (p["race_key"], p["plan_key"]) in sold]
skip_rows = [p for p in picks if (p["race_key"], p["plan_key"]) in skips
             and (p["race_key"], p["plan_key"]) not in sold]
print(f"=== ① 売った商品 ↔ 見送った商品（{a.start}〜{a.end}・{days}日） ===")
print("母集団はどちらも『その朝に実際に組んだ買い目』。見送りは submission_skips に理由がある行だけ。\n")
print(HEAD); print("-" * len(HEAD))
print(line("売った", summarize(sold_rows)))
print(line("見送った（合計）", summarize(skip_rows)))
print("-" * len(HEAD))
by = defaultdict(list)
for p in skip_rows:
    by[skips[(p["race_key"], p["plan_key"])]].append(p)
for k, rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
    print(line(f"  └ {k}", summarize(rows)))

# ── ② 売った商品と同じプランで、見送ったもの ─────────────────
print(f"\n=== ② プラン別（売った ↔ 見送った） ===")
print(HEAD); print("-" * len(HEAD))
plans = defaultdict(lambda: ([], []))
for p in sold_rows:
    plans[p["plan_key"]][0].append(p)
for p in skip_rows:
    plans[p["plan_key"]][1].append(p)
for k, (s_, k_) in sorted(plans.items(), key=lambda kv: -len(kv[1][0])):
    if len(s_) + len(k_) < 8:
        continue
    print(line(f"{k} 売った", summarize(s_)))
    print(line(f"{k} 見送った", summarize(k_)))


# ── ③ 差の不確かさ（日ブートストラップ） ─────────────────────────
import random

def boot(a_rows, b_rows, n=3000, seed=11):
    days_all = sorted({r["race_date"] for r in a_rows + b_rows})
    ga, gb = defaultdict(list), defaultdict(list)
    for r in a_rows:
        ga[r["race_date"]].append(r)
    for r in b_rows:
        gb[r["race_date"]].append(r)
    rnd = random.Random(seed)
    dh, dr = [], []
    for _ in range(n):
        smp = [rnd.choice(days_all) for _ in days_all]
        xa = [r for d in smp for r in ga.get(d, ())]
        xb = [r for d in smp for r in gb.get(d, ())]
        sa, sb = summarize(xa), summarize(xb)
        if not sa or not sb:
            continue
        dh.append((sb["net"] - sa["net"]) * 100)
        dr.append((sb["roi"] - sa["roi"]) * 100)
    def ci(v):
        v = sorted(v)
        return v[int(.025 * len(v))], v[int(.975 * len(v))], sum(v) / len(v)
    return ci(dh), ci(dr)

print(f"\n=== ③ 見送った側 − 売った側（日ブートストラップ3,000本） ===")
(hl, hh, hm), (rl, rh, rm) = boot(sold_rows, skip_rows)
print(f"  Δ表示的中  {hm:+.2f}pt  95%CI [{hl:+.2f}, {hh:+.2f}]")
print(f"  ΔROI      {rm:+.2f}pt  95%CI [{rl:+.2f}, {rh:+.2f}]")
ax = [p for p in skip_rows if skips[(p["race_key"], p["plan_key"])] == "axis_gate"]
if ax:
    (hl, hh, hm), (rl, rh, rm) = boot(sold_rows, ax)
    print(f"  うち軸信頼ゲート落ちだけ:")
    print(f"    Δ表示的中 {hm:+.2f}pt  95%CI [{hl:+.2f}, {hh:+.2f}]")
    print(f"    ΔROI     {rm:+.2f}pt  95%CI [{rl:+.2f}, {rh:+.2f}]")
