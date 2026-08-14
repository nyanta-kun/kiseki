"""jvnext_dm_importer の進捗記録（`progress`）のテスト。

🔴 **固定したい挙動**: 「POST は通ったが 1 頭も更新できなかった（updated=0）」を
成功として `progress` に記録してはいけない。

DM ファイルは出馬表（`race_entries`）より先に取得できることがあり、その回の POST は
必ず 0 件で終わる。旧実装はそれを `"ok"` として記録していたため、以後 `skip_ok` で
永久にスキップされ **その開催日は二度と DM が入らなかった**
（2026-08-09 が 5.9% のまま / 2026-08-15・16 が 0%）。

総合指数 v27 は gain の 71.8% を DM 2列に依存しており、欠けると指数1位馬の勝率が
28.1% → 22.8% に落ちる（docs/jra_rebuild_2026_08.md 4.2 / 4.4）。

    python3 -m pytest windows-agent/tests/test_jvnext_dm_importer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jvnext_dm_importer as imp  # noqa: E402

RACE_ID = "2026081501010701"


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict:
    """外部 I/O を全て差し替え、POST の戻り値だけ試験で決められるようにする。"""
    state: dict = {"posted": [], "response": {"updated": 0, "skipped": 3}}

    monkeypatch.setattr(imp, "fetch_race_id_map", lambda date: {("01", "01"): RACE_ID})
    monkeypatch.setattr(
        imp, "load_1403_file",
        lambda path: {1: {"jvan_time_dm": 81.0, "jvan_battle_dm": 80.7}},
    )
    monkeypatch.setattr(imp, "save_progress", lambda p: None)

    def _post(jravan_race_id: str, records: list[dict]) -> dict:
        state["posted"].append((jravan_race_id, len(records)))
        return state["response"]

    monkeypatch.setattr(imp, "post_race_records", _post)
    return state


def _files() -> list[Path]:
    return [Path("1403202608150101.dat")]


def test_updated_zero_is_not_recorded_as_ok(wired: dict) -> None:
    """反映0件は progress に残さない（次回に再試行される）。"""
    progress: dict[str, str] = {}
    ok, failed, skipped, empty = imp.run_import(_files(), progress)

    assert (ok, failed, skipped, empty) == (0, 0, 0, 1)
    assert RACE_ID not in progress, "updated=0 を ok として記録してはいけない"
    assert wired["posted"] == [(RACE_ID, 1)]


def test_burned_ok_is_cleared_when_still_empty(wired: dict) -> None:
    """旧実装が焼き付けた "ok" は、再実行して 0 件なら取り消される。"""
    progress = {RACE_ID: "ok"}
    imp.run_import(_files(), progress, skip_ok=False)

    assert RACE_ID not in progress


def test_updated_positive_is_recorded_as_ok(wired: dict) -> None:
    """実際に反映されたときだけ ok を記録する。"""
    wired["response"] = {"updated": 14, "skipped": 0}
    progress: dict[str, str] = {}
    ok, failed, skipped, empty = imp.run_import(_files(), progress)

    assert (ok, failed, skipped, empty) == (1, 0, 0, 0)
    assert progress[RACE_ID] == "ok"


def test_skip_ok_skips_only_recorded_races(wired: dict) -> None:
    """ok 済みは skip_ok=True で POST しない（既存の高速化は壊さない）。"""
    progress = {RACE_ID: "ok"}
    ok, failed, skipped, empty = imp.run_import(_files(), progress, skip_ok=True)

    assert (ok, failed, skipped, empty) == (0, 0, 1, 0)
    assert wired["posted"] == []


# ---------------------------------------------------------------------------
# 1403 ファイルの行選択（小倉のヘッダ行が DM 行に化ける問題）
# ---------------------------------------------------------------------------

def _dat(tmp_path: Path, lines: list[str]) -> Path:
    import zlib
    p = tmp_path / "1403202607191008.dat"
    p.write_bytes(zlib.compress("\r\n".join(lines).encode("cp932")))
    return p


def _dm_line(upd: int, n_horses: int) -> str:
    """更新回数 upd の DM 行。1頭 25 バイト（タイム4 + 対戦4 + 余白17）。"""
    body = "".join("0810" "0807" + " " * 17 for _ in range(n_horses))
    return str(upd) + "0" * (imp.LINE1_HEADER_LEN - 1) + body


def test_short_header_line_is_not_mistaken_for_data(tmp_path: Path) -> None:
    """先頭が `1` のヘッダ行（小倉=場コード10）を DM 行と取り違えない。

    ヘッダも DM 行も更新回数 1 だと max が同点になり、先に現れるヘッダが選ばれて
    1 レース丸ごと落ちていた（2026-07-19 小倉）。
    """
    header = "1020260208080910364"          # 19文字・先頭が '1'
    path = _dat(tmp_path, [header, _dm_line(1, 3), "", "0" * 500])

    result = imp.load_1403_file(path)

    assert len(result) == 3, "ヘッダ行が選ばれると 0 件になる"
    assert result[1] == {"jvan_time_dm": 81.0, "jvan_battle_dm": 80.7}


def test_latest_update_wins(tmp_path: Path) -> None:
    """更新回数が大きい行を採用する（従来の挙動は壊さない）。"""
    path = _dat(tmp_path, ["0220260112080450120", _dm_line(1, 2), _dm_line(3, 5)])

    assert len(imp.load_1403_file(path)) == 5


def test_post_failure_is_recorded_as_failed(wired: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST 例外は failed として記録し、--retry-failed で拾えるようにする。"""
    def _boom(jravan_race_id: str, records: list[dict]) -> dict:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(imp, "post_race_records", _boom)
    progress: dict[str, str] = {}
    ok, failed, skipped, empty = imp.run_import(_files(), progress)

    assert (ok, failed, skipped, empty) == (0, 1, 0, 0)
    assert progress[RACE_ID] == "failed"
