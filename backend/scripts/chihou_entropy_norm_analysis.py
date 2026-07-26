"""地方競馬(chihou) 市場混戦度(entropy_norm) 展開検証スクリプト。

研究用スクリプト。DB 書き込みは一切行わない（読み取り専用クエリのみ）。

背景:
    JRA では単勝オッズの implied probability から算出する市場混戦度スコア
    entropy_norm（`src/indices/confidence.py::calculate_market_chaos`）を、
    既存 tier 方式（S/A/B/C）の tier=C 内でさらに分割する上乗せ指標として
    診断期間(2023-07〜2025-12)→確認期間(2026-01〜07)の二段階 honest 検証で
    複勝的中率 11〜13pt の分離を確認済み・本番実装済み
    （`backend/scripts/jra_phase3_market_chaos_analysis.py` 参照）。

    chihou の本番推奨は JRA の tier 方式と異なり、ランキング規則ベースの
    4カテゴリ（`src/indices/buy_signal.py`）:
      - chihou_is_sweet_spot   : 指数1位 ∧ 単勝10-30倍 ∧ 割安5場
      - chihou_is_place_bet    : 断然人気R ∧ 単勝≥10 ∧ 指数3位以内 ∧ 頭数≥8
      - chihou_low_odds_trust_level: 単勝<1.5="trusted" / 1.5≤単勝<2.0="untrusted"

    本スクリプトは、これら4カテゴリの該当馬群を entropy_norm の中央値
    （診断期間で決定・固定値として確認期間へ適用）で2分割し、上乗せ効果が
    診断・確認の両方で再現するかを検証する。加えて、いずれのカテゴリにも
    属さない中間層で entropy_norm 単体の新セグメントを副次的に探索する。

    `calculate_market_chaos` / `chihou_is_sweet_spot` / `chihou_is_place_bet` /
    `chihou_low_odds_trust_level` はいずれもそのまま import して使用し、
    ロジックの再実装は行わない。

母集団:
    chihou.races で course != '83'（JRA除外）∧ head_count >= 8
    （entropy_norm 算出に十分な頭数が必要、かつ chihou_is_place_bet も
      head_count>=8 前提のため統一）。
    composite_index / win_probability は `chihou_rebuild_walkforward.py` の
    FULL_POP_QUERY と同一の DISTINCT ON パターン
    （`WHERE version >= CHIHOU_V9_VERSION ORDER BY ... (version = ver) DESC,
      version DESC`）で「そのレースで利用可能な代表バージョン」を1行に確定し、
    出走予定馬全体（LEFT JOIN race_results）を母集団に idx_rank を計算する
    （2026-07-23 監査で修正済みの生存者バイアス回避方式）。
    win_odds は `chihou.race_results.win_odds`（確定オッズ）を使用する
    （`chihou.odds_history` は2026-04-07以降のみ・5900万行超のため対象外）。

期間設計（多重比較・チェリーピッキング回避のため厳格に分離する）:
    - 全期間: 2023-01-01 〜 データ最新日（実行時点で動的に決定）
    - 診断期間: 全期間の前半70%（カレンダー日数ベース） — パターン探索用
    - 確認期間: 全期間の後半30% — 完全ホールドアウト。診断期間で決めた
      entropy_norm 閾値（中央値）をそのまま（再フィッティングせず）適用して
      再現性のみ確認する。

出力:
    標準出力にセクション A（カテゴリ別基礎統計）/ B（entropy_norm 分割）/
    C（副次探索）を表示。
    `backend/models/chihou_entropy_norm_analysis.json` にサマリーを保存する。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_entropy_norm_analysis.py
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402

from scripts.train_chihou_market_lgb import CHIHOU_V9_VERSION  # noqa: E402
from src.indices.buy_signal import (  # noqa: E402
    chihou_is_place_bet,
    chihou_is_sweet_spot,
    chihou_low_odds_trust_level,
)
from src.indices.confidence import calculate_market_chaos  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_entropy_norm")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_JSON = MODELS_DIR / "chihou_entropy_norm_analysis.json"

FULL_START = "20230101"
MIN_HEAD_COUNT = 8
MIN_SAMPLE_FOR_CONCLUSION = 100  # これ未満は参考値扱い
DIAGNOSTIC_FRACTION = 0.7  # カレンダー日数ベースで前半70%を診断期間にする

# 出走予定馬全体（LEFT JOIN）を母集団にする Phase0/walk-forward 修正済みクエリパターン
# （chihou_rebuild_walkforward.py::FULL_POP_QUERY を composite_index/win_probability
#   のみに簡略化したもの）
POP_QUERY = """
SELECT
    r.id AS race_id,
    r.date,
    r.course_name,
    r.head_count,
    re.horse_number,
    ci.composite_index,
    ci.win_probability,
    rr.win_odds,
    rr.place_odds,
    rr.finish_position,
    COALESCE(rr.abnormality_code, 0) AS abnormality_code
