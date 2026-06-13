"""払戻→組合せオッズ近似モデル 学習スクリプト。

keiba.race_payouts（確定払戻）と keiba.race_results（確定単勝オッズ）から
Harville 確率を算出し、log(確定オッズ) ≈ a + b·log(1/p_harville)
の券種別線形回帰を行う。

学習結果は models/odds_approx_v1.json に保存される。

使い方（スモーク: 3ヶ月窓）:
    cd backend
    .venv/bin/python scripts/fit_odds_approximation.py --start 20250101 --end 20250331

使い方（フル学習: train 期間）:
    .venv/bin/python scripts/fit_odds_approximation.py --start 20230101 --end 20250630

注意事項:
  - 本番 DB は読み取り専用で使用する（INSERT/UPDATE/DELETE 禁止）
  - 大量クエリは3ヶ月チャンクに分割して発行する
  - 的中組合せのみのサンプルバイアス（勝ち馬・複勝圏馬に偏る）を認識して使うこと
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np
import psycopg2

from src.betting.odds_model import (
    OddsApproximator,
    harville_combo_prob,
    harville_win_probs_from_odds,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("fit_odds_approx")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
OUTPUT_PATH = MODELS_DIR / "odds_approx_v1.json"

# 학습에 사용하는 bet_type 一覧（bracket は複雑なため除外）
TARGET_BET_TYPES = ["win", "place", "quinella", "wide", "exacta", "trio", "trifecta"]

# チャンク分割: 3ヶ月単位で DB クエリ
CHUNK_MONTHS = 3


def _conn() -> "psycopg2.connection":
    """DB 接続を返す。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


