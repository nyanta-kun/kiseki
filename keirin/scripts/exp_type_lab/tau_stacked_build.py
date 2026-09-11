#!/usr/bin/env python3
"""τ適応を **`apply_line_swap` 込みの本番経路**で測るための台（2026-09-11）。

## なぜ作り直したか

`band_by_race_build.py` の台は `RaceShape` を直接組んでいて **`lines=()`** だった。
`apply_line_swap`（`LINE_SWAP_PLANS` に `C_hit` が入る）は `shape.lines` が空だと
**一度も発動しない**ので、あの台の数字は「ライン差し替えの掛かっていない `C_hit`」
のものだった。τ適応 も差し替えも **同じ「点の入れ替え」層**なので、重なると
効果が消える可能性がある。ここではそれを測る。

## この台の作り方（検算つき）

- 板 `/tmp/race_type_board.npz`（7車・vintage walk-forward の p3/pw）。
- 🔴 **`A_line_pos` は float32**。`_lines_of` は `int(str(v))` で読むので
  `"1.0"` は `ValueError` になり（例外は握りつぶされる）**全車が最後尾 99 扱い＝
  ライン内の並びが車番順**になる。`int()` してから渡すこと。
- `lines = _lines_of(line_group, line_pos)` / `line_pair = _strongest_pair(lines, p3)`。
- 売る1商品は本番 `sell_plans_for`（型A の `pw_ent` / `trio_ok` 分岐込み）。
- 買い目は本番 `build_with_gate_fallback`（`GATE_FALLBACK` と `apply_line_swap` 込み）。
- 軸信頼ゲート `passes_axis_gate`（**2026-09-11 の #565 で `C_hit` も 1.500 に入った**）。
- 入稿ゲート: 平均想定払戻 > 20,000円 かつ 全点の予測オッズ >= 2.0倍。

同じスクリプトを **main と worktree の2つの repo** に対して走らせ、行を突き合わせる。

    PYTHONPATH=<repo> python tau_stacked_build.py --repo <repo> --out <pkl>
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
from dataclasses import replace

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True, help="keirin リポジトリのルート")
ap.add_argument("--out", required=True)
ap.add_argument("--tau-off", action="store_true",
                help="τ適応を切って組む。⚠️ `GATE_FALLBACK['C_hit']` は "
                     "`replace(PLANS['C_hit'], ...)` 製で `tau_adaptive=True` を継承するため、"
                     "**これだけでは現行と同じにならない**（22%のレースが代替へ回りτ適応が走る）")
ap.add_argument("--max-legs", type=int, default=None,
                help="`TAU_ADAPTIVE_MAX_LEGS` を差し替える（本番コードは触らない）")
ap.add_argument("--min-legs", type=int, default=None,
                help="`TAU_ADAPTIVE_MIN_LEGS` を差し替える")
ap.add_argument("--no-lines", action="store_true",
                help="`lines=()` で組む（band_by_race_build と同じ欠陥状態の再現）")
args = ap.parse_args()

REPO = Path(args.repo).resolve()
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                    # noqa: E402

# 🔴 上限・下限は `_tau_adaptive_legs` が**呼び出しのたびにモジュール大域を読む**ので、
#    ここで差し替えれば本番コードを編集せずに掃引できる。
if args.max_legs is not None:
    TL.TAU_ADAPTIVE_MAX_LEGS = int(args.max_legs)
if args.min_legs is not None:
    TL.TAU_ADAPTIVE_MIN_LEGS = int(args.min_legs)
from src.strategy_wt import rank_7t3_blend_probs             # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                    # type: ignore[union-attr]

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0
PERMS = C.CANON
C3 = C.CANON3

# ── 板を実体化（NpzFile の添字アクセスは毎回全展開する）──
_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

# ── `apply_line_swap` が発動したかを拾うための包み ──
_SWAP = {"n": 0, "hit": False}
_orig_swap = TL.apply_line_swap


def _wrapped_swap(shape, plan, legs, stakes, pred_odds, probs,
                  min_mean_payout=TL.MIN_MEAN_PAYOUT):
    out = _orig_swap(shape, plan, legs, stakes, pred_odds, probs, min_mean_payout)
    _SWAP["hit"] = list(out[0]) != list(legs)
    return out


TL.apply_line_swap = _wrapped_swap


class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date", "rtype")


def ctx(i: int) -> Ctx | None:
    cars = list(range(1, 8))
    p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
    pw = {c: float(Z["PW"][i][c - 1]) for c in cars}
    lg = {c: str(Z["LG"][i][c - 1]) for c in cars}
    # 🔴 float32 → int（`_lines_of` が `int(str(v))` で読むため）
    lp = {}
    for c in cars:
        v = Z["A_line_pos"][i][c - 1]
        lp[c] = int(v) if np.isfinite(v) else None
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    po = {PERMS[t]: float(Z["PO"][i][t]) for t in range(210)
          if np.isfinite(Z["PO"][i][t]) and Z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    lines = () if args.no_lines else TL._lines_of(lg, lp)
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
    """本番経路で1商品。組めなければ None。ゲート結果は行に持つ（落とさない）。"""
    trio = plan.bet_type == "trio"
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    _SWAP["hit"] = False
    got = TL.build_with_gate_fallback(x.shape, plan, pod, prb, 7)
    if not got:
        return None
    legs, st, pl = got
    swapped = _SWAP["hit"]
    mean = float(TL.mean_expected_payout(st, pod))
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(key=pl.key, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate), swapped=bool(swapped),
                min_odds=float(pl.min_odds))


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
            # 型A の `trio_ok`（本番は A_trio の行が入稿ゲートを通るか）
            trio_ok = None
            if tl == "A":
                r = build(x, TL.PLANS["A_trio"])
                trio_ok = bool(r and r["gate"])
            sel = TL.sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent,
                                    trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            if args.tau_off and plan.tau_adaptive:
                plan = replace(plan, tau_adaptive=False)
            # 🔴 軸信頼ゲートは**落とさずに印を付ける**（比較側で切る）。
            #    2026-09-11 の #565 で `C_hit` も対象になったので、
            #    「ゲートの変更」と「ライン差し替え」を分けて読めるようにしておく。
            axis_ok = bool(_G.passes_axis_gate(plan.key, x.shape.axis_sum, 7))
            r = build(x, plan)
            if r is None:
                continue
            rows.append(dict(i=i, win=win, date=x.date, type=tl, rtype=x.rtype,
                             axis=float(x.shape.axis_sum), axis_ok=axis_ok,
                             n_lines=len(x.shape.lines), **r))
    print(f"行 {len(rows):,}")
    pickle.dump(rows, Path(args.out).open("wb"))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
