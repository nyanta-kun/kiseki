"""calculated_indices.out_probability を全期間バックフィルする。

`models/jra_out_rate_lgb.txt`（着外率ヘッド）で v26 行の着外確率を算出し DB へ書き戻す。
特徴量は `composite.py::_build_v26_features` と同一の 34 列・同順・同一欠損補完。

使い方:
    cd backend
    .venv/bin/python scripts/backfill_jra_out_probability.py
    .venv/bin/python scripts/backfill_jra_out_probability.py --start 20260101 --end 20261231
"""

from __future__ import annotations

import argparse
import logging
import os
import time
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
from psycopg2.extras import execute_values  # noqa: E402

from src.indices.composite import COMPOSITE_VERSION, OUT_PROB_FEATURE_NAMES  # noqa: E402
from scripts.train_jra_out_rate import FETCH_SQL, featurize  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_out_prob")

MODEL_PATH = _root / "models" / "jra_out_rate_lgb.txt"

# 書き込み先は**現行版の行**。旧実装は `version = 26` 固定で、本番が v27 へ上がった
# あとは 1 行も更新しないまま正常終了していた（docs/jra_rebuild_2026_08.md 4.7）。
UPDATE_SQL = f"""
UPDATE keiba.calculated_indices AS ci
SET out_probability = v.out_probability
FROM (VALUES %s) AS v(race_id, horse_id, out_probability)
WHERE ci.race_id = v.race_id
  AND ci.horse_id = v.horse_id
  AND ci.version = {COMPOSITE_VERSION}
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20991231")
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--sleep", type=float, default=0.2,
                   help="バッチ間スリープ秒（VPS DB 負荷対策）")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not MODEL_PATH.exists():
        raise SystemExit(f"モデルが見つかりません: {MODEL_PATH}"
                         " → scripts/train_jra_out_rate.py を先に実行してください")
    model = lgb.Booster(model_file=str(MODEL_PATH))

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
    logger.info(f"対象: {len(df):,}行 / {df['race_id'].nunique():,}レース")
    if df.empty:
        return

    # 発走前に判定するものなので出走取消・除外馬も含めて算出する（結果の有無も問わない）
    df = featurize(df)
    preds = np.clip(model.predict(df[OUT_PROB_FEATURE_NAMES].values), 0.0, 1.0)
    rows = [
        (int(r), int(h), round(float(p), 4))
        for r, h, p in zip(df["race_id"], df["horse_id"], preds)
    ]
    logger.info(f"算出完了: 平均着外率={preds.mean():.4f} "
                f"足切り(>=0.80)割合={float((preds >= 0.80).mean()):.1%}")

    if args.dry_run:
        logger.info("dry-run のため DB 更新はスキップ")
        return

    total = 0
    for i in range(0, len(rows), args.batch_size):
        batch = rows[i:i + args.batch_size]
        execute_values(cur, UPDATE_SQL, batch, template="(%s,%s,%s::numeric)")
        conn.commit()
        total += len(batch)
        if (i // args.batch_size) % 5 == 0:
            logger.info(f"  更新 {total:,}/{len(rows):,}")
        if args.sleep:
            time.sleep(args.sleep)
    logger.info(f"更新完了: {total:,}行")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
