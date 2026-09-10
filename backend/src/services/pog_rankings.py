"""POG ランキング（sekito `/group/:id/*` `/all-groups/*` の移設先）。

統合 Phase 5。移設元は **9 指標 × 2 スコープ = 18 エンドポイント**に分かれて
いたが、どれも「何かで束ねて数える」だけなので **1 本にまとめた**。

    GET /api/pog/rankings?metric=sire-count[&year=2026]

`year` を省くと全年度（移設元の `/all-groups/*`）、指定するとその年度
（移設元の `/group/:id/*`）。

## 指標

| metric | 束ねる単位 | value | sub | total |
|---|---|---|---|---|
| `sire-count` | 父 | 指名数 | — | — |
| `sire-avg-prize` | 父 | 平均賞金(万) | — | — |
| `sire-win-rate` | 父 | 勝率(%) | 勝ち数 | 出走数 |
| `broodmare-sire-count` | 母父 | 指名数 | — | — |
| `broodmare-sire-avg-prize` | 母父 | 平均賞金(万) | — | — |
| `stable-ranking` | 厩舎 | 合計賞金(万) | 勝ち数 | — |
| `horse-performance` | 馬 | 賞金(万) | 勝ち数 | 出走数 |
| `debut-rate` | 馬主 | デビュー率(%) | 出走した頭数 | — |
| `win-rate` | 馬主 | 勝ち上がり率(%) | 勝った頭数 | — |
| `graded-win-count` | 馬主 | 重賞勝利数 | 重賞を勝った頭数 | — |

`count` はどの指標でも「その束に属する指名頭数」。

## 🔴 成績は毎回 `keiba.horse_runs` から数える

移設元の `stable-ranking` は `sekito.horse` の保存列（`prize` / `win` …）を
読んでいたが、その列は更新されておらず **2024年産は全頭 0** だった
（`pog_standings.py` の docstring 参照）。ここでは他の POG 画面と同じく
`keiba.horse_runs` から数える。

## ⚠️ 厩舎の東西（美浦/栗東）は出せない

移設元は `sekito.entries.stable_area` から関東/関西を割り出して 2 つに
分けていた。そのテーブルは **2026-05-03 で凍結**しており、kiseki 側に
所属地の情報は無い（`keiba.trainers` は id / name / jravan_code だけ）。
**厩舎名のランキングだけを出す**。東西で分けたい場合は別途データ源が要る。

## ⚠️ 移設元の `sire-win-rate` は壊れていた

`sekito.v_entries`（凍結）を JOIN していたため、2026-06 以降の出走が
1 件も数えられていない。ここは `keiba.horse_runs` を使う。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Metric:
    """1 指標の定義。

    Attributes:
        label: 画面に出す名前。
        group_sql: 束ねる式（`SELECT` と `GROUP BY` の両方に使う）。
        value_sql: 主指標の式。
        sub_sql: 補助の整数。無ければ None。
        total_sql: 母数。無ければ None。
        desc: 大きい順に並べるか。
        having_sql: `HAVING` 句（母数 0 を落とすなど）。
    """

    label: str
    group_sql: str
    value_sql: str
    sub_sql: str | None = None
    total_sql: str | None = None
    desc: bool = True
    having_sql: str | None = None


#: 1 行 = 1 指名馬。ここに指名・馬マスタ・成績をまとめておき、
#: 指標ごとに束ね方だけを変える。
#: ⚠️ 成績は LEFT JOIN で数える（未出走の指名馬を落とすと頭数が減って見える）。
_BASE = """
    WITH picks AS (
        SELECT
            p.user_id,
            p.netkeiba_horse_id,
            COALESCE(m.nickname, u.name)              AS owner_name,
            NULLIF(h.sire, '')                        AS sire,
            NULLIF(h.broodmare_sire, '')              AS broodmare_sire,
            NULLIF(h.stable, '')                      AS stable,
            COALESCE(NULLIF(h.name, ''), '（未命名）') AS horse_name
        FROM keiba.pog_picks p
        JOIN keiba.pog_groups pg ON pg.id = p.group_id
        LEFT JOIN keiba.pog_group_members m
               ON m.group_id = p.group_id AND m.user_id = p.user_id
        LEFT JOIN keiba.users u ON u.id = p.user_id
        LEFT JOIN keiba.pog_horses h ON h.netkeiba_horse_id = p.netkeiba_horse_id
        WHERE p.pick_order <> 0
          AND (CAST(:year AS int) IS NULL OR pg.year = CAST(:year AS int))
    ),
    runs AS (
        SELECT
            netkeiba_horse_id,
            count(*)                                        AS races,
            count(*) FILTER (WHERE finish_position = 1)      AS wins,
            COALESCE(SUM(prize_money), 0) / 100              AS prize
        FROM keiba.horse_runs
        GROUP BY netkeiba_horse_id
    ),
    graded AS (
        SELECT netkeiba_horse_id,
               count(*)                                          AS graded_wins,
               count(*) FILTER (WHERE grade = 'G1')               AS g1,
               count(*) FILTER (WHERE grade = 'G2')               AS g2,
               count(*) FILTER (WHERE grade = 'G3')               AS g3
        FROM keiba.mv_graded_wins
        GROUP BY netkeiba_horse_id
    ),
    horse AS (
        SELECT
            pk.*,
            COALESCE(r.races, 0)        AS races,
            COALESCE(r.wins, 0)         AS wins,
            COALESCE(r.prize, 0)        AS prize,
            COALESCE(g.graded_wins, 0)  AS graded_wins,
            COALESCE(g.g1, 0)           AS g1,
            COALESCE(g.g2, 0)           AS g2,
            COALESCE(g.g3, 0)           AS g3
        FROM picks pk
        LEFT JOIN runs r   ON r.netkeiba_horse_id = pk.netkeiba_horse_id
        LEFT JOIN graded g ON g.netkeiba_horse_id = pk.netkeiba_horse_id
    )
