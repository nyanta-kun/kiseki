"""39特徴を「本番ORM実装」と「学習/検証pandas実装」で両方作って列ごとに差分を出す。

tests/test_chihou_model_parity.py は列名と順序しか見ておらず、
**値が一致することは誰も検査していない**。ここを実測する。
"""
from __future__ import annotations
import argparse, asyncio, sys
from pathlib import Path
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path: sys.path.insert(0, str(_root))
from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import numpy as np, pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402
from src.db.session import AsyncSessionLocal  # noqa: E402
from src.db.chihou_models import ChihouRace, ChihouRaceEntry  # noqa: E402
from src.indices.chihou_calculator import (  # noqa: E402
    ChihouIndexCalculator, _LGB_FEATURE_NAMES, _build_lgb_features, _date_to_str,
)
from scripts.chihou_rank_quality_review import connect  # noqa: E402
from scripts.train_chihou_market_lgb import PROD_FEATURES, prep  # noqa: E402
from scripts.train_chihou_prod_lgb import CHIHOU_V9_VERSION  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from scripts.inference_chihou_v14 import INFER_QUERY  # noqa: E402


async def orm_features(race_ids: list[int]) -> pd.DataFrame:
    rows = []
    async with AsyncSessionLocal() as db:
        calc = ChihouIndexCalculator(db)
        await calc._ensure_par_times(); await calc._ensure_par_l3f()
        for rid in race_ids:
            race = (await db.execute(select(ChihouRace).where(ChihouRace.id == rid))).scalar_one()
            entries = list((await db.execute(
                select(ChihouRaceEntry).where(ChihouRaceEntry.race_id == rid))).scalars().all())
            if not entries: continue
            rd = _date_to_str(race.date)
            speed = await calc._speed_batch(rid, race, entries)
            l3f = await calc._last3f_batch(rid, race, entries)
            jk = await calc._jockey_batch(rd, race.course, entries)
            rot = await calc._rotation_batch(rd, entries)
            lm = await calc._last_margin_batch(rd, entries)
            hist = await calc._history_features_batch(rd, race, entries)
            ext = await calc._fetch_external_raw(rid, entries)
            wet = await calc._wet_apt_batch(rd, entries)
            corner = await calc._corner_features_batch(rd, entries)
            trainer = await calc._trainer_features_batch(rd, entries)
            X = _build_lgb_features(entries, race, speed, l3f, jk, rot, lm, hist, ext,
                                    wet, odds_map_race=None, corner_map=corner,
                                    trainer_feat_map=trainer)
            for e, x in zip(entries, X):
                rows.append({"race_id": rid, "horse_id": e.horse_id,
                             **dict(zip(_LGB_FEATURE_NAMES, x))})
    return pd.DataFrame(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True); p.add_argument("--course", default="大井")
    p.add_argument("--serve-mask", action="store_true", help="配信時(condition/head_count 不明)を再現")
    a = p.parse_args()

    conn = connect(); cur = conn.cursor()
    q = INFER_QUERY.replace("AND r.head_count >= 6", "")
    cur.execute(q, {"ver": CHIHOU_V9_VERSION, "start": a.date, "end": a.date})
    raw = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    for c in ["finish_position","win_odds","win_popularity","head_count"]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw = raw.drop_duplicates(subset=["race_id","horse_id"])
    raw = raw[raw["course_name"] == a.course].reset_index(drop=True)
    if a.serve_mask:
        # 配信時の状態を再現（00:01 時点で condition/head_count は NULL）
        raw["condition"] = None
        raw["head_count"] = raw.groupby("race_id")["horse_id"].transform("count")
    hist = fetch_hist(conn)
    pdf = prep(conn, raw, hist)
    conn.close()

    race_ids = sorted(pdf["race_id"].unique().tolist())
    odf = asyncio.run(orm_features(race_ids))

    m = pdf.merge(odf, on=["race_id","horse_id"], suffixes=("_pd","_orm"))
    print(f"\n突合 {len(m)}行 / {m['race_id'].nunique()}R\n")
    print(f"{'feature':24s} {'一致率':>7s} {'絶対平均差':>10s} {'最大差':>10s}")
    print("-"*56)
    bad = []
    for f in _LGB_FEATURE_NAMES:
        a_ = pd.to_numeric(m[f+"_pd"], errors="coerce").astype(float)
        b_ = pd.to_numeric(m[f+"_orm"], errors="coerce").astype(float)
        d = (a_ - b_).abs()
        same = float((d <= 1e-6).mean())
        print(f"{f:24s} {100*same:6.1f}% {d.mean():10.4f} {d.max():10.4f}")
        if same < 0.999: bad.append((f, same, d.mean(), d.max()))
    print("\n=== 食い違う特徴 ===")
    for f, s, mn, mx in sorted(bad, key=lambda t: t[1]):
        print(f"  {f:24s} 一致 {100*s:5.1f}%  絶対平均差 {mn:.4f}  最大 {mx:.4f}")

if __name__ == "__main__":
    main()
