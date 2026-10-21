#!/usr/bin/env python3
"""JRA 一般競走の空のレース名を競走種別・条件コードから埋め戻す（1回限り）。

RA 取込が一般競走の race_name を NULL で上書きしていた問題（2026-10-22 修正。
race_importer.jra_general_race_name 参照）で、既に空になった行を直す。

  1) keiba.races: race_name が空で、競走条件コードから名前を作れる行
  2) sekito.races: JRA 分で race_name が空の行を keiba.races の名前で補う
     （sync_sekito_races.py は既存行の race_name を更新しないため）

条件コードの無い古い行（2026-05 中旬以前）は名前を作れないので触らない。
既に名前がある行は一切変更しない。

使い方:
    ... scripts/backfill_jra_general_race_name.py           # 件数だけ表示（書き込まない）
    ... scripts/backfill_jra_general_race_name.py --apply   # 書き込む
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import bindparam, text

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402
from src.importers.race_importer import jra_general_race_name  # noqa: E402

JRA_COURSES = "ARRAY['01','02','03','04','05','06','07','08','09','10']"

SELECT_KEIBA = text(
    f"""
    SELECT id, race_type_code, race_condition_code
    FROM keiba.races
    WHERE course = ANY({JRA_COURSES})
      AND COALESCE(race_name, '') = ''
      AND race_condition_code IS NOT NULL
    """
)

UPDATE_KEIBA = text(
    """
    UPDATE keiba.races SET race_name = :name
    WHERE id = :id AND COALESCE(race_name, '') = ''
    """
).bindparams(bindparam("name"), bindparam("id"))

SEKITO_TARGET = f"""
    FROM keiba.races r
    WHERE sr.course_code LIKE 'J%'
      AND COALESCE(sr.race_name, '') = ''
      AND r.course = ANY({JRA_COURSES})
      AND COALESCE(r.race_name, '') <> ''
      AND sr.date = keiba.to_date_imm(r.date::text, 'YYYYMMDD')
      AND sr.course_code = sekito.jvlink_to_sekito_course(r.course::text)
      AND sr.race_no = r.race_number
"""

COUNT_SEKITO = text(f"SELECT count(*) FROM sekito.races sr, LATERAL (SELECT 1 {SEKITO_TARGET}) x")
UPDATE_SEKITO = text(f"UPDATE sekito.races sr SET race_name = r.race_name {SEKITO_TARGET}")


def main() -> int:
    p = argparse.ArgumentParser(description="JRA 一般競走の空のレース名を埋め戻す")
    p.add_argument("--apply", action="store_true", help="実際に書き込む（既定は件数表示のみ）")
    args = p.parse_args()

    with SyncSessionLocal() as s:
        rows = s.execute(SELECT_KEIBA).fetchall()
        updates = [
            {"id": r.id, "name": name}
            for r in rows
            if (name := jra_general_race_name(r.race_type_code, r.race_condition_code))
        ]
        print(f"keiba.races: 空 {len(rows)} 件 / 名前を作れる {len(updates)} 件")

        if not args.apply:
            print(f"sekito.races: 補える {s.execute(COUNT_SEKITO).scalar()} 件（keiba 埋め戻し前の時点）")
            print("--apply を付けると書き込みます")
            return 0

        if updates:
            s.execute(UPDATE_KEIBA, updates)
        sekito_n = s.execute(UPDATE_SEKITO).rowcount
        s.commit()
        print(f"更新: keiba.races {len(updates)} 件 / sekito.races {sekito_n} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
