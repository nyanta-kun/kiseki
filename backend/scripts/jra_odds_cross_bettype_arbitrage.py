"""JRA 予備検証: 単勝×複勝オッズ間の Harville 理論整合性から市場の歪みを検出できるか

研究用スクリプト。DB 書き込みは一切行わない（読み取り専用クエリのみ）。本番実装はしない。

背景・目的:
    単勝オッズが「正しい」市場評価だと仮定すると、Harville(1973) 公式で
    各馬の理論複勝確率（3着以内）を単勝オッズから逆算できる。一方、複勝オッズは
    複勝市場という別のプールで独立に値付けされている。もし両者が理論的に整合しない
    （複勝市場が単勝オッズから導かれる理論値より歪んでいる）馬群があり、かつその歪みが
    実際の複勝的中率と相関するなら、単勝オッズを基準に複勝オッズの歪みを検出する
    「クロス券種裁定」シグナルが存在する可能性がある。

⚠️ 致命的な制約（必ず結論に明記すること）:
    `keiba.odds_history` は 2026-03-28 開始・約5ヶ月分しかデータがなく、
    JRA10場・head_count>=8 で約1,200レース程度しかない（`jra_phase4a_odds_movement_analysis.py`
    と同様の制約）。Phase3 (`jra_phase3_market_chaos_analysis.py`) が使った
    「診断期間(2年+)→確認期間(半年)」の二段階ホールドアウト検証には全く足りない。
    本スクリプトは単一 hold-out 分割（前半60%探索/後半40%確認）による
    簡易一貫性チェックに限定し、結果は「参考値・信頼度低め」として扱う。

Harville 複勝確率の実装について:
    新規実装はしない。本プロジェクトには既に共有実装
    `src/betting/odds_model.py::harville_win_probs_from_odds` /
    `src/betting/odds_model.py::_harville_place_probs` があり、
    `tests/test_odds_model.py` で健全性検証済み・`scripts/validate_odds_approximation.py`
    等からも import されている（このリポジトリでの「Harville実装は1箇所に集約する」慣行）。
    本スクリプトはこれをそのまま import して使う。

対象母集団:
    head_count>=8 ∧ course in JRA10場 ∧ odds_history(bet_type='place') が
    存在するレース。さらに、出走取消・発走除外・失格等（abnormality_code!=0）の馬は
    単勝オッズ側の母集団（Harville 入力ベクトル）から除外した「アクティブ」馬のみで
    構成する（[[jra_upset_badge_redesign]] 等と同じ方針）。アクティブ馬数が8頭未満に
    なったレースは、8頭以上専用の3着以内スキーム（Harville sum=3 前提）が崩れるため
    解析対象から除外する。

単勝側 (Harville 入力):
    `keiba.race_results.win_odds`（確定・発走直前の最終単勝オッズ）を使用する。
    このリポジトリの既存慣行（Phase4a・recommender 等）と同様、これを「市場の単勝評価」
    として扱う。

複勝側 (市場 implied probability):
    `keiba.odds_history`(bet_type='place') の発走直前（レース内で fetched_at が
    最大のスナップショット）を主に使う。`keiba.race_results.place_odds` は
    HR（払戻）レコードから 1〜3着馬のみに設定される確定複勝オッズであり
    （非入着馬は NULL のまま。地方競馬の chihou スキーマと異なり odds_history での
    事後補完は行われていない）、これをフォールバックに使うと「複勝オッズが判明している
    ＝的中した馬」という生存者バイアスが生まれる。そのため本スクリプトは
    race_results.place_odds をフォールバックに使わず、odds_history の
    スナップショットがアクティブ馬全頭分そろっているレースのみを対象にする
    （そろわないレースはカウントして除外）。

    複勝オッズ→implied probability の変換:
    複勝は同時に3頭が的中するため、単純な 1/odds の合計は 1.0 ではなく
    概ね 3/(1-控除率) に対応する（1/odds_i ≈ P(place)_i / (1-takeout) なので、
    全馬で総和すると sum(1/odds_i) ≈ sum(P(place)_i) / (1-takeout) ≈ 3/(1-takeout)）。
    よって `normalize_market_place_probs()` は 1/odds を算出したうえで、
    合計がちょうど 3.0 になるよう線形正規化する（= 控除率を陽に推定せず、
    「合計3」という理論制約から逆算する形で控除率を暗に補正する）。

mispricing_score:
    market_place_prob - harville_place_prob。
    正: 市場 implied 複勝確率 > 単勝オッズから導いた理論複勝確率
        （市場は単勝オッズが示唆するより「複勝で来る」と評価している＝複勝オッズが
        理論よりも辛い＝相対的に割高）。
    負: 市場 implied 複勝確率 < 理論値（複勝オッズが理論よりも甘い＝相対的に割安）。
    符号の解釈は結論部で実際の的中率データを見てから確定させる
    （直感的な「甘い/辛い」の対応関係を先験的に決め打ちしない）。

出力:
    標準出力に表形式で結果を表示。
    `backend/models/v26_odds_arbitrage_analysis.json` にサマリー保存。
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

from src.betting.odds_model import _harville_place_probs, harville_win_probs_from_odds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_odds_cross_bettype_arbitrage")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_JSON = MODELS_DIR / "v26_odds_arbitrage_analysis.json"

JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

MIN_ACTIVE_HORSES = 8  # 3着以内(Harville sum=3)スキームが成立する最小アクティブ頭数
MIN_SAMPLE_FOR_CONCLUSION = 100  # これ未満は参考値扱い
EXPLORE_FRACTION = 0.6  # 前半(古い側)を探索用に割り当てる比率

BASE_QUERY = """
SELECT
    r.id AS race_id,
    r.date,
    r.course,
    r.head_count,
    re.horse_number,
    rr.win_odds,
    rr.finish_position,
    COALESCE(rr.abnormality_code, 0) AS abnormality_code
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id = r.id
LEFT JOIN keiba.race_results rr ON rr.race_id = re.race_id AND rr.horse_id = re.horse_id
WHERE r.head_count >= %(min_head)s
  AND r.course IN %(courses)s
  AND r.id IN (SELECT DISTINCT race_id FROM keiba.odds_history WHERE bet_type = 'place')