def _date_chunks(start: date, end: date, months: int = 3) -> list[tuple[date, date]]:
    """期間を months 月ごとのチャンクに分割する。

    Args:
        start: 開始日。
        end: 終了日。
        months: チャンク月数。

    Returns:
        (チャンク開始, チャンク終了) のリスト。
    """
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        # months ヶ月後の1日 - 1日
        year = cur.year + (cur.month - 1 + months) // 12
        month = (cur.month - 1 + months) % 12 + 1
        chunk_end = date(year, month, 1) - timedelta(days=1)
        chunk_end = min(chunk_end, end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _fetch_race_data(
    conn: "psycopg2.connection", start: date, end: date
) -> list[dict]:
    """指定期間のレースデータを取得する（各レース: 馬番→オッズ map + 払戻リスト）。

    Args:
        conn: DB 接続。
        start: 開始日。
        end: 終了日。

    Returns:
        レースごとの辞書リスト。各辞書:
          - race_id: int
          - date: str
          - head_count: int
          - horse_odds: dict[int, float]  # horse_number → win_odds
          - payouts: list[dict]  # {bet_type, combination, payout}
    """
    cur = conn.cursor()

    # 1) レース+結果: horse_number → win_odds マッピング
    cur.execute(
        """
        SELECT r.id, r.date, r.head_count,
               rr.horse_number, rr.win_odds
        FROM keiba.races r
        JOIN keiba.race_results rr ON rr.race_id = r.id
        WHERE r.date BETWEEN %s AND %s
          AND r.course IN (
              '01','02','03','04','05','06','07','08','09','10'
          )
          AND (rr.abnormality_code IS NULL OR rr.abnormality_code = 0)
        ORDER BY r.id, rr.horse_number
        """,
        (start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
    )
    rows = cur.fetchall()

    # レースデータを集約
    race_map: dict[int, dict] = {}
    for race_id, race_date, head_count, hn, win_odds in rows:
        if race_id not in race_map:
            race_map[race_id] = {
                "race_id": race_id,
                "date": str(race_date),
                "head_count": head_count,
                "horse_odds": {},
                "payouts": [],
            }
        if win_odds is not None and float(win_odds) > 0:
            race_map[race_id]["horse_odds"][int(hn)] = float(win_odds)

    if not race_map:
        return []

    race_ids = list(race_map.keys())

    # 2) 払戻データ取得（IN 句は最大1000件ずつ）
    batch_size = 500
    for i in range(0, len(race_ids), batch_size):
        batch = race_ids[i : i + batch_size]
        cur.execute(
            """
            SELECT race_id, bet_type, combination, payout
            FROM keiba.race_payouts
            WHERE race_id = ANY(%s)
              AND bet_type = ANY(%s)
            """,
            (batch, TARGET_BET_TYPES),
        )
        for race_id, bet_type, combination, payout in cur.fetchall():
            if race_id in race_map:
                race_map[race_id]["payouts"].append(
                    {
                        "bet_type": bet_type,
                        "combination": combination,
                        "payout": payout,
                    }
                )

    return list(race_map.values())


def _parse_combination(combination: str, bet_type: str) -> list[int] | None:
    """組合せ文字列を馬番リストに変換する。

    Args:
        combination: "7" / "3-14" / "6-7-11" 等。
        bet_type: 券種。

    Returns:
        馬番リスト（1-indexed）または None（パース失敗時）。
    """
    try:
        parts = [int(x) for x in combination.split("-")]
        return parts
    except ValueError:
        return None


def _build_samples(
    races: list[dict],
) -> dict[str, list[tuple[float, float]]]:
    """レースデータからサンプル（log10_inv_p_harville, log10_actual_odds）を構築する。

    Args:
        races: _fetch_race_data の返り値。

    Returns:
        bet_type → [(log10(1/p), log10(actual_odds)), ...] の辞書。
    """
    samples: dict[str, list[tuple[float, float]]] = {bt: [] for bt in TARGET_BET_TYPES}

    skipped_no_odds = 0
    skipped_parse = 0
    skipped_zero_p = 0
    processed_races = 0

    for race in races:
        horse_odds = race["horse_odds"]
        n_horses = race["head_count"]

        # 全馬のオッズが揃っているか確認
        # win_oddsが欠けている馬が多い場合はスキップ
        if len(horse_odds) < 2:
            skipped_no_odds += 1
            continue

        # 勝率ベクトル構築（horse_number 昇順でソートし index を確定）
        sorted_hnums = sorted(horse_odds.keys())
        win_probs = harville_win_probs_from_odds(
            [horse_odds[hn] for hn in sorted_hnums]
        )
        # horse_number → index マッピング
        hn_to_idx = {hn: idx for idx, hn in enumerate(sorted_hnums)}

        processed_races += 1

        for payout_item in race["payouts"]:
            bet_type = payout_item["bet_type"]
            combination = payout_item["combination"]
            payout = payout_item["payout"]

            if payout is None or payout <= 0:
                continue

            # 実際の確定オッズ（払戻/100）
            actual_odds = payout / 100.0
            if actual_odds < 1.0:
                continue  # 1.0 未満は異常値

            # 組合せ → 馬番リスト
            horse_nums = _parse_combination(combination, bet_type)
            if horse_nums is None:
                skipped_parse += 1
                continue

            # 馬番 → インデックス変換
            try:
                horse_indices = [hn_to_idx[hn] for hn in horse_nums]
            except KeyError:
                # 取消馬等でオッズ未収録の場合
                skipped_no_odds += 1
                continue

            # Harville 確率計算
            p = harville_combo_prob(
                win_probs, horse_indices, bet_type, n_horses
            )
            if p <= 1e-9:
                skipped_zero_p += 1
                continue

            log_inv_p = math.log10(1.0 / p)
            log_actual = math.log10(actual_odds)

            samples[bet_type].append((log_inv_p, log_actual))

    logger.info(
        "処理レース数: %d, スキップ(オッズ不足): %d, パース失敗: %d, 確率0: %d",
        processed_races,
        skipped_no_odds,
        skipped_parse,
        skipped_zero_p,
    )

    return samples


def _fit_linear(
    x_vals: list[float], y_vals: list[float]
) -> tuple[float, float]:
    """最小二乗法で y = a + b*x の係数を求める。

    Args:
        x_vals: 説明変数リスト（log10(1/p)）。
        y_vals: 目的変数リスト（log10(actual_odds)）。

    Returns:
        (a, b) = (切片, 傾き)。
    """
    x = np.array(x_vals)
    y = np.array(y_vals)
    # OLS: [a, b] = (X^T X)^{-1} X^T y
    X = np.column_stack([np.ones(len(x)), x])
    result = np.linalg.lstsq(X, y, rcond=None)
    coeffs = result[0]
    return float(coeffs[0]), float(coeffs[1])


def _compute_mae(
    x_vals: list[float], y_vals: list[float], a: float, b: float
) -> float:
    """log10(オッズ) の MAE を計算する。

    Args:
        x_vals: log10(1/p) リスト。
        y_vals: log10(実オッズ) リスト。
        a: 切片。
        b: 傾き。

    Returns:
        MAE（log10 スケール）。
    """
    x = np.array(x_vals)
    y = np.array(y_vals)
    y_pred = a + b * x
    return float(np.mean(np.abs(y - y_pred)))


def _compute_bias(
    x_vals: list[float], y_vals: list[float], a: float, b: float
) -> float:
    """log10(実オッズ) - log10(予測オッズ) の平均（正=過小評価、負=過大評価）。

    Args:
        x_vals: log10(1/p) リスト。
        y_vals: log10(実オッズ) リスト。
        a: 切片。
        b: 傾き。

    Returns:
        平均残差（バイアス）。
    """
    x = np.array(x_vals)
    y = np.array(y_vals)
    y_pred = a + b * x
    return float(np.mean(y - y_pred))


def main() -> None:
    """メインエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="払戻→組合せオッズ近似モデル 学習スクリプト"
    )
    parser.add_argument("--start", default="20230101", help="開始日 YYYYMMDD")
    parser.add_argument("--end", default="20250630", help="終了日 YYYYMMDD")
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help="出力 JSON パス",
    )
    args = parser.parse_args()

    start = date(int(args.start[:4]), int(args.start[4:6]), int(args.start[6:8]))
    end = date(int(args.end[:4]), int(args.end[4:6]), int(args.end[6:8]))

    logger.info("学習期間: %s 〜 %s", start, end)

    conn = _conn()
    try:
        # チャンク分割でデータ取得
        chunks = _date_chunks(start, end, CHUNK_MONTHS)
        logger.info("チャンク数: %d", len(chunks))

        all_samples: dict[str, list[tuple[float, float]]] = {
            bt: [] for bt in TARGET_BET_TYPES
        }

        for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
            logger.info(
                "チャンク %d/%d: %s 〜 %s", i, len(chunks), chunk_start, chunk_end
            )
            races = _fetch_race_data(conn, chunk_start, chunk_end)
            logger.info("  → レース数: %d", len(races))
            chunk_samples = _build_samples(races)
            for bt in TARGET_BET_TYPES:
                all_samples[bt].extend(chunk_samples[bt])
    finally:
        conn.close()

    # 券種別に回帰フィット
    params: dict[str, dict] = {}
    logger.info("=" * 60)
    logger.info("券種別 フィット結果")
    logger.info("=" * 60)

    for bt in TARGET_BET_TYPES:
        s = all_samples[bt]
        n = len(s)
        if n < 10:
            logger.warning("%s: サンプル不足 (%d 件) → スキップ", bt, n)
            continue

        x_vals = [item[0] for item in s]
        y_vals = [item[1] for item in s]

        a, b = _fit_linear(x_vals, y_vals)
        mae = _compute_mae(x_vals, y_vals, a, b)
        bias = _compute_bias(x_vals, y_vals, a, b)

        # パーセンタイル分布（favorite-longshot bias 把握）
        x_arr = np.array(x_vals)
        y_arr = np.array(y_vals)
        y_pred = a + b * x_arr
        residuals = y_arr - y_pred

        # 低オッズ（下位25%）vs 高オッズ（上位25%）のバイアス分割
        q25, q75 = np.percentile(x_arr, [25, 75])
        low_mask = x_arr <= q25
        high_mask = x_arr >= q75
        bias_low = float(np.mean(residuals[low_mask])) if low_mask.any() else 0.0
        bias_high = float(np.mean(residuals[high_mask])) if high_mask.any() else 0.0

        params[bt] = {
            "a": round(a, 6),
            "b": round(b, 6),
            "mae": round(mae, 4),
            "bias": round(bias, 4),
            "bias_low_odds": round(bias_low, 4),
            "bias_high_odds": round(bias_high, 4),
            "n": n,
        }

        logger.info(
            "%s: n=%d  a=%.4f b=%.4f  MAE=%.4f(log10)≈%.2fx "
            "bias=%.4f  bias_low=%.4f bias_high=%.4f",
            bt,
            n,
            a,
            b,
            mae,
            10 ** mae,
            bias,
            bias_low,
            bias_high,
        )

    if not params:
        logger.error("全券種でサンプル不足。学習失敗。")
        sys.exit(1)

    # JSON に保存
    import datetime

    approx = OddsApproximator(
        params=params,
        version=1,
        fit_date=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    approx.to_json(args.output)
    logger.info("モデル保存完了: %s", args.output)
    logger.info("学習済み券種: %s", list(params.keys()))


if __name__ == "__main__":
    main()
