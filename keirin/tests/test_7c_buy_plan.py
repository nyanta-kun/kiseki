"""7C の買い方（券種と買う相手）の単一正本を検査する（2026-08-09）。

## 設計

    pw1 >= RANK_7C_TRIFECTA_PW_MIN → 三連単 順序固定・**相手は全部**
    それ以外 ∧ p3_sum >= RANK_7C_TRIO_P3_SUM_MIN → 三連複・**相手は上位2点**
    それ以外 → 買わない

実測（13,960R・掃引/確認）: 網羅 100%→53.3% / 実質的中 31.7→33.6・32.0→33.0 /
ROI 75.8→78.5・76.8→79.7。

## 守る不変条件

1. **三連単は絞らない。** 点数を変えると効果が消える
   （[[keirin_7c_trifecta_switch_2026_08_09]]）
2. **相手2点は「絞る」とセットでしか効かない。** 単独だと ROI −1.35pt
3. 買い方を決めるのは `rank_7c_buy_plan` **だけ**。候補生成・発走前・入稿・Web が
   同じ結論になること（表示と入稿の食い違いはこのリポジトリの定番事故）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy_wt import (  # noqa: E402
    RANK_7C_TRIFECTA_PW_MIN,
    RANK_7C_TRIO_GAP_MIN,
    RANK_7C_TRIO_LEGS_FLOOR,
    RANK_7C_TRIO_P3_SUM_MIN,
    rank_7c_buy_plan,
    rank_7c_cut_legs_by_gap,
)

LEGS = [3, 4, 5, 6]


def _p3(sum_top2: float) -> dict[int, float]:
    """上位2車の合計が sum_top2 になる 7車分の3着内率。"""
    a = sum_top2 / 2
    return {1: a, 2: a, 3: 0.40, 4: 0.35, 5: 0.30, 6: 0.20, 7: 0.05}


def test_trifecta_switch_is_off_in_production() -> None:
    """🔴 2026-08-17〜 三連単切替は**停止**（ユーザー判断・高額狙いは 7T1 等が担う）。

    単勝率が閾値を超えていても三連複で買う。根拠と実測は
    `RANK_7C_TRIFECTA_ENABLED` の定義部。
    """
    import src.strategy_wt as sw
    assert sw.RANK_7C_TRIFECTA_ENABLED is False
    pw = {1: RANK_7C_TRIFECTA_PW_MIN, 2: 0.10}
    kind, _ = rank_7c_buy_plan(_p3(1.60), pw, 1, LEGS)
    assert kind == "trio", "停止したはずの三連単が出ている"


def test_trifecta_logic_is_kept_for_reenabling() -> None:
    """判定ロジック自体は残す（再開は定数1つ）。有効化した場合の挙動を固定する。

    - 相手を絞らない（点数を変えると効果が消える）
    - 三連複側の p3_sum ゲートを受けない
    """
    from src.strategy_wt import rank_7c_use_trifecta
    pw = {1: RANK_7C_TRIFECTA_PW_MIN, 2: 0.10}
    assert rank_7c_use_trifecta(pw, 1, enabled=True) is True
    assert rank_7c_use_trifecta({1: 0.69}, 1, enabled=True) is False


def test_trio_cuts_only_where_there_is_a_gap() -> None:
    """🔴 差がある所でだけ削る。**一律の点数制限ではない。**

    ユーザー指摘（2026-08-09）:「絞るべきは3着内率に差がある場合。
    割り込む余地なしという判断が必要。なんでも一律で買い目を削るのは意味がない」
    """
    pw = {1: 0.30, 2: 0.20}
    # 相手がなだらか（落差が全て gap 未満）→ 削らない＝総流し
    flat = {1: 0.80, 2: 0.76, 3: 0.46, 4: 0.32, 5: 0.29, 6: 0.22, 7: 0.18}
    kind, legs = rank_7c_buy_plan(flat, pw, 1, [3, 4, 5, 6, 7])
    assert kind == "trio"
    assert legs == [3, 4, 5, 6, 7], "差が無いのに削っている"

    # 相手の3番手で落ちる → そこで打ち切る（3点残るので下限には掛からない）
    steep = {1: 0.80, 2: 0.76, 3: 0.55, 4: 0.50, 5: 0.46, 6: 0.20, 7: 0.18}
    kind, legs = rank_7c_buy_plan(steep, pw, 1, [3, 4, 5, 6, 7])
    assert legs == [3, 4, 5], f"落差 0.26 で切れていない: {legs}"


def test_gap_cut_keeps_the_first_partner() -> None:
    """先頭の相手は必ず残る（買い目が0点にならない）。

    ⚠️ 下限（`RANK_7C_TRIO_LEGS_FLOOR`）を外した素の挙動で確認する。既定では
       1点まで縮んだ時点で総流しへ戻るので、この性質が見えなくなる。
    """
    p3 = {3: 0.60, 4: 0.10, 5: 0.05}
    assert rank_7c_cut_legs_by_gap([3, 4, 5], p3, legs_floor=0) == [3]
    assert rank_7c_cut_legs_by_gap([], p3) == []


def test_gap_cut_threshold_is_the_documented_value() -> None:
    """閾値 0.15。0.10 は両指標で劣ることを検証済み（定義部のコメント参照）。"""
    assert RANK_7C_TRIO_GAP_MIN == 0.15
    p3 = {3: 0.50, 4: 0.50 - RANK_7C_TRIO_GAP_MIN, 5: 0.10}
    # ちょうど閾値なら切る（>= 判定）
    assert rank_7c_cut_legs_by_gap([3, 4, 5], p3, legs_floor=0) == [3]


# ── カット後の点数の下限（2026-08-15・ユーザー判断）─────────────────────────


def test_one_point_falls_back_to_the_full_spread() -> None:
    """🔴 削った結果が1点なら **総流しへ戻す**（2026-08-17〜）。

    2026-08-15〜17 は「相手の2,3番手2点」へ差し替えていたが、**その2点買いだけを
    取り出すと的中 22.9%** と精度が突出して低かった（7C 平均 38.3%）。総流しへ
    戻すと看板側で 素の的中 +40.8pt / 表示的中 +8.0pt / ROI +5.7pt と3指標とも
    改善する。実測表は `RANK_7C_TRIO_LEGS_FLOOR` の定義部。

    ⚠️ **見送りにはしない。** 総流しなら的中 65.9% の普通のレースなので、
       母集団から落とす理由が無い（「看板以外は精度が大事」の方針でも、
       件数を捨てるより買い方を戻すほうが精度が上がる）。
    """
    legs = [3, 4, 5, 6, 7]
    steep1 = {3: 0.55, 4: 0.30, 5: 0.22, 6: 0.13, 7: 0.13}   # 1点まで縮む
    assert rank_7c_cut_legs_by_gap(legs, steep1) == legs, "総流しへ戻っていない"


def test_two_points_fall_back_to_the_full_spread() -> None:
    """2点まで縮んだ場合も総流しへ戻す。"""
    legs = [3, 4, 5, 6, 7]
    steep2 = {3: 0.55, 4: 0.50, 5: 0.20, 6: 0.13, 7: 0.13}
    assert rank_7c_cut_legs_by_gap(legs, steep2) == legs
    # 3点残るならカットはそのまま効く（規則自体は生きている）
    steep3 = {3: 0.55, 4: 0.50, 5: 0.46, 6: 0.20, 7: 0.18}
    assert rank_7c_cut_legs_by_gap(legs, steep3) == [3, 4, 5]


def test_no_two_point_trio_is_produced() -> None:
    """🔴 三連複で2点買いが出ないこと（本変更の目的そのもの）。

    落差カットは3点以上か総流しのどちらかにしか着地しない。ここが崩れると
    的中22.9%の帯が復活する。
    """
    legs = [3, 4, 5, 6, 7]
    for p3 in ({3: 0.55, 4: 0.30, 5: 0.22, 6: 0.13, 7: 0.13},
               {3: 0.55, 4: 0.50, 5: 0.20, 6: 0.13, 7: 0.13},
               {3: 0.60, 4: 0.20, 5: 0.19, 6: 0.18, 7: 0.17}):
        assert len(rank_7c_cut_legs_by_gap(legs, p3)) != 2


def test_buy_plan_never_returns_a_single_point_for_trio() -> None:
    """🔴 買い方の正本を通ると、三連複が1点になることはない。

    差し替えを `rank_7c_cut_legs_by_gap` の中に置いたのは、呼び出し側（候補生成・
    発走前判定・再構築）でそれぞれ掛ける形にすると**忘れた経路だけが1点買いを
    出し続ける**ため。ここは経路ではなく正本そのものを固定する。
    """
    pw = {1: 0.30, 2: 0.20}
    for p3_tail in ({3: 0.55, 4: 0.30, 5: 0.22, 6: 0.13, 7: 0.13},
                    {3: 0.55, 4: 0.50, 5: 0.20, 6: 0.13, 7: 0.13},
                    {3: 0.60, 4: 0.10, 5: 0.05, 6: 0.05, 7: 0.05}):
        p3 = {1: 0.80, 2: 0.76, **p3_tail}
        plan = rank_7c_buy_plan(p3, pw, 1, [3, 4, 5, 6, 7])
        assert plan is not None
        kind, legs = plan
        assert kind == "trio"
        assert len(legs) >= 2, f"三連複が{len(legs)}点になっている"


def test_swap_never_produces_an_empty_buy() -> None:
    """相手が少なく2点を作れない形でも買い目を空にしない（総流しへ倒す）。"""
    assert rank_7c_cut_legs_by_gap([3, 4], {3: 0.60, 4: 0.10}) == [3, 4]
    assert rank_7c_cut_legs_by_gap([3], {3: 0.60}) == [3]


def test_trio_below_gate_is_not_bought() -> None:
    """三連複側でゲートを下回るレースは買わない（見送り）。"""
    pw = {1: 0.30, 2: 0.20}
    assert rank_7c_buy_plan(_p3(RANK_7C_TRIO_P3_SUM_MIN - 0.01), pw, 1, LEGS) is None


def test_missing_win_probs_falls_back_to_trio() -> None:
    """単勝率が無いときは三連単へ切り替えない（検証済みの既定へ倒す）。"""
    plan = rank_7c_buy_plan(_p3(1.60), None, 1, LEGS)
    assert plan is not None and plan[0] == "trio"


def test_no_partners_means_no_bet() -> None:
    assert rank_7c_buy_plan(_p3(1.60), {1: 0.9}, 1, []) is None


def test_submit_reads_the_bought_partners_key() -> None:
    """入稿が `legs_7c_buy` を読んでいること。

    `legs_7c`（選別用の全リスト）を読むと絞り込みが効かず、
    **表示と入稿が食い違う**。
    """
    from scripts.netkeirin_submit_wt import RANK_CONFIGS
    assert RANK_CONFIGS["7C"]["partners_key"] == "legs_7c_buy"