"""

#: 100 分率。母数 0 のときに 0 を返す（`NULLIF` で 0 除算を避ける）。
_PCT = "ROUND(100.0 * {num} / NULLIF({den}, 0), 1)"

METRICS: dict[str, Metric] = {
    "sire-count": Metric("種牡馬 指名数", "sire", "count(*)"),
    "sire-avg-prize": Metric(
        "種牡馬 平均賞金", "sire", "ROUND(AVG(prize), 1)"
    ),
    "sire-win-rate": Metric(
        "種牡馬 勝率",
        "sire",
        _PCT.format(num="SUM(wins)", den="SUM(races)"),
        sub_sql="SUM(wins)",
        total_sql="SUM(races)",
        having_sql="SUM(races) > 0",
    ),
    "broodmare-sire-count": Metric("母父 指名数", "broodmare_sire", "count(*)"),
    "broodmare-sire-avg-prize": Metric(
        "母父 平均賞金", "broodmare_sire", "ROUND(AVG(prize), 1)"
    ),
    "stable-ranking": Metric(
        "厩舎別 賞金", "stable", "SUM(prize)", sub_sql="SUM(wins)"
    ),
    "horse-performance": Metric(
        "個別馬 賞金",
        "horse_name",
        "SUM(prize)",
        sub_sql="SUM(wins)",
        total_sql="SUM(races)",
    ),
    "debut-rate": Metric(
        "デビュー率",
        "owner_name",
        _PCT.format(num="count(*) FILTER (WHERE races > 0)", den="count(*)"),
        sub_sql="count(*) FILTER (WHERE races > 0)",
    ),
    "win-rate": Metric(
        "勝ち上がり率",
        "owner_name",
        _PCT.format(num="count(*) FILTER (WHERE wins > 0)", den="count(*)"),
        sub_sql="count(*) FILTER (WHERE wins > 0)",
    ),
    "graded-win-count": Metric(
        "重賞勝利数",
        "owner_name",
        "SUM(graded_wins)",
        sub_sql="count(*) FILTER (WHERE graded_wins > 0)",
        having_sql="SUM(graded_wins) > 0",
    ),
}


async def fetch_ranking(
    db: AsyncSession, *, metric: str, year: int | None = None, limit: int = 50
) -> list[dict]:
    """ランキングを 1 本返す。

    Args:
        db: DB セッション。
        metric: `METRICS` のキー。
        year: POG の年度。None なら全年度（通算）。
        limit: 返す行数の上限。

    Raises:
        ValueError: 知らない `metric` のとき。
    """
    if metric not in METRICS:
        raise ValueError(f"知らない指標です: {metric!r}")
    m = METRICS[metric]

    # `m` の各式は上の `METRICS` にしかない定数なので埋め込んでよい。
    # 外から来るのは `metric` の名前だけで、それは辞書の鍵として検証済み。
    sql = text(
        f"""
        {_BASE}
        SELECT
            {m.group_sql}                       AS key,
            count(*)::int                       AS count,
            ({m.value_sql})::numeric            AS value,
            {f"({m.sub_sql})::int" if m.sub_sql else "NULL::int"}     AS sub,
            {f"({m.total_sql})::int" if m.total_sql else "NULL::int"} AS total,
            SUM(g1)::int                        AS g1,
            SUM(g2)::int                        AS g2,
            SUM(g3)::int                        AS g3
        FROM horse
        WHERE {m.group_sql} IS NOT NULL
        GROUP BY {m.group_sql}
        {f"HAVING {m.having_sql}" if m.having_sql else ""}
        ORDER BY value {"DESC" if m.desc else "ASC"}, count DESC, key
        LIMIT :limit
        """
    )
    rows = await db.execute(sql, {"year": year, "limit": limit})
    out: list[dict] = []
    for r in rows.mappings():
        d = dict(r)
        # numeric は Decimal で返るので float にする（JSON にそのまま乗せられない）。
        d["value"] = float(d["value"]) if d["value"] is not None else 0.0
        out.append(d)
    return out
