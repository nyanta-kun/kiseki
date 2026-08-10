"""RANK_7H2（穴推奨・印なし2軸の高配当）のテスト（2026-08-10 新設）。

守るのは4点:
  1. **軸2車が WT公式印の付いていない車から選ばれること**。ここが壊れると
     規則が「制約なし」＝既存ランクと同じ形へ**黙って退化する**
     （検証中に実際に踏んだ。`prediction_mark` は印なし=0 であって NaN ではない）。
  2. **三連単が倍購入10点・三連複が◎を除く5車BOX 10点**であること。
  3. **入稿の車番グループを展開し直した目が、元の買い目と完全一致すること**。
     一致しないまま入稿すると意図と違う買い目が有料商品として外部へ出る。
  4. **合計購入額が予算枠を超えないこと**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.netkeirin_submit_wt import (  # noqa: E402
    RANK_CONFIGS,
    _normalize_7h2_candidate,
    _split_7h2_tf,
)
from src.netkeirin_client import (  # noqa: E402
    ACT_TYPE_LONGSHOT,
    BET_KIND_TRIFECTA_FORMATION,
    BET_KIND_TRIO_BOX,
    expand_bet,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7H2_BUDGET_CAP,
    RANK_7H2_TF_UNIT,
    rank_7h2_axes,
    rank_7h2_build_legs,
    rank_7h2_entropy,
    rank_7h2_stakes,
    rank_7h2_unmarked,
)

# 典型盤面: 1=◎ 2=○ 3=▲ 4=× / 5,6,7 が印なし。
# モデル1着率は 6 が最大、3着内率は 5 が最大（どちらも印なしの中で）。
MARKS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 0, 6: 0, 7: 0}
WIN = {1: 0.30, 2: 0.20, 3: 0.10, 4: 0.05, 5: 0.12, 6: 0.18, 7: 0.05}
TOP3 = {1: 0.70, 2: 0.60, 3: 0.40, 4: 0.30, 5: 0.55, 6: 0.45, 7: 0.35}


def _cand() -> dict:
    trio, tf = rank_7h2_build_legs(WIN, TOP3, MARKS)
    u_trio, u_tf, total = rank_7h2_stakes(len(trio), len(tf))
    return {
        "race_key": "20260810_11_01", "n_entries": 7,
        "legs_trio": ["=".join(str(x) for x in sorted(t)) for t in trio],
        "legs_tf": tf,
        "stake_trio": u_trio, "stake_tf": u_tf, "bet_amount": total,
        "axis1": int(tf[0].split("-")[0]), "axis2": int(tf[0].split("-")[1]),
    }


# ── 1. 軸は印なしから選ばれる ────────────────────────────────────────────


def test_unmarked_treats_zero_as_no_mark():
    """🔴 印なしは 0。NaN 判定にすると集合が空になり規則が黙って退化する。"""
    assert rank_7h2_unmarked(MARKS) == [5, 6, 7]


def test_unmarked_falls_back_to_all_cars_when_marks_missing():
    """印が2車ぶんも取れない盤面では全車へフォールバックする（買い目を消さない）。"""
    assert rank_7h2_unmarked({1: 1, 2: 2, 3: 3}) == [1, 2, 3]


def test_axes_are_chosen_from_unmarked_only():
    """軸1は印なしの1着率最大、軸2は印なしの3着内率最大。

    印付きの 1 番（1着率0.30・3着内率0.70）はどちらの軸にもならない。
    """
    a1, a2, legs = rank_7h2_axes(WIN, TOP3, MARKS)
    assert a1 == 6, "軸1は印なしの中で1着率が最大の車"
    assert a2 == 5, "軸2は印なしの中で3着内率が最大の車"
    assert 1 not in (a1, a2)
    assert sorted(legs) == [1, 2, 3, 4, 7], "相手は残り5車（総流し・◎を含む）"


def test_axis1_uses_win_not_top3():
    """軸1は**1着率**で選ぶ。3着内率で選ぶと別の車になる盤面で固定する。"""
    a1, a2, _ = rank_7h2_axes(WIN, TOP3, MARKS)
    assert max(rank_7h2_unmarked(MARKS), key=lambda f: TOP3[f]) == 5
    assert a1 == 6 != 5, "3着内率で軸1を選んでいる（1着率で選ぶこと）"


# ── 2. 買い目の形 ────────────────────────────────────────────────────────


def test_trifecta_is_ten_points_of_double_purchase():
    """三連単は軸2を2着に置く5点 + 3着に置く5点の10点。"""
    _trio, tf = rank_7h2_build_legs(WIN, TOP3, MARKS)
    assert len(tf) == 10
    assert sorted(tf) == sorted(
        [f"6-5-{c}" for c in (1, 2, 3, 4, 7)] + [f"6-{c}-5" for c in (1, 2, 3, 4, 7)])


def test_trio_box_excludes_honmei():
    """三連複BOXは◎(1番)を含まない。含めると安い的中が増え実質的中が下がる。"""
    trio, _tf = rank_7h2_build_legs(WIN, TOP3, MARKS)
    assert len(trio) == 10, "プール5車のBOX＝10点"
    cars = set().union(*trio)
    assert 1 not in cars, "◎が三連複に混入している"
    # 相手を3着内率降順に並べると 1(.70) 2(.60) 3(.40) 7(.35) 4(.30)。
    # ◎(1)を除いた上位3車は 2・3・7 なので、プールは 軸1(6)・軸2(5)+2,3,7。
    assert cars == {6, 5, 2, 3, 7}, "軸2車 + 相手のうち◎を除く3着内率上位3車"


def test_honmei_is_still_bought_in_trifecta():
    """◎は三連単の相手としては買う（総流しなので必ず入る）。"""
    _trio, tf = rank_7h2_build_legs(WIN, TOP3, MARKS)
    assert any("-1-" in t or t.endswith("-1") for t in tf)


# ── 3. 賭け金 ────────────────────────────────────────────────────────────


def test_stakes_fit_in_budget():
    """🔴 三連複300円は**ガミ対策**（2026-08-10 ユーザー指示）。

    1レース10,000円なので、三連複のみの的中で投資を上回るには
    「三連複オッズ >= 10,000 / 単価」が要る。100円だと100倍以上が必要で、
    三連複のみ的中の配当は中央20.7倍しかないため **96.9% がガミ**になる。
    300円なら33.3倍以上で済み 69.7% まで下がる。
    """
    u_trio, u_tf, total = rank_7h2_stakes(10, 10)
    assert u_tf == RANK_7H2_TF_UNIT == 700
    assert u_trio == 300, "三連複が薄いと当たっても netkeirin 表示は不的中になる"
    assert total == 10_000 <= RANK_7H2_BUDGET_CAP


@pytest.mark.parametrize("n_trio,n_tf", [(10, 10), (4, 10), (10, 8), (1, 12), (10, 6)])
def test_stakes_never_exceed_cap(n_trio: int, n_tf: int):
    """欠車で点数が動いても予算枠を超えない（超えたら ValueError で落ちる設計）。"""
    u_trio, u_tf, total = rank_7h2_stakes(n_trio, n_tf)
    assert total <= RANK_7H2_BUDGET_CAP
    if u_trio and u_tf:
        assert total == u_trio * n_trio + u_tf * n_tf


# ── 4. 入稿変換 ──────────────────────────────────────────────────────────


def test_split_tf_recovers_axes_and_partners():
    """🔴 軸2は「**全目に共通する車**」。2着列∩3着列で取ると全車になる。

    倍購入なので 2着列も3着列も {軸2}∪相手 で、積集合は相手も含んでしまう。
    実装時にこれを取り違えて全レースで ValueError になった。
    """
    cand = _cand()
    a1, a2, partners = _split_7h2_tf(cand["legs_tf"])
    assert (a1, a2) == (6, 5)
    assert partners == [1, 2, 3, 4, 7]


def test_split_tf_rejects_inconsistent_legs():
    """復元して組み直した目が元と違えば落とす（買い目を偽って入稿しない）。"""
    with pytest.raises(ValueError):
        _split_7h2_tf(["6-5-1", "6-5-2", "6-1-5", "6-2-4"])


def test_normalize_produces_two_formations_and_matching_points():
    """入稿の車番グループを展開し直した目が、元の買い目と完全一致すること。"""
    cand = _cand()
    legs, marks, a1, a2 = _normalize_7h2_candidate(cand, RANK_CONFIGS["7H2"])

    forms = [x for x in legs if x.bet_kind == BET_KIND_TRIFECTA_FORMATION]
    boxes = [x for x in legs if x.bet_kind == BET_KIND_TRIO_BOX]
    assert len(forms) == 2, "三連単は畳めないので2行に分ける"
    assert len(boxes) == 10, "三連複は1目=1行"

    expanded: set[tuple[int, ...]] = set()
    for leg in forms:
        expanded |= expand_bet(leg.bet_kind, leg.groups)
    want = {tuple(int(x) for x in s.split("-")) for s in cand["legs_tf"]}
    assert expanded == want, "入稿する三連単が候補の買い目と一致しない"

    total = sum(len(expand_bet(x.bet_kind, x.groups)) * x.stake_per_line for x in legs)
    assert total == cand["bet_amount"] <= RANK_7H2_BUDGET_CAP
    assert (a1, a2) == (6, 5)
    assert marks[a1] == "◎" and marks[a2] == "○"


def test_rank_config_is_right_after_7h1():
    """🔴 入稿の優先順位は RANK_CONFIGS の定義順。7H2 は 7H1 の直後（ユーザー判断）。

    ここが動くと、重複レース（7H1 の 49.2%）をどちらが取るかが変わる。
    """
    order = list(RANK_CONFIGS)
    assert order.index("7H2") == order.index("7H1") + 1
    assert RANK_CONFIGS["7H2"]["act_type"] == ACT_TYPE_LONGSHOT
    assert RANK_CONFIGS["7H2"]["n_cars"] == 7


def test_7h2_is_not_in_manual_or_gami_claim():
    """2券種なので手動入稿（軸2車UI）は対象外。ガミ抑制も謳わない（均等割りのため）。"""
    from src.race_shape import GAMI_CLAIM_RANKS
    assert "7H2" not in GAMI_CLAIM_RANKS


# ── エントロピー ──────────────────────────────────────────────────────────


def test_entropy_is_max_when_flat():
    """全車同値なら log(7)。読めないほど大きくなる向き。"""
    import math
    flat = {i: 0.5 for i in range(1, 8)}
    assert rank_7h2_entropy(flat) == pytest.approx(math.log(7))


def test_entropy_is_lower_when_one_car_dominates():
    peaked = {1: 0.99, 2: 0.01, 3: 0.01, 4: 0.01, 5: 0.01, 6: 0.01, 7: 0.01}
    assert rank_7h2_entropy(peaked) < rank_7h2_entropy({i: 0.5 for i in range(1, 8)})


def test_entropy_is_scale_invariant():
    """レース内で正規化してから測るので、確率の絶対水準には依らない。"""
    a = {i: v for i, v in TOP3.items()}
    b = {i: v * 0.5 for i, v in TOP3.items()}
    assert rank_7h2_entropy(a) == pytest.approx(rank_7h2_entropy(b))
