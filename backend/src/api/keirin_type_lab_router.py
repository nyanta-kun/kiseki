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


def _median(v: list[float]) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    n = len(s)
    return float(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


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
                           summaries=summaries, picks=picks)
