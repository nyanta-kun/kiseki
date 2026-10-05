"""決勝の軸信頼ゲート免除 A/B の割り付けを固定する（2026-10-05 事前登録）。

正本: `docs/type_lab/prereg_final_gate_ab_2026_10_05.md`
  - 対象: `race_type` が「決勝」「チャレンジ決勝」に**完全一致**（部分一致は準決勝を拾う）
  - 期間: 2026-10-12（月）〜 2026-12-06（日）の8週。月曜始まり、第1・3・5・7週が ON
  - 期間外は OFF

🔴 ここが崩れると A/B の割り付けが静かに変わり、8週かけた前向き検証が無効になる。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from scripts import netkeirin_submit_type_lab as m
from scripts.netkeirin_submit_type_lab import final_gate_exempt


def test_preregistered_constants_are_frozen():
    assert m.FINAL_GATE_AB_START == date(2026, 10, 12)
    assert m.FINAL_GATE_AB_START.weekday() == 0, "第1週は月曜始まり"
    assert m.FINAL_GATE_AB_END == date(2026, 12, 6)
    assert m.FINAL_GATE_AB_END.weekday() == 6, "第8週は日曜で終わる"
    assert (m.FINAL_GATE_AB_END - m.FINAL_GATE_AB_START).days + 1 == 8 * 7
    assert m.FINAL_GATE_AB_RACE_TYPES == frozenset({"決勝", "チャレンジ決勝"})


@pytest.mark.parametrize("race_type", ["決勝", "チャレンジ決勝"])
@pytest.mark.parametrize("first,last,expected", [
    ("2026-10-12", "2026-10-18", True),     # 第1週
    ("2026-10-19", "2026-10-25", False),    # 第2週
    ("2026-10-26", "2026-11-01", True),     # 第3週
    ("2026-11-02", "2026-11-08", False),    # 第4週
    ("2026-11-09", "2026-11-15", True),     # 第5週
    ("2026-11-16", "2026-11-22", False),    # 第6週
    ("2026-11-23", "2026-11-29", True),     # 第7週
    ("2026-11-30", "2026-12-06", False),    # 第8週
])
def test_week_assignment(race_type, first, last, expected):
    assert final_gate_exempt(race_type, first) is expected
    assert final_gate_exempt(race_type, last) is expected      # 週の端も同じ割り付け


def test_outside_period_is_off():
    assert final_gate_exempt("決勝", "2026-10-11") is False
    assert final_gate_exempt("決勝", "2026-12-07") is False
    assert final_gate_exempt("決勝", "2026-10-05") is False
    assert final_gate_exempt("決勝", "2027-10-12") is False


@pytest.mark.parametrize("race_type", [
    "準決勝", "準決勝A", "チャレンジ準決勝", "ガールズ決勝", "決勝戦", "特選", "予選", "",
    None,
])
def test_only_exact_final_types(race_type):
    """部分一致で準決勝などを拾わない（ON 週でも False）。"""
    assert final_gate_exempt(race_type, "2026-10-12") is False


def test_accepts_str_date_and_datetime_and_strips_race_type():
    assert final_gate_exempt(" 決勝 ", "2026-10-12") is True
    assert final_gate_exempt("決勝", date(2026, 10, 12)) is True
    assert final_gate_exempt("決勝", datetime(2026, 10, 12, 15, 0)) is True
    assert final_gate_exempt("決勝", date(2026, 10, 19)) is False


@pytest.mark.parametrize("bad", [None, "", "abc", "20261012x"])
def test_unreadable_date_is_off(bad):
    assert final_gate_exempt("決勝", bad) is False


def _row(race_type: str, day: str) -> dict:
    return {
        "race_key": f"{day.replace('-', '')}_13_05", "race_date": day, "venue_name": "松阪",
        "race_no": 5, "race_type": race_type, "n_entries": 7, "cup_grade": None,
        "type_label": "A", "axis_sum": 1.0, "axis1": 1, "axis2": 4,
        "p3_order": "1-4-5-6-7-2-3", "plan_key": "A_hit", "bet_type": "trifecta",
        "n_legs": 3, "budget": 10_000, "pred_mean_payout": 30_000.0,
        "pred_min_payout": 25_000.0,
        "legs": [{"combo": "1-4-5", "stake": 6400, "pred_odds": 2.6},
                 {"combo": "1-4-7", "stake": 2100, "pred_odds": 9.9},
                 {"combo": "1-4-6", "stake": 1500, "pred_odds": 16.2}],
    }


def _run_dry(monkeypatch, capsys, row: dict) -> tuple[str, list[tuple]]:
    """`run` を dry-run で1行だけ通し、(標準出力, 記録された見送り) を返す。"""
    skips: list[tuple] = []
    monkeypatch.setattr(m, "_load_settings", lambda: {})
    monkeypatch.setattr(m, "_approval_required", lambda: False)
    monkeypatch.setattr(m, "axis_gate_enabled", lambda: True)
    monkeypatch.setattr(m, "_load_closed_races", lambda day: set())
    monkeypatch.setattr(m, "_already_submitted", lambda keys: set())
    monkeypatch.setattr(m, "_missing_market_inputs", lambda rk: None)
    monkeypatch.setattr(m, "_build_entry_table", lambda rk, marks: None)
    monkeypatch.setattr(m, "_race_point_sd", lambda keys: {})
    monkeypatch.setattr(m, "_load_rows", lambda day: [row])
    monkeypatch.setattr(m, "_load_highpay_rows", lambda day: {})
    monkeypatch.setattr(m, "_make_skip",
                        lambda dry: (lambda *a, **k: skips.append(a)))
    monkeypatch.setattr(m, "send", lambda *a, **k: None)
    m.run(row["race_date"], "noon", dry_run=True, only_key=None, do_rebuild=False)
    return capsys.readouterr().out, skips


def test_reject_exempts_final_in_on_week_only(monkeypatch, capsys):
    """ON 週の決勝はゲート下回りでも `axis_gate` で落ちず、免除ログが出る。"""
    out, skips = _run_dry(monkeypatch, capsys, _row("決勝", "2026-10-13"))
    assert "決勝の軸信頼ゲート免除（A/B ON 週）" in out
    assert not [s for s in skips if "axis_gate" in s], "免除したのに axis_gate で記録された"


def test_reject_keeps_gate_in_off_week(monkeypatch, capsys):
    """OFF 週は従来どおり `axis_gate` で落ちる。"""
    out, skips = _run_dry(monkeypatch, capsys, _row("決勝", "2026-10-20"))
    assert "ゲート免除" not in out
    assert [s for s in skips if "axis_gate" in s], "OFF 週の決勝が axis_gate で落ちていない"


def test_reject_keeps_gate_for_semifinal_in_on_week(monkeypatch, capsys):
    """ON 週でも準決勝は免除しない。"""
    out, skips = _run_dry(monkeypatch, capsys, _row("準決勝", "2026-10-13"))
    assert "ゲート免除" not in out
    assert [s for s in skips if "axis_gate" in s]
