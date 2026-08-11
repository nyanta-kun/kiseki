"""netkeirin「ウマい車券」売上×成績の相関分析（2026-08-11 新設）。

`/api/keirin/netkeirin-analysis` の計算本体。SQL から取れた素の行を受け取り、
表示に必要な集計だけを返す **純関数の集まり**（DB にも FastAPI にも依存しない）。
分けてあるのは、相関や「ガミ」の定義が読み違えやすく、検査で固定したいため
（`backend/tests/test_keirin_sales_analysis.py`）。

用語（サイトの列名に合わせる。取り違えると数字が別物になる）:

- **的中(ガミ含む)** `n_hits_incl_garami` … 買い目が当たった。払戻＜賭け金でも的中。
- **的中(ガミ除く)** `n_hits_excl_garami` … そのうち払戻＞賭け金だったもの。
  netkeirin のプロフィールに出る「的中率」はこちら。
- **ガミ** … 差分。「当たったのに賭け金割れ」。
- **売上pt** `sold_paid_points`（販売**有償**pt）… 予想家の取り分の対象。
  `sold_points`（総販売pt）には無償pt が含まれ収益にならないので、
  金額の話をするときは必ず有償ptを使う（keirin_router.NETKEIRIN_REVENUE_RATE と同じ立場）。

⚠️ **相関・タイムラインの「的中」は ガミ含む で数える。** 買った人から見て
   当たったかどうかが売上に効く、という仮説を見るための図だから。
   一方サマリーの「的中率」はサイト表示と揃える必要があるので ガミ除く。
   **どちらか一方に統一してはいけない**（どちらも意味のある別の量）。
"""
from __future__ import annotations

from math import sqrt
from typing import Any

# 相関係数を出すのに最低限必要な標本数。これ未満は None を返す
# （2〜3点の相関は必ず ±1 付近に出て、読み手を確実に誤らせる）。
MIN_SAMPLES_FOR_CORRELATION = 5

