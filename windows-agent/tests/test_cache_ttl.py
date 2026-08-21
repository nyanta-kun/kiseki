"""JVRead 生データキャッシュの TTL テスト。

キャッシュキーは (dataspec, from_time, option) だけで時刻成分を持たない。
daily の from_time は「前日 00:00」＝その日いっぱい不変なので、TTL が無いと
**その日最初の取得結果を一日中使い回す**。JRA は土曜分の枠順確定を金曜 11:30 頃に
公開するため、それより前に一度でも取得していると、その日はもう確定出馬表を
取り込めない（2026-08-21 に実際に発生し、翌日の指数が DM 欠損のまま算出された）。

    python3 -m pytest windows-agent/tests/test_cache_ttl.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from link_common import load_cache, save_cache  # noqa: E402

RECORDS = [{"rec_id": "SE", "data": "SE1..."}]


def _save(tmp_path: Path) -> Path:
    save_cache("RACE", "20260820000000", 2, RECORDS, tmp_path)
    return tmp_path / "RACE_20260820000000_2.jsonl"


def test_fresh_cache_is_used(tmp_path: Path) -> None:
    _save(tmp_path)
    assert load_cache("RACE", "20260820000000", 2, tmp_path, max_age_sec=1800) == RECORDS


def test_stale_cache_is_ignored(tmp_path: Path) -> None:
    """TTL を超えたキャッシュは None を返し、呼び出し側に再取得させる。"""
    path = _save(tmp_path)
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert load_cache("RACE", "20260820000000", 2, tmp_path, max_age_sec=1800) is None


def test_no_ttl_keeps_previous_behaviour(tmp_path: Path) -> None:
    """max_age_sec 未指定は従来どおり無期限（setup 等の重い取得を守る）。"""
    path = _save(tmp_path)
    old = time.time() - 86400 * 7
    os.utime(path, (old, old))
    assert load_cache("RACE", "20260820000000", 2, tmp_path) == RECORDS


def test_missing_cache_returns_none(tmp_path: Path) -> None:
    assert load_cache("RACE", "20260820000000", 2, tmp_path, max_age_sec=1800) is None
