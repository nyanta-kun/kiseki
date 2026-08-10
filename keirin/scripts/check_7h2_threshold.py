#!/usr/bin/env python3
"""RANK_7H2 の2つの閾値を、**本番と同じ経路**で較正し直す。

  - `RANK_7H2_ENTROPY_MIN`      … 3着内率の正規化エントロピーの上位20%（母集団）
  - `RANK_7H2_HONMEI_SHARE_MAX` … ◎の3着内率シェアの上位20%を除外（母集団の中で）

どちらもモデルの較正に依存するので、**モデルを再学習したらここで分布を確認する**
（検証パイプラインの閾値をそのまま定数にすると壊れる。実例: エントロピーは
1.8485→1.8534、◎シェアは 0.2452→0.2430 のずれがあった）。

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
    RANK_7H2_ENTROPY_MIN, RANK_7H2_HONMEI_SHARE_MAX, RANK_7H2_NE,
    rank_7h2_entropy, rank_7h2_honmei_share,
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


def measure(month: str, model: str) -> tuple[list[float], list[float]]:
    """(全7車のエントロピー, 7H2母集団の◎シェア) を返す。

    ◎シェアは**エントロピー条件を通ったレースだけ**で測る（本番の選別順序と同じ）。
    母集団を絞らずに測ると分位がずれる。
    """
    y, m = (int(x) for x in month.split("-"))
    a = f"{month}-01"
    b = f"{month}-{calendar.monthrange(y, m)[1]:02d}"
    with get_connection() as c:
        keys = {r[0] for r in c.execute(
            "SELECT race_key FROM wt_races WHERE n_entries = ? AND cancel = 0 "
            "  AND race_date BETWEEN ? AND ?", (RANK_7H2_NE, a, b))}
        marks: dict[str, dict[int, int | None]] = defaultdict(dict)
        kl = sorted(keys)
        for i in range(0, len(kl), 700):
            ch = kl[i:i + 700]
            q = ("SELECT race_key, frame_no, prediction_mark FROM wt_entries "
                 "WHERE race_key IN (%s)" % ",".join("?" * len(ch)))
            for r in c.execute(q, ch):
                marks[r["race_key"]][int(r["frame_no"])] = r["prediction_mark"]
    if not keys:
        return [], []
    df = build_features_wt(load_raw_data_wt(min_date=a, max_date=b))
    df = df[df["race_key"].isin(keys)].copy()
    if df.empty:
        return [], []
    p = load_model(model).predict_proba(prepare_X(df))[:, 1]
    g: dict[str, dict[int, float]] = defaultdict(dict)
    for rk, fn, v in zip(df["race_key"], df["frame_no"], p):
        g[rk][int(fn)] = float(v)
    ents, shares = [], []
    for rk, d in g.items():
        if len(d) != RANK_7H2_NE:
            continue
        e = rank_7h2_entropy(d)
        ents.append(e)
        if e < RANK_7H2_ENTROPY_MIN:
            continue
        hs = rank_7h2_honmei_share(d, marks.get(rk, {}))
        if hs is not None:
            shares.append(hs)
    return ents, shares


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
    all_e: list[float] = []
    all_s: list[float] = []
    print(f"{'月':<9s} {'全7車n':>7s} {'ent p80':>9s} {'該当率':>8s} | "
          f"{'母集団n':>8s} {'◎share p80':>11s} {'除外率':>8s}")
    for mo in months:
        tag = args.model_prefix + mo[2:4] + mo[5:7]
        e, sh = measure(mo, tag)
        if not e:
            print(f"{mo:<9s} {'0':>7s}  （対象レース無し / モデル {tag} 未整備）")
            continue
        all_e += e
        all_s += sh
        ae, as_ = np.array(e), (np.array(sh) if sh else np.array([np.nan]))
        print(f"{mo:<9s} {len(e):7,d} {np.quantile(ae, .8):9.4f} "
              f"{(ae >= RANK_7H2_ENTROPY_MIN).mean() * 100:7.1f}% | "
              f"{len(sh):8,d} {np.nanquantile(as_, .8):11.4f} "
              f"{(as_ > RANK_7H2_HONMEI_SHARE_MAX).mean() * 100:7.1f}%")
    if not all_e:
        print("測定できるデータがありません")
        return
    ae = np.array(all_e)
    rate = (ae >= RANK_7H2_ENTROPY_MIN).mean() * 100
    print(f"\n合算 全7車 n={len(ae):,}")
    print(f"  エントロピー: 推奨 {float(np.quantile(ae, args.quantile)):.4f} / "
          f"現行 {RANK_7H2_ENTROPY_MIN:.4f} → 該当率 {rate:.1f}%（設計 20%）")
    if abs(rate - 20) > 5:
        print("  ⚠️ 設計値から5pt以上ずれています。定数の更新を検討してください。")
    if all_s:
        as_ = np.array(all_s)
        ex = (as_ > RANK_7H2_HONMEI_SHARE_MAX).mean() * 100
        print(f"  ◎シェア（母集団 n={len(as_):,}）: 推奨 "
              f"{float(np.quantile(as_, 0.8)):.4f} / "
              f"現行 {RANK_7H2_HONMEI_SHARE_MAX:.4f} → 除外率 {ex:.1f}%（設計 20%）")
        if abs(ex - 20) > 5:
            print("  ⚠️ 設計値から5pt以上ずれています。定数の更新を検討してください。")
    print("\n  ⚠️ **単月の振れは大きい**（エントロピー p80 が月で 1.842〜1.864）。"
          "\n     3ヶ月以上の合算で判断すること。")


if __name__ == "__main__":
    main()
