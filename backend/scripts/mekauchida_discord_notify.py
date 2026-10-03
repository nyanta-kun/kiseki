"""メカウチダの買い目 × 指数上位5位 / 穴ぐさ 一致の Discord 通知（10 分おき cron）。

判定は ``src/services/mekauchida_notify.py``、実行ループは ``src/services/mekauchida_runner.py``
（地方版 ``chihou_mekauchida_notify.py`` と共通）、パーサは ``src/scrapers/mekauchida.py``。

1. 今日の中央のレースのうち、発走 1 時間前〜発走前のものを DB から取る。
   無ければサイトを見ずに終わる（＝開催日でなければ何もしない）
2. サイトの今日ページを 1 回だけ取得し、監視中のレースの買い目を記録する
   （``logs/mekauchida_snapshots.jsonl``。見込み→判断の変化を後から測るため）
3. 発走まで 10 分以内（＝発走前の最後の監視）のレースで、買い目が
   指数 5 位以内 または 穴ぐさ と一致した馬を Discord に送る。
   送ったレースは ``logs/mekauchida_notified.json`` に残し二重送信しない

使い方:
    .venv/bin/python scripts/mekauchida_discord_notify.py
    .venv/bin/python scripts/mekauchida_discord_notify.py --dry-run
    .venv/bin/python scripts/mekauchida_discord_notify.py --now "2026-10-03 15:40" --dry-run
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
from src.indices.composite import COMPOSITE_VERSION  # noqa: E402
from src.scrapers.mekauchida import SITE_URL  # noqa: E402
from src.services.mekauchida_notify import JST  # noqa: E402
from src.services.mekauchida_runner import MonitorConfig, run_once  # noqa: E402
from src.utils.discord import send  # noqa: E402

# コンテナでは /app/logs（ホストの logs/ をマウント）。ローカルではリポジトリ直下の logs/
LOG_DIR = Path("/app/logs") if Path("/app/logs").is_dir() else _root.parent / "logs"

RACES_SQL = text("""
SELECT id, course_name, race_number, post_time
FROM keiba.races
WHERE date = :d AND post_time IS NOT NULL
""")

# 指数順位は API（races.py）と同じく「レースの最新 version を COMPOSITE_VERSION で上限」
# の行を使い、出走取消・発走除外（abnormality_code 1/2）を母集団から外す
HORSES_SQL = text("""
WITH rv AS (
  SELECT race_id, LEAST(MAX(version), :cv) AS v
  FROM keiba.calculated_indices
  WHERE race_id = ANY(:race_ids)
  GROUP BY race_id
),
ranked AS (
  SELECT ci.race_id, re.horse_number, ci.composite_index,
         RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC NULLS LAST) AS idx_rank
  FROM keiba.calculated_indices ci
  JOIN rv ON rv.race_id = ci.race_id AND rv.v = ci.version
  JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
  LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  WHERE COALESCE(rr.abnormality_code, 0) NOT IN (1, 2)
    AND ci.composite_index IS NOT NULL
)
SELECT r.id AS race_id, re.horse_number, k.idx_rank, k.composite_index, a.rank AS anagusa_rank
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id = r.id
LEFT JOIN ranked k ON k.race_id = r.id AND k.horse_number = re.horse_number
LEFT JOIN sekito.anagusa a
  ON a.date = to_date(r.date, 'YYYYMMDD')
 AND a.race_no = r.race_number
 AND a.horse_no = re.horse_number
 AND a.course_code = CASE r.course
       WHEN '01' THEN 'JSPK' WHEN '02' THEN 'JHKD' WHEN '03' THEN 'JFKS'
       WHEN '04' THEN 'JNGT' WHEN '05' THEN 'JTOK' WHEN '06' THEN 'JNKY'
       WHEN '07' THEN 'JCKO' WHEN '08' THEN 'JKYO' WHEN '09' THEN 'JHSN'
       WHEN '10' THEN 'JKKR' END
LEFT JOIN keiba.race_results rr2 ON rr2.race_id = r.id AND rr2.horse_id = re.horse_id
WHERE r.id = ANY(:race_ids)
  AND COALESCE(rr2.abnormality_code, 0) NOT IN (1, 2)  -- 取消・除外馬は穴ぐさ一致でも送らない
""")


CONFIG = MonitorConfig(
    name="mekauchida",
    site_url=SITE_URL,
    races_sql=RACES_SQL,
    horses_sql=HORSES_SQL,
    state_file=LOG_DIR / "mekauchida_notified.json",
    snapshot_file=LOG_DIR / "mekauchida_snapshots.jsonl",
    title="メカウチダ × 指数/穴ぐさ 一致",
    sql_params={"cv": COMPOSITE_VERSION},
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
