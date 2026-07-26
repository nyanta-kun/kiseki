"""JRA hit_tier 見送り(C)判定 診断スクリプト: 市場混戦度(HHI/Shannon Entropy)による追加分離検証

研究用スクリプト。DB 書き込みは一切行わない（読み取り専用クエリのみ）。

背景:
    既存の `build_hit_tier_recommendations`（`src/services/recommender.py`）は
    1レース1推奨・tier=C（confidence_score と market_agree から算出）を見送りとしている。
    tier=C は事実上「market_agree=False（市場と指数の不一致）」とほぼ同義であり、
    「市場全体がどれだけ拮抗しているか」という連続値の混戦度情報は使われていない。

    本スクリプトは Web 調査で得られた2つの理論で tier=C の中身をさらに分離できるか診断する:
      (1) 単勝オッズの implied probability から HHI / 正規化 Shannon Entropy を算出し
          「市場全体の混戦度」を連続値スコア化する
      (2) confidence_score を Risk-Coverage 曲線的に分析し、閾値65/80が
          カバレッジ・的中率トレードオフ上で妥当かを検証する

    `src/indices/confidence.py` の `calculate_race_confidence` / `is_market_favorite` /
    `calculate_recommend_rank` はそのまま import して使用し、ロジックの再実装は行わない。

期間設計（多重比較・チェリーピッキング回避のため厳格に分離する）:
    - 診断期間 (diagnostic): 2023-07-01 〜 2025-12-31 — パターン探索用
    - 確認期間 (confirmation): 2026-01-01 〜 2026-07-23 — 完全ホールドアウト。
      診断期間で決めた閾値・計算式をそのまま（再フィッティングせず）適用して再現性のみ確認する。

出力:
    標準出力にセクション A/B/C の表を表示。
    `backend/models/v26_phase3_market_chaos.json` にサマリーを保存（再現性のため）。
"""

from __future__ import annotations

import json
import logging
import math
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
    calculate_race_confidence,
    calculate_recommend_rank,
    is_market_favorite,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_phase3_market_chaos")

V26_VERSION = 26
MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_JSON = MODELS_DIR / "v26_phase3_market_chaos.json"

DIAGNOSTIC_START = "20230701"
DIAGNOSTIC_END = "20251231"
CONFIRMATION_START = "20260101"
CONFIRMATION_END = "20260723"

JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

MIN_SAMPLE_FOR_CONCLUSION = 100  # これ未満は参考値扱い

DATA_QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    ci.composite_index,
    ci.win_probability,
    r.date,
    r.head_count,
    r.course,
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
  AND r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN %(courses)s
ORDER BY ci.race_id;
"""


@dataclass
class RaceRecord:
    """レース単位の診断用集計レコード。"""

    race_id: int
    date: str
    course: str
    head_count: int
    confidence_score: int
    market_agree: bool | None
    tier: str
    hhi: float | None
    entropy_norm: float | None
    top1_win_odds: float | None
    top1_place_odds: float | None
    top1_finish_position: int | None
    top1_place_hit: bool
    top1_win_hit: bool

    @property
    def combo_score(self) -> float:
        """現行tier相当の複合キー: market_agree降順→confidence_score降順の代理連続値。

        market_agree=True を最上位、None を中位、False を最下位として
        confidence_score (0-100) に大きなオフセットを与えて表現する
        （sort key を単一float化するための変換であり、値そのものに意味はない）。
        """
        agree_offset = {True: 2000.0, None: 1000.0, False: 0.0}[self.market_agree]
        return agree_offset + self.confidence_score

    @property
    def chaos_penalty_score(self) -> float | None:
        """混戦度ペナルティ付き複合スコア: confidence_score - entropy_norm * 30。"""
        if self.entropy_norm is None:
            return None
        return self.confidence_score - self.entropy_norm * 30.0

    @property
    def combo_plus_chaos_score(self) -> float | None:
        """現行tier複合キー(combo_score)に entropy ペナルティを追加した拡張スコア。

        (b)現行tier複合キーが最良だった場合に、その上に entropy_norm を
        追加で乗せるとさらに改善するかを見る補助分析用（ユーザー要求外の追加検証）。
        """
        if self.entropy_norm is None:
            return None
        return self.combo_score - self.entropy_norm * 30.0


def get_connection() -> Any:
    """`.env` の DB_* 変数から psycopg2 接続を作成する（train_v26_lightgbm.py と同一パターン）。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


