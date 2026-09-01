"""地方 39特徴の**値**が配信側と学習側で一致することを固定する。

## なぜ必要か

39特徴は **別実装で2回書かれている**:

- 配信: `src/indices/chihou_calculator._build_lgb_features`（ORM・1レースずつ）
- 学習/検証/バックフィル: `scripts/train_chihou_prod_lgb` の
  `featurize` → `add_external_features` → `add_track_features` →
  `add_corner_trainer_features`（pandas・一括）

`test_chihou_model_parity.py` は**列名と順序しか見ていない**。
値の意味がずれても LightGBM は例外を出さず静かに劣化するだけで、
しかも検証スクリプトは全て pandas 側を通るので **検証は良いのに配信だけ外れる**
という形で表面化する（2026-08-31 の大井調査で実測したところ当時は一致していた。
この一致を固定するのが本テスト）。

DB を引かずに済むよう、両実装が受け取る「注入テーブル」を同一の素材から作って渡す。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.train_chihou_prod_lgb import (  # noqa: E402
    HIST_FEATURES,
    add_corner_trainer_features,
    add_external_features,
    add_track_features,
    featurize,
)
from src.indices.chihou_calculator import _LGB_FEATURE_NAMES, _build_lgb_features  # noqa: E402

RACE_ID = 999_001


class _Entry:
    """ChihouRaceEntry のうち _build_lgb_features が触る属性だけを持つ軽量スタブ。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class _Race:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


# ── 素材（両実装に同じものを流す唯一の真実源）──────────────────────
# 欠損・中立値の分岐を踏ませるため、意図的にバラつかせている:
#   horse 2: 外部指数が両方欠損（ext_missing=1 / rank_n=0.5 経路）
#   horse 3: last_margin が None（INDEX_NEUTRAL 経路）
#   horse 4: コーナー/調教師テーブルに行が無い（既定値 0.5 / 0.08 / 0.25 経路）
#   horse 5: prev_pace_ratio が -1（不明→0.5 経路）
#   horse 1,6: c_early_n<=0.3 かつ c_runs>0（front_density の分子）
#   horse 3  : c_early_n=0.35（front_density の閾値 0.3 の境界をまたぐ）
#   horse 2  : c_early_n は低いが c_runs=0（front_density の「経験あり」ガード）
_HORSES = [
    # hid, frame, age, carried, weight, wchg, sp,   l3f,  jk,   rot,  lm,   nk,   kc
    (1, 1, 3, 54.0, 480, +2, 61.2, 55.0, 58.0, 52.0, 63.0, 42.0, 71.0),
    (2, 2, 4, 55.0, 502, -4, 48.5, 51.0, 47.0, 50.0, 49.0, None, None),
    (3, 3, 5, 56.0, 466, 0, 55.1, 60.2, 51.0, 48.0, None, 38.0, 65.0),
    (4, 4, 3, 54.0, 512, +6, 44.0, 43.5, 62.0, 55.0, 41.0, 51.0, None),
    (5, 5, 6, 57.0, 490, -2, 52.0, 49.0, 44.0, 61.0, 58.0, None, 59.0),
    (6, 6, 4, 55.0, 498, 0, 58.8, 57.3, 55.0, 47.0, 52.0, 45.0, 68.0),
]
# horse_id -> (improving_form, track_win_rate, class_drop_ratio, prev_pace_ratio)
_HIST = {
    1: (1.0, 0.25, 0.0, 0.20),
    2: (0.0, 0.00, 1.0, 0.80),
    3: (-1.0, -1.0, 0.0, 0.55),
    4: (1.0, 0.50, 0.0, 0.35),
    5: (0.0, 0.10, 0.0, -1.0),   # prev_pace_ratio 不明
    6: (-1.0, 0.33, 0.0, 0.25),
}
# horse_id -> (horse_wet_apt, horse_wet_runs)
_WET = {1: (0.30, 0.25), 2: (-0.15, 0.10), 3: (0.0, 0.0), 5: (0.45, 0.60), 6: (-0.05, 0.05)}
# horse_id -> (c_early_n, c_late_gain_n, c_makuri_n, c_runs, jk_change)
_CT = {
    1: (0.20, 0.10, 0.05, 0.80, 0.0),
    2: (0.10, -0.20, 0.00, 0.00, 1.0),
    3: (0.35, 0.05, 0.10, 0.20, 0.0),
    5: (0.75, 0.30, 0.25, 1.00, 1.0),
    6: (0.15, -0.05, 0.00, 0.60, 0.0),
}
# horse_id -> (tr_win_rate, tr_top3_rate, tr_runs_n)
_TRAINER = {1: (0.12, 0.34, 0.7), 2: (0.05, 0.19, 0.3), 3: (0.09, 0.28, 0.5), 6: (0.15, 0.41, 0.9)}

_CONDITIONS = ["良", "稍", "重", "不", ""]


