"""型ラボ（`keirin.type_lab_picks`）の表示確認用 API（2026-08-27 新設）。

🔴 **既存の keirin API とは完全に独立**。`picks_history` も `netkeirin_submissions` も
   読まないので、既存の一覧・統計・売上集計に一切影響しない。
   型ラボは「既存商品の全面置き換え」を前提にした検証中の設計で、
   ペーパー検証（過去）と実地検証（当日）を混ぜずに見るための窓口。

設計と実測: `keirin/docs/type_lab/SUMMARY.md`
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db

router = APIRouter(prefix="/api/keirin/type-lab", tags=["keirin-type-lab"])

#: 表示順。`keirin/src/type_lab.PLANS` と揃える（片方だけ増やしても落ちないが並びが崩れる）。
PLAN_ORDER = ["A_hit", "A_pay", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_pay"]


class TypeLabLeg(BaseModel):
    combo: str
    stake: int
    pred_odds: float
    prob: float


class TypeLabPick(BaseModel):
    race_key: str
    race_date: str
    venue_name: str | None = None
    race_no: int | None = None
    race_type: str | None = None
    day_index: int | None = None
    type_label: str
    axis_sum: float | None = None
    arare: int | None = None
    axis1: int | None = None
    axis2: int | None = None
    mode: str
    plan_key: str
    bet_type: str
    n_legs: int
    budget: int
    legs: list[TypeLabLeg]
    pred_mean_payout: float | None = None
    pred_min_payout: float | None = None
    settled: bool
    win_combo: str | None = None
    hit: bool | None = None
    payout: int | None = None
    final_odds: float | None = None
    current: CurrentPick | None = None


class CurrentPick(BaseModel):
    """同じレースに対する**現行の推奨**。

    🔴 `picks_history` は**ランクの候補**であって「売った商品」ではない
       （実売との不一致は 18%・[[keirin_sold_source_of_truth_2026_08_25]]）。
       ただし型ラボも「設計が何を買うか」なので、**設計どうしの比較としては
       これが同じ土俵**。実際に売れたかどうかは `sold_rank_key` で別に示す。
    ⚠️ `netkeirin_submissions`（売った商品）は 2026-07-24 以降しか無いので、
       ペーパー検証の長い窓では `picks_history` しか使えない。
    """
    rank: str                     # 'RANK_7S' など（現行の優先順位で最上位のもの）
    pred_combo: str | None = None
    n_combos: int | None = None
    bet_amount: int | None = None
    hit: bool | None = None
    payout: int | None = None
    settled: bool = False
    sold_rank_key: str | None = None      # 実際に入稿された rank_key（あれば）


class ComparisonRow(BaseModel):
    """型ラボのプラン と 現行推奨 を**同じレース集合**で比べた行。"""
    plan_key: str
    n_races: int                  # 両方に採点済みの記録があるレース数
    lab_shown_hit: float
    cur_shown_hit: float
    lab_median_payout: float
    cur_median_payout: float
    lab_two_per_day: float
    cur_two_per_day: float
    lab_roi: float
    cur_roi: float
    n_days: int


class TypeLabSummary(BaseModel):
    """プラン別のまとめ。**判断指標だけ**を返す（ROI は参考）。"""
    plan_key: str
    type_label: str
    bet_type: str
    n: int
    n_days: int
    per_day: float
    n_settled: int
    n_hit: int
    n_gami: int
    hit_rate: float           # 生の的中率（%）
    shown_hit_rate: float     # 表示的中（ガミ除く・%）
    gami_rate: float
    median_payout: float
    median_pred_mean: float
    two_plus_per_day: float   # 2倍以上の的中 件/日
    big_per_day: float        # 10万円以上 件/日
    invested: int
    returned: int
    roi: float


class TypeLabResponse(BaseModel):
    mode: str
    date_from: str
    date_to: str
    rule_versions: list[str]
    summaries: list[TypeLabSummary]
    comparison: list[ComparisonRow]
    picks: list[TypeLabPick]


_SQL = text("""
    SELECT race_key, race_date, venue_name, race_no, race_type, day_index,
           type_label, axis_sum, arare, axis1, axis2, mode, plan_key, bet_type,
           n_legs, budget, legs, pred_mean_payout, pred_min_payout,
           settled_at, win_combo, hit, payout, final_odds, rule_version
    FROM keirin.type_lab_picks
    WHERE mode = :mode AND race_date BETWEEN :d1 AND :d2
    ORDER BY race_date DESC, race_key, plan_key
