"""荒れるレース事前分類器 — LightGBM 学習スクリプト。

build_chaos_dataset.py が出力した parquet を読み込み、
3ターゲット定義 × 5 seed の LightGBM 二値分類器を学習する。
最終モデルは target_a (三連単 >= 100,000円) を採用し
backend/models/chaos_classifier_v1.txt に保存する。

分割:
  train: 2023-01-01 〜 2025-06-30
  test:  2025-07-01 〜 2026-03-31
  fresh: 2026-04-01 〜

評価指標:
  AUC（5 seed 平均）+ lift 表（X=10/20/30%、ターゲット別）

使い方:
  cd backend
  .venv/bin/python scripts/train_chaos_classifier.py
  .venv/bin/python scripts/train_chaos_classifier.py --target a --smoke
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_chaos")

DATASET_PATH = _root / "data" / "roi100" / "chaos_dataset.pkl"
MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODELS_DIR / "chaos_classifier_v1.txt"
METRICS_PATH = MODELS_DIR / "chaos_classifier_v1_metrics.json"

TRAIN_END = "20250630"
TEST_START = "20250701"
TEST_END = "20260331"
FRESH_START = "20260401"

SEEDS = [42, 123, 456, 789, 1000]

# 特徴量リスト（point-in-time: 全て発走前確定）
FEATURES = [
    # レース属性
    "head_count",
    "distance",
    "is_turf",
    "is_handicap",
    "race_num",
    "kai",
    "day",
    "grade_code",
    # 市場構造（確定単勝オッズ）
    "odds_top1",
    "odds_top3_sum",
    "odds_entropy",
    "odds_gap12",
    "odds_gap23",
    "n_over10",
    # モデル構造（v26 win_probability）
    "wp_top1",
    "wp_top3_sum",
    "wp_entropy",
    "wp_mkt_gap",
    "wp_mkt_corr",
]

TARGET_COLS = {
    "a": "target_a",  # 三連単 >= 100,000円
    "b": "target_b",  # 三連単 >= 中央値×5
    "c": "target_c",  # 1-3番人気が3着内に1頭以下
}

LGB_PARAMS_BASE: dict = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "max_depth": 5,
    "min_data_in_leaf": 40,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "deterministic": True,
    "num_threads": 1,  # deterministic のため固定
}
NUM_ROUNDS = 400
EARLY_STOPPING_ROUNDS = 40


def _load_dataset(smoke: bool = False) -> pd.DataFrame:
    """parquet を読み込む。smoke=True の場合 2025-01〜2025-03 に絞る。"""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"データセットが存在しません: {DATASET_PATH}\n先に build_chaos_dataset.py を実行してください。"
        )
    df = pd.read_pickle(DATASET_PATH)
    if smoke:
        # スモーク用: 2025-01〜2025-03 を train 兼 test として使用
        df = df[df["date"] >= "20250101"].copy()
        df = df[df["date"] <= "20250331"].copy()
        logger.info("スモークモード: %d レース (2025-01〜2025-03)", len(df))
    return df


def _split(df: pd.DataFrame, smoke: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """train / test / fresh に分割。smoke モードは train=test=2025-01〜03。"""
    if smoke:
        # スモーク: 全データを train/test 兼用（AUC/lift の動作確認のみ）
        return df.copy(), df.copy(), pd.DataFrame()
    train = df[df["date"] <= TRAIN_END].copy()
    test = df[(df["date"] >= TEST_START) & (df["date"] <= TEST_END)].copy()
    fresh = df[df["date"] >= FRESH_START].copy()
    return train, test, fresh


def _prepare_Xy(df: pd.DataFrame, target_col: str) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    """特徴量行列・ラベルベクトル・有効マスクを返す。

    ターゲットが NaN の行（三連単未実施レース等）は除外する。
    """
    valid = df[target_col].notna()
    dv = df[valid].copy()
    X = dv[FEATURES].values.astype(float)
    y = dv[target_col].astype(int).values
    return X, y, dv


def _train_seeds(
    X_tr: np.ndarray,
    y_tr: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    target_key: str,
) -> tuple[list[lgb.Booster], list[float]]:
    """5 seed で学習し (models, aucs) を返す。"""
    models: list[lgb.Booster] = []
    aucs: list[float] = []

    for seed in SEEDS:
        params = {**LGB_PARAMS_BASE, "seed": seed}
        ds_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURES)
        ds_val = lgb.Dataset(X_val, label=y_val, reference=ds_tr)

        callbacks = [
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(100),
        ]
        model = lgb.train(
            params,
            ds_tr,
            num_boost_round=NUM_ROUNDS,
            valid_sets=[ds_val],
            callbacks=callbacks,
        )
        pred = model.predict(X_val)
        from sklearn.metrics import roc_auc_score

        auc = float(roc_auc_score(y_val, pred)) if y_val.sum() > 0 else float("nan")
        aucs.append(auc)
        models.append(model)
        logger.info("  target=%s seed=%d rounds=%d AUC=%.4f", target_key, seed, model.best_iteration, auc)

    return models, aucs


def _avg_predict(models: list[lgb.Booster], X: np.ndarray) -> np.ndarray:
    """5モデルの予測値平均を返す。"""
    preds = np.stack([m.predict(X) for m in models], axis=1)
    return preds.mean(axis=1)


def _lift_table(
    scores: np.ndarray,
    labels: np.ndarray,
    payouts: np.ndarray | None,
    pcts: tuple[float, ...] = (0.10, 0.20, 0.30),
) -> list[dict]:
    """lift 表を計算。

    分類器スコア上位 X% レースに絞ったとき:
      - 正例率（hit_rate）
      - 三連単払戻の総和/レース数（配当密度）が全体平均の何倍か
    """
    n = len(scores)
    order = np.argsort(scores)[::-1]
    sorted_labels = labels[order]
    sorted_payouts = payouts[order] if payouts is not None else None

    base_hit = labels.mean()
    base_density = float(np.nanmean(payouts)) if (payouts is not None and np.any(~np.isnan(payouts))) else float("nan")

    rows = []
    for pct in pcts:
        k = max(1, int(n * pct))
        top_labels = sorted_labels[:k]
        hit_rate = float(top_labels.mean()) if k > 0 else float("nan")
        lift_hr = hit_rate / base_hit if base_hit > 0 else float("nan")

        if sorted_payouts is not None:
            top_pay = sorted_payouts[:k]
            density = float(np.nanmean(top_pay))
            lift_density = density / base_density if base_density > 0 else float("nan")
        else:
            density = float("nan")
            lift_density = float("nan")

        rows.append(
            {
                "top_pct": int(pct * 100),
                "n": k,
                "hit_rate": round(hit_rate, 4),
                "lift_hit_rate": round(lift_hr, 3),
                "avg_payout": round(density) if not np.isnan(density) else None,
                "lift_payout_density": round(lift_density, 3) if not np.isnan(lift_density) else None,
            }
        )
    return rows


def _simple_rules(df: pd.DataFrame, target_col: str) -> dict:
    """単純ルールベースライン評価。

    ルール1: 頭数>=14 かつ ハンデ戦
    ルール2: 1番人気オッズ >= 3.5
    """
    valid = df[target_col].notna() & df["odds_top1"].notna()
    dv = df[valid]
    labels = dv[target_col].astype(int).values
    base_hr = labels.mean()
    payouts = dv["trifecta_payout"].values if "trifecta_payout" in dv.columns else None

    results = {}

    for rule_name, mask_fn in [
        ("heads14_handicap", lambda d: (d["head_count"] >= 14) & (d["is_handicap"] == 1)),
        ("odds_top1_ge35", lambda d: d["odds_top1"] >= 3.5),
    ]:
        mask = mask_fn(dv).values
        n_selected = int(mask.sum())
        if n_selected == 0:
            results[rule_name] = {"n": 0, "hit_rate": float("nan"), "lift": float("nan")}
            continue
        selected_labels = labels[mask]
        hr = float(selected_labels.mean())
        lift = hr / base_hr if base_hr > 0 else float("nan")
        avg_pay = float(np.nanmean(payouts[mask])) if payouts is not None else float("nan")
        results[rule_name] = {
            "n": n_selected,
            "selection_rate": round(n_selected / len(dv), 3),
            "hit_rate": round(hr, 4),
            "base_hit_rate": round(base_hr, 4),
            "lift": round(lift, 3),
            "avg_payout": round(avg_pay) if not np.isnan(avg_pay) else None,
        }

    return results


def train_target(
    target_key: str,
    df_all: pd.DataFrame,
    smoke: bool = False,
) -> dict:
    """1ターゲット定義のモデルを学習・評価し結果 dict を返す。"""
    target_col = TARGET_COLS[target_key]
    logger.info("=== ターゲット %s (%s) ===", target_key.upper(), target_col)

    train, test, fresh = _split(df_all, smoke=smoke)

    # 有効データの確認
    tr_valid = train[target_col].notna().sum()
    te_valid = test[target_col].notna().sum() if not test.empty else 0
    logger.info(
        "  train=%d (有効=%d) / test=%d (有効=%d) / fresh=%d",
        len(train),
        tr_valid,
        len(test),
        te_valid,
        len(fresh),
    )

    if tr_valid < 50 or (te_valid < 10 and not smoke):
        logger.warning("  データ不足。スキップ。")
        return {}

    X_tr, y_tr, dv_tr = _prepare_Xy(train, target_col)
    X_te, y_te, dv_te = _prepare_Xy(test if not test.empty else train, target_col)

    # 学習
    models, aucs = _train_seeds(X_tr, y_tr, X_te, y_te, target_key)
    avg_auc = float(np.nanmean(aucs))
    logger.info("  AUC 5seed平均: %.4f (min=%.4f, max=%.4f)", avg_auc, min(aucs), max(aucs))

    # lift 表
    score_te = _avg_predict(models, X_te)
    pay_te = dv_te["trifecta_payout"].values if "trifecta_payout" in dv_te.columns else None
    lift_te = _lift_table(score_te, y_te, pay_te)

    # fresh
    lift_fr: list[dict] = []
    if not fresh.empty:
        X_fr, y_fr, dv_fr = _prepare_Xy(fresh, target_col)
        if len(X_fr) > 0 and y_fr.sum() > 0:
            score_fr = _avg_predict(models, X_fr)
            pay_fr = dv_fr["trifecta_payout"].values if "trifecta_payout" in dv_fr.columns else None
            lift_fr = _lift_table(score_fr, y_fr, pay_fr)

    # 単純ルールベースライン（test or smoke dataで）
    simple = _simple_rules(test if not test.empty else train, target_col)

    # 特徴量重要度（seed 0 のモデルで）
    imp = pd.Series(
        models[0].feature_importance(importance_type="gain"),
        index=FEATURES,
    ).sort_values(ascending=False)
    top10_importance = [{"feature": str(f), "gain": round(float(v), 2)} for f, v in imp.head(10).items()]

    result = {
        "target": target_col,
        "n_train": int(tr_valid),
        "n_test": int(te_valid),
        "n_fresh": int(len(fresh[target_col].dropna())) if not fresh.empty else 0,
        "auc_mean": round(avg_auc, 4),
        "auc_seeds": [round(a, 4) for a in aucs],
        "lift_test": lift_te,
        "lift_fresh": lift_fr,
        "simple_rules": simple,
        "feature_importance_top10": top10_importance,
        "positive_rate_train": round(float(y_tr.mean()), 4),
        "positive_rate_test": round(float(y_te.mean()), 4),
    }

    return result, models


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="荒れるレース LightGBM 学習")
    parser.add_argument(
        "--target", choices=["a", "b", "c", "all"], default="all", help="学習するターゲット定義 (default: all)"
    )
    parser.add_argument("--smoke", action="store_true", help="スモークテスト: 2025-01〜03 の3ヶ月窓のみ")
    args = parser.parse_args()

    df_all = _load_dataset(smoke=args.smoke)
    logger.info("データセット読み込み: %d レース", len(df_all))

    target_keys = ["a", "b", "c"] if args.target == "all" else [args.target]
    all_results: dict = {}
    primary_models: list[lgb.Booster] | None = None  # target_a 採用

    for tk in target_keys:
        res = train_target(tk, df_all, smoke=args.smoke)
        if not res:
            continue
        result, models = res
        all_results[tk] = result

        if tk == "a":
            primary_models = models

    # 結果サマリ表示
    logger.info("\n=== 結果サマリ ===")
    for tk, res in all_results.items():
        logger.info(
            "Target %s: n_train=%d n_test=%d AUC=%.4f positive_rate=%.1f%%",
            tk.upper(),
            res["n_train"],
            res["n_test"],
            res["auc_mean"],
            100 * res["positive_rate_train"],
        )
        logger.info("  lift (test):")
        for row in res["lift_test"]:
            logger.info(
                "    top %2d%%: hit_rate=%.1f%% lift_hit=%.3fx lift_pay=%.3fx",
                row["top_pct"],
                100 * row["hit_rate"],
                row["lift_hit_rate"],
                row["lift_payout_density"] or 0.0,
            )
        if res["lift_fresh"]:
            logger.info("  lift (fresh):")
            for row in res["lift_fresh"]:
                logger.info(
                    "    top %2d%%: hit_rate=%.1f%% lift_hit=%.3fx lift_pay=%.3fx",
                    row["top_pct"],
                    100 * row["hit_rate"],
                    row["lift_hit_rate"],
                    row["lift_payout_density"] or 0.0,
                )
        logger.info("  単純ルール比較:")
        for rule_name, rule_res in res["simple_rules"].items():
            logger.info(
                "    %s: n=%d hr=%.1f%% lift=%.3fx",
                rule_name,
                rule_res["n"],
                100 * rule_res.get("hit_rate", 0),
                rule_res.get("lift", 0),
            )
        logger.info("  特徴量重要度 top10:")
        for imp_row in res["feature_importance_top10"]:
            logger.info("    %s: %.2f", imp_row["feature"], imp_row["gain"])

    # モデル保存（target_a を primary として採用）
    if primary_models and not args.smoke:
        primary_models[0].save_model(str(MODEL_PATH))
        logger.info("モデル保存: %s", MODEL_PATH)
    elif primary_models and args.smoke:
        logger.info("スモークモード: モデルファイルは保存しません")

    # メトリクス保存
    metrics_out = {
        "results": all_results,
        "adopted_target": "a",
        "adopted_reason": "三連単>=100,000円は明確な高配当基準で事前予測可能性と配当妙味のバランスが良い",
        "model_path": str(MODEL_PATH),
    }
    if not args.smoke:
        with open(METRICS_PATH, "w", encoding="utf-8") as f:
            json.dump(metrics_out, f, ensure_ascii=False, indent=2, default=str)
        logger.info("メトリクス保存: %s", METRICS_PATH)
    else:
        logger.info("スモーク結果:\n%s", json.dumps(metrics_out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
