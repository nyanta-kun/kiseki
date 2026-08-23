#!/usr/bin/env python3
"""◎○が信頼できるレースかを判定し、**信頼できない時だけ軸2を置き換える**。

## ユーザー指摘（2026-08-23）

> 単純な分析ではそのような結論になったのだと思う。当然◎◯が強いのは事実だが、
> その2車を選択することによって外れているケースが多数ある。
> **この指定になった際に信頼可能な◎◯のレースなのか、信頼できず軸２を置き換える
> レースなのか判断する**方がセンスが良い。

🔴 一律に「◎○を外す」は測って −5.42pt（軸2のみ着外 30.40→35.82%）だった。
   だが**それは全部を置き換えた場合**の数字で、
   「置き換えるべきレースだけ置き換える」を否定してはいない。

## 設計

- 母集団: **軸1∈{◎,○} ∧ 軸2∈{◎,○}**（＝二軸が◎○）∧ `axis_sum <= 1.40`
  （🔴 堅いレースは商品が売っていないので必ず除く・2026-08-23 の是正）
- 信頼度モデル: 目的＝**二軸的中**（両方3着内）。特徴はすべて**オッズ公開前**。
  🔴 **日で交互の2分割による交差適合**でスコアを作る（自分を in-sample で
     採点しない。年で割ると窓が1年しか無い向きで fold が作れない）。
- 信頼度の十分位ごとに、現行 / D(◎○除外) / A(ペア最良) の二軸的中を比べる。

**ユーザー仮説が成立する条件**: 信頼度が低い帯で **D > 現行**。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import RACE_FEATS, build as build_race  # noqa: E402
from scripts.exp_axis1_bust_stratified import load_rich  # noqa: E402
from scripts.exp_axis_prod_baseline import load_cache4  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any  # noqa: E402
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from scripts.exp_trio_pair_model import load_entries  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import rank_7s_select_axis  # noqa: E402

EXTRA = ["p3_a1", "p3_a2", "pw_a1", "pw_a2", "bad_a1", "bad_a2",
         "axis_sum", "p3_gap_a2_3rd", "a1_is_hon", "same_line_axes",
         "mark3_in_top3", "n_marked"]


def ci_diff(days, B=3000, seed=83):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--axis-sum-max", type=float, default=1.40)
    args = ap.parse_args()

    te = load_cache4(args.test)
    fin = _load_finishes([r["key"] for r in te])
    te = [r for r in te if r["key"] in fin and len(r["p3"]) >= 7]
    ent = load_rich([r["key"] for r in te])

    # ペアモデル（学習は 2024-25・完全に別窓）
    tr = load_any(args.train)
    Xtr, ytr, _ = build_pairs(tr, load_entries([r["key"] for r in tr]),
                              _load_finishes([r["key"] for r in tr]))
    pm = fit(Xtr, ytr, args.rounds)
    te_rows = [dict(key=r["key"], date=r["date"], p3=r["p3"],
                    order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c))) for r in te]
    Xp, _, mp = build_pairs(te_rows, load_entries([r["key"] for r in te_rows]), fin)
    pair = defaultdict(dict)
    for (key, _, a, b, _, _), p in zip(mp, pm.predict(Xp)):
        pair[key][frozenset((a, b))] = float(p)

    # レース単位の特徴（オッズ非依存）
    _, _, _, Xr, _, mr = build_race(te_rows, ent, fin)
    race_x = {m[0]: x for m, x in zip(mr, Xr)}

    rows = []
    for r in te:
        sel = rank_7s_select_axis(r["pw"], r["p3"], r["bad"])
        if sel is None or r["key"] not in race_x or r["key"] not in pair:
            continue
        a1, a2, _ = sel
        mk = r["mark"]
        hon = next((c for c, x in mk.items() if x == 1), None)
        tai = next((c for c, x in mk.items() if x == 2), None)
        if hon is None or tai is None:
            continue
        if a1 not in (hon, tai) or a2 not in (hon, tai):
            continue                      # 二軸が◎○のレースだけ
        p3 = r["p3"]
        if p3[a1] + p3[a2] > args.axis_sum_max:
            continue                      # 🔴 堅いレースは売っていない
        others = [c for c in p3 if c != a1]
        no_mk = [c for c in others if c not in (hon, tai)]
        if not no_mk:
            continue
        vals = sorted(p3.values(), reverse=True)
        e1, e2 = ent[r["key"]][a1], ent[r["key"]][a2]
        t3 = {c for w in winning_trifectas(fin[r["key"]]) for c in w}
        mark3 = next((c for c, x in mk.items() if x == 3), None)
        extra = [p3[a1], p3[a2], r["pw"][a1], r["pw"][a2],
                 r["bad"][a1], r["bad"][a2], p3[a1] + p3[a2],
                 p3[a2] - (vals[2] if len(vals) > 2 else 0.0),
                 float(a1 == hon),
                 float(e1["lg"] is not None and e1["lg"] == e2["lg"]),
                 float(mark3 in t3) * 0,          # 結果由来は使わない（常に0）
                 float(sum(1 for v in mk.values() if v))]
        rows.append(dict(
            key=r["key"], date=r["date"],
            x=np.concatenate([race_x[r["key"]], np.array(extra, np.float32)]),
            y=int(a1 in t3 and a2 in t3),
            cur=a2,
            D=max(no_mk, key=lambda c: pair[r["key"]].get(frozenset((a1, c)), 0.0)),
            A=max(others, key=lambda c: pair[r["key"]].get(frozenset((a1, c)), 0.0)),
            a1_in=int(a1 in t3), t3=t3, a1=a1))
    print(f"母集団 {len(rows):,}R（二軸が◎○ ∧ axis_sum<={args.axis_sum_max}）")
    print(f"  現行の二軸的中 {np.mean([r['y'] for r in rows]):.2%}\n")

    # 🔴 日で交互の2分割で交差適合（自分を in-sample で採点しない）
    days = sorted({r["date"] for r in rows})
    fold = {d: i % 2 for i, d in enumerate(days)}
    X = np.array([r["x"] for r in rows], np.float32)
    y = np.array([r["y"] for r in rows], np.int8)
    f = np.array([fold[r["date"]] for r in rows])
    score = np.zeros(len(rows))
    for k in (0, 1):
        m = f == k
        score[m] = fit(X[~m], y[~m], args.rounds).predict(X[m])

    # 🔴 裾を細かく見る。「信頼できないレースだけ置き換える」が成立するなら
    #    いちばん信頼の低い一部で D > 現行 になるはず。
    BANDS = [("最下位 5%", 0.0, .05), ("最下位10%", 0.0, .10),
             ("最下位20%", 0.0, .20), ("Q2", .2, .4), ("Q3", .4, .6),
             ("Q4", .6, .8), ("Q5 高", .8, 1.0)]
    qs_all = np.quantile(score, [b[1] for b in BANDS] + [b[2] for b in BANDS])
    nb = len(BANDS)
    print(f"{'信頼度':>10}{'件数':>8}{'現行':>9}{'D:◎○除外':>12}"
          f"{'（対現行）':>24}{'A:ペア最良':>12}{'（対現行）':>24}")
    for i, (bn, _, hq) in enumerate(BANDS):
        lo, hi = qs_all[i], qs_all[nb + i]
        sub = [r for r, s in zip(rows, score) if lo <= s <= hi] if hq == 1.0 else \
              [r for r, s in zip(rows, score) if lo <= s < hi]
        if len(sub) < 100:
            continue
        d = defaultdict(lambda: [0, 0, 0, 0])
        for r in sub:
            z = d[r["date"]]
            z[0] += 1
            z[1] += r["y"]
            z[2] += int(r["a1_in"] and r["D"] in r["t3"])
            z[3] += int(r["a1_in"] and r["A"] in r["t3"])
        n = len(sub)
        hc = sum(z[1] for z in d.values()) / n
        hd = sum(z[2] for z in d.values()) / n
        ha = sum(z[3] for z in d.values()) / n
        ld, ud = ci_diff({k_: [v[0], v[1], v[2]] for k_, v in d.items()})
        la, ua = ci_diff({k_: [v[0], v[1], v[3]] for k_, v in d.items()})
        fd = "🟢" if ld > 0 else ("🔴" if ud < 0 else "")
        fa = "🟢" if la > 0 else ("🔴" if ua < 0 else "")
        print(f"{bn:>10}"
              f"{n:>8,}{hc:>9.2%}{hd:>12.2%}"
              f"{f'Δ{(hd-hc)*100:+.2f}[{ld*100:+.1f},{ud*100:+.1f}]{fd}':>24}"
              f"{ha:>12.2%}"
              f"{f'Δ{(ha-hc)*100:+.2f}[{la*100:+.1f},{ua*100:+.1f}]{fa}':>24}")
    imp = sorted(zip(RACE_FEATS + EXTRA,
                     fit(X, y, args.rounds).feature_importance("gain")),
                 key=lambda x: -x[1])
    print("\n  信頼度の寄与上位: " + " / ".join(k for k, _ in imp[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
