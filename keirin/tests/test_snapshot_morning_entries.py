"""snapshot_morning_entries_wt.py のテスト（朝の出走表の退避と最終値との比較）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from snapshot_morning_entries_wt import (  # noqa: E402
    SNAP_COLS,
    compare,
    read_snapshot,
    write_snapshot,
)


def _row(race_key: str, frame_no: int, **kw) -> dict:
    base = {c: None for c in SNAP_COLS}
    base.update({"race_key": race_key, "frame_no": frame_no, "prediction_mark": 1,
                 "line_group": 1, "line_size": 2, "line_pos": 1, "is_line_leader": 1,
                 "n_lines": 3, "race_point": 100.5})
    base.update(kw)
    return base


def test_write_keeps_first_value(tmp_path):
    """既にファイルがあれば上書きしない（朝の値を保持する）。"""
    p = tmp_path / "2026-09-30.csv.gz"
    assert write_snapshot([_row("r1", 1)], p, "2026-09-30T07:05:00") == 1
    assert write_snapshot([_row("r1", 1, prediction_mark=5)], p, "2026-09-30T13:05:00") == 0
    rows = read_snapshot(p)
    assert rows[0]["prediction_mark"] == "1"
    assert rows[0]["captured_at"] == "2026-09-30T07:05:00"


def test_compare_roundtrip_no_change(tmp_path):
    """CSV を経由しても型の違い（int/float/None）で偽の変化を数えない。"""
    p = tmp_path / "d.csv.gz"
    final = [_row("r1", 1), _row("r1", 2, prediction_mark=None, race_point=99.0)]
    write_snapshot(final, p, "t")
    res = compare(read_snapshot(p), final)
    assert res["n_rows"] == 2
    assert res["col_changed"] == {}
    assert res["col_filled"] == {}
    assert res["races_mark_changed"] == 0


def test_compare_counts_mark_line_and_fill(tmp_path):
    """印の変化・並びの変化・朝は空で後から埋まった値を分けて数える。"""
    p = tmp_path / "d.csv.gz"
    morning = [
        _row("r1", 1, prediction_mark=1),
        _row("r2", 1, n_lines=None, line_group=None),
        _row("r3", 1),
    ]
    write_snapshot(morning, p, "t")
    final = [
        _row("r1", 1, prediction_mark=2),
        _row("r2", 1, n_lines=3, line_group=1),
        _row("r3", 1, line_pos=2),
    ]
    res = compare(read_snapshot(p), final)
    assert res["n_races"] == 3
    assert res["races_mark_changed"] == 1
    assert res["races_line_changed"] == 2          # r2（埋まった）と r3（変わった）
    assert res["races_missing_line_morning"] == 1  # r2
    assert res["col_changed"]["prediction_mark"] == 1
    assert res["col_changed"]["line_pos"] == 1
    assert res["col_filled"]["n_lines"] == 1


def test_snapshot_excludes_results_and_own_predictions():
    """結果列と自社の予測値は退避しない（発走前の入力だけを残す）。"""
    for c in ("finish_order", "factor", "res_standing", "res_back", "final_half",
              "pred_win_pct", "pred_top3_pct", "pred_top2_pct"):
        assert c not in SNAP_COLS
