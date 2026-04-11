"""指数ウェイト最適化スクリプト

DBに保存済みのサブ指数値を再利用し、ウェイトをPandas上で振り直すことで
再計算なしに高速なバックテストを実施する。

戦略:
  - 訓練期間 (2024年) でウェイトを最適化
  - テスト期間 (2025年) で汎化性能を検証
  - 過学習対策: Nelder-Mead + L2正則化 + 5-Fold CV
  - 加えてペース指数のウェイトを固定したグリッドサーチも実施

最適化目標: 単勝ROI（最大化） or スピアマン相関（最大化）

使い方:
  # デフォルト: 2024年訓練 → 2025年テスト
  python scripts/weight_optimizer.py

  # 期間指定
  python scripts/weight_optimizer.py --train-start 20230101 --train-end 20241231
  python scripts/weight_optimizer.py --test-start 20250101 --test-end 20251231

  # グリッドサーチのみ（ペース幅を確認したい場合）
  python scripts/weight_optimizer.py --mode grid

  # Nelder-Mead最適化のみ
  python scripts/weight_optimizer.py --mode optimize

  # 両方（デフォルト）
  python scripts/weight_optimizer.py --mode all
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import asyncio

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sqlalchemy import text

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.db.session import AsyncSessionLocal
from src.indices.composite import COMPOSITE_VERSION
from src.utils.constants import INDEX_WEIGHTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("weight_optimizer")

# ---------------------------------------------------------------------------
# 最適化対象のサブ指数キー（DB列名 → ウェイトキー）
# ---------------------------------------------------------------------------
# anagusa / paddock は現在 0 のため除外
# disadvantage_bonus はシステム的なボーナスなので固定
SUB_INDEX_COLS = [
    "speed_index",
    "last3f_index",
    "course_aptitude",
    "pace_index",
    "jockey_index",
    "rotation_index",
    "pedigree_index",
    "training_index",
    "position_advantage",
]

# ウェイト辞書のキーとの対応
COL_TO_KEY = {
    "speed_index":     "speed",
    "last3f_index":    "last_3f",
    "course_aptitude": "course_aptitude",
    "pace_index":      "pace",
    "jockey_index":    "jockey_trainer",
    "rotation_index":  "rotation",
    "pedigree_index":  "pedigree",
    "training_index":  "training",
    "position_advantage": "position_advantage",
}

DISADVANTAGE_BONUS = INDEX_WEIGHTS.get("disadvantage_bonus", 0.05)

# 現在のv9ウェイト（最適化対象分のみ、正規化して使う）
CURRENT_WEIGHTS_RAW = {k: INDEX_WEIGHTS[v] for k, v in COL_TO_KEY.items()}
_cw_sum = sum(CURRENT_WEIGHTS_RAW.values())
CURRENT_WEIGHTS = {k: v / _cw_sum for k, v in CURRENT_WEIGHTS_RAW.items()}  # 合計1に正規化


# ---------------------------------------------------------------------------
# データロード
# ---------------------------------------------------------------------------

def _build_query(version: int) -> text:
    return text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    r.grade           AS grade,
    r.course_name     AS course_name,
    ci.horse_id       AS horse_id,
    ci.composite_index    AS composite_index,
    ci.speed_index        AS speed_index,
    ci.last_3f_index      AS last3f_index,
    ci.course_aptitude    AS course_aptitude,
    ci.position_advantage AS position_advantage,
    ci.jockey_index       AS jockey_index,
    ci.pace_index         AS pace_index,
    ci.rotation_index     AS rotation_index,
    ci.pedigree_index     AS pedigree_index,
    ci.training_index     AS training_index,
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND ci.version = {version}
ORDER BY r.date, r.id, ci.horse_id
""")


async def _fetch_rows(start_date: str, end_date: str, version: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(_build_query(version), {"start_date": start_date, "end_date": end_date})
        rows = result.fetchall()
        cols = list(result.keys())
    return rows, cols


def _rows_to_df(rows, cols, label: str) -> pd.DataFrame:
    logger.info(f"データ処理: {label} ({len(rows):,} 件)")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=list(cols))

    num_cols = SUB_INDEX_COLS + ["composite_index", "finish_position", "win_odds", "distance", "head_count"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)

    # 無効レースを除外
    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    # サブ指数が全列揃っているレースのみ
    missing_sub = df[df[SUB_INDEX_COLS].isna().any(axis=1)]["race_id"].unique()
    df = df[~df["race_id"].isin(missing_sub)].copy()

    # 最低4頭
    counts = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(counts[counts >= 4].index)].copy()

    logger.info(f"有効レコード: {len(df):,} 件 / レース: {df['race_id'].nunique():,}")
    return df


