"""JRA Phase4a 予備検証: 締切直前オッズトレンド(odds_trend_ratio)の直交性検証

研究用スクリプト。DB 書き込みは一切行わない（読み取り専用クエリのみ）。

背景:
    既存の推奨エンジン(`src/services/recommender.py::build_hit_tier_recommendations`)は
    指数1位馬の tier(S/A/B/C/C+) を静的なオッズスナップショット1点(発走直前想定)のみで
    判定している。Web調査で「オッズの変化速度・方向(締切前の下げ足)は市場が持つが
    モデルが見ていない直交情報ではないか」という仮説が出た。

    `keiba.odds_history` は 2026-03-28 開始・約5ヶ月分・約1,200レースしかなく、
    Phase3 (`jra_phase3_market_chaos_analysis.py`) が使った「診断期間(2年+)→
    確認期間(半年)」の二段階ホールドアウト検証には全く足りない。
    本スクリプトは単一 hold-out 分割（前半60%探索/後半40%確認）による
    簡易一貫性チェックに限定し、結果は「参考値・信頼度低め」として扱う。

対象母集団:
    head_count>=8 ∧ course in JRA10場 ∧ abnormality_code=0（出走取消・除外馬は
    ランキング母集団から除外した上での top1 = 指数1位馬。[[jra_upset_badge_redesign]]と
    同じ方針、`jra_phase3_market_chaos_analysis.py::analyze_race` と同一パターン）。

odds_trend_ratio:
    top1 馬の `odds_history`(bet_type='win', combination=馬番) 時系列から、
    発走前の最初20%点のオッズ(odds_at_early)と最後20%点のオッズ(odds_at_late)の比。
    1.0未満 = 締切にかけて支持が強まった(下げた)、1.0超 = 支持が弱まった(上がった)。
    スナップショットが少なすぎる(distinct fetched_at < 10)レースは対象外。

tier/entropy_norm は `src/indices/confidence.py` の関数をそのまま import して使う
（再実装しない）。

出力:
    標準出力に表形式で結果を表示。
    `backend/models/v26_phase4a_odds_movement.json` にサマリー保存。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402

from src.indices.confidence import (  # noqa: E402
    calculate_market_chaos,
    calculate_race_confidence,
    calculate_recommend_rank,
    is_market_favorite,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_phase4a_odds_movement")

V26_VERSION = 26
MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_JSON = MODELS_DIR / "v26_phase4a_odds_movement.json"

JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

MIN_SAMPLE_FOR_CONCLUSION = 100  # これ未満は参考値扱い
MIN_SNAPSHOTS = 10  # distinct fetched_at がこれ未満のレースは対象外
EXPLORE_FRACTION = 0.6  # 前半(古い側)を探索用に割り当てる比率

BASE_QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    ci.composite_index,
    ci.win_probability,
    r.date,
    r.head_count,
    r.course,
    re.horse_number,
    rr.finish_position,
    rr.win_odds,
    rr.place_odds,
    COALESCE(rr.abnormality_code, 0) AS abnormality_code
FROM keiba.calculated_indices ci
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = %(ver)s
  AND r.head_count >= 8
  AND r.course IN %(courses)s
  AND ci.race_id IN (SELECT DISTINCT race_id FROM keiba.odds_history WHERE bet_type = 'win')
ORDER BY ci.race_id;
"""


@dataclass
class RaceRecord:
    """レース単位の分析用集計レコード。"""

    race_id: int
    date: str
    course: str
    head_count: int
    top1_horse_number: int
    confidence_score: int
    market_agree: bool | None
    tier: str
    entropy_norm: float | None
    top1_win_odds: float | None
    top1_place_odds: float | None
    top1_finish_position: int | None
    top1_place_hit: bool
    top1_win_hit: bool
    n_snapshots: int
    odds_at_early: float | None
    odds_at_late: float | None
    odds_trend_ratio: float | None


