#!/usr/bin/env python3
"""三者同時確率モデル（腕A）の**中身**を分解する（引き継ぎの未着手項目 3・4）。

## 問い

項目3 `p3_gap_dn`（相手と次点の3着内率の差）と 項目4「差・ラインの傾向」は
[[keirin_handoff_2026_08_23]] で「単体規則では動かないが両窓で同じ勾配」と
保留になっていた。`exp_trio_joint_partner` の腕Aで**両方とも寄与上位に入った**
ので、ここでは単体規則ではなく **腕Aの中でどれだけ効いているか**を測る。

1. **特徴量の落とし A/B** — gap_dn / ライン関係 / 競走得点 をそれぞれ抜いて
   腕Aを学習し直し、的中と ROI がどれだけ落ちるかを見る。
   落ちなければ「モデルが使っているように見えて実は要らない」。
2. **絞り込み** — 腕Aが選んだ1点を、①モデル予測確率 ②選んだ相手の gap_dn
   ③オッズ帯 で切って、壁（払戻率 74.85%）を越える帯があるかを見る。

🔴 **EV・確率の分位はオッズ帯の中で切る。** 跨ぐと longshot bias が
   確率の効果に化ける（[[keirin_tf_unconstrained_scan_2026_08_22]]）。
🔴 判定は日ブロック bootstrap の CI 下限 > 74.85%、**かつ両窓**。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    FEATS_A, build_A, fit, load_any, load_boards, load_entries)
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485

DROPS = {
    "全部入り": [],
    "− gap_dn": ["gap_dn"],
    "− ライン関係": ["same_a1", "same_a2", "adj_a1", "adj_a2",
                  "n_lines_in_trio", "lsize_c", "lpos_c", "leader_c",
                  "axis_same", "axis_leader"],
    "− 競走得点": ["rp_c", "rp_gap_a2"],
    "− p3の差(gap上下)": ["gap_up", "gap_dn", "p3_rel"],
    "p3と順位だけ": [f for f in FEATS_A
                 if f not in ("p3_c", "rank_c", "p3_a1", "p3_a2",
                              "axis_sum", "prod3", "sum3")],
}


def roi_ci(seg, B=4000, seed=17):
    """seg: [(date, hit, pay, bet)] → (roi, ci_lo, ci_hi, hit)"""
    by = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for d, h, p, b in seg:
        z = by[d]; z[0] += b; z[1] += p; z[2] += h; z[3] += 1
    v = np.array([[z[0], z[1], z[2], z[3]] for z in by.values()], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    r = np.sort(v[idx, 1].sum(1) / v[idx, 0].sum(1))
    return (v[:, 1].sum() / v[:, 0].sum(), r[int(B * .025)], r[int(B * .975)],
            v[:, 2].sum() / v[:, 3].sum())


def evaluate(pred, meta, board, stake):
    """腕Aの argmax 1点。→ [(date, hit, pay, bet, prob, gap_dn, odds)]"""
    by_race = defaultdict(list)
    for (key, date, a1, a2, c, rk), p in zip(meta, pred):
        by_race[key].append((float(p), c, rk, a1, a2, date))
    out = []
    for key, v in by_race.items():
        bd = board.get(key)
        if not bd or len(v) != 5:
            continue
        best = max(v, key=lambda x: x[0])
        k = frozenset((best[3], best[4], best[1]))
        if k not in bd:
            continue
        out.append(dict(key=key, date=best[5], prob=best[0], rank=best[2],
                        odds=bd[k], combo=k, _c=best[1]))
    return out


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
    print(f"壁 {PAYOUT_RATE:.2%}\n")

    ent_tr = load_entries([r["key"] for r in tr])
    ent_te = load_entries([r["key"] for r in te])
    fin_tr = _load_finishes([r["key"] for r in tr])
    fin_te = _load_finishes([r["key"] for r in te])
    board = load_boards([r["key"] for r in te])
    stake = unit_stake(1)
    Xtr, ytr, _ = build_A(tr, ent_tr, fin_tr)
    Xte, yte, mte = build_A(te, ent_te, fin_te)
    hit_of = {(k, c): int(t) for (k, _, _, _, c, _), t in zip(mte, yte)}
    gapdn_of = {(k, c): float(x[FEATS_A.index("gap_dn")])
                for (k, _, _, _, c, _), x in zip(mte, Xte)}

    # ── 1. 特徴量の落とし A/B ──
    print(f"{'構成':>18}{'的中%':>9}{'ROI':>9}{'CI下限':>9}{'CI上限':>9}{'件数':>9}")
    keep_full = None
    base = None
    for name, drop in DROPS.items():
        cols = [i for i, f in enumerate(FEATS_A) if f not in drop]
        m = fit(Xtr[:, cols], ytr, args.rounds)
        picks = evaluate(m.predict(Xte[:, cols]), mte, board, stake)
        seg = [(p["date"], hit_of[(p["key"], _c(p))],
                int(p["odds"] * 100) * stake // 100 if hit_of[(p["key"], _c(p))] else 0,
                stake) for p in picks]
        roi, lo, hi, hit = roi_ci(seg)
        mk = " 🟢" if lo > PAYOUT_RATE else ""
        print(f"{name:>18}{hit:>9.2%}{roi:>9.1%}{lo:>9.1%}{hi:>9.1%}"
              f"{len(seg):>9,}{mk}")
        if name == "全部入り":
            keep_full = picks
            base = (roi, hit)

    # ── 2. 絞り込み ──
    picks = keep_full
    seg_all = [(p, hit_of[(p["key"], _c(p))]) for p in picks]
    print(f"\n【腕Aの1点を絞る・{len(seg_all):,}R】")

    def show(title, keyfn, n_bin=5, within_odds=False):
        print(f"\n===== {title} =====")
        if within_odds:
            oq = np.quantile([p["odds"] for p, _ in seg_all], [1 / 3, 2 / 3])
            bands = [("低(〜%.0f倍)" % oq[0], -1e9, oq[0]),
                     ("中", oq[0], oq[1]),
                     ("高(%.0f倍〜)" % oq[1], oq[1], 1e9)]
        else:
            bands = [("全体", -1e9, 1e9)]
        for bn, blo, bhi in bands:
            pool = [(p, h) for p, h in seg_all if blo <= p["odds"] < bhi]
            if len(pool) < 500:
                continue
            q = np.quantile([keyfn(p) for p, _ in pool],
                            [i / n_bin for i in range(1, n_bin)])
            print(f"  [{bn}]  {'分位':>6}{'件数':>8}{'的中%':>9}{'ROI':>9}"
                  f"{'CI下限':>9}{'中央オッズ':>11}")
            for i in range(n_bin):
                lo_ = -1e9 if i == 0 else q[i - 1]
                hi_ = 1e9 if i == n_bin - 1 else q[i]
                sub = [(p, h) for p, h in pool if lo_ <= keyfn(p) < hi_]
                if len(sub) < 300:
                    continue
                s = [(p["date"], h,
                      int(p["odds"] * 100) * stake // 100 if h else 0, stake)
                     for p, h in sub]
                roi, cl, ch, hit = roi_ci(s)
                mk = " 🟢" if cl > PAYOUT_RATE else ""
                print(f"{'':>10}{f'Q{i+1}':>6}{len(sub):>8,}{hit:>9.2%}"
                      f"{roi:>9.1%}{cl:>9.1%}"
                      f"{np.median([p['odds'] for p, _ in sub]):>11.1f}{mk}")

    show("① モデル予測確率（オッズ帯を跨ぐ・参考）", lambda p: p["prob"])
    show("② モデル予測確率（オッズ帯の中で切る）", lambda p: p["prob"],
         within_odds=True)
    show("③ 選んだ相手の gap_dn", lambda p: gapdn_of[(p["key"], _c(p))])
    show("④ オッズそのもの", lambda p: p["odds"])
    return 0


def _c(p):
    """buy 目から相手（軸2車でない車）を取り出す — meta のキー用。"""
    return p["_c"]


if __name__ == "__main__":
    raise SystemExit(main())