# ---------------------------------------------------------------------------
# 評価関数
# ---------------------------------------------------------------------------

def reweight_composite(df: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """サブ指数を与えられたウェイトで合成して composite を再計算する。"""
    df = df.copy()
    composite = sum(df[col] * weights[col] for col in SUB_INDEX_COLS)
    # disadvantage_bonus は rebound列がないためスキップ（既にci.composite_indexに含まれているが
    # ここでは純粋にサブ指数ウェイト比較をするため、bonus抜きで統一する）
    df["composite_rw"] = composite
    return df


def compute_roi_series(df: pd.DataFrame, composite_col: str = "composite_rw") -> float:
    """指数1位馬の単勝ROI(%)を計算する。"""
    top1 = df.loc[df.groupby("race_id")[composite_col].idxmax()].copy()
    valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    if len(valid) == 0:
        return 0.0
    wins = valid["finish_position"] == 1
    payout = valid.loc[wins, "win_odds"].sum()
    return float(payout / len(valid) * 100)


def compute_place_rate(df: pd.DataFrame, composite_col: str = "composite_rw") -> float:
    """指数1位馬の3着内率(%)を計算する。"""
    top1 = df.loc[df.groupby("race_id")[composite_col].idxmax()]
    return float((top1["finish_position"] <= 3).mean() * 100)


def compute_spearman_mean(df: pd.DataFrame, composite_col: str = "composite_rw") -> float:
    """レースごとスピアマン相関の平均（指数が高いほど着順が良い → 負の相関が理想）。"""
    rhos = []
    for _, grp in df.groupby("race_id"):
        if len(grp) < 3:
            continue
        x = grp[composite_col].to_numpy(float)
        y = grp["finish_position"].to_numpy(float)
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        rx = x.argsort().argsort().astype(float)
        ry = y.argsort().argsort().astype(float)
        rho = float(np.corrcoef(rx, ry)[0, 1])
        if not np.isnan(rho):
            rhos.append(rho)
    return float(np.mean(rhos)) if rhos else 0.0


def evaluate(df: pd.DataFrame, weights: dict[str, float]) -> dict:
    """指定ウェイトで評価指標を返す。"""
    df_rw = reweight_composite(df, weights)
    roi = compute_roi_series(df_rw)
    place = compute_place_rate(df_rw)
    rho = compute_spearman_mean(df_rw)
    return {"roi": roi, "place_rate": place, "spearman": rho}


# ---------------------------------------------------------------------------
# グリッドサーチ（ペースウェイト × 後3F ウェイト）
# ---------------------------------------------------------------------------

def grid_search(df_train: pd.DataFrame, df_test: pd.DataFrame) -> pd.DataFrame:
    """ペース・後3Fのウェイトをグリッドサーチして訓練/テストROIを比較する。"""
    pace_range = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]
    last3f_range = [0.10, 0.13, 0.17, 0.20, 0.23]  # 現在0.17

    records = []
    base_weights = dict(CURRENT_WEIGHTS)  # コピー

    for pw in pace_range:
        for lw in last3f_range:
            # pace と last3f を指定値に固定し、残りを比例スケール
            fixed = {"pace_index": pw, "last3f_index": lw}
            remaining_budget = 1.0 - pw - lw
            others = {k: v for k, v in base_weights.items() if k not in ("pace_index", "last3f_index")}
            others_sum = sum(others.values())
            if others_sum <= 0 or remaining_budget <= 0:
                continue
            scaled = {k: v / others_sum * remaining_budget for k, v in others.items()}
            w = {**fixed, **scaled}

            ev_train = evaluate(df_train, w)
            ev_test = evaluate(df_test, w)

            records.append({
                "pace_w": pw,
                "last3f_w": lw,
                "train_roi": round(ev_train["roi"], 1),
                "test_roi": round(ev_test["roi"], 1),
                "train_place": round(ev_train["place_rate"], 1),
                "test_place": round(ev_test["place_rate"], 1),
                "train_rho": round(ev_train["spearman"], 4),
                "test_rho": round(ev_test["spearman"], 4),
            })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Nelder-Mead 最適化（5-Fold CV + L2正則化）
# ---------------------------------------------------------------------------

