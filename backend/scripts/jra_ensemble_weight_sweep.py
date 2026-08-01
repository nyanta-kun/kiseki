"""本番 composite のアンサンブル重み（LGB vs v24線形和）を honest 窓で掃引する。

本番: composite = 0.3 * LGB(min-max 15-85) + 0.7 * v24線形和
この 0.3/0.7 は 2026-05-02 に 1,072R の ROI 基準で決めた値。
ランキング品質（上位/下位の並び）を基準に選び直すとどうなるかを測る。

honest 性:
  - v26_lightgbm_rank.txt の学習期間は 2023-05〜2025-06。test は 2026-01 以降のみ使う。
  - v24 線形和は DB の version=24 行の composite_index をそのまま使う
    （ルールベース重みの線形和なのでモデル汚染はない）。v24 は 2026-04-26 まで存在。

使い方:
    cd backend
    .venv/bin/python scripts/jra_ensemble_weight_sweep.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.jra_rank_quality_review import FEATURES, evaluate, featurize  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ensemble_sweep")

MODELS_DIR = _root / "models"

SQL = """
SELECT
    r.date, ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position,
    ci.composite_index,
    v24.composite_index AS linear_index
FROM keiba.calculated_indices ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.calculated_indices v24
     ON v24.race_id = ci.race_id AND v24.horse_id = ci.horse_id AND v24.version = 24
WHERE ci.version = 26
  AND r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


def scale_in_race(df: pd.DataFrame, col: str) -> np.ndarray:
    """レース内 min-max → 15-85（v26 本番が使っていた変換の再現）。"""
    g = df.groupby("race_id")[col]
    lo, hi = g.transform("min"), g.transform("max")
    rng = (hi - lo).replace(0, np.nan)
    return (15.0 + (df[col] - lo) / rng * 70.0).fillna(50.0).values


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20260101", help="v26 LGB の学習期間より後にすること")
    p.add_argument("--end", default="20260426", help="v24 行が存在する最終日")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(SQL, {"start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    conn.close()

    df = featurize(df)
    df["linear_index"] = pd.to_numeric(df["linear_index"], errors="coerce")
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df[df["linear_index"].notna() & df["composite_index"].notna()]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    logger.info(f"test: {len(df):,}頭 / {df.race_id.nunique():,}レース "
                f"({df['date'].min()}〜{df['date'].max()})")

    booster = lgb.Booster(model_file=str(MODELS_DIR / "v26_lightgbm_rank.txt"))
    df["lgb_raw"] = booster.predict(df[FEATURES].values)
    df["lgb_scaled"] = scale_in_race(df, "lgb_raw")

    keys = ["HEAD_top1_win", "HEAD_top1_place", "HEAD_winner_in_top3", "HEAD_ndcg3",
            "TAIL_bot3_out_rate", "TAIL_placer_in_bot30pct", "ALL_spearman"]
    print("\n" + "=" * 118)
    print("アンサンブル重み掃引: composite = w*LGB(15-85) + (1-w)*v24線形和   ※w=0.3 が現行本番")
    print("=" * 118)
    print(f"{'w_lgb':>7}" + "".join(f"{k.split('_', 1)[1][:17]:>17}" for k in keys))
    results = {}
    for w in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        df["_s"] = w * df["lgb_scaled"] + (1 - w) * df["linear_index"]
        m = evaluate(df, "_s")
        results[f"w={w:.1f}"] = m
        mark = "  ← 現行" if abs(w - 0.3) < 1e-9 else ""
        print(f"{w:>7.1f}" + "".join(f"{m[k]:>17.4f}" for k in keys) + mark)

    # 参考: DB に保存されている本番値そのもの（丸め・スケール差の確認用）
    m = evaluate(df, "composite_index")
    results["prod_db"] = m
    print(f"{'prod_db':>7}" + "".join(f"{m[k]:>17.4f}" for k in keys))

    out = MODELS_DIR / "jra_ensemble_weight_sweep.json"
    out.write_text(json.dumps(
        {"period": [df["date"].min(), df["date"].max()],
         "n_races": int(df.race_id.nunique()), "results": results},
        ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
