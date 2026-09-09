"""keiba.horse_runs ビューを追加する（POG 移植の入口・当面は sekito のマテビューを指す）

統合 Phase 5 の 5d-3 の前提。POG の第一陣 API 5 本はすべて
「馬ごとの出走・着順・賞金」を必要とし、いまは sekito.mv_horse_runs
（keiba + chihou の race_results を統合したマテビュー・1.34M 行）がそれを持つ。

## 🔴 なぜ実体を作らず、ビューにするか

同じ内容のマテビューを keiba にもう 1 本持つと **REFRESH が二重になる**。
2026-09-09 の実測: sekito 側の REFRESH は **96 回/日・中央値 30.9 秒 = 約50分/日**。
VPS は 3 コアなので、消費者が居ないうちからこれを倍にする理由が無い
（VPS の制約は docs/pog_migration_plan_2026_09_08.md と memory に記録）。

そこで **名前だけ先に keiba へ置く**。新しいコードは `keiba.horse_runs` しか
見ないので、sekito を落とすときにこのビューの定義を差し替えるだけで済む。

    いま      keiba.horse_runs → sekito.mv_horse_runs（マテビュー）
    廃止時    keiba.horse_runs → keiba 側に移したマテビュー

⚠️ **`mv_` を名前に付けない。** 実体はマテビューではないので、
   `REFRESH MATERIALIZED VIEW keiba.horse_runs` は通らない。
   名前で嘘をつくと、後から読む人がそれを試して失敗する。

⚠️ このビューは sekito スキーマに依存する。**sekito を落とす前に定義を
   差し替えること**（5b-1 でトリガの依存を外したのと同じ順序の罠）。

Revision ID: 202609092140_shared
Revises: 202609091824_jra
Create Date: 2026-09-09
"""

from alembic import op

revision = "202609092140_shared"
down_revision = "202609091824_jra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW keiba.horse_runs AS
        SELECT source, rr_id, netkeiba_horse_id, run_date,
               finish_position, prize_money
        FROM sekito.mv_horse_runs
        """
    )
    op.execute(
        "COMMENT ON VIEW keiba.horse_runs IS "
        "'POG 用の馬ごと出走記録。当面 sekito.mv_horse_runs を指す。"
        "sekito 廃止時に keiba 側のマテビューへ差し替えること'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS keiba.horse_runs")
