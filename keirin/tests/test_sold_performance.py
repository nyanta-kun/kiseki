"""実際に売った商品の成績集計（`src/sold_performance.py`）。

守るのは4点:
  1. **三連単は着順まで一致**して初めて的中（三連複は順不同）
  2. **2券種（7H2）は行ごとに券種が違う**——一律で畳むと着順違いを的中に数える
  3. **確定配当が正**（`bet_detail.odds` は入稿時点の値で発走までに動く）
  4. **ガミ（当たったのに損）を区別する**——netkeirin の表示的中率はガミを不的中と数える
"""
from __future__ import annotations

import json

from src.sold_performance import (
    build_sold_races, group_by, settle_submission, summarize,
)

#: 三連単フォーメーション 1着=7 / 2着=2 / 3着=1,3
_TF = {"total": 10000, "lines": [
    {"bet_type": "3連単", "combo": "7-2-1", "stake": 5000, "odds": 30.0},
    {"bet_type": "3連単", "combo": "7-2-3", "stake": 5000, "odds": 40.0},
]}
#: 三連複 2軸流し
_TRIO = {"total": 10000, "lines": [
    {"bet_type": "3連複", "combo": "1=2=3", "stake": 5000, "odds": 8.0},
    {"bet_type": "3連複", "combo": "1=2=4", "stake": 5000, "odds": 12.0},
]}


def test_trifecta_needs_the_exact_order():
    """🔴 着順が違えば不的中。順不同で畳むと三連単が三連複になる。"""
    # 買い目 7-2-3 に対して実着順 7-2-3 → 的中
    assert settle_submission(_TF, [7, 2, 3])[2] is True
    # 同じ3車でも着順違い（3-2-7）→ 不的中
    assert settle_submission(_TF, [3, 2, 7])[2] is False


def test_trio_is_order_insensitive():
    assert settle_submission(_TRIO, [3, 1, 2])[2] is True
    assert settle_submission(_TRIO, [2, 4, 1])[2] is True
    assert settle_submission(_TRIO, [1, 2, 5])[2] is False


def test_mixed_bet_types_are_scored_per_line():
    """🔴 7H2 は三連単と三連複を1商品で売る。行ごとに券種を見ること。

    ここを一律 `sorted()` で畳むと、三連単の着順違いまで的中になる。
    """
    mixed = {"total": 10000, "lines": [
        {"bet_type": "3連単", "combo": "7-2-3", "stake": 5000, "odds": 40.0},
        {"bet_type": "3連複", "combo": "1=2=3", "stake": 5000, "odds": 8.0},
    ]}
    # 実着順 3-2-7 は三連単としては外れ、三連複としては当たり（{1,2,3} ではない）
    bet, _pay, hit = settle_submission(mixed, [3, 2, 7])
    assert bet == 10000 and hit is False
    # 実着順 1-2-3 なら三連複だけ当たる
    bet, pay, hit = settle_submission(mixed, [1, 2, 3])
    assert hit is True
    assert pay == int(8.0 * 5000)      # 三連単側は当たっていない


def test_settled_payout_wins_over_submission_time_odds():
    """🔴 確定配当が正。`bet_detail.odds` は入稿時点で発走までに動く。"""
    pay_map = {("3連単", (7, 2, 3)): 12_000}     # 100円あたり12,000円＝120.0倍
    _bet, pay, _hit = settle_submission(_TF, [7, 2, 3], pay_map)
    assert pay == 12_000 * 5000 // 100          # 確定配当×賭け金
    # 確定配当が引けないときだけ入稿時オッズへフォールバック
    _bet, pay_fb, _ = settle_submission(_TF, [7, 2, 3], {})
    assert pay_fb == int(40.0 * 5000)


def test_unscorable_inputs_return_none():
    """採点できないものは**黙って外れにしない**（投資額だけ増えて ROI が壊れる）。"""
    assert settle_submission(None, [1, 2, 3]) is None
    assert settle_submission({"total": 0, "lines": []}, [1, 2, 3]) is None
    assert settle_submission(_TRIO, None) is None          # 結果が未確定
    assert settle_submission(_TRIO, [1, 2]) is None        # 3着まで揃っていない
    unknown = {"lines": [{"bet_type": "2車単", "combo": "1-2", "stake": 100}]}
    assert settle_submission(unknown, [1, 2, 3]) is None   # 未知の券種


def test_bet_detail_accepts_json_string():
    """DB ドライバによっては JSON 文字列で返る。"""
    assert settle_submission(json.dumps(_TRIO), [1, 2, 3])[2] is True


def test_gami_is_counted_as_a_miss_for_the_displayed_rate():
    """🔴 netkeirin の表示的中率はガミ（払戻<賭け金）を不的中として数える。

    素の的中率だけを見ると、点数を増やしたときに「改善した」と誤読する。
    """
    subs = [
        # 当たったが払戻 4,000 < 投資 10,000 ＝ ガミ
        {"race_key": "r1", "race_date": "2026-08-10", "rank_key": "7C",
         "bet_detail": {"lines": [{"bet_type": "3連複", "combo": "1=2=3",
                                   "stake": 10000, "odds": 0.4}]}},
        # 当たって払戻 30,000 ＝ 実質的中
        {"race_key": "r2", "race_date": "2026-08-10", "rank_key": "7C",
         "bet_detail": {"lines": [{"bet_type": "3連複", "combo": "1=2=3",
                                   "stake": 10000, "odds": 3.0}]}},
    ]
    races, skipped = build_sold_races(subs, {"r1": [1, 2, 3], "r2": [1, 2, 3]})
    assert skipped == 0
    s = summarize(races)
    assert s.n_races == 2
    assert s.hit_rate == 1.0            # 素の的中は2/2
    assert s.net_hit_rate == 0.5        # 表示的中は1/2
    assert s.gami_rate == 0.5


def test_unscorable_submissions_are_counted_not_dropped():
    """🔴 採点できなかった件数を返すこと。

    黙って落とすと「売った全部を集計した」ように見える。`bet_detail` の保存は
    2026-08-07 開始で、それ以前の入稿は買い目も金額も残っていない。
    """
    subs = [
        {"race_key": "r1", "race_date": "2026-07-01", "rank_key": "7C",
         "bet_detail": None},
        {"race_key": "r2", "race_date": "2026-08-10", "rank_key": "7C",
         "bet_detail": _TRIO},
    ]
    races, skipped = build_sold_races(subs, {"r2": [1, 2, 3]})
    assert len(races) == 1 and skipped == 1
    assert summarize(races, n_no_detail=skipped).n_no_detail == 1


def test_group_by_splits_without_losing_races():
    subs = [
        {"race_key": f"r{i}", "race_date": "2026-08-10", "rank_key": rk,
         "origin": org, "bet_detail": _TRIO}
        for i, (rk, org) in enumerate([("7C", "rank"), ("7C", "marquee_fill"),
                                       ("9C", "marquee_fill")])
    ]
    races, _ = build_sold_races(subs, {f"r{i}": [1, 2, 3] for i in range(3)})
    by_rank = group_by(races, "rank_key")
    assert by_rank["7C"].n_races == 2 and by_rank["9C"].n_races == 1
    by_origin = group_by(races, "origin")
    assert by_origin["marquee_fill"].n_races == 2
    assert sum(s.n_races for s in by_rank.values()) == len(races)


def test_empty_summary_reports_none_not_zero():
    """0件のとき的中率0%と出すと「全部外した」に見える。"""
    s = summarize([])
    assert s.hit_rate is None and s.roi is None and s.median_payout is None
