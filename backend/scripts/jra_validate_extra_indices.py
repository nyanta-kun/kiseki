"""JRA 追加指数検証スクリプト

既存12指数に新規指数を1つ加えた場合のROI改善を測定する。
指数ごとにグリッドサーチ（α）を実行し、全指数の結果を比較表示する。

検証対象指数（6種）:
  - rivals_growth_index      : 上昇相手指数（DB計算済み）
  - career_phase_index       : 成長曲線指数（DB計算済み）
  - distance_change_index    : 距離変更適性指数（DB計算済み）
  - jockey_trainer_combo_index: 騎手×厩舎コンビ指数（DB計算済み）
  - going_pedigree_index     : 重馬場×血統指数（DB計算済み）
  - last_margin              : 前走着差指数（raw dataから算出）

使い方:
  uv run python scripts/jra_validate_extra_indices.py
  uv run python scripts/jra_validate_extra_indices.py --index last_margin
  uv run python scripts/jra_validate_extra_indices.py \\
      --train 20230101-20251231 --test 20260101-20260415 \\
      --min-odds 10.0 --l2 0.5

設計（chihou_validate_extra_indices.py と同様）:
  - baseline最適化: Nelder-Mead で12指数のみ最適化しベースラインROIを取得
  - グリッドサーチ: ベース12指数ウェイト固定 + extra col α をCV選択
  - objective: upside_place_roi（place_odds不足時は upside_win_roi にフォールバック）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from bisect import bisect_left
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import create_engine

from src.config import settings
from src.indices.composite import COMPOSITE_VERSION

sys.path.insert(0, str(_here.parent))
from analyst_agent import (
    UPSIDE_ODDS_THRESHOLD,
    filter_valid,
    add_ranks,
)
from feature_engineer import (
    INDEX_COLS,
    BASE_BUDGET,
    CURRENT_BASE_WEIGHTS,
    add_interaction_features,
    evaluate,
    nelder_mead_optimize,
    print_weights_table,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_validate_extra_indices")

# ---------------------------------------------------------------------------
# デフォルトパラメータ（v17採用時と同じ設定）
# ---------------------------------------------------------------------------

DEFAULT_TRAIN = "20230101-20251231"
DEFAULT_TEST = "20260101-20260415"
DEFAULT_MIN_ODDS = UPSIDE_ODDS_THRESHOLD  # 10.0
DEFAULT_L2 = 0.5
DEFAULT_FOLDS = 5
DEFAULT_OBJECTIVE = "upside_place_roi"

# ---------------------------------------------------------------------------
# 追加指数定義
# ---------------------------------------------------------------------------

# DB計算済み指数（load_extended_data でロード済み）
DB_EXTRA_COLS = [
    "rivals_growth_index",
    "career_phase_index",
    "distance_change_index",
    "jockey_trainer_combo_index",
    "going_pedigree_index",
]

EXTRA_INDEX_LABELS: dict[str, str] = {
    "rivals_growth_index":        "上昇相手指数",
    "career_phase_index":         "成長曲線指数",
    "distance_change_index":      "距離変更適性",
    "jockey_trainer_combo_index": "騎手×厩舎コンビ",
    "going_pedigree_index":       "重馬場×血統",
    "last_margin":                "前走着差",
}


# ---------------------------------------------------------------------------
# 拡張データロード（extra DB指数を追加取得）
# ---------------------------------------------------------------------------


def load_extended_data(
    start_date: str,
    end_date: str,
    version: int = COMPOSITE_VERSION,
) -> pd.DataFrame:
    """DB計算済みの追加指数5列を含む拡張データを取得する。

    analyst_agent.load_data の SQL を拡張し、
    rivals_growth_index / career_phase_index / distance_change_index /
    jockey_trainer_combo_index / going_pedigree_index を追加取得する。
    """
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    sql = text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course          AS course,
    r.course_name     AS course_name,
    r.grade           AS grade,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    r.condition       AS condition,
    r.race_type_code  AS race_type_code,
    ci.horse_id       AS horse_id,
    ci.composite_index            AS composite_index,
    ci.speed_index                AS speed_index,
    ci.last_3f_index              AS last_3f_index,
    ci.course_aptitude            AS course_aptitude,
    ci.position_advantage         AS position_advantage,
    ci.jockey_index               AS jockey_index,
    ci.pace_index                 AS pace_index,
    ci.rotation_index             AS rotation_index,
    ci.pedigree_index             AS pedigree_index,
    ci.training_index             AS training_index,
    ci.anagusa_index              AS anagusa_index,
    ci.paddock_index              AS paddock_index,
    ci.rebound_index              AS rebound_index,
    ci.rivals_growth_index        AS rivals_growth_index,
    ci.career_phase_index         AS career_phase_index,
    ci.distance_change_index      AS distance_change_index,
    ci.jockey_trainer_combo_index AS jockey_trainer_combo_index,
    ci.going_pedigree_index       AS going_pedigree_index,
    ci.win_probability            AS win_probability,
    rr.finish_position            AS finish_position,
    rr.abnormality_code           AS abnormality_code,
    rr.win_odds                   AS win_odds,
    rr.win_popularity             AS win_popularity,
    rr.horse_number               AS horse_number
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :sd AND :ed
  AND ci.version = {version}
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
ORDER BY r.date, r.id, ci.horse_id
""")

    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    with engine.connect() as conn:
        result = conn.execute(sql, {"sd": sd, "ed": ed})
        rows = result.fetchall()
        columns = list(result.keys())

    df = pd.DataFrame(rows, columns=columns)
    logger.info(f"拡張データ取得: {len(df):,} 件 / {df['race_id'].nunique():,} レース")
    return df