ORDER BY r.id, re.horse_number;
"""

MARKET_PLACE_QUERY = """
SELECT DISTINCT ON (oh.race_id, oh.combination)
    oh.race_id, oh.combination, oh.odds
FROM keiba.odds_history oh
WHERE oh.bet_type = 'place'
  AND oh.race_id = ANY(%(race_ids)s)
ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC;
"""


@dataclass
class HorseMispricingRecord:
    """馬単位の mispricing 分析レコード。"""

    race_id: int
    date: str
    course: str
    head_count: int
    n_active: int
    horse_number: int
    win_odds: float
    harville_place_prob: float
    market_place_odds: float
    market_place_prob: float
    mispricing_score: float
    finish_position: int | None
    place_hit: bool


def get_connection() -> Any:
    """`.env` の DB_* 変数から psycopg2 接続を作成する。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


def normalize_market_place_probs(place_odds: list[float]) -> list[float]:
    """複勝オッズ（下限値）のリストを implied probability に正規化する。

    複勝は同時に3頭が的中するため、単純な 1/odds ではなく、
    「1/odds の総和が理論上 3/(1-控除率) に対応する」関係を使って、
    1/odds を合計がちょうど3.0になるよう線形正規化する。
    これにより控除率を明示的に仮定せずに implied probability を求める。

    Args:
        place_odds: 各馬の複勝オッズ（下限倍率）。8頭以上のレースを想定。

    Returns:
        implied probability のリスト（合計 3.0、入力と同じ順序）。

    Raises:
        ValueError: 入力が空、またはオッズが0以下の値を含む場合。
    """
    if not place_odds:
        raise ValueError("place_odds が空です")
    raw: list[float] = []
    for o in place_odds:
        if o is None or o <= 0:
            raise ValueError(f"不正な複勝オッズ: {o}")
        raw.append(1.0 / float(o))
    total = sum(raw)
    if total <= 0:
        raise ValueError("1/odds の合計が0以下です")
    return [r * 3.0 / total for r in raw]


def fetch_base_rows(conn: Any) -> list[dict[str, Any]]:
    """odds_history(place) が存在する JRA10場・8頭立て以上レースの馬別行を取得する。"""
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"min_head": MIN_ACTIVE_HORSES, "courses": JRA_COURSES})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    logger.info(f"base rows取得: {len(rows):,}行")
    return [dict(zip(cols, r)) for r in rows]


def fetch_market_place_odds(conn: Any, race_ids: list[int]) -> dict[tuple[int, int], float]:
    """odds_history(place) の発走直前(最終)スナップショットを {(race_id, horse_number): odds} で返す。"""
    if not race_ids:
        return {}
    cur = conn.cursor()
    cur.execute(MARKET_PLACE_QUERY, {"race_ids": race_ids})
    rows = cur.fetchall()
    cur.close()
    logger.info(f"market place odds_history取得: {len(rows):,}行")

    out: dict[tuple[int, int], float] = {}
    for race_id, combination, odds in rows:
        if odds is None:
            continue
        try:
            hn = int(combination)
        except (TypeError, ValueError):
            continue
        out[(race_id, hn)] = float(odds)
    return out