def to_float(x: Any) -> float | None:
    """Decimal/None を float に変換する。"""
    return float(x) if x is not None else None


def fetch_rows(conn: Any) -> list[dict[str, Any]]:
    """診断期間+確認期間を通しで1クエリ取得し、あとで日付により分割する。"""
    cur = conn.cursor()
    cur.execute(
        DATA_QUERY,
        {
            "ver": V26_VERSION,
            "start": DIAGNOSTIC_START,
            "end": CONFIRMATION_END,
            "courses": JRA_COURSES,
        },
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    logger.info(f"取得: {len(rows):,}行 ({DIAGNOSTIC_START}〜{CONFIRMATION_END})")
    return [dict(zip(cols, r)) for r in rows]


def compute_market_chaos(win_odds_all: list[float], head_count: int) -> tuple[float | None, float | None]:
    """単勝オッズから HHI / 正規化 Shannon Entropy を算出する。

    implied probability q_i = 1/odds_i を控除率補正して正規化 p_i = q_i / sum(q_i) とし、
        HHI = sum(p_i^2)                        （1に近いほど混戦度低い＝本命一強）
        entropy_norm = -sum(p_i*ln(p_i)) / ln(head_count)  （0〜1、1に近いほど大混戦）

    Args:
        win_odds_all: レース内の全馬（有効値のみ）の単勝オッズ
        head_count: 出走頭数（entropy_norm の正規化分母に使用）

    Returns:
        (hhi, entropy_norm) のタプル。計算不能な場合は (None, None)
    """
    valid = [o for o in win_odds_all if o is not None and o >= 1.0]
    if head_count < 3 or len(valid) < 2:
        return None, None
    q = [1.0 / o for o in valid]
    total_q = sum(q)
    if total_q <= 0:
        return None, None
    p = [qi / total_q for qi in q]
    hhi = sum(pi**2 for pi in p)
    shannon = -sum(pi * math.log(pi) for pi in p if pi > 0)
    if head_count <= 1:
        return hhi, None
    entropy_norm = shannon / math.log(head_count)
    return hhi, entropy_norm


def analyze_race(head_count: int | None, horses_raw: list[dict[str, Any]]) -> RaceRecord | None:
    """1レース分の生データ行から RaceRecord を構築する。

    出走取消・発走除外馬（abnormality_code != 0）は既存の dm_signals と同じ方針で
    順位計算・信頼度算出の母集団から除外する（[[jra_upset_badge_redesign]] 参照）。
    """
    active = [h for h in horses_raw if (h["abnormality_code"] or 0) == 0]
    if not active:
        return None
    active_with_idx = [h for h in active if h["composite_index"] is not None]
    if not active_with_idx:
        return None

    n = head_count if head_count is not None else len(active)

    composite_indices = [to_float(h["composite_index"]) for h in active_with_idx]
    win_probs = [to_float(h["win_probability"]) for h in active if h["win_probability"] is not None]
    win_odds_all = [to_float(h["win_odds"]) for h in active if h["win_odds"] is not None]

    conf = calculate_race_confidence(composite_indices, n, win_probs or None)

    top1 = max(active_with_idx, key=lambda h: to_float(h["composite_index"]))
    top_odds = to_float(top1["win_odds"])
    market_agree = is_market_favorite(top_odds, win_odds_all or None)
    tier = calculate_recommend_rank(conf["score"], conf.get("win_prob_top"), top_odds, market_agree)

    hhi, entropy_norm = compute_market_chaos(win_odds_all, n)

    finish = top1["finish_position"]
    place_hit = finish is not None and 1 <= finish <= 3
    win_hit = finish is not None and finish == 1

    return RaceRecord(
        race_id=top1["race_id"],
        date=top1["date"],
        course=top1["course"],
        head_count=n,
        confidence_score=conf["score"],
        market_agree=market_agree,
        tier=tier,
        hhi=hhi,
        entropy_norm=entropy_norm,
        top1_win_odds=top_odds,
        top1_place_odds=to_float(top1["place_odds"]),
        top1_finish_position=finish,
        top1_place_hit=place_hit,
        top1_win_hit=win_hit,
    )


def build_race_records(rows: list[dict[str, Any]]) -> list[RaceRecord]:
    """行データをレース単位にグループ化し RaceRecord のリストを作る。"""
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        race_id = row["race_id"]
        entry = grouped.setdefault(
            race_id,
            {"date": row["date"], "course": row["course"], "head_count": row["head_count"], "horses": []},
        )
        entry["horses"].append(row)

    records: list[RaceRecord] = []
    for info in grouped.values():
        rec = analyze_race(info["head_count"], info["horses"])
        if rec is not None:
            records.append(rec)
    return records


def split_diagnostic_confirmation(records: list[RaceRecord]) -> tuple[list[RaceRecord], list[RaceRecord]]:
    diag = [r for r in records if DIAGNOSTIC_START <= r.date <= DIAGNOSTIC_END]
    conf = [r for r in records if CONFIRMATION_START <= r.date <= CONFIRMATION_END]
    return diag, conf


# ---------------------------------------------------------------------------
# セクション A: 既存 tier 別の的中率・ROI
# ---------------------------------------------------------------------------


def tier_stats(records: list[RaceRecord]) -> dict[str, dict[str, Any]]:
    """tier別に n, 複勝的中率, 単勝的中率, 単勝ROI, 複勝ROI(参考) を集計する。"""
    by_tier: dict[str, list[RaceRecord]] = defaultdict(list)
    for r in records:
        by_tier[r.tier].append(r)

    out: dict[str, dict[str, Any]] = {}
    for tier in ("S", "A", "B", "C"):
        rs = by_tier.get(tier, [])
        n = len(rs)
        if n == 0:
            out[tier] = {"n_races": 0}
            continue
        settled = [r for r in rs if r.top1_finish_position is not None]
        n_settled = len(settled)
        place_hits = sum(1 for r in settled if r.top1_place_hit)
        win_hits = sum(1 for r in settled if r.top1_win_hit)

        win_bets = [r for r in settled if r.top1_win_odds is not None]
        win_returns = [(r.top1_win_odds if r.top1_win_hit else 0.0) for r in win_bets]
        win_roi = (sum(win_returns) / len(win_bets)) if win_bets else None

        place_bets = [r for r in settled if r.top1_place_odds is not None]
        place_returns = [(r.top1_place_odds if r.top1_place_hit else 0.0) for r in place_bets]
        place_roi = (sum(place_returns) / len(place_bets)) if place_bets else None

        out[tier] = {
            "n_races": n,
            "n_settled": n_settled,
            "top1_place_pct": round(place_hits / n_settled, 4) if n_settled else None,
            "top1_win_pct": round(win_hits / n_settled, 4) if n_settled else None,
            "win_roi": round(win_roi, 4) if win_roi is not None else None,
            "n_win_bets": len(win_bets),
            "place_roi_ref": round(place_roi, 4) if place_roi is not None else None,
            "n_place_bets_ref": len(place_bets),
        }
    return out


def print_tier_stats_table(title: str, stats: dict[str, dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    header = f"{'tier':<5}{'n_races':>9}{'n_settled':>11}{'複勝的中率':>12}{'単勝的中率':>12}{'単勝ROI':>10}{'複勝ROI(参考)':>14}"
    print(header)
    for tier in ("S", "A", "B", "C"):
        s = stats.get(tier, {"n_races": 0})
        n = s.get("n_races", 0)
        if n == 0:
            print(f"{tier:<5}{0:>9}")
            continue
        place_pct = s.get("top1_place_pct")
        win_pct = s.get("top1_win_pct")
        win_roi = s.get("win_roi")
        place_roi = s.get("place_roi_ref")
        flag = "" if n >= MIN_SAMPLE_FOR_CONCLUSION else " (参考:n<100)"
        print(
            f"{tier:<5}{n:>9}{s.get('n_settled', 0):>11}"
            f"{(f'{place_pct:.1%}' if place_pct is not None else '-'):>12}"
            f"{(f'{win_pct:.1%}' if win_pct is not None else '-'):>12}"
            f"{(f'{win_roi:.3f}' if win_roi is not None else '-'):>10}"
            f"{(f'{place_roi:.3f}' if place_roi is not None else '-'):>14}{flag}"
        )


# ---------------------------------------------------------------------------
# セクション B: tier内 entropy_norm 分割
# ---------------------------------------------------------------------------


def median_entropy_by_tier(records: list[RaceRecord]) -> dict[str, float]:
    """診断期間の tier別 entropy_norm 中央値を算出する（entropy_norm 計算不能レースは除外）。"""
    out: dict[str, float] = {}
    by_tier: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.entropy_norm is not None:
            by_tier[r.tier].append(r.entropy_norm)
    for tier, vals in by_tier.items():
        if vals:
            out[tier] = statistics.median(vals)
    return out


def entropy_split_stats(records: list[RaceRecord], tier: str, threshold: float) -> dict[str, Any]:
    """指定 tier・固定 entropy_norm 閾値で低/高2グループに分け複勝的中率を比較する。

    threshold は呼び出し側から渡す固定値（診断期間の中央値をそのまま使う想定）。
    このレコード群自身から再計算はしない。
    """
    subset = [r for r in records if r.tier == tier and r.entropy_norm is not None]
    low = [r for r in subset if r.entropy_norm <= threshold]
    high = [r for r in subset if r.entropy_norm > threshold]

    def _rate(rs: list[RaceRecord]) -> dict[str, Any]:
        settled = [r for r in rs if r.top1_finish_position is not None]
        n = len(settled)
        hits = sum(1 for r in settled if r.top1_place_hit)
        return {
            "n": len(rs),
            "n_settled": n,
            "place_hit_rate": round(hits / n, 4) if n else None,
        }

    return {
        "tier": tier,
        "threshold": round(threshold, 4),
        "low_entropy": _rate(low),
        "high_entropy": _rate(high),
    }


# ---------------------------------------------------------------------------
# セクション C: Risk-Coverage 曲線
# ---------------------------------------------------------------------------

COVERAGE_DECILES = [i / 10 for i in range(1, 11)]


def risk_coverage_curve(records: list[RaceRecord], key_fn: Any) -> list[dict[str, Any]]:
    """key_fn(r) 降順にソートし、各カバレッジ decile での累積複勝的中率を計算する。

    settled（finish_position が確定している）レコードのみを対象にする。
    """
    settled = [r for r in records if r.top1_finish_position is not None]
    ordered = sorted(settled, key=key_fn, reverse=True)
    total = len(ordered)
    out: list[dict[str, Any]] = []
    for cov in COVERAGE_DECILES:
        n = max(1, round(total * cov))
        top_n = ordered[:n]
        hits = sum(1 for r in top_n if r.top1_place_hit)
        out.append(
            {
                "coverage_pct": int(cov * 100),
                "n": n,
                "place_hit_rate": round(hits / n, 4) if n else None,
            }
        )
    return out


def print_risk_coverage_table(title: str, curves: dict[str, list[dict[str, Any]]]) -> None:
    print(f"\n=== {title} ===")
    keys = list(curves.keys())
    header = f"{'cov%':>6}" + "".join(f"{k:>18}" for k in keys)
    print(header)
    n_rows = len(next(iter(curves.values())))
    for i in range(n_rows):
        row = f"{curves[keys[0]][i]['coverage_pct']:>6}"
        for k in keys:
            entry = curves[k][i]
            rate = entry["place_hit_rate"]
            n_val = entry["n"]
            cell = f"{rate:.1%} (n={n_val})" if rate is not None else "-"
            row += f"{cell:>18}"
        print(row)


def curve_mean_hit_rate(curve: list[dict[str, Any]]) -> float:
    """曲線全体の平均的中率（複数 decile の単純平均）。曲線比較の粗い要約指標。"""
    vals = [c["place_hit_rate"] for c in curve if c["place_hit_rate"] is not None]
    return sum(vals) / len(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    conn = get_connection()
    try:
        rows = fetch_rows(conn)
    finally:
        conn.close()

    records = build_race_records(rows)
    logger.info(f"レース単位レコード構築完了: {len(records):,}件")

    diag, conf = split_diagnostic_confirmation(records)
    logger.info(f"診断期間: {len(diag):,}レース ({DIAGNOSTIC_START}〜{DIAGNOSTIC_END})")
    logger.info(f"確認期間: {len(conf):,}レース ({CONFIRMATION_START}〜{CONFIRMATION_END})")

    summary: dict[str, Any] = {
        "diagnostic_period": [DIAGNOSTIC_START, DIAGNOSTIC_END],
        "confirmation_period": [CONFIRMATION_START, CONFIRMATION_END],
        "n_races_diagnostic": len(diag),
        "n_races_confirmation": len(conf),
    }

    # --- セクション A ---
    diag_tier_stats = tier_stats(diag)
    conf_tier_stats = tier_stats(conf)
    print_tier_stats_table("セクションA: 既存tier別 的中率・ROI（診断期間）", diag_tier_stats)
    print_tier_stats_table("セクションA: 既存tier別 的中率・ROI（確認期間）", conf_tier_stats)
    summary["section_a"] = {"diagnostic": diag_tier_stats, "confirmation": conf_tier_stats}

    # --- セクション B ---
    diag_medians = median_entropy_by_tier(diag)
    print("\n=== セクションB: tier別 entropy_norm 中央値（診断期間で決定・固定値として確認期間へ適用） ===")
    for tier in ("S", "A", "B", "C"):
        print(f"  tier={tier}: median entropy_norm = {diag_medians.get(tier)}")

    section_b: dict[str, Any] = {"thresholds_from_diagnostic": diag_medians, "diagnostic": {}, "confirmation": {}}
    print("\n=== セクションB: entropy_norm 低/高分割による複勝的中率の差 ===")
    for tier in ("S", "A", "B", "C"):
        if tier not in diag_medians:
            print(f"  tier={tier}: entropy_norm 計算可能レース無し（スキップ）")
            continue
        threshold = diag_medians[tier]
        diag_split = entropy_split_stats(diag, tier, threshold)
        conf_split = entropy_split_stats(conf, tier, threshold)
        section_b["diagnostic"][tier] = diag_split
        section_b["confirmation"][tier] = conf_split

        d_low, d_high = diag_split["low_entropy"], diag_split["high_entropy"]
        c_low, c_high = conf_split["low_entropy"], conf_split["high_entropy"]
        print(f"\n  --- tier={tier} (threshold entropy_norm={threshold:.4f}) ---")
        print(
            f"    診断期間: low(混戦度低)  n={d_low['n']:>5} 複勝的中率={d_low['place_hit_rate']}"
            f" / high(混戦度高) n={d_high['n']:>5} 複勝的中率={d_high['place_hit_rate']}"
        )
        print(
            f"    確認期間: low(混戦度低)  n={c_low['n']:>5} 複勝的中率={c_low['place_hit_rate']}"
            f" / high(混戦度高) n={c_high['n']:>5} 複勝的中率={c_high['place_hit_rate']}"
        )
        diag_gap = None
        conf_gap = None
        if d_low["place_hit_rate"] is not None and d_high["place_hit_rate"] is not None:
            diag_gap = d_low["place_hit_rate"] - d_high["place_hit_rate"]
        if c_low["place_hit_rate"] is not None and c_high["place_hit_rate"] is not None:
            conf_gap = c_low["place_hit_rate"] - c_high["place_hit_rate"]
        reproduced = (
            diag_gap is not None
            and conf_gap is not None
            and diag_gap > 0
            and conf_gap > 0
            and d_low["n"] >= MIN_SAMPLE_FOR_CONCLUSION
            and c_low["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        )
        print(f"    診断期間 low-high差: {diag_gap}  / 確認期間 low-high差: {conf_gap}  → 再現: {reproduced}")
        section_b["diagnostic"][tier]["gap"] = diag_gap
        section_b["confirmation"][tier]["gap"] = conf_gap
        section_b["confirmation"][tier]["reproduced"] = reproduced

    # --- セクション C ---
    # 3曲線を公平に比較するため、entropy_norm が計算可能なレースのみを共通母集団とする
    diag_universe = [r for r in diag if r.entropy_norm is not None]
    conf_universe = [r for r in conf if r.entropy_norm is not None]
    logger.info(f"Risk-Coverage 母集団: 診断={len(diag_universe):,} / 確認={len(conf_universe):,}")

    key_confidence = lambda r: r.confidence_score  # noqa: E731
    key_combo = lambda r: r.combo_score  # noqa: E731
    key_chaos_penalty = lambda r: (r.chaos_penalty_score if r.chaos_penalty_score is not None else -999.0)  # noqa: E731
    key_combo_plus_chaos = lambda r: (  # noqa: E731
        r.combo_plus_chaos_score if r.combo_plus_chaos_score is not None else -999.0
    )

    diag_curves = {
        "(a)confidence単独": risk_coverage_curve(diag_universe, key_confidence),
        "(b)現行tier複合": risk_coverage_curve(diag_universe, key_combo),
        "(c)混戦度ペナルティ": risk_coverage_curve(diag_universe, key_chaos_penalty),
        "(d)tier複合+混戦度": risk_coverage_curve(diag_universe, key_combo_plus_chaos),
    }
    print_risk_coverage_table("セクションC: Risk-Coverage曲線（診断期間）", diag_curves)

    curve_means = {k: curve_mean_hit_rate(v) for k, v in diag_curves.items()}
    best_curve_name = max(curve_means, key=lambda k: curve_means[k])
    print(f"\n診断期間 曲線別平均的中率(要約指標): {curve_means}")
    print(f"→ 診断期間で最も有望: {best_curve_name}")
    print(
        "  ※ (d)は補助分析: (b)現行tier複合キーにさらにentropyペナルティを足した場合、"
        "(b)単独から改善するかを見る（『既存tierの上に混戦度を追加する価値があるか』の直接テスト）"
    )

    conf_curves = {
        "(a)confidence単独": risk_coverage_curve(conf_universe, key_confidence),
        "(b)現行tier複合": risk_coverage_curve(conf_universe, key_combo),
        "(c)混戦度ペナルティ": risk_coverage_curve(conf_universe, key_chaos_penalty),
        "(d)tier複合+混戦度": risk_coverage_curve(conf_universe, key_combo_plus_chaos),
    }
    print_risk_coverage_table("セクションC: Risk-Coverage曲線（確認期間・同一計算式で再検証）", conf_curves)
    conf_curve_means = {k: curve_mean_hit_rate(v) for k, v in conf_curves.items()}
    print(f"\n確認期間 曲線別平均的中率(要約指標): {conf_curve_means}")
    print(
        f"確認期間でも '{best_curve_name}' が最良か: "
        f"{max(conf_curve_means, key=lambda k: conf_curve_means[k]) == best_curve_name}"
    )
    print(
        f"(d)が(b)を上回るか: 診断期間 {curve_means['(d)tier複合+混戦度'] > curve_means['(b)現行tier複合']}"
        f" / 確認期間 {conf_curve_means['(d)tier複合+混戦度'] > conf_curve_means['(b)現行tier複合']}"
    )

    summary["section_c"] = {
        "diagnostic_universe_n": len(diag_universe),
        "confirmation_universe_n": len(conf_universe),
        "diagnostic_curves": diag_curves,
        "confirmation_curves": conf_curves,
        "diagnostic_curve_means": curve_means,
        "confirmation_curve_means": conf_curve_means,
        "best_curve_diagnostic": best_curve_name,
    }
    summary["section_b"] = section_b

    OUTPUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    logger.info(f"サマリー保存: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
