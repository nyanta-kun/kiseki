"""型ラボ（`keirin.type_lab_picks`）の表示確認用 API（2026-08-27 新設）。

🔴 **既存の keirin API とは完全に独立**。`picks_history` も `netkeirin_submissions` も
   読まないので、既存の一覧・統計・売上集計に一切影響しない。
   型ラボは「既存商品の全面置き換え」を前提にした検証中の設計で、
   ペーパー検証（過去）と実地検証（当日）を混ぜずに見るための窓口。

## モード（`mode`）

| 値 | 中身 |
|---|---|
| `live` | 7車・当日・本番モデル |
| `live9` | **9車・当日**（2026-08-28 実投入。型F は決勝の `F_hit` だけ） |
| `paper` | 7車・過去・vintage |
| `paper9` | 9車・過去・**全8プラン**（型F を絞る前の検証行） |

🔴 **7車と9車は別モードのまま混ぜない。** 同じ `plan_key` でも確定オッズの中央値が
   2〜3倍違う（型A 18.6 ↔ 27.7倍 / 型F 58.7 ↔ 101.9倍）ので、
   まとめの「件/日・払戻中央・ROI」がどちらの話か読めなくなる。
⚠️ `live9` と `paper9` は**規則が別世代**（`rule_version` が違う）。`paper9` は
   型F を全部売っていたときの行。

設計と実測: `keirin/docs/type_lab/SUMMARY.md` /
`keirin/docs/type_lab/carcount_2026_08_27.md`
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..services.keirin_type_lab_gate import (
    AXIS_GATE_DROP_RATIO,
    AXIS_GATE_MIN,
    passes_axis_gate,
)
from ..services.keirin_type_lab_outcome import build_outcome, finish_class

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
    #: 発走時刻（JST の "HH:MM"）。取れなければ None
    start_time: str | None = None
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
    #: 指数順位から見た決着の中身（`keirin_type_lab_outcome.FINISH_CLASSES` の key）。
    #: `p3_order` を持たない古い行では None。
    finish_class: str | None = None
    #: 決着した 1-2-3 の三連単確定オッズ（券種に関係なくレース単位の荒れ具合）
    win_tf_odds: float | None = None
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
    #: このプランが**出た**日数（＝件/日 の分母ではない）
    n_days: int
    #: 件/日。分母は **窓の中で型ラボが動いた日数**（プランが出なかった日も含む）
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


class ComboRow(BaseModel):
    """選んだプランをひとまとめにしたときの1行（プラン別の内訳、または合計）。"""
    plan_key: str             # 合計行は "TOTAL"
    n_races: int              # 競合を除いたあとの対象レース数
    n_settled: int
    n_hit: int                # 生の的中（ガミを含む）
    n_shown_hit: int          # 表示的中（払戻 > 賭け金）
    invested: int
    returned: int
    roi: float


class ComboResponse(BaseModel):
    """複数プランを組み合わせた合計。

    🔴 **1レースの推奨は1プラン**なので、選んだプランが同じレースに複数当たったら
       そのレースは**丸ごと除外**する（どちらを買ったことにするか決められないため）。
       除いた数は `n_conflict_races` で必ず返す。黙って落とすと、
       競合だらけの選び方をしたときに「件数が少ない」としか見えなくなる。
    """
    #: 選択中のモード。`mode` は互換のためのカンマ連結、`modes` が正。
    mode: str
    modes: list[str] = []
    date_from: str
    date_to: str
    venue: str | None = None
    plans: list[str]
    n_days: int
    n_conflict_races: int
    #: 軸信頼ゲートを掛けたか / 掛けて落ちたレース数
    axis_gate: bool = False
    n_axis_gated_out: int = 0
    axis_gate_min: dict[str, float] = {}
    axis_gate_drop_ratio: float = AXIS_GATE_DROP_RATIO
    rows: list[ComboRow]
    total: ComboRow


class OutcomeColumn(BaseModel):
    key: str
    label: str
    note: str = ""


class OutcomeCell(BaseModel):
    key: str
    n: int
    pct: float
    #: プラン別の表だけ埋まる（そのセルでそのプランが当たった回数と率）
    n_hit: int | None = None
    hit_rate: float | None = None


class OutcomeRow(BaseModel):
    key: str
    label: str
    n: int
    median_tf_odds: float | None = None
    cells: list[OutcomeCell]


class OutcomeMatrix(BaseModel):
    key: str
    title: str
    note: str = ""
    columns: list[OutcomeColumn]
    rows: list[OutcomeRow]
    total: OutcomeRow | None = None


class TypeLabOutcomeResponse(BaseModel):
    """型分けの答え合わせ。

    🔴 母集団は**型ラボが実際に買ったレース**（`type_lab_picks` にある採点済みの行）。
       買い目が組めずゲートで落ちたレースは入っていないので、
       「全7車レースでの型の分布」とは一致しない。
    """
    mode: str
    modes: list[str] = []
    date_from: str
    date_to: str
    venue: str | None = None
    n_races: int
    n_races_settled: int
    n_unclassified: int
    n_no_payout: int
    matrices: list[OutcomeMatrix]


class TypeLabResponse(BaseModel):
    #: 選択中のモード。`mode` は互換のためのカンマ連結、`modes` が正。
    mode: str
    modes: list[str] = []
    date_from: str
    date_to: str
    rule_versions: list[str]
    #: 行数上限（`ROW_CAP`）で切ったか。切れているとまとめの母集団も切れている
    truncated: bool = False
    #: 期間内に出てくる競輪場（**絞り込み前**の全件から作る。
    #: 絞り込み後だと選んだ場しか候補に残らず、他の場へ切り替えられなくなる）
    venues: list[str]
    venue: str | None = None
    summaries: list[TypeLabSummary]
    comparison: list[ComparisonRow]
    picks: list[TypeLabPick]


# 🔴 発走時刻は `type_lab_picks` に持たず **`wt_races` から都度引く**
#    （出走変更で動くことがあり、行に焼き付けると古い時刻が残る）。
#    `wt_races.start_at` は **UNIX 秒の文字列**なので JST の HH:MM へ直す。
_SQL = text("""
    SELECT p.race_key, p.race_date, p.venue_name, p.race_no, p.race_type, p.day_index,
           p.type_label, p.axis_sum, p.arare, p.axis1, p.axis2, p.mode, p.plan_key,
           p.bet_type, p.n_legs, p.budget, p.legs, p.pred_mean_payout, p.pred_min_payout,
           p.settled_at, p.win_combo, p.hit, p.payout, p.final_odds, p.rule_version,
           p.p3_order, p.win_tf_odds,
           r.start_at
    FROM keirin.type_lab_picks p
    LEFT JOIN keirin.wt_races r ON r.race_key = p.race_key
    WHERE p.mode = ANY(:modes) AND p.race_date BETWEEN :d1 AND :d2
    ORDER BY p.race_date DESC,
             -- 発走の早い順に並べる（race_key は場コード順で時系列にならない）
             NULLIF(r.start_at, '')::bigint NULLS LAST, p.race_key, p.plan_key
    LIMIT :cap