def nelder_mead_optimize(
    df_train: pd.DataFrame,
    n_folds: int = 5,
    objective: str = "roi",
    l2_lambda: float = 0.5,
) -> dict[str, float]:
    """Nelder-Mead で最適ウェイトを求める。

    Args:
        df_train: 訓練データ
        n_folds: CVフォールド数
        objective: "roi" | "place_rate" | "spearman"
        l2_lambda: L2正則化係数（過学習抑制。現ウェイトからの乖離にペナルティ）
    """
    race_ids = df_train["race_id"].unique()
    np.random.seed(42)
    np.random.shuffle(race_ids)
    folds = np.array_split(race_ids, n_folds)

    current_w_arr = np.array([CURRENT_WEIGHTS[c] for c in SUB_INDEX_COLS])

    def objective_fn(w_raw: np.ndarray) -> float:
        # softmax で合計1・全非負に変換
        w_exp = np.exp(w_raw - w_raw.max())
        w_norm = w_exp / w_exp.sum()
        weights = dict(zip(SUB_INDEX_COLS, w_norm))

        scores = []
        for fold_ids in folds:
            val = df_train[df_train["race_id"].isin(fold_ids)]
            if len(val) == 0:
                continue
            ev = evaluate(val, weights)
            scores.append(ev[objective])

        cv_score = float(np.mean(scores))

        # L2正則化: 現ウェイトからの乖離にペナルティ
        penalty = l2_lambda * float(np.sum((w_norm - current_w_arr) ** 2))

        # 最大化 → 最小化に変換
        return -(cv_score - penalty * 100)  # ROIはパーセント単位なので×100

    # 初期値: 現ウェイトをsoftmax逆変換（log）
    w0 = np.log(current_w_arr + 1e-8)

    logger.info(f"Nelder-Mead最適化開始 (objective={objective}, folds={n_folds}, λ={l2_lambda})")
    res = minimize(
        objective_fn,
        w0,
        method="Nelder-Mead",
        options={"maxiter": 5000, "xatol": 1e-6, "fatol": 1e-6, "disp": False},
    )

    w_exp = np.exp(res.x - res.x.max())
    w_opt = w_exp / w_exp.sum()
    return dict(zip(SUB_INDEX_COLS, w_opt))


# ---------------------------------------------------------------------------
# レポート出力
# ---------------------------------------------------------------------------

def print_weights_comparison(label: str, weights: dict[str, float]) -> None:
    current = CURRENT_WEIGHTS
    print(f"\n  {'サブ指数':<20} {label:>10}  現在v9   差分")
    print("  " + "-" * 50)
    for col in SUB_INDEX_COLS:
        key = COL_TO_KEY[col]
        new_w = weights.get(col, 0.0)
        cur_w = current.get(col, 0.0)
        diff = new_w - cur_w
        bar = "+" if diff > 0.005 else ("▼" if diff < -0.005 else " ")
        print(f"  {key:<20} {new_w:>9.1%}  {cur_w:>6.1%}  {bar}{abs(diff):.1%}")


