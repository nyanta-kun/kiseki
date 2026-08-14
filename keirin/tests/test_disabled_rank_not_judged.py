"""入稿 OFF のランクはライブ判定も走らせない（2026-08-14・実害あり）。

## 背景（本番で起きたこと）

`netkeirin_settings.enabled` は**入稿だけ**を止める設計で、OFF のランクでも
発走前判定が走り picks_history へ bet>0 の行が入り続けていた。

    2026-08-14 松山1R = **9C（看板の穴埋め）として入稿**したのに、
                        推奨の記録は **9H1**（入稿OFF）

同日だけで「売っていない商品の推奨」が26件・投資25.4万円ぶん記録され、
サマリーの母集団が実態とずれていた（さらに実際に売った穴埋め分は未記録）。

⚠️ **fail-open**。読めなければ全ランク走らせる（判定が全部止まる方が重い）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.notify_prerace_wt as np  # noqa: E402

#: プロセッサ名 → 見るべき内部rank名
GATED = {
    "_process_rank_7s_candidates": "RANK_7S",
    "_process_rank_7a_candidates": "RANK_7S",
    "_process_rank_7ss_candidates": "RANK_7S",
    "_process_rank_9c_candidates": "RANK_9C",
    "_process_rank_7h1_candidates": "RANK_7H1",
    "_process_rank_7h2_candidates": "RANK_7H2",
    "_process_rank_9h1_candidates": "RANK_9H1",
    "_process_rank_7b_candidates": "RANK_7B",
    "_process_rank_7c_candidates": "RANK_7C",
}


@pytest.mark.parametrize(("fn", "rank"), sorted(GATED.items()))
def test_disabled_rank_returns_immediately(monkeypatch, fn, rank):
    """🔴 OFF のランクは**候補を読む前に**空で返ること。"""
    monkeypatch.setattr(np, "_DISABLED_RANKS", {rank})
    # 候補読み込みが呼ばれたら失敗（＝ゲートより先へ進んでいる）
    for loader in ("_load_rank_7c_candidates", "_load_rank_9c_candidates"):
        if hasattr(np, loader):
            monkeypatch.setattr(np, loader, lambda *a, **k: pytest.fail("ゲートを素通りした"))
    assert getattr(np, fn)("2026-08-14", 0, set()) == ([], set())


def test_enabled_rank_is_not_gated(monkeypatch):
    monkeypatch.setattr(np, "_DISABLED_RANKS", {"RANK_9H1"})
    assert np._rank_enabled("RANK_9C") is True
    assert np._rank_enabled("RANK_9H1") is False


def test_fails_open_when_settings_are_unreadable(monkeypatch):
    """🔴 読めなければ止めない（判定が全部消える方が重い）。"""
    monkeypatch.setattr(np, "_DISABLED_RANKS", None)
    monkeypatch.setattr(np, "disabled_rank_names", lambda: set())
    assert np._rank_enabled("RANK_9H1") is True


def test_lookup_is_cached(monkeypatch):
    """⚠️ 毎分呼ばれるので DB は1回だけ引くこと。"""
    monkeypatch.setattr(np, "_DISABLED_RANKS", None)
    calls = []
    monkeypatch.setattr(np, "disabled_rank_names",
                        lambda: calls.append(1) or {"RANK_7B"})
    np._rank_enabled("RANK_7S")
    np._rank_enabled("RANK_7C")
    assert len(calls) == 1
