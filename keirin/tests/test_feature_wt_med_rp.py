"""`race_point` の欠損補完は学習と配信で同じ値を使う（2026-09-20 監査 item7）。

旧実装は `df["race_point"].median()` を**渡された df の中だけ**で計算していた。

  - 学習は数年分（〜74万行）で `build_features_wt` を呼ぶので中央値は安定する
  - 配信は `predict_p3_pw` → `load_raw_data_wt(min_date=day, max_date=day)` で
    **その日1日ぶん**（実測 400〜650行）だけを渡す

つまり**同じ行でも呼び出しの母集団によって埋まる値が変わる** train/serve skew。
実測（2026-09-20）: 全期間 737,145行の中央値 85.55 に対し、単日は 82.4〜91.44。

影響するのは `race_point == 0.0`（＝欠損扱い）の 2,501/739,646行（0.34%）だけで
規模は小さいが、CLAUDE.md が掲げる「学習と配信は同じ関数・同じ値を通す」に反する。
固定値 `MED_RACE_POINT_FILL` へ揃えることで**片側（配信）だけが訓練時の実際の値へ
寄る**ので、モデルの再学習は要らない。
"""
from __future__ import annotations

import pandas as pd

from src.preprocessing.feature_wt import MED_RACE_POINT_FILL, build_features_wt

_BASE = dict(race_date="2026-01-01", venue_id="45", grade="A級", race_type="予選",
             distance=400, start_at=None, name="x", player_prefecture="東京",
             player_class="A1", term=100, gear_ratio=3.9, prediction_mark=0,
             s_count=0, h_count=0, b_count=0, front_runner=0, stalker=0,
             deep_closer=0, marker=0, first_rate=0.1, second_rate=0.1,
             third_rate=0.1, ex_spurt_pct=0, ex_thrust_pct=0, ex_left_behind_pct=0,
             ex_split_line_pct=0, ex_snatch_pct=0, line_group=1, line_size=1,
             line_pos=1, is_line_leader=1, n_lines=2, finish_order=1,
             bank_length=400, is_indoor=0, venue_prefecture="愛知", style="逃")


def _race(key: str, points: list[float]) -> list[dict]:
    return [{**_BASE, "race_key": key, "frame_no": i, "player_id": f"{key}{i}",
             "race_point": p} for i, p in enumerate(points, start=1)]


def test_欠損は固定の中央値で埋まる():
    out = build_features_wt(pd.DataFrame(_race("RA", [0.0, 50.0, 100.0])))
    assert out.loc[out.frame_no == 1, "race_point"].iloc[0] == MED_RACE_POINT_FILL


def test_母集団の大きさで埋める値が変わらない():
    """🔴 これが旧実装との違い。小さい df と大きい df で同じ値になること。"""
    small = build_features_wt(pd.DataFrame(_race("RA", [0.0, 50.0, 60.0])))
    big_rows: list[dict] = []
    for n in range(40):                       # 中央値が 95 付近へ寄る大きい母集団
        big_rows += _race(f"R{n}", [0.0, 95.0, 96.0, 97.0])
    big = build_features_wt(pd.DataFrame(big_rows))
    assert (small.loc[small.frame_no == 1, "race_point"].iloc[0]
            == big.loc[(big.race_key == "R0") & (big.frame_no == 1), "race_point"].iloc[0]
            == MED_RACE_POINT_FILL)


def test_全車ゼロのレースでも同じ値で埋まる():
    """ガールズ・新人戦は全車 0.0。旧実装では中央値が NaN になり 50.0 へ落ちていた。"""
    out = build_features_wt(pd.DataFrame(_race("RG", [0.0, 0.0, 0.0])))
    assert set(out["race_point"]) == {MED_RACE_POINT_FILL}


def test_固定値は全期間の中央値として妥当な範囲にある():
    """実測（2026-09-20・737,145行）は 85.55。桁違いの値を置いたら気づけるように。"""
    assert 70.0 < MED_RACE_POINT_FILL < 100.0
