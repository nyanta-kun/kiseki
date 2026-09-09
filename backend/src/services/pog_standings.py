"""POG の成績集計（純粋な SQL 層）。

sekito `backend/routes/pog.js` の `/owners` `/owners-history`
`/group/:id/user/:uid/horses` に相当する集計を、kiseki 側の
`keiba.pog_*` テーブルから作る。

## 🔴 成績は毎回 `keiba.horse_runs` から数える

移設元の `sekito.horse` は win / place / show / out / prize を**列として持って
いた**が、それを更新する処理が存在しなかった。2026-09-09 の実測:

    2022年産以前  70頭中 58〜70頭に値あり（当時の別処理が埋めていた）
    2023年産      7,764頭中 66頭
    2024年産      7,855頭中 **0頭**（実際には 2,538頭が出走済み）

その結果 `/api/pog/group/:id/stable-ranking` などが 2024年産の総賞金を
**0 と返していた**（本番 API で確認）。**保存した集計は必ず腐る。**
ここでは `keiba.horse_runs`（＝ keiba + chihou の race_results 統合）から
毎回数える。

## 賞金の単位

`race_results.prize_money` は円。POG の表示は**万円**なので 100 で割る
（移設元と同じ。`/100` の位置を変えると数字が 100 倍ずれる）。

## 指名の有効判定

移設元は `pu."order" != 0` を「有効な指名」として使っていた。
`pick_order != 0` をそのまま引き継ぐ（実測で `order=0` は 10 行あり、
そのすべてが重複行だった＝場所取りの行）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 馬ごとの成績を数える共通部分。`:group_id` と、必要なら `:asof` で絞る。
#
# ⚠️ LEFT JOIN にすること。未出走の指名馬を 0 行にすると、その馬主の
#    「頭数」が減って見える（移設元も LEFT JOIN）。
_HORSE_AGG = """
    SELECT
        p.user_id,
        p.netkeiba_horse_id,
        count(*) FILTER (WHERE r.finish_position = 1)  AS win,
        count(*) FILTER (WHERE r.finish_position = 2)  AS place,
        count(*) FILTER (WHERE r.finish_position = 3)  AS show,
        count(*) FILTER (WHERE r.finish_position >= 4) AS "out",
        COALESCE(SUM(r.prize_money), 0) / 100                                   AS prize,
        COALESCE(SUM(r.prize_money) FILTER (WHERE r.source = 'jra'), 0) / 100    AS prize_jra,
        COALESCE(SUM(r.prize_money) FILTER (WHERE r.source = 'chihou'), 0) / 100 AS prize_other
    FROM keiba.pog_picks p
    LEFT JOIN keiba.horse_runs r
           ON r.netkeiba_horse_id = p.netkeiba_horse_id
          {asof_filter}
    WHERE p.group_id = :group_id AND p.pick_order <> 0
    GROUP BY p.user_id, p.netkeiba_horse_id
"""


@dataclass(frozen=True)
class OwnerRow:
    """馬主 1 人ぶんの集計。"""

    rank: int
    user_id: int
    name: str | None
    win: int
    place: int
    show: int
    out: int
    prize: int
    prize_jra: int
    prize_other: int
    top_horse: str | None


def _owners_sql(*, asof: bool) -> str:
    agg = _HORSE_AGG.format(
        asof_filter="AND r.run_date <= :asof" if asof else ""
    )
    return f"""
        WITH horse_agg AS ({agg}),
        -- ⚠️ タイブレークは**馬名**。移設元が `ORDER BY uid, prize DESC NULLS LAST,
        --    horse_name` なので、ここを馬 ID にすると同賞金の馬が並んだときだけ
        --    表示が変わる（2026年度の2位で実際に食い違った）。
        top_horse AS (
            SELECT DISTINCT ON (ha.user_id)
                   ha.user_id, ha.netkeiba_horse_id, ha.prize
            FROM horse_agg ha
            LEFT JOIN keiba.pog_horses hh
                   ON hh.netkeiba_horse_id = ha.netkeiba_horse_id
            ORDER BY ha.user_id, ha.prize DESC NULLS LAST,
                     COALESCE(NULLIF(hh.name, ''), '母' || hh.broodmare)
        )
        SELECT
            RANK() OVER (ORDER BY SUM(ha.prize) DESC)::int AS rank,
            ha.user_id,
            u.name,
            SUM(ha.win)::int         AS win,
            SUM(ha.place)::int       AS place,
            SUM(ha.show)::int        AS show,
            SUM(ha."out")::int       AS "out",
            SUM(ha.prize)::int       AS prize,
            SUM(ha.prize_jra)::int   AS prize_jra,
            SUM(ha.prize_other)::int AS prize_other,
            CASE WHEN th.prize = 0 THEN NULL ELSE h.name END AS top_horse
        FROM horse_agg ha
        JOIN keiba.users u ON u.id = ha.user_id
        LEFT JOIN top_horse th ON th.user_id = ha.user_id
        LEFT JOIN keiba.pog_horses h ON h.netkeiba_horse_id = th.netkeiba_horse_id
        GROUP BY ha.user_id, u.name, th.prize, h.name
        ORDER BY prize DESC
    """


async def fetch_owners(
    db: AsyncSession, group_id: int, *, asof: date | None = None
) -> list[OwnerRow]:
    """馬主別の集計を賞金の降順で返す。

    Args:
        asof: 指定するとその日までの成績で集計する（順位の変動矢印に使う）。
    """
    params: dict[str, object] = {"group_id": group_id}
    if asof is not None:
        params["asof"] = asof
    rows = await db.execute(text(_owners_sql(asof=asof is not None)), params)
    return [
        OwnerRow(
            rank=r.rank, user_id=r.user_id, name=r.name,
            win=r.win, place=r.place, show=r.show, out=r.out,
            prize=r.prize, prize_jra=r.prize_jra, prize_other=r.prize_other,
            top_horse=r.top_horse,
        )
        for r in rows
    ]


_HORSES_SQL = """
    WITH horse_agg AS ({agg})
    SELECT
        ha.user_id,
        u.name AS owner_name,
        p.pick_order,
        COALESCE(NULLIF(h.name, ''), '母' || h.broodmare) AS horse_name,
        h.netkeiba_horse_id,
        h.sex, h.sire, h.broodmare, h.broodmare_sire, h.stable,
        ha.win, ha.place, ha.show, ha."out", ha.prize
    FROM horse_agg ha
    JOIN keiba.pog_picks p
      ON p.group_id = :group_id AND p.user_id = ha.user_id
     AND p.netkeiba_horse_id IS NOT DISTINCT FROM ha.netkeiba_horse_id
    JOIN keiba.users u ON u.id = ha.user_id
    LEFT JOIN keiba.pog_horses h ON h.netkeiba_horse_id = ha.netkeiba_horse_id
    {user_filter}
    ORDER BY ha.prize DESC, ha.user_id, p.pick_order
"""


async def fetch_horses(
    db: AsyncSession, group_id: int, *, user_id: int | None = None
) -> list[dict]:
    """指名馬の一覧を返す。`user_id` を省くとグループ全馬。"""
    sql = _HORSES_SQL.format(
        agg=_HORSE_AGG.format(asof_filter=""),
        user_filter="WHERE ha.user_id = :user_id" if user_id else "",
    )
    params: dict[str, object] = {"group_id": group_id}
    if user_id:
        params["user_id"] = user_id
    rows = await db.execute(text(sql), params)
    return [dict(r._mapping) for r in rows]