# 入稿の出自（`keirin.netkeirin_submissions.origin` / migration 202608111930_keirin）。
# 🔴 **ランク別の集計だけでは経路を分けられない。** 看板レースの穴埋め入稿は
#    keirin `submit_marquee_wt.py` の `RANK_BY_CARS={7:"7A",9:"9A"}` により
#    7A/9A を名乗るため、`rank` に両方が混ざる（実測で 7A の94%が穴埋め）。
ORIGIN_RANK = "rank"
ORIGIN_MARQUEE_FILL = "marquee_fill"
ORIGIN_MANUAL = "manual"
# 入稿記録そのものが無いレース。origin の値ではなく「結合できなかった」印。
ORIGIN_UNKNOWN = "unknown"
# 表示順。売上の主役である穴埋めを2番目に置く。
ORIGIN_ORDER = [ORIGIN_RANK, ORIGIN_MARQUEE_FILL, ORIGIN_MANUAL, ORIGIN_UNKNOWN]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """ピアソンの積率相関係数。標本不足・分散ゼロなら None。"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < MIN_SAMPLES_FOR_CORRELATION:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx <= 0 or syy <= 0:
        return None  # 片方が定数（例: 毎日ぴったり同じ予想数）なら相関は定義できない
    return round(sxy / sqrt(sxx * syy), 3)


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def build_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """日別行を表示用に整える（率は0〜1の小数で返し、%への変換は表示側に任せる）。"""
    out: list[dict[str, Any]] = []
    for r in rows:
        sd = str(r["sale_date"])
        n_pred = int(r.get("n_predictions") or 0)
        hits_incl = int(r.get("n_hits_incl_garami") or 0)
        hits_excl = int(r.get("n_hits_excl_garami") or 0)
        stake = int(r.get("stake_amount") or 0)
        payout = int(r.get("payout_amount") or 0)
        out.append({
            "date": f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}",
            "n_predictions": n_pred,
            "n_hits_incl_garami": hits_incl,
            "n_hits_excl_garami": hits_excl,
            "n_garami": hits_incl - hits_excl,
            "hit_rate_incl": _rate(hits_incl, n_pred),
            "hit_rate_excl": _rate(hits_excl, n_pred),
            # 的中のうちガミだった割合。的中0件の日は「ガミ率0%」ではなく未定義。
            "garami_rate": _rate(hits_incl - hits_excl, hits_incl),
            "n_sold": int(r.get("n_sold") or 0),
            "sold_points": int(r.get("sold_points") or 0),
            "sold_paid_points": int(r.get("sold_paid_points") or 0),
            "stake_amount": stake,
            "payout_amount": payout,
            "recovery_rate": _rate(payout, stake),
        })
    return out


def build_races(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """レース別行を表示用に整える。

    `meeting_type` は SQL 側で wt_races から算出済みの値をそのまま通す
    （判定の正本は `api/keirin_meeting.py`）。取れない開催は None のまま返し、
    表示側で「未登録」として区別できるようにする。
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        rd = str(r["race_date"])
        hits_incl = int(r.get("n_hits_incl_garami") or 0)
        hits_excl = int(r.get("n_hits_excl_garami") or 0)
        stake = int(r.get("stake_amount") or 0)
        payout = int(r.get("payout_amount") or 0)
        out.append({
            "race_id": r["race_id"],
            "race_key": r["race_key"],
            "date": f"{rd[0:4]}-{rd[4:6]}-{rd[6:8]}",
            "venue_code": r["venue_code"],
            "venue_name": r.get("venue_name"),
            "race_no": int(r["race_no"]),
            "label": r.get("race_label"),
            "rank": r.get("rank"),
            # 入稿記録が無いレースは「rank と決めつけない」。混ぜるとゲート通過分の
            # 成績が薄まる（この画面が分けたかったものそのもの）。
            "origin": r.get("origin") or ORIGIN_UNKNOWN,
            "meeting_type": r.get("meeting_type"),
            "hit": hits_incl > 0,
            "hit_excl_garami": hits_excl > 0,
            "is_garami": hits_incl > 0 and hits_excl == 0,
            "n_sold": int(r.get("n_sold") or 0),
            "sold_points": int(r.get("sold_points") or 0),
            "sold_paid_points": int(r.get("sold_paid_points") or 0),
            "stake_amount": stake,
            "payout_amount": payout,
            "recovery_rate": _rate(payout, stake),
            # 締切の何時間前に売れたか（0=締切直前・大きいほど先行購入）
            "lead_hours": r.get("avg_sold_hour"),
            "lead_minutes": r.get("avg_sold_minutes"),
        })
    out.sort(key=lambda x: (x["date"], x["race_id"]))
    return out


def build_correlations(daily: list[dict[str, Any]], races: list[dict[str, Any]]) -> dict[str, float | None]:
    """PDF ダッシュボードと同じ6本の相関係数を返す。

    日別4本は「量（予想レース数）を増やすと精度・販売はどう動くか」、
    レース別2本は「売れたレースほど当たっているか」を見るためのもの。
    """
    n_pred = [d["n_predictions"] for d in daily]
    hit_rate = [d["hit_rate_incl"] for d in daily]
    n_sold = [float(d["n_sold"]) for d in daily]
    sales = [float(d["sold_paid_points"]) for d in daily]

    # レース別は的中を 0/1 に落とした点双列相関（ピアソンの特殊形なので同じ式でよい）。
    race_sales = [float(r["sold_paid_points"]) for r in races]
    race_buyers = [float(r["n_sold"]) for r in races]
    race_hit = [1.0 if r["hit"] else 0.0 for r in races]

    return {
        "n_races_x_hit_rate": pearson(n_pred, hit_rate),
        "n_races_x_n_sold": pearson(n_pred, n_sold),
        "n_races_x_sales": pearson(n_pred, sales),
        "hit_rate_x_sales": pearson(hit_rate, sales),
        "race_sales_x_hit": pearson(race_sales, race_hit),
        "race_buyers_x_hit": pearson(race_buyers, race_hit),
    }


