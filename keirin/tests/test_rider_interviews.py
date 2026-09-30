"""選手コメント（`src/rider_interviews.py`）の取り出し・保存・分類の書き込みを固定する（2026-10-01）。"""
import json
import sqlite3

from src.rider_interviews import (
    TAG_COLUMNS,
    TAG_SCHEMA,
    apply_tags,
    interview_data,
    parse_state,
    rows_from_data,
    upsert,
    valid_tag,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE rider_interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, race_key TEXT NOT NULL,
            cup_id TEXT, race_date TEXT NOT NULL, player_id INTEGER NOT NULL, body TEXT NOT NULL,
            src_created_at TEXT, src_updated_at TEXT, fetched_at TEXT,
            condition INTEGER, trouble TEXT, fatigue INTEGER, equipment INTEGER, self_eval TEXT,
            trend TEXT, tactic TEXT, confidence INTEGER, tag_model TEXT, tagged_at TEXT,
            UNIQUE (kind, race_key, player_id))""")
    return conn


def _page(kind_query: str, answers: list[str]) -> str:
    state = {"tanStackQuery": {"queries": [
        {"queryKey": ["keirin/race/common", "FETCH_KEIRIN_RACE"], "state": {"data": {}}},
        {"queryKey": ["keirin/interview", kind_query], "state": {"data": {
            "interviews": [{"playerId": f"{i:06d}", "threads": [{"question": "q", "answer": a}]}
                           for i, a in enumerate(answers, start=1)],
            "createdAt": 1790741033, "updatedAt": 1790741386}}},
    ]}}
    # 本物のページと同じく、JSON の後ろに別の JS が続く
    return f"<script>window.__PRELOADED_STATE__ = {json.dumps(state)};window.x={{}}</script>"


def test_parse_post_page_and_skip_empty_answers():
    html = _page("FETCH_KEIRIN_RACE_RESULT_INTERVIEW_LIST", ["力負けです。", "", "調子は良い"])
    data = interview_data(parse_state(html), "post")
    rows = rows_from_data("post", "20260930_63_07", "2026092963", "2026-09-30", data)
    assert [(r["player_id"], r["body"]) for r in rows] == [(1, "力負けです。"), (3, "調子は良い")]
    assert rows[0]["src_updated_at"].startswith("2026-09-30")


def test_pre_and_post_queries_are_not_mixed():
    """前検日のページからレース後コメントを拾わない（逆も）。"""
    html = _page("FETCH_KEIRIN_INSPECTION_DAY_INTERVIEW_LIST", ["仕上がりは良い"])
    assert interview_data(parse_state(html), "post") == {}
    assert len(interview_data(parse_state(html), "pre")["interviews"]) == 1


def test_upsert_is_idempotent_and_resets_tags_on_body_change():
    conn = _db()
    rows = rows_from_data("post", "k1", "c", "2026-09-30",
                          {"interviews": [{"playerId": "000001", "threads": [{"answer": "A"}]}]})
    assert upsert(conn, rows) == (1, 0)
    assert upsert(conn, rows) == (0, 0)
    rid = conn.execute("SELECT id FROM rider_interviews").fetchone()[0]
    tag = {"c": 1, "t": "n", "f": 0, "e": 0, "s": "g", "r": "p", "n": "o", "v": 1}
    apply_tags(conn, [(rid, tag)])
    assert conn.execute("SELECT condition, tagged_at IS NOT NULL FROM rider_interviews").fetchone() == (1, 1)
    changed = rows_from_data("post", "k1", "c", "2026-09-30",
                             {"interviews": [{"playerId": "000001", "threads": [{"answer": "A（追記）"}]}]})
    assert upsert(conn, changed) == (0, 1)
    assert conn.execute("SELECT condition, tagged_at FROM rider_interviews").fetchone() == (None, None)


def test_valid_tag_rejects_out_of_range():
    ok = {"c": -2, "t": "FI", "f": 1, "e": 0, "s": "o", "r": "d", "n": "f", "v": -1}
    assert valid_tag(ok)
    assert not valid_tag({**ok, "c": 3})
    assert not valid_tag({**ok, "s": "x"})
    assert not valid_tag({k: v for k, v in ok.items() if k != "v"})


def test_schema_and_columns_cover_the_same_keys():
    props = set(TAG_SCHEMA["properties"]["x"]["items"]["properties"]) - {"i"}
    assert props == set(TAG_COLUMNS)
