"""`src/indices/lap_form.py` — ラップ由来の過去走特徴を固定する。

守っているもの:

1. **lap_times の読み方**（`test_parse_*` / `test_summarize_*`）。本番 DB の実例
   （2026-09-06 札幌9R・1700m）で `Σラップ`・上がり3F・形状を突き合わせる
2. 🔴 **point-in-time**（`test_pit_*`）。対象日と同日・以降の走を足しても特徴が
   1つも変わらないこと。par も走の日付より前だけで作ること。
   同日を含める実装は例外を出さず、ただ静かに未来を読む
3. **欠損の意味論**（`test_missing_*`）。`None` のまま返し、行では NaN になること

DB は使わない。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from src.indices.lap_form import (  # noqa: E402
    LAP_FORM_FEATURE_NAMES,
    PAR_MIN_COUNT,
    LapRun,
    LapRunStore,
    ParTable,
    RaceLapSummary,
    compute_lap_form_features,
    lap_form_feature_row,
    parse_lap_times,
    run_lap_metrics,
    summarize_race_laps,
)

# 2026-09-06 札幌9R ダ1700m の本番値（last_3f_race = 37.2, first_3f = 29.6）
SAPPORO_1700 = "069108119121126125124125123" + "0" * 48


# ---------------------------------------------------------------------------
# lap_times
# ---------------------------------------------------------------------------


def test_parse_strips_zero_padding() -> None:
    laps = parse_lap_times(SAPPORO_1700)
    assert laps == [6.9, 10.8, 11.9, 12.1, 12.6, 12.5, 12.4, 12.5, 12.3]


@pytest.mark.parametrize("raw", [None, "", "0" * 75, "12a115"])
def test_parse_rejects_empty_or_broken(raw: str | None) -> None:
    assert parse_lap_times(raw) is None


def test_summarize_matches_production_race() -> None:
    s = summarize_race_laps(parse_lap_times(SAPPORO_1700), 1700)
    assert s is not None
    assert s.total == pytest.approx(104.0)
    assert s.last3 == pytest.approx(37.2)          # = races.last_3f_race
    assert s.front == pytest.approx(66.8)
    assert sum(parse_lap_times(SAPPORO_1700)[:3]) == pytest.approx(29.6)   # = races.first_3f
    # mean(12.4, 12.5, 12.3) − mean(12.6, 12.5)
    assert s.shape == pytest.approx(12.4 - 12.55)


def test_summarize_rejects_segment_count_mismatch() -> None:
    laps = parse_lap_times(SAPPORO_1700)             # 9 区間（1700m・1800m と一致）
    assert summarize_race_laps(laps, 1600) is None
    assert summarize_race_laps(laps, 2000) is None
    assert summarize_race_laps(laps, None) is None


# ---------------------------------------------------------------------------
# par
# ---------------------------------------------------------------------------


def _par_rows(n: int, *, date_prefix: str = "202401", front: float = 60.0,
              last3: float = 36.0, condition: str | None = "良") -> list[tuple]:
    return [(f"{date_prefix}{i + 1:02d}", "05", "芝", 1600, condition, front, last3)
            for i in range(n)]


def test_pit_par_excludes_same_date_and_later() -> None:
    rows = _par_rows(PAR_MIN_COUNT)                      # 20240101..20240110
    rows.append(("20240111", "05", "芝", 1600, "良", 999.0, 999.0))   # 対象日
    rows.append(("20240112", "05", "芝", 1600, "良", 999.0, 999.0))   # 以降
    t = ParTable(rows)
    assert t.par("05", "芝", 1600, "良", "20240111") == pytest.approx((60.0, 36.0))


def test_par_needs_min_count() -> None:
    t = ParTable(_par_rows(PAR_MIN_COUNT - 1))
    assert t.par("05", "芝", 1600, "良", "20250101") is None


def test_par_falls_back_to_no_condition() -> None:
    rows = _par_rows(PAR_MIN_COUNT, condition="良") + [
        ("20240120", "05", "芝", 1600, "重", 70.0, 40.0)]
    t = ParTable(rows)
    # 重は1件しかないので馬場状態なしの par（11件平均）へ落ちる
    got = t.par("05", "芝", 1600, "重", "20250101")
    assert got is not None
    assert got[0] == pytest.approx((60.0 * PAR_MIN_COUNT + 70.0) / (PAR_MIN_COUNT + 1))


# ---------------------------------------------------------------------------
# 1走の指標・馬の特徴
# ---------------------------------------------------------------------------


_RACE = RaceLapSummary(total=96.0, last3=35.0, front=61.0, shape=0.2)


def _run(date: str, *, race_id: int = 1, finish: float = 96.5, last3: float = 34.5,
         distance: int = 1600) -> LapRun:
    return LapRun(date=date, race_id=race_id, course="05", surface="芝", distance=distance,
                  condition="良", finish_time=finish, last_3f=last3, race=_RACE)


def _table() -> ParTable:
    return ParTable(_par_rows(PAR_MIN_COUNT, date_prefix="202301"))  # par = (60.0, 36.0)


def test_run_metrics_values() -> None:
    m = run_lap_metrics(_run("20240301"), _table())
    assert m is not None
    n_front = (1600 - 600) / 200
    assert m["front_par"] == pytest.approx((96.5 - 34.5 - 60.0) / n_front)
    assert m["l3_par"] == pytest.approx(34.5 - 36.0)
    assert m["race_front_par"] == pytest.approx((61.0 - 60.0) / n_front)
    assert m["race_shape"] == pytest.approx(0.2)


def test_run_metrics_rejects_abnormal_last3f() -> None:
    assert run_lap_metrics(_run("20240301", last3=0.0), _table()) is None


def test_features_use_up_to_three_newest_runs() -> None:
    runs = [_run("20240501", last3=33.0), _run("20240401", last3=34.0),
            _run("20240301", last3=35.0), _run("20240201", last3=30.0)]   # 4走目は使わない
    f = compute_lap_form_features(runs, _table(), "20240601")
    assert f["lap_l3_par_l1"] == pytest.approx(33.0 - 36.0)
    assert f["lap_l3_par_m3"] == pytest.approx((33.0 + 34.0 + 35.0) / 3 - 36.0)
    assert f["lap_l3_par_best3"] == pytest.approx(33.0 - 36.0)


def test_pit_same_day_and_future_runs_do_not_change_features() -> None:
    base = [_run("20240501", last3=33.0), _run("20240401", last3=34.0)]
    polluted = [_run("20240701", last3=31.0), _run("20240601", last3=31.0, race_id=9)] + base
    t = _table()
    assert compute_lap_form_features(polluted, t, "20240601") == \
        compute_lap_form_features(base, t, "20240601")


def test_pit_store_before_is_strict() -> None:
    store = LapRunStore([(7, _run("20240601", race_id=2)), (7, _run("20240501", race_id=1)),
                         (7, _run("20240701", race_id=3))])
    assert [r.race_id for r in store.before(7, "20240601")] == [1]
    assert [r.race_id for r in store.before(7, "20240602")] == [2, 1]
    assert store.before(99, "20240601") == []


def test_missing_when_no_usable_runs() -> None:
    f = compute_lap_form_features([_run("20240501", last3=99.0)], _table(), "20240601")
    assert all(v is None for v in f.values())
    row = lap_form_feature_row(f)
    assert len(row) == len(LAP_FORM_FEATURE_NAMES)
    assert all(math.isnan(v) for v in row)
