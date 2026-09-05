"""realtime ループに組み込んだ「翌日ぶん前日発売オッズ」の回帰テスト。

なぜ必要か:
  開催日は agent が realtime モードで動くため、VPS cron が毎時投げる
  `odds_prefetch` コマンドは **一度も実行されない**（あれは run_command_loop
  でしか処理されない）。実測で 2026-09-05 に9回キュー投入された翌日ぶんが
  1回も走らず、日曜の朝までオッズが空だった。realtime ループ自身が1時間に
  1回だけ翌日ぶんを取りに行くようにしたので、その配線が外れないよう固定する。
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "jvlink_agent.py"


def _realtime_source() -> str:
    """run_realtime_monitor の本体ソースだけを取り出す。"""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run_realtime_monitor":
            lines = AGENT.read_text(encoding="utf-8").splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError("run_realtime_monitor が見つからない")


def test_realtime_calls_odds_prefetch() -> None:
    """realtime ループから run_odds_prefetch を呼んでいること。"""
    assert "run_odds_prefetch(" in _realtime_source()


def test_prefetch_interval_is_hourly() -> None:
    """間隔は1時間。短くすると JVRTOpen の回数が跳ねるので固定する。"""
    src = AGENT.read_text(encoding="utf-8")
    assert "ODDS_PREFETCH_INTERVAL_SEC = 3600" in src
    assert "ODDS_PREFETCH_INTERVAL_SEC" in _realtime_source()


def test_prefetch_targets_tomorrow() -> None:
    """取りに行くのは翌日ぶん（当日ぶんは realtime 本体が毎ループ取っている）。"""
    body = _realtime_source()
    assert "timedelta(days=1)" in body


def test_prefetch_runs_before_race_keys_guard() -> None:
    """本日レースなしで `continue` する前に置くこと。

    そうしないと「今日は開催なし・明日は開催あり」の日に翌日ぶんが取れない。
    """
    body = _realtime_source()
    prefetch_at = body.index("run_odds_prefetch(")
    guard_at = body.index("本日のJRAレースなし")
    assert prefetch_at < guard_at, "run_odds_prefetch は race_keys 空チェックより前に置くこと"


def test_prefetch_failure_does_not_break_realtime() -> None:
    """翌日ぶんが失敗しても当日の realtime を止めないこと（try/except で包む）。"""
    body = _realtime_source()
    head = body[: body.index("run_odds_prefetch(")]
    assert head.rstrip().endswith("try:"), "run_odds_prefetch は try: の直後に置くこと"