def get_connection() -> Any:
    """`.env` の DB_* 変数から psycopg2 接続を作成する。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


def to_float(x: Any) -> float | None:
    """Decimal/None を float に変換する。"""
    return float(x) if x is not None else None


def fetch_base_rows(conn: Any) -> list[dict[str, Any]]:
    """odds_history(win) が存在する JRA10場・8頭立て以上レースの指数+結果行を取得する。"""
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": V26_VERSION, "courses": JRA_COURSES})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    logger.info(f"base rows取得: {len(rows):,}行")
    return [dict(zip(cols, r)) for r in rows]


def build_race_infos(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """行データをレース単位にグループ化し、出走取消馬を除外した top1 を特定する。

    出走取消・発走除外馬（abnormality_code != 0）は既存の dm_signals/Phase3 と同じ方針で
    順位計算の母集団から除外する。
    """
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        race_id = row["race_id"]
        entry = grouped.setdefault(
            race_id,
            {"date": row["date"], "course": row["course"], "head_count": row["head_count"], "horses": []},
        )
        entry["horses"].append(row)

    race_infos: dict[int, dict[str, Any]] = {}
    for race_id, info in grouped.items():
        active = [h for h in info["horses"] if (h["abnormality_code"] or 0) == 0]
        active_with_idx = [h for h in active if h["composite_index"] is not None]
        if not active_with_idx:
            continue

        n = info["head_count"] if info["head_count"] is not None else len(active)
        composite_indices = [to_float(h["composite_index"]) for h in active_with_idx]
        win_probs = [to_float(h["win_probability"]) for h in active if h["win_probability"] is not None]
        win_odds_all = [to_float(h["win_odds"]) for h in active if h["win_odds"] is not None]

        conf = calculate_race_confidence(composite_indices, n, win_probs or None)

        top1 = max(active_with_idx, key=lambda h: to_float(h["composite_index"]))
        top_odds = to_float(top1["win_odds"])
        market_agree = is_market_favorite(top_odds, win_odds_all or None)
        chaos = calculate_market_chaos(win_odds_all)
        entropy_norm = chaos.get("entropy_norm")
        tier = calculate_recommend_rank(
            conf["score"], conf.get("win_prob_top"), top_odds, market_agree, entropy_norm
        )

        finish = top1["finish_position"]
        race_infos[race_id] = {
            "date": info["date"],
            "course": info["course"],
            "head_count": n,
            "top1_horse_number": top1["horse_number"],
            "confidence_score": conf["score"],
            "market_agree": market_agree,
            "tier": tier,
            "entropy_norm": entropy_norm,
            "top1_win_odds": top_odds,
            "top1_place_odds": to_float(top1["place_odds"]),
            "top1_finish_position": finish,
            "top1_place_hit": finish is not None and 1 <= finish <= 3,
            "top1_win_hit": finish is not None and finish == 1,
        }
    logger.info(f"top1特定完了: {len(race_infos):,}レース")
    return race_infos


def fetch_odds_trend(conn: Any, race_infos: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """top1馬の win odds_history 時系列から early/late スナップショット・trend ratio を計算する。"""
    pairs = [(race_id, str(info["top1_horse_number"])) for race_id, info in race_infos.items()]
    if not pairs:
        return {}

    cur = conn.cursor()
    values_clause = ",".join(cur.mogrify("(%s,%s)", p).decode() for p in pairs)
    query = f"""
        SELECT oh.race_id, oh.odds, oh.fetched_at
        FROM keiba.odds_history oh
        JOIN (VALUES {values_clause}) AS t(race_id, combination)
          ON oh.race_id = t.race_id AND oh.combination = t.combination
        WHERE oh.bet_type = 'win'
        ORDER BY oh.race_id, oh.fetched_at;
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    logger.info(f"top1 odds_history取得: {len(rows):,}行")

    by_race: dict[int, dict[Any, float]] = defaultdict(dict)
    for race_id, odds, fetched_at in rows:
        if odds is None:
            continue
        # 同一 fetched_at の重複は先勝ちで保持（通常は一意）
        by_race[race_id].setdefault(fetched_at, float(odds))

    trend: dict[int, dict[str, Any]] = {}
    for race_id, ts_map in by_race.items():
        ordered = sorted(ts_map.items(), key=lambda kv: kv[0])
        m = len(ordered)
        if m < MIN_SNAPSHOTS:
            trend[race_id] = {
                "n_snapshots": m,
                "odds_at_early": None,
                "odds_at_late": None,
                "odds_trend_ratio": None,
            }
            continue
        early_idx = round((m - 1) * 0.2)
        late_idx = round((m - 1) * 0.8)
        odds_at_early = ordered[early_idx][1]
        odds_at_late = ordered[late_idx][1]
        ratio = (odds_at_late / odds_at_early) if odds_at_early and odds_at_early > 0 else None
        trend[race_id] = {
            "n_snapshots": m,
            "odds_at_early": odds_at_early,
            "odds_at_late": odds_at_late,
            "odds_trend_ratio": round(ratio, 4) if ratio is not None else None,
        }
    return trend


