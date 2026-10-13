"""予想家比較の集計（`services/keirin_rivals_analysis`）の検査。

固定したいのは読み違えやすい3点:
  - 予想数が `min_goods` 未満の予想家に順位を付けない（1レース的中の予想家が常に1位になる）
  - 的中(ガミ含む) と 的中(ガミ除く) を取り違えない
  - 未確定（payout None）を 0 円として数えない
"""

from __future__ import annotations

from datetime import date

from src.services.keirin_rivals_analysis import SELF_YOSOKA_ID, analyze, bucket_of

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
