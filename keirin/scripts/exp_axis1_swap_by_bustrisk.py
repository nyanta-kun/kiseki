#!/usr/bin/env python3
"""「軸1が入着困難なレースは次点を軸1に繰り上げる」は成立するか（2026-08-23）。

## ユーザー提案

> 軸1が入着困難なレースを判別し、その場合は次点を軸1とし、改めて軸2を選別する

🔴 **まず構造を確認する。** 三連複の二軸的中は**順序ではなく集合**で決まる。
   次点を軸1へ繰り上げても軸2を `p3` で選び直せば残りの最上位＝元の軸1 が入るので、
   ペアは `{1位, 2位}` のままで**現行と完全に同一**。効果が出るのは
   ① 元の軸1を**ペアから外す**（＝`{2位, 3位}` にする）か
   ② **順序が意味を持つ三連単**の場合だけ。両方を測る。

## 測る量（バストリスクの帯ごと）

| 量 | 提案が成立する条件 |
|---|---|
| p3順位ごとの**3着内率** | 高リスク帯で **2位 > 1位** になる帯があるか |
| p3順位ごとの**1着率** | 同上（三連単で軸1を入れ替える根拠） |
| ペアの**二軸的中** | `{2,3}` が `{1,2}` を上回る帯があるか |

🔴 帯は**検出器のパーセンタイル**で切る。「入着困難」を極端に絞った尾
   （上位5%・1%）まで見る。🔴 学習・検定は年をまたぐ独立窓（`--swap` で逆向きも）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import auc, build, load_rich  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any  # noqa: E402

BANDS = [("D1(最も堅い)", 0.0, 0.1), ("D2", .1, .2), ("D3", .2, .3),
         ("D4", .3, .4), ("D5", .4, .5), ("D6", .5, .6), ("D7", .6, .7),
         ("D8", .7, .8), ("D9", .8, .9), ("D10(最も危険)", .9, 1.0),
         ("上位5%", .95, 1.0), ("上位2%", .98, 1.0), ("上位1%", .99, 1.0)]


def ci_diff(days, B=4000, seed=31):
    """days: {date: [n, a, b]} → (Δ=b-a, lo, hi)"""
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return (v[:, 2].sum() / v[:, 0].sum() - v[:, 1].sum() / v[:, 0].sum(),
            d[int(B * .025)], d[int(B * .975)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")

    ent_tr, ent_te = load_rich([r["key"] for r in tr]), load_rich([r["key"] for r in te])
    fin_tr, fin_te = _load_finishes([r["key"] for r in tr]), _load_finishes([r["key"] for r in te])
    _, _, _, Xr_tr, yr_tr, _ = build(tr, ent_tr, fin_tr)
    Xc_te, yc_te, mc_te, Xr_te, yr_te, mr_te = build(te, ent_te, fin_te)
    det = fit(Xr_tr, yr_tr, args.rounds)
    s = det.predict(Xr_te)
    print(f"バスト検出 AUC {auc(yr_te, s):.4f}"
          f"   検定窓の軸1バスト率 {yr_te.mean():.2%}\n")

    # レースごとに {p3順位: (3着内, 1着)}
    race = defaultdict(dict)
    for (key, date, c, rk, isw), t in zip(mc_te, yc_te):
        race[key][rk] = (int(t), int(isw))
    score = {m[0]: float(v) for m, v in zip(mr_te, s)}
    date_of = {m[0]: m[1] for m in mr_te}
    keys = [m[0] for m in mr_te if len(race[m[0]]) == 7]
    qs = np.quantile([score[k] for k in keys], [b[1] for b in BANDS] +
                     [b[2] for b in BANDS])
    nb = len(BANDS)

    print("【p3順位ごとの 3着内率 / 1着率（バストリスクの帯別）】")
    print(f"{'帯':>14}{'件数':>8}" +
          "".join(f"{f'{i}位':>15}" for i in range(1, 6)))
    rows = {}
    for bi, (bn, lo_q, hi_q) in enumerate(BANDS):
        lo, hi = qs[bi], qs[nb + bi]
        sub = [k for k in keys if lo <= score[k] <= hi] if hi_q == 1.0 else \
              [k for k in keys if lo <= score[k] < hi]
        if len(sub) < 200:
            continue
        cells, rows[bn] = [], sub
        for r_ in range(1, 6):
            t3 = np.mean([race[k][r_][0] for k in sub])
            w = np.mean([race[k][r_][1] for k in sub])
            cells.append(f"{t3:.1%}/{w:.1%}")
        print(f"{bn:>14}{len(sub):>8,}" + "".join(f"{c:>15}" for c in cells))

    print("\n【1位 vs 2位 の差（正なら2位のほうが強い＝繰り上げが成立）】")
    print(f"{'帯':>14}{'件数':>8}{'3着内率 Δ(2位−1位)':>30}{'1着率 Δ(2位−1位)':>30}")
    for bn, sub in rows.items():
        d1 = defaultdict(lambda: [0, 0, 0])
        d2 = defaultdict(lambda: [0, 0, 0])
        for k in sub:
            d = date_of[k]
            z = d1[d]; z[0] += 1; z[1] += race[k][1][0]; z[2] += race[k][2][0]
            z = d2[d]; z[0] += 1; z[1] += race[k][1][1]; z[2] += race[k][2][1]
        a, l1, u1 = ci_diff(d1)
        b, l2, u2 = ci_diff(d2)
        f1 = "🟢" if l1 > 0 else ("🔴" if u1 < 0 else "")
        f2 = "🟢" if l2 > 0 else ("🔴" if u2 < 0 else "")
        print(f"{bn:>14}{len(sub):>8,}"
              f"{f'{a*100:+.2f}pt [{l1*100:+.2f},{u1*100:+.2f}]{f1}':>30}"
              f"{f'{b*100:+.2f}pt [{l2*100:+.2f},{u2*100:+.2f}]{f2}':>30}")

    print("\n【ペアの二軸的中（集合で比べる）】")
    print(f"{'帯':>14}{'件数':>8}{'{1,2}現行':>11}{'{2,3}繰上げ':>12}"
          f"{'Δ':>28}{'{1,3}':>9}")
    for bn, sub in rows.items():
        d = defaultdict(lambda: [0, 0, 0])
        h13 = []
        for k in sub:
            r_ = race[k]
            z = d[date_of[k]]
            z[0] += 1
            z[1] += int(r_[1][0] and r_[2][0])
            z[2] += int(r_[2][0] and r_[3][0])
            h13.append(int(r_[1][0] and r_[3][0]))
        a, lo_, hi_ = ci_diff(d)
        n = sum(z[0] for z in d.values())
        p12 = sum(z[1] for z in d.values()) / n
        p23 = sum(z[2] for z in d.values()) / n
        f = "🟢" if lo_ > 0 else ("🔴" if hi_ < 0 else "")
        print(f"{bn:>14}{len(sub):>8,}{p12:>11.2%}{p23:>12.2%}"
              f"{f'{a*100:+.2f}pt [{lo_*100:+.2f},{hi_*100:+.2f}]{f}':>28}"
              f"{np.mean(h13):>9.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
