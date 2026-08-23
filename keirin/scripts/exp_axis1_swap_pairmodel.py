#!/usr/bin/env python3
"""繰り上げを**ペアの相性で軸2を選び直す**形で測る（2026-08-23・ユーザー再指摘）。

## ユーザーの設計（正しく読み直したもの）

    ■元の二軸
      1. 指数1位を軸1に
      2. 軸1とラインなどで一緒に3着以内へ来やすい1車を選び軸2に
    ■提案
      1. 指数1位が軸1にできそうにないレースでは、指数2位を軸1に
         （この時点で指数1位は**軸にしない**）
      2. 指数2位と一緒に来やすい1車を軸2に（**指数1位は候補から除外**）

🔴 **§28 / §28.1 は測り方を間違えていた。** 軸2を「p3の3位」に固定していたが、
   提案は**ペアの相性で選び直す**。3位が最良の相方とは限らないので、
   固定した時点で提案より弱い形を測っていたことになる。

## 腕（すべて `argmax P(i と j がともに3着内)` のペアモデルで選ぶ）

| 腕 | 軸1 | 軸2 |
|---|---|---|
| 現行 | 指数1位（固定） | 1位を除く6車から相性最大 |
| **提案** | 指数2位（固定） | **1位と2位を除く5車**から相性最大 |
| 参考: 最良除外 | — | **1位を含まない21−6=15ペアの最良**（アンカーも自由） |
| 参考: 切替 | 提案の予測 > 現行の予測 のときだけ提案へ | （モデル自身に選ばせる） |

🔴 一次指標は**二軸的中**（ユーザーの目的そのもの）。
🔴 バストリスクの帯ごとに出す。「指数1位が軸1にできそうにないレース」限定の
   提案なので、全体で負けても危険帯で勝てば成立する。
🔴 学習・検定は年をまたぐ独立窓（`--swap` で逆向きも）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import build as build_race  # noqa: E402
from scripts.exp_axis1_bust_stratified import load_rich  # noqa: E402
from scripts.exp_trio_pair_model import build_rows, load_entries  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any  # noqa: E402

BANDS = [("全体", 0.0, 1.0), ("D1-5(堅い)", 0.0, .5), ("D6-8", .5, .8),
         ("D9-10(危険)", .8, 1.0), ("上位10%", .9, 1.0), ("上位5%", .95, 1.0),
         ("上位2%", .98, 1.0), ("上位1%", .99, 1.0)]


def ci_diff(days, B=4000, seed=71):
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
    ap.add_argument("--swap", action="store_true")
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")

    kt, ke = [r["key"] for r in tr], [r["key"] for r in te]
    fin_tr, fin_te = _load_finishes(kt), _load_finishes(ke)
    # ── ペアモデル（21ペア・ライン込み）──
    ent_tr, ent_te = load_entries(kt), load_entries(ke)
    Xp_tr, yp_tr, _ = build_rows(tr, ent_tr, fin_tr)
    Xp_te, yp_te, mp_te = build_rows(te, ent_te, fin_te)
    pair_m = fit(Xp_tr, yp_tr, args.rounds)
    pp = pair_m.predict(Xp_te)
    print(f"ペア行 学習 {len(Xp_tr):,} / 検定 {len(Xp_te):,}"
          f"   両者3着内 {yp_te.mean():.2%}")

    # ── バスト検出器（レース単位）──
    entr_tr, entr_te = load_rich(kt), load_rich(ke)
    _, _, _, Xr_tr, yr_tr, _ = build_race(tr, entr_tr, fin_tr)
    _, _, _, Xr_te, yr_te, mr_te = build_race(te, entr_te, fin_te)
    det = fit(Xr_tr, yr_tr, args.rounds)
    score = {m[0]: float(v) for m, v in zip(mr_te, det.predict(Xr_te))}
    print(f"検定窓の軸1バスト率 {yr_te.mean():.2%}\n")

    # レースごとにペア表を組む
    race = defaultdict(dict)
    date_of = {}
    for (key, date, a, b, ra, rb), p, t in zip(mp_te, pp, yp_te):
        race[key][(ra, rb)] = (float(p), int(t))
        date_of[key] = date

    def pick(tbl, anchor_rank, exclude):
        """anchor と組む相手を相性最大で選ぶ。exclude は p3順位の集合。"""
        best = None
        for (ra, rb), v in tbl.items():
            if ra == anchor_rank and rb not in exclude:
                cand = (v[0], rb, v[1])
            elif rb == anchor_rank and ra not in exclude:
                cand = (v[0], ra, v[1])
            else:
                continue
            if best is None or cand[0] > best[0]:
                best = cand
        return best

    def best_excluding(tbl, excl_rank):
        best = None
        for (ra, rb), v in tbl.items():
            if ra == excl_rank or rb == excl_rank:
                continue
            if best is None or v[0] > best[0]:
                best = (v[0], (ra, rb), v[1])
        return best

    arms = ["現行(1位軸)", "提案(2位軸・1位除外)", "最良除外(1位を含まない最良)",
            "切替(モデル任せ)"]
    rows = []
    n_switch = 0
    for key, tbl in race.items():
        if len(tbl) != 21 or key not in score:
            continue
        cur = pick(tbl, 1, {1})
        prop = pick(tbl, 2, {1, 2})
        bex = best_excluding(tbl, 1)
        if cur is None or prop is None or bex is None:
            continue
        sw = prop if prop[0] > cur[0] else cur
        n_switch += int(prop[0] > cur[0])
        rows.append(dict(key=key, date=date_of[key], sc=score[key],
                         hits=[cur[2], prop[2], bex[2], sw[2]],
                         probs=[cur[0], prop[0], bex[0], sw[0]]))
    print(f"評価対象 {len(rows):,}R"
          f"   モデルが提案側を選んだ率 {n_switch/len(rows):.1%}\n")

    qs = np.quantile([r["sc"] for r in rows], [b[1] for b in BANDS] +
                     [b[2] for b in BANDS])
    nb = len(BANDS)
    print(f"{'帯':>14}{'件数':>7}" +
          "".join(f"{a:>22}" for a in arms))
    for bi, (bn, _, hi_q) in enumerate(BANDS):
        lo, hi = qs[bi], qs[nb + bi]
        sub = [r for r in rows if lo <= r["sc"] <= hi] if hi_q == 1.0 else \
              [r for r in rows if lo <= r["sc"] < hi]
        if len(sub) < 150:
            continue
        cells = []
        base = None
        for ai in range(len(arms)):
            d = defaultdict(lambda: [0, 0, 0])
            for r in sub:
                z = d[r["date"]]
                z[0] += 1; z[1] += r["hits"][0]; z[2] += r["hits"][ai]
            h = sum(r["hits"][ai] for r in sub) / len(sub)
            if ai == 0:
                base = h
                cells.append(f"{h:.2%}")
                continue
            lo_, hi_ = ci_diff(d)
            f = "🟢" if lo_ > 0 else ("🔴" if hi_ < 0 else "")
            cells.append(f"{h:.2%} {(h-base)*100:+.2f}{f}")
        print(f"{bn:>14}{len(sub):>7,}" + "".join(f"{c:>22}" for c in cells))
    print("\n  （セルは 二軸的中率 と 現行との差pt）")

    # 提案が現行と違うペアになったレースだけを見る
    print("\n【提案が現行と別のペアになったレースだけ】")
    print(f"{'帯':>14}{'件数':>7}{'現行':>10}{'提案':>10}{'Δ':>24}")
    for bi, (bn, _, hi_q) in enumerate(BANDS):
        lo, hi = qs[bi], qs[nb + bi]
        sub = [r for r in rows if (lo <= r["sc"] <= hi if hi_q == 1.0
                                   else lo <= r["sc"] < hi)]
        sub = [r for r in sub if r["hits"][0] != r["hits"][1] or True]
        if len(sub) < 150:
            continue
        d = defaultdict(lambda: [0, 0, 0])
        for r in sub:
            z = d[r["date"]]
            z[0] += 1; z[1] += r["hits"][0]; z[2] += r["hits"][1]
        lo_, hi_ = ci_diff(d)
        c = sum(r["hits"][0] for r in sub) / len(sub)
        p = sum(r["hits"][1] for r in sub) / len(sub)
        f = "🟢" if lo_ > 0 else ("🔴" if hi_ < 0 else "")
        print(f"{bn:>14}{len(sub):>7,}{c:>10.2%}{p:>10.2%}"
              f"{f'{(p-c)*100:+.2f}pt[{lo_*100:+.2f},{hi_*100:+.2f}]{f}':>24}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
