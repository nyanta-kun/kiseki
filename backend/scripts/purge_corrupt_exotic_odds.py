"""11バイトずれで壊れたエキゾチックオッズ行を削除する（既定 dry-run）。

2026-08-23 まで `odds_importer.EXOTIC_HEADER_SIZE` が 51（正しくは 40）だったため、
O2〜O6（馬連・ワイド・馬単・三連複・三連単）は組番欄に隣接エントリのゴミが、
オッズ欄に次エントリの組番が入っていた。実測（2026-08-15・中央36R）:

    券種             馬番が出走頭数以内   オッズ最小
    win                    99.8%            1.1     ← 無傷（O1 は別経路）
    place                  99.8%            1.0     ← 無傷
    trifecta                1.4%        10204.0
    trio                    3.0%         2040.0
    quinella_place          2.0%            0.1  ← 現在は `wide` に改名済み
    exacta                 15.0%         1000.3
    quinella               15.6%         3000.0

生レコードを保存していないためオッズ値は復元できない（6桁中3桁しか残らない）。
正しい値は `windows-agent/odds_backfill.py` で蓄積系から取り直す。

🔴 **`win` / `place` は無傷なので絶対に消さないこと。**
   複勝オッズの補完・前向き記録・オッズ時系列分析がこの2つに依存している。

⚠️ 削除は元に戻せない。実行前に当日の DB バックアップを確認すること。

使い方:
    cd backend
    .venv/bin/python scripts/purge_corrupt_exotic_odds.py            # dry-run
    .venv/bin/python scripts/purge_corrupt_exotic_odds.py --execute
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import psycopg2
from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("purge_exotic")

# 壊れている券種。win / place は含めない（無傷）。
# ワイドは 2026-08-23 の改名前が `quinella_place`、改名後が `wide`。
# rename_quinella_place_to_wide.py の実行前後どちらでも取りこぼさないよう両方入れる。
CORRUPT_BET_TYPES = ("trio", "trifecta", "quinella", "quinella_place", "wide", "exacta")
TARGET_TABLES = ("keiba.odds_history", "keiba.latest_odds")
BATCH = 200_000


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def count_rows(cur, table: str) -> dict[str, int]:
    cur.execute(
        f"SELECT bet_type, count(*) FROM {table} WHERE bet_type IN %(bt)s GROUP BY 1 ORDER BY 2 DESC",  # noqa: S608
        {"bt": CORRUPT_BET_TYPES},
    )
    return dict(cur.fetchall())


def purge(cur, conn, table: str) -> int:
    """ctid でバッチ削除する。ロックを長く握らないよう小分けにコミットする。"""
    total = 0
    while True:
        t0 = time.time()
        cur.execute(
            f"DELETE FROM {table} WHERE ctid IN "  # noqa: S608
            f"(SELECT ctid FROM {table} WHERE bet_type IN %(bt)s LIMIT %(n)s)",
            {"bt": CORRUPT_BET_TYPES, "n": BATCH},
        )
        n = cur.rowcount
        conn.commit()
        total += n
        if n == 0:
            break
        logger.info(f"  {table}: {n:,} 行削除 ({time.time() - t0:.1f}s) 累計 {total:,}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="壊れたエキゾチックオッズ行の削除")
    ap.add_argument("--execute", action="store_true", help="実際に削除する（既定は dry-run）")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    grand = 0
    for table in TARGET_TABLES:
        counts = count_rows(cur, table)
        sub = sum(counts.values())
        grand += sub
        logger.info(f"{table}: 削除対象 {sub:,} 行")
        for bt, n in counts.items():
            logger.info(f"    {bt:16s} {n:>10,}")

    # 無傷な券種の行数も出して、消さないことを目視できるようにする
    for table in TARGET_TABLES:
        cur.execute(
            f"SELECT bet_type, count(*) FROM {table} "  # noqa: S608
            "WHERE bet_type IN ('win','place') GROUP BY 1 ORDER BY 1"
        )
        keep = dict(cur.fetchall())
        logger.info(f"{table}: 温存 {sum(keep.values()):,} 行 {keep}")

    if not args.execute:
        logger.info(f"[dry-run] 合計 {grand:,} 行が削除対象。--execute で実行する。")
        return

    logger.info(f"=== 削除開始: 合計 {grand:,} 行 ===")
    for table in TARGET_TABLES:
        deleted = purge(cur, conn, table)
        logger.info(f"{table}: 削除完了 {deleted:,} 行")

    # 事後確認
    for table in TARGET_TABLES:
        left = sum(count_rows(cur, table).values())
        cur.execute(
            f"SELECT count(*) FROM {table} WHERE bet_type IN ('win','place')"  # noqa: S608
        )
        kept = cur.fetchone()[0]
        status = "OK" if left == 0 else f"残り {left:,} 行"
        logger.info(f"{table}: 壊れた行 {status} / win+place {kept:,} 行 温存")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
