"""地方競馬 v10 LightGBM 推論スクリプト

⚠️ 非推奨 (2026-06-05): 本番取込パス (chihou_calculator.calculate_and_save) が
純LGB(17特徴)を直接算出するようになったため、このバッチ推論は不要。
本スクリプトは (a) win_probability が LGB単独softmaxで composite(アンサンブル)と
不整合 (b) version=9 行を入力に要求し直近レースに適用不能、という既知問題があり、
実行すると本番 version=10(純LGB) を上書きしてしまう。原則使用しないこと。
モデル比較は scripts/chihou_model_compare.py、本番学習は train_chihou_prod_lgb.py。

学習済みモデルで composite_index / win_probability を算出し、
chihou.calculated_indices (version=10) に upsert する。

アンサンブル: LGB_WEIGHT * lgb_score + LINEAR_WEIGHT * v9_composite
  デフォルト: 0.3 * lgb + 0.7 * v9 (JRA v26 と同比率)

使い方:
  cd backend
  # 期間指定
  .venv/bin/python scripts/inference_chihou_v10.py --start 20240101 --end 20260503
  # 本番（当日のみ）
  .venv/bin/python scripts/inference_chihou_v10.py --start 20260503 --end 20260503
  # モデル指定
  .venv/bin/python scripts/inference_chihou_v10.py --start 20260101 --end 20260503 \\
      --model models/chihou_v10_lightgbm_rank.txt --lgb-weight 0.4 --linear-weight 0.6
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
from sqlalchemy import select

from src.db.chihou_models import ChihouCalculatedIndex
from src.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_v10_inference")

CHIHOU_V9_VERSION = 9
CHIHOU_V10_VERSION = 10
DEFAULT_MODEL = _root / "models" / "chihou_v10_lightgbm_binary.txt"

SUBINDEX_FEATURES = [
    "speed_index", "last3f_index", "jockey_index", "rotation_index", "last_margin_index",
]
RACE_FEATURES = [
    "distance", "head_count",
    "is_turf", "is_dirt",
    "is_good", "is_heavy", "is_bad",
]
HORSE_FEATURES = [
    "frame_number", "horse_age", "weight_carried", "horse_weight", "weight_change",
]
ALL_FEATURES = SUBINDEX_FEATURES + RACE_FEATURES + HORSE_FEATURES

FETCH_SQL = """
SELECT
    ci.race_id,
    ci.horse_id,
    COALESCE(ci.speed_index, 50.0)        AS speed_index,
    COALESCE(ci.last3f_index, 50.0)       AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)       AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)     AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0)  AS last_margin_index,
    r.distance,
    r.head_count,
    r.surface,
    r.condition,
    re.frame_number,
    re.horse_age,
    re.weight_carried,
    COALESCE(rr.horse_weight, 500)        AS horse_weight,
    COALESCE(rr.weight_change, 0)         AS weight_change
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = %(ver)s
  AND r.course != '83'
  AND r.date BETWEEN %(start)s AND %(end)s
