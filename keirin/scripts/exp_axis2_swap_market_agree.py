#!/usr/bin/env python3
"""◎○一致時に**片方だけ着外**で外すのを減らす（2026-08-23・ユーザー指摘）。

## 指摘

> 二軸とも着外の議論はしておらず、**二軸一致時に片方が着外になって外している
> ケース**が問題。ここは確実に当てる、当てられないなら片方の軸はそれ以外の選手にする。

実際 2026-08-23 の 武雄5R（軸1=◎が5着）・立川4R（軸1=◎が5着）がこの型。

## 失点は軸2に偏っている

◎○完全一致（overlap=2）の実測: 二軸的中 57.06% / 両方着外 6.43% /
**軸1のみ着外 12.65% / 軸2のみ着外 23.86%**。
＝**軸2が外れるケースが軸1の約2倍**。差し替える候補は軸2。

## 腕（母集団は overlap=2 のみ・軸1は本番のまま固定）

| 腕 | 軸2 |
|---|---|
| 現行 | `z(p3) − 0.3×z(bad)` の最上位（軸1を除く） |
| **A** | ペア同時確率 `P(軸1とjがともに3着内)` の最大（○も候補） |
| **B** | 同上だが **○を候補から外す**（＝市場と必ずずらす・ユーザー案） |
| C | `p3` 最上位だが **○を外す**（単純版） |

🔴 一次指標は**二軸的中**。併せて「片方だけ着外」の内訳も出す。
🔴 学習 2024-01〜2025-12 / 検定 2026（`--swap` で逆向き）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis_prod_baseline import load_cache4  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any  # noqa: E402
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from scripts.exp_trio_pair_model import load_entries  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import rank_7s_select_axis, rank_7s_wt_overlap_n  # noqa: E402


def ci_diff(days, B=4000, seed=61):
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
    # 🔴 **堅いレースを除く**（2026-08-23・ユーザー指摘）。商品は狙う払戻レンジの
    #    ために堅いレースを外している（7S は axis_sum <= 1.40）。それを含めた
    #    母集団で測ると失点が薄まり、結論を誤る。
    ap.add_argument("--axis-sum-max", type=float, default=None,
                    help="軸2車の p3 合計の上限（7S 本番は 1.40）")
    args = ap.parse_args()

    te = load_cache4(args.test)
    fin = _load_finishes([r["key"] for r in te])
    te = [r for r in te if r["key"] in fin and len(r["p3"]) >= 7]

    # ペアモデル（学習は2024-25）
    tr = load_any(args.train)
    Xtr, ytr, _ = build_pairs(tr, load_entries([r["key"] for r in tr]),
                              _load_finishes([r["key"] for r in tr]))
    m = fit(Xtr, ytr, args.rounds)
    te_rows = [dict(key=r["key"], date=r["date"], p3=r["p3"],
                    order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c))) for r in te]
    Xte, _, mte = build_pairs(te_rows, load_entries([r["key"] for r in te_rows]), fin)
    pair = defaultdict(dict)
    for (key, _, a, b, _, _), p in zip(mte, m.predict(Xte)):
        pair[key][frozenset((a, b))] = float(p)

    arms = ["現行", "A:ペア最良(制限なし)", "D:◎○を除外(ご提案)", "E:◎○除外・p3順"]
    d = {a: defaultdict(lambda: [0, 0, 0, 0, 0]) for a in arms}
    n = 0
    changed = defaultdict(int)
    for r in te:
        sel = rank_7s_select_axis(r["pw"], r["p3"], r["bad"])
        if sel is None:
            continue
        a1, a2, _ = sel
        mk = r["mark"]
        hon = next((c for c, x in mk.items() if x == 1), None)
        tai = next((c for c, x in mk.items() if x == 2), None)
        # 🔴 **ユーザー規則の母集団はこちら**（2026-08-23 訂正）:
        #    「軸2選出時に**軸1が WT◎○ のいずれか**の場合、軸2に◎○を選ばない」。
        #    overlap==2 限定ではなく **軸1∈{◎,○} の全レース**が対象。
        #    overlap==1（軸1が◎で軸2は無印）も含むので母集団が広い。
        if hon is None or tai is None or a1 not in (hon, tai):
            continue
        if args.axis_sum_max is not None and (r["p3"][a1] + r["p3"][a2]) > args.axis_sum_max:
            continue          # 🔴 堅いレースは商品が売っていない
        pr = pair.get(r["key"])
        if not pr:
            continue
        others = [c for c in r["p3"] if c != a1]
        # 🔴 ユーザー規則: 軸2 の候補から **◎ と ○ の両方**を外す
        no_mk = [c for c in others if c not in (hon, tai)]
        if not no_mk:
            continue
        cand = {
            "現行": a2,
            "A:ペア最良(制限なし)": max(others, key=lambda c: pr.get(frozenset((a1, c)), 0.0)),
            "D:◎○を除外(ご提案)": max(no_mk, key=lambda c: pr.get(frozenset((a1, c)), 0.0)),
            "E:◎○除外・p3順": max(no_mk, key=lambda c: r["p3"][c]),
        }
        t3 = {c for w in winning_trifectas(fin[r["key"]]) for c in w}
        n += 1
        for k, b in cand.items():
            z = d[k][r["date"]]
            z[0] += 1
            z[1] += int(a1 in t3 and b in t3)          # 二軸的中
            z[2] += int(a1 in t3 and b not in t3)      # 軸2のみ着外
            z[3] += int(a1 not in t3 and b in t3)      # 軸1のみ着外
            z[4] += int(a1 not in t3 and b not in t3)  # 両方着外
            if k != "現行":
                changed[k] += int(b != a2)

    lab = "全レース" if args.axis_sum_max is None else f"axis_sum<={args.axis_sum_max}"
    print(f"検定 {n:,}R（軸1が◎か○・軸1は本番のまま固定・{lab}）\n")
    print(f"{'腕':>18}{'二軸的中':>10}{'（対現行）':>26}"
          f"{'軸2のみ着外':>12}{'軸1のみ着外':>12}{'両方着外':>10}{'差替率':>8}")
    base = d["現行"]
    b1 = sum(z[1] for z in base.values()) / n
    for k in arms:
        h = sum(z[1] for z in d[k].values()) / n
        o2 = sum(z[2] for z in d[k].values()) / n
        o1 = sum(z[3] for z in d[k].values()) / n
        bo = sum(z[4] for z in d[k].values()) / n
        if k == "現行":
            print(f"{k:>18}{h:>10.2%}{'':>26}{o2:>12.2%}{o1:>12.2%}{bo:>10.2%}")
            continue
        dd = {x: [base[x][0], base[x][1], d[k][x][1]] for x in base}
        lo, hi = ci_diff(dd)
        f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        c = f"Δ{(h-b1)*100:+.2f}pt[{lo*100:+.2f},{hi*100:+.2f}]{f}"
        print(f"{k:>18}{h:>10.2%}{c:>26}{o2:>12.2%}{o1:>12.2%}{bo:>10.2%}"
              f"{changed[k]/n:>8.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
