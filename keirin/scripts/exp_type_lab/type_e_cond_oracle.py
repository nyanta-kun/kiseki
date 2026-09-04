#!/usr/bin/env python3
"""型E: 「条件で買い目を入れ替える」の**天井**を測る（2026-09-04・ユーザー提案）。

> 点数を拡げるのは NG。**条件により現在の購入点数を上限に買い目を変更する**のが正しい。

先行検証では「条件ごとに買い方を変える」は2度否定されている:
  - `type_e_2026_09_01.md` §3 … 帯を下げる条件分岐 → **無作為に同数選んだ対照と互角**
  - `type_e_order_split_2026_09_03.md` … 三連複への振替を条件分岐 → 24セルで両窓勝ちゼロ

そこで本稿は個々の条件を試す前に **オラクルの天井**（各レースで結果を見てから
最良の買い方を選べたとしたら表示的中はいくつになるか）を出す。天井が小さければ、
どんな条件を作っても投資に値しないと**まとめて**言える。

🔴 買い方はすべて **14点以下・本番の配分とゲート**。点数は増やさない。
🔴 「無作為に毎レース選ぶ」対照も置く（腕が増えれば天井は自動的に上がるため）。
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import (ctx, AXIS_GATE_MIN,  # noqa: E402
                            MIN_MEAN_PAYOUT, MIN_POINT_ODDS)
from src.type_lab import (PLANS, Plan, allocate, build_legs,  # noqa: E402
                          mean_expected_payout)

K = 14


def _band(x, lo: float, cap: int = 0, k: int = K):
    c = [tuple(p) for p, v in x.po_tf.items()
         if v and float(v) >= lo and len(set(p)) == 3]
    c.sort(key=lambda p: -float(x.pr_tf.get(p, 0.0)))
    out, per = [], Counter()
    for p in c:
        t = frozenset(p)
        if cap and per[t] >= cap:
            continue
        out.append(p)
        per[t] += 1
        if len(out) >= k:
            break
    return out if len(out) >= 2 else None


def _trio(x, n: int):
    plan = Plan("x", "E", "trio", "axis2_flow", n, alloc="dutch")
    return build_legs(x.shape, plan, x.po_t3, x.pr_t3)


ARMS = {
    "現行 帯30+ 14点":      lambda x: ("tf", _band(x, 30.0)),
    "帯20+ 14点":           lambda x: ("tf", _band(x, 20.0)),
    "帯40+ 14点":           lambda x: ("tf", _band(x, 40.0)),
    "帯なし 確率上位14点":    lambda x: ("tf", _band(x, 2.0)),
    "帯30+ 集合上限2":       lambda x: ("tf", _band(x, 30.0, cap=2)),
    "帯30+ 集合上限1":       lambda x: ("tf", _band(x, 30.0, cap=1)),
    "三連複 軸2+相手4（4点）": lambda x: ("t3", _trio(x, 4)),
}


def run(x, kind, legs):
    if not legs:
        return None
    pod, prb = (x.po_t3, x.pr_t3) if kind == "t3" else (x.po_tf, x.pr_tf)
    plan = PLANS["E_hit"] if kind == "tf" else Plan(
        "x", "E", "trio", "axis2_flow", len(legs), alloc="dutch")
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if kind == "t3":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    inv = float(sum(st.values()))
    return dict(date=x.date, inv=inv, pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod), shown=pay > inv)


def main() -> None:
    z = C.board()
    axs = z["AXIS_SUM"].astype(float)
    names = list(ARMS)
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        idx = [int(i) for i in C.select("E", win)
               if axs[int(i)] >= AXIS_GATE_MIN["E_hit"]]
        nd = C.days_of(C.select(None, win))
        per_race = []
        for i in idx:
            x = ctx(i)
            if x is None:
                continue
            got = {}
            for n in names:
                kind, legs = ARMS[n](x)
                r = run(x, kind, legs)
                if r:
                    got[n] = r
            if got:
                per_race.append(got)
        print("\n" + "=" * 108)
        print(f"███ 型E 条件分岐の天井  {label}  n={len(per_race):,}R / {nd}日")
        print(f"  {'腕':24s} {'組めた率':>7s} {'点数':>5s} {'表示的中%':>9s} "
              f"{'ROI':>6s} {'払戻中央':>9s}")
        for n in names:
            recs = [g[n] for g in per_race if n in g]
            s = C.summarize(recs, nd)
            print(f"  {n:24s} {len(recs)/len(per_race)*100:6.1f}% {s['k']:5.1f} "
                  f"{s['shown']:8.2f}% {s['roi']:6.1f} {s['med_pay']:9,.0f}")

        base = [g["現行 帯30+ 14点"] for g in per_race if "現行 帯30+ 14点" in g]
        b = C.summarize(base, nd)
        # ── オラクル: レースごとに**結果を見てから**最良の腕を選ぶ ──
        for subset in (names, ["現行 帯30+ 14点", "帯20+ 14点"],
                       ["現行 帯30+ 14点", "三連複 軸2+相手4（4点）"],
                       ["現行 帯30+ 14点", "帯30+ 集合上限1"]):
            orc = []
            for g in per_race:
                cand = [g[n] for n in subset if n in g]
                if not cand:
                    continue
                orc.append(max(cand, key=lambda r: (r["shown"], r["pay"])))
            s = C.summarize(orc, nd)
            tag = "全腕" if subset is names else " + ".join(
                x.replace("現行 帯30+ 14点", "現行") for x in subset)
            print(f"  {'【オラクル】' + tag:24s} {'':7s} {s['k']:5.1f} "
                  f"{s['shown']:8.2f}% {s['roi']:6.1f} {s['med_pay']:9,.0f}"
                  f"   （現行比 {s['shown'] - b['shown']:+.2f}pt）")
        # ── 対照: 毎レース無作為に腕を選ぶ ──
        ms = []
        for seed in range(20):
            rng = random.Random(seed * 977 + 13)
            rc = [g[rng.choice([n for n in names if n in g])] for g in per_race]
            ms.append(C.summarize(rc, nd)["shown"])
        print(f"  {'（対照）毎レース無作為に選ぶ':24s} {'':7s} {'':5s} "
              f"{np.median(ms):8.2f}%   （現行比 {np.median(ms) - b['shown']:+.2f}pt）")



# ═══════ 追補 — 「組めるなら別の形を使う」ハイブリッド（条件＝入稿ゲート）═══════
#
# 上の表で目を引くのは**組めた率**:
#   帯なし 確率上位14点 … 組めた率 39〜43% で 表示的中 28.8〜29.6%
#   三連複 軸2+相手4    … 組めた率 34〜37% で 表示的中 31.3〜31.8%
# どちらも「その形が2万円ゲートを通るレース」でしか成立しない。つまり
# **条件は結果の予測ではなく構造（ゲートを通るか）**で、点数も増えない。
# `F_hit` の `GATE_FALLBACK`（落ちたら帯15倍）と同じ形の**逆向き**。
#
# 🔴 単体の表示的中を比べてはいけない（母集団が違う）。**同一レースの対比較**で見る。

#: 帯の段（低いほど当たりやすく、計画払戻は小さくなる）。
LADDER = (2.0, 5.0, 8.0, 10.0, 12.0, 15.0, 18.0, 20.0, 22.0, 25.0, 28.0, 30.0)


def lowest_band(x, k: int = K, floor: float = MIN_MEAN_PAYOUT):
    """**ゲートを通る範囲でいちばん低い帯**の14点。通らなければ None。

    🔴 点数は増やさない。動かすのは「どの14点を買うか」だけ。
       ダッチでは平均想定払戻 = 予算 ÷ Σ(1/予測オッズ) なので、帯を下げるほど
       計画払戻が下がる。**2万円ゲートが自動的に下限を決める**。
    """
    for lo in LADDER:
        legs = _band(x, lo, k=k)
        if not legs:
            continue
        st = allocate(legs, x.po_tf, x.pr_tf, PLANS["E_hit"])
        if not st or mean_expected_payout(st, x.po_tf) <= floor:
            continue
        if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
            continue
        return legs
    return None


def _boot(a, b, nb=1000, seed=7):
    ia = np.array([r["inv"] for r in a], float); pa = np.array([r["pay"] for r in a], float)
    ib = np.array([r["inv"] for r in b], float); pb = np.array([r["pay"] for r in b], float)
    sa, sb = (pa > ia).astype(float), (pb > ib).astype(float)
    rng = np.random.default_rng(seed)
    n = len(a)
    ds, dr = np.empty(nb), np.empty(nb)
    for t in range(nb):
        j = rng.integers(0, n, size=n)
        ds[t] = (sb[j].mean() - sa[j].mean()) * 100
        dr[t] = (pb[j].sum() / ib[j].sum() - pa[j].sum() / ia[j].sum()) * 100
    q = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return q(ds), q(dr)


def hybrid() -> None:
    z = C.board()
    axs = z["AXIS_SUM"].astype(float)
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        idx = [int(i) for i in C.select("E", win)
               if axs[int(i)] >= AXIS_GATE_MIN["E_hit"]]
        nd = C.days_of(C.select(None, win))
        cur, alt = [], {"帯なし14点": [], "三連複4点": []}
        for t in (20_000, 25_000, 30_000, 35_000):
            alt[f"最低帯14点(床{t//1000}k)"] = []
        for i in idx:
            x = ctx(i)
            if x is None:
                continue
            c = run(x, "tf", _band(x, 30.0))
            if not c:
                continue
            cur.append(c)
            alt["帯なし14点"].append(run(x, "tf", _band(x, 2.0)))
            alt["三連複4点"].append(run(x, "t3", _trio(x, 4)))
            for t in (20_000, 25_000, 30_000, 35_000):
                lb = lowest_band(x, floor=t)
                alt[f"最低帯14点(床{t//1000}k)"].append(
                    run(x, "tf", lb) if lb else None)
        print("\n" + "=" * 108)
        print(f"███ 型E ハイブリッド（組めるなら差し替え）  {label}  n={len(cur):,}R")
        base = C.summarize(cur, nd)
        print(f"  {'腕':26s} {'差替率':>6s} {'点数':>5s} {'表示的中%':>9s} {'ROI':>6s} "
              f"{'払戻中央':>9s} {'10万+/日':>8s}")
        print(f"  {'現行（帯30+ 14点）':26s} {'':6s} {base['k']:5.1f} "
              f"{base['shown']:8.2f}% {base['roi']:6.1f} {base['med_pay']:9,.0f} "
              f"{base['big_per_day']:8.3f}")
        for name in ("帯なし14点", "最低帯14点(床20k)", "最低帯14点(床25k)",
                     "最低帯14点(床30k)", "最低帯14点(床35k)", "三連複4点"):
            arm = [(a or c) for c, a in zip(cur, alt[name])]
            n_sw = sum(1 for a in alt[name] if a)
            s = C.summarize(arm, nd)
            ci_s, ci_r = _boot(cur, arm)
            print(f"  {'H ' + name + ' 優先':26s} {n_sw/len(cur)*100:5.1f}% {s['k']:5.1f} "
                  f"{s['shown']:8.2f}% {s['roi']:6.1f} {s['med_pay']:9,.0f} "
                  f"{s['big_per_day']:8.3f}")
            print(f"      └ Δ表示的中 [{ci_s[0]:+.2f},{ci_s[1]:+.2f}]pt  "
                  f"ΔROI [{ci_r[0]:+.1f},{ci_r[1]:+.1f}]pt")
            # 差し替えたレースだけの直接対決
            pa = [c for c, a in zip(cur, alt[name]) if a]
            pb = [a for a in alt[name] if a]
            sa, sb = C.summarize(pa, nd), C.summarize(pb, nd)
            print(f"      └ 差し替えたレースだけ: 現行 {sa['shown']:5.2f}% → "
                  f"{sb['shown']:5.2f}%  ROI {sa['roi']:5.1f} → {sb['roi']:5.1f}  "
                  f"払戻中央 {sa['med_pay']:,.0f} → {sb['med_pay']:,.0f}円")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hybrid":
        hybrid()
    else:
        main()
