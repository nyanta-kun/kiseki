#!/usr/bin/env python3
"""外れたレースの分解台を作る（2026-09-10・ユーザー依頼）。

## 先に本番を読む（CLAUDE.md）

- 売る1商品は `src.type_lab.sell_plans_for`（看板枠 → 型Aの3分割 → 型Fの種別）。
- 買い目は `build_legs` + `allocate`、入稿ゲートは
  平均想定払戻 > 20,000円 かつ 全点の予測オッズ >= 2.0倍。
- 確率は `src.strategy_wt.rank_7t3_blend_probs`（PL に隣接ボーナス λ/μ を掛けたもの）。
  **板の `PROB` ではない**（あちらはボーナス前）。

1レース1行で、外れの分解に要るものを全部焼き付ける:
  決着の順位（確率順・予測オッズ順）／確率上位k点のカバレッジ／三連複のカバレッジ／
  軸2車が3着以内に来たか／実際の1-2-3。
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, SIGNBOARD_RACE_TYPES, allocate, build_legs, mean_expected_payout)

PERMS, C3 = C.CANON, C.CANON3
CIDX = {c: i for i, c in enumerate(PERMS)}
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/miss_anatomy_rows.pkl")


def build_one(x, key: str):
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(plan=key, trio=trio, k=len(st), inv=float(sum(st.values())),
                pay=pay, mean=float(mean), legs=list(st))


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    ven = np.array([str(v) for v in z["VENUE"]])
    RP = z["A_race_point"].astype(float)
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            trio_ok = build_one(x, "A_trio") is not None if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            r = build_one(x, key)
            if r is None:
                continue

            # ── 確率順・予測オッズ順での決着の位置 ────────────────────────
            pr = x.pr_tf
            order = sorted(pr, key=lambda c: -pr[c])            # 210点を確率降順
            pos = {c: j for j, c in enumerate(order)}
            wt = x.win_tf
            rank_prob = pos.get(wt, 999)
            cum = np.cumsum([pr[c] for c in order])
            # 三連複（確率降順）
            o3 = sorted(x.pr_t3, key=lambda c: -x.pr_t3[c])
            rank_trio = {c: j for j, c in enumerate(o3)}.get(x.win_t3, 999)
            # 市場（予測オッズ昇順）での位置
            om = sorted(x.po_tf, key=lambda c: x.po_tf[c])
            rank_mkt = {c: j for j, c in enumerate(om)}.get(wt, 999)

            a1, a2 = x.shape.order[0], x.shape.order[1]
            top3 = set(wt)
            legs_set = set(r["legs"])
            if r["trio"]:
                in_legs = x.win_t3 in legs_set
                set_hit = in_legs
            else:
                in_legs = wt in legs_set
                set_hit = frozenset(wt) in {frozenset(c) for c in legs_set}

            # ── 相手・順番を追うための追加情報 ───────────────────────────
            p3o = list(x.shape.order)                      # 3着内率の降順（7車）
            p3rank = {c: j for j, c in enumerate(p3o)}
            legs_cars = set()
            for c in r["legs"]:
                legs_cars |= set(c)
            # 決着3車それぞれの p3 順位（相手＝軸2車以外の1車）
            third = [c for c in wt if c not in (a1, a2)]
            third_rank = min((p3rank[c] for c in third), default=99)
            # 集合の中で実際の並びが確率何番目だったか（1..6）
            perms6 = sorted(itertools.permutations(sorted(set(wt))),
                            key=lambda c: -pr.get(c, 0.0))
            perm_rank = perms6.index(wt) if wt in perms6 else 99

            rows.append(dict(
                i=i, race_key=str(z["KEY"][i]),
                p3o=p3o, third_rank=third_rank, perm_rank=perm_rank,
                fin_ranks=tuple(p3rank[c] for c in wt),
                fin_in_legs_cars=all(c in legs_cars for c in wt),
                n_legs_cars=len(legs_cars),
                win=win, date=x.date, venue=ven[i], rtype=rt[i], dayi=int(z["DAYI"][i]),
                type=tp[i], axis=float(z["AXIS_SUM"][i]), gap=float(z["GAP"][i]),
                arare=int(z["ARARE"][i]), pw_ent=float(x.shape.pw_ent),
                rp_sd=float(RP[i].std()), agree=bool(z["AGREE"][i]),
                plan=r["plan"], trio=r["trio"], k=r["k"], inv=r["inv"], pay=r["pay"],
                mean=r["mean"], hit=r["pay"] > 0, shown=r["pay"] >= r["inv"],
                in_legs=in_legs, set_hit=set_hit,
                rank_prob=rank_prob, rank_trio=rank_trio, rank_mkt=rank_mkt,
                sp1=float(cum[0]), sp3=float(cum[2]), sp5=float(cum[4]),
                sp8=float(cum[7]), sp12=float(cum[11]),
                a1=a1, a2=a2, a1_in3=a1 in top3, a2_in3=a2 in top3,
                a1_1st=wt[0] == a1, both_in3=(a1 in top3 and a2 in top3),
                fin=wt, po_win=float(x.po_tf.get(wt, np.nan)),
                pay_tf=float(x.pay_tf), odds_t3=float(x.odds_t3),
            ))
    print(f"作った行 {len(rows):,}")
    pickle.dump(rows, OUT.open("wb"))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
