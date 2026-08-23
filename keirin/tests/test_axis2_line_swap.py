"""軸2の差し替え規則を固定する（2026-08-23）。

## 背景

「二軸（◎○）のうち片方が着外で外す」ケースがユーザーの最大の不満だった。
一律に「◎○を軸2から外す」は測って **−5.42pt**（減らしたい「軸2のみ着外」が
30.40%→35.82% に**増える**）。信頼度の十分位で切っても最下位10%で −1.17pt。

効くのは**3条件がそろうときだけ**で、両窓で確認済み:

    探索 2026     575R  31.83% → 38.43%  (+6.61pt [+0.7,+12.2])
    確認 2024-25 1,418R 33.00% → 38.36%  (+5.36pt [+1.9,+8.8])

🔴 **条件を1つでも外すと効果が消える**（片方だけでは +1.5〜1.7pt・有意でない）。
🔴 **現行の二軸が同ラインのときに置換すると −7.96pt。** 噛んでいるペアは崩さない。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    RANK_AXIS2_SWAP_GAP_MAX, rank_7s_swap_axis2_line)

P3 = {1: .70, 2: .60, 3: .55, 4: .30}
LG = {1: "A", 2: "B", 3: "A", 4: "B"}          # 軸1(1)と3が同ライン、◎○は別ライン


def test_swaps_when_all_three_conditions_hold():
    """🟢 これが本体。◎○が別ライン ∧ 代替が軸1と同ライン ∧ 差が小さい。"""
    assert rank_7s_swap_axis2_line(1, 2, P3, LG, 1, 2) == 3


def test_keeps_axis2_when_axes_share_a_line():
    """🔴 噛んでいるペアを崩すと −7.96pt。絶対に差し替えない。"""
    assert rank_7s_swap_axis2_line(1, 2, P3, {1: "A", 2: "A", 3: "A", 4: "B"}, 1, 2) == 2


def test_keeps_axis2_when_gap_is_large():
    """③ 置き換えの代償が大きいときは据え置く。

    ⚠️ 境界ちょうどを浮動小数で作らないこと（`.60 - .114` は 0.486 になり、
       引き算し直すと 0.11399… で条件を通ってしまう）。余裕を持った値で見る。
    """
    far = {1: .70, 2: .60, 3: .60 - RANK_AXIS2_SWAP_GAP_MAX - .05, 4: .20}
    assert rank_7s_swap_axis2_line(1, 2, far, LG, 1, 2) == 2
    near = {1: .70, 2: .60, 3: .60 - RANK_AXIS2_SWAP_GAP_MAX + .05, 4: .20}
    assert rank_7s_swap_axis2_line(1, 2, near, LG, 1, 2) == 3


def test_gap_boundary_is_exclusive():
    """差が `gap_max` **以上**なら据え置く（`<` で通す）。

    ⚠️ 二進で厳密に表せる値を使うこと（`.70-.60` は 0.0999… になる）。
       `.75 - .5 = .25` は厳密。
    """
    p3 = {1: .9, 2: .75, 3: .5, 4: .2}
    assert rank_7s_swap_axis2_line(1, 2, p3, LG, 1, 2, gap_max=0.25) == 2   # 境界＝据え置き
    assert rank_7s_swap_axis2_line(1, 2, p3, LG, 1, 2, gap_max=0.5) == 3


def test_keeps_axis2_when_no_same_line_candidate():
    """② 軸1と同ラインに（◎○以外の）候補がいなければ据え置く。"""
    assert rank_7s_swap_axis2_line(1, 2, P3, {1: "A", 2: "B", 3: "B", 4: "B"}, 1, 2) == 2


def test_only_applies_when_both_axes_are_marks():
    """対象は「二軸が◎○」のレースだけ。"""
    assert rank_7s_swap_axis2_line(1, 2, P3, LG, 1, 4) == 2     # 軸2が○でない
    assert rank_7s_swap_axis2_line(1, 2, P3, LG, 4, 2) == 2     # 軸1が◎でない


def test_fails_closed_without_line_or_marks():
    """⚠️ ライン不明・印欠損は**差し替えない側へ倒す**（推奨を勝手に動かさない）。"""
    assert rank_7s_swap_axis2_line(1, 2, P3, None, 1, 2) == 2
    assert rank_7s_swap_axis2_line(1, 2, P3, {}, 1, 2) == 2
    assert rank_7s_swap_axis2_line(1, 2, P3, LG, None, 2) == 2
    assert rank_7s_swap_axis2_line(1, 2, P3, LG, 1, None) == 2
    assert rank_7s_swap_axis2_line(1, 2, P3, {1: None, 2: "B", 3: "A"}, 1, 2) == 2


def test_replacement_is_chosen_by_p3_not_a_model():
    """🔴 代替は `p3` 順で選ぶ（モデル配布を増やさない）。

    ペア同時確率モデルで選ぶ版との差は 探索 +6.17 vs +6.61 /
    確認 +4.80 vs +5.36 で、条件②が候補を絞るため差が小さい。
    """
    p3 = {1: .70, 2: .60, 3: .52, 5: .58}
    lg = {1: "A", 2: "B", 3: "A", 5: "A"}
    assert rank_7s_swap_axis2_line(1, 2, p3, lg, 1, 2) == 5     # 同ライン内の p3 最大


def test_can_be_disabled():
    assert rank_7s_swap_axis2_line(1, 2, P3, LG, 1, 2, enabled=False) == 2


def test_axis_sum_is_recomputed_after_swap():
    """🔴 生成側が差し替え後の軸で `axis_sum` を引き直していること。

    据え置くとゲート（axis_sum<=1.40）が「もう買わない軸」で判定し、
    選ばれるレースが検証時とずれる。
    """
    src = (REPO / "src" / "cli" / "main.py").read_text(encoding="utf-8")
    i = src.index("rank_7s_swap_axis2_line(")
    tail = src[i:i + 800]
    assert "axis_sum = top3_probs[axis1] + top3_probs[axis2]" in tail


def test_overlap_is_recomputed_after_swap():
    """差し替えたら `wt_overlap_n` も引き直すこと（ゲートの入力が変わる）。"""
    src = (REPO / "src" / "cli" / "main.py").read_text(encoding="utf-8")
    i = src.index("rank_7s_swap_axis2_line(")
    j = src.index("wt_overlap_n = rank_7s_wt_overlap_n(")
    assert j > i, "差し替えの**後**に overlap を計算すること"
