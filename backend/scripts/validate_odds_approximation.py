"""払戻→組合せオッズ近似モデル 検証スクリプト。

学習済みモデル（models/odds_approx_v1.json）を使い、
test/fresh 期間での券種別 MAE・較正曲線を標準出力に表示する。

さらに 2026-03-28 以降は keiba.odds_history の実発走前オッズ（win/place）と
近似値を比較して、リアルオッズ vs 近似オッズの乖離を直接計測する。

使い方:
    cd backend
    .venv/bin/python scripts/validate_odds_approximation.py

    # 期間指定
    .venv/bin/python scripts/validate_odds_approximation.py --start 20250701 --end 20260331

    # モデル指定
    .venv/bin/python scripts/validate_odds_approximation.py --model models/odds_approx_v1.json
"""

from __future__ import annotations

import argparse
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

from scripts.fit_odds_approximation import (
    _build_samples,
    _date_chunks,
    _fetch_race_data,
)
from src.betting.odds_model import (
    OddsApproximator,
    TAKEOUT_RATE,
    harville_combo_prob,
    harville_win_probs_from_odds,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("validate_odds_approx")

TARGET_BET_TYPES = ["win", "place", "quinella", "wide", "exacta", "trio", "trifecta"]
MODELS_DIR = _root / "models"


def _conn() -> "psycopg2.connection":
    """DB 接続を返す。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """MAE を返す。"""
    return float(np.mean(np.abs(y_true - y_pred)))


def _calibration_table(
    x_vals: list[float],
    y_vals: list[float],
    a: float,
    b: float,
    n_bins: int = 8,
) -> list[dict]:
    """予測オッズ分位別の較正テーブルを返す。

    Args:
        x_vals: log10(1/p) リスト。
        y_vals: log10(実オッズ) リスト。
        a: 切片。
        b: 傾き。
        n_bins: 分位数（デフォルト 8 = 12.5%刻み）。

    Returns:
        較正テーブル（各ビン: predicted_median, actual_median, bias, n）。
    """
    x = np.array(x_vals)
    y = np.array(y_vals)
    y_pred = a + b * x

    # 予測値でソートしてビン分割
    order = np.argsort(y_pred)
    x_sorted = x[order]
    y_sorted = y[order]
    pred_sorted = y_pred[order]

    bins = np.array_split(np.arange(len(y)), n_bins)
    table = []
    for bin_idx in bins:
        if len(bin_idx) == 0:
            continue
        pred_med = float(np.median(pred_sorted[bin_idx]))
        actual_med = float(np.median(y_sorted[bin_idx]))
        bias = actual_med - pred_med
        # log10 → 実オッズ中央値
        actual_odds_med = 10 ** actual_med
        pred_odds_med = 10 ** pred_med
        table.append(
            {
                "pred_log10_mid": round(pred_med, 3),
                "actual_log10_mid": round(actual_med, 3),
                "pred_odds": round(pred_odds_med, 1),
                "actual_odds": round(actual_odds_med, 1),
                "bias_log10": round(bias, 3),
                "n": len(bin_idx),
            }
        )
    return table


def _naive_mae(
    x_vals: list[float], y_vals: list[float], bet_type: str
) -> float:
    """ナイーブ推定（控除率ベース）の MAE を返す。

    Args:
        x_vals: log10(1/p) リスト。
        y_vals: log10(実オッズ) リスト。
        bet_type: 券種。

    Returns:
        MAE（log10 スケール）。
    """
    takeout = TAKEOUT_RATE.get(bet_type, 0.25)
    x = np.array(x_vals)
    y = np.array(y_vals)
    # ナイーブ: log10(1-takeout) + log10(1/p) = log10((1-takeout)/p)
    y_naive = math.log10(1.0 - takeout) + x
    return float(np.mean(np.abs(y - y_naive)))


def _validate_vs_odds_history(
    conn: "psycopg2.connection",
    approx: OddsApproximator,
    start: date,
    end: date,
) -> dict[str, dict]:
    """odds_history の実オッズと近似値を比較する（win/place のみ）。

    2026-03-28 以降に利用可能。

    Args:
        conn: DB 接続。
        approx: 学習済みモデル。
        start: 開始日。
        end: 終了日。

    Returns:
        {"win": {"mae": float, "n": int, ...}, "place": ...}
    """
    cur = conn.cursor()

    # 実発走前オッズ（発走直前=最終スナップショット）
    cur.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (oh.race_id, oh.bet_type, oh.combination)
                   oh.race_id, oh.bet_type, oh.combination, oh.odds
            FROM keiba.odds_history oh
            JOIN keiba.races r ON r.id = oh.race_id
            WHERE r.date BETWEEN %s AND %s
              AND oh.bet_type IN ('win', 'place')
            ORDER BY oh.race_id, oh.bet_type, oh.combination, oh.fetched_at DESC
        )
        SELECT l.race_id, l.bet_type, l.combination, l.odds,
               r.date, r.head_count
        FROM latest l
        JOIN keiba.races r ON r.id = l.race_id
        WHERE r.course IN ('01','02','03','04','05','06','07','08','09','10')
        """,
        (start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
    )
    rows = cur.fetchall()
    if not rows:
        return {}

    # レースごとに馬別オッズ（race_results から）
    race_ids = list({row[0] for row in rows})
    cur.execute(
        """
        SELECT race_id, horse_number, win_odds
        FROM keiba.race_results
        WHERE race_id = ANY(%s)
          AND (abnormality_code IS NULL OR abnormality_code = 0)
        """,
        (race_ids,),
    )
    race_odds_map: dict[int, dict[int, float]] = {}
    for race_id, hn, win_odds in cur.fetchall():
        if win_odds and float(win_odds) > 0:
            if race_id not in race_odds_map:
                race_odds_map[race_id] = {}
            race_odds_map[race_id][int(hn)] = float(win_odds)

    results: dict[str, list[tuple[float, float]]] = {"win": [], "place": []}

    for race_id, bet_type, combination, actual_odds, race_date, head_count in rows:
        if race_id not in race_odds_map:
            continue
        horse_odds_map = race_odds_map[race_id]
        if len(horse_odds_map) < 2:
            continue

        sorted_hnums = sorted(horse_odds_map.keys())
        win_probs = harville_win_probs_from_odds(
            [horse_odds_map[hn] for hn in sorted_hnums]
        )
        hn_to_idx = {hn: idx for idx, hn in enumerate(sorted_hnums)}

        try:
            hn = int(combination)
            if hn not in hn_to_idx:
                continue
            idx = hn_to_idx[hn]
        except (ValueError, KeyError):
            continue

        actual = float(actual_odds)
        if actual < 1.0:
            continue

        est = approx.estimate(bet_type, [idx], win_probs, head_count)
        if est == float("inf") or est < 1.0:
            continue

        results[bet_type].append((math.log10(actual), math.log10(est)))

    output: dict[str, dict] = {}
    for bt in ["win", "place"]:
        pairs = results[bt]
        if not pairs:
            continue
        y_true = np.array([p[0] for p in pairs])
        y_pred = np.array([p[1] for p in pairs])
        mae_val = _mae(y_true, y_pred)
        bias_val = float(np.mean(y_true - y_pred))
        output[bt] = {
            "n": len(pairs),
            "mae_log10": round(mae_val, 4),
            "mae_odds_ratio": round(10 ** mae_val, 2),
            "bias_log10": round(bias_val, 4),
        }

    return output


def main() -> None:
    """メインエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="払戻→組合せオッズ近似モデル 検証スクリプト"
    )
    parser.add_argument("--start", default="20250701", help="検証開始日 YYYYMMDD")
    parser.add_argument("--end", default="20260331", help="検証終了日 YYYYMMDD")
    parser.add_argument(
        "--model",
        default=str(MODELS_DIR / "odds_approx_v1.json"),
        help="モデル JSON パス",
    )
    args = parser.parse_args()

    start = date(int(args.start[:4]), int(args.start[4:6]), int(args.start[6:8]))
    end = date(int(args.end[:4]), int(args.end[4:6]), int(args.end[6:8]))

    logger.info("検証期間: %s 〜 %s", start, end)

    # モデル読み込み
    model_path = Path(args.model)
    if not model_path.exists():
        logger.error("モデルファイルが見つかりません: %s", model_path)
        logger.error(
            "先に fit_odds_approximation.py を実行してください。"
        )
        sys.exit(1)

    approx = OddsApproximator.from_json(model_path)
    logger.info("モデル読み込み完了: v%d (%s)", approx.version, approx.fit_date)
    logger.info("学習済み券種: %s", approx.coverage())

    conn = _conn()
    try:
        # チャンク分割でデータ取得
        chunks = _date_chunks(start, end, months=3)
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

        # odds_history 検証（2026-03-28 以降）
        oh_start = max(start, date(2026, 3, 28))
        oh_results: dict[str, dict] = {}
        if oh_start <= end:
            logger.info("odds_history 実オッズ比較: %s 〜 %s", oh_start, end)
            oh_results = _validate_vs_odds_history(conn, approx, oh_start, end)
    finally:
        conn.close()

    # ===== レポート出力 =====
    print()
    print("=" * 70)
    print("  払戻→組合せオッズ近似モデル 検証レポート")
    print(f"  期間: {start} 〜 {end}")
    print(f"  モデル: v{approx.version} ({approx.fit_date})")
    print("=" * 70)
    print()
    print("受け入れ基準: test で log10(オッズ) MAE ≤ 0.25（約1.8倍以内）")
    print()

    print(
        f"{'券種':12} {'n':>7} {'MAE(log10)':>12} {'約x倍':>8} "
        f"{'naive MAE':>10} {'バイアス':>9} {'判定':>5}"
    )
    print("-" * 70)

    summary_mae: dict[str, float] = {}
    for bt in TARGET_BET_TYPES:
        s = all_samples[bt]
        if not s:
            print(f"{bt:12} {'(データなし)':>60}")
            continue

        x_vals = [item[0] for item in s]
        y_vals = [item[1] for item in s]

        if bt not in approx.params:
            print(f"{bt:12} {'(モデルなし)':>60}")
            continue

        p = approx.params[bt]
        a, b = p["a"], p["b"]

        mae = float(np.mean(np.abs(np.array(y_vals) - (a + b * np.array(x_vals)))))
        naive = _naive_mae(x_vals, y_vals, bt)
        bias = float(
            np.mean(np.array(y_vals) - (a + b * np.array(x_vals)))
        )
        ok = "★OK" if mae <= 0.25 else "×NG"
        summary_mae[bt] = mae

        print(
            f"{bt:12} {len(s):>7,} {mae:>12.4f} {10**mae:>8.2f}x "
            f"{naive:>10.4f} {bias:>+9.4f} {ok:>5}"
        )

    print()
    print("バイアス符号: 正=実オッズ>予測（過小評価）, 負=実オッズ<予測（過大評価）")

    # 較正テーブル（win と trio のみ代表）
    for bt in ["win", "quinella", "trio", "trifecta"]:
        s = all_samples.get(bt, [])
        if len(s) < 20 or bt not in approx.params:
            continue
        x_vals = [item[0] for item in s]
        y_vals = [item[1] for item in s]
        a = approx.params[bt]["a"]
        b = approx.params[bt]["b"]
        table = _calibration_table(x_vals, y_vals, a, b, n_bins=6)

        print()
        print(f"--- {bt} 較正テーブル（予測オッズ分位別）---")
        print(f"  {'予測(中央)':>10} {'実際(中央)':>10} {'予測x':>8} {'実際x':>8} {'bias':>7} {'n':>6}")
        for row in table:
            print(
                f"  {row['pred_log10_mid']:>10.3f} {row['actual_log10_mid']:>10.3f} "
                f"{row['pred_odds']:>8.1f} {row['actual_odds']:>8.1f} "
                f"{row['bias_log10']:>+7.3f} {row['n']:>6}"
            )

    # odds_history 実オッズ比較
    if oh_results:
        print()
        print("--- odds_history 実発走前オッズ vs 近似オッズ（2026-03-28 以降）---")
        print(
            f"  {'券種':8} {'n':>7} {'MAE(log10)':>12} {'約x倍':>8} {'バイアス':>9}"
        )
        for bt, res in oh_results.items():
            print(
                f"  {bt:8} {res['n']:>7,} {res['mae_log10']:>12.4f} "
                f"{res['mae_odds_ratio']:>8.2f}x {res['bias_log10']:>+9.4f}"
            )

    # 総合判定
    print()
    print("=" * 70)
    passed = [bt for bt, mae in summary_mae.items() if mae <= 0.25]
    failed = [bt for bt, mae in summary_mae.items() if mae > 0.25]
    if failed:
        print(f"注意: 以下の券種は MAE > 0.25（外挿・EV計算は参考値扱いにすること）")
        for bt in failed:
            print(f"  {bt}: MAE = {summary_mae[bt]:.4f} (≈{10**summary_mae[bt]:.2f}x)")
    if passed:
        print(f"基準クリア: {passed}")

    # 三連系外挿の妥当性判断
    print()
    print("--- 三連系外挿の妥当性判断 ---")
    trio_mae = summary_mae.get("trio", float("inf"))
    trifecta_mae = summary_mae.get("trifecta", float("inf"))
    if trio_mae <= 0.25 and trifecta_mae <= 0.25:
        print("  三連系: MAE ≤ 0.25 → EVバックテスト利用可（精度は許容範囲）")
    elif trio_mae <= 0.40 and trifecta_mae <= 0.40:
        print(
            "  三連系: MAE > 0.25 → 精度は限定的。"
            "正確なEVよりオーバーレイ発見の補助的用途に限定すること"
        )
    else:
        print(
            "  三連系: MAE > 0.40 → 精度不足。"
            "三連系 EV バックテストは不可能。結論として報告する"
        )
    print()


if __name__ == "__main__":
    main()
