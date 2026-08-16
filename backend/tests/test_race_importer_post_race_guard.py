"""発走後にしか埋まらない列を発走前 RA で NULL 上書きしないことの検査。

## なぜ要るか

差分ファイルには同一レースの複数データ区分（1:出走馬名表 / 2:出馬表 / 成績）が
入る。発走前の区分では馬場状態・出走頭数などが空なので、素直に UPSERT すると
**確定済みの値を NULL へ戻す**。

2026-08-16 に実際に起きた: `head_count` がガードから漏れており、
`jvlink_agent.py --mode fix-race --from-date 20260814` を流したところ
**開催済みだった 8/15 の `races.head_count` が 36 レースぶん消えた**
（memory: jra_entries_dm_cascade_2026_08_16）。列を1つ足し忘れるだけで
静かにデータが欠けるので、生成される SQL の形で固定する。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from src.importers.race_importer import POST_RACE_ONLY_COLS, RaceImporter


def _ra(jravan_race_id: str = "2026081501010101", **over) -> dict:
    """`_bulk_upsert_races` が受け取る parse_ra 相当の 1 件。"""
    base = {
        "jravan_race_id": jravan_race_id,
        "race_date": "20260815",
        "course": "01",
        "course_name": "札幌",
        "race_number": 1,
        "surface": "芝",
        "distance": 1200,
        # 発走前 RA ではこれらが空（parse_ra の _i は "00"/空を None にする）
        "head_count": None,
        "condition": None,
        "weather": None,
        "finishers_count": None,
        # 発走前から埋まる列
        "registered_count": 14,
        "post_time": "1000",
    }
    base.update(over)
    return base


async def _compiled_upsert_sql(ra_list: list[dict]) -> str:
    """`_bulk_upsert_races` が実行する SQL を捕まえて文字列にする。"""
    db = AsyncMock()
    captured: list = []

    async def _execute(stmt, *a, **k):
        captured.append(stmt)
        result = AsyncMock()
        result.__iter__ = lambda self: iter([])  # RETURNING を空で返す
        return result

    db.execute = AsyncMock(side_effect=_execute)
    importer = RaceImporter(db)
    await importer._bulk_upsert_races(ra_list)
    assert captured, "UPSERT が実行されていない"
    return str(captured[-1].compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_head_count_is_guarded_by_coalesce():
    """head_count は COALESCE 経由でのみ更新される（発走前の空で潰さない）。"""
    sql = await _compiled_upsert_sql([_ra()])
    assert "head_count" in sql
    # excluded.head_count を直接代入していたら、発走前 RA が確定値を NULL にする
    assert "coalesce(excluded.head_count" in sql.lower(), (
        "head_count が COALESCE ガードを通っていない。"
        "発走前 RA で確定済みの出走頭数が NULL に上書きされる"
    )


@pytest.mark.asyncio
async def test_all_post_race_columns_are_guarded():
    """POST_RACE_ONLY_COLS の全列が COALESCE 経由であること。"""
    sql = (await _compiled_upsert_sql([_ra()])).lower()
    missing = [c for c in POST_RACE_ONLY_COLS if f"coalesce(excluded.{c}" not in sql]
    assert not missing, f"COALESCE ガードが掛かっていない列: {missing}"


@pytest.mark.asyncio
async def test_registered_count_is_not_guarded():
    """登録頭数は発走前から埋まるので、素直に上書きされること。

    ここをガードしてしまうと、出走取消で頭数が減ったときに古い値が残る。
    """
    assert "registered_count" not in POST_RACE_ONLY_COLS
    sql = (await _compiled_upsert_sql([_ra()])).lower()
    assert "coalesce(excluded.registered_count" not in sql
