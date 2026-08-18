#!/usr/bin/env python3
"""7C/7M1 のレース選別スコアの係数と閾値を引き直す（2026-08-18 新設）

`src/race_gate_7c.py` の `COEF` / `INTERCEPT` / `THRESHOLD` を推定する。
**固定値を焼き込んだままにしないこと**——モデルを再学習すると 3着内率の分布が動くので、
`p3_calibration` と同じく定期的に引き直す。

## 手順

1. 推定窓の7車レースを取り、**較正後**の3着内率で4特徴を作る
   （`sum2` / `gap23` / `same_line` / `p_ent`）
2. 二軸的中（軸2車がともに3着内）を目的変数にロジスティック回帰
3. 閾値は「**旧ゲート（較正後 sum2 >= 1.44）と同じ通過率**」になる分位点

🔴 **推定窓を評価窓と重ねないこと。** 重ねると当然良く見える。
   既定は 2025-01〜06（`race_gate_7c.FIT_WINDOW`）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/fit_race_gate_7c.py \\
        [--since 2025-01-01] [--until 2025-06-30]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from src.p3_calibration import calibrate_top3  # noqa: E402
from src.race_gate_7c import COEF, N_CARS  # noqa: E402

OLD_GATE_MIN = 1.44          # 旧ゲート（較正後 sum2）。通過率を合わせる基準
SQL = """
SELECT e.race_key, r.race_date, r.race_type, r.cup_grade, e.frame_no,
       e.pred_top3_pct / 100.0 AS p3, e.line_group, e.finish_order
FROM keirin.wt_entries e
JOIN keirin.wt_races r USING(race_key)
WHERE r.cancel = 0 AND r.race_date BETWEEN :since AND :until
"""


def build(since: str, until: str) -> pd.DataFrame:
    """レース単位の4特徴 + 二軸的中フラグ。

    ⚠️ `wt_entries.pred_top3_pct` は **% 表記（0-100）**。0-1 へ直してから使う
       （直さないと全レースが閾値を越えて差が消える。実際に一度踏んだ）。
    """
    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    with eng.connect() as conn:
        df = pd.read_sql_query(text(SQL), conn, params={"since": since, "until": until})
    eng.dispose()
    df = df[df["finish_order"] >= 1].copy()
    df["n"] = df.groupby("race_key")["finish_order"].transform("max")
    df = df[df["n"] == N_CARS]
    rows = []
    for rk, g in df.groupby("race_key"):
        if len(g) != N_CARS:
            continue
        rt, cg = g["race_type"].iloc[0], g["cup_grade"].iloc[0]
        g = g.assign(cal=[calibrate_top3(v, rt, cg) for v in g["p3"]]) \
             .sort_values("cal", ascending=False).reset_index(drop=True)
        p = g["cal"].values
        pn = p / p.sum()
        t3 = set(g["frame_no"][g["finish_order"] <= 3])
        lg0, lg1 = g["line_group"][0], g["line_group"][1]
        rows.append({
            "race_key": rk,
            "sum2": p[0] + p[1],
            "gap23": p[1] - p[2],
            "same_line": float(lg0 is not None and lg1 is not None
                               and str(lg0) == str(lg1)),
            "p_ent": float(-(pn * np.log(pn + 1e-12)).sum()),
            "hit": float(g["frame_no"][0] in t3 and g["frame_no"][1] in t3),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01")
    ap.add_argument("--until", default="2025-06-30")
    args = ap.parse_args()

    t = build(args.since, args.until)
    cols = list(COEF)
    print(f"推定窓 {args.since}〜{args.until}  n={len(t):,}R  "
          f"二軸的中 {t['hit'].mean()*100:.2f}%")
    lr = LogisticRegression(max_iter=3000).fit(t[cols], t["hit"])
    rate = float((t["sum2"] >= OLD_GATE_MIN).mean())
    s = lr.decision_function(t[cols])
    thr = float(np.quantile(s, 1 - rate))
    print(f"旧ゲートの通過率 {rate*100:.1f}% → 同率になる閾値 {thr:.4f}")
    print("\n# --- src/race_gate_7c.py へ貼る ---")
    print(f'FIT_WINDOW = "{args.since}〜{args.until}"')
    print("COEF: dict[str, float] = {")
    for c, b in zip(cols, lr.coef_[0]):
        print(f'    "{c}":{" " * (10 - len(c))}{b:+.4f},')
    print("}")
    print(f"INTERCEPT = {lr.intercept_[0]:.4f}")
    print(f"THRESHOLD = {thr:.4f}")
    # 推定窓での効き（in-sample。評価は別窓で行うこと）
    k = int((t["sum2"] >= OLD_GATE_MIN).sum())
    old = t.nlargest(k, "sum2")["hit"].mean() * 100
    new = t.assign(s=s).nlargest(k, "s")["hit"].mean() * 100
    print(f"\n（推定窓 in-sample: 同件数 {k:,}R で {old:.2f}% → {new:.2f}%・"
          f"{new-old:+.2f}pt。**採否はこの数字で決めないこと**）")
    assert math.isfinite(thr)


if __name__ == "__main__":
    main()
