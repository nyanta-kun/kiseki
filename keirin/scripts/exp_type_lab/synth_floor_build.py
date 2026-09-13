#!/usr/bin/env python3
"""合成オッズ（= 1/Σ(1/予測オッズ)）の台を **現行規則で** 作る（2026-09-14）。

発端: ユーザー観察「合成2倍前後の商品が多い。1レース単体では50%以上の的中率が
      要る計算になり足りない。3倍以上になるよう点数を調整すべきでは」。

🔴 既存の paper テーブル（`mode='paper'`）は `rule_version='cf39e62d1442'` ＝
   **2026-09-01〜02 の規則**で、いま売っている `9619f3cb7668` ではない。
   9/8 の `C_hit` 帯下差込・`F_hit` 下限5倍・9/11 の τ適応（`tau_adaptive`）は
   入っていない。作り直すと 2.5 時間かかるので、**板（`race_type_board.npz`）の上で
   本番関数を直接回す**（`a_hit_ship.py` と同じ作法）。

⚠️ `ctx()` の `RaceShape` は `lines=()` なので `apply_line_swap` は無効。
   腕の比較は同じ台の上で行うので結論には効かない（`synth_odds_thin` と同じ断り）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/synth_floor_build.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.strategy_wt import rank_7t3_order_swap_probs  # noqa: E402
from src.type_lab import SIGNBOARD_RACE_TYPES  # noqa: E402

OUT = Path("/tmp/synthfloor/board.pkl")
PERMS, C3 = C.CANON, C.CANON3
PIDX = {c: i for i, c in enumerate(PERMS)}
TIDX = {frozenset(c): i for i, c in enumerate(C3)}


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    keys = np.array([str(v) for v in z["KEY"]])
    idx = []
    for w in ("explore", "confirm"):
        idx += [(w, int(i)) for i in C.select(None, w) if tp[int(i)] in "ABCDEF"]
    rows = []
    for n, (w, i) in enumerate(idx):
        if n % 2000 == 0:
            print(f"  {n:,}/{len(idx):,}", flush=True)
        x = ctx(i)
        if x is None:
            continue
        cars = list(range(1, 8))
        p3 = {c: float(z["P3"][i][c - 1]) for c in cars}
        pw = {c: float(z["PW"][i][c - 1]) for c in cars}
        lg = {c: str(z["LG"][i][c - 1]) for c in cars}
        lp = {c: float(z["A_line_pos"][i][c - 1]) for c in cars}
        op = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
        po = np.full(210, np.nan, np.float32)
        pr = np.zeros(210, np.float32)
        ops = np.zeros(210, np.float32)
        for c, v in x.po_tf.items():
            po[PIDX[c]] = v
        for c, v in x.pr_tf.items():
            pr[PIDX[c]] = v
        for c, v in (op or {}).items():
            ops[PIDX[c]] = v
        pot = np.full(35, np.nan, np.float32)
        prt = np.zeros(35, np.float32)
        for c, v in x.po_t3.items():
            pot[TIDX[c]] = v
        for c, v in x.pr_t3.items():
            prt[TIDX[c]] = v
        rows.append(dict(
            win=w, key=str(keys[i]), date=x.date, tl=str(tp[i]), rtype=str(rt[i]),
            axis_sum=float(x.shape.axis_sum), arare=int(x.shape.arare),
            gap=float(x.shape.gap), pw_ent=float(x.shape.pw_ent),
            order=tuple(x.shape.order), po=po, pr=pr, op=ops, pot=pot, prt=prt,
            win_tf=PIDX[x.win_tf], pay_tf=float(x.pay_tf),
            win_t3=TIDX[x.win_t3], odds_t3=float(x.odds_t3)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as f:
        pickle.dump(rows, f, protocol=4)
    print(f"保存 {OUT} {len(rows):,}行 "
          f"explore {sum(1 for r in rows if r['win']=='explore'):,} / "
          f"confirm {sum(1 for r in rows if r['win']=='confirm'):,}")


if __name__ == "__main__":
    main()