# ---------------------------------------------------------------------------
# 前走着差指数（last_margin）の算出
# ---------------------------------------------------------------------------


def compute_last_margin(df: pd.DataFrame) -> pd.Series:
    """前走1着とのタイム差（time_diff）をスコア化する。

    小さいほど良い（接戦=高評価）。1着馬は time_diff=0。
    ルックアヘッドを避けるため target_date より前の直近レースを参照。
    JRAレース（course 01-10）のみ対象。
    """
    horse_ids = df["horse_id"].unique().tolist()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

    hist_sql = text("""
        SELECT rr.horse_id, r.date, rr.time_diff, rr.finish_position
        FROM keiba.race_results rr
        JOIN keiba.races r ON r.id = rr.race_id
        WHERE rr.horse_id = ANY(:hids)
          AND (rr.abnormality_code = 0 OR rr.abnormality_code IS NULL)
          AND rr.finish_position IS NOT NULL
          AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
        ORDER BY rr.horse_id, r.date
    """)

    with engine.connect() as conn:
        hist = pd.DataFrame(
            conn.execute(hist_sql, {"hids": horse_ids}).fetchall(),
            columns=["horse_id", "date", "time_diff", "finish_position"],
        )

    if hist.empty:
        return pd.Series(50.0, index=df.index, name="last_margin")

    hist["time_diff"] = pd.to_numeric(hist["time_diff"], errors="coerce")

    # horse_id → [(date, time_diff, finish_position), ...] (日付昇順)
    horse_hist: dict[int, list[tuple]] = {}
    for row in hist.itertuples(index=False):
        hid = int(row.horse_id)
        if hid not in horse_hist:
            horse_hist[hid] = []
        horse_hist[hid].append((str(row.date), row.time_diff, int(row.finish_position)))

    results = []
    for idx in df.index:
        horse_id = int(df.at[idx, "horse_id"])
        target_date = str(df.at[idx, "date"])
        races = horse_hist.get(horse_id, [])
        if not races:
            results.append(None)
            continue
        dates = [r[0] for r in races]
        pos = bisect_left(dates, target_date)
        if pos == 0:
            results.append(None)
            continue
        prev = races[pos - 1]
        if prev[2] == 1:
            results.append(0.0)
        elif pd.notna(prev[1]):
            results.append(float(prev[1]))
        else:
            results.append(None)

    raw = pd.Series(results, index=df.index, dtype="float64")
    # time_diff 小=良 → 符号反転して z-score 正規化
    median_val = float(raw.median()) if raw.notna().any() else 3.0
    neg = -raw.fillna(median_val)

    s = neg.dropna()
    if len(s) < 2:
        return pd.Series(50.0, index=df.index, name="last_margin")
    mu = float(s.mean())
    sigma = float(s.std())
    if sigma < 1e-9:
        return pd.Series(50.0, index=df.index, name="last_margin")

    result = (neg - mu) / sigma * 10.0 + 50.0
    return result.clip(0.0, 100.0).fillna(50.0).rename("last_margin")


# ---------------------------------------------------------------------------
# ベースライン最適化
# ---------------------------------------------------------------------------


