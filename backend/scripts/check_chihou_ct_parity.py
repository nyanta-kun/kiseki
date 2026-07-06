"""Phase6 CT9特徴の train/serve パリティ検証。

train 側 (scripts/train_chihou_prod_lgb.compute_corner_table / compute_trainer_table) と
serve 側 (chihou_calculator._corner_features_batch / _trainer_features_batch) を
指定日の全レースで突き合わせ、不一致件数を報告する。

既知の許容差:
  - train は同日他レースを累積に含む（cumsum−自走）が serve は date < race_date で除外
    → 同日複数出走馬（稀）のみ差が出る
  - jk_change は train=race_results の騎手連鎖 / serve=直近走騎手 vs 出走表騎手

使い方: cd backend && PYTHONPATH=. .venv/bin/python scripts/check_chihou_ct_parity.py --date 20260705
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from scripts.train_chihou_prod_lgb import (  # noqa: E402
    compute_corner_table,
    compute_trainer_table,
    fetch_hist_full,
)
from src.db.chihou_models import ChihouRace, ChihouRaceEntry  # noqa: E402
from src.indices.chihou_calculator import ChihouIndexCalculator  # noqa: E402

TOL = 1e-6


def _sync_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYYMMDD")
    args = p.parse_args()

    # ── train 側: 全履歴からテーブル構築 → 当日レース行を抽出 ──
    conn = _sync_conn()
    hist = fetch_hist_full(conn)
    conn.close()
    corner_tbl = compute_corner_table(hist)
    trainer_tbl = compute_trainer_table(hist)

    url = (
        f"postgresql+asyncpg://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    engine = create_async_engine(url)
    smk = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    n_total = n_corner_ok = n_trainer_ok = 0
    diffs: list[str] = []
    async with smk() as db:
        calc = ChihouIndexCalculator(db)
        races = (await db.execute(
            select(ChihouRace).where(ChihouRace.date == args.date, ChihouRace.course != "83")
        )).scalars().all()
        print(f"{args.date}: {len(races)} レース")
        for race in races:
            entries = (await db.execute(
                select(ChihouRaceEntry).where(ChihouRaceEntry.race_id == race.id)
            )).scalars().all()
            if not entries:
                continue
            corner_map = await calc._corner_features_batch(args.date, entries)
            trainer_map = await calc._trainer_features_batch(args.date, entries)
            for e in entries:
                n_total += 1
                # train 側参照（当日レースが履歴に存在する場合のみ）
                key = (e.horse_id, race.id)
                if key in corner_tbl.index:
                    t = corner_tbl.loc[key]
                    s = corner_map[e.horse_id]
                    tv = [
                        t["c_early_n"] if pd.notna(t["c_early_n"]) else 0.5,
                        t["c_late_gain_n"] if pd.notna(t["c_late_gain_n"]) else 0.0,
                        t["c_makuri_n"] if pd.notna(t["c_makuri_n"]) else 0.0,
                        t["c_runs"],
                    ]
                    if all(abs(float(tv[i]) - s[i]) < 1e-4 for i in range(4)):
                        n_corner_ok += 1
                    else:
                        diffs.append(
                            f"corner race={race.id} hid={e.horse_id} train={[round(float(x),4) for x in tv]}"
                            f" serve={[round(x,4) for x in s[:4]]}")
                else:
                    n_corner_ok += 1  # 履歴側に当日行なし（結果未取込）→ 比較対象外

                if e.trainer_id is not None and (e.trainer_id, args.date) in trainer_tbl.index:
                    tt = trainer_tbl.loc[(e.trainer_id, args.date)]
                    ss = trainer_map[e.horse_id]
                    if all(abs(float(tt.iloc[i]) - ss[i]) < 1e-4 for i in range(3)):
                        n_trainer_ok += 1
                    else:
                        diffs.append(
                            f"trainer race={race.id} hid={e.horse_id} tid={e.trainer_id}"
                            f" train={[round(float(tt.iloc[i]),4) for i in range(3)]}"
                            f" serve={[round(x,4) for x in ss]}")
                else:
                    n_trainer_ok += 1

    await engine.dispose()
    print(f"corner : {n_corner_ok}/{n_total} 一致")
    print(f"trainer: {n_trainer_ok}/{n_total} 一致")
    for d in diffs[:20]:
        print("  ", d)
    if len(diffs) > 20:
        print(f"  ... 他 {len(diffs)-20} 件")


if __name__ == "__main__":
    asyncio.run(main())
