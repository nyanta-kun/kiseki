#!/usr/bin/env python3
"""`src/p3_calibration.py` の係数を推定し直す（2026-08-17 新設）。

## なぜ定期的に引き直すのか

🔴 **固定値を焼き込んだままにしてはいけない。** 窓を替えて推定し直すと決勝の
   `a` が 0.877(2025推定) → 0.971(2026推定) と動く。決勝は 2025 で約1,800レース
   しかなく係数が振れる。モデルを再学習したら較正もずれるので、**週次再学習と
   同じ頻度で見直す**のが望ましい。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/fit_p3_calibration.py \
        --from 2025-01-01 --to 2025-12-31

出力は `src/p3_calibration.py` へ手で反映する（自動書き換えはしない。
係数が黙って変わると、どの期間の較正で商品が出たのか追えなくなるため）。

⚠️ **過去の再構築へ未来を含む係数を当てると in-sample になる。**
   walk-forward で作り直すときは、その時点までの窓で推定した係数を使うこと。
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.p3_calibration import grade_group, race_type_group   # noqa: E402

MIN_ROWS = 1500        # これ未満のセルは推定しない（1次元へフォールバックさせる）


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1.0 - 1e-4)
    return math.log(p / (1.0 - p))


def _fit(rows: list[tuple[float, int]]) -> tuple[float, float]:
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    X = np.array([[_logit(p)] for p, _ in rows])
    y = np.array([a for _, a in rows])
    m = LogisticRegression(C=1e6, solver="lbfgs").fit(X, y)
    return float(m.coef_[0][0]), float(m.intercept_[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2025-01-01")
    ap.add_argument("--to", dest="d_to", default="2025-12-31")
    args = ap.parse_args()

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT e.pred_top3_pct p, e.finish_order f, r.race_type, r.cup_grade "
            "FROM wt_entries e JOIN wt_races r ON r.race_key = e.race_key "
            "WHERE r.race_date BETWEEN ? AND ? AND r.status = 3 "
            "  AND r.n_entries IN (7, 9) AND e.pred_top3_pct IS NOT NULL",
            (args.d_from, args.d_to))
        rows = cur.fetchall()
    if not rows:
        print("対象が0件。期間かDB接続を確認すること（黙って空の係数を出さない）", file=sys.stderr)
        return 1

    cross: dict = defaultdict(list)
    by_grade: dict = defaultdict(list)
    by_rt: dict = defaultdict(list)
    for r in rows:
        p = float(r["p"] or 0) / 100
        a = 1 if (r["f"] or 0) in (1, 2, 3) else 0
        rt, gr = race_type_group(r["race_type"]), grade_group(r["cup_grade"])
        cross[(rt, gr)].append((p, a))
        by_grade[gr].append((p, a))
        by_rt[rt].append((p, a))

    print(f"# FIT_WINDOW = \"{args.d_from}〜{args.d_to}\"   （{len(rows):,}行）")
    print("_CROSS = {")
    for k in sorted(cross):
        v = cross[k]
        if len(v) < MIN_ROWS:
            print(f"    # {k}: {len(v)}行 → 少なすぎるので採らない（1次元へフォールバック）")
            continue
        a, b = _fit(v)
        print(f'    ("{k[0]}", "{k[1]}"): ({a:.4f}, {b:+.4f}),   # n={len(v):,}')
    print("}")
    for name, d in (("_BY_GRADE", by_grade), ("_BY_RACE_TYPE", by_rt)):
        print(f"{name} = {{")
        for k in sorted(d):
            if len(d[k]) < MIN_ROWS:
                continue
            a, b = _fit(d[k])
            print(f'    "{k}": ({a:.4f}, {b:+.4f}),   # n={len(d[k]):,}')
        print("}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