def build_race_records(
    race_infos: dict[int, dict[str, Any]], trend: dict[int, dict[str, Any]]
) -> list[RaceRecord]:
    """race_infos + trend を結合し、odds_trend_ratio 算出可能なレコードのみ返す。"""
    records: list[RaceRecord] = []
    n_no_trend = 0
    for race_id, info in race_infos.items():
        t = trend.get(race_id)
        if t is None or t["odds_trend_ratio"] is None:
            n_no_trend += 1
            continue
        records.append(
            RaceRecord(
                race_id=race_id,
                date=info["date"],
                course=info["course"],
                head_count=info["head_count"],
                top1_horse_number=info["top1_horse_number"],
                confidence_score=info["confidence_score"],
                market_agree=info["market_agree"],
                tier=info["tier"],
                entropy_norm=info["entropy_norm"],
                top1_win_odds=info["top1_win_odds"],
                top1_place_odds=info["top1_place_odds"],
                top1_finish_position=info["top1_finish_position"],
                top1_place_hit=info["top1_place_hit"],
                top1_win_hit=info["top1_win_hit"],
                n_snapshots=t["n_snapshots"],
                odds_at_early=t["odds_at_early"],
                odds_at_late=t["odds_at_late"],
                odds_trend_ratio=t["odds_trend_ratio"],
            )
        )
    logger.info(
        f"odds_trend_ratio算出可能: {len(records):,}レース "
        f"(スナップショット不足等で除外: {n_no_trend:,}レース)"
    )
    return records


def split_explore_confirm(records: list[RaceRecord]) -> tuple[list[RaceRecord], list[RaceRecord]]:
    """時系列で前半60%(探索用)/後半40%(確認用)に単純分割する。"""
    ordered = sorted(records, key=lambda r: (r.date, r.race_id))
    split_idx = round(len(ordered) * EXPLORE_FRACTION)
    return ordered[:split_idx], ordered[split_idx:]


# ---------------------------------------------------------------------------
# セクション1: odds_trend_ratio 四分位別 複勝的中率
# ---------------------------------------------------------------------------


def compute_quartile_bounds(explore: list[RaceRecord]) -> list[float]:
    """探索用データの odds_trend_ratio から四分位境界 [Q1, Q2, Q3] を算出する。"""
    vals = sorted(r.odds_trend_ratio for r in explore if r.odds_trend_ratio is not None)
    return statistics.quantiles(vals, n=4, method="inclusive")


def assign_quartile(ratio: float, bounds: list[float]) -> str:
    """固定境界 bounds=[Q1,Q2,Q3] を用いて四分位ラベル(Q1〜Q4)を割り当てる。"""
    q1, q2, q3 = bounds
    if ratio <= q1:
        return "Q1(最も下げた)"
    if ratio <= q2:
        return "Q2"
    if ratio <= q3:
        return "Q3"
    return "Q4(最も上がった)"


def quartile_stats(records: list[RaceRecord], bounds: list[float]) -> dict[str, dict[str, Any]]:
    """固定境界で四分位に分割し、各分位の複勝的中率を集計する。"""
    by_q: dict[str, list[RaceRecord]] = defaultdict(list)
    for r in records:
        by_q[assign_quartile(r.odds_trend_ratio, bounds)].append(r)

    out: dict[str, dict[str, Any]] = {}
    for label in ("Q1(最も下げた)", "Q2", "Q3", "Q4(最も上がった)"):
        rs = by_q.get(label, [])
        n = len(rs)
        settled = [r for r in rs if r.top1_finish_position is not None]
        n_settled = len(settled)
        hits = sum(1 for r in settled if r.top1_place_hit)
        win_hits = sum(1 for r in settled if r.top1_win_hit)
        out[label] = {
            "n": n,
            "n_settled": n_settled,
            "place_hit_rate": round(hits / n_settled, 4) if n_settled else None,
            "win_hit_rate": round(win_hits / n_settled, 4) if n_settled else None,
            "mean_ratio": round(statistics.mean(r.odds_trend_ratio for r in rs), 4) if rs else None,
        }
    return out


