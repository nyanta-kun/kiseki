"""odds_history / latest_odds の bet_type='quinella_place' を 'wide' へ改名する（既定 dry-run）。

## 背景

同じワイド（O3 / HR ワイド払戻）に 2 つの名前が付いていた。

    keiba.race_payouts.bet_type   = 'wide'            ← HR レコード（払戻・確定値）
    keiba.odds_history.bet_type   = 'quinella_place'  ← O3 レコード（オッズ）
    keiba.latest_odds.bet_type    = 'quinella_place'

どちらも `jvlink_parser.py` が書いているのに語彙が違うため、
`odds_history` と `race_payouts` を `bet_type` で join すると
**エラーにならず 0 件**になる。確定オッズ検証で trio / trifecta /
quinella / exacta は突き合わせできたのにワイドだけ結果が出なかったのはこれ。

`wide` に寄せる理由は docs/jvdata-spec.md「券種名（bet_type）の正準表記」を参照。
要点は、payouts 側が精算の正本で全 betting モジュール（backtest / allocation /
odds_model）が `wide` を使っており、`quinella_place` はソース 2 ファイルにしか
存在しないこと。

## 実行順序の注意

`purge_corrupt_exotic_odds.py`（11バイトずれ行の削除）を先に流す場合、
ワイド行はそこで消えるため本スクリプトの対象は 0 件になる。逆順でも
purge 側は `quinella_place` / `wide` の両方を対象にしているので取りこぼさない。

## 使い方

    cd backend
    .venv/bin/python scripts/rename_quinella_place_to_wide.py            # dry-run
    .venv/bin/python scripts/rename_quinella_place_to_wide.py --execute
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
logger = logging.getLogger("rename_wide")

OLD_NAME = "quinella_place"
NEW_NAME = "wide"

# latest_odds は PRIMARY KEY (race_id, bet_type, combination) を持つため、
# 改名先が既に存在すると衝突する。衝突があるかを先に必ず確認する。
TARGET_TABLES = ("keiba.odds_history", "keiba.latest_odds")
PK_TABLES = {"keiba.latest_odds"}  # (race_id, bet_type, combination) が一意
BATCH = 100_000


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def count_rows(cur, table: str, bet_type: str) -> int:
    cur.execute(
        f"SELECT count(*) FROM {table} WHERE bet_type = %(bt)s",  # noqa: S608
        {"bt": bet_type},
    )
    return int(cur.fetchone()[0])


def count_collisions(cur, table: str) -> int:
    """改名すると一意制約に衝突する行数を返す（PK を持つテーブルのみ意味を持つ）。"""
    cur.execute(
        f"""
        SELECT count(*) FROM {table} o
        WHERE o.bet_type = %(old)s
          AND EXISTS (
              SELECT 1 FROM {table} w
              WHERE w.race_id = o.race_id
                AND w.combination = o.combination
                AND w.bet_type = %(new)s
          )
        """,  # noqa: S608
        {"old": OLD_NAME, "new": NEW_NAME},
    )
    return int(cur.fetchone()[0])


def rename(cur, conn, table: str) -> int:
    """ctid でバッチ更新する。ロックを長く握らないよう小分けにコミットする。"""
    total = 0
    while True:
        t0 = time.time()
        cur.execute(
            f"UPDATE {table} SET bet_type = %(new)s WHERE ctid IN "  # noqa: S608
            f"(SELECT ctid FROM {table} WHERE bet_type = %(old)s LIMIT %(n)s)",
            {"old": OLD_NAME, "new": NEW_NAME, "n": BATCH},
        )
        n = cur.rowcount
        conn.commit()
        total += n
        if n == 0:
            break
        logger.info(f"  {table}: {n:,} 行改名 ({time.time() - t0:.1f}s) 累計 {total:,}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(
        description=f"odds_history / latest_odds の bet_type {OLD_NAME!r} → {NEW_NAME!r}"
    )
    ap.add_argument("--execute", action="store_true", help="実際に更新する（既定は dry-run）")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    grand = 0
    blocked = False
    for table in TARGET_TABLES:
        n_old = count_rows(cur, table, OLD_NAME)
        n_new = count_rows(cur, table, NEW_NAME)
        grand += n_old
        logger.info(f"{table}: {OLD_NAME}={n_old:,} → {NEW_NAME} (既存 {NEW_NAME}={n_new:,})")

        if table in PK_TABLES and n_new:
            n_col = count_collisions(cur, table)
            if n_col:
                logger.error(
                    f"  [!] {table}: 一意制約に衝突する行が {n_col:,} 件あります。"
                    " 重複の扱いを決めるまで改名できません。"
                )
                blocked = True
            else:
                logger.info(f"  {table}: 衝突なし（既存 {NEW_NAME} 行と重ならない）")

    if grand == 0:
        logger.info("対象行がありません。何もしません。")
        return

    if blocked:
        logger.error("衝突があるため中止しました。")
        sys.exit(1)

    if not args.execute:
        logger.info(f"dry-run: 合計 {grand:,} 行が対象。実行するには --execute を付けてください。")
        return

    logger.info(f"=== 改名を実行します（合計 {grand:,} 行） ===")
    done = 0
    for table in TARGET_TABLES:
        done += rename(cur, conn, table)
    logger.info(f"完了: {done:,} 行を {OLD_NAME} → {NEW_NAME} に改名しました。")

    for table in TARGET_TABLES:
        left = count_rows(cur, table, OLD_NAME)
        now = count_rows(cur, table, NEW_NAME)
        logger.info(f"{table}: 残 {OLD_NAME}={left:,} / {NEW_NAME}={now:,}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