FROM (
    SELECT DISTINCT ON (race_id, horse_id)
        race_id, horse_id, composite_index, win_probability
    FROM chihou.calculated_indices
    WHERE version >= %(ver)s
    ORDER BY race_id, horse_id, (version = %(ver)s) DESC, version DESC
) ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE r.course != '83'
  AND r.head_count >= %(min_head)s
  AND r.date >= %(start)s
  AND r.date <= %(end)s
ORDER BY r.date, ci.race_id
"""


def get_connection() -> Any:
    """`.env` の DB_* 変数から psycopg2 接続を作成する（既存の chihou 研究スクリプトと同一パターン）。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


def to_float(x: Any) -> float | None:
    """Decimal/None を float に変換する。"""
    return float(x) if x is not None else None


def today_jst_str() -> str:
    """JST 現在日を YYYYMMDD 文字列で返す（CLAUDE.md 既定のタイムゾーン規約）。"""
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d")


def fetch_rows(conn: Any, end_date: str) -> list[dict[str, Any]]:
    """全期間(FULL_START〜end_date)を1クエリで取得する。"""
    cur = conn.cursor()
    cur.execute(POP_QUERY, {"ver": CHIHOU_V9_VERSION, "min_head": MIN_HEAD_COUNT, "start": FULL_START, "end": end_date})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    logger.info(f"取得: {len(rows):,}行 ({FULL_START}〜{end_date})")
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# レース単位の集計 → 馬単位レコード構築
# ---------------------------------------------------------------------------


@dataclass
class HorseRecord:
    """1頭・1レースぶんの分析用レコード。"""

    race_id: int
    date: str
    course_name: str
    head_count: int
    horse_number: int
    idx_rank: int | None
    win_odds: float | None
    place_odds: float | None
    finish_position: int | None
    abnormality_code: int
    entropy_norm: float | None
    hhi: float | None
    is_sweet_spot: bool
    is_place_bet: bool
    low_odds_level: str | None

    @property
    def settled(self) -> bool:
        return (
            self.finish_position is not None
            and self.abnormality_code == 0
            and self.win_odds is not None
            and self.win_odds >= 1.0
        )

    @property
    def win_hit(self) -> bool:
        return self.finish_position == 1

    @property
    def place_hit(self) -> bool:
        return self.finish_position is not None and 1 <= self.finish_position <= 3