def print_quartile_table(title: str, stats: dict[str, dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    header = f"{'quartile':<16}{'n':>7}{'n_settled':>11}{'平均ratio':>10}{'複勝的中率':>12}{'単勝的中率':>12}"
    print(header)
    for label in ("Q1(最も下げた)", "Q2", "Q3", "Q4(最も上がった)"):
        s = stats.get(label, {"n": 0})
        n = s.get("n", 0)
        flag = "" if n >= MIN_SAMPLE_FOR_CONCLUSION else " (参考:n<100)"
        place = s.get("place_hit_rate")
        win = s.get("win_hit_rate")
        mean_r = s.get("mean_ratio")
        print(
            f"{label:<16}{n:>7}{s.get('n_settled', 0):>11}"
            f"{(f'{mean_r:.3f}' if mean_r is not None else '-'):>10}"
            f"{(f'{place:.1%}' if place is not None else '-'):>12}"
            f"{(f'{win:.1%}' if win is not None else '-'):>12}{flag}"
        )


# ---------------------------------------------------------------------------
# セクション2: 既存tier内での odds_trend_ratio 追加分離
# ---------------------------------------------------------------------------


def median_ratio_by_tier(records: list[RaceRecord]) -> dict[str, float]:
    """探索用データの tier別 odds_trend_ratio 中央値を算出する。"""
    by_tier: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_tier[r.tier].append(r.odds_trend_ratio)
    return {tier: statistics.median(vals) for tier, vals in by_tier.items() if vals}


def tier_ratio_split_stats(records: list[RaceRecord], tier: str, threshold: float) -> dict[str, Any]:
    """指定 tier・固定 odds_trend_ratio 閾値で低(下げた)/高(上がった)2グループに分け複勝的中率を比較する。"""
    subset = [r for r in records if r.tier == tier]
    low = [r for r in subset if r.odds_trend_ratio <= threshold]
    high = [r for r in subset if r.odds_trend_ratio > threshold]

    def _rate(rs: list[RaceRecord]) -> dict[str, Any]:
        settled = [r for r in rs if r.top1_finish_position is not None]
        n = len(settled)
        hits = sum(1 for r in settled if r.top1_place_hit)
        return {"n": len(rs), "n_settled": n, "place_hit_rate": round(hits / n, 4) if n else None}

    return {
        "tier": tier,
        "threshold": round(threshold, 4),
        "low_ratio(下げた)": _rate(low),
        "high_ratio(上がった)": _rate(high),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    conn = get_connection()
    try:
        rows = fetch_base_rows(conn)
        race_infos = build_race_infos(rows)
        trend = fetch_odds_trend(conn, race_infos)
    finally:
        conn.close()

    records = build_race_records(race_infos, trend)
    logger.info(f"分析対象レコード構築完了: {len(records):,}件")

    explore, confirm = split_explore_confirm(records)
    if explore:
        logger.info(f"探索用(前半{EXPLORE_FRACTION:.0%}): {len(explore):,}レース ({explore[0].date}〜{explore[-1].date})")
    if confirm:
        logger.info(f"確認用(後半{1 - EXPLORE_FRACTION:.0%}): {len(confirm):,}レース ({confirm[0].date}〜{confirm[-1].date})")

    summary: dict[str, Any] = {
        "note": (
            "参考値・信頼度低め。odds_history蓄積が約5ヶ月・"
            f"全体{len(records)}レースしかなく、Phase3の確認期間検証"
            "(diagnostic 8,377/confirmation 1,905)と比べて統計的信頼性が大幅に低い。"
            "単一hold-out分割(前後半)による簡易一貫性チェックであり、"
            "二段階honest検証(診断/確認)ではない。"
        ),
        "min_snapshots_filter": MIN_SNAPSHOTS,
        "explore_fraction": EXPLORE_FRACTION,
        "n_races_explore": len(explore),
        "n_races_confirm": len(confirm),
        "explore_date_range": [explore[0].date, explore[-1].date] if explore else None,
        "confirm_date_range": [confirm[0].date, confirm[-1].date] if confirm else None,
    }

    # --- セクション1: 四分位分析 ---
    bounds = compute_quartile_bounds(explore)
    print(f"\n探索用データから決定した odds_trend_ratio 四分位境界(固定・確認用にもそのまま適用): {bounds}")
    summary["quartile_bounds_from_explore"] = bounds

    explore_q_stats = quartile_stats(explore, bounds)
    confirm_q_stats = quartile_stats(confirm, bounds)
    print_quartile_table("セクション1: odds_trend_ratio四分位別 複勝的中率（探索用）", explore_q_stats)
    print_quartile_table("セクション1: odds_trend_ratio四分位別 複勝的中率（確認用・固定境界を適用）", confirm_q_stats)
    summary["section1_quartile"] = {"explore": explore_q_stats, "confirm": confirm_q_stats}

    q1_explore = explore_q_stats["Q1(最も下げた)"]["place_hit_rate"]
    q4_explore = explore_q_stats["Q4(最も上がった)"]["place_hit_rate"]
    q1_confirm = confirm_q_stats["Q1(最も下げた)"]["place_hit_rate"]
    q4_confirm = confirm_q_stats["Q4(最も上がった)"]["place_hit_rate"]
    explore_gap = (q1_explore - q4_explore) if q1_explore is not None and q4_explore is not None else None
    confirm_gap = (q1_confirm - q4_confirm) if q1_confirm is not None and q4_confirm is not None else None
    print(f"\nQ1-Q4 差(下げた方が高いほど正): 探索用={explore_gap} / 確認用={confirm_gap}")
    summary["q1_minus_q4_gap"] = {"explore": explore_gap, "confirm": confirm_gap}

    # --- セクション2: tier内 odds_trend_ratio 追加分離 ---
    tier_thresholds = median_ratio_by_tier(explore)
    print("\n=== セクション2: tier別 odds_trend_ratio 中央値（探索用で決定・固定値として確認用へ適用） ===")
    for tier in ("S", "A", "B", "C+", "C"):
        print(f"  tier={tier}: median odds_trend_ratio = {tier_thresholds.get(tier)}")

    section2: dict[str, Any] = {"thresholds_from_explore": tier_thresholds, "explore": {}, "confirm": {}}
    print("\n=== セクション2: tier内 odds_trend_ratio 低(下げた)/高(上がった)分割による複勝的中率の差 ===")
    for tier in ("S", "A", "B", "C+", "C"):
        if tier not in tier_thresholds:
            print(f"  tier={tier}: 該当レース無し（スキップ）")
            continue
        threshold = tier_thresholds[tier]
        explore_split = tier_ratio_split_stats(explore, tier, threshold)
        confirm_split = tier_ratio_split_stats(confirm, tier, threshold)
        section2["explore"][tier] = explore_split
        section2["confirm"][tier] = confirm_split

        e_low, e_high = explore_split["low_ratio(下げた)"], explore_split["high_ratio(上がった)"]
        c_low, c_high = confirm_split["low_ratio(下げた)"], confirm_split["high_ratio(上がった)"]
        print(f"\n  --- tier={tier} (threshold odds_trend_ratio={threshold:.4f}) ---")
        print(
            f"    探索用: low(下げた) n={e_low['n']:>5} 複勝的中率={e_low['place_hit_rate']}"
            f" / high(上がった) n={e_high['n']:>5} 複勝的中率={e_high['place_hit_rate']}"
        )
        print(
            f"    確認用: low(下げた) n={c_low['n']:>5} 複勝的中率={c_low['place_hit_rate']}"
            f" / high(上がった) n={c_high['n']:>5} 複勝的中率={c_high['place_hit_rate']}"
        )
        e_gap = (
            e_low["place_hit_rate"] - e_high["place_hit_rate"]
            if e_low["place_hit_rate"] is not None and e_high["place_hit_rate"] is not None
            else None
        )
        c_gap = (
            c_low["place_hit_rate"] - c_high["place_hit_rate"]
            if c_low["place_hit_rate"] is not None and c_high["place_hit_rate"] is not None
            else None
        )
        reproduced = (
            e_gap is not None
            and c_gap is not None
            and e_gap > 0
            and c_gap > 0
            and e_low["n"] >= MIN_SAMPLE_FOR_CONCLUSION
            and c_low["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        )
        print(f"    探索用 low-high差: {e_gap}  / 確認用 low-high差: {c_gap}  → 再現(かつn>=100): {reproduced}")
        section2["explore"][tier]["gap"] = e_gap
        section2["confirm"][tier]["gap"] = c_gap
        section2["confirm"][tier]["reproduced"] = reproduced

    summary["section2_tier_split"] = section2

    OUTPUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    logger.info(f"サマリー保存: {OUTPUT_JSON}")

    print(
        "\n⚠️ 結論の解釈上の注意: サンプル数(全体約"
        f"{len(records)}レース、探索用{len(explore)}/確認用{len(confirm)})が小さく、"
        "Phase3の確認期間検証(diagnostic 8,377/confirmation 1,905)と比べて"
        "統計的信頼性が大幅に低い参考値である。"
    )


if __name__ == "__main__":
    main()
