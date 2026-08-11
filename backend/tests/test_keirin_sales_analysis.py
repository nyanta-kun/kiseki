"""netkeirin 売上×成績 分析（`services/keirin_sales_analysis.py`）の検査。

ここで縛りたいのは主に **定義の取り違え**。「的中」がガミ含む／除くのどちらか、
「売上」が総販売pt／有償ptのどちらかは、数字が普通に出てしまうため
コードを読んでも間違いに気づけない。
"""
from __future__ import annotations

import pytest

from src.services.keirin_sales_analysis import (
    MIN_SAMPLES_FOR_CORRELATION,
    build_correlations,
    build_daily,
    build_leadtime_buckets,
    build_link_check,
    build_origin_breakdown,
    build_races,
    build_rank_breakdown,
    build_route_breakdown,
    build_summary,
    classify_route,
    pearson,
)


def _daily_row(date: str, **kw):
    base = {
        "sale_date": date,
        "n_predictions": 10,
        "n_hits_incl_garami": 4,
        "n_hits_excl_garami": 2,
        "stake_amount": 100000,
        "payout_amount": 60000,
        "n_sold": 30,
        "sold_points": 9000,
        "sold_paid_points": 6000,
    }
    base.update(kw)
    return base


def _race_row(race_id: str, **kw):
    base = {
        "race_id": race_id,
        "race_key": f"{race_id[:8]}_{race_id[8:10]}_{race_id[10:12]}",
        "race_date": race_id[:8],
        "venue_code": race_id[8:10],
        "venue_name": "四日市",
        "race_no": int(race_id[10:12]),
        "race_label": "08/10 四日市 Ａ級 準決勝",
        "rank": "7S",
        "origin": "rank",
        "detected_ranks": None,
        "meeting_type": "day",
        "n_hits_incl_garami": 1,
        "n_hits_excl_garami": 1,
        "stake_amount": 10000,
        "payout_amount": 15000,
        "n_sold": 3,
        "sold_points": 900,
        "sold_paid_points": 600,
        "avg_sold_minutes": 152.0,
        "avg_sold_hour": 3.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# pearson
# ---------------------------------------------------------------------------

def test_相関は完全一致で1になる():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert pearson(xs, [2.0, 4.0, 6.0, 8.0, 10.0]) == 1.0
    assert pearson(xs, [10.0, 8.0, 6.0, 4.0, 2.0]) == -1.0


def test_標本が足りなければ相関を出さない():
    n = MIN_SAMPLES_FOR_CORRELATION - 1
    xs = [float(i) for i in range(n)]
    # 完全な直線でも標本不足なら None。少数点の相関は必ず±1付近に出て誤読を招く。
    assert pearson(xs, xs) is None


def test_片方が定数なら相関は定義できない():
    assert pearson([1.0, 2.0, 3.0, 4.0, 5.0], [7.0] * 5) is None


# ---------------------------------------------------------------------------
# 日別
# ---------------------------------------------------------------------------

def test_日別はガミ含む率とガミ除く率を別々に持つ():
    """netkeirin プロフィールの「的中率」はガミ除く。相関用はガミ含む。
    片方に潰すと、当たったのに賭け金割れした日が「外れ」になる（またはその逆）。"""
    d = build_daily([_daily_row("20260809", n_predictions=32,
                                n_hits_incl_garami=10, n_hits_excl_garami=6)])[0]
    assert d["date"] == "2026-08-09"
    assert d["hit_rate_incl"] == pytest.approx(10 / 32)
    assert d["hit_rate_excl"] == pytest.approx(6 / 32)
    assert d["n_garami"] == 4
    assert d["garami_rate"] == pytest.approx(4 / 10)


def test_的中ゼロの日のガミ率は0ではなく未定義():
    d = build_daily([_daily_row("20260802", n_hits_incl_garami=0, n_hits_excl_garami=0)])[0]
    assert d["garami_rate"] is None


def test_予想ゼロの日は的中率を出さない():
    d = build_daily([_daily_row("20260802", n_predictions=0,
                                n_hits_incl_garami=0, n_hits_excl_garami=0)])[0]
    assert d["hit_rate_incl"] is None


# ---------------------------------------------------------------------------
# レース別
# ---------------------------------------------------------------------------

def test_ガミ的中は的中として数えつつガミ印を立てる():
    r = build_races([_race_row("202608104808", n_hits_incl_garami=1, n_hits_excl_garami=0)])[0]
    assert r["hit"] is True            # 買い目は当たっている
    assert r["hit_excl_garami"] is False
    assert r["is_garami"] is True


def test_レースは日付とIDで昇順に並ぶ():
    races = build_races([
        _race_row("202608104808"),
        _race_row("202608094611", race_date="20260809"),
        _race_row("202608104611"),
    ])
    assert [r["race_id"] for r in races] == [
        "202608094611", "202608104611", "202608104808",
    ]


# ---------------------------------------------------------------------------
# リードタイム
# ---------------------------------------------------------------------------

def test_リードタイムは時間帯別に積み上がる():
    races = build_races([
        _race_row("202608104801", avg_sold_hour=3.0, meeting_type="day", sold_paid_points=600),
        _race_row("202608104802", avg_sold_hour=3.0, meeting_type="day", sold_paid_points=400),
        _race_row("202608104803", avg_sold_hour=3.0, meeting_type="nighter", sold_paid_points=100),
        _race_row("202608104804", avg_sold_hour=8.0, meeting_type="midnight", sold_paid_points=50),
    ])
    buckets = build_leadtime_buckets(races)
    assert buckets == [
        {"lead_hours": 3, "day": 1000, "nighter": 100},
        {"lead_hours": 8, "midnight": 50},
    ]


def test_開催時間帯不明はunknownに積む():
    """勝手にどれかの時間帯へ倒すと、実際と違う色が付いて誤読の元になる。"""
    races = build_races([_race_row("202608104801", meeting_type=None, sold_paid_points=700)])
    assert build_leadtime_buckets(races) == [{"lead_hours": 3, "unknown": 700}]


def test_リードタイム不明のレースはバケットから除く():
    races = build_races([_race_row("202608104801", avg_sold_hour=None)])
    assert build_leadtime_buckets(races) == []


# ---------------------------------------------------------------------------
# ランク別
# ---------------------------------------------------------------------------

def test_ランク未判明のレースも未入稿として残る():
    """picks_history に無いレースを落とすと売上合計が netkeirin の実績と合わなくなる。"""
    races = build_races([
        _race_row("202608104801", rank="7S", sold_paid_points=600),
        _race_row("202608104802", rank=None, sold_paid_points=900),
    ])
    by_rank = build_rank_breakdown(races)
    assert {b["rank"] for b in by_rank} == {"7S", "未入稿"}
    assert sum(b["sold_paid_points"] for b in by_rank) == 1500
    # 売上pt の大きい順
    assert by_rank[0]["rank"] == "未入稿"


def test_ランク別ガミ率は的中に対する割合():
    races = build_races([
        _race_row("202608104801", rank="7A", n_hits_incl_garami=1, n_hits_excl_garami=0),
        _race_row("202608104802", rank="7A", n_hits_incl_garami=1, n_hits_excl_garami=1),
        _race_row("202608104803", rank="7A", n_hits_incl_garami=0, n_hits_excl_garami=0),
    ])
    a = build_rank_breakdown(races)[0]
    assert a["n_races"] == 3
    assert a["n_hits"] == 2
    assert a["garami_rate"] == pytest.approx(0.5)
    assert a["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)  # 率は小数4桁に丸めて返す


# ---------------------------------------------------------------------------
# 出自別（看板レースの穴埋めとゲート通過の分離）
# ---------------------------------------------------------------------------

def test_出自別に分けるとランク別では見えない差が出る():
    """🔴 この関数が存在する理由そのもの。

    看板の穴埋めは `RANK_BY_CARS={7:"7A",9:"9A"}` により 7A を名乗って入稿される。
    ランクで割ると「7A は売れるのに当たらない」に見えるが、それは
    ランクの性質ではなく経路の混在。
    """
    races = build_races([
        # ゲートを通った 7A：当たる
        _race_row("202608104801", rank="7A", origin="rank",
                  n_hits_incl_garami=1, n_hits_excl_garami=1, sold_paid_points=600),
        # 同じ 7A を名乗る穴埋め：当たらないが売れる
        _race_row("202608104802", rank="7A", origin="marquee_fill",
                  n_hits_incl_garami=0, n_hits_excl_garami=0, sold_paid_points=3000),
        _race_row("202608104803", rank="7A", origin="marquee_fill",
                  n_hits_incl_garami=0, n_hits_excl_garami=0, sold_paid_points=6400),
    ])
    by_origin = {b["origin"]: b for b in build_origin_breakdown(races)}
    assert by_origin["rank"]["hit_rate"] == 1.0
    assert by_origin["marquee_fill"]["hit_rate"] == 0.0
    # 売上シェアは穴埋めが圧倒的（現実と同じ構図）
    assert by_origin["marquee_fill"]["sales_share"] == pytest.approx(9400 / 10000)
    assert by_origin["rank"]["sales_share"] == pytest.approx(600 / 10000)

    # ランク別に畳むと差が消える（＝畳んだ数字だけ見てはいけない）
    (rank_row,) = build_rank_breakdown(races)
    assert rank_row["rank"] == "7A"
    assert rank_row["hit_rate"] == pytest.approx(1 / 3, abs=1e-4)


def test_ランク別は出自の内訳を持つ():
    races = build_races([
        _race_row("202608104801", rank="7A", origin="rank"),
        _race_row("202608104802", rank="7A", origin="marquee_fill"),
    ])
    (row,) = build_rank_breakdown(races)
    assert [b["origin"] for b in row["by_origin"]] == ["rank", "marquee_fill"]
    assert sum(b["n_races"] for b in row["by_origin"]) == row["n_races"]


def test_出自の並びは朝令暮改しない():
    """系列が減っても順序が動かないこと（rank → marquee_fill → manual → unknown）。"""
    races = build_races([
        _race_row("202608104804", origin="unknown"),
        _race_row("202608104803", origin="manual"),
        _race_row("202608104802", origin="marquee_fill"),
        _race_row("202608104801", origin="rank"),
    ])
    assert [b["origin"] for b in build_origin_breakdown(races)] == [
        "rank", "marquee_fill", "manual", "unknown",
    ]


def test_入稿記録が無いレースはrankに混ぜない():
    """混ぜるとゲート通過分の成績が薄まる。この画面が分けたかったものそのもの。"""
    (r,) = build_races([_race_row("202608104801", origin=None)])
    assert r["origin"] == "unknown"
    assert build_origin_breakdown([r])[0]["origin"] == "unknown"


def test_未知の出自が来ても落とさない():
    """値が増えたときに黙って消えると売上の合計が合わなくなる。"""
    races = build_races([
        _race_row("202608104801", origin="rank", sold_paid_points=100),
        _race_row("202608104802", origin="future_kind", sold_paid_points=900),
    ])
    out = build_origin_breakdown(races)
    assert [b["origin"] for b in out] == ["rank", "future_kind"]  # 未知は末尾
    assert sum(b["sold_paid_points"] for b in out) == 1000


# ---------------------------------------------------------------------------
# 経路（出自 × 候補の有無）
# ---------------------------------------------------------------------------

def test_名義違いと真の穴埋めを分ける():
    """🔴 origin だけでは失敗モードが2つ混ざる（2026-08-11 に実際に誤読した）。

    - 候補が立っていたのに別ランク名義で入稿 → `renamed`（ランクの付け替えで直る）
    - 候補が一切ないレースへ出した          → `no_candidate`（出すかの判断そのもの）
    """
    assert classify_route("rank", None) == "gate"
    assert classify_route("rank", "7C") == "gate"
    assert classify_route("marquee_fill", "7C") == "renamed"
    assert classify_route("marquee_fill", None) == "no_candidate"
    assert classify_route("manual", "7B,7C") == "renamed"
    assert classify_route("manual", None) == "no_candidate"
    assert classify_route(None, "7C") == "unknown"
    assert classify_route("unknown", None) == "unknown"


def test_候補がありさえすれば名義が違っても検出できる():
    """候補ランクと入稿ランクを**等値比較してはいけない**。
    穴埋めは 7A を名乗るので、7C 候補のレースが「候補なし」に見えてしまう。"""
    (r,) = build_races([
        _race_row("202608104801", rank="7A", origin="marquee_fill", detected_ranks="7C"),
    ])
    assert r["route"] == "renamed"
    assert r["detected_ranks"] == "7C"


def test_経路別の並びと合計():
    races = build_races([
        _race_row("202608104801", origin="rank", sold_paid_points=100),
        _race_row("202608104802", origin="marquee_fill", detected_ranks="7C",
                  sold_paid_points=200),
        _race_row("202608104803", origin="marquee_fill", sold_paid_points=300),
        _race_row("202608104804", origin=None, sold_paid_points=400),
    ])
    out = build_route_breakdown(races)
    assert [b["route"] for b in out] == ["gate", "renamed", "no_candidate", "unknown"]
    assert [b["sold_paid_points"] for b in out] == [100, 200, 300, 400]
    assert sum(b["n_races"] for b in out) == len(races)
    assert out[1]["sales_share"] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# 相関・リンク検証・サマリー
# ---------------------------------------------------------------------------

def test_相関は日別とレース別の両方を返す():
    daily = build_daily([
        _daily_row(f"2026080{i}", n_predictions=i * 2,
                   n_hits_incl_garami=i, sold_paid_points=i * 100, n_sold=i * 5)
        for i in range(1, 8)
    ])
    races = build_races([_race_row(f"2026080{i}4801") for i in range(1, 8)])
    corr = build_correlations(daily, races)
    assert set(corr) == {
        "n_races_x_hit_rate", "n_races_x_n_sold", "n_races_x_sales",
        "hit_rate_x_sales", "race_sales_x_hit", "race_buyers_x_hit",
    }
    # 予想数と販売数を比例させたので +1
    assert corr["n_races_x_n_sold"] == 1.0


def test_リンク検証は直近3日平均と比べる():
    daily = build_daily([
        _daily_row("20260806", n_predictions=10, n_sold=20, sold_paid_points=1000),
        _daily_row("20260807", n_predictions=10, n_sold=20, sold_paid_points=1000),
        _daily_row("20260808", n_predictions=10, n_sold=20, sold_paid_points=1000),
        _daily_row("20260809", n_predictions=20, n_sold=30, sold_paid_points=1500),
    ])
    check = build_link_check(daily)
    assert check is not None
    assert check["date"] == "2026-08-09"
    assert check["baseline_from"] == "2026-08-06"
    assert check["baseline_to"] == "2026-08-08"
    assert check["metrics"]["n_predictions"]["delta_ratio"] == pytest.approx(1.0)
    assert check["linked"] is True


def test_比較対象が足りなければリンク検証は出さない():
    daily = build_daily([_daily_row("20260808"), _daily_row("20260809")])
    assert build_link_check(daily) is None


def test_サマリーは前日比を持ち初日はNone():
    daily = build_daily([
        _daily_row("20260808", sold_paid_points=5060),
        _daily_row("20260809", sold_paid_points=7540),
    ])
    s = build_summary(daily, [])
    assert s["latest"]["date"] == "2026-08-09"
    assert s["latest"]["delta"]["sold_paid_points"] == 2480

    only_one = build_summary(build_daily([_daily_row("20260809")]), [])
    assert only_one["latest"]["delta"] is None


def test_サマリーは有償ptと総販売ptを取り違えない():
    """収益になるのは有償ptだけ。合計で取り違えると売上金額が丸ごとずれる。"""
    s = build_summary(build_daily([_daily_row("20260809", sold_points=11400,
                                              sold_paid_points=7540)]), [])
    assert s["sold_points"] == 11400
    assert s["sold_paid_points"] == 7540


def test_空期間でも落ちない():
    s = build_summary([], [])
    assert s["n_days"] == 0
    assert s["latest"] is None
    assert build_correlations([], []) == {
        k: None for k in (
            "n_races_x_hit_rate", "n_races_x_n_sold", "n_races_x_sales",
            "hit_rate_x_sales", "race_sales_x_hit", "race_buyers_x_hit")
    }