""")

#: 一覧が1回に引く行の上限。🔴 **`legs` を含むので行数がそのまま転送量になる**
#: （実測: `mode=paper` の全期間 53,017行で **39MB・5.4秒**。同じ画面が `/combo` と
#: `/outcome` も並行で叩く）。まとめの母集団が切られるので、**切ったことは
#: `truncated` で必ず返す**（黙って減らすと「件数が少ない」としか見えない）。
ROW_CAP = 20_000

#: 選べるモード。**車数 × 実地/ペーパー の4通り**。
#: 🔴 `type_lab_picks.mode` の実値そのもの（`build_type_lab_picks` が
#:    `mode + MODE_TAG` で書く）。ここを勝手な別名にすると SQL が空を返す。
TYPE_LAB_MODES: tuple[str, ...] = ("live", "live9", "paper", "paper9")


def parse_modes(mode: str | None) -> list[str]:
    """`?mode=live,paper9` → `["live", "paper9"]`。**複数選択の唯一の正本**。

    競輪場の絞り込みと同じ操作感にするため、モードも「すべて」＋個別の
    複数選択にした（2026-08-28・ユーザー要望）。

    - 空・`all` … 4つ全部（＝「すべて」）
    - 知らない値は**捨てる**（増やすのではなく無視する。URL を手で書いたときに
      500 を返すより、選べる範囲へ丸めるほうが画面が壊れない）
    - 全部捨てられたら**既定の `live` 1つ**へ戻す（0件の SQL を投げない）
    - 並びは `TYPE_LAB_MODES` の順に正規化する（**同じ選択なら同じ URL** になり、
      キャッシュとログが読める）

    >>> parse_modes("live")
    ['live']
    >>> parse_modes("paper9,live")
    ['live', 'paper9']
    >>> parse_modes("")
    ['live', 'live9', 'paper', 'paper9']
    >>> parse_modes("all")
    ['live', 'live9', 'paper', 'paper9']
    >>> parse_modes("live, live9 ,live")
    ['live', 'live9']
    >>> parse_modes("知らない値")
    ['live']
    """
    if mode is None:
        return ["live"]
    raw = [x.strip() for x in str(mode).split(",")]
    if not any(raw) or "all" in raw:
        return list(TYPE_LAB_MODES)
    picked = {x for x in raw if x in TYPE_LAB_MODES}
    if not picked:
        return ["live"]
    return [m for m in TYPE_LAB_MODES if m in picked]


#: 現行の入稿優先順位（`keirin/src/netkeirin_submit_wt.RANK_ORDER` と同じ並び）。
#: 1レースに複数ランクの候補があるとき、**実際に売られるのは最上位の1つだけ**なので
#: 比較でもそれに揃える。
#: 🔴 向こうを変えたらここも変えること（手書きリスト）。
CURRENT_RANK_ORDER = ["RANK_7H2", "RANK_9H1", "RANK_7T1", "RANK_7T3", "RANK_7S",
                      "RANK_9C", "RANK_7B", "RANK_7C", "RANK_7H1", "RANK_7M1"]

# 🔴 **テーブルごとに `race_date` の型が違う**（2026-08-27 に両方 date にして 500 を出した）:
#     keirin.type_lab_picks.race_date        … DATE      → `datetime.date` を渡す
#     keirin.picks_history.race_date         … VARCHAR   → **文字列**を渡す
#     keirin.netkeirin_submissions           … 日付列なし → `race_key` の先頭8桁と比較
#   asyncpg は型を厳格に見るので、取り違えるとその場で DataError になる。
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


# 組み合わせ集計用。**買い目（legs）は引かない** — チェックを付け外しするたびに
# 呼ぶので軽くしておく。`plan_key = ANY(:plans)` は asyncpg の配列渡し
# （この repo の既存クエリと同じ形）。
_SQL_COMBO = text("""
    SELECT race_key, race_date, venue_name, plan_key, budget, settled_at, hit, payout,
           axis_sum, n_entries
    FROM keirin.type_lab_picks
    WHERE mode = ANY(:modes) AND race_date BETWEEN :d1 AND :d2
      AND plan_key = ANY(:plans)
