"""ライン先頭比較・ライン内結束の特徴量（2026-08-19 新設）の回帰テスト。

## 背景

ユーザー提起:「逃げの競走得点が低いと別ラインから離されてしまうことがありそう」。
実測（先頭が2人以上いる7車レース 33,293R・2025-01〜2026-08）:

    最強の先頭との得点差 → その先頭自身の3着内率
      0（最強） 68.3% / 0〜2 50.0% / 2〜5 41.2% / 5〜10 29.2% / 10以上 22.5%
    **−45.8pt・単調。** ライン員も道連れ（50.1% → 36.8%）。

既存の `line_rp_*` は**ライン合計**の比較しか持たず、この量を表せなかった。
A/B は `scripts/exp_line_leader_ab.py`（2窓×5seed）。

⚠️ 壊れても例外は出ない。値が 0 で埋まるだけで学習は通り、精度がわずかに
   落ちるだけなので気づけない。テストでしか守れない。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT, LINE_LEADER_COLS_WT, add_line_leader_features_wt,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


#: ライン1 = (1:110 先頭, 2:70)  ライン2 = (3:95 先頭, 4:93)
#: ライン3 = (5:60 先頭, 6:88, 7:86)  ← **逃げが弱く番手が強い**
FIELD = _df([
    dict(race_key="R", frame_no=1, race_point=110, line_group=1, line_size=2, is_line_leader=1),
    dict(race_key="R", frame_no=2, race_point=70,  line_group=1, line_size=2, is_line_leader=0),
    dict(race_key="R", frame_no=3, race_point=95,  line_group=2, line_size=2, is_line_leader=1),
    dict(race_key="R", frame_no=4, race_point=93,  line_group=2, line_size=2, is_line_leader=0),
    dict(race_key="R", frame_no=5, race_point=60,  line_group=3, line_size=3, is_line_leader=1),
    dict(race_key="R", frame_no=6, race_point=88,  line_group=3, line_size=3, is_line_leader=0),
    dict(race_key="R", frame_no=7, race_point=86,  line_group=3, line_size=3, is_line_leader=0),
])


def _by_frame(out: pd.DataFrame, col: str) -> dict[int, float]:
    return dict(zip(out["frame_no"], out[col]))


def test_all_columns_are_registered_in_feature_cols():
    for c in LINE_LEADER_COLS_WT:
        assert c in FEATURE_COLS_WT, f"{c} が FEATURE_COLS_WT に無い"


def test_line_leader_rp_is_the_leader_not_the_line_max():
    """🔴 これが本体。`leader_rp` は**先頭の得点**で、ライン内最大ではない。

    ライン3 は先頭60 / 番手88 なので、`line_rp_max` なら 88 を拾ってしまい
    「逃げが弱い」という事実が消える。
    """
    got = _by_frame(add_line_leader_features_wt(FIELD), "line_leader_rp")
    assert got[5] == 60.0 and got[6] == 60.0 and got[7] == 60.0
    assert got[1] == 110.0
    assert got[3] == 95.0


def test_gap_to_strongest_leader():
    """最強の先頭（110）との差。実測で3着内率を −45.8pt 動かした量。"""
    got = _by_frame(add_line_leader_features_wt(FIELD), "line_leader_rp_gap_top")
    assert got[1] == 0.0        # 自分が最強
    assert got[3] == 15.0       # 110 − 95
    assert got[5] == 50.0       # 110 − 60 ← 離される側


def test_leader_rank_and_weak_leader_flag():
    out = add_line_leader_features_wt(FIELD)
    rank = _by_frame(out, "line_leader_rp_rank")
    weak = _by_frame(out, "line_leader_is_weakest")
    assert rank[1] == 0.0 and rank[3] == 1.0 and rank[5] == 2.0
    assert weak[1] == 0.0 and weak[3] == 0.0
    assert weak[5] == 1.0       # 本物の先頭の中で最下位


def test_cohesion_distinguishes_lines_with_the_same_total():
    """🔴 合計が同じでも中身が違えば別の値になること。

    `line_rp_sum` は「90+90」と「110+70」を区別できない。番手がちぎれる
    リスクを表すのが `line_rp_spread` / `line_rp_lead_minus_next`。
    """
    tight = _df([
        dict(race_key="A", frame_no=1, race_point=90, line_group=1, line_size=2, is_line_leader=1),
        dict(race_key="A", frame_no=2, race_point=90, line_group=1, line_size=2, is_line_leader=0),
    ])
    loose = _df([
        dict(race_key="B", frame_no=1, race_point=110, line_group=1, line_size=2, is_line_leader=1),
        dict(race_key="B", frame_no=2, race_point=70,  line_group=1, line_size=2, is_line_leader=0),
    ])
    t = add_line_leader_features_wt(tight)
    l = add_line_leader_features_wt(loose)  # noqa: E741
    assert t["line_rp_sum"].iloc[0] if "line_rp_sum" in t else True
    assert t["line_rp_spread"].iloc[0] == 0.0
    assert l["line_rp_spread"].iloc[0] == 40.0
    assert t["line_rp_lead_minus_deputy"].iloc[0] == 0.0
    assert l["line_rp_lead_minus_deputy"].iloc[0] == 40.0


def test_lead_minus_deputy_uses_the_best_non_leader():
    """🔴 **先頭 − 番手（＝先頭以外の最高得点）**であること。

    ライン3 は 先頭60 / 88 / 86。番手は 88 なので **60 − 88 = −28**。
    初版は「ライン内2番目の得点(86)」を引いて −26 になっており、
    **先頭が最上位でない（逃げが弱い）ラインでだけ値がずれていた**
    （2026-08-19 ユーザー指摘で是正）。
    """
    got = _by_frame(add_line_leader_features_wt(FIELD), "line_rp_lead_minus_deputy")
    assert got[5] == 60.0 - 88.0
    assert got[5] < 0


def test_solo_riders_are_excluded_from_every_leader_feature():
    """🔴 単騎は**どの特徴でも**先頭に数えない（定義の統一）。

    初版は `gap_top` だけ単騎を除外し、`rank` / `is_weak` は含めていたため、
    同じ「先頭」という語が特徴ごとに違う意味になっていた
    （2026-08-19 ユーザー指摘で是正）。
    """
    field = _df([
        dict(race_key="R", frame_no=1, race_point=110, line_group=1, line_size=2, is_line_leader=1),
        dict(race_key="R", frame_no=2, race_point=70,  line_group=1, line_size=2, is_line_leader=0),
        dict(race_key="R", frame_no=3, race_point=95,  line_group=2, line_size=2, is_line_leader=1),
        dict(race_key="R", frame_no=4, race_point=93,  line_group=2, line_size=2, is_line_leader=0),
        dict(race_key="R", frame_no=5, race_point=120, line_group=3, line_size=1, is_line_leader=1),
    ])
    out = add_line_leader_features_wt(field)
    rank = _by_frame(out, "line_leader_rp_rank")
    weak = _by_frame(out, "line_leader_is_weakest")
    # 最強の先頭は 110（単騎の120ではない）
    assert _by_frame(out, "line_leader_rp_gap_top")[1] == 0.0
    # 単騎は順位の外（本物の先頭2つの次＝2.0）に置き、最弱にも数えない
    assert rank[1] == 0.0 and rank[3] == 1.0 and rank[5] == 2.0
    assert weak[3] == 1.0 and weak[5] == 0.0


def test_missing_columns_do_not_crash():
    """🔴 列が欠けても落ちないこと。

    `pd.to_numeric(df.get("無い列"))` は **numpy.float64 を返す**ので、
    そのまま `.fillna()` を呼ぶと AttributeError になる（2026-08-19 に実際に発生）。
    """
    minimal = _df([dict(race_key="R", frame_no=1), dict(race_key="R", frame_no=2)])
    out = add_line_leader_features_wt(minimal)
    for c in LINE_LEADER_COLS_WT:
        assert c in out.columns
        assert out[c].notna().all()
