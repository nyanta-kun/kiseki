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
    ② 買う点すべての想定払戻（**下限包絡**）>= MIN_PAYOUT_RATIO（1.0倍）
    ③ 残りを **`trio_hit_probability` の降順**に並べ、上位 TOP_N（3本）

## 🔴 ②は「下限包絡」で測る（2026-08-22・ユーザー指示「厳選のガミは許容できない」）

初版は**予測オッズ**で ②を測っていた。的中は良かった（61.1%）が、
**実ガミが出る**（18件中1件・母集団全体では的中の14%がガミ）。
ユーザー要件は「厳選ではガミを許容しない」なので、
`_conservative_trio_board()` の**下限包絡**（下振れしてもこの倍率は割らない水準）で
測るように変えた。

8/16〜8/21 の実売 228件（三連複ランクのみ・予測オッズを引き直し・全件 OOS）:

    条件                的中           実ガミ  ROI
    予測払戻>=1.0（初版）  11(61.1%)      1件   74.8%   ← ガミが出るので不可
    **下限包絡>=1.0**     8(44.4%)      **0件**  64.0%   ← 採用
    下限包絡>=1.1         6(33.3%)      0件   48.1%
    予測払戻>=1.2         8(44.4%)      0件   64.0%   （同値・下限包絡のほうが筋が通る）

    ランダム帰無 4000回  的中 平均6.4 5-95%[3,10] → 採用案は**上位28%点**
                       ガミなし 平均5.5 5-95%[3,9] → **上位14%点**

🔴 **ガミを許容しない代償として、「当たりやすさ」の優位はランダムと区別できなく
   なった**（初版は上位3%点だった）。全体の的中率 35.1% に対し 44.4% で上ではあるが、
   n=18 では差と言えない。**「厳選」の根拠は「当たれば必ず増える」のほうにある。**

## 🔴 それでも「ガミゼロ」は保証ではない

確定オッズは予測から大きく下振れする。買った点 1,020点の実測:

    確定/予測      p05 0.60 / p10 0.69 / 中央 1.08 / **1.0未満 40%**
    確定/下限包絡  p05 0.71 / p10 0.82 / 中央 1.29 / **1.0未満 22%**

下限包絡でも 22% の点が下回るので、**構造的にガミを排除することはできない**。
18件で 0件だったという実測があるだけ。**「絶対にガミにならない」と説明しないこと。**

## 🔴 その他の設計上の注意

- **締めすぎると壊れる。** ②を下限包絡 1.1倍にすると的中 33.3%、
  予測払戻 1.5倍なら 27.8% まで落ちる。**1.0 が最良**で
  「安全側へ倒すほど良い」ではない
- **三連単のランクは対象外**（予測盤面が三連複しか作れない）。fail-closed
- ⚠️ 厳選3本は実測で **「2倍以上の的中」が0件**。当たりやすい＝安い、の裏返し。
  **「厳選＝増える」と読ませる文言にしないこと**
- ⚠️ 窓は 6日・18件しかない。**確認窓は無い**

検証: `scripts/exp_ev_title_and_low_odds_cut.py` / `tests/test_premium_pick.py`
"""
from __future__ import annotations

from typing import Iterable, Mapping

from src.stake_allocation import MIN_POINT_ODDS

#: 1日に「厳選の二軸」とする本数。
#: 🔴 **2026-08-22 夕に 0（＝停止）へ落とした**（ユーザー判断「まだ課題があるので
#:    一旦保留」）。0 なら `select_premium` が空を返し、タイトルの差し替えは起きない。
#:    実装とテストは**残してある**ので、再開は 3 に戻すだけ。
#:
#: 再開する前に片付けること（[[keirin_handoff_2026_08_22_pm]]）:
#:   1. ガミを許容しない形（下限包絡>=1.0）にすると「当たりやすさ」の優位が
#:      ランダム帰無と区別できない（的中は上位28%点）。何を根拠に「厳選」と
#:      名乗るかが未解決
#:   2. ガミゼロは保証ではない（確定/下限包絡 で 22% の点が 1.0 を下回る）
#:   3. 7A の既定タイトルが同じ「厳選の二軸」で衝突している
#:   4. 検証窓が 6日・18件しかなく確認窓が無い
TOP_N = 0

#: 買う点すべてに要求する想定払戻の下限。
#: 🔴 **下限包絡（`odds_low`）で測ること。** 予測オッズで測ると実ガミが出る
#:    （初版がそうだった）。ユーザー要件は「厳選のガミは許容できない」。
#: 🔴 1.0 より上げてはいけない（上げると的中が落ちる。1.1 で 44.4%→33.3%）。
MIN_PAYOUT_RATIO = 1.0


def is_premium_candidate(m: Mapping[str, float | None]) -> bool:
    """厳選の候補になりうるか（①②のゲート）。

    m: {"p_hit", "min_point_odds", "min_payout_ratio"}。
       `min_payout_ratio` は **下限包絡ベース**（賭け金×`odds_low`÷予算）。
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
