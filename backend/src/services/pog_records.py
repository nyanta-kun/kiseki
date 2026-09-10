"""POG 記録室とスコア集計の SQL 層（sekito `/graded-wins` `/score-summary` の移設先）。

統合 Phase 5。点数計算そのものは `pog_score.py`（純関数）にあるので、ここは
**入力を集めるだけ**に徹する。

## 重賞勝ちの取得元

`keiba.mv_graded_wins`（2026-09-10 に新設）。移設元の `sekito.mv_graded_wins` は
`sekito.entries`（2026-05-03 で凍結）に依存しており、地方は 2026-04-15 で止まり、
中央も馬 ID が 2026年 89件中 51件しか付いていなかった。

## 🔴 スコアの対象期間

    開始  {year}-06-01        POG 年度の 2 歳戦が始まるころ
    終了  {year + 1}-06-01    未満（日本ダービーは 5 月最終週）

移設元と同じ。**重賞勝ちだけがこの窓で絞られる**。賞金・順位は窓なしの通算で、
移設元もそうなっている（変えると順位賞が変わる）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .pog_score import OwnerInput, OwnerScore, Wins, build_scores, classify

#: 記録室の一覧。年度・馬主で絞れる。
_GRADED_WINS_SQL = """
    SELECT
        g.date::text        AS date,
        g.source,
        g.race_id,
        g.course_code,
        g.course_name,
        g.race_no,
        g.race_name,
        g.grade,
        g.netkeiba_horse_id,
        COALESCE(h.name, g.entry_name) AS horse_name,
        COALESCE(m.nickname, u.name)   AS owner_name,
        p.user_id,
        pg.year                        AS pog_year
    FROM keiba.mv_graded_wins g
    JOIN keiba.pog_picks p
      ON p.netkeiba_horse_id = g.netkeiba_horse_id AND p.pick_order <> 0
    JOIN keiba.pog_groups pg ON pg.id = p.group_id
    LEFT JOIN keiba.pog_group_members m
           ON m.group_id = p.group_id AND m.user_id = p.user_id
    LEFT JOIN keiba.users u ON u.id = p.user_id
    LEFT JOIN keiba.pog_horses h ON h.netkeiba_horse_id = g.netkeiba_horse_id
    -- ⚠️ `:year::int` と書いてはいけない。SQLAlchemy が `::` を型キャストと
    --    解釈してプレースホルダを展開せず、Postgres が
    --    `syntax error at or near ":"` を返す。`CAST(... AS int)` を使う。
    WHERE (CAST(:year AS int) IS NULL OR pg.year = CAST(:year AS int))
      AND (CAST(:user_id AS int) IS NULL OR p.user_id = CAST(:user_id AS int))
    ORDER BY g.date DESC, g.race_name
