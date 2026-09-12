#!/usr/bin/env python3
"""型E: 「順序が読めないレースだけ三連複へ」条件別の買い分け（2026-09-03・ユーザー提案）。

## 発端

> 今日も昨日も一昨日も型E の当たりが出ていない。現状の条件では正しいが
> **惜しい組み合わせは見えている**ので、条件による買い分けで的中率・ROI を上げられないか。

実入稿の直近12件の外れを解剖すると、**6件（50%）は3車を当てている**
（4件は順序違い・2件は3車とも上位だが帯30倍+で目が落ちていた）。
台上の統計（`type_e_2026_09_01.md` §9）とも一致（順序違いのみ 44.7/41.9%）。

## 既に測ってあること（再検証しない）

- 三連複4点ハイブリッド（**無条件**）: Δ表示的中 +4.50/+3.22pt・件/日不変・
  ΔROI は窓で反転（+3.3/−8.4）・払戻中央 −28%・10万+ −64% → 方針「hit は三連単」で不採用
- 「決着の型を事前予測して買い分け」: OOS AUC 0.539/0.559・**オラクルでも天井 +1.8/+2.2pt**
- 条件別に帯を変える / 型E内 axis_sum・gap・pw_max・po_min・GRADE・種別: すべて否定

## ここで新しく測ること

**「順序の読めなさ」を事前に測る量**は否定リストに無い。無条件ハイブリッドが
+3.2〜4.5pt 出ている以上、**読めない側だけに絞れれば代償（払戻中央・看板）を
半分に抑えて同じ利得を取れる**はず。それができるかを対照つきで見る。

  ord_conc  … 最有力の三連複集合について「その6順列のうち最大順列が占める割合」
  ord_top2  … 同・上位2順列の合計
  ord_w8    … 上位8集合の加重平均（型E が張るのは8集合なので本命の定義）
  rp_sd     … 競走得点のレース内SD（否定リストに無い・axis_sum と相関0.220）

🔴 **無作為に同数を三連複へ振った対照20本**に勝つかで判定する（条件が働いているか）。
🔴 確認窓(2026)が本番相当。ROI 単独では決めない。
"""
from __future__ import annotations
import sys, random, itertools
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import ctx, MIN_MEAN_PAYOUT, MIN_POINT_ODDS
from src.type_lab import PLANS, Plan, allocate, build_legs, mean_expected_payout

TRIO4 = Plan("x", "E", "trio", "axis2_flow", 4, alloc="dutch")
E_HIT = PLANS["E_hit"]


def run(x, plan: Plan):
    pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod))


def conds(x) -> dict:
    """事前に分かる「順序の読めなさ」。小さいほど読めない。"""
    sets = sorted(x.pr_t3.items(), key=lambda kv: -kv[1])[:8]
    out = {}
    def share(s, n):
        perms = sorted((x.pr_tf.get(p, 0.0) for p in itertools.permutations(sorted(s))),
                       reverse=True)
        tot = sum(perms)
        return (sum(perms[:n]) / tot) if tot > 0 else 0.0
    out["ord_conc 最有力集合の最大順列比"] = share(sets[0][0], 1)
    out["ord_top2 同・上位2順列"] = share(sets[0][0], 2)
    w = sum(v for _, v in sets) or 1.0
    out["ord_w8 上位8集合の加重平均"] = sum(v * share(s, 1) for s, v in sets) / w
    return out


def main() -> None:
    z = C.board()
    rp = z["A_race_point"].astype(float)
    QS = (0.2, 0.33, 0.5)
    for label, win in (("探索 2024-07〜2025-12 (in-sample)", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        nd = C.days_of(C.select(None, win))
        rows = []
        for i in [int(v) for v in C.select("E", win)]:
            x = ctx(i)
            if x is None:
                continue
            r = dict(tf=run(x, E_HIT), t3=run(x, TRIO4))
            r.update(conds(x))
            r["rp_sd 実力伯仲(小=伯仲)"] = float(rp[i].std())
            rows.append(r)
        rows = [r for r in rows if r["tf"]]          # 現行が組めるレースだけで比べる
        print("\n" + "=" * 118)
        print(f"███ 型E {label}   n={len(rows):,}R / {nd}日")
        print(C.HEAD + "  10万+/日")
        base = C.summarize([r["tf"] for r in rows], nd)
        print(C.line("現行 三連単14点", base) + f"  {base['big_per_day']:8.3f}")
        allhyb = [(r["t3"] or r["tf"]) for r in rows]
        s = C.summarize(allhyb, nd)
        print(C.line("無条件 三連複4点ハイブリッド", s) + f"  {s['big_per_day']:8.3f}")
        print(f"    （三連複へ振れた割合 {sum(1 for r in rows if r['t3'])/len(rows)*100:.1f}%）")

        keys = [k for k in rows[0] if k not in ("tf", "t3")]
        for key in keys:
            v = np.array([r[key] for r in rows])
            print(f"\n  ▼ 条件 {key}")
            for q in QS:
                thr = float(np.quantile(v, q))
                sel = v <= thr           # 小さい側＝読めない側を三連複へ
                arm = [(r["t3"] or r["tf"]) if s_ else r["tf"]
                       for r, s_ in zip(rows, sel)]
                sa = C.summarize(arm, nd)
                nsw = sum(1 for r, s_ in zip(rows, sel) if s_ and r["t3"])
                # 無作為に同数(実際に振替が起きた数)を三連複へ振る対照
                idx_t3 = [j for j, r in enumerate(rows) if r["t3"]]
                ws = wr = 0
                ms, mr = [], []
                for seed in range(20):
                    rng = random.Random(seed * 977 + 13)
                    pick = set(rng.sample(idx_t3, min(nsw, len(idx_t3))))
                    ctrl = [(rows[j]["t3"] if j in pick else rows[j]["tf"])
                            for j in range(len(rows))]
                    cs = C.summarize(ctrl, nd)
                    ms.append(cs["shown"]); mr.append(cs["roi"])
                    ws += sa["shown"] > cs["shown"]; wr += sa["roi"] > cs["roi"]
                print(C.line(f"  下位{int(q*100)}%を三連複({nsw}R振替)", sa)
                      + f"  {sa['big_per_day']:8.3f}")
                print(f"      └ 無作為同数20本 中央 表示的中 {np.median(ms):5.2f}% / "
                      f"ROI {np.median(mr):5.1f}  →  条件が勝ち "
                      f"表示的中 {ws}/20・ROI {wr}/20")


if __name__ == "__main__":
    main()
