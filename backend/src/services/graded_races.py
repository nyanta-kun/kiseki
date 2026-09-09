"""今週の重賞一覧（sekito `/api/races/graded` の移設先）。

統合 Phase 5 の 5d-4。POG 詳細ページの重賞パネルの中身で、14 日のアクセスログで
**106 回**と POG 本体に次ぐ利用があった。

## 🔴 移設元は地方の優勝馬を 2026-06 以降ずっと出せていなかった

中央は `keiba.race_results` を直読みしていたのに、**地方だけ `sekito.entries`**
から `result = '1'` を引いていた。あのテーブルは **2026-05-03 で凍結**している
ので、それ以降の地方重賞（帝王賞・不来方賞・サマーチャンピオン…）の優勝馬は
**必ず空欄**になる。レースは出るので「まだ結果が出ていない」ようにしか見えない。

ここでは中央・地方とも `{schema}.race_results` + `{schema}.horses` を直読みする。

## 何を「重賞」とするか

移設元と同じ 7 つに絞る（`OP特別` / `Listed` / 一般を除く）:

    G1  G2  G3  J.G1  J.G2  J.G3  重賞

⚠️ **地方の `S` / `T` / `R` / `P` / `Q` は入れない**（移設元と同じ）。これらは
NAR の重賞格付けで、実体は地方重賞そのもの（西日本３歳優駿・戸塚記念・
アフター５スター賞など・年 420 件ほど）。除外は移設元の仕様であって不具合では
ないが、**地方重賞が一切出ない**という意味なので、出すかどうかは別途決めること。
`chihou` から拾えるのはダートグレード（G1〜G3・年 46 件）だけになる。

## 落とした列

移設元は `status`（`init` / `resulted` / `cancelled`）を返し、フロントは
`cancelled` に【中止】を出していた。kiseki の `races` にこの列は無い。
実測では `cancelled` は全 65,164 行中 **36 行**、すべて 2026-02-09 京都の
未勝利戦（降雪）で、**重賞は 1 件も無い**。中止された重賞は優勝馬が空欄の
まま残る（「まだ結果が出ていない」ように見える）。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 重賞とみなす `races.grade`。移設元の白リストと同一。
GRADES = ("G1", "G2", "G3", "J.G1", "J.G2", "J.G3", "重賞")

#: 中央・地方で表の形が同じなのでスキーマ名だけ差し替えて使う。
#: ⚠️ `:schema` のようなプレースホルダは識別子に使えないため文字列で埋める。
#:    埋めてよいのは下の `_SCHEMAS` の値だけ（外部入力を通さないこと）。
_SCHEMAS = {"jra": "keiba", "chihou": "chihou"}

_SQL = """
    SELECT
        r.id                AS race_id,
        '{kind}'            AS kind,
        r.date              AS date,
        r.course_name       AS course_name,
        r.race_number       AS race_number,
        r.race_name         AS race_name,
        r.post_time         AS post_time,
        r.grade             AS grade,
        w.name              AS winner_name
    FROM {schema}.races r
    LEFT JOIN LATERAL (
        SELECT h.name
        FROM {schema}.race_results rr
        JOIN {schema}.horses h ON h.id = rr.horse_id
        WHERE rr.race_id = r.id AND rr.finish_position = 1
        LIMIT 1
    ) w ON true
    WHERE r.date BETWEEN :start AND :end
      AND r.grade = ANY(:grades)
"""


async def fetch_graded_races(
    db: AsyncSession, *, start: str, end: str
) -> list[dict]:
    """`start`〜`end`（ともに `YYYYMMDD`）の重賞を中央・地方まとめて返す。

    Args:
        db: DB セッション。
        start: 開始日 `YYYYMMDD`（両端を含む）。
        end: 終了日 `YYYYMMDD`（両端を含む）。

    Returns:
        日付→発走時刻→レース番号の順に並べた重賞のリスト。
        `winner_name` は結果が入っていなければ None。
    """
    union = "\nUNION ALL\n".join(
        _SQL.format(kind=kind, schema=schema) for kind, schema in _SCHEMAS.items()
    )
    # ⚠️ post_time は未確定のレースで NULL。既定の昇順は NULL が最後に来るので
    #    明示して固定する（発走が決まった順に上へ並ぶ）。
    sql = text(
        f"SELECT * FROM (\n{union}\n) t "
        "ORDER BY date, post_time NULLS LAST, race_number"
    )
    rows = await db.execute(
        sql, {"start": start, "end": end, "grades": list(GRADES)}
    )
    return [dict(r) for r in rows.mappings()]
