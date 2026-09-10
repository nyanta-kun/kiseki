"""POG の兄弟馬（sekito `/api/pog/sibling-horses` の移設先）。

統合 Phase 5。**同じ母から 2 回以上指名されている**母をまとめ、その産駒を
年度の新しい順に並べる。ドラフトの下調べ（この母の上の子はどうだったか）に使う。

## 🔴 成績は毎回 `keiba.horse_runs` から数える

移設元は `sekito.horse` の保存列（`prize` / `win` / `place` / `show` / `out`）を
`CONCAT` して戦績を作っていたが、その列は更新されておらず **2024年産は全頭 0**
だった（`pog_standings.py` の docstring 参照）。つまり兄弟馬の画面は
**新しい世代ほど戦績が空**という状態だった。

## ⚠️ 「兄弟」は母が同じことだけで判定する

移設元と同じ。父は見ない（半兄弟を含む）。母名の表記ゆれは吸収しない。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SQL = text(
    """
    WITH picked AS (
        SELECT
            p.netkeiba_horse_id,
            p.user_id,
            p.pick_order,
            pg.year,
            NULLIF(h.broodmare, '') AS broodmare,
            h.name,
            h.sex,
            h.sire,
            h.stable,
            COALESCE(m.nickname, u.name) AS owner_name
        FROM keiba.pog_picks p
        JOIN keiba.pog_groups pg ON pg.id = p.group_id
        JOIN keiba.pog_horses h ON h.netkeiba_horse_id = p.netkeiba_horse_id
        LEFT JOIN keiba.pog_group_members m
               ON m.group_id = p.group_id AND m.user_id = p.user_id
        LEFT JOIN keiba.users u ON u.id = p.user_id
        WHERE p.pick_order <> 0
    ),
    counts AS (
        SELECT broodmare, count(*) AS nomination_count
        FROM picked
        WHERE broodmare IS NOT NULL
        GROUP BY broodmare
        HAVING count(*) >= :min_nominations
    ),
    runs AS (
        SELECT netkeiba_horse_id,
               count(*) FILTER (WHERE finish_position = 1)  AS win,
               count(*) FILTER (WHERE finish_position = 2)  AS place,
               count(*) FILTER (WHERE finish_position = 3)  AS show,
               count(*) FILTER (WHERE finish_position >= 4) AS "out",
               COALESCE(SUM(prize_money), 0) / 100          AS prize
        FROM keiba.horse_runs
        GROUP BY netkeiba_horse_id
    )
    SELECT
        p.broodmare,
        c.nomination_count::int,
        p.year,
        p.netkeiba_horse_id,
        COALESCE(NULLIF(p.name, ''), '（未命名）') AS horse_name,
        p.sex,
        p.sire,
        p.stable,
        p.owner_name,
        p.pick_order,
        COALESCE(r.win, 0)::int   AS win,
        COALESCE(r.place, 0)::int AS place,
        COALESCE(r.show, 0)::int  AS show,
        COALESCE(r."out", 0)::int AS "out",
        COALESCE(r.prize, 0)::int AS prize
    FROM picked p
    JOIN counts c ON c.broodmare = p.broodmare
    LEFT JOIN runs r ON r.netkeiba_horse_id = p.netkeiba_horse_id
    ORDER BY c.nomination_count DESC, p.broodmare, p.year DESC, horse_name
    """
)


async def fetch_siblings(
    db: AsyncSession, *, min_nominations: int = 2
) -> list[dict]:
    """母ごとに指名された産駒をまとめて返す。

    Args:
        db: DB セッション。
        min_nominations: 何回以上指名された母を出すか（移設元の既定は 2）。

    Returns:
        `[{broodmare, nomination_count, horses: [...]}, ...]`。
        指名数の多い母から並ぶ。
    """
    rows = (await db.execute(_SQL, {"min_nominations": min_nominations})).mappings()
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(
            r["broodmare"],
            {
                "broodmare": r["broodmare"],
                "nomination_count": r["nomination_count"],
                "horses": [],
            },
        )
        g["horses"].append(
            {
                "year": r["year"],
                "netkeiba_horse_id": r["netkeiba_horse_id"],
                "horse_name": r["horse_name"],
                "sex": r["sex"],
                "sire": r["sire"],
                "stable": r["stable"],
                "owner_name": r["owner_name"],
                "pick_order": r["pick_order"],
                "win": r["win"],
                "place": r["place"],
                "show": r["show"],
                "out": r["out"],
                "prize": r["prize"],
            }
        )
    return list(grouped.values())
