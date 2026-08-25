"""実際に売った商品の成績集計（`src/sold_performance.py`）。

守るのは5点:
  1. **三連単は着順まで一致**して初めて的中（三連複は順不同）
  2. **2券種（7H2）は行ごとに券種が違う**——一律で畳むと着順違いを的中に数える
  3. **確定配当だけを使う**（`bet_detail.odds` は入稿時点の値で発走までに動く）。
     引けないなら「未採点」であって外れでも0円でもない
  4. **ガミ（当たったのに損）を区別する**——netkeirin の表示的中率はガミを不的中と数える
  5. **採点は kiseki 側の正本へ委譲している**——ここに規則を書き直すと
     Discord だけが別の答えを出す状態へ戻る

🔴 採点の引数は `(着順, 車番)` の並び。**車番だけに畳まない**（同着が潰れる）。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.sold_performance import (
    _CANONICAL, build_sold_races, group_by, settle_submission, summarize,
)


def _fin(*frames: int) -> list[tuple[int, int]]:
    """着順どおりの車番から `(着順, 車番)` を作る（同着なしの近道）。"""
    return [(i + 1, f) for i, f in enumerate(frames)]

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


def test_採点は正本へ委譲している():
    """🔴 採点規則をこのファイルへ書き直してはいけない。書いた瞬間、同じ商品の
    結果が Discord と Web で食い違う状態（2026-08-25 の障害）へ戻る。"""
    assert _CANONICAL.name == "keirin_settlement.py"
    assert _CANONICAL.exists(), f"正本が見つかりません: {_CANONICAL}"
    # ⚠️ **カレントディレクトリに依存させない。** 相対パスで書くと
    #    リポジトリ直下から pytest を回したときだけ落ちる。
    body = (Path(__file__).resolve().parents[1] / "src" / "sold_performance.py").read_text()
    assert "def settle(" not in body, "採点の実装を keirin 側に持ってはいけない"


def test_trifecta_needs_the_exact_order():
    """🔴 着順が違えば不的中。順不同で畳むと三連単が三連複になる。"""
    # 買い目 7-2-3 に対して実着順 7-2-3 → 的中
    assert settle_submission(_TF, _fin(7, 2, 3), {"7-2-3": 4000}).hit is True
    # 同じ3車でも着順違い（3-2-7）→ 不的中
    assert settle_submission(_TF, _fin(3, 2, 7)).hit is False


def test_trio_is_order_insensitive():
    assert settle_submission(_TRIO, _fin(3, 1, 2), {"1=2=3": 800}).hit is True
    assert settle_submission(_TRIO, _fin(2, 4, 1), {"1=2=4": 800}).hit is True
    assert settle_submission(_TRIO, _fin(1, 2, 5)).hit is False


def test_mixed_bet_types_are_scored_per_line():
    """🔴 7H2 は三連単と三連複を1商品で売る。行ごとに券種を見ること。

    ここを一律 `sorted()` で畳むと、三連単の着順違いまで的中になる。
    """
    mixed = {"total": 10000, "lines": [
        {"bet_type": "3連単", "combo": "7-2-3", "stake": 5000, "odds": 40.0},
        {"bet_type": "3連複", "combo": "1=2=3", "stake": 5000, "odds": 8.0},
    ]}
    # 実着順 3-2-7 は三連単としては外れ、三連複としては当たり（{1,2,3} ではない）
    got = settle_submission(mixed, _fin(3, 2, 7))
    assert got.bet == 10000 and got.hit is False
    # 実着順 1-2-3 なら三連複だけ当たる
    got = settle_submission(mixed, _fin(1, 2, 3), {"1=2=3": 800})
    assert got.hit is True
    assert got.payout == 800 * 5000 // 100      # 三連単側は当たっていない


def test_settled_payout_wins_over_submission_time_odds():
    """🔴 確定配当だけを使う。`bet_detail.odds` は入稿時点で発走までに動く。"""
    got = settle_submission(_TF, _fin(7, 2, 3), {"7-2-3": 12_000})
    assert got.payout == 12_000 * 5000 // 100   # 確定配当×賭け金
    assert got.settled is True
    # 🔴 **引けないときに入稿時オッズで代用しない**（2026-08-25 是正）。
    #    代用すると Web が「未確定」と出すレースに Discord だけ金額を出す。
    pending = settle_submission(_TF, _fin(7, 2, 3), {})
    assert pending.hit is True
    assert pending.payout == 0
    assert pending.settled is False


def test_unscorable_inputs_are_not_scored():
    """採点できないものは**黙って外れにしない**（投資額だけ増えて ROI が壊れる）。"""
    assert settle_submission(None, _fin(1, 2, 3)) is None          # 買い目が無い
    assert settle_submission({"total": 0, "lines": []}, _fin(1, 2, 3)) is None
    assert settle_submission(_TRIO, None).settled is False         # 結果が未確定
    assert settle_submission(_TRIO, _fin(1, 2)).settled is False   # 3着まで揃っていない
    unknown = {"lines": [{"bet_type": "2車単", "combo": "1-2", "stake": 100}]}
    assert settle_submission(unknown, _fin(1, 2, 3)).settled is False   # 未知の券種


def test_dead_heat_is_scored():
    """🔴 同着で採点を諦めない（2026-08-21 立川11R は 10,000円の外れが消えていた）。

    3着同着なら三連複の当たりは2通りで、両方買っていれば両方の払戻が付く。
    """
    fin = [(1, 5), (2, 3), (3, 1), (3, 2)]      # 3着が 1・2 の同着
    both = {"total": 8000, "lines": [
        {"bet_type": "3連複", "combo": "1=3=5", "stake": 5000, "odds": None},
        {"bet_type": "3連複", "combo": "2=3=5", "stake": 3000, "odds": None},
    ]}
    got = settle_submission(both, fin, {"1=3=5": 1000, "2=3=5": 2000})
    assert got.hit is True                       # 同着なので**2点とも当たり**
    assert got.payout == 1000 * 5000 // 100 + 2000 * 3000 // 100
    assert got.settled is True
    # 外れの同着レースも「未確定」にせず外れとして確定させる
    assert settle_submission(_TRIO, [(1, 6), (2, 7), (3, 5), (3, 4)]).settled is True


def test_bet_detail_accepts_json_string():
    """DB ドライバによっては JSON 文字列で返る。"""
    assert settle_submission(json.dumps(_TRIO), _fin(1, 2, 3), {"1=2=3": 800}).hit is True


def test_gami_is_counted_as_a_miss_for_the_displayed_rate():
    """🔴 netkeirin の表示的中率はガミ（払戻<賭け金）を不的中として数える。

    素の的中率だけを見ると、点数を増やしたときに「改善した」と誤読する。
    """
    subs = [
        # 当たったが払戻 4,000 < 投資 10,000 ＝ ガミ
        {"race_key": "r1", "race_date": "2026-08-10", "rank_key": "7C",
         "bet_detail": {"lines": [{"bet_type": "3連複", "combo": "1=2=3",
                                   "stake": 10000, "odds": None}]}},
        # 当たって払戻 30,000 ＝ 実質的中
        {"race_key": "r2", "race_date": "2026-08-10", "rank_key": "7C",
         "bet_detail": {"lines": [{"bet_type": "3連複", "combo": "1=2=3",
                                   "stake": 10000, "odds": None}]}},
    ]
    races, skipped = build_sold_races(
        subs, {"r1": _fin(1, 2, 3), "r2": _fin(1, 2, 3)},
        {"r1": {"1=2=3": 40}, "r2": {"1=2=3": 300}})
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
    races, skipped = build_sold_races(subs, {"r2": _fin(1, 2, 3)},
                                      {"r2": {"1=2=3": 800}})
    assert len(races) == 1 and skipped == 1
    assert summarize(races, n_no_detail=skipped).n_no_detail == 1


def test_group_by_splits_without_losing_races():
    subs = [
        {"race_key": f"r{i}", "race_date": "2026-08-10", "rank_key": rk,
         "origin": org, "bet_detail": _TRIO}
        for i, (rk, org) in enumerate([("7C", "rank"), ("7C", "marquee_fill"),
                                       ("9C", "marquee_fill")])
    ]
    races, _ = build_sold_races(subs, {f"r{i}": _fin(1, 2, 3) for i in range(3)},
                                {f"r{i}": {"1=2=3": 800} for i in range(3)})
    by_rank = group_by(races, "rank_key")
    assert by_rank["7C"].n_races == 2 and by_rank["9C"].n_races == 1
    by_origin = group_by(races, "origin")
    assert by_origin["marquee_fill"].n_races == 2
    assert sum(s.n_races for s in by_rank.values()) == len(races)


def test_empty_summary_reports_none_not_zero():
    """0件のとき的中率0%と出すと「全部外した」に見える。"""
    s = summarize([])
    assert s.hit_rate is None and s.roi is None and s.median_payout is None
