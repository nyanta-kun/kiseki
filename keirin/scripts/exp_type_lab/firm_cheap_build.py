#!/usr/bin/env python3
"""「順当かつ三連単が安いレースは丸ごと見送る」の台（2026-09-11）。

計画: `docs/type_lab/PLAN_firm_cheap_skip_2026_09_11.md`

`tau_stacked_build.py` と同じ**本番経路**（`apply_line_swap` 込み・`A_line_pos` を
int 化・`build_with_gate_fallback`）で1レース1商品を組み、そこへ本件の条件量を足す。

条件量（すべて朝に確定する量だけ）:
  cheapA  指数1-2-3位（p3 降順）の三連単**予測**オッズ   ← 「安い」の定義 A
  cheapB  印 ◎-○-△（`A_prediction_mark` 1/2/3）の同     ← 定義 B
  sig14   Σ(1/予測オッズ)（確率降順 上位14点・帯なし）    ← 安い層を2分する量
  q_m3    ◎○△ の3車で決まるモデル確率（三連複）          ← ⑥の検算用
  hs_trio 三連複 {◎○△} の予測オッズ（`mark_order` の本線）

🔴 `A_prediction_mark` は **float32**。`int()` で読むこと
   （`str()` で "◎" と比べる実装は一度も一致しない）。
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import pickle
import sys
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
ap.add_argument("--out", default="/tmp/firm_cheap_rows.pkl")
args = ap.parse_args()

REPO = Path(args.repo).resolve()
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs             # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                    # type: ignore[union-attr]

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0
PERMS, C3 = C.CANON, C.CANON3
CIDX = C.CIDX

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

#: 一撃商品（`DESIGN.md` 2.1）。板には高額枠が無いのでこの2つ。
ICHIGEKI = {"A_ana", "F_sign"}


class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date", "rtype")


def ctx(i: int) -> Ctx | None:
    cars = list(range(1, 8))
    p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
    pw = {c: float(Z["PW"][i][c - 1]) for c in cars}
    lg = {c: str(Z["LG"][i][c - 1]) for c in cars}
    lp = {}
    for c in cars:
        v = Z["A_line_pos"][i][c - 1]
        lp[c] = int(v) if np.isfinite(v) else None          # 🔴 float32 → int
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    po = {PERMS[t]: float(Z["PO"][i][t]) for t in range(210)
          if np.isfinite(Z["PO"][i][t]) and Z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    lines = TL._lines_of(lg, lp)
    x = Ctx()
    x.shape = TL.RaceShape(
        str(Z["TYPE"][i]), float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
        float(Z["GAP"][i]), float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
        order, TL.win_entropy(pw), lines, TL._strongest_pair(lines, p3))
    x.po_tf, x.pr_tf = po, pr
    x.po_t3 = {frozenset(c): float(Z["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(Z["TRIO_PO"][i][j]) and Z["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
               for c in C3}
    x.win_tf = PERMS[int(Z["WIN"][i])]
    x.pay_tf = float(Z["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(Z["TRIO_WIN"][i])])
    x.odds_t3 = float(Z["TRIO_PAY"][i])
    x.date = str(Z["DATE"][i])
    x.rtype = str(Z["RTYPE"][i])
    return x


def build(x: Ctx, plan) -> dict | None:
    trio = plan.bet_type == "trio"
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    got = TL.build_with_gate_fallback(x.shape, plan, pod, prb, 7)
    if not got:
        return None
    legs, st, pl = got
    mean = float(TL.mean_expected_payout(st, pod))
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(key=pl.key, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate))


def conds(x: Ctx, i: int) -> dict:
    """本件の条件量。すべて発走前に確定する。"""
    order = x.shape.order
    cheapA = float(x.po_tf.get(tuple(order[:3]), np.nan))
    mk = {c: int(Z["A_prediction_mark"][i][c - 1]) for c in range(1, 8)}
    inv = {v: c for c, v in mk.items()}
    hon, tai, ana = inv.get(1), inv.get(2), inv.get(3)
    cheapB = hs_trio = q_m3 = float("nan")
    if hon and tai and ana:
        cheapB = float(x.po_tf.get((hon, tai, ana), np.nan))
        fs = frozenset({hon, tai, ana})
        hs_trio = float(x.po_t3.get(fs, np.nan))
        q_m3 = float(x.pr_t3.get(fs, np.nan))
    # 確率降順（帯なし）の Σ(1/予測オッズ)
    srt = sorted(x.pr_tf, key=lambda c: -x.pr_tf[c])
    sig = lambda k: float(sum(1.0 / x.po_tf[c] for c in srt[:k] if x.po_tf.get(c, 0) > 0))
    cum = np.cumsum([x.pr_tf[c] for c in srt])
    # 決着（評価用・条件には使わない）
    rk = {c: j for j, c in enumerate(order)}
    fr = tuple(rk[c] for c in x.win_tf)
    return dict(cheapA=cheapA, cheapB=cheapB, hs_trio=hs_trio, q_m3=q_m3,
                sig12=sig(12), sig14=sig(14), sp5=float(cum[4]), sp14=float(cum[13]),
                fin_ranks=fr, exact123=bool(fr == (0, 1, 2)),
                jundo=bool(set(fr) <= {0, 1, 2, 3} and {0, 1} <= set(fr)),
                pay_tf=float(x.pay_tf))


def main() -> None:
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if str(Z["TYPE"][int(i)]) in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = x.shape.type_label
            trio_ok = None
            if tl == "A":
                r = build(x, TL.PLANS["A_trio"])
                trio_ok = bool(r and r["gate"])
            sel = TL.sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent,
                                    trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            axis_ok = bool(_G.passes_axis_gate(plan.key, x.shape.axis_sum, 7))
            r = build(x, plan)
            if r is None:
                continue
            rows.append(dict(i=i, win=win, date=x.date, type=tl, rtype=x.rtype,
                             axis=float(x.shape.axis_sum), gap=float(x.shape.gap),
                             arare=int(x.shape.arare), pw_ent=float(x.shape.pw_ent),
                             axis_ok=axis_ok,
                             ichigeki=bool(r["key"] in ICHIGEKI),
                             **r, **conds(x, i)))
    print(f"行 {len(rows):,}")
    pickle.dump(rows, Path(args.out).open("wb"))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
