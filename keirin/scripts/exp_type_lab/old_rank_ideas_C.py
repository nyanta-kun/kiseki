#!/usr/bin/env python3
"""C. 7H1 の買い方「本命ラインを丸ごと落とす」を型ラボの穴商品 `A_ana` に当てる（2026-09-11）。

`A_ana` は軸1を外した5点。7H1 はさらに**軸1と同じラインの車を全部落とす**。
paper 台には買った点の `pred_odds` / `prob` が焼き付いているので、
**点を減らして本番の `allocate` で配り直す**ところまでは忠実に再現できる。
🔴 落とした点の代わりに新しい点を入れることはできない（その点の予測オッズが台に無い）。
   ＝ **本稿で測れるのは「減らす」方向だけ。**
"""
from __future__ import annotations
import os, sys, pickle
from collections import defaultdict
import numpy as np
import psycopg2

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.type_lab import PLANS, allocate, BUDGET, UNIT  # noqa: E402
from src.stake_allocation import MIN_MEAN_PAYOUT  # noqa: E402
MIN_POINT_ODDS = 2.0

con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
cur.execute("""SELECT race_key, frame_no, line_group, pred_top3_pct
               FROM keirin.wt_entries e
               WHERE race_key IN (SELECT race_key FROM keirin.type_lab_picks
                                  WHERE mode='paper' AND plan_key='A_ana')""")
ENT = defaultdict(list)
for rk, fn, lg, p3 in cur.fetchall():
    ENT[rk].append((int(fn), lg, float(p3 or 0)))

cur.execute("""SELECT race_key, race_date, legs, budget, payout, win_combo, final_odds,
                      axis1, pred_mean_payout
               FROM keirin.type_lab_picks
               WHERE mode='paper' AND plan_key='A_ana' AND settled_at IS NOT NULL
                     AND n_entries=7""")
ROWS = cur.fetchall()
print(f"A_ana paper {len(ROWS):,}行")

plan = PLANS["A_ana"]


def gate(stakes, odds):
    if not stakes:
        return False
    mean = sum(s * odds[c] for c, s in stakes.items()) / len(stakes)
    if mean <= MIN_MEAN_PAYOUT:
        return False
    return min(odds[c] for c in stakes) >= MIN_POINT_ODDS


res = defaultdict(lambda: defaultdict(lambda: dict(n=0, shown=0, inv=0, pay=0, hp=0, pays=[], k=[])))
days = defaultdict(set)
SEEDS = 20
rng = np.random.default_rng(0)


def run(keep, odds, probs, win, fo):
    """点集合 keep で本番配分＋入稿ゲートを通す。通らなければ None。"""
    if not keep:
        return None
    st = allocate(list(keep), odds, probs, plan, BUDGET, UNIT)
    if st is None or not gate(st, odds):
        return None
    p = int(round(fo * st[win])) if (win is not None and win in st and fo) else 0
    return st, p


def add(w, arm, st, p):
    a = res[w][arm]
    a["n"] += 1; a["inv"] += sum(st.values()); a["pay"] += p; a["k"].append(len(st))
    a["shown"] += 1 if p > sum(st.values()) else 0
    a["hp"] += 1 if p >= 100000 else 0
    if p:
        a["pays"].append(p)


for rk, d, legs, bud, pay, wc, fo, a1, pmp in ROWS:
    es = ENT.get(rk)
    if not es or len(es) != 7:
        continue
    w = "探索" if str(d) < "2026-01-01" else "確認"
    days[w].add(str(d))
    lg1 = next((g for f, g, _ in es if f == a1), None)
    mates = {f for f, g, _ in es if g == lg1 and f != a1 and g not in (None, "", "0")}
    odds = {tuple(int(x) for x in l["combo"].split("-")): float(l["pred_odds"]) for l in legs}
    probs = {tuple(int(x) for x in l["combo"].split("-")): float(l.get("prob") or 0) for l in legs}
    win = tuple(int(x) for x in str(wc).split("-")) if wc else None

    cur_r = run(list(odds), odds, probs, win, fo)
    keep = [c for c in odds if not (set(c) & mates)]
    arm_r = run(keep, odds, probs, win, fo)
    n_drop = len(odds) - len(keep)

    if cur_r:
        add(w, "現行(全件)", *cur_r)
    # 🔴 ペア比較: **本命ライン落としが成立したレースだけ**で現行と並べる
    if arm_r and cur_r:
        add(w, "現行(同一R)", *cur_r)
        add(w, "本命ライン落とし", *arm_r)
        # 同じ点数だけ無作為に落とす対照（seed ごとに別集計）
        for s in range(SEEDS):
            if n_drop <= 0:
                r2 = cur_r
            else:
                idx = rng.choice(len(odds), size=len(odds) - n_drop, replace=False)
                r2 = run([list(odds)[i] for i in idx], odds, probs, win, fo)
            if r2:
                add(w, f"無作為落とし#{s}", *r2)

for w in ("探索", "確認"):
    nd = len(days[w])
    print(f"\n[{w}] {nd}日")
    for arm in ("現行(全件)", "現行(同一R)", "本命ライン落とし"):
        a = res[w].get(arm)
        if not a or not a["n"]:
            continue
        print(f"  {arm:14s} n={a['n']:5d} 件/日={a['n']/nd:5.2f} 平均点数={np.mean(a['k']):.2f} "
              f"表示的中={a['shown']/a['n']*100:5.2f}% ROI={a['pay']/a['inv']*100:6.1f}% "
              f"中央={int(np.median(a['pays'])) if a['pays'] else 0:7d} 10万+/日={a['hp']/nd:.3f}")
    cs = [res[w][f"無作為落とし#{s}"] for s in range(SEEDS) if res[w].get(f"無作為落とし#{s}", {}).get("n")]
    if cs:
        sh = np.array([c["shown"] / c["n"] * 100 for c in cs])
        roi = np.array([c["pay"] / c["inv"] * 100 for c in cs])
        hp = np.array([c["hp"] / nd for c in cs])
        arm = res[w]["本命ライン落とし"]
        ash, aroi, ahp = arm["shown"]/arm["n"]*100, arm["pay"]/arm["inv"]*100, arm["hp"]/nd
        print(f"  {'無作為落とし(中央)':14s} n={int(np.median([c['n'] for c in cs])):5d} "
              f"平均点数={np.mean([np.mean(c['k']) for c in cs]):.2f} "
              f"表示的中={np.median(sh):5.2f}% ROI={np.median(roi):6.1f}% 10万+/日={np.median(hp):.3f}")
        print(f"    → 対照20seed に 表示的中 {int((ash>sh).sum())}/20 ・ ROI {int((aroi>roi).sum())}/20 ・ "
              f"10万+ {int((ahp>hp).sum())}/20 で勝ち")
