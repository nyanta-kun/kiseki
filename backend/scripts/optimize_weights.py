"""指数重み最適化スクリプト

算出済みの各単体指数（speed, last_3f, course_aptitude, ...）と
実際の着順を照合し、総合指数 composite_index の重みを最適化する。

手法:
  1. 訓練データでSpearman相関を算出 → 相関比例初期重みを設定
  2. scipy Nelder-Mead で単勝的中率を最大化
  3. テストデータで「現行重み」「相関比例重み」「最適化重み」を比較
  4. Markdownレポートを docs/verification/ へ出力

使い方:
  python scripts/optimize_weights.py --start 20250101 --end 20260322
  python scripts/optimize_weights.py --start 20250101 --end 20250831 --test-start 20250901 --test-end 20260322
  python scripts/optimize_weights.py --start 20250101 --end 20260322 --output docs/verification/
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sqlalchemy import text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.db.session import engine
from src.indices.composite import COMPOSITE_VERSION
from src.utils.constants import INDEX_WEIGHTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("optimize_weights")


# ---------------------------------------------------------------------------
# 最適化対象の指数カラム
# ---------------------------------------------------------------------------

# disadvantage_bonus は加算型（線形重みではない）ため最適化対象外
# 残り11指数の重みの合計 = 0.95
OPTIM_COLS = [
    "speed_index",
    "last_3f_index",
    "course_aptitude",
    "position_advantage",
    "jockey_index",
    "pace_index",
    "pedigree_index",
    "rotation_index",
    "training_index",
    "anagusa_index",
    "paddock_index",
]

OPTIM_LABELS = {
    "speed_index": "スピード",
    "last_3f_index": "後3F",
    "course_aptitude": "コース適性",
    "position_advantage": "枠順",
    "jockey_index": "騎手",
    "pace_index": "展開",
    "pedigree_index": "血統",
    "rotation_index": "ローテ",
    "training_index": "調教",
    "anagusa_index": "穴ぐさ",
    "paddock_index": "パドック",
}

# disadvantage_bonus(0.05) を除いた最適化可能な重みの合計
OPTIMIZABLE_TOTAL = 1.0 - INDEX_WEIGHTS.get("disadvantage_bonus", 0.05)  # 0.95

# 現行重み（constants.py の INDEX_WEIGHTS から自動取得）
_KEY_MAP = {
    "speed_index": "speed",
    "last_3f_index": "last_3f",
    "course_aptitude": "course_aptitude",
    "position_advantage": "position_advantage",
    "jockey_index": "jockey_trainer",
    "pace_index": "pace",
    "pedigree_index": "pedigree",
    "rotation_index": "rotation",
    "training_index": "training",
    "anagusa_index": "anagusa",
    "paddock_index": "paddock",
}
CURRENT_WEIGHTS = {col: INDEX_WEIGHTS.get(_KEY_MAP[col], 0.0) for col in OPTIM_COLS}

NEUTRAL_VALUE = 50.0


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------


def _build_query(version: int) -> text:
    return text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    ci.horse_id       AS horse_id,
    ci.speed_index        AS speed_index,
    ci.last_3f_index      AS last_3f_index,
    ci.course_aptitude    AS course_aptitude,
    ci.position_advantage AS position_advantage,
    ci.jockey_index       AS jockey_index,
    ci.pace_index         AS pace_index,
    ci.pedigree_index     AS pedigree_index,
    ci.rotation_index     AS rotation_index,
    ci.training_index     AS training_index,
    ci.anagusa_index      AS anagusa_index,
    ci.paddock_index      AS paddock_index,
    rr.finish_position    AS finish_position,
    rr.abnormality_code   AS abnormality_code,
    rr.win_odds           AS win_odds
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND ci.version = {version}
  AND r.course_name ~ '^[^\x30-\x39]'
ORDER BY r.date, r.id, ci.horse_id
""")


def load_data(start_date: str, end_date: str, version: int = COMPOSITE_VERSION) -> pd.DataFrame:
    """指定期間のデータを取得する。"""
    with Session(engine) as db:
        result = db.execute(
            _build_query(version),
            {"start_date": start_date, "end_date": end_date},
        )
        rows = result.fetchall()
        cols = list(result.keys())

    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    for col in OPTIM_COLS + ["finish_position", "win_odds"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)

    # NaN を neutral 値で埋める（データ未取得のパドック等）
    for col in OPTIM_COLS:
        df[col] = df[col].fillna(NEUTRAL_VALUE)

    logger.info(
        f"{start_date}〜{end_date} 取得: {len(df):,} 件 / "
        f"レース数: {df['race_id'].nunique():,} (version={version})"
    )
    return df


