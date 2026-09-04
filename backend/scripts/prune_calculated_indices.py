"""`keiba.calculated_indices` の旧世代を削除する。

台帳 `docs/jra_rebuild_2026_08.md` 課題#10。23 世代 / 2,689,460 行 / 817MB が滞留している。

## 何を残すか

`SUBINDEX_MIN_VERSION = 26` にしたので**学習は v26/v27 しか読まない**が、
**古い版を明示的に指定しているスクリプトが残っている**ため機械的に消してはいけない:

| version | 参照しているもの |
|---|---|
| 22 | `backtest_dm{,_signal,_signal_segments}.py` / `backtest_combined_signals.py` |
| 24 | `inference_v26.py` / `train_v26_lightgbm.py` / `jra_ensemble_weight_sweep.py` 他 |
| 26 | 学習の入力（`SUBINDEX_SOURCE_SQL` の下限） |
| 27 | 現本番。API が読む |

上記 4 つを残すと、削除できるのは約 209 万行（テーブルの約 78%）。
**全体の 94% の削減効果を、既存スクリプトを 1 本も壊さずに得られる**ので既定はこれ。

⚠️ **削除は元に戻せない。** 当時の指数を再現するには各版のモデルが要る（多くは残っていない）。
日次バックアップ（03:30 JST・`~/kiseki-backups/daily/`）が唯一の退避先になるので、
実行前に当日ぶんが取れていることを確認すること。

使い方:
    cd backend
    .venv/bin/python scripts/prune_calculated_indices.py            # dry-run（既定）
    .venv/bin/python scripts/prune_calculated_indices.py --execute
    .venv/bin/python scripts/prune_calculated_indices.py --keep 22,24,26,27 --execute
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402

from src.indices.composite import COMPOSITE_VERSION, SUBINDEX_MIN_VERSION  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prune_ci")

# 明示的に版を指定しているスクリプトがあるため残す（モジュール docstring の表を参照）
#
# 🔴 27 は 2026-09-04 に追加した。DEFAULT_KEEP は COMPOSITE_VERSION から導出するので、
#   版を 28 へ上げた瞬間に **直前の本番版 v27 が既定の保持リストから静かに外れる**。
#   デプロイ後・v28 のバックフィル完了前にこれを --execute で回すと、
#   「現行データ(v27)を消したうえで v28 もまだ無い」状態になる。
#   `anagusa_top3_walkforward.py:101` が v27 を直書きで参照してもいる。
PINNED_BY_SCRIPTS = [22, 24, 27]
DEFAULT_KEEP = sorted({*PINNED_BY_SCRIPTS, SUBINDEX_MIN_VERSION, COMPOSITE_VERSION})


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--keep", default=",".join(map(str, DEFAULT_KEEP)),
                   help=f"残す version（カンマ区切り）。既定 {DEFAULT_KEEP}")
    p.add_argument("--execute", action="store_true", help="付けないと dry-run")
    p.add_argument("--batch-size", type=int, default=50000,
                   help="1トランザクションで消す行数（VPS 負荷対策）")
    p.add_argument("--sleep", type=float, default=0.5)
    args = p.parse_args()
    keep = sorted({int(v) for v in args.keep.split(",") if v.strip()})

    if COMPOSITE_VERSION not in keep:
        raise SystemExit(f"現本番 version={COMPOSITE_VERSION} を残さない指定は危険。中止")
    if SUBINDEX_MIN_VERSION not in keep:
        raise SystemExit(
            f"学習の入力 version={SUBINDEX_MIN_VERSION} を残さない指定は危険。中止"
            "（SUBINDEX_SOURCE_SQL がこの版以上を読む）"
        )

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT version, count(*) FROM keiba.calculated_indices GROUP BY 1 ORDER BY 1"
    )
    counts = dict(cur.fetchall())
    total = sum(counts.values())
    doomed = {v: n for v, n in counts.items() if v not in keep}

    logger.info(f"残す version: {keep}")
    logger.info(f"総行数 {total:,} / 削除対象 {sum(doomed.values()):,} 行 "
                f"({100.0 * sum(doomed.values()) / total:.1f}%) / "
                f"{len(doomed)} 世代")
    for v, n in sorted(doomed.items()):
        logger.info(f"  version={v:>3}  {n:>9,} 行")

    if not args.execute:
        logger.info("dry-run（--execute で実行）。**実行前に当日の DB バックアップを確認すること**")
        return
    if not doomed:
        logger.info("削除対象なし")
        return

    deleted = 0
    for v in sorted(doomed):
        while True:
            cur.execute(
                "DELETE FROM keiba.calculated_indices WHERE ctid IN ("
                "  SELECT ctid FROM keiba.calculated_indices WHERE version = %s LIMIT %s)",
                (v, args.batch_size),
            )
            n = cur.rowcount
            conn.commit()
            deleted += n
            if n == 0:
                break
            logger.info(f"  version={v} 削除 {deleted:,}/{sum(doomed.values()):,}")
            if args.sleep:
                time.sleep(args.sleep)
    logger.info(f"削除完了 {deleted:,} 行。VACUUM は autovacuum に任せる"
                "（VACUUM FULL は排他ロックを取るので本番中は打たないこと）")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
