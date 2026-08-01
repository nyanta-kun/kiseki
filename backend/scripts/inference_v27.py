"""v27 composite（順位回帰 + 着外率合成）を全期間バックフィルする。

v26 行に保存済みのサブ指数をそのまま流用し、composite_index / win_probability /
place_probability / out_probability を v27 のロジックで再算出して **version=27 の行**として
書き込む（サブ指数の再計算はしない。ロジックが変わったのは合成部分だけのため）。

  composite_index   = blend_v27(reg_rank, out_prob)  … z(-reg_rank) - 0.5*z(out_prob) → 15〜85
  out_probability   = 着外率ヘッド
  win_probability   = is_win 較正ヘッドのレース内正規化（本番 composite.py と同一）
  place_probability = Harville（win から 3着以内確率）

冪等: 対象期間の version=27 行を削除してから挿入する。

⚠️ **DB に書き込まれた過去分は in-sample である**
本番モデル（jra_reg_rank_lgb.txt / jra_out_rate_lgb.txt）は全期間 refit のため、
バックフィルされた過去レースの composite_index / out_probability は
「そのレースを学習に含んだモデル」の出力になる（model-vintage look-ahead）。
未来のレースを予測する運用上は正しいが、**この値を使って過去のROI・的中率を
評価してはいけない**。honest 評価は必ず walk-forward スクリプトで行うこと:
  scripts/jra_rank_quality_review.py  (ランキング品質)
  scripts/train_jra_reg_rank.py       (--train-end で分割した test メトリクス)
  scripts/train_jra_out_rate.py       (同上・足切り閾値の性能)
参考: memory/chihou_survivor_bias_audit_2026_07_23.md（同型の失敗事例）

使い方:
    cd backend
    .venv/bin/python scripts/inference_v27.py --dry-run
    .venv/bin/python scripts/inference_v27.py
    .venv/bin/python scripts/inference_v27.py --start 20260101 --end 20261231
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
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
from psycopg2.extras import execute_values  # noqa: E402

from scripts.train_jra_out_rate import featurize  # noqa: E402
from src.indices.composite import (  # noqa: E402
    COMPOSITE_VERSION,
    OUT_PROB_FEATURE_NAMES,
    CompositeIndexCalculator,
    blend_v27,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inference_v27")

MODELS_DIR = _root / "models"
REG_RANK_MODEL = MODELS_DIR / "jra_reg_rank_lgb.txt"
OUT_RATE_MODEL = MODELS_DIR / "jra_out_rate_lgb.txt"
ISWIN_MODEL = MODELS_DIR / "v26_iswin_calib.txt"

SOURCE_VERSION = 26

# v27 行にコピーするサブ指数（DB 列名）
SUBINDEX_COLUMNS = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]

FETCH_SQL = f"""
SELECT
    r.date, ci.race_id, ci.horse_id,
    {", ".join("ci." + c for c in SUBINDEX_COLUMNS)},
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position
FROM keiba.calculated_indices ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = {SOURCE_VERSION}
  AND r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
ORDER BY ci.race_id, ci.horse_id
"""

INSERT_SQL = f"""
INSERT INTO keiba.calculated_indices
  (race_id, horse_id, version, {", ".join(SUBINDEX_COLUMNS)},
   composite_index, win_probability, place_probability, out_probability, calculated_at)
VALUES %s
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20991231")
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--sleep", type=float, default=0.2, help="バッチ間スリープ秒（VPS DB 負荷対策）")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    for path in (REG_RANK_MODEL, OUT_RATE_MODEL):
        if not path.exists():
            raise SystemExit(f"モデルが見つかりません: {path}")
    reg_model = lgb.Booster(model_file=str(REG_RANK_MODEL))
    out_model = lgb.Booster(model_file=str(OUT_RATE_MODEL))
    iswin_model = lgb.Booster(model_file=str(ISWIN_MODEL)) if ISWIN_MODEL.exists() else None
    if iswin_model is None:
        logger.warning(f"{ISWIN_MODEL} が無いため win_probability は softmax フォールバック")

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    logger.info(f"対象(v{SOURCE_VERSION}): {len(df):,}行 / {df['race_id'].nunique():,}レース")
    if df.empty:
        return

    df = featurize(df)
    X = df[OUT_PROB_FEATURE_NAMES].values
    df["_reg"] = reg_model.predict(X)
    df["_out"] = np.clip(out_model.predict(X), 0.0, 1.0)
    df["_iswin_raw"] = (
        np.clip(iswin_model.predict(X), 1e-9, 1.0) if iswin_model is not None else np.nan
    )

    # レース単位で合成・確率化（本番 composite.py と同じ処理）
    records: list[tuple] = []
    for _, g in df.groupby("race_id", sort=False):
        comps = blend_v27(g["_reg"].values, g["_out"].values)
        if iswin_model is not None:
            raw_w = g["_iswin_raw"].values
            total = float(raw_w.sum())
            win_p = list(raw_w / total) if total > 0 else None
        else:
            win_p = None
        if win_p is None:
            win_p = CompositeIndexCalculator._softmax([float(c) for c in comps])
        place_p = CompositeIndexCalculator._harville_place_probs(list(win_p))
        for i, (_, row) in enumerate(g.iterrows()):
            sub = [None if pd.isna(row[c]) else float(row[c]) for c in SUBINDEX_COLUMNS]
            records.append((
                int(row["race_id"]), int(row["horse_id"]), COMPOSITE_VERSION, *sub,
                float(comps[i]), round(float(win_p[i]), 4), round(float(place_p[i]), 4),
                round(float(row["_out"]), 4),
            ))

    logger.info(f"算出完了: {len(records):,}行  "
                f"composite平均={np.mean([r[3 + len(SUBINDEX_COLUMNS)] for r in records]):.2f}")

    if args.dry_run:
        logger.info("dry-run のため DB 更新はスキップ")
        return

    # 冪等性のため対象期間の v27 行を先に削除
    cur.execute(
        """
        DELETE FROM keiba.calculated_indices ci
        USING keiba.races r
        WHERE r.id = ci.race_id AND ci.version = %(ver)s
          AND r.date >= %(start)s AND r.date <= %(end)s
        """,
        {"ver": COMPOSITE_VERSION, "start": args.start, "end": args.end},
    )
    logger.info(f"既存 v{COMPOSITE_VERSION} 行を削除: {cur.rowcount:,}行")
    conn.commit()

    template = "(" + ",".join(["%s"] * (3 + len(SUBINDEX_COLUMNS) + 4)) + ", NOW())"
    total = 0
    for i in range(0, len(records), args.batch_size):
        batch = records[i:i + args.batch_size]
        execute_values(cur, INSERT_SQL, batch, template=template)
        conn.commit()
        total += len(batch)
        if (i // args.batch_size) % 5 == 0:
            logger.info(f"  挿入 {total:,}/{len(records):,}")
        if args.sleep:
            time.sleep(args.sleep)
    logger.info(f"挿入完了: {total:,}行 (version={COMPOSITE_VERSION})")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
