"""POG のマテビューを sekito から keiba へ移す

Revision ID: 202609100730_shared
Revises: 202609092313_jra
Create Date: 2026-09-10

## なぜ要るか

🔴 `keiba.horse_runs` は**テーブルではなく `sekito.mv_horse_runs` のビュー**で、
その `REFRESH` を回しているのは **sekito のバックエンド**（`services/mv-refresh-listener.js`）
だった。実測（2026-09-10）:

    LISTEN mv_horse_runs_dirty   pid 3904073   ← sekito-backend-1
    sekito.mv_horse_runs    1,343,782 行
    sekito.mv_graded_wins       1,915 行

つまり **sekito のコンテナを止めた瞬間に kiseki の POG 順位表と今週の出走が
静かに凍結する**。マテビューは最後の内容を保持し続けるので、エラーも空表示も
出ない。統合 Phase 5 で sekito を廃止する前に、必ずこちらへ移す必要がある。

## mv_horse_runs は「置き場所」だけの問題

定義は `keiba.*` / `chihou.*` しか参照しておらず sekito のデータは使っていない。
同じ定義で `keiba.mv_horse_runs` を作り、`keiba.horse_runs` を張り替えるだけ。

## 🔴 mv_graded_wins は作り直す（コピーではない）

移設元の定義は **`sekito.entries` に強く依存**しており、そのテーブルは
2026-05-03 で凍結している。実測の壊れ方:

    地方の重賞勝ち  mv は 2026-04-15 で停止。2026年は 11 件しか入っていない
                    （keiba/chihou から直接数えると 27 件）
    中央の重賞勝ち  2026年 89 件のうち **netkeiba_horse_id が付くのは 51 件だけ**
                    （ID は sekito.entries 由来。付かないと POG 指名馬に紐付かない）

ここでは `mv_horse_runs` と同じ対応付け（`keiba.horses.jravan_code` /
`chihou.horses.umaconn_code`）で中央・地方から直接作る。列は移設元と互換に保つが、
`course_code` は sekito 独自コードではなく **`races.course` をそのまま**入れ、
レース詳細へ飛べるよう `race_id` を足した。

## ⚠️ sekito 側は消さない

sekito の 2 つのマテビューと listener はそのまま残す（sekito の画面がまだ
使っている）。しばらく両方が REFRESH されるが、VPS の CPU は idle 91〜95% で
余裕がある。sekito 廃止時にあちらを落とす。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202609100730_shared"
down_revision: str | Sequence[str] | None = "202609092313_jra"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 重賞とみなす格。移設元 `sekito.mv_graded_wins` と同一。
#: ⚠️ 中央の `J.G1` 等（障害）は移設元も入れていないので入れない。
_GRADES = "'G1','G2','G3','JpnI','JpnII','JpnIII','Jpn1','Jpn2','Jpn3'"


def upgrade() -> None:
    # --- 1. mv_horse_runs（定義は sekito のものと同一） ---
    op.execute(
        """
        CREATE MATERIALIZED VIEW keiba.mv_horse_runs AS
        SELECT 'jra'::text AS source,
               rr.id AS rr_id,
               kh.jravan_code AS netkeiba_horse_id,
               to_date(kr.date::text, 'YYYYMMDD') AS run_date,
               rr.finish_position,
               rr.prize_money
          FROM keiba.horses kh
          JOIN keiba.race_results rr ON rr.horse_id = kh.id
          JOIN keiba.races kr ON kr.id = rr.race_id
         WHERE kh.jravan_code IS NOT NULL
        UNION ALL
        SELECT 'chihou'::text AS source,
               rr.id AS rr_id,
               ch.umaconn_code AS netkeiba_horse_id,
               to_date(r.date::text, 'YYYYMMDD') AS run_date,
               rr.finish_position,
               rr.prize_money
          FROM chihou.horses ch
          JOIN chihou.race_results rr ON rr.horse_id = ch.id
          JOIN chihou.races r ON r.id = rr.race_id
         WHERE ch.umaconn_code IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM keiba.races kr
                WHERE kr.date = r.date AND kr.course = r.course
                  AND kr.race_number = r.race_number
           )
        """
    )
    # ⚠️ UNIQUE 索引が無いと `REFRESH ... CONCURRENTLY` が使えず、
    #    REFRESH 中そのマテビューが排他ロックされる（POG の画面が固まる）。
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_horse_runs_unique "
        "ON keiba.mv_horse_runs (source, rr_id)"
    )
    op.execute(
        "CREATE INDEX idx_mv_horse_runs_code ON keiba.mv_horse_runs (netkeiba_horse_id)"
    )
    op.execute("CREATE INDEX idx_mv_horse_runs_date ON keiba.mv_horse_runs (run_date)")

    # --- 2. mv_graded_wins（sekito.entries を使わずに作り直す） ---
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW keiba.mv_graded_wins AS
        SELECT 'jra'::text AS source,
               rr.id AS rr_id,
               kr.id AS race_id,
               to_date(kr.date::text, 'YYYYMMDD') AS date,
               kr.course AS course_code,
               kr.course_name,
               kr.race_number AS race_no,
               kr.race_name,
               kr.grade,
               kh.jravan_code AS netkeiba_horse_id,
               kh.name AS entry_name
          FROM keiba.races kr
          JOIN keiba.race_results rr ON rr.race_id = kr.id AND rr.finish_position = 1
          JOIN keiba.horses kh ON kh.id = rr.horse_id
         WHERE kr.grade IN ({_GRADES})
        UNION ALL
        SELECT 'chihou'::text AS source,
               rr.id AS rr_id,
               r.id AS race_id,
               to_date(r.date::text, 'YYYYMMDD') AS date,
               r.course AS course_code,
               r.course_name,
               r.race_number AS race_no,
               r.race_name,
               r.grade,
               ch.umaconn_code AS netkeiba_horse_id,
               ch.name AS entry_name
          FROM chihou.races r
          JOIN chihou.race_results rr ON rr.race_id = r.id AND rr.finish_position = 1
          JOIN chihou.horses ch ON ch.id = rr.horse_id
         WHERE r.grade IN ({_GRADES})
           AND NOT EXISTS (
               SELECT 1 FROM keiba.races kr
                WHERE kr.date = r.date AND kr.course = r.course
                  AND kr.race_number = r.race_number
           )
        """
    )
    # 🔴 一意キーは `(source, rr_id)`。`race_id` では**同着の 1 着**で重複する
    #    （`race_results` は同着を別行で持つ）。`entry_name` や
    #    `netkeiba_horse_id` は NULL になりうるので一意キーに使わない。
    #    `REFRESH ... CONCURRENTLY` は全行を覆う UNIQUE 索引を要求する。
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_graded_wins_unique "
        "ON keiba.mv_graded_wins (source, rr_id)"
    )
    op.execute(
        "CREATE INDEX idx_mv_graded_wins_code ON keiba.mv_graded_wins (netkeiba_horse_id)"
    )
    op.execute("CREATE INDEX idx_mv_graded_wins_grade ON keiba.mv_graded_wins (grade)")

    # --- 3. keiba.horse_runs を張り替える ---
    # 列も順序も同じなので、参照側（pog_standings 等）は変更不要。
    op.execute("DROP VIEW IF EXISTS keiba.horse_runs")
    op.execute(
        """
        CREATE VIEW keiba.horse_runs AS
        SELECT source, rr_id, netkeiba_horse_id, run_date, finish_position, prize_money
          FROM keiba.mv_horse_runs
        """
    )


def downgrade() -> None:
    # 元どおり sekito のマテビューを指す。
    op.execute("DROP VIEW IF EXISTS keiba.horse_runs")
    op.execute(
        """
        CREATE VIEW keiba.horse_runs AS
        SELECT source, rr_id, netkeiba_horse_id, run_date, finish_position, prize_money
          FROM sekito.mv_horse_runs
        """
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS keiba.mv_graded_wins")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS keiba.mv_horse_runs")