def build_race_records(
    rows: list[dict[str, Any]], market_map: dict[tuple[int, int], float]
) -> tuple[list[HorseMispricingRecord], dict[str, int]]:
    """行データをレース単位にグループ化し、mispricing レコードを構築する。

    出走取消・除外・失格等（abnormality_code!=0）はアクティブ母集団から除外する。
    アクティブ馬数が8頭未満になったレース、市場複勝オッズがアクティブ馬全頭分
    そろわないレースは対象外とする（前者はHarville sum=3スキームの前提が崩れるため、
    後者は race_results.place_odds フォールバックによる生存者バイアスを避けるため）。

    Returns:
        (レコードリスト, 除外理由別カウント辞書)
    """
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        race_id = row["race_id"]
        entry = grouped.setdefault(
            race_id,
            {"date": row["date"], "course": row["course"], "head_count": row["head_count"], "horses": []},
        )
        entry["horses"].append(row)

    records: list[HorseMispricingRecord] = []
    skip_counts = {"active_lt_8": 0, "market_odds_incomplete": 0, "included_races": 0}

    for race_id, info in grouped.items():
        active = [
            h
            for h in info["horses"]
            if (h["abnormality_code"] or 0) == 0 and h["win_odds"] is not None and float(h["win_odds"]) > 0
        ]
        if len(active) < MIN_ACTIVE_HORSES:
            skip_counts["active_lt_8"] += 1
            continue

        active = sorted(active, key=lambda h: h["horse_number"])
        market_odds_vec: list[float] = []
        ok = True
        for h in active:
            odds = market_map.get((race_id, h["horse_number"]))
            if odds is None or odds <= 0:
                ok = False
                break
            market_odds_vec.append(odds)
        if not ok:
            skip_counts["market_odds_incomplete"] += 1
            continue

        win_odds_vec = [float(h["win_odds"]) for h in active]
        win_probs = harville_win_probs_from_odds(win_odds_vec)
        harville_place = _harville_place_probs(win_probs, n=len(active))
        market_place_probs = normalize_market_place_probs(market_odds_vec)

        skip_counts["included_races"] += 1
        for h, hp, mo, mp in zip(active, harville_place, market_odds_vec, market_place_probs):
            finish = h["finish_position"]
            records.append(
                HorseMispricingRecord(
                    race_id=race_id,
                    date=info["date"],
                    course=info["course"],
                    head_count=info["head_count"],
                    n_active=len(active),
                    horse_number=h["horse_number"],
                    win_odds=float(h["win_odds"]),
                    harville_place_prob=round(hp, 6),
                    market_place_odds=mo,
                    market_place_prob=round(mp, 6),
                    mispricing_score=round(mp - hp, 6),
                    finish_position=finish,
                    place_hit=finish is not None and 1 <= finish <= 3,
                )
            )

    logger.info(
        f"レース分類: 対象={skip_counts['included_races']:,} / "
        f"アクティブ8頭未満で除外={skip_counts['active_lt_8']:,} / "
        f"市場複勝オッズ不足で除外={skip_counts['market_odds_incomplete']:,}"
    )
    logger.info(f"馬単位レコード構築完了: {len(records):,}件")
    return records, skip_counts


def health_check(records: list[HorseMispricingRecord]) -> dict[str, Any]:
    """Harville 実装の健全性チェック: レースごとの P(3着以内) 合計が3.0に近いか確認する。"""
    by_race: dict[int, list[float]] = defaultdict(list)
    for r in records:
        by_race[r.race_id].append(r.harville_place_prob)

    sums = [sum(vals) for vals in by_race.values()]
    market_sums = defaultdict(list)
    for r in records:
        market_sums[r.race_id].append(r.market_place_prob)
    market_sum_vals = [sum(vals) for vals in market_sums.values()]

    result = {
        "n_races": len(sums),
        "harville_sum_mean": round(statistics.mean(sums), 6) if sums else None,
        "harville_sum_min": round(min(sums), 6) if sums else None,
        "harville_sum_max": round(max(sums), 6) if sums else None,
        "harville_sum_stdev": round(statistics.stdev(sums), 6) if len(sums) > 1 else None,
        "market_sum_mean": round(statistics.mean(market_sum_vals), 6) if market_sum_vals else None,
        "market_sum_min": round(min(market_sum_vals), 6) if market_sum_vals else None,
        "market_sum_max": round(max(market_sum_vals), 6) if market_sum_vals else None,
    }
    print("\n=== 健全性チェック: レース全体の P(3着以内) 合計（理論上ちょうど3.0） ===")
    print(
        f"  Harville理論値: mean={result['harville_sum_mean']} "
        f"min={result['harville_sum_min']} max={result['harville_sum_max']} "
        f"stdev={result['harville_sum_stdev']} (n={result['n_races']}レース)"
    )
    print(
        f"  市場implied値(正規化後・定義上ちょうど3.0のはず): "
        f"mean={result['market_sum_mean']} min={result['market_sum_min']} max={result['market_sum_max']}"
    )
    return result


