"""`scripts/jra_viewpoint_transfer.py` の観点の列が、対象レースより前の走だけから作られることを固定する。"""

import numpy as np
import pandas as pd

from scripts.jra_viewpoint_transfer import build_viewpoint_features


def _run(date, race_id, horse_id, jockey, blinker, course, surface, dist, fin, ab=0):
    return {
        "date": date,
        "race_id": race_id,
        "horse_id": horse_id,
        "jockey_id": jockey,
        "blinker": blinker,
        "course_name": course,
        "surface": surface,
        "distance": dist,
        "finish_position": fin,
        "abnormality_code": ab,
    }


def _runs():
    return pd.DataFrame(
        [
            # 馬 1: 東京芝1600 で 2 着 → 中山ダ1800（初ダート・初コース・乗り替わり・ブリンカー着用）
            _run("20260101", 10, 1, 100, False, "東京", "芝", 1600, 2),
            _run("20260201", 20, 1, 200, True, "中山", "ダ", 1800, 1),
            # 馬 1 の 3 走目: 東京芝1600（好走条件一致 1）
            _run("20260301", 30, 1, 200, True, "東京", "芝", 1600, 5),
            # 前走（レース 10）の相手 2〜5
            _run("20260101", 10, 2, 101, False, "東京", "芝", 1600, 1),
            _run("20260101", 10, 3, 102, False, "東京", "芝", 1600, 3),
            _run("20260101", 10, 4, 103, False, "東京", "芝", 1600, 4),
            _run("20260101", 10, 5, 104, False, "東京", "芝", 1600, 5),
            # 相手がその後に走る: 2 は 3着内・3 は 着外・4 は 3着内・5 は今回と同日（数えない）
            _run("20260115", 40, 2, 101, False, "中山", "芝", 1600, 2),
            _run("20260115", 41, 3, 102, False, "中山", "芝", 1600, 8),
            _run("20260120", 42, 4, 103, False, "中山", "芝", 1600, 1),
            _run("20260201", 43, 5, 104, False, "中山", "芝", 1600, 1),
        ]
    )


def test_change_flags_use_previous_run_only():
    runs = _runs()
    f = build_viewpoint_features(runs, pd.DataFrame({"race_id": [10, 20, 30], "horse_id": [1, 1, 1]}))
    first, second, third = (f.iloc[i] for i in range(3))
    assert np.isnan(first["jockey_change"]) and np.isnan(first["first_course"])  # 前走なし
    assert second["jockey_change"] == 1 and second["blinker_change"] == 1
    assert second["surface_switch"] == 1 and second["first_dirt"] == 1 and second["first_course"] == 1
    assert third["jockey_change"] == 0 and third["first_course"] == 0 and third["first_dirt"] == 0


def test_good_condition_match_counts_prior_top3_only():
    f = build_viewpoint_features(_runs(), pd.DataFrame({"race_id": [20, 30], "horse_id": [1, 1]}))
    assert f.iloc[0]["good_cond_match"] == 0  # 中山ダは過去に無い
    assert f.iloc[1]["good_cond_match"] == 1  # 東京芝1600 の 2着が1回（今回の5着は数えない）


def test_last_race_level_excludes_same_day_and_needs_three():
    f = build_viewpoint_features(_runs(), pd.DataFrame({"race_id": [20], "horse_id": [1]}))
    # 前走の翌日〜今回の前日に走った相手は 2・3・4 の3頭（5 は今回と同日なので除く）。3着内は 2 と 4
    assert abs(f.iloc[0]["last_race_level"] - 2 / 3) < 1e-9


def test_scratched_run_is_not_a_previous_run():
    runs = _runs()
    runs = pd.concat([runs, pd.DataFrame([_run("20260215", 25, 1, 300, False, "京都", "芝", 2000, None, ab=1)])])
    f = build_viewpoint_features(runs, pd.DataFrame({"race_id": [30], "horse_id": [1]}))
    # 取消のレース 25 は前走にならない → 前走はレース 20（騎手 200）のまま
    assert f.iloc[0]["jockey_change"] == 0
