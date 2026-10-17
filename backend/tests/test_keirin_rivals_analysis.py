"""予想家比較の集計（`services/keirin_rivals_analysis`）の検査。

固定したいのは読み違えやすい4点:
  - 予想数が `min_goods` 未満の予想家に順位を付けない（1レース的中の予想家が常に1位になる）
  - 的中(ガミ含む) と 的中(ガミ除く) を取り違えない
  - 未確定（payout None）を 0 円として数えない
  - 推移の既定の比較相手を表示期間の成績で選ばない（前の1年で継続して高い人を並べる）
"""

from __future__ import annotations

from datetime import date

from src.services.keirin_rivals_analysis import (
    SELF_YOSOKA_ID,
    analyze,
    bucket_of,
    steady_compare_candidates,
)

ME = SELF_YOSOKA_ID


def _r(d: str, rk: str, yid: int, bet: int, payout: int | None, name: str = "") -> dict:
    return {
        "race_date": date.fromisoformat(d),
        "race_key": rk,
        "yosoka_id": yid,
        "yosoka_name": name or f"y{yid}",
        "bet": bet,
        "payout": payout,
    }


def _rows() -> list[dict]:
    rows = []
    # 自分: 4商品 bet 40,000 / payout 50,000（1本は払戻>購入、1本はガミ）
    rows += [
        _r("2026-10-05", "A1", ME, 10000, 42000),
        _r("2026-10-05", "A2", ME, 10000, 8000),
        _r("2026-10-06", "A3", ME, 10000, 0),
        _r("2026-10-12", "A4", ME, 10000, 0),
    ]
    # 他A: 4商品 回収 30%
    rows += [_r("2026-10-05", f"B{i}", 1, 10000, 3000 if i == 0 else 0) for i in range(4)]
    rows[-4]["payout"] = 12000  # 1本だけ 12,000 → 回収 30%
    # 他B: 1商品だけで大当たり（順位対象外であるべき）
    rows += [_r("2026-10-05", "C1", 2, 10000, 500000)]
    # 他C: 4商品 回収 125%（自分と同値になる）
    rows += [_r("2026-10-06", f"D{i}", 3, 10000, 50000 if i == 0 else 0) for i in range(4)]
    return rows


def test_min_goods_excludes_lucky_single_race():
    got = analyze(_rows(), self_id=ME, min_goods=3)
    by = {r["yosoka_id"]: r for r in got["ranking"]}
    assert by[2]["roi"] == 50.0
    assert by[2]["rank"] is None and by[2]["ranked"] is False
    # 自分と他C は回収率 125% で同順位1位、他A は3位
    assert by[ME]["roi"] == 1.25 and by[3]["roi"] == 1.25
    assert by[ME]["rank"] == 1 and by[3]["rank"] == 1 and by[1]["rank"] == 3
    assert got["n_ranked"]["roi"] == 3
    assert got["self"]["yosoka_id"] == ME


def test_hit_definitions():
    me = next(r for r in analyze(_rows(), self_id=ME, min_goods=1)["ranking"] if r["is_self"])
    assert me["n_goods"] == 4 and me["n_races"] == 4
    assert me["n_hits"] == 2  # ガミ含む（払戻 > 0）
    assert me["n_net_hits"] == 1  # ガミ除く（払戻 > 購入）
    assert me["hit_rate"] == 0.5 and me["net_hit_rate"] == 0.25
    assert me["profit"] == 10000


def test_unsettled_rows_are_ignored():
    rows = _rows() + [_r("2026-10-12", "A5", ME, 10000, None)]
    me = next(r for r in analyze(rows, self_id=ME, min_goods=1)["ranking"] if r["is_self"])
    assert me["n_goods"] == 4 and me["bet"] == 40000


def test_rank_by_other_metric():
    got = analyze(_rows(), self_id=ME, metric="net_hit_rate", min_goods=3)
    by = {r["yosoka_id"]: r for r in got["ranking"]}
    # net_hit_rate: 自分 0.25 / 他A 0.25（12,000>10,000） / 他C 0.25 → 全員1位
    assert {by[i]["rank"] for i in (ME, 1, 3)} == {1}
    assert got["metric"] == "net_hit_rate"


def test_series_cumulative_and_rank():
    got = analyze(_rows(), self_id=ME, min_goods=3, granularity="week", min_goods_per_bucket=1)
    buckets = [p["bucket"] for p in got["series"]]
    assert buckets == ["2026-10-05", "2026-10-12"]  # 月曜始まり
    first, last = got["series"][0], got["series"][-1]
    # 10/5 週: 自分 3商品 bet 30,000 / payout 50,000
    assert first["self"]["bet"] == 30000 and first["self_cum_roi"] == 50000 / 30000
    # 累積は最後に全期間と一致
    assert last["self_cum_roi"] == 1.25
    # 比較相手は既定で順位上位から自分を除いたもの
    assert [c["yosoka_id"] for c in got["compare"]][0] == 3
    assert "3" in last["compare_cum_roi"]


def test_bucket_of():
    d = date(2026, 10, 14)  # 水曜
    assert bucket_of(d, "week") == "2026-10-12"
    assert bucket_of(d, "month") == "2026-10-01"
    assert bucket_of(d, "day") == "2026-10-14"


