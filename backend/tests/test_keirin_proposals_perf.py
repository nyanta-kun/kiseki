"""入稿案APIの**性能修正**を構造で固定する（2026-08-23）。

## 背景（本番が遅くなった実バグ）

2026-08-21 の落車リスク指標で入った `Q_SQL_CRASH` が
`keirin.wt_entries JOIN keirin.wt_races` を使い、`player_id` に索引が無いため
**724,166行 / 448MB を毎リクエスト全表走査**していた。本番 EXPLAIN で
`Execution Time 2575.879 ms`。トップページ表示の支配項になっていた。

修正は2つで、どちらも**壊れても例外が出ない**ため、ここで固定する:

1. `wt_races` との結合を外し `e.race_key < :ymd`（8桁）で絞る
2. トップページのバッジは `/proposals/count`（件数だけ）を使う

⚠️ 1 は**日付の切れ目を1日ずらすと落車リスクが当日のレースを含んでしまう**
   （look-ahead）。文字列比較の境界をテストで押さえる。
"""
from __future__ import annotations

import re

from src.api import keirin_router as R


def test_crash_sql_does_not_join_wt_races():
    """🔴 結合を戻すと本番実測 655ms → 2,576ms に逆戻りする。"""
    sql = R.Q_SQL_CRASH
    assert "wt_races" not in sql, "wt_races との結合は性能上戻してはいけない"
    assert "e.race_key < :ymd" in sql, "日付の絞り込みは race_key の前置8桁で行う"


def test_crash_sql_filters_by_player_id_for_index():
    """`player_id` の等値/IN で絞ること（ix_wt_entries_player_id が効く形）。"""
    assert re.search(r"e\.player_id\s+IN\s*\(", R.Q_SQL_CRASH)


def _race_key_lt(race_key: str, ymd: str) -> bool:
    """本番 SQL と同じ比較（Postgres の text 比較＝辞書順）。"""
    return race_key < ymd


def test_race_key_string_compare_matches_race_date_lt():
    """🔴 **当日を含めないこと。** 含めると落車リスクが look-ahead になる。

    `race_key` は `YYYYMMDD_場_R`。8桁の日付と辞書順で比べると
    「前日以前だけ」が残る。
    """
    ymd = "20260823"
    assert _race_key_lt("20260822_27_01", ymd) is True      # 前日は含む
    assert _race_key_lt("20251231_27_12", ymd) is True      # 年跨ぎも含む
    assert _race_key_lt("20260823_27_01", ymd) is False     # 🔴 当日は含めない
    assert _race_key_lt("20260824_27_01", ymd) is False     # 翌日も含めない
    # 桁が同じで末尾だけ違うケース（`_` は数字より大きいので必ず当日側が上）
    assert _race_key_lt("20260823_01_01", ymd) is False


def test_proposals_count_endpoint_exists_and_is_separate():
    """バッジ用の軽い口があること。

    🔴 これが消えるとフロントが `/proposals` 本体（201〜282KB・約3秒）へ
       戻り、トップページが再び遅くなる。
    """
    paths = {r.path for r in R.router.routes}  # type: ignore[attr-defined]
    assert "/api/keirin/proposals/count" in paths
    assert "/api/keirin/proposals" in paths


def test_proposals_count_matches_full_endpoint_semantics():
    """件数の定義を本体と揃える（削除済みも数える）。

    ⚠️ `/proposals` 側の `n_proposed` は `deleted_at` を見ずに
       `status == STATUS_PROPOSED` を数えている。count 側だけ
       `deleted_at IS NULL` を足すと**バッジの数字が黙って変わる**。
       挙動を変えるなら両方を同時に変えること。
    """
    src = R.get_proposals_count.__doc__ or ""
    assert src, "docstring で意図を残すこと"
    import inspect
    body = inspect.getsource(R.get_proposals_count)
    # 🔴 コメントには "deleted_at IS NULL" の語が出てくるので、**SQL 本体だけ**を見る。
    sql = body.split('text("""')[1].split('"""')[0]
    assert "deleted_at" not in sql, (
        "本体の n_proposed は削除済みも数える。ここだけ条件を足すと食い違う"
    )
    assert "status = :st" in sql