def split_explore_confirm(
    records: list[HorseMispricingRecord],
) -> tuple[list[HorseMispricingRecord], list[HorseMispricingRecord]]:
    """時系列(レース単位)で前半60%(探索用)/後半40%(確認用)に単純分割する。"""
    race_dates = sorted({(r.date, r.race_id) for r in records})
    split_idx = round(len(race_dates) * EXPLORE_FRACTION)
    explore_races = {rid for _, rid in race_dates[:split_idx]}
    explore = [r for r in records if r.race_id in explore_races]
    confirm = [r for r in records if r.race_id not in explore_races]
    return explore, confirm


def compute_quartile_bounds(explore: list[HorseMispricingRecord]) -> list[float]:
    """探索用データの mispricing_score から四分位境界 [Q1, Q2, Q3] を算出する。"""
    vals = sorted(r.mispricing_score for r in explore)
    return statistics.quantiles(vals, n=4, method="inclusive")


def assign_quartile(score: float, bounds: list[float]) -> str:
    """固定境界 bounds=[Q1,Q2,Q3] を用いて四分位ラベルを割り当てる。"""
    q1, q2, q3 = bounds
    if score <= q1:
        return "Q1(市場<理論・最も割安寄り)"
    if score <= q2:
        return "Q2"
    if score <= q3:
        return "Q3"
    return "Q4(市場>理論・最も割高寄り)"


QUARTILE_LABELS = (
    "Q1(市場<理論・最も割安寄り)",
    "Q2",
    "Q3",
    "Q4(市場>理論・最も割高寄り)",
)


def quartile_stats(records: list[HorseMispricingRecord], bounds: list[float]) -> dict[str, dict[str, Any]]:
    """固定境界で四分位に分割し、各分位の複勝的中率を集計する。"""
    by_q: dict[str, list[HorseMispricingRecord]] = defaultdict(list)
    for r in records:
        by_q[assign_quartile(r.mispricing_score, bounds)].append(r)

    out: dict[str, dict[str, Any]] = {}
    for label in QUARTILE_LABELS:
        rs = by_q.get(label, [])
        n = len(rs)
        settled = [r for r in rs if r.finish_position is not None]
        n_settled = len(settled)
        hits = sum(1 for r in settled if r.place_hit)
        out[label] = {
            "n": n,
            "n_settled": n_settled,
            "place_hit_rate": round(hits / n_settled, 4) if n_settled else None,
            "mean_score": round(statistics.mean(r.mispricing_score for r in rs), 6) if rs else None,
            "mean_market_place_prob": round(statistics.mean(r.market_place_prob for r in rs), 4) if rs else None,
            "mean_harville_place_prob": round(statistics.mean(r.harville_place_prob for r in rs), 4) if rs else None,
        }
    return out


