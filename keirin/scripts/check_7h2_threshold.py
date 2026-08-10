#!/usr/bin/env python3
"""RANK_7H2 のエントロピー閾値を、**本番と同じ経路**で較正し直す。

`RANK_7H2_ENTROPY_MIN` は「モデル3着内率の正規化エントロピーの上位20%」を
絶対値で固定したもの。値はモデルの較正に依存するので、**モデルを再学習したら
ここで分布を確認する**（検証パイプラインの閾値をそのまま定数にすると壊れる）。

使い方:

    # 直近3ヶ月を月次vintageで測る（honest・既定）
    PYTHONPATH=. .venv/bin/python scripts/check_7h2_threshold.py

    # 期間と分位を指定
    PYTHONPATH=. .venv/bin/python scripts/check_7h2_threshold.py \\
        --months 2026-05 2026-06 2026-07 --quantile 0.8

⚠️ 過去日に本番モデル（全期間学習）を当てると in-sample になる。
   既定は月次vintage（`lgbm_wt_eval_mYYMM`）を月ごとに読み替える。

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import calendar
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7H2_ENTROPY_MIN, RANK_7H2_NE, rank_7h2_entropy,
)


def recent_months(n: int = 3) -> list[str]:
    """今日から遡って直近 n ヶ月（当月は含めない＝vintage が揃っているもの）。"""
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return sorted(out)


def entropies(month: str, model: str) -> list[float]:
    y, m = (int(x) for x in month.split("-"))
    a = f"{month}-01"
    b = f"{month}-{calendar.monthrange(y, m)[1]:02d}"
    with get_connection() as c:
        keys = {r[0] for r in c.execute(
            "SELECT race_key FROM wt_races WHERE n_entries = ? AND cancel = 0 "
            "  AND race_date BETWEEN ? AND ?", (RANK_7H2_NE, a, b))}
    if not keys:
        return []
    df = build_features_wt(load_raw_data_wt(min_date=a, max_date=b))
    df = df[df["race_key"].isin(keys)].copy()
    if df.empty:
        return []
    p = load_model(model).predict_proba(prepare_X(df))[:, 1]
    g: dict[str, dict[int, float]] = defaultdict(dict)
    for rk, fn, v in zip(df["race_key"], df["frame_no"], p):
        g[rk][int(fn)] = float(v)
    return [rank_7h2_entropy(d) for d in g.values() if len(d) == RANK_7H2_NE]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", default=None,
                    help="YYYY-MM を並べる（既定: 直近3ヶ月）")
    ap.add_argument("--quantile", type=float, default=0.8,
                    help="採用したい上側の割合の境界（既定 0.8＝上位20%%）")
    ap.add_argument("--model-prefix", default="lgbm_wt_eval_m",
                    help="月次vintageモデル名の接頭辞")
    args = ap.parse_args()

    months = args.months or recent_months()
    allv: list[float] = []
    print(f"{'月':<9s} {'n':>6s} {'p70':>8s} {'p80':>8s} {'p90':>8s} "
          f"{'現行定数での該当率':>18s}")
    for mo in months:
        tag = args.model_prefix + mo[2:4] + mo[5:7]
        v = entropies(mo, tag)
        if not v:
            print(f"{mo:<9s} {'0':>6s}  （対象レース無し / モデル {tag} 未整備）")
            continue
        allv += v
        a = np.array(v)
        print(f"{mo:<9s} {len(v):6,d} {np.quantile(a, .7):8.4f} "
              f"{np.quantile(a, .8):8.4f} {np.quantile(a, .9):8.4f} "
              f"{(a >= RANK_7H2_ENTROPY_MIN).mean() * 100:17.1f}%")
    if not allv:
        print("測定できるデータがありません")
        return
    a = np.array(allv)
    q = float(np.quantile(a, args.quantile))
    rate = (a >= RANK_7H2_ENTROPY_MIN).mean() * 100
    print(f"\n合算 n={len(a):,}")
    print(f"  推奨閾値（{args.quantile:.0%} 点） = {q:.4f}")
    print(f"  現行 RANK_7H2_ENTROPY_MIN = {RANK_7H2_ENTROPY_MIN:.4f} "
          f"→ 該当率 {rate:.1f}%（設計値 {100 * (1 - args.quantile):.0f}%）")
    if abs(rate - 100 * (1 - args.quantile)) > 5:
        print("  ⚠️ 設計値から5pt以上ずれています。定数の更新を検討してください。")
        print("     ただし**単月の振れは大きい**（実測 p80 が月で 1.842〜1.864）ので、"
              "\n     3ヶ月以上の合算で判断すること。")


if __name__ == "__main__":
    main()