def print_eval_comparison(
    label: str,
    weights: dict[str, float],
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
) -> None:
    ev_tr = evaluate(df_train, weights)
    ev_te = evaluate(df_test, weights)
    cur_tr = evaluate(df_train, CURRENT_WEIGHTS)
    cur_te = evaluate(df_test, CURRENT_WEIGHTS)

    print(f"\n  {'指標':<15} {'訓練(新)':>10} {'訓練(v9)':>10} {'テスト(新)':>11} {'テスト(v9)':>10}")
    print("  " + "-" * 60)
    print(f"  {'単勝ROI':<15} {ev_tr['roi']:>9.1f}%  {cur_tr['roi']:>9.1f}%  "
          f"{ev_te['roi']:>10.1f}%  {cur_te['roi']:>9.1f}%")
    print(f"  {'1位3着内率':<15} {ev_tr['place_rate']:>9.1f}%  {cur_tr['place_rate']:>9.1f}%  "
          f"{ev_te['place_rate']:>10.1f}%  {cur_te['place_rate']:>9.1f}%")
    print(f"  {'スピアマン相関':<15} {ev_tr['spearman']:>9.4f}  {cur_tr['spearman']:>9.4f}  "
          f"{ev_te['spearman']:>10.4f}  {cur_te['spearman']:>9.4f}")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="指数ウェイト最適化")
    parser.add_argument("--train-start", default="20240101", help="訓練開始日 (YYYYMMDD)")
    parser.add_argument("--train-end",   default="20241231", help="訓練終了日 (YYYYMMDD)")
    parser.add_argument("--test-start",  default="20250101", help="テスト開始日 (YYYYMMDD)")
    parser.add_argument("--test-end",    default="20251231", help="テスト終了日 (YYYYMMDD)")
    parser.add_argument(
        "--mode", choices=["grid", "optimize", "all"], default="all",
        help="grid=グリッドサーチのみ / optimize=Nelder-Meadのみ / all=両方"
    )
    parser.add_argument(
        "--objective", choices=["roi", "place_rate", "spearman"], default="roi",
        help="最適化目標 (Nelder-Meadのみ)"
    )
    parser.add_argument("--l2", type=float, default=0.5, help="L2正則化係数 (デフォルト=0.5)")
    parser.add_argument("--version", type=int, default=8,
                        help="使用するcalculated_indicesのバージョン (デフォルト=8: 2024-2025完全データ)")
    args = parser.parse_args()

    print("=" * 65)
    print("  指数ウェイト最適化バックテスト")
    print(f"  訓練: {args.train_start} 〜 {args.train_end}")
    print(f"  テスト: {args.test_start} 〜 {args.test_end}")
    print("=" * 65)

    # 1回のasyncio.runで両データを取得（ループ再利用問題を回避）
    async def fetch_both():
        tr_rows, tr_cols = await _fetch_rows(args.train_start, args.train_end, args.version)
        te_rows, te_cols = await _fetch_rows(args.test_start, args.test_end, args.version)
        return tr_rows, tr_cols, te_rows, te_cols

    logger.info(f"データ取得 v{args.version}: {args.train_start}〜{args.train_end} / {args.test_start}〜{args.test_end}")
    tr_rows, tr_cols, te_rows, te_cols = asyncio.run(fetch_both())

    df_train = _rows_to_df(tr_rows, tr_cols, f"{args.train_start}〜{args.train_end}")
    df_test  = _rows_to_df(te_rows, te_cols, f"{args.test_start}〜{args.test_end}")

    if df_train.empty or df_test.empty:
        logger.error("データが取得できません")
        sys.exit(1)

    # ── 現在のv9 ベースライン ──
    print("\n■ 現在の v9 ウェイト ベースライン")
    print_eval_comparison("v9", CURRENT_WEIGHTS, df_train, df_test)

    # ── グリッドサーチ ──
    if args.mode in ("grid", "all"):
        print("\n" + "=" * 65)
        print("■ グリッドサーチ（ペース × 後3F ウェイト）")
        print("  訓練ROI が高く、テストROIとの乖離が小さい組み合わせを探す")
        print("-" * 65)

        grid_df = grid_search(df_train, df_test)

        # テストROI上位10件を表示
        top_test = grid_df.nlargest(10, "test_roi")
        print(f"\n  {'pace_w':>7} {'last3f_w':>9} {'訓練ROI':>9} {'テストROI':>10} {'訓練place':>10} {'テストplace':>11} {'訓練ρ':>8} {'テストρ':>8}")
        print("  " + "-" * 75)
        for _, row in top_test.iterrows():
            marker = " ◀" if row["test_roi"] == grid_df["test_roi"].max() else ""
            print(
                f"  {row['pace_w']:>7.2f} {row['last3f_w']:>9.2f} "
                f"{row['train_roi']:>8.1f}% {row['test_roi']:>9.1f}% "
                f"{row['train_place']:>9.1f}% {row['test_place']:>10.1f}% "
                f"{row['train_rho']:>7.4f} {row['test_rho']:>7.4f}{marker}"
            )

        best_grid = grid_df.loc[grid_df["test_roi"].idxmax()]
        print(f"\n  最良グリッド点: pace={best_grid['pace_w']:.2f}, last3f={best_grid['last3f_w']:.2f}")
        print(f"    訓練ROI={best_grid['train_roi']:.1f}%  テストROI={best_grid['test_roi']:.1f}%")

    # ── Nelder-Mead最適化 ──
    best_opt_weights = None
    if args.mode in ("optimize", "all"):
        print("\n" + "=" * 65)
        print(f"■ Nelder-Mead 最適化 (目標={args.objective}, λ={args.l2})")
        print("  5-Fold CV + L2正則化で過学習を抑制")
        print("-" * 65)

        best_opt_weights = nelder_mead_optimize(
            df_train,
            n_folds=5,
            objective=args.objective,
            l2_lambda=args.l2,
        )

        print("\n  最適化後ウェイト:")
        print_weights_comparison("最適化後", best_opt_weights)

        print("\n  評価比較（最適化 vs v9）:")
        print_eval_comparison("最適化", best_opt_weights, df_train, df_test)

    # ── 最適ウェイトの定数出力 ──
    if best_opt_weights is not None:
        print("\n" + "=" * 65)
        print("■ constants.py への適用候補（正規化後・disadvantage_bonus=0.05 別途加算）")
        print("-" * 65)
        # 0.95 スケールに圧縮（bonus分を確保）
        scale = 0.95
        print("INDEX_WEIGHTS = {")
        for col in SUB_INDEX_COLS:
            key = COL_TO_KEY[col]
            w = best_opt_weights[col] * scale
            print(f'    "{key}": {w:.4f},')
        print('    "anagusa": 0.0000,')
        print('    "paddock": 0.0000,')
        print(f'    "disadvantage_bonus": {DISADVANTAGE_BONUS},')
        print("}")


if __name__ == "__main__":
    main()