""")


#: 現行の入稿優先順位（`keirin/src/netkeirin_submit_wt.RANK_ORDER` と同じ並び）。
#: 1レースに複数ランクの候補があるとき、**実際に売られるのは最上位の1つだけ**なので
#: 比較でもそれに揃える。
#: 🔴 向こうを変えたらここも変えること（手書きリスト）。
CURRENT_RANK_ORDER = ["RANK_7H2", "RANK_9H1", "RANK_7T1", "RANK_7T3", "RANK_7S",
                      "RANK_9C", "RANK_7B", "RANK_7C", "RANK_7H1", "RANK_7M1"]

_SQL_CURRENT = text("""
    SELECT split_part(race_key, '#', 1) AS rk, rank, pred_combo, n_combos,
           hit, payout, bet_amount
    FROM keirin.picks_history
    WHERE race_date BETWEEN :d1 AND :d2
""")

_SQL_SOLD = text("""
    SELECT split_part(race_key, '#', 1) AS rk, rank_key
    FROM keirin.netkeirin_submissions
    WHERE substring(race_key, 1, 8) BETWEEN :d1 AND :d2
      AND status <> 'deleted'
""")


def _median(v: list[float]) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    n = len(s)
    return float(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def _rank_pos(rank: str) -> int:
    return (CURRENT_RANK_ORDER.index(rank) if rank in CURRENT_RANK_ORDER
            else len(CURRENT_RANK_ORDER))


@router.get("", response_model=TypeLabResponse)
async def get_type_lab(
    mode: Literal["paper", "live"] = "live",
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
) -> TypeLabResponse:
    """型ラボの買い目と、プラン別のまとめ。

    既定は直近7日の実地（`mode=live`）。ペーパー検証は `mode=paper` と期間を指定する。
    """
    d2 = date_to or date.today().isoformat()
    d1 = date_from or (date.fromisoformat(d2) - timedelta(days=6)).isoformat()

    res = await db.execute(_SQL, {"mode": mode, "d1": d1, "d2": d2})
    rows = [dict(r._mapping) for r in res]

    # 同じ期間の現行推奨。1レースに複数ランクがあれば優先順位の最上位を採る。
    cur_res = await db.execute(_SQL_CURRENT, {"d1": d1, "d2": d2})
    current: dict[str, dict[str, Any]] = {}
    for c in (dict(r._mapping) for r in cur_res):
        prev = current.get(c["rk"])
        if prev is None or _rank_pos(c["rank"]) < _rank_pos(prev["rank"]):
            current[c["rk"]] = c
    sold_res = await db.execute(_SQL_SOLD,
                                {"d1": d1.replace("-", ""), "d2": d2.replace("-", "")})
    sold = {r._mapping["rk"]: r._mapping["rank_key"] for r in sold_res}

    picks: list[TypeLabPick] = []
    by_plan: dict[str, list[dict[str, Any]]] = {}
    versions: set[str] = set()
    for r in rows:
        legs_raw = r["legs"]
        legs = json.loads(legs_raw) if isinstance(legs_raw, str) else (legs_raw or [])
        versions.add(str(r["rule_version"]))
        by_plan.setdefault(r["plan_key"], []).append(r)
        if len(picks) < limit:
            picks.append(TypeLabPick(
                race_key=r["race_key"], race_date=str(r["race_date"]),
                venue_name=r["venue_name"], race_no=r["race_no"],
                race_type=r["race_type"], day_index=r["day_index"],
                type_label=r["type_label"],
                axis_sum=float(r["axis_sum"]) if r["axis_sum"] is not None else None,
                arare=r["arare"], axis1=r["axis1"], axis2=r["axis2"],
                mode=r["mode"], plan_key=r["plan_key"], bet_type=r["bet_type"],
                n_legs=r["n_legs"], budget=r["budget"],
                legs=[TypeLabLeg(**leg) for leg in legs],
                pred_mean_payout=(float(r["pred_mean_payout"])
                                  if r["pred_mean_payout"] is not None else None),
                pred_min_payout=(float(r["pred_min_payout"])
                                 if r["pred_min_payout"] is not None else None),
                settled=r["settled_at"] is not None, win_combo=r["win_combo"],
                hit=(bool(r["hit"]) if r["hit"] is not None else None),
                payout=r["payout"],
                final_odds=(float(r["final_odds"]) if r["final_odds"] is not None else None),
                current=_current_of(current.get(r["race_key"]), sold.get(r["race_key"])),
            ))

    summaries: list[TypeLabSummary] = []
    for plan in sorted(by_plan, key=lambda p: (PLAN_ORDER.index(p)
                                               if p in PLAN_ORDER else 99, p)):
        g = by_plan[plan]
        days = {str(x["race_date"]) for x in g}
        st = [x for x in g if x["settled_at"] is not None]
        hits = [x for x in st if x["hit"]]
        gami = [x for x in hits if (x["payout"] or 0) < x["budget"]]
        inv = sum(int(x["budget"]) for x in st)
        ret = sum(int(x["payout"] or 0) for x in st)
        two = [x for x in hits if (x["payout"] or 0) >= 2 * int(x["budget"])]
        big = [x for x in hits if (x["payout"] or 0) >= 100_000]
        nd = max(len(days), 1)
        summaries.append(TypeLabSummary(
            plan_key=plan, type_label=g[0]["type_label"], bet_type=g[0]["bet_type"],
            n=len(g), n_days=len(days), per_day=round(len(g) / nd, 2),
            n_settled=len(st), n_hit=len(hits), n_gami=len(gami),
            hit_rate=round(len(hits) / len(st) * 100, 2) if st else 0.0,
            shown_hit_rate=round((len(hits) - len(gami)) / len(st) * 100, 2) if st else 0.0,
            gami_rate=round(len(gami) / len(hits) * 100, 2) if hits else 0.0,
            median_payout=round(_median([float(x["payout"] or 0) for x in hits]), 0),
            median_pred_mean=round(_median([float(x["pred_mean_payout"] or 0) for x in g]), 0),
            two_plus_per_day=round(len(two) / nd, 3),
            big_per_day=round(len(big) / nd, 3),
            invested=inv, returned=ret,
            roi=round(ret / inv * 100, 1) if inv else 0.0,
        ))

    return TypeLabResponse(mode=mode, date_from=d1, date_to=d2,
                           rule_versions=sorted(versions),
                           summaries=summaries,
                           comparison=_comparison(by_plan, current),
                           picks=picks)


def _current_of(c: dict[str, Any] | None, sold_rank: str | None) -> CurrentPick | None:
    if c is None:
        return None
    # `picks_history.hit` は当日中は未採点（確定は翌朝 08:40）なので、
    # bet_amount が 0 の行は「まだ採点されていない」として扱う。
    settled = bool(c.get("bet_amount"))
    return CurrentPick(
        rank=c["rank"], pred_combo=c.get("pred_combo"), n_combos=c.get("n_combos"),
        bet_amount=c.get("bet_amount"),
        hit=(bool(c["hit"]) if settled and c.get("hit") is not None else None),
        payout=c.get("payout"), settled=settled, sold_rank_key=sold_rank,
    )


def _comparison(by_plan: dict[str, list[dict[str, Any]]],
                current: dict[str, dict[str, Any]]) -> list[ComparisonRow]:
    """プランごとに、**両方に採点済みの記録があるレースだけ**で並べて比べる。

    🔴 母集団を揃えないと比較にならない（CLAUDE.md「検証の作法」#3）。
       型ラボが出していないレースや、現行に候補が無いレースは両方から外す。
    """
    out: list[ComparisonRow] = []
    for plan in sorted(by_plan, key=lambda x: (PLAN_ORDER.index(x)
                                               if x in PLAN_ORDER else 99, x)):
        pairs = []
        for g in by_plan[plan]:
            if g["settled_at"] is None:
                continue
            c = current.get(g["race_key"])
            if not c or not c.get("bet_amount"):
                continue
            pairs.append((g, c))
        if not pairs:
            continue
        days = {str(g["race_date"]) for g, _ in pairs}
        nd = max(len(days), 1)

        def _side(get_pay, get_inv, get_hit):
            hits = [p for p in pairs if get_hit(p)]
            shown = [p for p in hits if get_pay(p) >= get_inv(p)]
            two = [p for p in hits if get_pay(p) >= 2 * get_inv(p)]
            inv = sum(get_inv(p) for p in pairs)
            ret = sum(get_pay(p) for p in pairs)
            return (len(shown) / len(pairs) * 100,
                    _median([float(get_pay(p)) for p in hits]),
                    len(two) / nd, (ret / inv * 100) if inv else 0.0)

        lab = _side(lambda p: float(p[0]["payout"] or 0),
                    lambda p: float(p[0]["budget"]),
                    lambda p: bool(p[0]["hit"]))
        cur = _side(lambda p: float(p[1]["payout"] or 0),
                    lambda p: float(p[1]["bet_amount"] or 0),
                    lambda p: bool(p[1]["hit"]))
        out.append(ComparisonRow(
            plan_key=plan, n_races=len(pairs), n_days=len(days),
            lab_shown_hit=round(lab[0], 2), cur_shown_hit=round(cur[0], 2),
            lab_median_payout=round(lab[1], 0), cur_median_payout=round(cur[1], 0),
            lab_two_per_day=round(lab[2], 3), cur_two_per_day=round(cur[2], 3),
            lab_roi=round(lab[3], 1), cur_roi=round(cur[3], 1),
        ))
    return out
