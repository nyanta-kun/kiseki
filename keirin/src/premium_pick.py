"""「厳選の二軸」に差し替える当日3レースの選定（2026-08-22 新設）。

## 決めたこと（ユーザー判断 2026-08-22）

> 的中率の高い側から、ガミになりそうなレースを除いて、**当日の上位3レース**の
> タイトルを「厳選の二軸」にする。

最初の案は「期待値（予測オッズ×確率）の上位3」だったが、**採らなかった**。
EV が高い＝市場より我々が強気＝人気薄狙いなので、実測で

    EV上位3/日   18件 的中 16.7%  ROI 27.2%
    それ以外    210件 的中 36.7%  ROI 67.7%

と「厳選」が最も当たらない群になる（EV 四分位でも Q4 24.6% / Q2 49.1% で単調でない）。

## 採った規則と実測

    ① 買う点すべての予測オッズ >= MIN_POINT_ODDS（2.0倍）  ← A案と同じ
    ② 買う点すべての想定払戻 >= MIN_PAYOUT_RATIO（1.0倍）  ＝ 当たれば必ず増える
    ③ 残りを **`trio_hit_probability` の降順**に並べ、上位 TOP_N（3本）

8/16〜8/21 の実売 228件（三連複ランクのみ・予測オッズを引き直し・全件 OOS）:

    提案                 18件 的中11(61.1%) ガミなし10 ROI 74.8%
    ランダム帰無 2000回   的中 平均6.4件 5-95%[3,10]  → **提案は上位3%点**
                        ガミなし 平均5.5件            → **上位2%点**
                        ROI 平均64.4% 5-95%[28,108]  → 上位30%点（**差なし**）

🟢 **「当たる回数」と「増えて終わる回数」は選べている。収支は選べていない。**

## 🔴 設計上の注意

- **締めすぎると壊れる。** ②を 1.2倍にすると的中 44.4%、1.5倍で 27.8% まで落ちる。
  **1.0（元返しを割らない）が最良**で、これは「安全側へ倒すほど良い」ではない
- **下振れ側（`odds_low`）で②を判定してはいけない。** 実ガミは 1件→0件になるが
  的中が 61.1%→44.4% へ落ち、ランダム帰無（5-95%[3,10]）と**区別できなくなる**。
  予測オッズのまま判定し、**約9%はガミになるものとして受け入れる**（実測 7/74件）
- **三連単のランクは対象外**（予測盤面が三連複しか作れない）。fail-closed
- ⚠️ 厳選3本は実測で **「2倍以上の的中」が0件**。当たりやすい＝安い、の裏返し。
  **「厳選＝増える」と読ませる文言にしないこと**
- ⚠️ 窓は 6日・18件しかない。ランダム帰無の上位3%は強い証拠だが**確認窓は無い**

検証: `scripts/exp_ev_title_and_low_odds_cut.py` / `tests/test_premium_pick.py`
"""
from __future__ import annotations

from typing import Iterable, Mapping

from src.stake_allocation import MIN_POINT_ODDS

#: 1日に「厳選の二軸」とする本数。
TOP_N = 3

#: 買う点すべてに要求する想定払戻の下限（賭け金×予測オッズ÷予算）。
#: 🔴 1.0 より上げてはいけない（上げると的中が落ちて帰無と区別できなくなる）。
MIN_PAYOUT_RATIO = 1.0


def is_premium_candidate(m: Mapping[str, float | None]) -> bool:
    """厳選の候補になりうるか（①②のゲート）。

    m: {"p_hit", "min_point_odds", "min_payout_ratio"}。
       いずれかが None（測れない）なら **候補にしない**（fail-closed）。
       ここは「出すか出さないか」ではなく「特別扱いするか」なので、
       分からないものを特別扱いしないのが安全側。

    >>> is_premium_candidate({"p_hit": .7, "min_point_odds": 3.0, "min_payout_ratio": 1.2})
    True
    >>> is_premium_candidate({"p_hit": .7, "min_point_odds": 1.9, "min_payout_ratio": 1.2})
    False
    >>> is_premium_candidate({"p_hit": .7, "min_point_odds": 3.0, "min_payout_ratio": 0.9})
    False
    >>> is_premium_candidate({"p_hit": None, "min_point_odds": 3.0, "min_payout_ratio": 1.2})
    False
    """
    p_hit = m.get("p_hit")
    odds = m.get("min_point_odds")
    ratio = m.get("min_payout_ratio")
    if p_hit is None or odds is None or ratio is None:
        return False
    return odds >= MIN_POINT_ODDS and ratio >= MIN_PAYOUT_RATIO


def select_premium(metrics: Iterable[Mapping], top_n: int = TOP_N) -> list[str]:
    """当日の「厳選の二軸」にする race_key を返す（多い順に最大 top_n 本）。

    metrics: [{"race_key", "p_hit", "min_point_odds", "min_payout_ratio"}, …]

    🔴 **並びは決定的**にする（p_hit 降順 → race_key 昇順）。同点で順序が揺れると
       同じ日を2回処理したときに別のレースが「厳選」になり、入稿済みの
       タイトルと食い違う。

    >>> select_premium([
    ...     {"race_key": "b", "p_hit": .5, "min_point_odds": 3, "min_payout_ratio": 1.1},
    ...     {"race_key": "a", "p_hit": .9, "min_point_odds": 3, "min_payout_ratio": 1.1},
    ...     {"race_key": "c", "p_hit": .1, "min_point_odds": 3, "min_payout_ratio": 1.1},
    ... ], top_n=2)
    ['a', 'b']
    >>> select_premium([{"race_key": "a", "p_hit": .9,
    ...                  "min_point_odds": 1.5, "min_payout_ratio": 1.1}])
    []
    """
    ok = [m for m in metrics if is_premium_candidate(m)]
    ok.sort(key=lambda m: (-float(m["p_hit"]), str(m["race_key"])))
    return [str(m["race_key"]) for m in ok[:max(0, top_n)]]
