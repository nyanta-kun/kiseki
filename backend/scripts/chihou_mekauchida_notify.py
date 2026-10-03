"""メカウチダ地方の買い目 × 指数上位5位 一致の Discord 通知（10 分おき cron）。

中央版 ``mekauchida_discord_notify.py`` と同じ実行ループ（``src/services/mekauchida_runner.py``）を
地方の設定で回す。違いは次の 3 点だけ:

- サイトは ``nar/index.html``
- 指数は ``chihou.calculated_indices`` の **``version = CHIHOU_COMPOSITE_VERSION``**
  （地方の API と同じく現行版に完全一致。``LEAST`` で落とさない）
- 🔴 **穴ぐさは無い**（``sekito.anagusa`` は中央の場コードしか持たない）。一致は指数 5 位以内だけ

ばんえい（帯広）はサイトに無いので、runner が「サイトに無い場」として黙って飛ばす。

使い方:
    .venv/bin/python scripts/chihou_mekauchida_notify.py
    .venv/bin/python scripts/chihou_mekauchida_notify.py --dry-run
    .venv/bin/python scripts/chihou_mekauchida_notify.py --now "2026-10-03 18:12" --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root.parent / ".env")
except ImportError:
    pass  # Docker コンテナ内では環境変数が env_file で注入済み

from sqlalchemy import text  # noqa: E402

from src.db.session import sync_engine as engine  # noqa: E402
from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION  # noqa: E402
from src.scrapers.mekauchida import NAR_SITE_URL  # noqa: E402
from src.services.mekauchida_notify import JST  # noqa: E402
from src.services.mekauchida_runner import MonitorConfig, run_once  # noqa: E402
from src.utils.discord import send  # noqa: E402

# コンテナでは /app/logs（ホストの logs/ をマウント）。ローカルではリポジトリ直下の logs/
LOG_DIR = Path("/app/logs") if Path("/app/logs").is_dir() else _root.parent / "logs"

RACES_SQL = text("""
SELECT id, course_name, race_number, post_time
FROM chihou.races
WHERE date = :d AND post_time IS NOT NULL
""")

# 出走取消・発走除外（abnormality_code 1/2）は順位の母集団からも通知対象からも外す
HORSES_SQL = text("""
WITH live AS (
  SELECT re.race_id, re.horse_id, re.horse_number
  FROM chihou.race_entries re
  LEFT JOIN chihou.race_results rr ON rr.race_id = re.race_id AND rr.horse_id = re.horse_id
  WHERE re.race_id = ANY(:race_ids)
    AND COALESCE(rr.abnormality_code, 0) NOT IN (1, 2)
),
ranked AS (
  SELECT l.race_id, l.horse_number, ci.composite_index,
         RANK() OVER (PARTITION BY l.race_id ORDER BY ci.composite_index DESC) AS idx_rank
  FROM live l
  JOIN chihou.calculated_indices ci
    ON ci.race_id = l.race_id AND ci.horse_id = l.horse_id AND ci.version = :cv
  WHERE ci.composite_index IS NOT NULL
)
SELECT l.race_id, l.horse_number, k.idx_rank, k.composite_index, NULL::text AS anagusa_rank
FROM live l
LEFT JOIN ranked k ON k.race_id = l.race_id AND k.horse_number = l.horse_number
""")

CONFIG = MonitorConfig(
    name="mekauchida_nar",
    site_url=NAR_SITE_URL,
    races_sql=RACES_SQL,
    horses_sql=HORSES_SQL,
    state_file=LOG_DIR / "mekauchida_nar_notified.json",
    snapshot_file=LOG_DIR / "mekauchida_nar_snapshots.jsonl",
    title="メカウチダ地方 × 指数 一致",
    sql_params={"cv": CHIHOU_COMPOSITE_VERSION},
)


def main() -> int:
    """1 回分の監視。終了コード 0=正常 1=異常。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Discord に送らず標準出力のみ")
    parser.add_argument("--now", help="現在時刻を上書き（検証用・JST 'YYYY-MM-DD HH:MM'）")
    args = parser.parse_args()
    now = datetime.strptime(args.now, "%Y-%m-%d %H:%M").replace(tzinfo=JST) if args.now else datetime.now(JST)
    return run_once(CONFIG, engine, now, args.dry_run, send)


if __name__ == "__main__":
    sys.exit(main())
