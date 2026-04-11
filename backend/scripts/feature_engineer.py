"""Feature Engineer — 交互作用項込みウェイト最適化

weight_optimizer.py を発展させ、交互作用項（2指数の積）を加えた拡張特徴量で
穴馬ROI（単勝/複勝）を最大化するウェイトを探索する。

設計:
  - ベース指数（12個）+ 交互作用項（≤15個）= 最大27次元
  - ウェイト予算: ベース 0.80 + 交互作用 0.20 = 1.00
  - 最適化: Nelder-Mead + Softmax 変換 + 5-Fold CV + L2 正則化
  - 目標関数: upside_place_roi（デフォルト）/ upside_win_roi / place_rate / roi

使い方:
  # デフォルト（interaction_candidates.json を自動読み込み）
  uv run python scripts/feature_engineer.py \\
      --train 20230101-20241231 --test 20250101-20251231

  # 交互作用項 JSON を明示指定
  uv run python scripts/feature_engineer.py \\
      --train 20230101-20241231 --test 20250101-20251231 \\
      --interactions scripts/interaction_candidates.json \\
      --objective upside_win_roi

  # 結果を JSON に出力
  uv run python scripts/feature_engineer.py \\
      --train 20230101-20241231 --test 20250101-20251231 \\
      --out scripts/optimization_result.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.indices.composite import COMPOSITE_VERSION
from src.utils.constants import INDEX_WEIGHTS

# analyst_agent から共通関数を import
sys.path.insert(0, str(_here.parent))
from analyst_agent import (
    INDEX_COLS,
    INDEX_LABELS,
    UPSIDE_ODDS_THRESHOLD,
    filter_valid,
    add_ranks,
    load_data as _analyst_load_data,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("feature_engineer")

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

BASE_BUDGET = 0.80  # ベース指数ウェイトの合計上限
INTER_BUDGET = 0.20  # 交互作用項ウェイトの合計上限

# weight_optimizer.py との列名対応（DB列名 → constants.INDEX_WEIGHTS キー）
COL_TO_WEIGHT_KEY = {
    "speed_index": "speed",
    "last_3f_index": "last_3f",
    "course_aptitude": "course_aptitude",
    "position_advantage": "position_advantage",
    "jockey_index": "jockey_trainer",
    "pace_index": "pace",
    "rotation_index": "rotation",
    "pedigree_index": "pedigree",
    "training_index": "training",
    "anagusa_index": "anagusa",
    "paddock_index": "paddock",
    "rebound_index": "disadvantage_bonus",
}

# 現行ウェイト（constants.INDEX_WEIGHTS から取得）
CURRENT_WEIGHTS_RAW: dict[str, float] = {
    col: float(INDEX_WEIGHTS.get(key, 0.0)) for col, key in COL_TO_WEIGHT_KEY.items()
}
_cw_sum = sum(CURRENT_WEIGHTS_RAW.values()) or 1.0
# ベース予算（0.80）に正規化した現行ウェイト
CURRENT_BASE_WEIGHTS: dict[str, float] = {
    col: v / _cw_sum * BASE_BUDGET for col, v in CURRENT_WEIGHTS_RAW.items()
}


# ---------------------------------------------------------------------------
# データロード
# ---------------------------------------------------------------------------


def load_data(
    start_date: str,
    end_date: str,
    version: int = COMPOSITE_VERSION,
) -> pd.DataFrame:
    """analyst_agent.load_data のラッパー。filter_valid・add_ranks 済みデータを返す。

    Args:
        start_date: 開始日 (YYYYMMDD)
        end_date: 終了日 (YYYYMMDD)
        version: calculated_indices バージョン

    Returns:
        filter_valid 済み DataFrame
    """
    df = _analyst_load_data(start_date, end_date, version=version)
    if df.empty:
        return df
    df = filter_valid(df)
    df = add_ranks(df)
    return df


# ---------------------------------------------------------------------------
# 交互作用項の追加
# ---------------------------------------------------------------------------


def add_interaction_features(
    df: pd.DataFrame,
    interactions: list[dict],
) -> pd.DataFrame:
    """交互作用項列を DataFrame に追加する。

    各交互作用項 = f_i * f_j / 100（スケール調整: 0-100 × 0-100 → ÷100 → 0-100相当）

    Args:
        df: ベース指数列を含む DataFrame
        interactions: analyst_agent.score_interactions の戻り値
            [{"col_i": "speed_index", "col_j": "rebound_index", "feature": "speed_index*rebound_index"}, ...]

    Returns:
        交互作用項列を追加した DataFrame
    """
    df = df.copy()
    for inter in interactions:
        col_i = inter["col_i"]
        col_j = inter["col_j"]
        feat = inter["feature"]
        if col_i in df.columns and col_j in df.columns:
            df[feat] = df[col_i] * df[col_j] / 100.0
    return df


# ---------------------------------------------------------------------------
# スコア計算（最適化ループ内で使用）
# ---------------------------------------------------------------------------


def compute_composite_from_weights(
    df: pd.DataFrame,
    base_weights: dict[str, float],
    inter_weights: dict[str, float],
) -> pd.Series:
    """ベース指数ウェイト + 交互作用項ウェイトで総合スコアを算出する。

    Args:
        df: add_interaction_features 済みデータ
        base_weights: {列名: ウェイト} (合計 ≈ BASE_BUDGET)
        inter_weights: {交互作用項列名: ウェイト} (合計 ≤ INTER_BUDGET)

    Returns:
        pd.Series: 各行のスコア
    """
    score = pd.Series(0.0, index=df.index)
    for col, w in base_weights.items():
        if col in df.columns:
            score += df[col].fillna(50.0) * w
    for col, w in inter_weights.items():
        if col in df.columns:
            score += df[col].fillna(df[col].mean() if df[col].notna().any() else 0.0) * w
    return score


# ---------------------------------------------------------------------------
# 評価関数
# ---------------------------------------------------------------------------


def evaluate(
    df: pd.DataFrame,
    base_weights: dict[str, float],
    inter_weights: dict[str, float],
    objective: str = "upside_place_roi",
    odds_threshold: float = UPSIDE_ODDS_THRESHOLD,
    upside_top_n: int = 3,
) -> float:
    """指定ウェイトで評価スコアを計算する。

    Args:
        df: add_interaction_features + add_ranks 済みデータ
        base_weights: ベース指数ウェイト
        inter_weights: 交互作用項ウェイト
        objective: 最適化目標
            "upside_place_roi" | "upside_win_roi" | "place_rate" | "roi"
        odds_threshold: 穴馬判定の単勝オッズ閾値
        upside_top_n: 穴馬候補として選ぶ上位頭数

    Returns:
        float: 評価スコア（大きいほど良い）
    """
    df = df.copy()
    df["_score"] = compute_composite_from_weights(df, base_weights, inter_weights)

    if objective in ("upside_place_roi", "upside_win_roi"):
        df["_rank"] = df.groupby("race_id")["_score"].rank(ascending=False, method="min")
        candidates = df[(df["_rank"] <= upside_top_n) & (df["win_odds"] >= odds_threshold)]
        if candidates.empty:
            return 0.0
        n = len(candidates)
        if objective == "upside_win_roi":
            wins = candidates[candidates["finish_position"] == 1]
            return float(wins["win_odds"].sum() / n * 100)
        else:  # upside_place_roi
            places = candidates[candidates["finish_position"] <= 3]
            if (
                "place_odds" not in candidates.columns
                or candidates["place_odds"].notna().mean() < 0.2
            ):
                # place_odds が少ない場合は win_roi にフォールバック
                wins = candidates[candidates["finish_position"] == 1]
                return float(wins["win_odds"].sum() / n * 100)
            return float(places["place_odds"].sum() / n * 100)

    elif objective == "place_rate":
        top1 = df.loc[df.groupby("race_id")["_score"].idxmax()]
        return float((top1["finish_position"] <= 3).mean() * 100)

    else:  # roi (単勝ROI)
        top1 = df.loc[df.groupby("race_id")["_score"].idxmax()]
        valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
        if valid.empty:
            return 0.0
        return float(
            valid.loc[valid["finish_position"] == 1, "win_odds"].sum() / len(valid) * 100
        )


# ---------------------------------------------------------------------------
# Nelder-Mead 最適化
# ---------------------------------------------------------------------------


def nelder_mead_optimize(
    df_train: pd.DataFrame,
    inter_names: list[str],
    objective: str = "upside_place_roi",
    n_folds: int = 5,
    l2_lambda: float = 0.5,
    odds_threshold: float = UPSIDE_ODDS_THRESHOLD,
) -> tuple[dict[str, float], dict[str, float]]:
    """Nelder-Mead で最適ウェイトを探索する。

    パラメータ構造（2段階 Softmax）:
      - raw_base[12]  → softmax → scale to BASE_BUDGET
      - raw_inter[N]  → softmax → scale to INTER_BUDGET

    L2 正則化: ベース指数は現行ウェイト（CURRENT_BASE_WEIGHTS）からの乖離にペナルティ

    Args:
        df_train: 訓練データ（add_interaction_features 済み）
        inter_names: 交互作用項列名のリスト
        objective: 最適化目標
        n_folds: CV フォールド数
        l2_lambda: L2 正則化係数
        odds_threshold: 穴馬判定オッズ閾値

    Returns:
        (base_weights, inter_weights): 最適化済みウェイト
    """
    n_base = len(INDEX_COLS)
    n_inter = len(inter_names)

    race_ids = df_train["race_id"].unique()
    np.random.seed(42)
    np.random.shuffle(race_ids)
    folds = np.array_split(race_ids, n_folds)

    # 現行ベースウェイトを配列化（正規化済み、BASE_BUDGET スケール）
    current_base_arr = np.array([CURRENT_BASE_WEIGHTS.get(c, 0.0) for c in INDEX_COLS])
    current_base_norm = current_base_arr / (current_base_arr.sum() or 1.0)

    def _decode(params: np.ndarray) -> tuple[dict[str, float], dict[str, float]]:
        raw_base = params[:n_base]
        raw_inter = params[n_base:] if n_inter > 0 else np.array([])

        exp_base = np.exp(raw_base - raw_base.max())
        base_norm = exp_base / exp_base.sum()
        base_w = {col: float(base_norm[i] * BASE_BUDGET) for i, col in enumerate(INDEX_COLS)}

        inter_w: dict[str, float] = {}
        if n_inter > 0:
            exp_inter = np.exp(raw_inter - raw_inter.max())
            inter_norm = exp_inter / exp_inter.sum()
            inter_w = {col: float(inter_norm[i] * INTER_BUDGET) for i, col in enumerate(inter_names)}

        return base_w, inter_w

    def objective_fn(params: np.ndarray) -> float:
        base_w, inter_w = _decode(params)

        scores = []
        for fold_ids in folds:
            val = df_train[df_train["race_id"].isin(fold_ids)]
            if len(val) == 0:
                continue
            s = evaluate(val, base_w, inter_w, objective=objective, odds_threshold=odds_threshold)
            scores.append(s)

        cv_score = float(np.mean(scores)) if scores else 0.0

        # L2 正則化: ベース指数の現行ウェイトからの乖離にペナルティ
        raw_base = params[:n_base]
        exp_base = np.exp(raw_base - raw_base.max())
        base_norm = exp_base / exp_base.sum()
        penalty = l2_lambda * float(np.sum((base_norm - current_base_norm) ** 2))

        # 最大化 → 最小化（スケールは ROI=100前後なので ×1 でOK）
        return -(cv_score - penalty * 100)

    # 初期値: ベースは現行ウェイトの log、交互作用は均等
    w0_base = np.log(current_base_norm + 1e-8)
    w0_inter = np.zeros(n_inter) if n_inter > 0 else np.array([])
    w0 = np.concatenate([w0_base, w0_inter])

    logger.info(
        f"Nelder-Mead最適化開始 (objective={objective}, "
        f"dims={n_base}+{n_inter}, folds={n_folds}, λ={l2_lambda})"
    )
    res = minimize(
        objective_fn,
        w0,
        method="Nelder-Mead",
        options={"maxiter": 10000, "xatol": 1e-6, "fatol": 1e-6, "disp": False},
    )
    logger.info(f"最適化完了: iterations={res.nit}, success={res.success}")

    base_w, inter_w = _decode(res.x)
    return base_w, inter_w


# ---------------------------------------------------------------------------
# 評価テーブル出力
# ---------------------------------------------------------------------------


def make_eval_table(
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    base_w_new: dict[str, float],
    inter_w_new: dict[str, float],
    objective: str,
    odds_threshold: float,
) -> dict:
    """訓練/テスト期間で現行 vs 新ウェイトの全指標を比較した dict を返す。

    Args:
        df_train: 訓練データ（add_interaction_features 済み）
        df_test: テストデータ（add_interaction_features 済み）
        base_w_new: 新ベースウェイト
        inter_w_new: 新交互作用項ウェイト
        objective: 最適化目標（レポート表示用）
        odds_threshold: 穴馬判定オッズ閾値

    Returns:
        dict: 各期間 × 各指標の評価結果
    """
    metrics = ["upside_place_roi", "upside_win_roi", "place_rate", "roi"]
    current_base = CURRENT_BASE_WEIGHTS
    current_inter: dict[str, float] = {}

    result = {}
    for split_label, df in [("train", df_train), ("test", df_test)]:
        for m in metrics:
            cur = evaluate(df, current_base, current_inter, objective=m, odds_threshold=odds_threshold)
            new = evaluate(df, base_w_new, inter_w_new, objective=m, odds_threshold=odds_threshold)
            result[f"{split_label}_{m}_current"] = round(cur, 1)
            result[f"{split_label}_{m}_new"] = round(new, 1)
            result[f"{split_label}_{m}_diff"] = round(new - cur, 1)

    # 過学習フラグ: テスト期間の目標指標が訓練期間比 -10% 以上悪化
    obj_train_new = result[f"train_{objective}_new"]
    obj_test_new = result[f"test_{objective}_new"]
    overfit_flag = (obj_test_new < obj_train_new * 0.90) if obj_train_new > 0 else False
    result["overfit_flag"] = overfit_flag

    return result


def print_eval_table(result: dict, objective: str) -> None:
    """評価テーブルをコンソールに出力する。"""
    label_map = {
        "upside_place_roi": "穴馬複勝ROI%",
        "upside_win_roi": "穴馬単勝ROI%",
        "place_rate": "1位3着内率%",
        "roi": "1位単勝ROI%",
    }

    print(
        f"\n  {'指標':<16} {'訓練(現行)':>10} {'訓練(新)':>10} {'差':>6}"
        f" {'テスト(現行)':>11} {'テスト(新)':>10} {'差':>6}"
    )
    print("  " + "-" * 76)
    for m, lbl in label_map.items():
        is_obj = "★" if m == objective else " "
        tr_cur = result[f"train_{m}_current"]
        tr_new = result[f"train_{m}_new"]
        tr_diff = result[f"train_{m}_diff"]
        te_cur = result[f"test_{m}_current"]
        te_new = result[f"test_{m}_new"]
        te_diff = result[f"test_{m}_diff"]
        diff_sign = "+" if te_diff >= 0 else ""
        print(
            f"  {is_obj}{lbl:<15} {tr_cur:>10.1f} {tr_new:>10.1f} {'+' if tr_diff>=0 else ''}{tr_diff:>5.1f}"
            f" {te_cur:>11.1f} {te_new:>10.1f} {diff_sign}{te_diff:>5.1f}"
        )

    flag = result.get("overfit_flag", False)
    print(f"\n  過学習フラグ: {'⚠️  あり（リジェクト推奨）' if flag else 'なし'}")


def print_weights_table(base_w: dict[str, float], inter_w: dict[str, float]) -> None:
    """新ウェイトと現行ウェイトを比較表示する。"""
    print(f"\n  {'指数名':<22} {'新ウェイト':>10}  {'現行':>8}  {'差分':>8}")
    print("  " + "-" * 54)
    for col in INDEX_COLS:
        key = COL_TO_WEIGHT_KEY.get(col, col)
        new_w = base_w.get(col, 0.0)
        cur_w = float(INDEX_WEIGHTS.get(key, 0.0))
        diff = new_w - cur_w
        bar = "▲" if diff > 0.005 else ("▼" if diff < -0.005 else " ")
        print(f"  {col:<22} {new_w:>9.1%}  {cur_w:>7.1%}  {bar}{abs(diff):.1%}")

    if inter_w:
        print(f"\n  {'交互作用項':<35} {'新ウェイト':>10}")
        print("  " + "-" * 48)
        for feat, w in sorted(inter_w.items(), key=lambda x: -x[1]):
            cols = feat.split("*")
            label = " × ".join(INDEX_LABELS.get(c, c) for c in cols)
            print(f"  {label:<35} {w:>9.1%}")


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(description="Feature Engineer — 交互作用項込みウェイト最適化")
    parser.add_argument(
        "--train", default="20230101-20241231", help="訓練期間 (YYYYMMDD-YYYYMMDD)"
    )
    parser.add_argument(
        "--test", default="20250101-20251231", help="テスト期間 (YYYYMMDD-YYYYMMDD)"
    )
    parser.add_argument(
        "--objective",
        choices=["upside_place_roi", "upside_win_roi", "place_rate", "roi"],
        default="upside_place_roi",
        help="最適化目標",
    )
    parser.add_argument(
        "--interactions",
        default=None,
        help="交互作用項候補 JSON（省略時: scripts/interaction_candidates.json）",
    )
    parser.add_argument("--l2", type=float, default=0.5, help="L2 正則化係数")
    parser.add_argument("--folds", type=int, default=5, help="CV フォールド数")
    parser.add_argument("--min-odds", type=float, default=10.0, help="穴馬判定オッズ閾値")
    parser.add_argument(
        "--version", type=int, default=COMPOSITE_VERSION, help="calculated_indices バージョン"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="最適化結果 JSON 出力パス（省略時: scripts/optimization_result.json）",
    )
    args = parser.parse_args()

    # 期間パース
    train_start, train_end = args.train.split("-", 1)
    test_start, test_end = args.test.split("-", 1)

    # 交互作用項 JSON 読み込み
    inter_path = Path(args.interactions) if args.interactions else _here.parent / "interaction_candidates.json"
    interactions: list[dict] = []
    if inter_path.exists():
        payload = json.loads(inter_path.read_text())
        interactions = payload.get("top_interactions", [])
        logger.info(f"交互作用項候補: {len(interactions)} 個 ({inter_path})")
    else:
        logger.warning(f"交互作用項 JSON が見つかりません: {inter_path}（ベース指数のみで最適化）")

    inter_names = [d["feature"] for d in interactions]

    print("=" * 68)
    print("  Feature Engineer — 交互作用項込みウェイト最適化")
    print(f"  訓練: {train_start} 〜 {train_end}")
    print(f"  テスト: {test_start} 〜 {test_end}")
    print(f"  目標: {args.objective}")
    print(f"  交互作用項数: {len(inter_names)}")
    print("=" * 68)

    # データロード
    logger.info("訓練データ読み込み中...")
    df_train = load_data(train_start, train_end, version=args.version)
    logger.info("テストデータ読み込み中...")
    df_test = load_data(test_start, test_end, version=args.version)

    if df_train.empty or df_test.empty:
        print("データなし")
        return

    # 交互作用項列を追加
    df_train = add_interaction_features(df_train, interactions)
    df_test = add_interaction_features(df_test, interactions)

    # 最適化実行
    base_w, inter_w = nelder_mead_optimize(
        df_train,
        inter_names=inter_names,
        objective=args.objective,
        n_folds=args.folds,
        l2_lambda=args.l2,
        odds_threshold=args.min_odds,
    )

    # 評価テーブル
    eval_result = make_eval_table(
        df_train, df_test, base_w, inter_w, args.objective, args.min_odds
    )

    print(f"\n── ウェイト比較（ベース12指数 + 交互作用項{len(inter_w)}個）")
    print_weights_table(base_w, inter_w)

    print(f"\n── 評価指標比較（objective=★{args.objective}）")
    print_eval_table(eval_result, args.objective)

    # JSON 出力
    out_path = Path(args.out) if args.out else _here.parent / "optimization_result.json"
    output = {
        "meta": {
            "train": args.train,
            "test": args.test,
            "objective": args.objective,
            "l2_lambda": args.l2,
            "n_folds": args.folds,
            "odds_threshold": args.min_odds,
            "n_interactions": len(inter_names),
        },
        "base_weights": {col: round(w, 6) for col, w in base_w.items()},
        "inter_weights": {feat: round(w, 6) for feat, w in inter_w.items()},
        "eval": eval_result,
        "interactions_used": interactions,
    }
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n最適化結果を保存: {out_path}")


if __name__ == "__main__":
    main()