"""


async def fetch_graded_wins(
    db: AsyncSession, *, year: int | None = None, user_id: int | None = None
) -> list[dict]:
    """POG 指名馬の重賞勝ちを新しい順に返す。

    Args:
        db: DB セッション。
        year: POG の年度。None なら全年度（記録室の「全グループ」）。
        user_id: 馬主。None なら全員。
    """
    rows = await db.execute(
        text(_GRADED_WINS_SQL), {"year": year, "user_id": user_id}
    )
    return [dict(r) for r in rows.mappings()]


#: スコアの入力。馬主ごとの賞金・戦績・頭数をまとめる。
#: 🔴 `pog_standings._HORSE_AGG` と同じ数え方にすること（順位表と食い違うと
#:    「順位表では 1 位なのにスコアでは 2 位」が起きる）。
_OWNER_INPUT_SQL = """
    WITH per_horse AS (
        SELECT
            p.user_id,
            p.netkeiba_horse_id,
            COALESCE(SUM(r.prize_money), 0) / 100           AS prize,
            count(*) FILTER (WHERE r.finish_position = 1)   AS win,
            count(*) FILTER (WHERE r.finish_position = 2)   AS place,
            count(*) FILTER (WHERE r.finish_position = 3)   AS show,
            count(*) FILTER (WHERE r.finish_position >= 4)  AS "out",
            count(r.netkeiba_horse_id) > 0                  AS has_raced,
            count(*) FILTER (WHERE r.finish_position = 1) > 0 AS has_won
        FROM keiba.pog_picks p
        LEFT JOIN keiba.horse_runs r
               ON r.netkeiba_horse_id = p.netkeiba_horse_id
        WHERE p.group_id = :group_id AND p.pick_order <> 0
        GROUP BY p.user_id, p.netkeiba_horse_id
    ),
    per_owner AS (
        SELECT
            ph.user_id,
            COALESCE(m.nickname, u.name)                    AS name,
            SUM(ph.prize)::bigint                           AS total_prize,
            SUM(ph.win)::int                                AS win,
            SUM(ph.place)::int                              AS place,
            SUM(ph.show)::int                               AS show,
            SUM(ph."out")::int                              AS "out",
            count(*)::int                                   AS horse_count,
            count(*) FILTER (WHERE ph.has_raced)::int       AS horses_raced,
            count(*) FILTER (WHERE ph.has_won)::int         AS horses_won
        FROM per_horse ph
        LEFT JOIN keiba.pog_group_members m
               ON m.group_id = :group_id AND m.user_id = ph.user_id
        LEFT JOIN keiba.users u ON u.id = ph.user_id
        GROUP BY ph.user_id, COALESCE(m.nickname, u.name)
    )
    SELECT *, RANK() OVER (ORDER BY total_prize DESC)::int AS prize_rank
    FROM per_owner
    ORDER BY total_prize DESC
"""

#: スコア対象期間の重賞勝ち。区分の振り分けは Python 側（`classify`）で行う。
#: ⚠️ SQL で振り分けないのは、判定規則を `pog_score.py` に一本化するため
#:    （記録室の表示と精算の数え方が食い違うと気づけない）。
_WINDOW_WINS_SQL = """
    SELECT p.user_id, g.grade, g.course_code, g.race_name
    FROM keiba.mv_graded_wins g
    JOIN keiba.pog_picks p
      ON p.netkeiba_horse_id = g.netkeiba_horse_id AND p.pick_order <> 0
    WHERE p.group_id = :group_id
      AND g.date >= :start AND g.date < :end
"""


async def fetch_score_summary(
    db: AsyncSession, *, group_id: int, year: int
) -> list[OwnerScore]:
    """グループのスコア集計を返す。

    Args:
        db: DB セッション。
        group_id: `keiba.pog_groups.id`。
        year: POG の年度。対象期間 `{year}-06-01` 〜 `{year+1}-06-01` の算出に使う。
    """
    owners_rows = (
        await db.execute(text(_OWNER_INPUT_SQL), {"group_id": group_id})
    ).mappings().all()

    win_rows = (
        await db.execute(
            text(_WINDOW_WINS_SQL),
            {
                "group_id": group_id,
                # ⚠️ `mv_graded_wins.date` は date 型。文字列を渡すと asyncpg が
                #    `'str' object has no attribute 'toordinal'` で落ちる。
                "start": date(year, 6, 1),
                "end": date(year + 1, 6, 1),
            },
        )
    ).mappings().all()

    counts: dict[int, dict[str, int]] = {}
    for w in win_rows:
        kind = classify(w["grade"], w["course_code"], w["race_name"])
        if kind is None:
            continue
        counts.setdefault(w["user_id"], {})
        counts[w["user_id"]][kind] = counts[w["user_id"]].get(kind, 0) + 1

    owners = [
        OwnerInput(
            user_id=r["user_id"],
            name=r["name"],
            total_prize=int(r["total_prize"] or 0),
            prize_rank=r["prize_rank"],
            win=r["win"],
            place=r["place"],
            show=r["show"],
            out=r["out"],
            horse_count=r["horse_count"],
            horses_raced=r["horses_raced"],
            horses_won=r["horses_won"],
            wins=Wins(**counts.get(r["user_id"], {})),
        )
        for r in owners_rows
    ]
    return build_scores(owners)
