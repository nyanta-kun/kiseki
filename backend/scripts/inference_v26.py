"""v26 LightGBM 推論で composite_index を全レース算出して DB に保存する。

v24 の sub-indices を入力に、学習済みモデルでスコアを予測 → 0-100 にスケーリングして
composite_index として CalculatedIndex に upsert する。

使い方:
    python scripts/inference_v26.py --start 20230501 --end 20260501
    python scripts/inference_v26.py --model models/v26_lightgbm_rank.txt --concurrency 16
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
from sqlalchemy import select, text

from src.db.models import CalculatedIndex
from src.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v26_inference")

V24_VERSION = 24
V26_VERSION = 26
DEFAULT_MODEL = _root / "models" / "v26_lightgbm_rank.txt"

SUBINDEX_FEATURES = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]
RACE_FEATURES = ["distance", "head_count", "is_turf", "is_dirt", "is_jump",
                 "is_good", "is_yaya", "is_heavy", "is_bad", "is_g1g2g3"]
HORSE_FEATURES = ["frame_number", "horse_age", "weight_carried", "horse_weight",
                  "weight_change", "jvan_time_dm", "jvan_battle_dm"]
ALL_FEATURES = SUBINDEX_FEATURES + RACE_FEATURES + HORSE_FEATURES

# DB に保存する composite_index は 0-100 にスケール
INDEX_MIN = 0.0
INDEX_MAX = 100.0


FETCH_SQL = """
SELECT
    ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    rr.weight_change,
    re.jvan_time_dm, re.jvan_battle_dm
FROM keiba.calculated_indices ci
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = %(ver)s
  AND r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10');
"""


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.startswith("芝").astype(int)
    df["is_dirt"] = s.str.startswith("ダ").astype(int)
    df["is_jump"] = s.str.startswith("障").astype(int)
    c = df["condition"].fillna("").astype(str)
    df["is_good"] = (c == "良").astype(int)
    df["is_yaya"] = (c == "稍").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"] = (c == "不").astype(int)
    g = df["grade"].fillna("").astype(str)
    df["is_g1g2g3"] = g.str.match(r"^G[1-3]$").astype(int)
    for c in SUBINDEX_FEATURES + HORSE_FEATURES + ["distance", "head_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def scale_to_index(scores: np.ndarray, race_ids: pd.Series) -> np.ndarray:
    """レース内で min-max スケーリング → 0-100 の indexに変換。

    レース間でスコアは比較できないため、各レース内で正規化する。
    最高得点=85, 最低=15 程度になるよう中央化。
    """
    df = pd.DataFrame({"race_id": race_ids.values, "score": scores})
    out = np.zeros(len(scores), dtype=float)
    for rid, idx in df.groupby("race_id").indices.items():
        s = df.loc[idx, "score"].values
        if len(s) <= 1:
            out[idx] = 50.0
            continue
        lo, hi = s.min(), s.max()
        if hi - lo < 1e-9:
            out[idx] = 50.0
            continue
        # 0-1 → 15-85（端を緩める）
        out[idx] = 15.0 + (s - lo) / (hi - lo) * 70.0
    return out


async def upsert_v26(rows: list[dict]) -> int:
    """v26 行を upsert（既存があれば composite/probabilities だけ更新）。"""
    if not rows:
        return 0

    async with AsyncSessionLocal() as session:
        # まず race_id 単位で existing v26 を取得
        race_ids = list({r["race_id"] for r in rows})
        existing_rows = (
            await session.execute(
                select(CalculatedIndex).where(
                    CalculatedIndex.race_id.in_(race_ids),
                    CalculatedIndex.version == V26_VERSION,
                )
            )
        ).scalars().all()
        ex_map: dict[tuple[int, int], CalculatedIndex] = {
            (e.race_id, e.horse_id): e for e in existing_rows
        }

        # v24 の sub-indices をコピー（同じ race_id+horse_id で v24 を引く）
        v24_rows = (
            await session.execute(
                select(CalculatedIndex).where(
                    CalculatedIndex.race_id.in_(race_ids),
                    CalculatedIndex.version == V24_VERSION,
                )
            )
        ).scalars().all()
        v24_map: dict[tuple[int, int], CalculatedIndex] = {
            (e.race_id, e.horse_id): e for e in v24_rows
        }

        new_records: list[CalculatedIndex] = []
        for r in rows:
            key = (r["race_id"], r["horse_id"])
            v24 = v24_map.get(key)
            kw = {
                "speed_index": v24.speed_index if v24 else None,
                "last_3f_index": v24.last_3f_index if v24 else None,
                "course_aptitude": v24.course_aptitude if v24 else None,
                "position_advantage": v24.position_advantage if v24 else None,
                "rotation_index": v24.rotation_index if v24 else None,
                "jockey_index": v24.jockey_index if v24 else None,
                "pace_index": v24.pace_index if v24 else None,
                "pedigree_index": v24.pedigree_index if v24 else None,
                "training_index": v24.training_index if v24 else None,
                "anagusa_index": v24.anagusa_index if v24 else None,
                "paddock_index": v24.paddock_index if v24 else None,
                "rebound_index": v24.rebound_index if v24 else None,
                "rivals_growth_index": v24.rivals_growth_index if v24 else None,
                "career_phase_index": v24.career_phase_index if v24 else None,
                "distance_change_index": v24.distance_change_index if v24 else None,
                "jockey_trainer_combo_index": v24.jockey_trainer_combo_index if v24 else None,
                "going_pedigree_index": v24.going_pedigree_index if v24 else None,
                "composite_index": Decimal(str(r["composite_index"])),
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
                    CalculatedIndex(
                        race_id=r["race_id"],
                        horse_id=r["horse_id"],
                        version=V26_VERSION,
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
    p.add_argument("--batch-days", type=int, default=10, help="DBから日付をN日ずつ取得")
    args = p.parse_args()

    logger.info(f"v26 推論: {args.start}〜{args.end}, model={args.model}")
    model = lgb.Booster(model_file=args.model)

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"ver": V24_VERSION, "start": args.start, "end": args.end})
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

    # win_probability はレース内 softmax(temperature=10)
    df["score"] = indices
    rec_list = []
    for rid, idx in df.groupby("race_id").indices.items():
        scores = df.loc[idx, "score"].values
        s_t = scores / 10.0
        ex = np.exp(s_t - s_t.max())
        win_p = ex / ex.sum()
        # 簡易: place_p = win_p × 3 (上限1.0)
        place_p = np.clip(win_p * 3.0, 0.0, 1.0)
        for j, i in enumerate(idx):
            rec_list.append({
                "race_id": int(df.loc[i, "race_id"]),
                "horse_id": int(df.loc[i, "horse_id"]),
                "composite_index": round(float(scores[j]), 1),
                "win_probability": round(float(win_p[j]), 4),
                "place_probability": round(float(place_p[j]), 4),
            })

    # batch upsert (1000件ずつ)
    batch_size = 1000
    total = 0
    for i in range(0, len(rec_list), batch_size):
        batch = rec_list[i:i + batch_size]
        n = await upsert_v26(batch)
        total += n
        if (i // batch_size) % 10 == 0:
            logger.info(f"upsert: {total:,}/{len(rec_list):,}")
    logger.info(f"完了: {total:,}行 upsert")


if __name__ == "__main__":
    asyncio.run(main_async())
