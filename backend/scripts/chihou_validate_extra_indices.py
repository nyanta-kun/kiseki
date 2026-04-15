"""地方競馬 追加指数検証スクリプト

既存5指数に新規指数を1つ加えた場合のROI改善を測定する。
指数ごとに最適化を実行し、全9指数の結果を比較表示する。

使い方:
  uv run python scripts/chihou_validate_extra_indices.py
  uv run python scripts/chihou_validate_extra_indices.py --indices frame_bias,trainer
  uv run python scripts/chihou_validate_extra_indices.py --index frame_bias  # 1件のみ
  uv run python scripts/chihou_validate_extra_indices.py --version 4 --min-odds 15.0 --l2 3.0

出力例:
  === 追加指数 検証結果サマリー ===
  指数名          テストROI差  過学習  判定
  枠順バイアス      +1.8%     なし   採用候補
  脚質展開         +0.3%     なし   微改善
  前走着差         -0.5%     なし   却下
  ...
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import create_engine

from src.config import settings
from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

sys.path.insert(0, str(_here.parent))
from chihou_analyst_agent import (
    filter_valid,
    add_ranks,
    load_data as _analyst_load_data,
)
from chihou_extra_indices import EXTRA_INDEX_REGISTRY, EXTRA_INDEX_LABELS
from chihou_feature_engineer import (
    UPSIDE_ODDS_THRESHOLD,
    add_interaction_features,
    evaluate,
    nelder_mead_optimize,
    make_eval_table,
    print_weights_table,
    print_eval_table,
    INDEX_COLS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_validate_extra_indices")

# デフォルトパラメータ (v4採用時と同じ設定)
DEFAULT_TRAIN = "20240101-20251231"
DEFAULT_TEST = "20260101-20261231"
DEFAULT_MIN_ODDS = 15.0
DEFAULT_L2 = 3.0
DEFAULT_FOLDS = 3


def load_base_data(
    train: str,
    test: str,
    version: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """訓練・テストデータを1回だけ読み込む（全9検証で共有）"""
    train_start, train_end = train.split("-", 1)
    test_start, test_end = test.split("-", 1)

    logger.info("訓練データ読み込み中...")
    df_train_raw = _analyst_load_data(train_start, train_end, version=version)
    logger.info("テストデータ読み込み中...")
    df_test_raw = _analyst_load_data(test_start, test_end, version=version)

    if df_train_raw.empty or df_test_raw.empty:
        raise RuntimeError("データなし。終了します。")

    df_train = filter_valid(df_train_raw)
    df_train = add_ranks(df_train)
    df_test = filter_valid(df_test_raw)
    df_test = add_ranks(df_test)
    return df_train, df_test


def run_baseline_optimization(
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    min_odds: float,
    l2: float,
    folds: int,
) -> tuple[dict[str, float], dict[str, float], float, float]:
    """5指数のみで再最適化してベースラインROIを計算する。

    Returns:
        (base_w, inter_w, baseline_train_roi, baseline_test_roi)
    """
    logger.info("\n" + "=" * 60)
    logger.info("  [ベースライン] 5指数のみ再最適化")
    logger.info("=" * 60)

    df_tr = add_interaction_features(df_train.copy(), [])
    df_te = add_interaction_features(df_test.copy(), [])

    base_w, inter_w = nelder_mead_optimize(
        df_tr,
        inter_names=[],
        objective="upside_win_roi",
        n_folds=folds,
        l2_lambda=l2,
        odds_threshold=min_odds,
        extra_cols=[],
    )
    print_weights_table(base_w, inter_w, extra_cols=[])

    baseline_train_roi = evaluate(df_tr, base_w, inter_w, "upside_win_roi", min_odds)
    baseline_test_roi = evaluate(df_te, base_w, inter_w, "upside_win_roi", min_odds)
    logger.info(
        f"  ベースライン: train_roi={baseline_train_roi:.1f}%  test_roi={baseline_test_roi:.1f}%"
    )
    return base_w, inter_w, baseline_train_roi, baseline_test_roi


def run_single_validation(
    index_name: str,
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    engine,
    min_odds: float,
    l2: float,
    folds: int,
    baseline_train_roi: float = 0.0,
    baseline_test_roi: float = 0.0,
) -> dict:
    """1つの追加指数の検証を実行する。

    Returns:
        dict: {
            "index_name", "label",
            "test_upside_win_roi_diff", "train_upside_win_roi_diff",
            "overfit_flag", "extra_weight",
            "elapsed_sec"
        }
    """
    label = EXTRA_INDEX_LABELS.get(index_name, index_name)
    compute_fn = EXTRA_INDEX_REGISTRY.get(index_name)
    if compute_fn is None:
        logger.error(f"未知の指数: {index_name}")
        return {"index_name": index_name, "label": label, "error": "未知の指数"}

    t_start = time.time()
    logger.info(f"\n{'=' * 60}")
    logger.info(f"  [{index_name}] {label} 検証開始")
    logger.info(f"{'=' * 60}")

    # 追加指数の計算
    logger.info(f"  訓練データの {label} を計算中...")
    col_name = f"{index_name}_x"  # 既存列との衝突を避けるためサフィックス付き
    train_extra = compute_fn(df_train, engine)  # type: ignore[call-arg]
    train_extra.name = col_name
    df_tr = df_train.copy()
    df_tr[col_name] = train_extra.values

    logger.info(f"  テストデータの {label} を計算中...")
    test_extra = compute_fn(df_test, engine)  # type: ignore[call-arg]
    test_extra.name = col_name
    df_te = df_test.copy()
    df_te[col_name] = test_extra.values

    # 統計情報
    non_neutral_tr = (df_tr[col_name] != 50.0).mean()
    logger.info(
        f"  訓練: 非中立値率={non_neutral_tr:.1%}, "
        f"mean={df_tr[col_name].mean():.1f}, std={df_tr[col_name].std():.1f}"
    )
    if non_neutral_tr < 0.1:
        logger.warning(f"  警告: {label} の有効データが少ない（{non_neutral_tr:.1%}）→ スキップ")
        return {
            "index_name": index_name,
            "label": label,
            "test_upside_win_roi_diff": None,
            "train_upside_win_roi_diff": None,
            "overfit_flag": None,
            "extra_weight": None,
            "elapsed_sec": round(time.time() - t_start, 1),
            "note": "有効データ不足",
        }

    # 交互作用項なし（新指数単体の効果を純粋に測定）
    df_tr = add_interaction_features(df_tr, [])
    df_te = add_interaction_features(df_te, [])

    # 最適化（追加指数 col_name を base_cols に追加）
    base_w, inter_w = nelder_mead_optimize(
        df_tr,
        inter_names=[],
        objective="upside_win_roi",
        n_folds=folds,
        l2_lambda=l2,
        odds_threshold=min_odds,
        extra_cols=[col_name],
    )

    print_weights_table(base_w, inter_w, extra_cols=[col_name])

    elapsed = round(time.time() - t_start, 1)
    extra_w = base_w.get(col_name, 0.0)

    # ベースライン比較（正しい差分: 5指数再最適化との比較）
    new_train_roi = round(evaluate(df_tr, base_w, inter_w, "upside_win_roi", min_odds), 1)
    new_test_roi = round(evaluate(df_te, base_w, inter_w, "upside_win_roi", min_odds), 1)
    train_diff = round(new_train_roi - baseline_train_roi, 1)
    test_diff = round(new_test_roi - baseline_test_roi, 1)
    overfit_flag = (new_test_roi < new_train_roi * 0.90) if new_train_roi > 0 else False

    print(f"\n  指標                   ベースライン        新         差")
    print(f"  {'穴馬単勝ROI (train)':<22} {baseline_train_roi:>7.1f}%  {new_train_roi:>7.1f}%  {train_diff:>+7.1f}%")
    print(f"  {'穴馬単勝ROI (test)':<22} {baseline_test_roi:>7.1f}%  {new_test_roi:>7.1f}%  {test_diff:>+7.1f}%")
    print(f"  過学習フラグ: {'あり' if overfit_flag else 'なし'}")

    logger.info(
        f"\n  [{label}] "
        f"train_diff={train_diff:+.1f}%  "
        f"test_diff={test_diff:+.1f}%  "
        f"overfit={overfit_flag}  "
        f"extra_w={extra_w:.1%}  "
        f"elapsed={elapsed}s"
    )

    return {
        "index_name": index_name,
        "label": label,
        "test_upside_win_roi_diff": test_diff,
        "train_upside_win_roi_diff": train_diff,
        "test_upside_win_roi_baseline": baseline_test_roi,
        "test_upside_win_roi_new": new_test_roi,
        "overfit_flag": overfit_flag,
        "extra_weight": round(extra_w, 4),
        "elapsed_sec": elapsed,
        "note": "",
    }


def print_summary(results: list[dict]) -> None:
    """全検証結果のサマリーを表示する。"""
    print(f"\n{'=' * 70}")
    print("  === 追加指数 検証結果サマリー ===")
    print(f"{'=' * 70}")
    print(f"\n  {'指数名':<14} {'label':<12} {'train差':>8} {'test差':>8} {'過学習':>6} {'採用weight':>10}  判定")
    print("  " + "-" * 68)

    for r in sorted(results, key=lambda x: x.get("test_upside_win_roi_diff") or -99, reverse=True):
        if "error" in r or r.get("test_upside_win_roi_diff") is None:
            print(f"  {r['index_name']:<14} {r['label']:<12} {'N/A':>8} {'N/A':>8} {'N/A':>6} {'N/A':>10}  {r.get('note', r.get('error', ''))}")
            continue
        diff = r["test_upside_win_roi_diff"]
        train_diff = r.get("train_upside_win_roi_diff", 0.0)
        overfit = r["overfit_flag"]
        ew = r["extra_weight"]
        if diff is not None and diff > 0 and not overfit:
            judgment = "★ 採用候補"
        elif diff is not None and diff > 0:
            judgment = "△ 過学習あり"
        else:
            judgment = "却下"
        print(
            f"  {r['index_name']:<14} {r['label']:<12}"
            f" {('+' if train_diff and train_diff >= 0 else '')}{train_diff:>7.1f}%"
            f" {('+' if diff >= 0 else '')}{diff:>7.1f}%"
            f" {'あり' if overfit else 'なし':>6}"
            f" {ew:>10.1%}  {judgment}"
        )
    print()


def main() -> None:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="地方競馬 追加指数検証スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--train", default=DEFAULT_TRAIN, help="訓練期間 YYYYMMDD-YYYYMMDD")
    parser.add_argument("--test", default=DEFAULT_TEST, help="テスト期間 YYYYMMDD-YYYYMMDD")
    parser.add_argument("--version", type=int, default=CHIHOU_COMPOSITE_VERSION, help="calculated_indices バージョン")
    parser.add_argument("--min-odds", type=float, default=DEFAULT_MIN_ODDS, help="穴馬判定オッズ閾値")
    parser.add_argument("--l2", type=float, default=DEFAULT_L2, help="L2 正則化係数")
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="CV フォールド数")
    parser.add_argument(
        "--indices",
        default=None,
        help=f"検証する指数（カンマ区切り）。省略時は全9指数。利用可能: {','.join(EXTRA_INDEX_REGISTRY.keys())}",
    )
    parser.add_argument(
        "--index",
        default=None,
        help="単一指数の検証",
    )
    parser.add_argument(
        "--out",
        default="/tmp/chihou_extra_index_validation.json",
        help="結果 JSON 出力パス",
    )
    args = parser.parse_args()

    # 検証対象の指数リスト
    if args.index:
        target_indices = [args.index.strip()]
    elif args.indices:
        target_indices = [s.strip() for s in args.indices.split(",")]
    else:
        target_indices = list(EXTRA_INDEX_REGISTRY.keys())

    logger.info(f"検証対象: {target_indices}")
    logger.info(f"設定: train={args.train}, test={args.test}, v={args.version}, min_odds={args.min_odds}, l2={args.l2}")

    # データを1回だけ読み込む
    df_train, df_test = load_base_data(args.train, args.test, args.version)

    # SQLAlchemy engine（extra indices計算用）
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

    # ベースライン（5指数のみ再最適化）を事前に計算
    # 各extra指数の diff はこのベースラインとの比較で計算する（CURRENT_BASE_WEIGHTS固定との比較ではない）
    logger.info("\nベースライン最適化を実行中...")
    _, _, baseline_train_roi, baseline_test_roi = run_baseline_optimization(
        df_train, df_test,
        min_odds=args.min_odds,
        l2=args.l2,
        folds=args.folds,
    )
    logger.info(f"ベースライン確定: train={baseline_train_roi:.1f}%  test={baseline_test_roi:.1f}%")

    all_results = []
    t_total = time.time()

    for i, idx_name in enumerate(target_indices, 1):
        logger.info(f"\n[{i}/{len(target_indices)}] {idx_name} 検証中...")
        result = run_single_validation(
            idx_name, df_train, df_test, engine,
            min_odds=args.min_odds,
            l2=args.l2,
            folds=args.folds,
            baseline_train_roi=baseline_train_roi,
            baseline_test_roi=baseline_test_roi,
        )
        all_results.append(result)
        logger.info(f"  完了 ({result.get('elapsed_sec', 0):.0f}s)")

    total_elapsed = round(time.time() - t_total, 1)
    logger.info(f"\n全{len(target_indices)}指数の検証完了（合計 {total_elapsed:.0f}s）")

    print_summary(all_results)

    # JSON 保存
    output = {
        "meta": {
            "train": args.train,
            "train": args.train,
            "test": args.test,
            "version": args.version,
            "min_odds": args.min_odds,
            "l2": args.l2,
            "baseline_train_roi": baseline_train_roi,
            "baseline_test_roi": baseline_test_roi,
            "total_elapsed_sec": total_elapsed,
        },
        "results": all_results,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2))
    logger.info(f"結果を保存: {args.out}")


if __name__ == "__main__":
    main()