# ── 推移の既定の比較相手（継続上位・2026-10-16） ──────────────────────────


def _q(yid: int, q: str, n: int, roi: float, name: str = "") -> dict:
    bet = n * 10000
    return {
        "yosoka_id": yid,
        "yosoka_name": name or f"y{yid}",
        "quarter": date.fromisoformat(q),
        "n_goods": n,
        "bet": bet,
        "payout": int(bet * roi),
    }


def _lookback() -> list[dict]:
    """全体の四半期回収率がほぼ 0.75 になる母集団 + 判定対象の予想家。"""
    qs = ("2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01")
    rows = [_q(900, q, 5000, 0.75) for q in qs]  # 全体を作る大口（回収率は全体と同じ）
    # 10: 4/4 で全体超・回収率 0.95 → 採る
    rows += [_q(10, q, 100, 0.95) for q in qs]
    # 11: 3/4 で全体超・回収率 1.10（1四半期だけ大負け） → 採る・10 より上
    rows += [_q(11, "2025-10-01", 100, 0.10)] + [_q(11, q, 100, 1.50) for q in qs[1:]]
    # 12: 2/4 で全体超（過半に届かない）。1年の回収率は高い 1.20 → 落とす
    rows += [_q(12, q, 100, r) for q, r in zip(qs, (2.0, 2.0, 0.40, 0.40))]
    # 13: 全四半期で全体超だが予想数 160 < 200 → 落とす
    rows += [_q(13, q, 40, 1.30) for q in qs]
    # 14: 予想数は足りるが、数える四半期（20件以上）が1つだけ（そこは全体超） → 短すぎて落とす
    rows += [_q(14, "2026-07-01", 190, 0.90)] + [_q(14, q, 10, 0.10) for q in qs[:3]]
    # 15: 数える四半期がちょうど3つ・2/3 で全体超 → 採る
    rows += [_q(15, q, 100, r) for q, r in zip(qs[1:], (0.90, 0.90, 0.50))] + [_q(15, qs[0], 10, 0.0)]
    # 自分: 全四半期で全体超でも候補に入れない
    rows += [_q(ME, q, 100, 1.40) for q in qs]
    return rows


def test_steady_candidates_filter_and_order():
    got = steady_compare_candidates(_lookback(), self_id=ME)
    ids = [c["yosoka_id"] for c in got]
    assert ids == [11, 10, 15]
    by = {c["yosoka_id"]: c for c in got}
    assert (by[11]["quarters_above"], by[11]["quarters_counted"]) == (3, 4)
    assert (by[15]["quarters_above"], by[15]["quarters_counted"]) == (2, 3)
    assert by[10]["lookback_n_goods"] == 400 and abs(by[10]["lookback_roi"] - 0.95) < 1e-9


def test_steady_default_ignores_displayed_period_performance():
    """表示期間に大勝ちした人（他C=3）ではなく、前の1年で継続して高い人を並べる。"""
    steady = [
        {
            "yosoka_id": 1,
            "yosoka_name": "y1",
            "lookback_roi": 0.95,
            "lookback_n_goods": 400,
            "quarters_above": 4,
            "quarters_counted": 4,
        },
        {
            "yosoka_id": 77,
            "yosoka_name": "y77",
            "lookback_roi": 0.93,
            "lookback_n_goods": 400,
            "quarters_above": 4,
            "quarters_counted": 4,
        },  # 表示期間に予想が無い → 飛ばす
        {
            "yosoka_id": 2,
            "yosoka_name": "y2",
            "lookback_roi": 0.90,
            "lookback_n_goods": 400,
            "quarters_above": 3,
            "quarters_counted": 4,
        },
    ]
    got = analyze(_rows(), self_id=ME, min_goods=3, steady_candidates=steady, n_compare_default=5)
    assert got["compare_basis"] == "steady"
    assert [c["yosoka_id"] for c in got["compare"]] == [1, 2]
    assert got["compare"][0]["lookback_roi"] == 0.95 and got["compare"][0]["quarters_above"] == 4
    assert set(got["series"][-1]["compare_cum_roi"]) == {"1", "2"}


def test_steady_skips_absent_and_takes_next():
    steady = [{"yosoka_id": i, "yosoka_name": f"y{i}"} for i in (77, 78, 1, 3, 2)]
    got = analyze(_rows(), self_id=ME, min_goods=3, steady_candidates=steady, n_compare_default=2)
    assert [c["yosoka_id"] for c in got["compare"]] == [1, 3]


def test_no_steady_candidates_falls_back_to_period_top():
    got = analyze(_rows(), self_id=ME, min_goods=3, steady_candidates=[])
    assert got["compare_basis"] == "period_top"
    assert [c["yosoka_id"] for c in got["compare"]][0] == 3


def test_manual_compare_wins():
    steady = [{"yosoka_id": 1, "yosoka_name": "y1"}]
    got = analyze(_rows(), self_id=ME, min_goods=3, compare_ids=[3, ME], steady_candidates=steady)
    assert got["compare_basis"] == "manual"
    assert [c["yosoka_id"] for c in got["compare"]] == [3]
