"""mv_horse_runs 更新通知トリガを sekito スキーマ依存から外す

keiba.race_results / chihou.race_results の AFTER 文トリガが
**sekito スキーマの関数** sekito.notify_mv_horse_runs_dirty() を呼んでいた。
sekito スキーマを先に DROP すると**本番のレース結果取り込みが関数欠損で失敗する**
（統合の順序の罠・docs/pog_migration_plan_2026_09_08.md §3）。

同じ本体の関数を keiba スキーマへ作り、両トリガの参照先を差し替える。
**通知チャンネル名 'mv_horse_runs_dirty' は変えない**ので、現行の
sekito-backend の LISTEN リスナー（services/mv-refresh-listener.js）は
そのまま両マテビューを REFRESH し続ける。挙動の変化はゼロ。

2026-09-08 の実測では、keiba/chihou 上で sekito を参照しているオブジェクトは
この2トリガのみ（関数・ビュー・外部キーはいずれも0件）。したがってこの差し替えで
**書き込み経路の sekito 依存は消える**。

⚠️ sekito 側の関数は残す。sekito.mv_graded_wins は sekito.entries / sekito.races /
sekito.jvlink_to_sekito_course() に依存しており、まだ keiba へ移せないため。

Revision ID: 202609081613_shared
Revises: 202609081552_jra
Create Date: 2026-09-08
"""

from alembic import op

revision = "202609081613_shared"
down_revision = "202609081552_jra"
branch_labels = None
depends_on = None

# トリガ再作成は race_results に ACCESS EXCLUSIVE を取る。取り込み中の長い
# トランザクションの後ろに並んで**取り込み全体を止めない**よう、待たずに諦める。
# 失敗したら再実行すればよい（このマイグレーションは冪等）。
LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION keiba.notify_mv_horse_runs_dirty()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          PERFORM pg_notify('mv_horse_runs_dirty', '');
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for schema, suffix in (("keiba", "keiba"), ("chihou", "chihou")):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_notify_mv_horse_runs_dirty_{suffix} "
            f"ON {schema}.race_results"
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_notify_mv_horse_runs_dirty_{suffix}
            AFTER INSERT OR UPDATE OR DELETE ON {schema}.race_results
            FOR EACH STATEMENT
            EXECUTE FUNCTION keiba.notify_mv_horse_runs_dirty()
            """
        )


def downgrade() -> None:
    """sekito 側の関数へ戻す（関数は残してあるので参照先を戻すだけ）。"""
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for schema, suffix in (("keiba", "keiba"), ("chihou", "chihou")):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_notify_mv_horse_runs_dirty_{suffix} "
            f"ON {schema}.race_results"
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_notify_mv_horse_runs_dirty_{suffix}
            AFTER INSERT OR UPDATE OR DELETE ON {schema}.race_results
            FOR EACH STATEMENT
            EXECUTE FUNCTION sekito.notify_mv_horse_runs_dirty()
            """
        )
    op.execute("DROP FUNCTION IF EXISTS keiba.notify_mv_horse_runs_dirty()")
