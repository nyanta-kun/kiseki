#!/usr/bin/env python3
"""繰り上げを**フォーメーション（相手複数・1位も相手に含む）**で測り直す。

## ユーザーの補足（2026-08-23）

> 軸2は軸1に連動しての認識であり、軸1の入れ替えは元の1位を除外しているケースの
> はずのため、軸2選定では1位を除外して選出すべき。ただ相手は紛れも含め複数選択
> する想定のため、1位を選択しても良いものとする。

🔴 **§28 の測り方では足りない。** §28 は**ペアの二軸的中**だけを見た。
   だが `{2位,3位}` を軸にして**1位を相手に置く**なら、
   軸が当たったあとの3枠目に**1位という強い候補**が残る。
   つまり「ペアは弱いが条件付きが強い」形になりうる。**積で測らないと分からない。**

    三連複の的中 = P(軸2車がともに3着内) × P(相手のどれかが残り1枠 | 軸的中)

## 測り方（点数を揃える）

| 腕 | 軸2車 | 相手（N点） |
|---|---|---|
| 現行 | `{1位, 2位}` | 残りを p3 順に N 車（3位,4位,…） |
| 繰り上げ | `{2位, 3位}` | 残りを p3 順に N 車（**1位が先頭**,4位,…） |

N=1〜5 で並べる。**賭け金は `unit_stake(N)` なので総額はどちらも同じ**
（1レースの予算枠が一定）。これで初めて公平な比較になる。

🔴 バストリスクの帯ごとに出す。提案は「入着困難なレース」限定の話なので、
   全体で負けていても危険帯で勝てば成立する。
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
from scripts.exp_axis1_bust_stratified import build, load_rich  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any, load_boards  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

BANDS = [("全体", 0.0, 1.0), ("D1-5(堅い)", 0.0, .5), ("D6-8", .5, .8),
         ("D9-10(危険)", .8, 1.0), ("上位10%", .9, 1.0), ("上位5%", .95, 1.0),
         ("上位2%", .98, 1.0)]


def ci_diff(days, B=4000, seed=53):
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

    ent_tr, ent_te = load_rich([r["key"] for r in tr]), load_rich([r["key"] for r in te])
    fin_tr, fin_te = _load_finishes([r["key"] for r in tr]), _load_finishes([r["key"] for r in te])
    _, _, _, Xr_tr, yr_tr, _ = build(tr, ent_tr, fin_tr)
    _, _, _, Xr_te, yr_te, mr_te = build(te, ent_te, fin_te)
    det = fit(Xr_tr, yr_tr, args.rounds)
    s = det.predict(Xr_te)
    score = {m[0]: float(v) for m, v in zip(mr_te, s)}
    board = load_boards([r["key"] for r in te])

    races = []
    for r in te:
        key = r["key"]
        o3 = fin_te.get(key); bd = board.get(key)
        if not o3 or not bd or key not in score or len(r["order"]) < 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        o = r["order"]
        races.append(dict(key=key, date=r["date"], o=o, wins=wins, bd=bd,
                          sc=score[key]))
    print(f"評価対象 {len(races):,}R\n")

    def buy(rc, axes, legs, n):
        """軸2車 × 相手上位n点。板に無い目は捨てる。→ (bet, pay, hit)"""
        ks = [frozenset(axes + (c,)) for c in legs[:n]]
        ks = [k for k in ks if k in rc["bd"]]
        if not ks:
            return None
        st = unit_stake(len(ks))
        pay = sum(int(rc["bd"][k] * 100) * st // 100 for k in ks if k in rc["wins"])
        return len(ks) * st, pay, int(any(k in rc["wins"] for k in ks))

    qs = np.quantile([r["sc"] for r in races], [b[1] for b in BANDS] +
                     [b[2] for b in BANDS])
    nb = len(BANDS)
    for bi, (bn, _, hi_q) in enumerate(BANDS):
        lo, hi = qs[bi], qs[nb + bi]
        sub = [r for r in races if lo <= r["sc"] <= hi] if hi_q == 1.0 else \
              [r for r in races if lo <= r["sc"] < hi]
        if len(sub) < 200:
            continue
        print(f"===== {bn}（{len(sub):,}R・実測バスト率 "
              f"{np.mean([1 for _ in sub]) and sum(1 for r in sub if r['o'][0] not in {c for w in r['wins'] for c in w})/len(sub):.1%}）=====")
        print(f"{'点数':>5}{'現行 的中/ROI':>20}{'繰上げ 的中/ROI':>20}"
              f"{'的中Δ':>24}{'ROIΔ':>24}")
        for n in (1, 2, 3, 4, 5):
            d = defaultdict(lambda: [0, 0, 0])      # 的中用
            dr = defaultdict(lambda: [0.0, 0.0, 0.0])  # ROI用
            cb = cp = ch = sb = sp = sh = cnt = 0
            for rc in sub:
                o = rc["o"]
                cur = buy(rc, (o[0], o[1]), [o[2], o[3], o[4], o[5], o[6]], n)
                sw = buy(rc, (o[1], o[2]), [o[0], o[3], o[4], o[5], o[6]], n)
                if cur is None or sw is None:
                    continue
                cnt += 1
                cb += cur[0]; cp += cur[1]; ch += cur[2]
                sb += sw[0]; sp += sw[1]; sh += sw[2]
                z = d[rc["date"]]; z[0] += 1; z[1] += cur[2]; z[2] += sw[2]
                z = dr[rc["date"]]; z[0] += cur[0]; z[1] += cur[1]; z[2] += sw[1]
            if not cnt:
                continue
            lh, uh = ci_diff(d)
            lr, ur = ci_diff(dr)
            fh = "🟢" if lh > 0 else ("🔴" if uh < 0 else "")
            fr = "🟢" if lr > 0 else ("🔴" if ur < 0 else "")
            print(f"{n:>5}{f'{ch/cnt:.1%}/{cp/cb:.1%}':>20}"
                  f"{f'{sh/cnt:.1%}/{sp/sb:.1%}':>20}"
                  f"{f'{(sh-ch)/cnt*100:+.2f}pt[{lh*100:+.2f},{uh*100:+.2f}]{fh}':>24}"
                  f"{f'{(sp/sb-cp/cb)*100:+.1f}pt[{lr*100:+.1f},{ur*100:+.1f}]{fr}':>24}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