def build_link_check(daily: list[dict[str, Any]], recent_days: int = 3) -> dict[str, Any] | None:
    """「レース数増 → 販売数増 → 売上増」の当日検証。

    最新日を直近 `recent_days` 日の平均と比べる。比較対象が足りなければ None
    （1日しか無いのに「+100%」と出すよりは出さない方がよい）。
    """
    if len(daily) < recent_days + 1:
        return None
    latest = daily[-1]
    baseline = daily[-(recent_days + 1):-1]

    def _avg(key: str) -> float:
        return sum(float(d[key]) for d in baseline) / len(baseline)

    metrics: dict[str, Any] = {}
    for key in ("n_predictions", "n_sold", "sold_paid_points"):
        avg = _avg(key)
        latest_val = float(latest[key])
        metrics[key] = {
            "latest": latest_val,
            "recent_avg": round(avg, 1),
            # 直近平均が0だと比率が定義できない（増えたことは分かるが「何%増」は言えない）
            "delta_ratio": round(latest_val / avg - 1, 3) if avg > 0 else None,
        }
    return {
        "date": latest["date"],
        "recent_days": recent_days,
        "baseline_from": baseline[0]["date"],
        "baseline_to": baseline[-1]["date"],
        "metrics": metrics,
        # 3指標すべてが直近平均を上回ったら「狙いどおり」。
        "linked": all(m["latest"] > m["recent_avg"] for m in metrics.values()),
    }


