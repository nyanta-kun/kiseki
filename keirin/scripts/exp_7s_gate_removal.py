#!/usr/bin/env python3
"""7S系のゲートを外すと母集団と ROI がどうなるかを walk-forward で測る（2026-08-14）。

## なぜ測るか

7SS / 7S / 7A は**買い目構造が完全に同一**（三連複 軸2車+5点流し）で、違うのは
ゲートの通り方だけ。ところが picks_history の全 live 記録（n=7,461・32ヶ月）では

    7SS ROI 79.0% / 7S 79.7% / 7A 85.8%

と**設計と逆順**（7A＝ゲートが1つ落ちた境界ランクが最良）で、差はいずれも有意でなく、
設計どおりの順序になった月は 32ヶ月中 7（偶然なら 5.3）。
つまり **2つのゲート（axis_sum / entropy）は順位付けに効いていない**。

順位付けに効かないなら**足切りとしても効いていない可能性**がある。そこで
「ゲートを外すと母集団がどれだけ増え、ROI がどうなるか」をここで測る。

## 何と何を比べるか

母集団は 7車 ∧ 軸2車が WT公式印◎◯と**2つとも一致しない**（`wt_overlap_n ∈ {0,1}`)。
その中を2つのゲートの落ち方で層別する:

    n_fail=0 → 現行 7S
    n_fail=1 → 現行 7A
    n_fail=2 → **現在どこにも採用されていない**（7SS の同ライン部分を除く）

さらに `wt_overlap_n == 2`（◎◯と完全一致・現行は全除外）も参考に測る。

## 設計上の注意

- **軸選定・欠車処理・賭け金配分・採点は本番の再構築コードをそのまま使う**
  （`backfill_7s_rank_wt` から import）。ここで独自実装すると、測っているものが
  本番と違うのに気づけない。
- **月次 vintage モデル**で scoring するので model-vintage look-ahead は無い。
- 日次 cap（`rank_7s_evening_reselect`）は**適用しない**。cap はゲート通過後の
  件数調整であり、ここで測りたいのはゲート自体の効果のため。

    PYTHONPATH=. .venv/bin/python scripts/exp_7s_gate_removal.py [--from 2024-01]
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.backfill_7s_rank_wt import (  # noqa: E402
    _load_board_frames_wt, _load_payouts_wt, _load_trio_boards,
)
from src.database import get_connection  # noqa: E402
from src.evaluation.void_rules import void_by_dns  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.rebuild_stakes import load_morning_boards, stakes_for_combos  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7S_AXIS_SUM_MAX, RANK_7S_ENTROPY_MAX, rank_7s_field_entropy,
    rank_7s_select_axis, rank_7s_wt_overlap_n,
)
from src.wt_vintage_config import monthly_windows  # noqa: E402


def _bad_model_name(eval_model: str) -> str:
    """`lgbm_wt_eval_m2605` → `lgbm_wt_bad_m2605`。"""
    return "lgbm_wt_bad_" + eval_model.rsplit("_", 1)[-1]


def collect(date_from: str, date_to: str, eval_model: str,
            win_model: str) -> list[dict]:
    """1窓分の候補（ゲート適用**前**）を、本番と同じ作り方で返す。"""
    df = build_features_wt(load_raw_data_wt(min_date=date_from, max_date=date_to))
    if df is None or df.empty:
        return []
    with get_connection() as c:
        ne_map = dict(c.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        date_map = dict(c.execute(
            "SELECT race_key, race_date FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to)))
        rks7 = [rk for rk, ne in ne_map.items() if ne and int(ne) == 7]
        fins: dict[str, list[tuple[int, int]]] = {}
        marks: dict[str, dict[int, int]] = {}
        for i in range(0, len(rks7), 900):
            chunk = rks7[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order, prediction_mark "
                 "FROM wt_entries WHERE race_key IN (%s)" % ",".join("?" * len(chunk)))
            for rk, fno, fo, pmv in c.execute(q, chunk):
                if fo is not None and fo >= 1:
                    fins.setdefault(rk, []).append((fo, int(fno)))
                if pmv is not None:
                    marks.setdefault(rk, {})[int(fno)] = int(pmv)
    df = df[df["race_key"].isin(set(rks7))].copy()
    if df.empty:
        return []

    X = prepare_X(df)
    df["pred_prob"] = load_model(eval_model).predict_proba(X)[:, 1]
    df["pred_win"] = load_model(win_model).predict_proba(X)[:, 1]
    df["pred_bad"] = load_model(_bad_model_name(eval_model)).predict_proba(X)[:, 1]

    keys = df["race_key"].unique().tolist()
    trio_bd = _load_trio_boards(keys)
    board_map = _load_board_frames_wt(keys)
    pm = _load_payouts_wt(keys)
    morning = load_morning_boards(keys)

    out: list[dict] = []
    for rk, g in df.groupby("race_key"):
        if ne_map.get(rk) != 7 or len(g) != 7:
            continue
        trio = trio_bd.get(rk)
        board = board_map.get(rk)
        fin = sorted(fins.get(rk, []))
        if not trio or not board or len(fin) < 3:
            continue

        win_probs = {int(r.frame_no): float(r.pred_win) for r in g.itertuples(index=False)}
        top3_probs = {int(r.frame_no): float(r.pred_prob) for r in g.itertuples(index=False)}
        bad_probs = None
        if not g["pred_bad"].isna().any():
            bad_probs = {int(r.frame_no): float(r.pred_bad) for r in g.itertuples(index=False)}
        sel = rank_7s_select_axis(win_probs, top3_probs, bad_probs)
        if sel is None:
            continue
        axis1, axis2, axis_sum = sel

        thirds_full = sorted(set(top3_probs) - {axis1, axis2})
        skip_race, others = void_by_dns(axis1, axis2, thirds_full, board)
        if skip_race:
            continue

        combos, _ = [], None
        for x in others:
            key = frozenset({axis1, axis2, x})
            if key in trio:
                combos.append(key)
        if not combos:
            continue

        actual = frozenset(fno for _, fno in fin[:3])
        stakes = stakes_for_combos(axis1, axis2, combos, top3_probs, morning.get(rk))
        hit = actual in combos
        trio_pay = pm.get(rk, {}).get(("trio", actual), 0)
        pay = trio_pay * stakes[actual] // 100 if hit else 0

        mk = marks.get(rk, {})
        honmei = next((f for f, v in mk.items() if v == 1), None)
        taikou = next((f for f, v in mk.items() if v == 2), None)

        out.append({
            "race_date": date_map.get(rk, ""),
            "axis_sum": axis_sum,
            "entropy": rank_7s_field_entropy(top3_probs),
            "wt_overlap_n": rank_7s_wt_overlap_n(axis1, axis2, honmei, taikou),
            "bet": sum(stakes.values()),
            "pay": float(pay),
        })
    return out


# --------------------------------------------------------------------------- 集計


def _roi(v):
    b = sum(x["bet"] for x in v)
    return 100 * sum(x["pay"] for x in v) / b if b else 0.0


def _ci(v, n=3000, seed=13):
    rs = random.Random(seed)
    N = len(v)
    if N < 2:
        return (0.0, 0.0)
    out = []
    for _ in range(n):
        s = [v[rs.randrange(N)] for _ in range(N)]
        out.append(_roi(s))
    out.sort()
    return out[int(0.025 * n)], out[int(0.975 * n)]


def _line(name, v, days):
    if not v:
        return f"  {name:22} —"
    hits = [x for x in v if x["pay"] > 0]
    gami = sum(1 for x in hits if x["pay"] < x["bet"])
    lo, hi = _ci(v)
    med = st.median([x["pay"] for x in hits]) if hits else 0
    return (f"  {name:22}{len(v):7d}{len(v)/days:8.1f}{100*len(hits)/len(v):8.1f}%"
            f"{_roi(v):8.1f}%  [{lo:5.1f},{hi:6.1f}]{med:10,.0f}"
            f"{(100*gami/len(hits) if hits else 0):8.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default="2024-01-01")
    args = ap.parse_args()

    cands: list[dict] = []
    for date_from, date_to, eval_model, win_model in monthly_windows():
        if date_to < args.date_from:
            continue
        got = collect(date_from, date_to, eval_model, win_model)
        cands += got
        print(f"  {date_from}〜{date_to} eval={eval_model} +{len(got):4d} "
              f"累計{len(cands):,}", flush=True)

    days = len({c["race_date"] for c in cands}) or 1
    print(f"\n候補（ゲート適用前・7車）: {len(cands):,}R / {days}日\n")

    def nfail(c):
        return ((c["axis_sum"] > RANK_7S_AXIS_SUM_MAX)
                + (c["entropy"] > RANK_7S_ENTROPY_MAX))

    hdr = (f"  {'区分':22}{'n':>7}{'件/日':>8}{'的中':>9}{'ROI':>9}"
           f"  {'95%CI':>14}{'払戻中央':>10}{'ガミ率':>9}")
    print("=== ◎◯と2つとも一致しない（現行の母集団）===")
    print(hdr)
    base = [c for c in cands if c["wt_overlap_n"] in (0, 1)]
    for k in (0, 1, 2):
        sub = [c for c in base if nfail(c) == k]
        label = {0: "n_fail=0 (現行7S)", 1: "n_fail=1 (現行7A)",
                 2: "n_fail=2 (現在不採用)"}[k]
        print(_line(label, sub, days))
    print(_line("── 現行合計 (0+1)", [c for c in base if nfail(c) <= 1], days))
    print(_line("── ゲート撤廃 (全件)", base, days))

    print("\n=== 参考: ◎◯と完全一致（現行は全除外）===")
    print(hdr)
    print(_line("wt_overlap_n=2", [c for c in cands if c["wt_overlap_n"] == 2], days))
    print(_line("wt_overlap_n=None", [c for c in cands if c["wt_overlap_n"] is None], days))

    print("\n=== ゲート単体の効き（◎◯不一致の母集団内）===")
    print(hdr)
    for name, ok in (
        (f"axis_sum<={RANK_7S_AXIS_SUM_MAX}", lambda c: c["axis_sum"] <= RANK_7S_AXIS_SUM_MAX),
        (f"axis_sum> {RANK_7S_AXIS_SUM_MAX}", lambda c: c["axis_sum"] > RANK_7S_AXIS_SUM_MAX),
        (f"entropy<={RANK_7S_ENTROPY_MAX}", lambda c: c["entropy"] <= RANK_7S_ENTROPY_MAX),
        (f"entropy> {RANK_7S_ENTROPY_MAX}", lambda c: c["entropy"] > RANK_7S_ENTROPY_MAX),
    ):
        print(_line(name, [c for c in base if ok(c)], days))

    print("\n=== 年別（ゲート撤廃 vs 現行）===")
    by_year = defaultdict(list)
    for c in base:
        by_year[c["race_date"][:4]].append(c)
    print(f"  {'年':6}{'現行 n':>8}{'ROI':>8}{'撤廃 n':>9}{'ROI':>8}{'差':>8}")
    for y in sorted(by_year):
        cur = [c for c in by_year[y] if nfail(c) <= 1]
        allc = by_year[y]
        print(f"  {y:6}{len(cur):8d}{_roi(cur):7.1f}%{len(allc):9d}"
              f"{_roi(allc):7.1f}%{_roi(allc)-_roi(cur):+7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