def run_baseline_optimization(
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    min_odds: float,
    l2: float,
    folds: int,
    objective: str,
) -> tuple[dict[str, float], dict[str, float], float, float]:
    """12指数のみで再最適化してベースラインROIを計算する。

    Returns:
        (base_w, inter_w, baseline_train_roi, baseline_test_roi)
    """
    logger.info("\n" + "=" * 60)
    logger.info("  [ベースライン] 12指数のみ再最適化")
    logger.info("=" * 60)

    df_tr = add_interaction_features(df_train.copy(), [])
    df_te = add_interaction_features(df_test.copy(), [])

    base_w, inter_w = nelder_mead_optimize(
        df_tr,
        inter_names=[],
        objective=objective,
        n_folds=folds,
        l2_lambda=l2,
        odds_threshold=min_odds,
    )
    print_weights_table(base_w, inter_w)

    baseline_train_roi = evaluate(df_tr, base_w, inter_w, objective, min_odds)
    baseline_test_roi = evaluate(df_te, base_w, inter_w, objective, min_odds)
    logger.info(
        f"  ベースライン: train_roi={baseline_train_roi:.1f}%  test_roi={baseline_test_roi:.1f}%"
    )
    return base_w, inter_w, baseline_train_roi, baseline_test_roi


# ---------------------------------------------------------------------------
# 単一指数の検証
# ---------------------------------------------------------------------------


def run_single_validation(
    index_name: str,
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    min_odds: float,
    l2: float,
    folds: int,
    objective: str,
    baseline_base_w: dict[str, float],
    baseline_inter_w: dict[str, float],
    baseline_train_roi: float,
    baseline_test_roi: float,
) -> dict:
    """1つの追加指数の検証を実行する（グリッドサーチ）。

    アプローチ:
    - ベース12指数は baseline_base_w で固定（Softmax 予算を共有しない）
    - extra col の加算ウェイト α をグリッドサーチして CV 選択
    - 過学習判定: test_roi < train_roi × 0.90

    Returns:
        dict: 検証結果
    """
    label = EXTRA_INDEX_LABELS.get(index_name, index_name)
    t_start = time.time()

    logger.info(f"\n{'=' * 60}")
    logger.info(f"  [{index_name}] {label} 検証開始（グリッドサーチ）")
    logger.info(f"{'=' * 60}")

    col_name = f"{index_name}_x"

    # 指数の取得（DB列 or 算出）
    df_tr = add_interaction_features(df_train.copy(), [])
    df_te = add_interaction_features(df_test.copy(), [])

    if index_name in DB_EXTRA_COLS:
        # DB計算済み列はそのまま使う
        if index_name not in df_train.columns:
            logger.error(f"列 {index_name} がデータにありません")
            return {"index_name": index_name, "label": label, "error": "列なし"}
        df_tr[col_name] = df_train[index_name].fillna(50.0).values
        df_te[col_name] = df_test[index_name].fillna(50.0).values
    elif index_name == "last_margin":
        # raw dataから算出（ルックアヘッド防止済み）
        logger.info("  訓練データの前走着差を計算中...")
        df_tr[col_name] = compute_last_margin(df_train).values
        logger.info("  テストデータの前走着差を計算中...")
        df_te[col_name] = compute_last_margin(df_test).values
    else:
        logger.error(f"未知の指数: {index_name}")
        return {"index_name": index_name, "label": label, "error": "未知の指数"}

    # 有効データ率チェック
    non_neutral_tr = (df_tr[col_name] != 50.0).mean()
    logger.info(
        f"  訓練: 非中立値率={non_neutral_tr:.1%}, "
        f"mean={df_tr[col_name].mean():.1f}, std={df_tr[col_name].std():.1f}"
    )
    if non_neutral_tr < 0.05:
        logger.warning(f"  警告: {label} の有効データが少ない（{non_neutral_tr:.1%}）→ スキップ")
        return {
            "index_name": index_name,
            "label": label,
            "test_roi_diff": None,
            "train_roi_diff": None,
            "overfit_flag": None,
            "extra_weight": None,
            "elapsed_sec": round(time.time() - t_start, 1),
            "note": "有効データ不足",
        }

    # グリッドサーチ: α を CV で最適化
    race_ids = df_tr["race_id"].unique()
    np.random.seed(42)
    np.random.shuffle(race_ids)
    cv_folds = np.array_split(race_ids, folds)

    alpha_grid = [0.0, 0.01, 0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]
    best_cv_roi = -float("inf")
    best_alpha = 0.0
    cv_results = []

    for alpha in alpha_grid:
        combined_w = dict(baseline_base_w)
        combined_w[col_name] = alpha

        fold_rois = []
        for fold_ids in cv_folds:
            val = df_tr[df_tr["race_id"].isin(fold_ids)]
            if len(val) == 0:
                continue
            fold_rois.append(evaluate(val, combined_w, {}, objective, min_odds))

        cv_roi = float(np.mean(fold_rois)) if fold_rois else 0.0
        cv_results.append((alpha, cv_roi))
        if cv_roi > best_cv_roi:
            best_cv_roi = cv_roi
            best_alpha = alpha

    logger.info(f"  グリッドサーチ結果: best_alpha={best_alpha:.2f}, best_cv_roi={best_cv_roi:.1f}%")
    logger.info("  α別 CV ROI: " + "  ".join(f"α={a:.2f}:{r:.1f}%" for a, r in cv_results))

    # ベスト α でフルセット評価
    combined_best = dict(baseline_base_w)
    combined_best[col_name] = best_alpha

    new_train_roi = round(evaluate(df_tr, combined_best, {}, objective, min_odds), 1)
    new_test_roi = round(evaluate(df_te, combined_best, {}, objective, min_odds), 1)
    train_diff = round(new_train_roi - baseline_train_roi, 1)
    test_diff = round(new_test_roi - baseline_test_roi, 1)
    overfit_flag = (new_test_roi < new_train_roi * 0.90) if new_train_roi > 0 else False

    elapsed = round(time.time() - t_start, 1)

    print(f"\n  グリッドサーチ α別 CV ROI:")
    for alpha, cv_roi in cv_results:
        marker = " ← best" if alpha == best_alpha else ""
        print(f"    α={alpha:.2f}: CV={cv_roi:.1f}%{marker}")
    print(f"\n  指標                   ベースライン      ベストα={best_alpha:.2f}      差")
    print(f"  {'ROI (train)':<22} {baseline_train_roi:>7.1f}%  {new_train_roi:>7.1f}%  {train_diff:>+7.1f}%")
    print(f"  {'ROI (test)':<22} {baseline_test_roi:>7.1f}%  {new_test_roi:>7.1f}%  {test_diff:>+7.1f}%")
    print(f"  過学習フラグ: {'あり' if overfit_flag else 'なし'}")

    logger.info(
        f"\n  [{label}] "
        f"best_alpha={best_alpha:.2f}  "
        f"train_diff={train_diff:+.1f}%  "
        f"test_diff={test_diff:+.1f}%  "
        f"overfit={overfit_flag}  "
        f"elapsed={elapsed}s"
    )

    return {
        "index_name": index_name,
        "label": label,
        "test_roi_diff": test_diff,
        "train_roi_diff": train_diff,
        "test_roi_baseline": baseline_test_roi,
        "test_roi_new": new_test_roi,
        "overfit_flag": overfit_flag,
        "extra_weight": round(best_alpha, 4),
        "cv_roi_by_alpha": {str(a): round(r, 1) for a, r in cv_results},
        "elapsed_sec": elapsed,
        "note": "",
    }


