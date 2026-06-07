"""chihou_calculator._compute_ext_features の単体テスト（外部指数特徴・train/serve parity）。

train 側 scripts/train_chihou_prod_lgb.add_external_features と同一意味論であることを保証する:
  z = レース内z(標本std ddof=1, 欠損→0) / rank_n = 降順min順位/頭数(0=最良, 欠損→0.5) / ext_missing。
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from src.indices.chihou_calculator import _compute_ext_features


def _entries(horse_ids):
    return [SimpleNamespace(horse_id=h, horse_number=h) for h in horse_ids]


def test_ext_features_basic_z_and_rank() -> None:
    """kc=[60,50,40] → z=[1,0,-1]・rank_n=[0,1/3,2/3]。全馬present→ext_missing=0。"""
    entries = _entries([1, 2, 3])
    ext_raw = {1: (60.0, None), 2: (50.0, None), 3: (40.0, None)}
    out = _compute_ext_features(entries, ext_raw, head_count=3.0)
    # [kc_sp_z, nk_idx_z, kc_rank_n, nk_rank_n, ext_missing]
    assert math.isclose(out[1][0], 1.0, abs_tol=1e-9)
    assert math.isclose(out[2][0], 0.0, abs_tol=1e-9)
    assert math.isclose(out[3][0], -1.0, abs_tol=1e-9)
    assert math.isclose(out[1][2], 0.0, abs_tol=1e-9)        # 60=1位 → (1-1)/3
    assert math.isclose(out[2][2], 1.0 / 3.0, abs_tol=1e-9)  # 50=2位
    assert math.isclose(out[3][2], 2.0 / 3.0, abs_tol=1e-9)  # 40=3位
    # nk 全欠損 → z=0, rank_n=0.5, ext_missing は kc present のため 0
    assert all(out[h][1] == 0.0 for h in (1, 2, 3))
    assert all(out[h][3] == 0.5 for h in (1, 2, 3))
    assert all(out[h][4] == 0.0 for h in (1, 2, 3))


def test_ext_features_missing_flag_and_partial() -> None:
    """馬2は両欠損→ext_missing=1。present馬のz/rankは欠損を母集団から除外して計算。"""
    entries = _entries([1, 2, 3])
    ext_raw = {1: (60.0, 30.0), 2: (None, None), 3: (40.0, 20.0)}
    out = _compute_ext_features(entries, ext_raw, head_count=3.0)
    sd = math.sqrt(((60 - 50) ** 2 + (40 - 50) ** 2) / 1)  # ddof=1, present=[60,40]
    assert math.isclose(out[1][0], (60 - 50) / sd, abs_tol=1e-9)
    assert math.isclose(out[3][0], (40 - 50) / sd, abs_tol=1e-9)
    assert out[2][0] == 0.0 and out[2][1] == 0.0    # 欠損→z=0
    assert out[2][2] == 0.5 and out[2][3] == 0.5    # 欠損→rank_n=0.5
    assert out[2][4] == 1.0                          # 両欠損→ext_missing=1
    assert out[1][4] == 0.0 and out[3][4] == 0.0
    # rank: 60=1位→0, 40=2位→(2-1)/3。欠損馬は順位母集団に入らない
    assert math.isclose(out[1][2], 0.0, abs_tol=1e-9)
    assert math.isclose(out[3][2], 1.0 / 3.0, abs_tol=1e-9)


def test_ext_features_all_missing() -> None:
    """全馬欠損(外部スクレイプ無し)→ 全特徴が中立値・ext_missing=1で安全縮退。"""
    entries = _entries([1, 2])
    out = _compute_ext_features(entries, {}, head_count=2.0)
    for h in (1, 2):
        assert out[h] == [0.0, 0.0, 0.5, 0.5, 1.0]
