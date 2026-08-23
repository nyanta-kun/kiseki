"""keirin.wt_entries(player_id) に索引を張る（落車リスク集計の全表走査を消す）

## なぜ

2026-08-21 の `a01234a`（レース信頼度指標＝落車リスク）で入った
`keirin_router.Q_SQL_CRASH` が `WHERE e.player_id IN (...)` を使うが、
`keirin.wt_entries` には **player_id の索引が無い**。結果として
**724,166行 / 448MB を毎リクエスト全表走査**していた。

実測（本番 EXPLAIN (ANALYZE, BUFFERS)）:

    Execution Time: 2575.879 ms
      -> Seq Scan on wt_entries e  (actual rows=724166)  Buffers: read=57223

⚠️ VPS の RAM は 1.9GiB しかなく、`keirin.wt_odds`（3,537万行 / 7.2GB）へ
   realtime が約30秒ごとに書き込むためページキャッシュが絶えず入れ替わる。
   そのため同じクエリが**ウォーム 43ms / コールド 2,576ms（60倍差）**に振れる。
   索引はこのコールド側を消すためのもの。

選手は 2,722人・行は 724,166 なので `INCLUDE` を付けて **index only scan** にする。
索引サイズは約 25MB の見込み。

## CONCURRENTLY を使う理由

`wt_entries` は realtime エージェントが書き込み続けるテーブルなので、
通常の `CREATE INDEX` が取る `SHARE` ロックで**取り込みが止まる**。
`CONCURRENTLY` はトランザクション内で実行できないため
`autocommit_block()` で囲む。

Revision ID: 202608230945_keirin
Revises: 202608181800_keirin
Create Date: 2026-08-23
"""

from __future__ import annotations

from alembic import op

revision = "202608230945_keirin"
down_revision = "202608181800_keirin"
branch_labels = None
depends_on = None

INDEX = "ix_wt_entries_player_id"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
            "ON keirin.wt_entries (player_id) INCLUDE (race_key, finish_order)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS keirin.{INDEX}")
