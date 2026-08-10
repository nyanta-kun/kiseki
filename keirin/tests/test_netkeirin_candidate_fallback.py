"""波ごとの候補ファイルのフォールバック（2026-08-10 の実バグに対する回帰テスト）。

## 何が起きたか

夜の再生成（`evening_picks_wt.sh`）は**その波の開催だけ**を作り直すため、
その波に該当が無いランクは `[]`（2バイト）を書き出す。
`_load_candidates` は「ファイルが**存在**するか」だけで選んでいたので、
この空ファイルが**朝の候補を無言で隠していた**。

2026-08-10 実測:
  wave_picks_wt_2026-08-10_s7a_candidates.json        956バイト（1件）
  wave_picks_wt_2026-08-10_night_s7a_candidates.json    2バイト（0件）← これが勝っていた

夕方の実行は 7A・7SS についてログを1行も出さずに終了しており（他ランクは
「発走済み◯件を除外」等が出る）、**無言なのが唯一の手がかり**だった。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import netkeirin_submit_wt as ns  # noqa: E402

DATE = "2026-08-10"


@pytest.fixture()
def picks_dir(tmp_path, monkeypatch):
    """`_load_candidates` が見る data/picks を差し替える。"""
    d = tmp_path / "data" / "picks"
    d.mkdir(parents=True)
    monkeypatch.setattr(ns, "__file__", str(tmp_path / "scripts" / "x.py"))
    return d


def _write(d: Path, name: str, rows: list) -> None:
    (d / name).write_text(json.dumps(rows), encoding="utf-8")


def test_empty_wave_file_falls_back_to_morning(picks_dir, capsys):
    """🔴 空の _night は朝の候補を隠してはいけない（本バグの本体）。"""
    _write(picks_dir, f"wave_picks_wt_{DATE}_night_s7a_candidates.json", [])
    _write(picks_dir, f"wave_picks_wt_{DATE}_s7a_candidates.json", [{"race_key": "A"}])
    got = ns._load_candidates(DATE, "evening", "s7a")
    assert [r["race_key"] for r in got] == ["A"]
    # 空だったことをログに残す（無言だと「なぜ入稿ゼロか」を追えない）
    assert "は0件" in capsys.readouterr().out


def test_nonempty_wave_file_wins(picks_dir):
    """夜の再生成に中身があれば、そちらが朝より優先される（従来どおり）。"""
    _write(picks_dir, f"wave_picks_wt_{DATE}_night_s7a_candidates.json",
           [{"race_key": "NIGHT"}])
    _write(picks_dir, f"wave_picks_wt_{DATE}_s7a_candidates.json",
           [{"race_key": "MORNING"}])
    got = ns._load_candidates(DATE, "evening", "s7a")
    assert [r["race_key"] for r in got] == ["NIGHT"]


def test_missing_wave_file_falls_back(picks_dir):
    """夜のファイルが無い日も朝へ落ちる（従来の動作）。"""
    _write(picks_dir, f"wave_picks_wt_{DATE}_s7a_candidates.json", [{"race_key": "M"}])
    assert [r["race_key"] for r in ns._load_candidates(DATE, "evening", "s7a")] == ["M"]


def test_both_empty_returns_empty(picks_dir):
    """両方空なら空。ここで例外を投げると後続ランクごと止まる。"""
    _write(picks_dir, f"wave_picks_wt_{DATE}_night_s7a_candidates.json", [])
    _write(picks_dir, f"wave_picks_wt_{DATE}_s7a_candidates.json", [])
    assert ns._load_candidates(DATE, "evening", "s7a") == []


def test_broken_json_falls_through(picks_dir, capsys):
    """壊れたJSONは読み飛ばして次の候補へ。ここで落とすと入稿が丸ごと止まる。"""
    (picks_dir / f"wave_picks_wt_{DATE}_night_s7a_candidates.json").write_text(
        "{ broken", encoding="utf-8")
    _write(picks_dir, f"wave_picks_wt_{DATE}_s7a_candidates.json", [{"race_key": "M"}])
    got = ns._load_candidates(DATE, "evening", "s7a")
    assert [r["race_key"] for r in got] == ["M"]
    assert "読み込み失敗" in capsys.readouterr().out


def test_morning_session_ignores_wave_files(picks_dir):
    """朝の回は _night を見ない（波の接頭辞は evening/noon のみ）。"""
    _write(picks_dir, f"wave_picks_wt_{DATE}_night_s7a_candidates.json", [{"race_key": "N"}])
    _write(picks_dir, f"wave_picks_wt_{DATE}_s7a_candidates.json", [{"race_key": "M"}])
    assert [r["race_key"] for r in ns._load_candidates(DATE, "morning", "s7a")] == ["M"]