def build_race_records(rows: list[dict[str, Any]]) -> list[HorseRecord]:
    """行データをレース単位にグループ化し、entropy_norm・idx_rank・カテゴリ判定を付与する。

    idx_rank は出走予定馬全体（取消・失格含む LEFT JOIN 結果）を母集団に、
    composite_index 降順で確定する（aggregate_chihou_recent.py の
    `_apply_production_rules` と同一方針）。sweet_spot/place_bet は本番の
    `chihou_recommender.py` と同様「該当馬が3頭以上(k>=3)の混戦レースは
    カテゴリ対象外」という後段フィルタも再現する。
    """
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["race_id"],
            {"date": row["date"], "course_name": row["course_name"], "head_count": row["head_count"], "horses": []},
        )
        entry["horses"].append(row)

    records: list[HorseRecord] = []
    for race_id, info in grouped.items():
        horses = info["horses"]
        course_name = info["course_name"]
        head_count = info["head_count"]

        all_win_odds = [to_float(h["win_odds"]) for h in horses]
        chaos = calculate_market_chaos(all_win_odds)
        entropy_norm = chaos["entropy_norm"]
        hhi = chaos["hhi"]

        with_idx = [h for h in horses if h["composite_index"] is not None]
        with_idx_sorted = sorted(with_idx, key=lambda h: -to_float(h["composite_index"]))
        rank_by_hn: dict[int, int] = {h["horse_number"]: i + 1 for i, h in enumerate(with_idx_sorted)}

        valid_odds = [
            to_float(h["win_odds"]) for h in horses if h["win_odds"] is not None and to_float(h["win_odds"]) >= 1.0
        ]
        fav_odds = min(valid_odds) if valid_odds else None

        raw_sweet: set[int] = set()
        raw_place: set[int] = set()
        for h in horses:
            hn = h["horse_number"]
            idx_rank = rank_by_hn.get(hn)
            wo = to_float(h["win_odds"])
            if chihou_is_sweet_spot(idx_rank, wo, course_name):
                raw_sweet.add(hn)
            if chihou_is_place_bet(idx_rank, wo, fav_odds, head_count):
                raw_place.add(hn)
        sweet_k_ok = len(raw_sweet) < 3
        place_k_ok = len(raw_place) < 3

        for h in horses:
            hn = h["horse_number"]
            idx_rank = rank_by_hn.get(hn)
            wo = to_float(h["win_odds"])
            records.append(
                HorseRecord(
                    race_id=race_id,
                    date=info["date"],
                    course_name=course_name,
                    head_count=head_count,
                    horse_number=hn,
                    idx_rank=idx_rank,
                    win_odds=wo,
                    place_odds=to_float(h["place_odds"]),
                    finish_position=h["finish_position"],
                    abnormality_code=h["abnormality_code"] or 0,
                    entropy_norm=entropy_norm,
                    hhi=hhi,
                    is_sweet_spot=(hn in raw_sweet) and sweet_k_ok,
                    is_place_bet=(hn in raw_place) and place_k_ok,
                    low_odds_level=chihou_low_odds_trust_level(wo),
                )
            )
    return records


def compute_split_date(start: str, end: str, frac: float) -> str:
    """FULL_START〜end のカレンダー日数を frac:1-frac で分割する日付を返す。"""
    d0 = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    d1 = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    split = d0 + timedelta(days=int((d1 - d0).days * frac))
    return split.strftime("%Y%m%d")


def split_diagnostic_confirmation(
    records: list[HorseRecord], split_date: str
) -> tuple[list[HorseRecord], list[HorseRecord]]:
    diag = [r for r in records if r.date < split_date]
    conf = [r for r in records if r.date >= split_date]
    return diag, conf


# ---------------------------------------------------------------------------
# セクション A: カテゴリ別 基礎統計
# ---------------------------------------------------------------------------


def category_subset(records: list[HorseRecord], category: str) -> list[HorseRecord]:
    """カテゴリ名からその該当馬レコード群を抽出する（本番と同一の選定ロジック）。"""
    if category == "sweet_spot":
        return [r for r in records if r.is_sweet_spot]
    if category == "place_bet":
        return [r for r in records if r.is_place_bet]
    if category in ("low_odds_trusted", "low_odds_untrusted"):
        level = category.removeprefix("low_odds_")
        candidates = [r for r in records if r.low_odds_level == level and r.settled]
        # 本番は「レース内最低オッズ馬1頭のみ採用」(chihou_recommender.py 低オッズ本命セクション)
        by_race: dict[int, HorseRecord] = {}
        for r in sorted(candidates, key=lambda r: (r.race_id, r.win_odds if r.win_odds is not None else 999.0)):
            by_race.setdefault(r.race_id, r)
        return list(by_race.values())
    raise ValueError(f"unknown category: {category}")


CATEGORY_BET_TYPE: dict[str, str] = {
    "sweet_spot": "win",
    "place_bet": "place",
    "low_odds_trusted": "win",
    "low_odds_untrusted": "win",
}


