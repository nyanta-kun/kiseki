#!/usr/bin/env python3
"""「軸2車が1-2着 ∧ 3着が人気薄」を100円で押さえられるかの台（2026-09-10・ユーザー依頼）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

- 売る1商品は `src.type_lab.sell_plans_for(type, 7, race_type, pw_ent=, trio_ok=)`。
- 買い目・配分は `build_with_gate_fallback`（= `build_legs` → `allocate` →
  ゲート落ちなら `GATE_FALLBACK` → `apply_line_swap`）。
- 入稿ゲートは ①平均想定払戻 > 20,000円 ②全点の予測オッズ >= 2.0倍。
- 軸信頼ゲート `AXIS_GATE_MIN`（A_hit/D_hit/E_hit/F_hit のみ・7車のみ）。
- 配分 `conf` の床は `予算 × floor_mult / 予測オッズ`（`MIN_PAYOUT_MULT=2.0`）。
  🔴 **100円の押さえはこの床と正面から衝突する**（110倍の点でも床は 182円→200円、
     30倍なら 667円→700円）。したがって押さえは**床の外で固定100円**として置き、
     残りを本番の配分に掛ける。
- `_insert_underband` は「帯の下から1点を差し込み、確率最下位の1点と入れ替える」。
  **点数も予算配分の枠組みも変えない**操作で、本件（点数を1点増やし、その1点だけ
  固定額にする）とは別物。流用しない。

1レース1行で焼き付ける。分析は `osae.py`。
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
from typef_racetype import ctx  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, RaceShape, _lines_of, _strongest_pair, allocate, build_legs,
    build_with_gate_fallback, mean_expected_payout, sell_plans_for)

import importlib.util  # noqa: E402
_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

PERMS = C.CANON
CIDX = {c: i for i, c in enumerate(PERMS)}
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
OUT = Path("/tmp/osae_rows.pkl")


def gate_ok(stakes, pod) -> bool:
    if not stakes:
        return False
    if mean_expected_payout(stakes, pod) <= MIN_MEAN_PAYOUT:
        return False
    return min(float(pod[c]) for c in stakes) >= MIN_POINT_ODDS


def main() -> None:
    z = C.board()
    A = {k: z[k] for k in ("LG", "ST", "A_line_pos", "A_line_size", "A_prediction_mark",
                           "P3", "PO", "RTYPE", "VENUE", "TYPE", "AXIS_SUM", "ARARE",
                           "GAP", "DATE", "KEY", "DAYI")}
    rt = np.array([str(v) for v in A["RTYPE"]])
    tp = np.array([str(v) for v in A["TYPE"]])
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            cars = list(range(1, 8))
            lg = {c: str(A["LG"][i][c - 1]) for c in cars}
            # 🔴 float32 のまま渡すと `str(line_pos)=="1"` が一致しない（例外は出ない）
            lp = {c: int(A["A_line_pos"][i][c - 1]) for c in cars}
            lines = _lines_of(lg, lp)
            p3 = {c: float(A["P3"][i][c - 1]) for c in cars}
            # 本番と同じ `lines` / `line_pair` を持たせる（ライン差し替えを効かせるため）
            x.shape = RaceShape(x.shape.type_label, x.shape.axis_sum, x.shape.arare,
                                x.shape.gap, x.shape.firm, x.shape.order,
                                x.shape.pw_ent, lines, _strongest_pair(lines, p3))

            # ── 売る1商品を決める（本番の関数） ──────────────────────────
            tr = PLANS["A_trio"]
            trio_ok = False
            if tp[i] == "A":
                tl_ = build_legs(x.shape, tr, x.po_t3, x.pr_t3)
                if tl_:
                    ts_ = allocate(tl_, x.po_t3, x.pr_t3, tr)
                    trio_ok = bool(ts_) and gate_ok(ts_, x.po_t3)
            sp = sell_plans_for(tp[i], 7, rt[i], pw_ent=x.shape.pw_ent,
                                trio_ok=trio_ok)
            if not sp:
                continue
            plan = sp[0]
            if float(A["AXIS_SUM"][i]) < AXIS_GATE_MIN.get(plan.key, 0.0):
                continue
            pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio"
                        else (x.po_tf, x.pr_tf))
            built = build_with_gate_fallback(x.shape, plan, pod, prb, 7)
            if not built:
                continue
            legs, stakes, used = built
            if not gate_ok(stakes, pod):
                continue

            trio = plan.bet_type == "trio"
            if trio:
                pay = float(stakes[x.win_t3] * x.odds_t3) if x.win_t3 in stakes else 0.0
            else:
                pay = float(stakes[x.win_tf] * x.pay_tf) if x.win_tf in stakes else 0.0
            rows.append(dict(
                i=i, win=win, date=x.date, race_key=str(A["KEY"][i]),
                venue=str(A["VENUE"][i]), rtype=rt[i], type=tp[i],
                plan=plan.key, used=used.key, trio=trio,
                order=tuple(x.shape.order), a1=x.shape.order[0], a2=x.shape.order[1],
                p3=p3, lg=lg, lp=lp,
                mk={c: int(A["A_prediction_mark"][i][c - 1]) for c in cars},
                st={c: str(A["ST"][i][c - 1]) for c in cars},
                lsz={c: int(A["A_line_size"][i][c - 1]) for c in cars},
                legs=list(stakes), stakes=dict(stakes),
                probs={c: float(prb.get(c, 0.0)) for c in stakes},
                pod={c: float(pod[c]) for c in stakes},
                mean=float(mean_expected_payout(stakes, pod)),
                inv=float(sum(stakes.values())), pay=pay,
                fin=x.win_tf, pay_tf=float(x.pay_tf),
                win_t3=x.win_t3, odds_t3=float(x.odds_t3),
                floor_mult=float(used.floor_mult), alloc=used.alloc,
                axis=float(A["AXIS_SUM"][i]),
            ))
    print(f"作った行 {len(rows):,}")
    pickle.dump(rows, OUT.open("wb"))
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
