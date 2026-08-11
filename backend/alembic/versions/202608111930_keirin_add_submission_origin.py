"""add keirin.netkeirin_submissions.origin（入稿の出自）

入稿には**ゲートを通ったもの**と**看板レースの取りこぼしを埋めたもの**があるが、
`submit_marquee_wt.py` の `RANK_BY_CARS = {7: "7A", 9: "9A"}` により穴埋めも
`rank_key='7A'/'9A'` を名乗るため、`rank_key` だけでは経路を判別できない。
実測（2026-08-01〜08-10）では **7A 入稿52件中49件（94%）が穴埋め**で、
「7Aは売れるのに当たらない」という観測はランクの性質ではなくこの混在が原因だった。

| 経路 | R数 | 売上(有償pt) | 表示的中率 | 回収率 |
|---|---|---|---|---|
| ゲート通過 | 107 | 29,145 (25.4%) | 29.0% | 0.702 |
| 穴埋め | 87 | 81,800 (**71.2%**) | 14.9% | 0.333 |

⚠️ **`rank_key` は書き換えない。** `netkeirin_settings` が rank_key をキーに
   `enabled` / タイトル・コメントのテンプレートを持っており、しかも
   `_is_enabled()` は **fail-open**（行が無い rank_key は有効扱い）。
   新しいキーへ変えると顧客から見て別商品になるうえ、
   **テンプレート空のまま有効**で入稿されうる。測定だけを足す。

値は3種（呼び出し経路そのもの。推定ではない）:
  - `rank`         … ランクのゲートを通った自動入稿
  - `marquee_fill` … 看板レースの穴埋め（`netkeirin_submit_wt.py --marquee`）
  - `manual`       … 手動入稿（Web の `/submit-race` → `--manual-rank-key` のみ）

Revision ID: 202608111930_keirin
Revises: 202608111400_keirin
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608111930_keirin"
down_revision = "202608111400_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"

# 既存行の推定バックフィル。
# 🔴 **これは推定である。** 入稿時に出自を記録し始めるのはこの migration 以降で、
#    それ以前の行には手掛かりが無い。`picks_history` に
#    「同じレース × 同じランク」の行があるかどうかでゲート通過を判定する
#    （ゲートが立った推奨は必ず picks_history に残るため）。
#    以後は **入稿時に記録した値が正**で、この推定は上書きしない。
#
# ⚠️ 手動入稿（`manual`）は過去分では穴埋めと区別できないため、まとめて
#    `marquee_fill` になる。実運用上ほぼ全てが `submit_marquee_wt.py` 由来だが、
#    過去分の `marquee_fill` に少量の手動が混ざりうることは承知しておくこと。
#
# ⚠️ `submitted_at` の分（穴埋めは :20 起動）では分離できない。
#    実測で両経路とも :00 / :14 / :20 に散らばっている（2026-08-11 確認）。
_BACKFILL_SQL = f"""
UPDATE {SCHEMA}.netkeirin_submissions s
SET origin = 'marquee_fill'
WHERE NOT EXISTS (
    SELECT 1 FROM {SCHEMA}.picks_history p
    WHERE split_part(p.race_key, '#', 1) = s.race_key
      AND p.rank = 'RANK_' || s.rank_key
)
"""


def upgrade() -> None:
    # server_default='rank' は既存行を埋めるために必要。
    # ⚠️ default は**残す**。keirin 側の INSERT は列を明示するが、
    #    取りこぼしがあったときに NOT NULL 違反で入稿ごと落とすより、
    #    既定値の 'rank' で通して集計だけがずれる方が被害が小さい。
    op.add_column(
        "netkeirin_submissions",
        sa.Column("origin", sa.String(20), nullable=False, server_default="rank",
                  comment="入稿の出自（rank / marquee_fill / manual）"),
        schema=SCHEMA,
    )
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("netkeirin_submissions", "origin", schema=SCHEMA)