""")


#: 日本標準時。`wt_races.start_at` は UNIX 秒なので、表示前にこれで直す。
JST = timezone(timedelta(hours=9))


def _hhmm(start_at: object) -> str | None:
    """UNIX 秒（文字列）→ JST の "HH:MM"。読めなければ None。"""
    if start_at in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(str(start_at)), JST).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _median(v: list[float]) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    n = len(s)
    return float(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def window(date_from: str | None, date_to: str | None
           ) -> tuple[str, str, date, date]:
    """(表示用の文字列 d1, d2, DATE 比較用の date dd1, dd2)。

    🔴 asyncpg は DATE 列へ文字列を渡せない（`'str' object has no attribute
       'toordinal'` で 500 になる）。日付比較には必ず `datetime.date` を渡すこと。
       `race_key` の先頭8文字と比べるクエリだけは文字列のままでよい。
    """
    d2 = date_to or date.today().isoformat()
    d1 = date_from or (date.fromisoformat(d2) - timedelta(days=6)).isoformat()
    return d1, d2, date.fromisoformat(d1), date.fromisoformat(d2)


def _rank_pos(rank: str) -> int:
    return (CURRENT_RANK_ORDER.index(rank) if rank in CURRENT_RANK_ORDER
            else len(CURRENT_RANK_ORDER))


@router.get("", response_model=TypeLabResponse)
async def get_type_lab(
    mode: str = Query("live", description="カンマ区切りで複数可（例 'live,paper9'）。"
                                          "空または 'all' で全モード"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    venue: str | None = Query(None, description="競輪場名で絞り込む（例 '伊東'）"),
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
) -> TypeLabResponse:
    """型ラボの買い目と、プラン別のまとめ。

    既定は直近7日の実地（`mode=live`）。ペーパー検証は `mode=paper` と期間を指定する。
    """
    modes = parse_modes(mode)
    d1, d2, dd1, dd2 = window(date_from, date_to)
    # 上の注記のとおり、渡す型はテーブルごとに違う。

    # type_lab_picks.race_date は DATE なので `datetime.date` で渡す
    res = await db.execute(_SQL, {"modes": modes, "d1": dd1, "d2": dd2,
                                  "cap": ROW_CAP})
    rows = [dict(r._mapping) for r in res]
    truncated = len(rows) >= ROW_CAP

    # 🔴 選択肢は**絞り込む前**に作る。絞ってから作ると選んだ場しか残らず
    #    他の場へ切り替えられなくなる。
    venues = sorted({str(r["venue_name"]) for r in rows if r["venue_name"]})
    if venue:
        rows = [r for r in rows if r["venue_name"] == venue]

    # 同じ期間の現行推奨。1レースに複数ランクがあれば優先順位の最上位を採る。
    # picks_history.race_date は VARCHAR なので**文字列**で渡す
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
                start_time=_hhmm(r.get("start_at")),
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
                finish_class=finish_class(r.get("win_combo"), r.get("p3_order")),
                win_tf_odds=(float(r["win_tf_odds"])
                             if r["win_tf_odds"] is not None else None),
                current=_current_of(current.get(r["race_key"]), sold.get(r["race_key"])),
            ))

    # 🔴 **件/日 の分母は「窓の中で型ラボが動いた日数」**（2026-08-28 是正）。
    #    プランごとに「そのプランが出た日数」で割ると、**出なかった日が分母から消えて
    #    件/日 が過大になる**。実測（`paper9`・窓 323日）で D_hit 1.72 ↔ 0.56 /
    #    A_hit 1.85 ↔ 0.74 と最大 3.1倍ずれていた。「1日あたり何件売れるか」は
    #    本ページの筆頭の判断指標なので、稀なプランほど読めなくなるのが効く。
    window_days = max(len({str(r["race_date"]) for r in rows}), 1)

    summaries: list[TypeLabSummary] = []
    for plan in sorted(by_plan, key=lambda p: (PLAN_ORDER.index(p)
                                               if p in PLAN_ORDER else 99, p)):
        g = by_plan[plan]
        days = {str(x["race_date"]) for x in g}
        st = [x for x in g if x["settled_at"] is not None]
        hits = [x for x in st if x["hit"]]
        # 🔴 **表示的中は `払戻 > 賭け金`**（`combine_plans` と同じ定義に揃える）。
        #    以前はここだけ `<` で、`払戻 == 賭け金` を表示的中に数えていた
        #    （3か所で `<` / `>` / `>=` と割れていた。実測 5行）。
        gami = [x for x in hits if (x["payout"] or 0) <= x["budget"]]
        inv = sum(int(x["budget"]) for x in st)
        ret = sum(int(x["payout"] or 0) for x in st)
        two = [x for x in hits if (x["payout"] or 0) >= 2 * int(x["budget"])]
        big = [x for x in hits if (x["payout"] or 0) >= 100_000]
        nd = window_days
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

    return TypeLabResponse(mode=",".join(modes), modes=modes,
                           date_from=d1, date_to=d2,
                           rule_versions=sorted(versions), truncated=truncated,
                           venues=venues, venue=venue,
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
            # 表示的中は `払戻 > 賭け金`（summaries / combine_plans と同じ定義）
            shown = [p for p in hits if get_pay(p) > get_inv(p)]
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


def combine_plans(rows: list[dict[str, Any]]) -> tuple[list[ComboRow], ComboRow, int, int]:
    """選んだプランの行を1つの商品ラインとして合計する。

    `rows` は `race_key` / `plan_key` / `budget` / `settled_at` / `hit` / `payout` /
    `race_date` を持つ辞書の並び（**選んだプランだけに絞ってから渡すこと**）。

    🔴 **1レースの推奨は1プラン。** 同じレースに選択中のプランが2つ以上当たったら、
       そのレースは両方とも集計から外す（競合）。除いた数を第3の戻り値で返す。
    🔴 **ROI・的中は採点済みの行だけで計算する。** 未採点を分母に入れると
       当日の朝ほど ROI が 0 に近く見える。

    戻り値: (プラン別の内訳, 合計, 競合で除いたレース数, 日数)
    """
    by_race: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_race.setdefault(str(r["race_key"]), []).append(r)
    kept = [g[0] for g in by_race.values() if len(g) == 1]
    n_conflict = sum(1 for g in by_race.values() if len(g) > 1)

    def _row(key: str, group: list[dict[str, Any]]) -> ComboRow:
        st = [x for x in group if x["settled_at"] is not None]
        hits = [x for x in st if x["hit"]]
        shown = [x for x in hits if int(x["payout"] or 0) > int(x["budget"])]
        inv = sum(int(x["budget"]) for x in st)
        ret = sum(int(x["payout"] or 0) for x in st)
        return ComboRow(
            plan_key=key, n_races=len(group), n_settled=len(st),
            n_hit=len(hits), n_shown_hit=len(shown),
            invested=inv, returned=ret,
            roi=round(ret / inv * 100, 1) if inv else 0.0,
        )

    by_plan: dict[str, list[dict[str, Any]]] = {}
    for r in kept:
        by_plan.setdefault(str(r["plan_key"]), []).append(r)
    out = [_row(p, by_plan[p])
           for p in sorted(by_plan, key=lambda x: (PLAN_ORDER.index(x)
                                                   if x in PLAN_ORDER else 99, x))]
    days = {str(r["race_date"]) for r in kept}
    return out, _row("TOTAL", kept), n_conflict, len(days)


@router.get("/combo", response_model=ComboResponse)
async def get_type_lab_combo(
    plans: str = Query("", description="カンマ区切りのプラン（例 'A_hit,B_hit'）"),
    mode: str = Query("live", description="カンマ区切りで複数可（例 'live,paper9'）"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    venue: str | None = Query(None),
    axis_gate: bool = Query(False, description="プラン内で軸信頼が下位のレースを外す"),
    db: AsyncSession = Depends(get_db),
) -> ComboResponse:
    """チェックした複数プランを**1つの商品ライン**として合計する。

    プラン別のまとめ（`GET /api/keirin/type-lab`）は各プランを別々に見るが、
    実際に売るときは1レースにつき1プランしか出せない。
    ここでは「この組み合わせで売ったら1日いくつ出て、いくら返るか」を出す。
    """
    modes = parse_modes(mode)
    d1, d2, dd1, dd2 = window(date_from, date_to)
    wanted = [p for p in PLAN_ORDER if p in {x.strip() for x in plans.split(",")}]
    if not wanted:
        empty = ComboRow(plan_key="TOTAL", n_races=0, n_settled=0, n_hit=0,
                         n_shown_hit=0, invested=0, returned=0, roi=0.0)
        return ComboResponse(mode=",".join(modes), modes=modes,
                             date_from=d1, date_to=d2, venue=venue,
                             plans=[], n_days=0, n_conflict_races=0,
                             axis_gate=axis_gate, axis_gate_min=AXIS_GATE_MIN,
                             rows=[], total=empty)

    # race_date は DATE 列なので `datetime.date` を渡す（文字列だと 500）
    res = await db.execute(_SQL_COMBO,
                           {"modes": modes, "d1": dd1, "d2": dd2, "plans": wanted})
    rows = [dict(r._mapping) for r in res]
    if venue:
        rows = [r for r in rows if r["venue_name"] == venue]

    # 🔴 ゲートは**競合の判定より前**に掛ける。後に掛けると、片方だけ落ちたレースが
    #    「競合ではない」のに1プランだけ残って母集団がずれる。
    n_gated = 0
    if axis_gate:
        before = len(rows)
        # 🔴 **車数を渡すこと。** 閾値は7車の探索窓の分位なので、9車へ当てると
        #    「下位1/5を外す」ではなく絶対値で切る形になる（`passes_axis_gate` 参照）。
        rows = [r for r in rows
                if passes_axis_gate(str(r["plan_key"]), r["axis_sum"],
                                    r.get("n_entries"))]
        n_gated = before - len(rows)

    detail, total, n_conflict, n_days = combine_plans(rows)
    return ComboResponse(mode=",".join(modes), modes=modes,
                         date_from=d1, date_to=d2, venue=venue,
                         plans=wanted, n_days=n_days,
                         n_conflict_races=n_conflict,
                         axis_gate=axis_gate, n_axis_gated_out=n_gated,
                         axis_gate_min=AXIS_GATE_MIN,
                         rows=detail, total=total)


# 答え合わせ用。**買い目（legs）は引かない**（分類に要らないので軽くする）。
_SQL_OUTCOME = text("""
    SELECT race_key, race_date, venue_name, plan_key, type_label, gap,
           settled_at, hit, win_combo, p3_order, win_tf_odds
    FROM keirin.type_lab_picks
    WHERE mode = ANY(:modes) AND race_date BETWEEN :d1 AND :d2
""")


@router.get("/outcome", response_model=TypeLabOutcomeResponse)
async def get_type_lab_outcome(
    mode: str = Query("live", description="カンマ区切りで複数可（例 'live,paper9'）"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    venue: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> TypeLabOutcomeResponse:
    """事前の型分けが決着と合っていたかを表にして返す。

    ①軸の堅さ→的中率 / ②相手の開き→3着の出どころ / ③荒れ度→配当 の3層を、
    それぞれ「事前の分割 × 実際の決着」のマトリクスで出す。
    計算の正本は `services/keirin_type_lab_outcome.build_outcome`（純関数）。
    """
    modes = parse_modes(mode)
    d1, d2, dd1, dd2 = window(date_from, date_to)
    res = await db.execute(_SQL_OUTCOME, {"modes": modes, "d1": dd1, "d2": dd2})
    rows = [dict(r._mapping) for r in res]
    if venue:
        rows = [r for r in rows if r["venue_name"] == venue]
    out = build_outcome(rows)
    return TypeLabOutcomeResponse(mode=",".join(modes), modes=modes,
                                  date_from=d1, date_to=d2, venue=venue,
                                  **out)
