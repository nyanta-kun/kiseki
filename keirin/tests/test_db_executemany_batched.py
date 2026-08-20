"""一括書き込みが「1行ずつの往復」に戻らないことを固定する（2026-08-20 新設）。

🔴 **これはテストで守るしかない。** psycopg2 の素の `executemany` は1行につき1往復
   するが、**動作としては正しい**ので機能テストでは絶対に検出できない。壊れても
   「なぜか遅い」としか見えず、実際 2026-08-20 まで気づかれなかった。

実測（VPS への RTT 24.9ms）:
    16,000行の UPDATE   素の executemany 約399秒 → execute_batch 3秒（約130倍）
    backfill_index_pct_wt.py は所要時間の76%がこの往復待ちだった
"""
from unittest.mock import MagicMock, patch

from src.database import _PgConn


def _conn_with_mock_cursor():
    raw = MagicMock()
    cur = MagicMock()
    raw.cursor.return_value = cur
    conn = _PgConn.__new__(_PgConn)      # __init__ は実接続を張るので通さない
    conn._conn = raw
    return conn, cur


def test_executemany_uses_execute_batch_not_row_by_row():
    """🔴 1行ずつ送る `cur.executemany` を使わないこと。"""
    conn, cur = _conn_with_mock_cursor()
    rows = [(1.0, "20260820_45_01", i) for i in range(1, 8)]
    with patch("psycopg2.extras.execute_batch") as eb:
        conn.executemany(
            "UPDATE wt_entries SET pred_win_pct = ? WHERE race_key = ? AND frame_no = ?",
            rows,
        )
    eb.assert_called_once()
    cur.executemany.assert_not_called()          # ← ここが本体
    args, kwargs = eb.call_args
    assert args[2] == rows                       # 行はそのまま渡す
    assert kwargs.get("page_size", 0) >= 100     # まとめて送っている


def test_executemany_translates_placeholders():
    """`?` → `%s` の変換が execute_batch 経路でも効いていること。"""
    conn, _ = _conn_with_mock_cursor()
    with patch("psycopg2.extras.execute_batch") as eb:
        conn.executemany("UPDATE t SET a = ? WHERE b = ?", [(1, 2)])
    sql = eb.call_args[0][1]
    assert "?" not in sql and "%s" in sql


def test_executemany_noop_on_empty_rows():
    """空リストでは接続に触らない（従来の挙動を保つ）。"""
    conn, cur = _conn_with_mock_cursor()
    with patch("psycopg2.extras.execute_batch") as eb:
        conn.executemany("UPDATE t SET a = ? WHERE b = ?", [])
    eb.assert_not_called()
    cur.executemany.assert_not_called()