def print_quartile_table(title: str, stats: dict[str, dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    header = f"{'quartile':<28}{'n':>7}{'n_settled':>11}{'平均score':>12}{'複勝的中率':>12}"
    print(header)
    for label in QUARTILE_LABELS:
        s = stats.get(label, {"n": 0})
        n = s.get("n", 0)
        flag = "" if n >= MIN_SAMPLE_FOR_CONCLUSION else " (参考:n<100)"
        place = s.get("place_hit_rate")
        mean_score = s.get("mean_score")
        print(
            f"{label:<28}{n:>7}{s.get('n_settled', 0):>11}"
            f"{(f'{mean_score:.4f}' if mean_score is not None else '-'):>12}"
            f"{(f'{place:.1%}' if place is not None else '-'):>12}{flag}"
        )


def main() -> None:
    conn = get_connection()
    try:
        rows = fetch_base_rows(conn)
        race_ids = sorted({row["race_id"] for row in rows})
        market_map = fetch_market_place_odds(conn, race_ids)
    finally:
        conn.close()

    records, skip_counts = build_race_records(rows, market_map)
    if not records:
        logger.error("解析対象レコードが0件です。終了します。")
        return

    health = health_check(records)

    explore, confirm = split_explore_confirm(records)
    explore_races = {r.race_id for r in explore}
    confirm_races = {r.race_id for r in confirm}
    if explore:
        logger.info(
            f"探索用(前半{EXPLORE_FRACTION:.0%}): {len(explore):,}馬 / {len(explore_races):,}レース "
            f"({min(r.date for r in explore)}〜{max(r.date for r in explore)})"
        )
    if confirm:
        logger.info(
            f"確認用(後半{1 - EXPLORE_FRACTION:.0%}): {len(confirm):,}馬 / {len(confirm_races):,}レース "
            f"({min(r.date for r in confirm)}〜{max(r.date for r in confirm)})"
        )

    summary: dict[str, Any] = {
        "note": (
            "参考値・信頼度低め。odds_history蓄積が約5ヶ月・"
            f"全体{len(explore_races) + len(confirm_races)}レースしかなく、"
            "Phase3の確認期間検証(diagnostic 8,377/confirmation 1,905)と比べて"
            "統計的信頼性が大幅に低い。単一hold-out分割(前後半)による簡易一貫性"
            "チェックであり、二段階honest検証(診断/確認)ではない。本番実装はしない。"
        ),
        "skip_counts": skip_counts,
        "min_active_horses": MIN_ACTIVE_HORSES,
        "explore_fraction": EXPLORE_FRACTION,
        "n_horses_explore": len(explore),
        "n_horses_confirm": len(confirm),
        "n_races_explore": len(explore_races),
        "n_races_confirm": len(confirm_races),
        "health_check": health,
    }

    bounds = compute_quartile_bounds(explore)
    print(f"\n探索用データから決定した mispricing_score 四分位境界(固定・確認用にもそのまま適用): {bounds}")
    summary["quartile_bounds_from_explore"] = bounds

    explore_q_stats = quartile_stats(explore, bounds)
    confirm_q_stats = quartile_stats(confirm, bounds)
    print_quartile_table("mispricing_score四分位別 複勝的中率（探索用・前半60%）", explore_q_stats)
    print_quartile_table("mispricing_score四分位別 複勝的中率（確認用・後半40%・固定境界を適用）", confirm_q_stats)
    summary["quartile_stats"] = {"explore": explore_q_stats, "confirm": confirm_q_stats}

    q1_explore = explore_q_stats["Q1(市場<理論・最も割安寄り)"]["place_hit_rate"]
    q4_explore = explore_q_stats["Q4(市場>理論・最も割高寄り)"]["place_hit_rate"]
    q1_confirm = confirm_q_stats["Q1(市場<理論・最も割安寄り)"]["place_hit_rate"]
    q4_confirm = confirm_q_stats["Q4(市場>理論・最も割高寄り)"]["place_hit_rate"]
    explore_gap = (q4_explore - q1_explore) if q1_explore is not None and q4_explore is not None else None
    confirm_gap = (q4_confirm - q1_confirm) if q1_confirm is not None and q4_confirm is not None else None
    print(
        f"\nQ4-Q1 差(市場が理論より複勝を強気評価している馬群ほど複勝的中率が高いなら正):"
        f" 探索用={explore_gap} / 確認用={confirm_gap}"
    )
    summary["q4_minus_q1_gap"] = {"explore": explore_gap, "confirm": confirm_gap}

    reproduced = (
        explore_gap is not None
        and confirm_gap is not None
        and explore_gap > 0
        and confirm_gap > 0
        and explore_q_stats["Q1(市場<理論・最も割安寄り)"]["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and explore_q_stats["Q4(市場>理論・最も割高寄り)"]["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and confirm_q_stats["Q1(市場<理論・最も割安寄り)"]["n"] >= MIN_SAMPLE_FOR_CONCLUSION
        and confirm_q_stats["Q4(市場>理論・最も割高寄り)"]["n"] >= MIN_SAMPLE_FOR_CONCLUSION
    )
    print(f"傾向の再現(符号一致 かつ 全群n>=100): {reproduced}")
    summary["gap_reproduced"] = reproduced

    OUTPUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    logger.info(f"サマリー保存: {OUTPUT_JSON}")

    print(
        "\n⚠️ 結論の解釈上の注意: サンプル数(全体約"
        f"{len(explore_races) + len(confirm_races)}レース、探索用{len(explore_races)}/確認用{len(confirm_races)})"
        "が小さく、Phase3の確認期間検証(diagnostic 8,377/confirmation 1,905)と比べて"
        "統計的信頼性が大幅に低い参考値である。本番実装はしない。"
    )


if __name__ == "__main__":
    main()
