#!/usr/bin/env python3
"""軸1 の選び方を初めて検証する（引き継ぎの未着手項目 2）。

## ユーザー指摘（2026-08-23）

> 軸1 を上げれば二軸的中の底上げになるのではないか

🔴 **軸1 は一貫して `p3` の最大値を採るだけで、選び方を一度も検証していない。**
   軸2 は 4ヘッドすべてで比較済み（どれでも 65〜67%＝ノイズ）だが、
   軸1 は比較対象を置いたことすらない。3着内率 79.3%・外れ 20.7%。

## 問い

`p3` は「その車が3着以内に入る確率」の**限界確率**として学習されている。
軸1 に必要なのも同じ量なので、素直に考えれば `p3` の argmax が最適のはず。
**それでも上がる余地があるとしたら、`p3` が使っていない情報が残っている場合**——
ライン構造・競走得点・レース全体の形（`p3` は車ごとに独立に出るので
「そのレースの中でどれくらい抜けているか」は入っていない）。

そこで **同じ目的変数（その車が3着内か）を、レース内の相対量とライン構造つきで
学習し直し**、argmax が `p3` の argmax を上回るかを見る。

| 腕 | 軸1 の選び方 |
|---|---|
| 現行 | `p3` の最大 |
| **M** | 再学習した3着内モデルの最大 |
| 参考 | 競走得点の最大 |
| 参考 | 無作為（7車から1車） |

さらに **二軸まで伸ばした効果**（M の上位2車）も出す。ユーザーの見立て
「軸1を上げれば二軸の底上げになる」が成り立つかは、そこで初めて分かる。

🔴 学習と検定は**年をまたぐ独立窓**（`--swap` で逆向きも確認する）。
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
from scripts.exp_trio_joint_partner import (  # noqa: E402
    day_ci, fit, load_any, load_entries, race_context)
from src.result_top3 import winning_trifectas  # noqa: E402

FEATS = [
    "p3", "rank", "p3_share", "gap_up", "gap_dn", "p3_minus_mean",
    "p3_minus_2nd", "z_in_race",
    "rp", "rp_rank", "rp_gap_top", "rp_z",
    "lsize", "lpos", "leader", "line_p3_sum", "line_p3_rank", "line_best_rank",
    "n_lines", "max_lsize", "p3_ent", "p3_std", "axis_sum", "n_cars",
]


def build(races, ent, fins):
    """1レース7行（全車）。目的変数＝その車が3着内に入ったか。"""
    X, y, meta = [], [], []
    for r in races:
        e = ent.get(r["key"]); o3 = fins.get(r["key"])
        if not e or not o3:
            continue
        o, p3 = r["order"], r["p3"]
        if len(o) < 7 or len(e) < 7:
            continue
        top3 = {c for w in winning_trifectas(o3) for c in w}
        ctx = race_context(o, p3, e)
        vals = np.array([p3[c] for c in o], dtype=float)
        mean, std, tot = vals.mean(), max(vals.std(), 1e-9), max(vals.sum(), 1e-9)
        rps = {c: e[c]["rp"] for c in o}
        rp_sorted = sorted(rps.values(), reverse=True)
        rp_arr = np.array(list(rps.values()), dtype=float)
        rp_mean, rp_std = rp_arr.mean(), max(rp_arr.std(), 1e-9)
        # ライン単位の集計
        line_p3 = defaultdict(float)
        line_best = {}
        for i, c in enumerate(o):
            g = e[c]["lg"]
            line_p3[g] += p3[c]
            if g not in line_best:
                line_best[g] = i + 1
        lorder = sorted(line_p3, key=lambda g: -line_p3[g])
        lrank = {g: i + 1 for i, g in enumerate(lorder)}
        for i, c in enumerate(o):
            g = e[c]["lg"]
            X.append([
                p3[c], float(i + 1), p3[c] / tot,
                (p3[o[i - 1]] - p3[c]) if i > 0 else 0.0,
                (p3[c] - p3[o[i + 1]]) if i + 1 < len(o) else 0.0,
                p3[c] - mean, p3[c] - p3[o[1]], (p3[c] - mean) / std,
                rps[c], float(rp_sorted.index(rps[c]) + 1),
                rp_sorted[0] - rps[c], (rps[c] - rp_mean) / rp_std,
                e[c]["lsize"], e[c]["lp"], e[c]["leader"],
                line_p3[g], float(lrank[g]), float(line_best[g]),
                ctx["n_lines"], ctx["max_lsize"], ctx["p3_ent"], ctx["p3_std"],
                ctx["axis_sum"], float(len(o)),
            ])
            y.append(int(c in top3))
            meta.append((r["key"], r["date"], c, i + 1))
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int8), meta


def _summ(name, days, ref=None):
    """days: {date: [n, hit]}"""
    n = sum(v[0] for v in days.values())
    h = sum(v[1] for v in days.values())
    line = f"{name:>18}{h/n:>10.2%}"
    if ref is not None:
        dd = [(days[d][0], ref[d][1], days[d][1]) for d in days]
        lo, hi, _ = day_ci(dd)
        rh = sum(v[1] for v in ref.values()) / n
        mk = "  🟢有意に改善" if lo > 0 else ("  🔴有意に悪化" if hi < 0
                                            else "  （有意でない）")
        line += f"   Δ{(h/n-rh)*100:+.2f}pt [{lo*100:+.2f},{hi*100:+.2f}]{mk}"
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    # 🔴 **商品が売らない堅いレースを除く**（2026-08-23 の是正）。
    #    初出時は全レースで測っており、7S の母集団（axis_sum<=1.40）ではなかった。
    #    ◎○の分析では、堅いレースを含めるか外すかで結論が逆転した実例がある。
    ap.add_argument("--axis-sum-max", type=float, default=None)
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")

    ent_tr = load_entries([r["key"] for r in tr])
    ent_te = load_entries([r["key"] for r in te])
    fin_tr = _load_finishes([r["key"] for r in tr])
    fin_te = _load_finishes([r["key"] for r in te])
    if args.axis_sum_max is not None:
        def _firm(rows):
            out = []
            for r in rows:
                o = r["order"]
                if len(o) >= 2 and r["p3"][o[0]] + r["p3"][o[1]] <= args.axis_sum_max:
                    out.append(r)
            return out
        tr, te = _firm(tr), _firm(te)
        print(f"堅いレースを除外（p3上位2合計<={args.axis_sum_max}）: "
              f"学習 {len(tr):,}R / 検定 {len(te):,}R")
        ent_tr = {k: v for k, v in ent_tr.items()}
        ent_te = {k: v for k, v in ent_te.items()}
    Xtr, ytr, _ = build(tr, ent_tr, fin_tr)
    Xte, yte, mte = build(te, ent_te, fin_te)
    print(f"車行 学習 {len(Xtr):,} / 検定 {len(Xte):,}"
          f"   3着内率 学習 {ytr.mean():.2%} / 検定 {yte.mean():.2%}\n")
    m = fit(Xtr, ytr, args.rounds)
    pred = m.predict(Xte)

    by_race = defaultdict(list)
    date_of = {}
    for (key, date, c, rk), p, t in zip(mte, pred, yte):
        by_race[key].append((float(p), c, rk, int(t)))
        date_of[key] = date

    rng = np.random.default_rng(5)
    rp_of = {}
    arms = ["現行(p3最大)", "M:再学習モデル", "参考(競走得点最大)", "参考(無作為)"]
    d1 = {a: defaultdict(lambda: [0, 0]) for a in arms}
    d2 = {a: defaultdict(lambda: [0, 0]) for a in arms}
    agree = n = 0
    m_rank_dist = defaultdict(int)
    for key, v in by_race.items():
        if len(v) != 7:
            continue
        e = ent_te[key]
        n += 1
        d = date_of[key]
        cur = sorted(v, key=lambda x: x[2])            # p3 順
        mod = sorted(v, key=lambda x: -x[0])           # モデル順
        rp_ = sorted(v, key=lambda x: -e[x[1]]["rp"])
        rnd = list(v); rng.shuffle(rnd)
        picks = {"現行(p3最大)": cur, "M:再学習モデル": mod,
                 "参考(競走得点最大)": rp_, "参考(無作為)": rnd}
        for a, seq in picks.items():
            d1[a][d][0] += 1; d1[a][d][1] += seq[0][3]
            d2[a][d][0] += 1; d2[a][d][1] += int(seq[0][3] and seq[1][3])
        agree += int(mod[0][1] == cur[0][1])
        m_rank_dist[mod[0][2]] += 1

    print(f"【軸1 の3着内率・検定窓 {n:,}R】")
    print(f"{'腕':>18}{'3着内率':>10}")
    for a in arms:
        _summ(a, d1[a], d1["現行(p3最大)"] if a != "現行(p3最大)" else None)
    print(f"\n  M が現行と同じ車を軸1にした率: {agree/n:.1%}")
    print("  M が選んだ車の p3 順位: " +
          " / ".join(f"{k}位:{m_rank_dist[k]/n:.1%}" for k in sorted(m_rank_dist)))

    print(f"\n【二軸（上位2車がともに3着内）・同 {n:,}R】")
    print(f"{'腕':>18}{'二軸的中':>10}")
    for a in arms:
        _summ(a, d2[a], d2["現行(p3最大)"] if a != "現行(p3最大)" else None)

    imp = sorted(zip(FEATS, m.feature_importance("gain")), key=lambda x: -x[1])
    print("\n  寄与上位: " + " / ".join(k for k, _ in imp[:10]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
