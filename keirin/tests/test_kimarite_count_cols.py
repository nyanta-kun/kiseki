"""決まり手の回数列の対応（`KIMARITE_COUNT_COLS`）を固定する（2026-09-30）。

`wt_entries.stalker` は差し、`deep_closer` は捲り。列名（winticket の英語名）から読むと
取り違えるので、対応を1か所に置いて固定する。
"""
from src.preprocessing.feature_wt import FEATURE_COLS_WT, KIMARITE_COUNT_COLS


def test_mapping_is_the_measured_one():
    assert KIMARITE_COUNT_COLS == {
        "逃げ": "front_runner",
        "捲り": "deep_closer",
        "差し": "stalker",
        "マーク": "marker",
    }


def test_raw_counts_are_not_model_inputs_yet():
    """現行モデルは回数列を直接使っていない（使い始めるときはこのテストを直し、再学習する）。"""
    assert not set(KIMARITE_COUNT_COLS.values()) & set(FEATURE_COLS_WT)