# ---------------------------------------------------------------------------
# サマリー表示
# ---------------------------------------------------------------------------


def print_summary(results: list[dict], objective: str) -> None:
    """全検証結果のサマリーを表示する。"""
    print(f"\n{'=' * 72}")
    print(f"  === JRA 追加指数 検証結果サマリー (objective={objective}) ===")
    print(f"{'=' * 72}")
    print(f"\n  {'指数名':<26} {'label':<12} {'train差':>8} {'test差':>8} {'過学習':>6} {'best_α':>8}  判定")
    print("  " + "-" * 70)

    for r in sorted(results, key=lambda x: x.get("test_roi_diff") or -99, reverse=True):
        if "error" in r or r.get("test_roi_diff") is None:
            print(f"  {r['index_name']:<26} {r['label']:<12} {'N/A':>8} {'N/A':>8} {'N/A':>6} {'N/A':>8}  {r.get('note', r.get('error', ''))}")
            continue
        diff = r["test_roi_diff"]
        train_diff = r.get("train_roi_diff", 0.0)
        overfit = r["overfit_flag"]
        ew = r["extra_weight"]
        if diff is not None and diff > 0 and not overfit:
            judgment = "★ 採用候補"
        elif diff is not None and diff > 0:
            judgment = "△ 過学習あり"
        else:
            judgment = "却下"
        print(
            f"  {r['index_name']:<26} {r['label']:<12}"
            f" {('+' if train_diff and train_diff >= 0 else '')}{train_diff:>7.1f}%"
            f" {('+' if diff >= 0 else '')}{diff:>7.1f}%"
            f" {'あり' if overfit else 'なし':>6}"
            f" {ew:>8.2f}  {judgment}"
        )
    print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI エントリポイント。"""
    all_extra = DB_EXTRA_COLS + ["last_margin"]

    parser = argparse.ArgumentParser(
        description="JRA 追加指数検証スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--train", default=DEFAULT_TRAIN, help="訓練期間 YYYYMMDD-YYYYMMDD")
    parser.add_argument("--test", default=DEFAULT_TEST, help="テスト期間 YYYYMMDD-YYYYMMDD")
    parser.add_argument("--version", type=int, default=COMPOSITE_VERSION, help="calculated_indices バージョン")
    parser.add_argument("--min-odds", type=float, default=DEFAULT_MIN_ODDS, help="穴馬判定オッズ閾値")
    parser.add_argument("--l2", type=float, default=DEFAULT_L2, help="L2 正則化係数")
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="CV フォールド数")
    parser.add_argument("--objective", default=DEFAULT_OBJECTIVE,
                        choices=["upside_place_roi", "upside_win_roi", "roi", "place_rate"],
                        help="最適化目標")
    parser.add_argument("--indices", default=None,
                        help=f"検証する指数（カンマ区切り）。省略時は全6指数。利用可能: {','.join(all_extra)}")
    parser.add_argument("--index", default=None, help="単一指数の検証")
    parser.add_argument("--out", default="/tmp/jra_extra_index_validation.json",
                        help="結果 JSON 出力パス")
    args = parser.parse_args()

    # 検証対象の指数リスト
    if args.index:
        target_indices = [args.index.strip()]
    elif args.indices:
        target_indices = [s.strip() for s in args.indices.split(",")]
    else:
        target_indices = all_extra

    logger.info(f"検証対象: {target_indices}")
    logger.info(
        f"設定: train={args.train}, test={args.test}, "
        f"v={args.version}, min_odds={args.min_odds}, "
        f"l2={args.l2}, folds={args.folds}, objective={args.objective}"
    )

    # データを1回だけ読み込む（拡張DB指数も含む）
    logger.info("訓練データ読み込み中...")
    train_start, train_end = args.train.split("-", 1)
    df_train_raw = load_extended_data(train_start, train_end, version=args.version)
    logger.info("テストデータ読み込み中...")
    test_start, test_end = args.test.split("-", 1)
    df_test_raw = load_extended_data(test_start, test_end, version=args.version)

    if df_train_raw.empty or df_test_raw.empty:
        raise RuntimeError("データなし。終了します。")

    df_train = filter_valid(df_train_raw)
    df_train = add_ranks(df_train)
    df_test = filter_valid(df_test_raw)
    df_test = add_ranks(df_test)

    logger.info(f"訓練: {len(df_train):,} 件 / {df_train['race_id'].nunique():,} レース")
    logger.info(f"テスト: {len(df_test):,} 件 / {df_test['race_id'].nunique():,} レース")

    # ベースライン（12指数のみ再最適化）
    logger.info("\nベースライン最適化を実行中...")
    baseline_base_w, baseline_inter_w, baseline_train_roi, baseline_test_roi = (
        run_baseline_optimization(
            df_train, df_test,
            min_odds=args.min_odds,
            l2=args.l2,
            folds=args.folds,
            objective=args.objective,
        )
    )
    logger.info(f"ベースライン確定: train={baseline_train_roi:.1f}%  test={baseline_test_roi:.1f}%")

    all_results = []
    t_total = time.time()

    for i, idx_name in enumerate(target_indices, 1):
        logger.info(f"\n[{i}/{len(target_indices)}] {idx_name} 検証中...")
        result = run_single_validation(
            idx_name, df_train, df_test,
            min_odds=args.min_odds,
            l2=args.l2,
            folds=args.folds,
            objective=args.objective,
            baseline_base_w=baseline_base_w,
            baseline_inter_w=baseline_inter_w,
            baseline_train_roi=baseline_train_roi,
            baseline_test_roi=baseline_test_roi,
        )
        all_results.append(result)
        logger.info(f"  完了 ({result.get('elapsed_sec', 0):.0f}s)")

    total_elapsed = round(time.time() - t_total, 1)
    logger.info(f"\n全{len(target_indices)}指数の検証完了（合計 {total_elapsed:.0f}s）")

    print_summary(all_results, args.objective)

    # JSON 保存
    output = {
        "meta": {
            "train": args.train,
            "test": args.test,
            "version": args.version,
            "min_odds": args.min_odds,
            "l2": args.l2,
            "folds": args.folds,
            "objective": args.objective,
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