def bet_stats(records: list[HorseRecord], bet: str) -> dict[str, Any]:
    """settled レコード群から的中率・ROI を計算する。"""
    settled = [r for r in records if r.settled]
    n = len(settled)
    if n == 0:
        return {"n": 0, "hit_rate": None, "roi": None, "n_roi": 0}
    if bet == "win":
        hits = sum(1 for r in settled if r.win_hit)
        roi = sum((r.win_odds if r.win_hit else 0.0) for r in settled) / n
        return {"n": n, "hit_rate": round(hits / n, 4), "roi": round(roi, 4), "n_roi": n}
    # place
    hits = sum(1 for r in settled if r.place_hit)
    hit_rate = hits / n
    valid = [r for r in settled if r.place_odds is not None]
    n_roi = len(valid)
    roi = None
    if n_roi:
        roi = sum((r.place_odds if r.place_hit else 0.0) for r in valid) / n_roi
    return {"n": n, "hit_rate": round(hit_rate, 4), "roi": round(roi, 4) if roi is not None else None, "n_roi": n_roi}


# ---------------------------------------------------------------------------
# セクション B: カテゴリ内 entropy_norm 分割
# ---------------------------------------------------------------------------


def median_entropy(records: list[HorseRecord]) -> float | None:
    vals = [r.entropy_norm for r in records if r.entropy_norm is not None]
    return statistics.median(vals) if vals else None


def split_by_entropy(records: list[HorseRecord], threshold: float) -> tuple[list[HorseRecord], list[HorseRecord]]:
    with_e = [r for r in records if r.entropy_norm is not None]
    low = [r for r in with_e if r.entropy_norm <= threshold]
    high = [r for r in with_e if r.entropy_norm > threshold]
    return low, high


