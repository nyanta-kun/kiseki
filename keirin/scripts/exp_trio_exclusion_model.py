#!/usr/bin/env python3
"""「◎が来るとき3着に**来ない**確率」で相手を削れるか（2026-08-25・ユーザー提案の検証）。

## 提案

> 入着する選手を抽出するモデル・順位を見るモデルは作った。だが払戻額を増やすには
> 1点あたりの金額を増やしたい。そこで「来ない側」、しかも **◎が来るなら3着には
> 来ない確率** を出すモデルを作り、相手を減らせないか。

主張は2段に分かれるので分けて測る。

  (Q1) 「◎が来る条件つきの不来確率」は、いま相手切りに使っている **周辺 p3**
       （`rank_7c_select_legs` の `p3 >= 0.15`）より相手を良く並べ替えるか
  (Q2) 点数を減らすと（1レース予算は `RACE_BUDGET` 固定なので1点あたりが増える）
       払戻・表示的中・ROI はどう動くか

🔴 **Q1 は数学的にほぼ答えが出ている**（§24）。軸2車がレース内で固定なら
   `argmin P(相手が3着外 | 軸)` = `argmax P(相手が3着内 | 軸)` = `argmax P(三者同時)`
   で、**§24 腕A とまったく同じ並べ替え**になる。本スクリプトはそれでも
   「学習母集団を条件つき側に絞る」形（ME/ME2）が別物として効くかを実測する。

## 腕

| 腕 | 相手の並べ替え | 学習母集団 |
|---|---|---|
| P3 | 周辺 p3 降順（**現行**） | — |
| MJ | `P(三者同時3着内)` 降順（§24 腕A） | 全レース |
| ME | `P(相手が3着外 \| ◎が3着内)` 昇順 | **◎が3着内のレースのみ** |
| ME2 | `P(相手が3着外 \| ◎○とも3着内)` 昇順 | **二軸そろいのレースのみ** |

⚠️ 学習は `--train`、検定は `--test`。`--swap` で逆向き。年をまたぐ独立窓。
⚠️ 1次指標は的中率と表示的中（払戻>賭け金）。ROI は市場が織り込むので必ず分ける。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    FEATS_A, _adj, _same, day_ci, fit, load_any, load_boards, load_entries,
    race_context,
)
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEG_P3_MIN, RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN, unit_stake,
)

PAYOUT_RATE = 0.7485


def build(races, ent, fins):
    """軸2車固定・相手5候補（1レース5行）。§24 腕A と同一の特徴量。

    ラベルは3種を返す:  y_trio（三者同時＝買い目的中）/ y_c（相手が3着内）
                        a1_hit / pair_hit（学習母集団を絞るため）
    """
    X, meta = [], []
    y_trio, y_c, a1h, ph = [], [], [], []
    for r in races:
        e = ent.get(r["key"]); o3 = fins.get(r["key"])
        if not e or not o3:
            continue
        o, p3 = r["order"], r["p3"]
        if len(o) < 7 or len(e) < 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        top3 = {c for w in winning_trifectas(o3) for c in w}
        ctx = race_context(o, p3, e)
        a1, a2 = o[0], o[1]
        vals = [p3[c] for c in o]
        for i in range(2, 7):
            c = o[i]
            nxt = vals[i + 1] if i + 1 < len(vals) else 0.0
            ec = e[c]
            X.append([
                p3[c], float(i + 1), p3[a2] - p3[c], p3[c] - nxt,
                p3[c] / max(p3[a2], 1e-9),
                p3[a1], p3[a2], ctx["axis_sum"], p3[a1] * p3[a2] * p3[c],
                p3[a1] + p3[a2] + p3[c],
                _same(e, a1, c), _same(e, a2, c), _adj(e, a1, c), _adj(e, a2, c),
                ec["lsize"], ec["lp"], ec["leader"], ec["rp"],
                abs(ec["rp"] - e[a2]["rp"]),
                ctx["n_lines"], ctx["max_lsize"], ctx["p3_ent"], ctx["p3_std"],
                _same(e, a1, a2), int(_same(e, a1, a2) and
                                      {e[a1]["lp"], e[a2]["lp"]} == {1.0, 2.0}),
                float(len({e[x]["lg"] for x in (a1, a2, c)})),
            ])
            y_trio.append(int(frozenset((a1, a2, c)) in wins))
            y_c.append(int(c in top3))
            a1h.append(int(a1 in top3))
            ph.append(int(a1 in top3 and a2 in top3))
            meta.append((r["key"], r["date"], a1, a2, c, i + 1, p3[c],
                         p3[a1] + p3[a2]))
    return (np.array(X, dtype=np.float32), np.array(y_trio, dtype=np.int8),
            np.array(y_c, dtype=np.int8), np.array(a1h, dtype=np.int8),
            np.array(ph, dtype=np.int8), meta)


def bootstrap_days(days, B=4000, seed=41):
    """日ブロック bootstrap: days=[(bet, pay_ref, pay_alt)] → Δ の 95%CI と alt 下限。"""
    return day_ci(days, B=B, seed=seed)


def summarize(rows, stake_of):
    """rows=[(date, n_legs, hit, payout, bet)] → 集計辞書。"""
    n = len(rows)
    if not n:
        return None
    bet = sum(r[4] for r in rows)
    pay = sum(r[3] for r in rows)
    hit = sum(1 for r in rows if r[2]) / n
    disp = sum(1 for r in rows if r[3] > r[4]) / n          # 表示的中（ガミ除き）
    big2 = sum(1 for r in rows if r[3] >= 20000)
    pl = sorted(r[3] for r in rows if r[2])
    days = len({r[0] for r in rows})
    return dict(n=n, bet=bet, pay=pay, roi=pay / max(bet, 1), hit=hit,
                disp=disp, med=(np.median(pl) if pl else 0.0),
                big2_per_day=big2 / max(days, 1), days=days,
                legs=float(np.mean([r[1] for r in rows])),
                per_race=bet / n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio7_cache_wf_train.jsonl")
    ap.add_argument("--test", default="data/exp/trio7_cache_wf_test.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    ap.add_argument("--pop", default="all", choices=["all", "7c"],
                    help="all=7車全レース / 7c=7Cのゲート近似（軸2車合計・相手4点以上）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr

    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）"
          f"  母集団={args.pop}")

    ent_tr = load_entries([r["key"] for r in tr])
    ent_te = load_entries([r["key"] for r in te])
    fin_tr = _load_finishes([r["key"] for r in tr])
    fin_te = _load_finishes([r["key"] for r in te])
    board = load_boards([r["key"] for r in te])

    Xtr, ytr_trio, ytr_c, a1h_tr, ph_tr, _ = build(tr, ent_tr, fin_tr)
    Xte, yte_trio, yte_c, a1h_te, ph_te, mte = build(te, ent_te, fin_te)
    print(f"候補行 学習 {len(Xtr):,} / 検定 {len(Xte):,}"
          f"   買い目的中率 学習 {ytr_trio.mean():.2%} / 検定 {yte_trio.mean():.2%}")
    print(f"  ◎(=p3 1位)が3着内     : 学習 {a1h_tr.mean():.2%} / 検定 {a1h_te.mean():.2%}")
    print(f"  二軸そろい            : 学習 {ph_tr.mean():.2%} / 検定 {ph_te.mean():.2%}")

    # ── 3モデル ──
    mJ = fit(Xtr, ytr_trio, args.rounds)
    sJ = mJ.predict(Xte)                                  # 大きいほど買う

    selE = a1h_tr == 1
    mE = fit(Xtr[selE], 1 - ytr_c[selE], args.rounds)      # ◎来る条件で「来ない」確率
    sE = -mE.predict(Xte)                                  # 昇順→符号反転で「大きいほど買う」

    selE2 = ph_tr == 1
    mE2 = fit(Xtr[selE2], 1 - ytr_c[selE2], args.rounds)
    sE2 = -mE2.predict(Xte)

    print(f"\n学習母集団: MJ {len(Xtr):,}行 / ME {selE.sum():,}行 "
          f"({selE.mean():.0%}) / ME2 {selE2.sum():,}行 ({selE2.mean():.0%})")

    # ── レース単位に畳む ──
    by_race = defaultdict(list)
    info = {}
    for (key, date, a1, a2, c, rk, p3c, asum), j, e1, e2, t in zip(
            mte, sJ, sE, sE2, yte_trio):
        by_race[key].append(dict(c=c, rk=rk, p3=p3c, t=int(t),
                                 P3=p3c, MJ=float(j), ME=float(e1), ME2=float(e2)))
        info[key] = (date, a1, a2, asum)

    arms = ["P3", "MJ", "ME", "ME2"]
    KS = [1, 2, 3, 4, 5]
    rows = defaultdict(list)          # (arm,k) -> [(date,legs,hit,pay,bet)]
    prod_rows = []                    # 現行（p3>=0.15 の可変点数）
    agree = defaultdict(lambda: defaultdict(int))
    n_eval = 0
    excluded_odds = 0

    for key, v in by_race.items():
        if len(v) != 5:
            continue
        bd = board.get(key)
        if not bd:
            continue
        date, a1, a2, asum = info[key]
        # 全買い目のオッズが盤面にあるレースだけ（腕間の母集団を揃える）
        ks = {frozenset((a1, a2, x["c"])) for x in v}
        if any(k not in bd for k in ks):
            excluded_odds += 1
            continue
        prod_legs = [x for x in sorted(v, key=lambda z: (-z["p3"], z["c"]))
                     if x["p3"] >= RANK_7C_LEG_P3_MIN]
        if args.pop == "7c":
            if asum < RANK_7C_P3_SUM_MIN or len(prod_legs) < RANK_7C_LEGS_MIN:
                continue
        n_eval += 1

        def buy(legs):
            k = len(legs)
            st = unit_stake(k)
            bet = st * k
            pay = sum(int(bd[frozenset((a1, a2, x["c"]))] * 100) * st // 100
                      for x in legs if x["t"])
            return (date, k, any(x["t"] for x in legs), pay, bet)

        if prod_legs:
            prod_rows.append(buy(prod_legs))

        order_by = {a: sorted(v, key=lambda z: (-z[a], z["c"])) for a in arms}
        for a in arms:
            for k in KS:
                rows[(a, k)].append(buy(order_by[a][:k]))
            for k in KS:
                agree[a][k] += len({x["c"] for x in order_by[a][:k]}
                                   & {x["c"] for x in order_by["P3"][:k]})

    print(f"\n【評価対象 {n_eval:,}R】（盤面欠けで除外 {excluded_odds:,}R）")

    # ── 現行（可変点数）──
    ps = summarize(prod_rows, None)
    print(f"\n〈現行の相手切り: p3 >= {RANK_7C_LEG_P3_MIN}（可変点数）〉")
    print(f"  平均{ps['legs']:.2f}点 / 1R投資{ps['per_race']:,.0f}円 / "
          f"的中{ps['hit']:.2%} / 表示的中{ps['disp']:.2%} / ROI{ps['roi']:.1%} / "
          f"払戻中央{ps['med']:,.0f}円 / 2万+ {ps['big2_per_day']:.2f}件per日")

    # ── 点数 × 腕 ──
    print(f"\n【点数 × 並べ替え】1レース {ps['per_race']:,.0f}円相当を点数で割る")
    hdr = f"{'腕':>5}{'点':>3}{'的中%':>8}{'表示的中%':>10}{'ROI':>8}" \
          f"{'ROI下限':>9}{'払戻中央':>10}{'2万+件/日':>10}{'P3一致':>8}"
    print(hdr)
    base = {}
    for k in KS:
        agg = summarize(rows[("P3", k)], None)
        base[k] = agg
    for a in arms:
        for k in KS:
            agg = summarize(rows[(a, k)], None)
            byd_a = defaultdict(lambda: [0.0, 0.0])
            byd_b = defaultdict(lambda: [0.0, 0.0])
            for (d, _, _, p, b), (d2, _, _, p2, b2) in zip(
                    rows[(a, k)], rows[("P3", k)]):
                byd_a[d][0] += b; byd_a[d][1] += p
                byd_b[d][0] += b2; byd_b[d][1] += p2
            dd = [(byd_a[d][0], byd_b[d][1], byd_a[d][1]) for d in byd_a]
            _, _, lo = bootstrap_days(dd)
            mk = " 🟢" if lo > PAYOUT_RATE else ""
            ag = agree[a][k] / (n_eval * k)
            print(f"{a:>5}{k:>3}{agg['hit']:>8.2%}{agg['disp']:>10.2%}"
                  f"{agg['roi']:>8.1%}{lo:>9.1%}{agg['med']:>10,.0f}"
                  f"{agg['big2_per_day']:>10.2f}{ag:>8.0%}{mk}")
        print()

    # ── 腕の対応差（同一点数・同一レース）──
    print("【同点数での対応差（vs P3）】")
    for a in ["MJ", "ME", "ME2"]:
        for k in KS:
            byd_a = defaultdict(lambda: [0.0, 0.0, 0])
            byd_b = defaultdict(lambda: [0.0, 0.0, 0])
            for (d, _, h, p, b), (_, _, h2, p2, b2) in zip(
                    rows[(a, k)], rows[("P3", k)]):
                z = byd_a[d]; z[0] += b; z[1] += p; z[2] += int(h)
                z = byd_b[d]; z[0] += b2; z[1] += p2; z[2] += int(h2)
            dd_r = [(byd_a[d][0], byd_b[d][1], byd_a[d][1]) for d in byd_a]
            dd_h = [(byd_a[d][0] / max(unit_stake(k) * k, 1),
                     byd_b[d][2], byd_a[d][2]) for d in byd_a]
            lo_r, hi_r, _ = bootstrap_days(dd_r)
            lo_h, hi_h, _ = bootstrap_days(dd_h)
            ha = summarize(rows[(a, k)], None)
            hb = base[k]
            print(f"  {a:>4} k={k}  的中Δ{(ha['hit']-hb['hit'])*100:+6.2f}pt"
                  f" [{lo_h*100:+.2f},{hi_h*100:+.2f}]"
                  f"{'🟢' if lo_h > 0 else '  '}"
                  f"   ROIΔ{(ha['roi']-hb['roi'])*100:+6.1f}pt"
                  f" [{lo_r*100:+.1f},{hi_r*100:+.1f}]"
                  f"{'🟢' if lo_r > 0 else '  '}")
        print()

    if args.out:
        Path(args.out).write_text(json.dumps(
            {f"{a}_{k}": summarize(rows[(a, k)], None) for a in arms for k in KS},
            ensure_ascii=False, default=float, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
