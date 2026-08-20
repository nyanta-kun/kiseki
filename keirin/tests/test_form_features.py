"""節内成績・結果条件つきローリング・単独先行の特徴量を固定する（2026-08-20 新設）。

🔴 **リークの有無をここで押さえる。** 節内成績は「自分の当日を含めたら」黙って
   良い数字になり、A/B では検出できない（学習・テスト双方が同じように漏れるため）。
"""
import pandas as pd

from src.preprocessing.feature_wt import (
    MEETING_FORM_COLS_WT, FORM_QUALITY_COLS_WT,
    add_meeting_form_features_wt, add_form_quality_features_wt,
)


def _mf_history():
    """cup=C1 の3日間。選手 P1 は 1着→5着→(当日)。P2 は当日が初日。"""
    return pd.DataFrame([
        # race_key, player_id, finish_order, cup_id, day_index, n_entries
        ("R1", "P1", 1, "C1", 1, 7),
        ("R2", "P1", 5, "C1", 2, 7),
        ("R3", "P1", None, "C1", 3, 7),   # 当日・未確定
        ("R3", "P2", None, "C1", 3, 7),   # 当日が初出走
    ], columns=["race_key", "player_id", "finish_order",
                "cup_id", "day_index", "n_entries"])


def test_meeting_form_uses_only_earlier_days():
    """当日の行は自分の集計に入らない（リークしない）。"""
    df = pd.DataFrame([("R3", "P1"), ("R3", "P2")], columns=["race_key", "player_id"])
    out = add_meeting_form_features_wt(df, history=_mf_history())
    p1 = out[out.player_id == "P1"].iloc[0]
    assert p1["cup_n_so_far"] == 2                 # 1着・5着の2走ぶん
    assert p1["cup_top3_rate"] == 0.5              # 1着のみ3着内
    assert p1["cup_win_rate"] == 0.5
    # 着順の頭数正規化: (1-1)/6=0.0 と (5-1)/6≈0.667 の平均
    assert abs(p1["cup_mean_order_n"] - (0.0 + 4 / 6) / 2) < 1e-9


def test_meeting_form_first_day_is_zero_filled_but_countable():
    """初日は率が 0 補完される。cup_n_so_far で「全外」と区別できること。"""
    df = pd.DataFrame([("R3", "P2")], columns=["race_key", "player_id"])
    out = add_meeting_form_features_wt(df, history=_mf_history())
    r = out.iloc[0]
    assert r["cup_n_so_far"] == 0                  # ← これが無いと初日と 0/3 が同じ値になる
    assert r["cup_top3_rate"] == 0.0


def test_meeting_form_does_not_cross_cups():
    """別開催の成績を混ぜない。"""
    h = _mf_history().copy()
    h.loc[h.race_key == "R1", "cup_id"] = "C0"     # 1着を別開催へ移す
    df = pd.DataFrame([("R3", "P1")], columns=["race_key", "player_id"])
    out = add_meeting_form_features_wt(df, history=h)
    assert out.iloc[0]["cup_n_so_far"] == 1        # C1 の5着だけ
    assert out.iloc[0]["cup_top3_rate"] == 0.0


def _fq_history():
    """P1: 2走とも B を取り、片方は4着（沈）・片方は2着（持）。上がりは共にレース内最速。"""
    rows = []
    for rk, day, p1_fin, p1_fh, p1_b in (("R1", "2026-01-01", 4, 11.0, 1),
                                         ("R2", "2026-01-08", 2, 11.0, 1)):
        rows.append((rk, "P1", p1_b, p1_fh, p1_fin, day))
        rows.append((rk, "PX", 0, 12.0, 1, day))    # 相手（上がりは遅い）
    rows.append(("R3", "P1", None, None, None, "2026-01-15"))  # 当日・未確定
    return pd.DataFrame(rows, columns=["race_key", "player_id", "res_back",
                                       "final_half", "finish_order", "race_date"])


def test_form_quality_splits_sink_and_hold():
    """『Bを取って沈む』と『Bを取って持つ』を分けて数える。"""
    df = pd.DataFrame([("R3", "P1", "2026-01-15")],
                      columns=["race_key", "player_id", "race_date"])
    out = add_form_quality_features_wt(df, history=_fq_history())
    r = out.iloc[0]
    assert abs(r["b_sink_rate_90"] - 0.5) < 1e-9   # 2走中1走が B×4着以下
    assert abs(r["b_hold_rate_90"] - 0.5) < 1e-9   # 2走中1走が B×3着内
    # 上がり最速で着外だったのは R1 の1走のみ
    assert abs(r["fh_lost_rate_90"] - 0.5) < 1e-9


def test_form_quality_keeps_unconfirmed_rows_as_merge_keys():
    """未確定行を drop しないこと。

    🔴 `add_sb_dyn_features_wt` は 2026-07-18〜07-28 にこれを落として
       発走前予測が全選手 0 になる事故を起こした。同じ轍を踏まない。
    """
    df = pd.DataFrame([("R3", "P1", "2026-01-15")],
                      columns=["race_key", "player_id", "race_date"])
    out = add_form_quality_features_wt(df, history=_fq_history())
    assert len(out) == 1
    assert out.iloc[0]["b_sink_rate_90"] > 0       # 0.0 補完に落ちていない


def test_form_quality_missing_columns_defaults_to_zero():
    df = pd.DataFrame([("R3",)], columns=["race_key"])
    out = add_form_quality_features_wt(df, history=_fq_history())
    for c in FORM_QUALITY_COLS_WT:
        assert out.iloc[0][c] == 0.0


def test_is_lone_senko_definition():
    """逃げが1人のときだけ、その逃げ選手に立つ。"""
    from src.preprocessing.feature_wt import build_features_wt
    base = dict(race_date="2026-01-01", venue_id="45", grade="A級", race_type="予選",
                distance=400, start_at=None, name="x", player_prefecture="東京",
                player_class="A1", term=100, gear_ratio=3.9, race_point=90.0,
                prediction_mark=0, s_count=0, h_count=0, b_count=0, front_runner=0,
                stalker=0, deep_closer=0, marker=0, first_rate=0.1, second_rate=0.1,
                third_rate=0.1, ex_spurt_pct=0, ex_thrust_pct=0, ex_left_behind_pct=0,
                ex_split_line_pct=0, ex_snatch_pct=0, line_group=1, line_size=1,
                line_pos=1, is_line_leader=1, n_lines=2, finish_order=1,
                bank_length=400, is_indoor=0, venue_prefecture="愛知", player_id="P")
    rows = []
    for i, st in enumerate(["逃", "追", "追"], start=1):        # 逃げ1人
        rows.append({**base, "race_key": "RA", "frame_no": i,
                     "player_id": f"A{i}", "style": st})
    for i, st in enumerate(["逃", "逃", "追"], start=1):        # 逃げ2人
        rows.append({**base, "race_key": "RB", "frame_no": i,
                     "player_id": f"B{i}", "style": st})
    out = build_features_wt(pd.DataFrame(rows))
    ra = out[out.race_key == "RA"]
    rb = out[out.race_key == "RB"]
    assert ra["is_lone_senko"].tolist() == [1, 0, 0]
    assert rb["is_lone_senko"].tolist() == [0, 0, 0]