def analyze_category_entropy_split(
    category: str, diag_pool: list[HorseRecord], conf_pool: list[HorseRecord]
) -> dict[str, Any]:
    """診断期間の中央値で低/高分割し、確認期間へ同一閾値を適用して再現性を見る。"""
    bet = CATEGORY_BET_TYPE[category]
    diag_sub = category_subset(diag_pool, category)
    conf_sub = category_subset(conf_pool, category)

    threshold = median_entropy(diag_sub)
    result: dict[str, Any] = {"category": category, "bet_type": bet, "threshold": threshold}
    print(f"\n  --- カテゴリ={category} (bet={bet}) ---")
    print(f"    診断期間 全体: {bet_stats(diag_sub, bet)}")
    print(f"    確認期間 全体: {bet_stats(conf_sub, bet)}")
    result["diagnostic_overall"] = bet_stats(diag_sub, bet)
    result["confirmation_overall"] = bet_stats(conf_sub, bet)

    if threshold is None:
        print("    entropy_norm 算出可能レコードなし（スキップ）")
        result["reproduced"] = None
        return result

    d_low, d_high = split_by_entropy(diag_sub, threshold)
    c_low, c_high = split_by_entropy(conf_sub, threshold)

    d_low_s, d_high_s = bet_stats(d_low, bet), bet_stats(d_high, bet)
    c_low_s, c_high_s = bet_stats(c_low, bet), bet_stats(c_high, bet)

    print(f"    threshold(診断期間中央値 entropy_norm) = {threshold:.4f}")
    print(f"    診断期間: low(混戦度低) n={d_low_s['n']:>5} 的中率={d_low_s['hit_rate']} ROI={d_low_s['roi']}")
    print(f"              high(混戦度高) n={d_high_s['n']:>5} 的中率={d_high_s['hit_rate']} ROI={d_high_s['roi']}")
    print(f"    確認期間: low(混戦度低) n={c_low_s['n']:>5} 的中率={c_low_s['hit_rate']} ROI={c_low_s['roi']}")
    print(f"              high(混戦度高) n={c_high_s['n']:>5} 的中率={c_high_s['hit_rate']} ROI={c_high_s['roi']}")

    def _gap(low_s: dict[str, Any], high_s: dict[str, Any]) -> float | None:
        if low_s["hit_rate"] is None or high_s["hit_rate"] is None:
            return None
        return round(low_s["hit_rate"] - high_s["hit_rate"], 4)

    diag_gap = _gap(d_low_s, d_high_s)
    conf_gap = _gap(c_low_s, c_high_s)
    enough_n = (
        d_low_s["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and d_high_s["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and c_low_s["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and c_high_s["n"] >= MIN_SAMPLE_FOR_CONCLUSION
    )
    reproduced = (
        diag_gap is not None
        and conf_gap is not None
        and abs(diag_gap) > 0.005
        and (diag_gap > 0) == (conf_gap > 0)
        and enough_n
    )
    print(f"    診断期間 low-high的中率差: {diag_gap}  確認期間 low-high的中率差: {conf_gap}")
    print(f"    → 再現(方向一致 ∧ n>=100全群): {reproduced}" + ("" if enough_n else "  ※n<100群あり・参考程度"))

    result["diagnostic_low"] = d_low_s
    result["diagnostic_high"] = d_high_s
    result["confirmation_low"] = c_low_s
    result["confirmation_high"] = c_high_s
    result["diagnostic_gap"] = diag_gap
    result["confirmation_gap"] = conf_gap
    result["enough_n"] = enough_n
    result["reproduced"] = reproduced
    return result


# ---------------------------------------------------------------------------
# セクション C: 副次探索 — 既存カテゴリ非該当層での新セグメント探索
# ---------------------------------------------------------------------------


def other_pool(records: list[HorseRecord]) -> list[HorseRecord]:
    """既存4カテゴリいずれにも該当しない「対象外」の中間層。"""
    return [
        r
        for r in records
        if not r.is_sweet_spot and not r.is_place_bet and r.low_odds_level is None
    ]


def explore_low_entropy_favorite(diag_pool: list[HorseRecord], conf_pool: list[HorseRecord]) -> dict[str, Any]:
    """副次探索: 対象外層×指数1位×極端な低entropy(一強レース)の単勝的中率を見る。

    diag_pool/conf_pool は既に settled 前提でなくてよい（内部で settled 化する）。
    閾値(entropy_norm 25%分位点)は診断期間で決定し、確認期間にそのまま適用する。
    """
    diag_other = [r for r in other_pool(diag_pool) if r.idx_rank == 1 and r.entropy_norm is not None]
    conf_other = [r for r in other_pool(conf_pool) if r.idx_rank == 1 and r.entropy_norm is not None]

    if not diag_other:
        return {"note": "診断期間に対象外×指数1位のレコードなし"}

    vals = sorted(r.entropy_norm for r in diag_other)
    q25 = vals[int(len(vals) * 0.25)]

    diag_low = [r for r in diag_other if r.entropy_norm <= q25]
    diag_high = [r for r in diag_other if r.entropy_norm > q25]
    conf_low = [r for r in conf_other if r.entropy_norm <= q25]
    conf_high = [r for r in conf_other if r.entropy_norm > q25]

    d_low_s, d_high_s = bet_stats(diag_low, "win"), bet_stats(diag_high, "win")
    c_low_s, c_high_s = bet_stats(conf_low, "win"), bet_stats(conf_high, "win")

    print("\n  --- 副次探索: 対象外(4カテゴリ非該当)×指数1位×entropy_norm下位25%(一強レース) ---")
    print(f"    threshold(診断期間25%分位 entropy_norm) = {q25:.4f}")
    print(f"    診断期間: low-entropy(一強) n={d_low_s['n']:>5} 単勝的中率={d_low_s['hit_rate']} ROI={d_low_s['roi']}")
    print(f"              それ以外       n={d_high_s['n']:>5} 単勝的中率={d_high_s['hit_rate']} ROI={d_high_s['roi']}")
    print(f"    確認期間: low-entropy(一強) n={c_low_s['n']:>5} 単勝的中率={c_low_s['hit_rate']} ROI={c_low_s['roi']}")
    print(f"              それ以外       n={c_high_s['n']:>5} 単勝的中率={c_high_s['hit_rate']} ROI={c_high_s['roi']}")

    def _gap(low_s: dict[str, Any], high_s: dict[str, Any]) -> float | None:
        if low_s["hit_rate"] is None or high_s["hit_rate"] is None:
            return None
        return round(low_s["hit_rate"] - high_s["hit_rate"], 4)

    diag_gap = _gap(d_low_s, d_high_s)
    conf_gap = _gap(c_low_s, c_high_s)
    enough_n = (
        d_low_s["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and c_low_s["n"] >= MIN_SAMPLE_FOR_CONCLUSION
    )
    reproduced = (
        diag_gap is not None and conf_gap is not None and (diag_gap > 0) == (conf_gap > 0) and enough_n
    )
    print(f"    診断期間 差: {diag_gap}  確認期間 差: {conf_gap}  → 再現(参考): {reproduced}" + ("" if enough_n else "  ※n<100群あり・参考程度"))

    return {
        "threshold_q25": q25,
        "diagnostic_low": d_low_s,
        "diagnostic_high": d_high_s,
        "confirmation_low": c_low_s,
        "confirmation_high": c_high_s,
        "diagnostic_gap": diag_gap,
        "confirmation_gap": conf_gap,
        "enough_n": enough_n,
        "reproduced": reproduced,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    end_date = today_jst_str()
    conn = get_connection()
    try:
        rows = fetch_rows(conn, end_date)
    finally:
        conn.close()

    if not rows:
        logger.error("データが取得できませんでした")
        return

    actual_end = max(r["date"] for r in rows)
    records = build_race_records(rows)
    logger.info(f"馬単位レコード構築完了: {len(records):,}件 / {len({r.race_id for r in records}):,}レース")

    split_date = compute_split_date(FULL_START, actual_end, DIAGNOSTIC_FRACTION)
    diag, conf = split_diagnostic_confirmation(records, split_date)
    n_races_diag = len({r.race_id for r in diag})
    n_races_conf = len({r.race_id for r in conf})

    print(f"\n{'=' * 78}")
    print("  地方競馬 entropy_norm 展開検証")
    print(f"  全期間: {FULL_START} 〜 {actual_end}")
    print(f"  診断期間: {FULL_START} 〜 {split_date} ({n_races_diag:,}レース)")
    print(f"  確認期間: {split_date} 〜 {actual_end} ({n_races_conf:,}レース)")
    print(f"{'=' * 78}")

    summary: dict[str, Any] = {
        "full_period": [FULL_START, actual_end],
        "diagnostic_period": [FULL_START, split_date],
        "confirmation_period": [split_date, actual_end],
        "n_races_diagnostic": n_races_diag,
        "n_races_confirmation": n_races_conf,
        "min_head_count": MIN_HEAD_COUNT,
        "min_sample_for_conclusion": MIN_SAMPLE_FOR_CONCLUSION,
    }

    # --- セクションA: カテゴリ別 基礎統計 ---
    print("\n=== セクションA: カテゴリ別 基礎統計（entropy_norm分割前） ===")
    section_a: dict[str, Any] = {}
    for category, bet in CATEGORY_BET_TYPE.items():
        diag_sub = category_subset(diag, category)
        conf_sub = category_subset(conf, category)
        d_stats = bet_stats(diag_sub, bet)
        c_stats = bet_stats(conf_sub, bet)
        print(f"  [{category}] bet={bet}")
        print(f"    診断期間: n={d_stats['n']:,}  的中率={d_stats['hit_rate']}  ROI={d_stats['roi']}")
        print(f"    確認期間: n={c_stats['n']:,}  的中率={c_stats['hit_rate']}  ROI={c_stats['roi']}")
        section_a[category] = {"diagnostic": d_stats, "confirmation": c_stats}
    summary["section_a"] = section_a

    # --- セクションB: entropy_norm 分割 ---
    print(f"\n{'=' * 78}\n=== セクションB: カテゴリ内 entropy_norm 分割（診断中央値→確認期間で再現確認） ===\n{'=' * 78}")
    section_b: dict[str, Any] = {}
    for category in CATEGORY_BET_TYPE:
        section_b[category] = analyze_category_entropy_split(category, diag, conf)
    summary["section_b"] = section_b

    # --- セクションC: 副次探索 ---
    print(f"\n{'=' * 78}\n=== セクションC: 副次探索（対象外層での新セグメント） ===\n{'=' * 78}")
    section_c = explore_low_entropy_favorite(diag, conf)
    summary["section_c"] = section_c

    OUTPUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    logger.info(f"サマリー保存: {OUTPUT_JSON}")

    print(f"\n{'=' * 78}\n  完了\n{'=' * 78}")


if __name__ == "__main__":
    main()