def filter_valid(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """異常あり・着順なし・頭数不足のレースを除外する。"""
    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()
    counts = df.groupby("race_id")["horse_id"].count()
    valid = counts[counts >= min_runners].index
    return df[df["race_id"].isin(valid)].copy()


# ---------------------------------------------------------------------------
# 評価関数
# ---------------------------------------------------------------------------


def score_weights(df: pd.DataFrame, weights: dict[str, float]) -> dict:
    """重みセットを評価して指標を返す。"""
    df = df.copy()
    df["_composite"] = sum(df[col] * weights[col] for col in OPTIM_COLS)

    top1 = df.loc[df.groupby("race_id")["_composite"].idxmax()]
    n = len(top1)
    wins = (top1["finish_position"] == 1).sum()
    places = (top1["finish_position"] <= 3).sum()

    valid_odds = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    bets = len(valid_odds)
    payout = valid_odds.loc[valid_odds["finish_position"] == 1, "win_odds"].sum()
    roi = float(payout / bets * 100) if bets > 0 else 0.0

    return {
        "win_rate": float(wins / n) if n > 0 else 0.0,
        "place_rate": float(places / n) if n > 0 else 0.0,
        "roi_pct": round(roi, 1),
        "n_races": n,
    }


def spearman_per_index(df: pd.DataFrame) -> dict[str, float]:
    """各指数と着順のスピアマン相関（レース平均）を返す。"""
    result: dict[str, float] = {}
    for col in OPTIM_COLS:
        rhos = []
        for _, grp in df.groupby("race_id"):
            if len(grp) < 3:
                continue
            x = grp[col].to_numpy(float)
            y = grp["finish_position"].to_numpy(float)
            if np.any(np.isnan(x)) or np.any(np.isnan(y)):
                continue
            rx = x.argsort().argsort().astype(float)
            ry = y.argsort().argsort().astype(float)
            rho = float(np.corrcoef(rx, ry)[0, 1])
            if not np.isnan(rho):
                rhos.append(rho)
        result[col] = float(np.mean(rhos)) if rhos else 0.0
    return result


# ---------------------------------------------------------------------------
# 最適化
# ---------------------------------------------------------------------------


def correlation_weights(spearman: dict[str, float]) -> dict[str, float]:
    """スピアマン相関を正規化して相関比例重みを返す。

    各指数は「高いほど良い着順」設計なので着順との相関は負が正常。
    絶対値で重みを配分。正の相関（逆方向）は 0 に切り捨てる。
    """
    vals = np.array([max(0.0, -spearman[col]) for col in OPTIM_COLS])
    total = vals.sum()
    if total < 1e-9:
        vals = np.ones(len(OPTIM_COLS))
        total = vals.sum()
    normed = vals / total * OPTIMIZABLE_TOTAL
    return {col: float(w) for col, w in zip(OPTIM_COLS, normed)}


def optimize(df_train: pd.DataFrame, init_weights: dict[str, float]) -> dict[str, float]:
    """Nelder-Mead で単勝的中率を最大化する重みを求める。

    softmax 変換により「合計=OPTIMIZABLE_TOTAL・各重み>0」を暗黙的に保証。
    """
    init_vals = np.array([init_weights[col] for col in OPTIM_COLS])
    x0 = np.log(init_vals + 1e-9)

    def objective(x: np.ndarray) -> float:
        exp_x = np.exp(x - x.max())
        w = exp_x / exp_x.sum() * OPTIMIZABLE_TOTAL
        weights = {col: float(wi) for col, wi in zip(OPTIM_COLS, w)}
        return -score_weights(df_train, weights)["win_rate"]

    res = minimize(
        objective,
        x0,
        method="Nelder-Mead",
        options={"maxiter": 5000, "xatol": 1e-5, "fatol": 1e-5},
    )
    exp_x = np.exp(res.x - res.x.max())
    w = exp_x / exp_x.sum() * OPTIMIZABLE_TOTAL
    return {col: round(float(wi), 4) for col, wi in zip(OPTIM_COLS, w)}


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------


def build_report(
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    spearman: dict[str, float],
    weight_sets: dict[str, dict[str, float]],
    train_results: dict[str, dict],
    eval_results: dict[str, dict],
    version: int,
) -> str:
    from datetime import date as _date

    today = _date.today().strftime("%Y-%m-%d")

    lines = [
        "# INDEX_WEIGHTS 最適化レポート",
        "",
        f"**実行日**: {today}  ",
        f"**算出バージョン**: v{version}  ",
        f"**訓練データ**: {train_start} 〜 {train_end}  ",
        f"**テストデータ**: {test_start} 〜 {test_end}  ",
        "",
        "---",
        "",
        "## 1. 各指数のスピアマン相関（訓練データ）",
        "",
        "> ρ < 0: 高指数 → 良い着順（正常）  ρ > 0: 逆相関（要確認）",
        "",
        "| 指数 | スピアマン相関 ρ | 傾向 | 現行重み |",
        "|------|----------------|------|---------|",
    ]
    for col in sorted(OPTIM_COLS, key=lambda c: spearman[c]):
        rho = spearman[col]
        trend = "↑ 予測力あり" if rho < -0.05 else ("→ 中立" if rho < 0.05 else "↓ 逆相関")
        cur_w = CURRENT_WEIGHTS.get(col, 0.0)
        lines.append(f"| {OPTIM_LABELS[col]} | {rho:+.4f} | {trend} | {cur_w:.3f} |")

    lines += [
        "",
        "---",
        "",
        "## 2. 重みセット比較",
        "",
        "| 指数 | 現行重み | 相関比例 | 最適化 | 変化 |",
        "|------|---------|---------|--------|------|",
    ]
    for col in OPTIM_COLS:
        cur = weight_sets["現行"].get(col, 0.0)
        corr = weight_sets["相関比例"].get(col, 0.0)
        opt = weight_sets["最適化"].get(col, 0.0)
        delta = opt - cur
        arrow = "▲" if delta > 0.005 else ("▽" if delta < -0.005 else "─")
        lines.append(
            f"| {OPTIM_LABELS[col]} | {cur:.3f} | {corr:.3f} | **{opt:.3f}** "
            f"| {arrow}{abs(delta):.3f} |"
        )
    lines.append(
        f"| **合計** | **{sum(weight_sets['現行'].values()):.3f}** "
        f"| **{sum(weight_sets['相関比例'].values()):.3f}** "
        f"| **{sum(weight_sets['最適化'].values()):.3f}** | |"
    )

    lines += [
        "",
        "---",
        "",
        "## 3. 訓練データでの評価",
        "",
        "| 重みセット | 単勝的中率 | 複勝的中率 | ROI | レース数 |",
        "|-----------|---------|---------|-----|---------|",
    ]
    for label, r in train_results.items():
        lines.append(
            f"| {label} | {r['win_rate']:.1%} | {r['place_rate']:.1%} "
            f"| {r['roi_pct']}% | {r['n_races']:,} |"
        )

    best_label = max(eval_results, key=lambda k: eval_results[k]["win_rate"])
    lines += [
        "",
        "## 4. テストデータでの評価（汎化性能）",
        "",
        "| 重みセット | 単勝的中率 | 複勝的中率 | ROI | レース数 |",
        "|-----------|---------|---------|-----|---------|",
    ]
    for label, r in eval_results.items():
        marker = " ✅" if label == best_label else ""
        lines.append(
            f"| {label}{marker} | **{r['win_rate']:.1%}** | {r['place_rate']:.1%} "
            f"| {r['roi_pct']}% | {r['n_races']:,} |"
        )

    best_w = weight_sets["最適化"]
    lines += [
        "",
        "---",
        "",
        "## 5. 推奨重み（constants.py への反映案）",
        "",
        "```python",
        "INDEX_WEIGHTS = {",
    ]
    for col in OPTIM_COLS:
        k = _KEY_MAP[col]
        cur = CURRENT_WEIGHTS.get(col, 0.0)
        lines.append(f'    "{k}": {best_w[col]:.4f},  # {OPTIM_LABELS[col]} (現行: {cur:.3f})')
    lines += [
        '    "disadvantage_bonus": 0.05,  # 加算型・固定',
        "}",
        "```",
        "",
        "> **注意**: 過学習に注意。テストデータで現行重みを上回る場合のみ採用を推奨。",
        "",
        "---",
        "*Generated by kiseki/backend/scripts/optimize_weights.py*",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def run(
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    output_dir: Path,
    version: int = COMPOSITE_VERSION,
) -> None:
    logger.info(f"=== 重み最適化 v{version} ===")
    logger.info(f"訓練: {train_start}〜{train_end} / テスト: {test_start}〜{test_end}")

    df_train_raw = load_data(train_start, train_end, version)
    df_test_raw = load_data(test_start, test_end, version)

    if df_train_raw.empty or df_test_raw.empty:
        logger.error("データが取得できませんでした。")
        return

    df_train = filter_valid(df_train_raw)
    df_test = filter_valid(df_test_raw)
    logger.info(
        f"訓練: {df_train['race_id'].nunique()} レース / "
        f"テスト: {df_test['race_id'].nunique()} レース"
    )

    if df_train.empty or df_test.empty:
        logger.error("有効なレースがありません。")
        return

    # Step 1: スピアマン相関
    logger.info("スピアマン相関を算出中...")
    spearman = spearman_per_index(df_train)
    for col, rho in sorted(spearman.items(), key=lambda x: x[1]):
        logger.info(f"  {OPTIM_LABELS[col]:<12}: ρ = {rho:+.4f}")

    # Step 2: 相関比例重み
    corr_weights = correlation_weights(spearman)

    # Step 3: scipy 最適化
    logger.info("Nelder-Mead 最適化中...")
    opt_weights = optimize(df_train, corr_weights)

    weight_sets = {
        "現行": CURRENT_WEIGHTS,
        "相関比例": corr_weights,
        "最適化": opt_weights,
    }

    # 評価
    logger.info("\n--- 訓練データ評価 ---")
    train_results: dict[str, dict] = {}
    for label, w in weight_sets.items():
        s = score_weights(df_train, w)
        train_results[label] = s
        logger.info(
            f"  {label}: 単勝 {s['win_rate']:.1%}  複勝 {s['place_rate']:.1%}  ROI {s['roi_pct']}%"
        )

    logger.info("\n--- テストデータ評価 ---")
    eval_results: dict[str, dict] = {}
    for label, w in weight_sets.items():
        s = score_weights(df_test, w)
        eval_results[label] = s
        logger.info(
            f"  {label}: 単勝 {s['win_rate']:.1%}  複勝 {s['place_rate']:.1%}  ROI {s['roi_pct']}%"
        )

    # レポート出力
    report = build_report(
        train_start,
        train_end,
        test_start,
        test_end,
        spearman,
        weight_sets,
        train_results,
        eval_results,
        version,
    )
    from datetime import date as _date

    fname = f"{_date.today().strftime('%Y%m%d')}_{train_start}_{train_end}_weight_opt.md"
    report_path = output_dir / fname
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    logger.info(f"\nレポート出力: {report_path}")

    # サマリー表示
    print("\n" + "=" * 62)
    print(f"  推奨重み（最適化後）  ※ 合計={OPTIMIZABLE_TOTAL:.2f}")
    print("=" * 62)
    for col in OPTIM_COLS:
        cur = CURRENT_WEIGHTS.get(col, 0.0)
        opt = opt_weights[col]
        delta = opt - cur
        sign = "▲" if delta > 0.005 else ("▽" if delta < -0.005 else " ")
        print(f"  {OPTIM_LABELS[col]:<12}: {cur:.3f} → {opt:.3f}  {sign}{abs(delta):.3f}")
    print("=" * 62)
    best_label = max(eval_results, key=lambda k: eval_results[k]["win_rate"])
    print(f"  テストデータ最良: {best_label}  単勝 {eval_results[best_label]['win_rate']:.1%}")
    print("=" * 62)


def main() -> None:
    parser = argparse.ArgumentParser(description="指数重み最適化")
    parser.add_argument("--start", required=True, help="訓練データ開始日 YYYYMMDD")
    parser.add_argument("--end", required=True, help="訓練データ終了日 YYYYMMDD")
    parser.add_argument(
        "--test-start",
        default=None,
        help="テストデータ開始日（省略時は --start と同じ）",
    )
    parser.add_argument(
        "--test-end",
        default=None,
        help="テストデータ終了日（省略時は --end と同じ）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="レポート出力先ディレクトリ（省略時は docs/verification/）",
    )
    parser.add_argument(
        "--version",
        type=int,
        default=COMPOSITE_VERSION,
        help=f"算出バージョン (default: {COMPOSITE_VERSION})",
    )
    args = parser.parse_args()

    test_start = args.test_start or args.start
    test_end = args.test_end or args.end
    output_dir = Path(args.output) if args.output else _root.parent / "docs" / "verification"

    run(args.start, args.end, test_start, test_end, output_dir, args.version)


if __name__ == "__main__":
    main()
