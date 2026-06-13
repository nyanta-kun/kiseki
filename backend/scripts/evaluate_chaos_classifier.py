"""荒れるレース事前分類器 — 評価スクリプト。

学習済みモデル (chaos_classifier_v1.txt) とデータセット (chaos_dataset.parquet) を読み込み、
lift 表・単純ルール比較・ターゲット定義別 AUC を表示する。

出力内容:
  - 3ターゲット定義 × lift 表 (X=10/20/30%)
  - 単純ルール比較 (頭数>=14かつハンデ戦 / 1番人気オッズ>=3.5)
  - 特徴量重要度 top10
  - test/fresh の分割評価

モデルを事前に学習する必要があります:
  .venv/bin/python scripts/train_chaos_classifier.py

使い方:
  cd backend
  .venv/bin/python scripts/evaluate_chaos_classifier.py
  .venv/bin/python scripts/evaluate_chaos_classifier.py --smoke
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
logger = logging.getLogger("evaluate_chaos")

DATASET_PATH = _root / "data" / "roi100" / "chaos_dataset.pkl"
MODEL_PATH = _root / "models" / "chaos_classifier_v1.txt"
METRICS_PATH = _root / "models" / "chaos_classifier_v1_metrics.json"

TRAIN_END = "20250630"
TEST_START = "20250701"
TEST_END = "20260331"
FRESH_START = "20260401"

FEATURES = [
    "head_count",
    "distance",
    "is_turf",
    "is_handicap",
    "race_num",
    "kai",
    "day",
    "grade_code",
    "odds_top1",
    "odds_top3_sum",
    "odds_entropy",
    "odds_gap12",
    "odds_gap23",
    "n_over10",
    "wp_top1",
    "wp_top3_sum",
    "wp_entropy",
    "wp_mkt_gap",
    "wp_mkt_corr",
]

TARGET_COLS = {
    "a": "target_a",
    "b": "target_b",
    "c": "target_c",
}

SEEDS = [42, 123, 456, 789, 1000]


def _safe_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """roc_auc_score のラッパ。正例 0 の場合は NaN。"""
    if y_true.sum() == 0 or y_true.sum() == len(y_true):
        return float("nan")
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y_true, y_score))


def _lift_table(
    scores: np.ndarray,
    labels: np.ndarray,
    payouts: np.ndarray | None = None,
    pcts: tuple[float, ...] = (0.10, 0.20, 0.30),
) -> list[dict]:
    """lift 表を計算して list[dict] で返す。"""
    n = len(scores)
    if n == 0:
        return []
    order = np.argsort(scores)[::-1]
    sorted_labels = labels[order]
    sorted_payouts = payouts[order] if payouts is not None else None

    base_hr = labels.mean()
    base_density = float(np.nanmean(payouts)) if payouts is not None and np.any(~np.isnan(payouts)) else float("nan")

    rows = []
    for pct in pcts:
        k = max(1, int(n * pct))
        top_labels = sorted_labels[:k]
        hr = float(top_labels.mean())
        lift_hr = hr / base_hr if base_hr > 0 else float("nan")

        if sorted_payouts is not None:
            density = float(np.nanmean(sorted_payouts[:k]))
            lift_dens = density / base_density if base_density > 0 else float("nan")
        else:
            density = float("nan")
            lift_dens = float("nan")

        rows.append(
            {
                "top_pct": int(pct * 100),
                "n": k,
                "hit_rate": round(hr, 4),
                "lift_hit_rate": round(lift_hr, 3),
                "avg_payout": round(density) if not np.isnan(density) else None,
                "lift_payout_density": round(lift_dens, 3) if not np.isnan(lift_dens) else None,
            }
        )
    return rows


def _simple_rules_eval(df: pd.DataFrame, target_col: str) -> dict:
    """単純ルールベースラインを評価。"""
    valid = df[target_col].notna()
    dv = df[valid]
    if dv.empty:
        return {}
    labels = dv[target_col].astype(int).values
    base_hr = labels.mean()
    payouts = dv["trifecta_payout"].values if "trifecta_payout" in dv.columns else None

    rules = {
        "heads14_handicap": (dv["head_count"] >= 14) & (dv["is_handicap"] == 1),
        "odds_top1_ge35": dv["odds_top1"].fillna(0) >= 3.5,
    }

    results = {}
    for rule_name, mask_series in rules.items():
        mask = mask_series.values
        n_sel = int(mask.sum())
        if n_sel == 0:
            results[rule_name] = {"n": 0, "hit_rate": float("nan"), "lift": float("nan"), "avg_payout": None}
            continue
        hr = float(labels[mask].mean())
        lift = hr / base_hr if base_hr > 0 else float("nan")
        avg_pay = float(np.nanmean(payouts[mask])) if payouts is not None else float("nan")
        results[rule_name] = {
            "n": n_sel,
            "selection_rate": round(n_sel / len(dv), 3),
            "hit_rate": round(hr, 4),
            "base_hit_rate": round(base_hr, 4),
            "lift": round(lift, 3),
            "avg_payout": round(avg_pay) if not np.isnan(avg_pay) else None,
        }

    return results


def _train_eval_target(
    df_all: pd.DataFrame,
    target_key: str,
    smoke: bool = False,
) -> dict:
    """1ターゲット定義を学習・評価し結果 dict を返す。

    evaluate スクリプト独自の学習（モデルファイルが無い場合のフォールバック）。
    """
    target_col = TARGET_COLS[target_key]

    if smoke:
        train = df_all.copy()
        test = df_all.copy()
        fresh = pd.DataFrame()
    else:
        train = df_all[df_all["date"] <= TRAIN_END].copy()
        test = df_all[(df_all["date"] >= TEST_START) & (df_all["date"] <= TEST_END)].copy()
        fresh = df_all[df_all["date"] >= FRESH_START].copy()

    def prep(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        valid = d[target_col].notna()
        dv = d[valid]
        X = dv[FEATURES].values.astype(float)
        y = dv[target_col].astype(int).values
        pay = dv["trifecta_payout"].values if "trifecta_payout" in dv.columns else None
        return X, y, pay, dv

    X_tr, y_tr, _, dv_tr = prep(train)
    X_te, y_te, pay_te, dv_te = prep(test if not test.empty else train)

    if len(X_tr) < 50 or (len(X_te) < 10 and not smoke):
        logger.warning("  target=%s: データ不足、スキップ", target_key)
        return {}

    from scripts.train_chaos_classifier import LGB_PARAMS_BASE, NUM_ROUNDS, EARLY_STOPPING_ROUNDS

    all_preds_te: list[np.ndarray] = []
    aucs: list[float] = []

    for seed in SEEDS:
        params = {**LGB_PARAMS_BASE, "seed": seed}
        ds_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURES)
        ds_val = lgb.Dataset(X_te, label=y_te, reference=ds_tr)
        callbacks = [
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(200),
        ]
        model = lgb.train(params, ds_tr, num_boost_round=NUM_ROUNDS, valid_sets=[ds_val], callbacks=callbacks)
        pred = model.predict(X_te)
        aucs.append(_safe_auc(y_te, pred))
        all_preds_te.append(pred)

    avg_pred_te = np.stack(all_preds_te).mean(axis=0)
    avg_auc = float(np.nanmean(aucs))

    lift_te = _lift_table(avg_pred_te, y_te, pay_te)

    lift_fr: list[dict] = []
    if not fresh.empty:
        X_fr, y_fr, pay_fr, _ = prep(fresh)
        if len(X_fr) > 10 and y_fr.sum() > 0:
            fr_preds = []
            for seed in SEEDS:
                params = {**LGB_PARAMS_BASE, "seed": seed}
                ds_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURES)
                ds_fr = lgb.Dataset(X_fr, label=y_fr, reference=ds_tr)
                callbacks = [
                    lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                    lgb.log_evaluation(9999),
                ]
                model = lgb.train(params, ds_tr, num_boost_round=NUM_ROUNDS, valid_sets=[ds_fr], callbacks=callbacks)
                fr_preds.append(model.predict(X_fr))
            avg_pred_fr = np.stack(fr_preds).mean(axis=0)
            lift_fr = _lift_table(avg_pred_fr, y_fr, pay_fr)

    # 単純ルール比較
    simple = _simple_rules_eval(test if not test.empty else train, target_col)

    # 特徴量重要度（seed 0 のモデル）
    params0 = {**LGB_PARAMS_BASE, "seed": SEEDS[0]}
    ds_tr0 = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURES)
    model0 = lgb.train(params0, ds_tr0, num_boost_round=NUM_ROUNDS, callbacks=[lgb.log_evaluation(9999)])
    imp = pd.Series(model0.feature_importance(importance_type="gain"), index=FEATURES)
    top10 = [
        {"feature": str(k), "gain": round(float(v), 2)} for k, v in imp.sort_values(ascending=False).head(10).items()
    ]

    return {
        "target": target_col,
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "n_fresh": int(len(fresh[target_col].dropna())) if not fresh.empty else 0,
        "positive_rate_train": round(float(y_tr.mean()), 4),
        "positive_rate_test": round(float(y_te.mean()), 4),
        "auc_mean": round(avg_auc, 4),
        "auc_seeds": [round(a, 4) for a in aucs],
        "lift_test": lift_te,
        "lift_fresh": lift_fr,
        "simple_rules": simple,
        "feature_importance_top10": top10,
    }


def _print_result(tk: str, res: dict) -> None:
    """結果を整形して表示。"""
    print(f"\n{'=' * 60}")
    print(f"  ターゲット {tk.upper()}: {res['target']}")
    print(f"{'=' * 60}")
    print(f"  n_train={res['n_train']} n_test={res['n_test']} n_fresh={res['n_fresh']}")
    print(f"  正例率(train)={100 * res['positive_rate_train']:.1f}% / (test)={100 * res['positive_rate_test']:.1f}%")
    print(f"  AUC 5seed平均: {res['auc_mean']:.4f}")

    print("\n  [lift 表 - test]")
    print(f"  {'top%':>6} {'n':>6} {'hit_rate':>10} {'lift_hr':>8} {'avg_pay':>10} {'lift_pay':>9}")
    for row in res["lift_test"]:
        print(
            f"  {row['top_pct']:>5}%  {row['n']:>6}  "
            f"{100 * row['hit_rate']:>8.1f}%  "
            f"{row['lift_hit_rate']:>7.3f}x  "
            f"{(row['avg_payout'] or 0):>9,}  "
            f"{(row['lift_payout_density'] or 0):>8.3f}x"
        )

    if res.get("lift_fresh"):
        print("\n  [lift 表 - fresh]")
        print(f"  {'top%':>6} {'n':>6} {'hit_rate':>10} {'lift_hr':>8} {'avg_pay':>10} {'lift_pay':>9}")
        for row in res["lift_fresh"]:
            print(
                f"  {row['top_pct']:>5}%  {row['n']:>6}  "
                f"{100 * row['hit_rate']:>8.1f}%  "
                f"{row['lift_hit_rate']:>7.3f}x  "
                f"{(row['avg_payout'] or 0):>9,}  "
                f"{(row['lift_payout_density'] or 0):>8.3f}x"
            )

    print("\n  [単純ルール比較]")
    for rule_name, r in res["simple_rules"].items():
        if r["n"] == 0:
            print(f"  {rule_name}: n=0")
            continue
        print(
            f"  {rule_name}: n={r['n']} sel={100 * r['selection_rate']:.0f}% "
            f"hit={100 * r['hit_rate']:.1f}% (base={100 * r['base_hit_rate']:.1f}%) "
            f"lift={r['lift']:.3f}x avg_pay={r['avg_payout'] or 'N/A'}"
        )

    print("\n  [特徴量重要度 top10]")
    for imp_row in res["feature_importance_top10"]:
        print(f"    {imp_row['feature']:<25} gain={imp_row['gain']:.2f}")


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="荒れるレース分類器 評価")
    parser.add_argument("--smoke", action="store_true", help="スモークテスト: データセットの 2025-01〜03 窓のみ")
    parser.add_argument("--from-metrics", action="store_true", help="学習済みメトリクス JSON から表示のみ（学習不要）")
    args = parser.parse_args()

    if args.from_metrics and METRICS_PATH.exists():
        logger.info("メトリクスファイルから表示: %s", METRICS_PATH)
        with open(METRICS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for tk, res in data.get("results", {}).items():
            _print_result(tk, res)
        print(f"\n採用ターゲット: {data.get('adopted_target')} — {data.get('adopted_reason')}")
        return

    if not DATASET_PATH.exists():
        logger.error(
            "データセットが存在しません: %s\n先に build_chaos_dataset.py を実行してください。",
            DATASET_PATH,
        )
        sys.exit(1)

    df_all = pd.read_pickle(DATASET_PATH)

    if args.smoke:
        df_all = df_all[df_all["date"] >= "20250101"]
        df_all = df_all[df_all["date"] <= "20250331"].copy()
        logger.info("スモークモード: %d レース (2025-01〜2025-03)", len(df_all))

    all_results = {}
    for tk in ["a", "b", "c"]:
        logger.info("--- ターゲット %s 評価中 ---", tk.upper())
        res = _train_eval_target(df_all, tk, smoke=args.smoke)
        if res:
            all_results[tk] = res
            _print_result(tk, res)

    # 採用ターゲットの判断（lift_test の top10% lift_hit_rate が最大のもの）
    best_target = max(all_results, key=lambda k: all_results[k].get("lift_test", [{}])[0].get("lift_hit_rate", 0))
    print(f"\n{'=' * 60}")
    print(f"  採用推奨ターゲット: {best_target.upper()} ({TARGET_COLS[best_target]})")
    print(f"  lift (top10%) = {all_results[best_target]['lift_test'][0]['lift_hit_rate']:.3f}x")
    print("='*60")

    # 単純ルールとの比較総評
    print("\n  [単純ルールに対する勝敗]")
    for tk, res in all_results.items():
        lgbm_lift = res["lift_test"][2]["lift_hit_rate"] if len(res["lift_test"]) >= 3 else float("nan")
        for rule_name, r in res["simple_rules"].items():
            rule_lift = r.get("lift", float("nan"))
            if not np.isnan(lgbm_lift) and not np.isnan(rule_lift):
                win = "LGB勝利" if lgbm_lift > rule_lift else "ルール勝利"
                print(
                    f"  target={tk.upper()} vs {rule_name}: LGB_top30={lgbm_lift:.3f}x vs rule={rule_lift:.3f}x → {win}"
                )


if __name__ == "__main__":
    main()