"""


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.contains("芝").astype(int)
    df["is_dirt"] = s.str.contains("ダ").astype(int)
    c = df["condition"].fillna("").astype(str)
    df["is_good"] = (c == "良").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"] = (c == "不").astype(int)
    for col in ALL_FEATURES:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def scale_to_index(scores: np.ndarray, race_ids: pd.Series) -> np.ndarray:
    """レース内 min-max スケーリング → 15-85 範囲にクリップ（v26と同方式）。"""
    out = np.zeros(len(scores), dtype=float)
    df = pd.DataFrame({"race_id": race_ids.values, "score": scores})
    for rid, idx in df.groupby("race_id").indices.items():
        s = df.loc[idx, "score"].values
        if len(s) <= 1:
            out[idx] = 50.0
            continue
        lo, hi = s.min(), s.max()
        if hi - lo < 1e-9:
            out[idx] = 50.0
            continue
        out[idx] = 15.0 + (s - lo) / (hi - lo) * 70.0
    return out


async def upsert_v10(
    rows: list[dict],
    lgb_weight: float,
    linear_weight: float,
) -> int:
    """v10 行を upsert する。composite はアンサンブル: lgb * v9。"""
    if not rows:
        return 0

    async with AsyncSessionLocal() as session:
        race_ids = list({r["race_id"] for r in rows})

        existing_v10 = (
            await session.execute(
                select(ChihouCalculatedIndex).where(
                    ChihouCalculatedIndex.race_id.in_(race_ids),
                    ChihouCalculatedIndex.version == CHIHOU_V10_VERSION,
                )
            )
        ).scalars().all()
        ex_map = {(e.race_id, e.horse_id): e for e in existing_v10}

        v9_rows = (
            await session.execute(
                select(ChihouCalculatedIndex).where(
                    ChihouCalculatedIndex.race_id.in_(race_ids),
                    ChihouCalculatedIndex.version == CHIHOU_V9_VERSION,
                )
            )
        ).scalars().all()
        v9_map = {(e.race_id, e.horse_id): e for e in v9_rows}

        new_records: list[ChihouCalculatedIndex] = []
        for r in rows:
            key = (r["race_id"], r["horse_id"])
            v9 = v9_map.get(key)
            lgb_score = float(r["composite_index"])

            if v9 is not None and v9.composite_index is not None:
                v9_score = float(v9.composite_index)
                composite = round(lgb_weight * lgb_score + linear_weight * v9_score, 1)
            else:
                composite = round(lgb_score, 1)

            kw = {
                "speed_index": v9.speed_index if v9 else None,
                "last3f_index": v9.last3f_index if v9 else None,
                "jockey_index": v9.jockey_index if v9 else None,
                "rotation_index": v9.rotation_index if v9 else None,
                "last_margin_index": v9.last_margin_index if v9 else None,
                "composite_index": Decimal(str(composite)),
                "win_probability": Decimal(str(r["win_probability"])),
                "place_probability": Decimal(str(r["place_probability"])),
            }
            if key in ex_map:
                e = ex_map[key]
                for k, v in kw.items():
                    setattr(e, k, v)
                e.calculated_at = datetime.now()
            else:
                new_records.append(
                    ChihouCalculatedIndex(
                        race_id=r["race_id"],
                        horse_id=r["horse_id"],
                        version=CHIHOU_V10_VERSION,
                        **kw,
                    )
                )
        if new_records:
            session.add_all(new_records)
        await session.commit()
        return len(rows)


async def main_async() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--model", default=str(DEFAULT_MODEL))
    p.add_argument("--lgb-weight", type=float, default=0.3)
    p.add_argument("--linear-weight", type=float, default=0.7)
    p.add_argument("--batch-size", type=int, default=1000)
    args = p.parse_args()

    logger.info(f"v10 推論: {args.start}〜{args.end}, model={args.model}")
    model = lgb.Booster(model_file=args.model)

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"ver": CHIHOU_V9_VERSION, "start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    logger.info(f"取得: {len(rows):,}行")
    if not rows:
        return

    df = pd.DataFrame(rows, columns=cols)
    df = featurize(df)

    X = df[ALL_FEATURES].values
    raw_scores = model.predict(X)
    indices = scale_to_index(raw_scores, df["race_id"])

    # win_probability: レース内 softmax（v26と同方式）
    df["lgb_index"] = indices
    rec_list: list[dict] = []
    for rid, idx in df.groupby("race_id").indices.items():
        scores = df.loc[idx, "lgb_index"].values
        s_t = scores / 10.0
        ex = np.exp(s_t - s_t.max())
        win_p = ex / ex.sum()
        place_p = np.clip(win_p * 3.0, 0.0, 1.0)
        for j, i in enumerate(idx):
            rec_list.append({
                "race_id": int(df.loc[i, "race_id"]),
                "horse_id": int(df.loc[i, "horse_id"]),
                "composite_index": round(float(scores[j]), 1),
                "win_probability": round(float(win_p[j]), 4),
                "place_probability": round(float(place_p[j]), 4),
            })

    logger.info(f"アンサンブル: LGB={args.lgb_weight}, v9={args.linear_weight}")
    total = 0
    for i in range(0, len(rec_list), args.batch_size):
        batch = rec_list[i:i + args.batch_size]
        n = await upsert_v10(batch, args.lgb_weight, args.linear_weight)
        total += n
        if (i // args.batch_size) % 10 == 0:
            logger.info(f"upsert: {total:,}/{len(rec_list):,}")
    logger.info(f"完了: {total:,}行 upsert (version={CHIHOU_V10_VERSION})")


if __name__ == "__main__":
    asyncio.run(main_async())
