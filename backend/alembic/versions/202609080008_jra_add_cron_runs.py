"""cron 実行記録テーブルを追加する

Revision ID: 202609080008_jra
Revises: 202609052115_shared
Create Date: 2026-09-07T15:08:56

## なぜ要るか（2026-09-08）

統合 Phase 2 で sekito のスクレイプジョブをすべて kiseki の host cron へ移した結果、
`check_scrape_supply.py` の②「当日のスクレイプジョブに failed なし」が
**完全に空になった**。あのチェックは `sekito.script_requests` を見ており、
そこに記録するのは sekito のスケジューラだけだから。

    2026-09-07 の切替直後に実測: 有効なスクレイプ系 sekito ジョブ 0 件
    → 以後、何が失敗しても②は必ず OK を返す

②は元々「netkeiba-index が毎日 10 分でタイムアウト kill されている」ことを
唯一検知していたチェックで、失うと痛い。kiseki 側で同じ事実
（**いつ・何が・どう終わったか**）を持つための表。

## 設計

  - **成否だけでなく「そもそも走ったか」を見たい**ので、開始時に 1 行入れて
    終了時に更新する形にする。開始行が残ったまま終了が無ければ「途中で死んだ」。
  - `summary` は自由記述。件数など、ジョブが自分で意味づけた要約を入れる
    （「success を返しながら 0 件」を人が読んで気づけるようにする）。
  - 保持は当面無制限。1 日あたり数百行（paddock が */3 で 30 回など）なので、
    増えすぎたら `started_at` で刈る。

🔴 このマイグレーションは**テーブルを作るだけ**で、参照するコードは入れない。
   kiseki のデプロイは「本番へ切替 → その後にマイグレーション」の順なので、
   同じ PR に参照を入れると切替窓（最大2分）で該当コードが落ちる。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202609080008_jra"
down_revision: str | None = "202609052115_shared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cron_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False,
                  comment="ラッパスクリプト名（例: scrape_netkeiba_index）"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()"), comment="開始時刻"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True,
                  comment="終了時刻。NULL のまま古い行は『途中で死んだ』"),
        sa.Column("exit_code", sa.Integer(), nullable=True,
                  comment="終了コード。NULL は実行中または異常終了"),
        sa.Column("summary", sa.Text(), nullable=True,
                  comment="ジョブ自身が書く要約（件数など）。0 件success を人が読んで気づくため"),
        sa.PrimaryKeyConstraint("id"),
        schema="keiba",
        comment="kiseki の host cron ジョブの実行記録。sekito.script_requests の置き換え",
    )
    op.create_index(
        "ix_cron_runs_job_started", "cron_runs", ["job_name", "started_at"],
        unique=False, schema="keiba",
    )


def downgrade() -> None:
    op.drop_index("ix_cron_runs_job_started", table_name="cron_runs", schema="keiba")
    op.drop_table("cron_runs", schema="keiba")