def build_leadtime_buckets(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """締切までのリードタイム（時間）× 開催時間帯 の売上pt積み上げ。

    `lead_hours` = サイトの「平均販売時」。0 が締切直前で、大きいほど早い先行購入。
    開催時間帯が取れないレースは `unknown` に積む（勝手にどれかへ倒さない）。
    """
    buckets: dict[int, dict[str, int]] = {}
    for r in races:
        lead = r.get("lead_hours")
        if lead is None:
            continue
        h = int(lead)
        key = r.get("meeting_type") or "unknown"
        b = buckets.setdefault(h, {})
        b[key] = b.get(key, 0) + int(r["sold_paid_points"])
    return [
        {"lead_hours": h, **{k: v for k, v in sorted(buckets[h].items())}}
        for h in sorted(buckets)
    ]


def _empty_bucket(key_name: str, key_value: str) -> dict[str, Any]:
    return {
        key_name: key_value, "n_races": 0, "n_hits": 0, "n_garami": 0,
        "n_sold": 0, "sold_paid_points": 0, "stake_amount": 0, "payout_amount": 0,
    }


def _accumulate(bucket: dict[str, Any], r: dict[str, Any]) -> None:
    bucket["n_races"] += 1
    bucket["n_hits"] += 1 if r["hit"] else 0
    bucket["n_garami"] += 1 if r["is_garami"] else 0
    bucket["n_sold"] += r["n_sold"]
    bucket["sold_paid_points"] += r["sold_paid_points"]
    bucket["stake_amount"] += r["stake_amount"]
    bucket["payout_amount"] += r["payout_amount"]


def _finalize(bucket: dict[str, Any], total_sales: int) -> dict[str, Any]:
    bucket["hit_rate"] = _rate(bucket["n_hits"], bucket["n_races"])
    bucket["garami_rate"] = _rate(bucket["n_garami"], bucket["n_hits"])
    bucket["recovery_rate"] = _rate(bucket["payout_amount"], bucket["stake_amount"])
    # 売上シェア。「どれだけ当たるか」より「どれだけ売れているか」が
    # 商品ミックスの判断材料になるため必ず添える。
    bucket["sales_share"] = _rate(bucket["sold_paid_points"], total_sales)
    return bucket


def build_origin_breakdown(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """入稿の**出自**別の売上・的中（2026-08-11 新設）。

    ランク別（`build_rank_breakdown`）では見えない断面。看板レースの穴埋めは
    7A/9A を名乗って入稿されるため、ランクで割ると
    「7A は売れるのに当たらない」という**ランクの性質ではない結論**が出る。

    実測 2026-08-01〜08-10:
        ゲート通過 107R / 売上25.4% / 表示的中29.0% / 回収0.702
        穴埋め      87R / 売上**71.2%** / 表示的中14.9% / 回収0.333
    """
    total_sales = sum(r["sold_paid_points"] for r in races)
    agg: dict[str, dict[str, Any]] = {}
    for r in races:
        key = r.get("origin") or ORIGIN_UNKNOWN
        _accumulate(agg.setdefault(key, _empty_bucket("origin", key)), r)
    order = {k: i for i, k in enumerate(ORIGIN_ORDER)}
    return sorted(
        (_finalize(b, total_sales) for b in agg.values()),
        # 既知の出自は定義順、未知の値が増えても末尾に落として消さない。
        key=lambda x: (order.get(x["origin"], len(ORIGIN_ORDER)), x["origin"]),
    )


def build_rank_breakdown(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """kiseki の入稿ランク別の売上・的中・ガミ。

    netkeirin 側にランクの概念は無いので、入稿記録と結合して初めて作れる断面。
    ランク未判明（＝入稿記録が無いレース）も `未入稿` として必ず残す。

    ⚠️ **ランク単位の行だけを読んではいけない。** 7A/9A にはゲート通過分と
       看板レースの穴埋めが混ざる（実測で 7A の94%が穴埋め）。各行は
       `by_origin` に出自別の内訳を持つので、そちらまで見ること。
       全体の経路比較は `build_origin_breakdown()`。
    """
    total_sales = sum(r["sold_paid_points"] for r in races)
    agg: dict[str, dict[str, Any]] = {}
    sub: dict[tuple[str, str], dict[str, Any]] = {}
    for r in races:
        key = r.get("rank") or "未入稿"
        _accumulate(agg.setdefault(key, _empty_bucket("rank", key)), r)
        origin = r.get("origin") or ORIGIN_UNKNOWN
        _accumulate(sub.setdefault((key, origin), _empty_bucket("origin", origin)), r)

    order = {k: i for i, k in enumerate(ORIGIN_ORDER)}
    out = []
    for rank_key, a in agg.items():
        a["by_origin"] = sorted(
            (_finalize(b, total_sales) for (rk, _o), b in sub.items() if rk == rank_key),
            key=lambda x: (order.get(x["origin"], len(ORIGIN_ORDER)), x["origin"]),
        )
        out.append(_finalize(a, total_sales))
    out.sort(key=lambda x: (-x["sold_paid_points"], x["rank"]))
    return out


def build_summary(daily: list[dict[str, Any]], races: list[dict[str, Any]]) -> dict[str, Any]:
    """期間合計と、最新日＋前日比。"""
    def _sum(key: str) -> int:
        return sum(int(d[key]) for d in daily)

    total_pred = _sum("n_predictions")
    total_incl = _sum("n_hits_incl_garami")
    total_excl = _sum("n_hits_excl_garami")
    total_stake = _sum("stake_amount")
    total_payout = _sum("payout_amount")

    latest = daily[-1] if daily else None
    prev = daily[-2] if len(daily) >= 2 else None
    latest_block = None
    if latest:
        latest_block = {
            **latest,
            "delta": {
                key: (latest[key] - prev[key]) if prev else None
                for key in ("sold_paid_points", "sold_points", "n_sold", "n_predictions")
            } if prev else None,
        }

    return {
        "n_days": len(daily),
        "n_races": len(races),
        "n_predictions": total_pred,
        "n_hits_incl_garami": total_incl,
        "n_hits_excl_garami": total_excl,
        "n_garami": total_incl - total_excl,
        "hit_rate_incl": _rate(total_incl, total_pred),
        "hit_rate_excl": _rate(total_excl, total_pred),
        "garami_rate": _rate(total_incl - total_excl, total_incl),
        "n_sold": _sum("n_sold"),
        "sold_points": _sum("sold_points"),
        "sold_paid_points": _sum("sold_paid_points"),
        "stake_amount": total_stake,
        "payout_amount": total_payout,
        "recovery_rate": _rate(total_payout, total_stake),
        "latest": latest_block,
    }