def _orm_matrix(condition: str) -> np.ndarray:
    entries = [
        _Entry(horse_id=h[0], frame_number=h[1], horse_age=h[2],
               weight_carried=h[3], horse_weight=h[4], weight_change=h[5])
        for h in _HORSES
    ]
    race = _Race(surface="ダ", condition=condition, distance=1400,
                 head_count=len(_HORSES), course="44", course_name="大井")
    rows = _build_lgb_features(
        entries, race,
        speed_map={h[0]: h[6] for h in _HORSES},
        last3f_map={h[0]: h[7] for h in _HORSES},
        jockey_map={h[0]: h[8] for h in _HORSES},
        rotation_map={h[0]: h[9] for h in _HORSES},
        last_margin_map={h[0]: h[10] for h in _HORSES},
        hist_feat_map={hid: list(v) for hid, v in _HIST.items()},
        ext_raw={h[0]: (h[12], h[11]) for h in _HORSES},  # (kc_sp, nk_idx)
        wet_apt_map=_WET,
        odds_map_race=None,
        corner_map=_CT,
        trainer_feat_map=_TRAINER,
    )
    return np.asarray(rows, dtype=np.float64)


def _pandas_matrix(condition: str) -> np.ndarray:
    raw = pd.DataFrame([
        {
            "race_id": RACE_ID, "horse_id": h[0], "date": "20260831",
            "surface": "ダ", "condition": condition, "distance": 1400,
            "head_count": len(_HORSES),
            "frame_number": h[1], "horse_age": h[2], "weight_carried": h[3],
            # 本番クエリは COALESCE(horse_weight,500) / COALESCE(weight_change,0) 済み
            "horse_weight": h[4], "weight_change": h[5],
            "speed_index": h[6], "last3f_index": h[7], "jockey_index": h[8],
            "rotation_index": h[9],
            # 本番クエリは COALESCE(last_margin_index, 50.0) 済み
            "last_margin_index": 50.0 if h[10] is None else h[10],
            "nk_idx": h[11], "kc_sp": h[12],
        }
        for h in _HORSES
    ])

    # prep と同じ順序: featurize → 履歴系(-1 埋め) → 外部 → 馬場 → CT
    df = featurize(raw)
    for i, col in enumerate(HIST_FEATURES):
        df[col] = [float(_HIST[h[0]][i]) for h in _HORSES]
    df = add_external_features(df)

    apt_tbl = pd.DataFrame(
        [{"horse_id": hid, "race_id": RACE_ID, "horse_wet_apt": v[0], "horse_wet_runs": v[1]}
         for hid, v in _WET.items()]
    ).set_index(["horse_id", "race_id"])
    df = add_track_features(df, apt_tbl)

    corner_tbl = pd.DataFrame(
        [{"horse_id": hid, "race_id": RACE_ID, "c_early_n": v[0], "c_late_gain_n": v[1],
          "c_makuri_n": v[2], "c_runs": v[3], "jk_change": v[4]} for hid, v in _CT.items()]
    ).set_index(["horse_id", "race_id"])
    trainer_map = pd.DataFrame(
        [{"horse_id": h[0], "race_id": RACE_ID, "trainer_id": h[0]} for h in _HORSES]
    )
    trainer_tbl = pd.DataFrame(
        [{"trainer_id": hid, "date": "20260831", "tr_win_rate": v[0],
          "tr_top3_rate": v[1], "tr_runs_n": v[2]} for hid, v in _TRAINER.items()]
    ).set_index(["trainer_id", "date"])
    df = add_corner_trainer_features(df, corner_tbl, trainer_tbl, trainer_map)

    df = df.set_index("horse_id").loc[[h[0] for h in _HORSES]]
    return df[list(_LGB_FEATURE_NAMES)].to_numpy(dtype=np.float64)


@pytest.mark.parametrize("condition", _CONDITIONS)
def test_39特徴の値が配信側と学習側で一致する(condition: str) -> None:
    """馬場状態を変えても全 39 列が一致すること。

    condition="" は**本番の配信時そのもの**（指数は当日00:01に算出され、
    その時点で races.condition は NULL。確定後にしか入らない）。
    """
    orm = _orm_matrix(condition)
    pdm = _pandas_matrix(condition)
    assert orm.shape == pdm.shape == (len(_HORSES), 39)

    mismatched = [
        (name, orm[:, i].tolist(), pdm[:, i].tolist())
        for i, name in enumerate(_LGB_FEATURE_NAMES)
        if not np.allclose(orm[:, i], pdm[:, i], rtol=1e-9, atol=1e-9)
    ]
    assert not mismatched, "配信側と学習側で値がずれた特徴: " + "; ".join(
        f"{n}: 配信={o} 学習={p}" for n, o, p in mismatched
    )


def test_学習側の列定義が配信側と同順である() -> None:
    """PROD_FEATURES と _LGB_FEATURE_NAMES の同一性（順序込み）。"""
    from scripts.train_chihou_market_lgb import PROD_FEATURES

    assert list(PROD_FEATURES) == list(_LGB_FEATURE_NAMES)
