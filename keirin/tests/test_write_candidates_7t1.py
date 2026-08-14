"""7T1 の候補時点プレースホルダ（2026-08-15 追加）。

## なぜ要るのか

7T1 は **発走前判定（`notify_prerace_wt`）を持たない唯一のランク**で、
picks_history の行は翌朝08:30の `reconcile_walkforward_tail.sh` が作る。
`tail_windows()` は当日を含めない設計（当日は結果が無く再構築できない）なので、
**7T1 だけ当日中ずっと Web に出ない**状態だった。

買い目は候補時点で確定しており、入稿もその買い目をそのまま送る
（`netkeirin_submit_wt` の `formation_bet_7t1`）ので、候補時点で行を書ける。

🔴 pred_combo は `backfill_7t1_rank_wt.py` と**同一規約**（`三単:` + 着順つきの目）。
   ずれると同じレースが再構築の前後で別表記になり、Web と採点が食い違う。
"""
from __future__ import annotations

import json
import re

import pytest

import scripts.write_candidates_wt as wc

DATE = "2026-08-15"
LEGS = ["1-7-6", "1-7-3"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    picks = tmp_path / "data" / "picks"
    picks.mkdir(parents=True)
    captured: list[tuple] = []

    class _Cur:
        rowcount = 1

    class _Conn:
        def execute(self, sql, params):
            captured.append((sql, params))
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(wc, "__file__", str(tmp_path / "scripts" / "write_candidates_wt.py"))
    monkeypatch.setattr(wc, "get_connection", lambda: _Conn())
    monkeypatch.delenv("KEIRIN_DB_URL", raising=False)   # VPS ミラーは走らせない
    return picks, captured


def _write(picks, legs, race_key="20260815_26_06"):
    (picks / f"wave_picks_wt_{DATE}_s7t1_candidates.json").write_text(
        json.dumps([{"race_key": race_key, "axis1": 1, "axis2": 7, "legs": legs,
                     "bet_amount": 10000}], ensure_ascii=False), encoding="utf-8")


def _rows(captured):
    return [p for _, p in captured if len(p) > 2 and p[2] == "RANK_7T1"]


def test_7t1_の暫定行が朝に書かれる(env):
    picks, captured = env
    _write(picks, LEGS)
    wc._write_paper_candidates(DATE)

    rows = _rows(captured)
    assert len(rows) == 1, "7T1 の行が書かれていない（当日 Web に出ない）"
    target_date, store_key, rank, pred, n_combos, _gate = rows[0]
    assert target_date == DATE
    assert store_key == "20260815_26_06#7T1"
    assert n_combos == len(LEGS)
    # 🔴 再構築（backfill_7t1_rank_wt.py）と同一規約。ずれると同じレースが
    #    再構築の前後で別表記になる。
    assert pred == "三単:" + ",".join(LEGS)


def test_bet_amount_は0で書かれる(env):
    """他ランクと同じ規約。7T1 は発走前判定が無いので当日中は0のままで、
    翌朝の再構築が実際の投資額・的中で上書きする。"""
    picks, captured = env
    _write(picks, LEGS)
    wc._write_paper_candidates(DATE)

    sql = next(s for s, p in captured if len(p) > 2 and p[2] == "RANK_7T1")
    m = re.search(r"picks_history\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", sql, re.S)
    assert m, f"INSERT 文を解析できない: {sql}"
    cols = [c.strip() for c in m.group(1).split(",")]
    vals = [v.strip() for v in m.group(2).split(",")]
    assert vals[cols.index("bet_amount")] == "0"


def test_買い目が空の候補は書かない(env):
    picks, captured = env
    _write(picks, [])
    wc._write_paper_candidates(DATE)
    assert not _rows(captured)


def test_pred_combo_の規約が再構築と一致している():
    """🔴 表記規約を両方が同じ形で持っていること（文字列として突き合わせる）。

    ⚠️ `backfill_7t1_rank_wt.py` 側を書き換えたらここが落ちる。落ちたら
       **両方を揃える**こと——片方だけ直すと Web と採点が静かに食い違う。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "scripts"
           / "backfill_7t1_rank_wt.py").read_text(encoding="utf-8")
    assert '"pred_combo": "三単:" + ",".join(legs)' in src, (
        "再構築側の pred_combo 表記が変わった。write_candidates_wt.py の"
        " 7T1 ブロックも同じ形へ揃えること")
