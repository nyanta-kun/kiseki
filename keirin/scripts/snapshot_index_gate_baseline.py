#!/usr/bin/env python3
"""ゲート較正のための「変更前」ベースラインを保存する（2026-08-19）。

## なぜ今撮るのか

`FEATURE_COLS_WT` を 60→66 へ変えたので、指数（`pred_win/top2/top3`）の分布が動く。
すると **絶対値で切っているゲートの通過率が変わる**:

    RANK_7C_P3_SUM_MIN = 1.44 / RANK_7C_LEG_P3_MIN = 0.15
    RANK_7S_AXIS_SUM_MAX = 1.40 / RANK_7S_ENTROPY_MAX = 1.8329
    RANK_9C_* / p3_calibration の補正量

🔴 **バックフィル（`backfill_index_pct_wt.py`）を走らせると
   `wt_entries.pred_*_pct` が上書きされ、変更前の分布は二度と取れない。**
   較正の第一基準は「各ランクの日次件数を現行と同水準に保つ」なので、
   その現行値をここで凍結しておく。

⚠️ 成績で較正してはいけない。件数が動いたまま成績を比べると
   「少なく賭けただけ」を改善と誤認する（[[keirin_race_selection_meta_2026_08_18]]）。

出力: data/exp_cache/index_gate_baseline.json（読み取りのみ・DB は触らない）

使い方:
    PYTHONPATH=. .venv/bin/python scripts/snapshot_index_gate_baseline.py
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEG_P3_MIN, RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN,
    RANK_7S_ENTROPY_MAX, rank_7c_select_axis, rank_7c_select_legs,
    rank_7s_field_entropy,
)

OUT = Path(__file__).resolve().parent.parent / "data" / "exp_cache" / "index_gate_baseline.json"


def _q(v, qs=(0.05, 0.25, 0.5, 0.75, 0.95)):
    v = sorted(v)
    return {f"p{int(q*100):02d}": round(v[min(int(q * len(v)), len(v) - 1)], 5) for q in qs} \
        if v else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--days", type=int, default=90)
    # 🔴 当日は VPS の日次バッチ（07:00・旧モデル）が上書きしうるので、
    #    移行前後の比較では末尾を切って混在を避ける。
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD")
    a = ap.parse_args()
    d2 = a.until or date.today().isoformat()
    d1 = (date.fromisoformat(d2) - timedelta(days=a.days)).isoformat()
    out_path = Path(a.out)
    with get_connection() as conn:
        # ① 各ランクの日次件数（picks_history の実績）
        cur = conn.execute(
            "SELECT rank, race_date, COUNT(*) FROM picks_history "
            "WHERE race_date BETWEEN ? AND ? AND bet_amount > 0 "
            "GROUP BY rank, race_date", (d1, d2))
        per_day = defaultdict(list)
        for rank, _d, n in cur.fetchall():
            per_day[rank].append(int(n))
        # ② ゲート量の分布（現行の pred_top3_pct から計算）
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, r.race_type, r.cup_grade, "
            "       r.n_entries FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND e.pred_top3_pct IS NOT NULL "
            "  AND r.n_entries IN (7, 9)", (d1, d2))
        ent, meta = defaultdict(dict), {}
        for rk, fn, p3, rt, g, ne in cur.fetchall():
            ent[rk][int(fn)] = float(p3) / 100.0
            meta[rk] = (rt, g, int(ne))

    gates = defaultdict(lambda: defaultdict(list))
    for rk, cars in ent.items():
        rt, g, ne = meta[rk]
        if len(cars) != ne:
            continue
        tag = f"{ne}車"
        sel = rank_7c_select_axis(cars)
        if sel is None:
            continue
        a1, a2, raw = sel
        gates[tag]["p3_sum_top2"].append(raw)
        cal = calibrated_p3_sum_top2(cars, rt, g)
        if cal is not None:
            gates[tag]["p3_sum_top2_cal"].append(float(cal))
        gates[tag]["entropy"].append(rank_7s_field_entropy(cars))
        legs = rank_7c_select_legs(sorted(set(cars) - {a1, a2}), cars)
        gates[tag]["n_legs"].append(len(legs))
        gates[tag]["p3_max"].append(max(cars.values()))

    out = {
        "captured_at": d2,
        "window": [d1, d2],
        "note": "FEATURE_COLS_WT 60特徴時点の分布。66特徴へ変更後の較正基準。",
        "thresholds_at_capture": {
            "RANK_7C_P3_SUM_MIN": RANK_7C_P3_SUM_MIN,
            "RANK_7C_LEG_P3_MIN": RANK_7C_LEG_P3_MIN,
            "RANK_7C_LEGS_MIN": RANK_7C_LEGS_MIN,
            "RANK_7S_ENTROPY_MAX": RANK_7S_ENTROPY_MAX,
        },
        "daily_counts": {
            r: {"days": len(v), "mean": round(statistics.mean(v), 2),
                "median": statistics.median(v), "min": min(v), "max": max(v)}
            for r, v in sorted(per_day.items()) if v},
        "gate_quantiles": {
            tag: {k: {"n": len(v), **_q(v),
                      "mean": round(statistics.mean(v), 5)}
                  for k, v in sorted(d.items())}
            for tag, d in sorted(gates.items())},
        "pass_rates": {
            tag: {
                "p3_sum_top2_cal >= RANK_7C_P3_SUM_MIN": round(
                    sum(1 for x in d["p3_sum_top2_cal"] if x >= RANK_7C_P3_SUM_MIN)
                    / max(len(d["p3_sum_top2_cal"]), 1), 4),
                "n_legs >= RANK_7C_LEGS_MIN": round(
                    sum(1 for x in d["n_legs"] if x >= RANK_7C_LEGS_MIN)
                    / max(len(d["n_legs"]), 1), 4),
                "entropy <= RANK_7S_ENTROPY_MAX": round(
                    sum(1 for x in d["entropy"] if x <= RANK_7S_ENTROPY_MAX)
                    / max(len(d["entropy"]), 1), 4),
            } for tag, d in sorted(gates.items()) if d.get("p3_sum_top2_cal")},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"保存: {out_path}\n")
    print("=== 各ランクの日次件数（直近90日・picks_history）===")
    for r, v in out["daily_counts"].items():
        print(f"  {r:12} 平均 {v['mean']:>6.2f}件/日  中央 {v['median']:>3}  "
              f"（{v['days']}日・最小{v['min']}・最大{v['max']}）")
    print("\n=== ゲートの通過率（現行60特徴）===")
    for tag, d in out["pass_rates"].items():
        print(f"  [{tag}]")
        for k, v in d.items():
            print(f"    {k:44} {100*v:>6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
