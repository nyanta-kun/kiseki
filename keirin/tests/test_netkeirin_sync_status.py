"""netkeirin と記録の状態合わせ（`scripts/netkeirin_sync_status.py`・2026-08-19）。

netkeirin は自分の画面からも公開できるので、そこで押されるとこちらの
`netkeirin_submissions.status` は `submitted`（公開待ち）のまま取り残される。
公開待ち一覧に無いものを `published` へ寄せるのがこのスクリプト。

🔴 **一番危険なのは「取得できなかった」を「0件」と読むこと。** そのまま書くと
   通信が落ちた日にその日の入稿を全部「公開済み」にしてしまう。ここで固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import netkeirin_sync_status as S  # noqa: E402
from src.database import get_connection  # noqa: E402

DATE = "2026-08-19"


def _seed(rows: list[tuple[str, str, str, str | None]]) -> None:
    """(race_key, rank_key, status, netkeirin_race_id) を入れる。"""
    with get_connection() as conn:
        conn.execute("DELETE FROM netkeirin_submissions WHERE race_key LIKE '20260819_%'")
        for race_key, rank_key, status, rid in rows:
            conn.execute(
                "INSERT OR REPLACE INTO netkeirin_submissions "
                "(race_key, rank_key, status, netkeirin_race_id, submitted_at, "
                " venue_name, race_no) VALUES (?,?,?,?,?,?,?)",
                (race_key, rank_key, status, rid, f"{DATE} 07:10:00", "テスト場", 1))
        conn.commit()


def _status() -> dict[str, str]:
    with get_connection() as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT race_key, status FROM netkeirin_submissions "
            "WHERE race_key LIKE '20260819_%'").fetchall()}


class _Client:
    """`NetkeirinClient` の差し替え。`wait_state` だけ持てば足りる。"""

    def __init__(self, state):
        self._state = state

    def wait_state(self):
        return self._state


@pytest.fixture
def patch_client(monkeypatch):
    def _apply(state):
        monkeypatch.setattr(S, "NetkeirinClient", lambda **kw: _Client(state))
    return _apply


def test_marks_published_when_not_waiting_on_netkeirin(patch_client):
    """公開待ち一覧に無い `submitted` は `published` になる。"""
    _seed([("20260819_48_02", "7S", "submitted", "111"),
           ("20260819_48_03", "7S", "submitted", "222")])
    patch_client((True, 0, []))
    res = S.sync(DATE, dry_run=False)
    assert res["ok"] and res["n_synced"] == 2
    assert _status() == {"20260819_48_02": "published", "20260819_48_03": "published"}


def test_keeps_rows_that_are_still_waiting(patch_client):
    """netkeirin にまだ残っているものは触らない。"""
    _seed([("20260819_48_02", "7S", "submitted", "111"),
           ("20260819_48_03", "7S", "submitted", "222")])
    patch_client((True, 1, [{"race_id": "111"}]))
    res = S.sync(DATE, dry_run=False)
    assert res["n_synced"] == 1
    assert _status()["20260819_48_02"] == "submitted"
    assert _status()["20260819_48_03"] == "published"


def test_does_nothing_when_netkeirin_state_cannot_be_read(patch_client):
    """🔴 取得に失敗したら**1件も触らない**。

    `count_wait()` は失敗しても `(0, [])` を返す。それを根拠に書くと
    通信が落ちた日にその日の入稿を全部「公開済み」にしてしまう。
    """
    _seed([("20260819_48_02", "7S", "submitted", "111")])
    patch_client((False, 0, []))
    res = S.sync(DATE, dry_run=False)
    assert res["ok"] is False
    assert res["n_synced"] == 0
    assert _status()["20260819_48_02"] == "submitted"


def test_dry_run_does_not_write(patch_client):
    _seed([("20260819_48_02", "7S", "submitted", "111")])
    patch_client((True, 0, []))
    res = S.sync(DATE, dry_run=True)
    assert res["n_synced"] == 1 and res["dry_run"] is True
    assert _status()["20260819_48_02"] == "submitted"


def test_never_touches_published_or_deleted(patch_client):
    """🔴 逆向き（published → submitted）はしない。公開は不可逆なので必ず誤り。"""
    _seed([("20260819_48_02", "7S", "published", "111"),
           ("20260819_48_03", "7S", "deleted", "222")])
    patch_client((True, 0, []))
    res = S.sync(DATE, dry_run=False)
    assert res["n_synced"] == 0
    assert _status() == {"20260819_48_02": "published", "20260819_48_03": "deleted"}


def test_unparsable_wait_list_is_an_error_not_an_empty_set():
    """🔴 解釈できない一覧を空集合にしない（＝「全部公開された」と読まれる）。"""
    with pytest.raises(ValueError):
        S._wait_race_ids([{"no_race_id_here": 1}])
    with pytest.raises(ValueError):
        S._wait_race_ids([object()])
    # 文字列・数値・dict はどれも拾える
    assert S._wait_race_ids(["1", 2, {"race_id": 3}]) == {"1", "2", "3"}


def test_count_wait_still_hides_failures_but_wait_state_does_not():
    """`count_wait` は画面用に失敗を隠す。`wait_state` は隠さない（役割の違い）。"""
    import inspect

    from src import netkeirin_client

    src = inspect.getsource(netkeirin_client.NetkeirinClient.count_wait)
    assert "wait_state()" in src, "count_wait は wait_state へ委譲すること（実装の二重化を防ぐ）"


def test_tests_never_talk_to_the_production_database():
    """🔴 テストが本番 PostgreSQL を掴んでいないこと（2026-08-19 の実害）。

    この Mac は `~/.zshrc` が `KEIRIN_DB_URL`（本番）を export しているため、
    「未設定なら SQLite」という条件付きの切り替えでは効かず、ローカルの
    pytest が本番を直接叩いていた。上のテスト群は `netkeirin_submissions` を
    seed するので、**当日の入稿45件が実際に消えた**。
    conftest が握り潰しているので、ここに来る時点で環境変数は消えている。
    """
    import os

    assert not os.environ.get("KEIRIN_DB_URL"), (
        "テスト中に KEIRIN_DB_URL が生きています。conftest.pytest_configure が "
        "握り潰しているはずで、ここが立っているなら本番へ書き込む恐れがあります")
    from src.database import get_connection
    with get_connection() as conn:
        assert conn.__class__.__module__.startswith("sqlite3"), (
            f"SQLite ではない接続です: {conn.__class__}")
