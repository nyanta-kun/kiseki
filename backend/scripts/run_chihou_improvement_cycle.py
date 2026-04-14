"""地方競馬 再帰的改善 Orchestrator — 穴馬ROI向上サイクル管理

改善ループを手動トリガーで実行し、バックテスト→分析→最適化→A/B比較→
Markdown レポート生成→ユーザー採用/却下入力→履歴保存 を一括実行する。

採用時は chihou_calculator.py の COMPOSITE_WEIGHTS とバージョンを自動更新し、
バックフィルコマンドを案内する。

使い方:
  # 基本実行（訓練2024-2025、テスト2026年）
  uv run python scripts/run_chihou_improvement_cycle.py \\
      --train 20240101-20251231 --test 20260101-20260413

  # 分析のみ（最適化スキップ）
  uv run python scripts/run_chihou_improvement_cycle.py \\
      --train 20240101-20251231 --test 20260101-20260413 \\
      --skip-optimize

  # 非対話モード（CI等での自動実行）
  uv run python scripts/run_chihou_improvement_cycle.py \\
      --train 20240101-20251231 --test 20260101-20260413 \\
      --non-interactive
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

sys.path.insert(0, str(_here.parent))
import chihou_analyst_agent as analyst
import chihou_feature_engineer as feat_eng

from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_chihou_improvement_cycle")

_WRITABLE_DIR = Path("/tmp/kiseki_chihou_improvement") if Path("/tmp").exists() else _here.parent
_WRITABLE_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = _WRITABLE_DIR / "chihou_improvement_log.json"

# chihou_calculator.py のパス（COMPOSITE_WEIGHTS と CHIHOU_COMPOSITE_VERSION を書き換える）
CHIHOU_CALC_PATH = _root / "src" / "indices" / "chihou_calculator.py"


# ---------------------------------------------------------------------------
# 改善履歴管理
# ---------------------------------------------------------------------------


def load_log() -> dict:
    """chihou_improvement_log.json を読み込む。"""
    if LOG_PATH.exists():
        return json.loads(LOG_PATH.read_text())
    return {"cycles": []}


def save_log(log: dict) -> None:
    """chihou_improvement_log.json に書き込む。"""
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2))
    logger.info(f"履歴保存: {LOG_PATH}")


def next_cycle_id(log: dict) -> int:
    """次のサイクルIDを返す。"""
    if not log["cycles"]:
        return 1
    return max(c["cycle_id"] for c in log["cycles"]) + 1


# ---------------------------------------------------------------------------
# chihou_calculator.py 書き換え
# ---------------------------------------------------------------------------


def update_chihou_calculator(
    new_weights: dict[str, float],
    new_version: int,
) -> None:
    """chihou_calculator.py の COMPOSITE_WEIGHTS と CHIHOU_COMPOSITE_VERSION を更新する。

    Args:
        new_weights: {weight_key: float} (speed/last3f/jockey/rotation/place_ev)
        new_version: 新しいバージョン番号
    """
    source = CHIHOU_CALC_PATH.read_text()

    # CHIHOU_COMPOSITE_VERSION を更新
    source = re.sub(
        r"^CHIHOU_COMPOSITE_VERSION\s*=\s*\d+",
        f"CHIHOU_COMPOSITE_VERSION = {new_version}",
        source,
        flags=re.MULTILINE,
    )

    # COMPOSITE_WEIGHTS を更新（place_ev は固定 0.25 で保持）
    speed    = new_weights.get("speed",    0.30)
    last3f   = new_weights.get("last3f",   0.20)
    jockey   = new_weights.get("jockey",   0.15)
    rotation = new_weights.get("rotation", 0.10)
    place_ev = new_weights.get("place_ev", 0.25)

    new_weights_block = (
        "COMPOSITE_WEIGHTS = {\n"
        f'    "speed":    {speed:.4f},\n'
        f'    "last3f":   {last3f:.4f},\n'
        f'    "jockey":   {jockey:.4f},\n'
        f'    "rotation": {rotation:.4f},\n'
        f'    "place_ev": {place_ev:.4f},  # 複勝期待値指数（v3で新設）\n'
        "}"
    )

    source = re.sub(
        r"COMPOSITE_WEIGHTS\s*=\s*\{[^}]+\}",
        new_weights_block,
        source,
        flags=re.DOTALL,
    )

    CHIHOU_CALC_PATH.write_text(source)
    logger.info(
        f"chihou_calculator.py を更新: version={new_version}, "
        f"speed={speed:.4f}, last3f={last3f:.4f}, "
        f"jockey={jockey:.4f}, rotation={rotation:.4f}, place_ev={place_ev:.4f}"
    )


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _parse_period(period_str: str) -> tuple[str, str]:
    """'YYYYMMDD-YYYYMMDD' を (start, end) に分割する。"""
    parts = period_str.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"期間フォーマットエラー: {period_str} (例: 20240101-20251231)")
    return parts[0], parts[1]


def _fmt_roi(val: float | None, suffix: str = "%") -> str:
    if val is None:
        return "N/A"
    return f"{val:.1f}{suffix}"


# ---------------------------------------------------------------------------
# ステップ実行関数
# ---------------------------------------------------------------------------


def step_baseline(
    df_train,
    df_test,
    odds_threshold: float = analyst.UPSIDE_ODDS_THRESHOLD,
) -> dict:
    """Step 1: 現行ベースライン計測。

    Returns:
        dict: train / test の各指標
    """
    logger.info("Step 1: ベースライン計測")

    def _metrics(df):
        std = analyst.compute_standard_metrics(df)
        up = analyst.compute_upside_roi(df, score_col="composite_index", odds_threshold=odds_threshold)
        return {**std, **{f"upside_{k}": v for k, v in up.items()}}

    return {
        "train": _metrics(df_train),
        "test": _metrics(df_test),
    }


def step_analyst(
    df_train,
    top_n: int,
    odds_threshold: float,
    inter_out_path: Path,
) -> dict:
    """Step 2: Analyst Agent — 穴馬パターン分析 + 交互作用項候補生成。

    Returns:
        dict: 分析結果（top_interactions を含む）
    """
    logger.info("Step 2: Analyst Agent 実行")
    df_ranked = analyst.add_individual_ranks(df_train)
    results = analyst.run_analysis(df_ranked, top_n=top_n, odds_threshold=odds_threshold)
    analyst.print_report(results, odds_threshold=odds_threshold)

    payload = {
        "meta": results["meta"],
        "top_interactions": results["top_interactions"],
        "baseline_upside_roi": results["baseline_upside_roi"],
        "baseline_standard": results["baseline_standard"],
    }
    inter_out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info(f"交互作用項候補を保存: {inter_out_path}")

    return results


def step_feature_engineer(
    df_train,
    df_test,
    interactions: list[dict],
    objective: str,
    l2_lambda: float,
    n_folds: int,
    odds_threshold: float,
) -> tuple[dict, dict, dict]:
    """Step 3: Feature Engineer — 最適ウェイト探索。

    Returns:
        (base_weights, inter_weights, eval_result)
    """
    logger.info("Step 3: Feature Engineer — ウェイト最適化")
    inter_names = [d["feature"] for d in interactions]

    df_tr = feat_eng.add_interaction_features(df_train, interactions)
    df_te = feat_eng.add_interaction_features(df_test, interactions)

    base_w, inter_w = feat_eng.nelder_mead_optimize(
        df_tr,
        inter_names=inter_names,
        objective=objective,
        n_folds=n_folds,
        l2_lambda=l2_lambda,
        odds_threshold=odds_threshold,
    )

    print(f"\n── ウェイト比較（ベース4指数 + 交互作用項{len(inter_w)}個）")
    feat_eng.print_weights_table(base_w, inter_w)

    eval_result = feat_eng.make_eval_table(df_tr, df_te, base_w, inter_w, objective, odds_threshold)

    print(f"\n── 評価指標比較（objective=★{objective}）")
    feat_eng.print_eval_table(eval_result, objective)

    return base_w, inter_w, eval_result


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------


def generate_report(
    cycle_id: int,
    train_period: str,
    test_period: str,
    baseline: dict,
    analyst_results: dict,
    base_w: dict,
    inter_w: dict,
    eval_result: dict,
    objective: str,
) -> str:
    """Markdown 形式の改善レポートを生成する。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    overfit = eval_result.get("overfit_flag", False)
    overfit_str = "⚠️ あり（リジェクト推奨）" if overfit else "なし"

    te_obj_new = eval_result.get(f"test_{objective}_new", 0.0)
    te_obj_cur = eval_result.get(f"test_{objective}_current", 0.0)
    te_obj_diff = eval_result.get(f"test_{objective}_diff", 0.0)
    diff_sign = "+" if te_obj_diff >= 0 else ""

    top_inter = sorted(inter_w.items(), key=lambda x: -x[1])[:5]
    inter_lines = "\n".join(
        f"  - `{feat}` : {w:.1%}" for feat, w in top_inter
    ) if top_inter else "  （なし）"

    # 新ウェイトをCOMPOSITE_WEIGHTS形式に変換
    col_to_key = feat_eng.COL_TO_WEIGHT_KEY
    new_weights_display = "\n".join(
        f"    \"{col_to_key.get(col, col)}\": {w:.4f},"
        for col, w in base_w.items()
    )

    report = textwrap.dedent(f"""\
    # 地方競馬 再帰的改善 Cycle #{cycle_id}

    **実行日時**: {ts}
    **訓練期間**: {train_period}
    **テスト期間**: {test_period}
    **最適化目標**: `{objective}`

    ---

    ## ベースライン（現行 composite_index）

    | 指標 | 訓練 | テスト |
    |------|------|--------|
    | 1位 3着内率 | {baseline['train']['place_rate_pct']:.1f}% | {baseline['test']['place_rate_pct']:.1f}% |
    | 1位 単勝ROI | {baseline['train']['win_roi_pct']:.1f}% | {baseline['test']['win_roi_pct']:.1f}% |
    | 穴馬ヒット率 | {baseline['train']['upside_hit_rate']:.1%} | {baseline['test']['upside_hit_rate']:.1%} |
    | 穴馬 単勝ROI | {_fmt_roi(baseline['train']['upside_win_roi_pct'])} | {_fmt_roi(baseline['test']['upside_win_roi_pct'])} |

    ---

    ## 改善案

    ### 採用した交互作用項（上位5個）

    {inter_lines}

    ### 新ウェイト（COMPOSITE_WEIGHTS）

    ```python
    COMPOSITE_WEIGHTS = {{
    {new_weights_display}
    }}
    ```

    ### 評価指標比較（テスト期間）

    | 指標 | 現行 | 新ウェイト | 差分 |
    |------|------|----------|------|
    | 穴馬単勝ROI | {eval_result.get('test_upside_win_roi_current', 0):.1f}% | {eval_result.get('test_upside_win_roi_new', 0):.1f}% | {'+' if eval_result.get('test_upside_win_roi_diff', 0)>=0 else ''}{eval_result.get('test_upside_win_roi_diff', 0):.1f}% |
    | 1位 3着内率 | {eval_result.get('test_place_rate_current', 0):.1f}% | {eval_result.get('test_place_rate_new', 0):.1f}% | {'+' if eval_result.get('test_place_rate_diff', 0)>=0 else ''}{eval_result.get('test_place_rate_diff', 0):.1f}% |
    | 1位 単勝ROI | {eval_result.get('test_roi_current', 0):.1f}% | {eval_result.get('test_roi_new', 0):.1f}% | {'+' if eval_result.get('test_roi_diff', 0)>=0 else ''}{eval_result.get('test_roi_diff', 0):.1f}% |

    **主目標 ({objective}) テスト期間**: {te_obj_cur:.1f}% → {te_obj_new:.1f}% ({diff_sign}{te_obj_diff:.1f}%)

    ### 過学習フラグ: {overfit_str}

    ---

    ## 採用時の手順

    1. このレポートで「採用」を選択（スクリプトが chihou_calculator.py を自動更新）
    2. バックフィル再実行:
       ```bash
       cd backend
       uv run python scripts/chihou_backfill_indices.py --from-date 20240101
       ```
    3. バックフィル完了後、フロントエンドで地方指数を確認

    ## 判断

    - [ ] 採用: chihou_calculator.py の COMPOSITE_WEIGHTS + バージョンを自動更新
    - [ ] 却下: 次サイクルへ
    """)

    return report


# ---------------------------------------------------------------------------
# 停止条件チェック
# ---------------------------------------------------------------------------


def check_stopping_conditions(log: dict, objective: str) -> None:
    """連続改善なし・過学習継続の場合に警告を表示する。"""
    cycles = [c for c in log["cycles"] if c.get("objective") == objective]
    if len(cycles) < 3:
        return

    recent = cycles[-3:]
    no_improve = all(
        (c.get("result") or {}).get(f"test_{objective}_diff", 0) <= 0 for c in recent
    )
    if no_improve:
        print(
            f"\n⚠️  警告: 直近3サイクル連続で {objective} の改善なし。"
            f" 探索アプローチの見直しを検討してください。"
        )

    all_overfit = all((c.get("result") or {}).get("overfit_flag", False) for c in recent)
    if all_overfit:
        print(
            f"\n⚠️  警告: 直近3サイクル全て過学習フラグ。"
            f" L2正則化係数 (--l2) を増やすか、交互作用項数 (--top-n) を減らしてください。"
        )


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="地方競馬 再帰的改善 Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--train", default="20240101-20251231", help="訓練期間 (YYYYMMDD-YYYYMMDD)"
    )
    parser.add_argument(
        "--test", default="20260101-20261231", help="テスト期間 (YYYYMMDD-YYYYMMDD)"
    )
    parser.add_argument(
        "--objective",
        choices=["upside_win_roi", "upside_place_roi", "place_rate", "roi"],
        default="upside_win_roi",
        help="最適化目標（地方はplace_odds不足のためupside_win_roiを推奨）",
    )
    parser.add_argument("--top-n", type=int, default=6, help="交互作用項候補の上位件数")
    parser.add_argument("--min-odds", type=float, default=10.0, help="穴馬判定オッズ閾値")
    parser.add_argument("--l2", type=float, default=3.0, help="L2 正則化係数（推奨: 3.0）")
    parser.add_argument("--folds", type=int, default=3, help="CV フォールド数")
    parser.add_argument(
        "--version", type=int, default=CHIHOU_COMPOSITE_VERSION,
        help="calculated_indices バージョン"
    )
    parser.add_argument(
        "--skip-optimize", action="store_true", help="最適化をスキップ（分析のみ実行）"
    )
    parser.add_argument(
        "--non-interactive", action="store_true", help="非対話モード"
    )
    parser.add_argument(
        "--report-out", default=None, help="Markdown レポート出力パス"
    )
    args = parser.parse_args()

    train_start, train_end = _parse_period(args.train)
    test_start, test_end = _parse_period(args.test)
    inter_json = _WRITABLE_DIR / "chihou_interaction_candidates.json"

    log = load_log()
    cycle_id = next_cycle_id(log)

    print("=" * 68)
    print(f"  地方競馬 再帰的改善 Cycle #{cycle_id}")
    print(f"  訓練: {train_start} 〜 {train_end}")
    print(f"  テスト: {test_start} 〜 {test_end}")
    print(f"  目標: {args.objective}")
    print("=" * 68)

    check_stopping_conditions(log, args.objective)

    # ── Step 1: データロード ──────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Step 1: データロード")
    print(f"{'─'*68}")
    logger.info("訓練データ読み込み中...")
    df_train_raw = analyst.load_data(train_start, train_end, version=args.version)
    logger.info("テストデータ読み込み中...")
    df_test_raw = analyst.load_data(test_start, test_end, version=args.version)

    if df_train_raw.empty or df_test_raw.empty:
        print("データなし。終了します。")
        return

    df_train = analyst.filter_valid(df_train_raw)
    df_test = analyst.filter_valid(df_test_raw)
    df_train = analyst.add_ranks(df_train, odds_threshold=args.min_odds)
    df_test = analyst.add_ranks(df_test, odds_threshold=args.min_odds)

    # ── Step 2: ベースライン計測 ──────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Step 2: ベースライン計測")
    print(f"{'─'*68}")
    baseline = step_baseline(df_train, df_test, odds_threshold=args.min_odds)

    print(f"\n  {'指標':<20} {'訓練':>10} {'テスト':>10}")
    print(f"  {'-'*44}")
    print(f"  {'1位 3着内率':<20} {baseline['train']['place_rate_pct']:>9.1f}% {baseline['test']['place_rate_pct']:>9.1f}%")
    print(f"  {'1位 単勝ROI':<20} {baseline['train']['win_roi_pct']:>9.1f}% {baseline['test']['win_roi_pct']:>9.1f}%")
    print(f"  {'穴馬ヒット率':<20} {baseline['train']['upside_hit_rate']:>9.1%} {baseline['test']['upside_hit_rate']:>9.1%}")
    win_roi_tr = baseline['train']['upside_win_roi_pct']
    win_roi_te = baseline['test']['upside_win_roi_pct']
    print(f"  {'穴馬 単勝ROI':<20} {_fmt_roi(win_roi_tr):>10} {_fmt_roi(win_roi_te):>10}")

    # ── Step 3: Analyst Agent ─────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Step 3: Analyst Agent — 穴馬パターン分析")
    print(f"{'─'*68}")
    analyst_results = step_analyst(
        df_train,
        top_n=args.top_n,
        odds_threshold=args.min_odds,
        inter_out_path=inter_json,
    )
    interactions = analyst_results["top_interactions"]

    if args.skip_optimize or (not interactions and args.top_n > 0):
        if args.skip_optimize:
            print("\n  --skip-optimize 指定のため最適化をスキップ")
        else:
            print("\n  交互作用項候補なし。最適化をスキップ")

        entry = {
            "cycle_id": cycle_id,
            "timestamp": datetime.now().isoformat(),
            "train_period": args.train,
            "test_period": args.test,
            "objective": args.objective,
            "baseline": baseline,
            "candidate_interactions": [d["feature"] for d in interactions],
            "result": None,
            "decision": "skipped",
            "note": "最適化スキップ",
        }
        log["cycles"].append(entry)
        save_log(log)
        return

    # ── Step 4: Feature Engineer ──────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Step 4: Feature Engineer — ウェイト最適化")
    print(f"{'─'*68}")
    base_w, inter_w, eval_result = step_feature_engineer(
        df_train=df_train,
        df_test=df_test,
        interactions=interactions,
        objective=args.objective,
        l2_lambda=args.l2,
        n_folds=args.folds,
        odds_threshold=args.min_odds,
    )

    # ── Step 5: レポート生成 ──────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Step 5: レポート生成")
    print(f"{'─'*68}")
    report_md = generate_report(
        cycle_id=cycle_id,
        train_period=args.train,
        test_period=args.test,
        baseline=baseline,
        analyst_results=analyst_results,
        base_w=base_w,
        inter_w=inter_w,
        eval_result=eval_result,
        objective=args.objective,
    )

    report_path = (
        Path(args.report_out)
        if args.report_out
        else _WRITABLE_DIR / f"chihou_improvement_report_{cycle_id}.md"
    )
    report_path.write_text(report_md)
    print(f"\n  Markdown レポートを保存: {report_path}")

    # ── Step 6: ユーザー採用/却下 ─────────────────────────────────
    overfit = eval_result.get("overfit_flag", False)
    obj_diff = eval_result.get(f"test_{args.objective}_diff", 0.0)

    if not args.non_interactive:
        print(f"\n{'─'*68}")
        print(f"  Step 6: 採用 / 却下")
        print(f"{'─'*68}")

        if overfit:
            print(f"\n  ⚠️  過学習フラグあり（テスト期間ROIが訓練比 -10% 以上）")
        if obj_diff <= 0:
            print(f"\n  ⚠️  主目標 ({args.objective}) テスト期間での改善なし ({obj_diff:+.1f}%)")

        print(f"\n  この改善案を採用しますか？")
        print(f"  採用時: chihou_calculator.py の COMPOSITE_WEIGHTS を自動更新")
        print(f"  採用後: chihou_backfill_indices.py の再実行が必要です")
        answer = input("\n  [y=採用 / n=却下 / skip=保留]: ").strip().lower()
    else:
        answer = "skip"

    decision_map = {"y": "adopted", "n": "rejected", "skip": "pending"}
    decision = decision_map.get(answer, "pending")

    # 新バージョン番号
    new_version = CHIHOU_COMPOSITE_VERSION + 1

    if decision == "adopted":
        # base_w の列名 → weight_key に変換してCOMPOSITE_WEIGHTS形式に
        col_to_key = feat_eng.COL_TO_WEIGHT_KEY
        # ウェイトを正規化して合計1.0に（BASE_BUDGET=0.80 → 1.00）
        w_sum = sum(base_w.values()) or 1.0
        new_composite_weights = {
            col_to_key[col]: round(w / w_sum, 4)
            for col, w in base_w.items()
            if col in col_to_key
        }
        update_chihou_calculator(new_composite_weights, new_version)
        print(f"\n  ✅ 採用しました。")
        print(f"  📝 chihou_calculator.py を更新: CHIHOU_COMPOSITE_VERSION={new_version}")
        print(f"  📝 新ウェイト: {new_composite_weights}")
        print(f"\n  次のステップ: バックフィル再実行")
        print(f"  cd backend && uv run python scripts/chihou_backfill_indices.py --from-date 20240101")

    elif decision == "rejected":
        print("\n  ❌ 却下しました。次のサイクルで別のアプローチを試みます。")
    else:
        print("\n  ⏸  保留しました。後から chihou_improvement_log.json の decision を更新できます。")

    # optimization_result.json に保存
    opt_out = _WRITABLE_DIR / "chihou_optimization_result.json"
    opt_payload = {
        "meta": {
            "cycle_id": cycle_id,
            "train": args.train,
            "test": args.test,
            "objective": args.objective,
            "odds_threshold": args.min_odds,
        },
        "base_weights": {col: round(w, 6) for col, w in base_w.items()},
        "inter_weights": {feat: round(w, 6) for feat, w in inter_w.items()},
        "eval": eval_result,
        "interactions_used": interactions,
    }
    opt_out.write_text(json.dumps(opt_payload, ensure_ascii=False, indent=2))

    # 履歴記録
    entry = {
        "cycle_id": cycle_id,
        "timestamp": datetime.now().isoformat(),
        "train_period": args.train,
        "test_period": args.test,
        "objective": args.objective,
        "baseline": {
            "train": baseline["train"],
            "test": baseline["test"],
        },
        "candidate_interactions": [d["feature"] for d in interactions],
        "new_weights": {
            "base": {col: round(w, 6) for col, w in base_w.items()},
            "interactions": {feat: round(w, 6) for feat, w in inter_w.items()},
        },
        "result": eval_result,
        "decision": decision,
        "note": "",
    }
    log["cycles"].append(entry)
    save_log(log)

    print(f"\n{'=' * 68}")
    print(f"  地方競馬 Cycle #{cycle_id} 完了")
    print(f"  決定: {decision}")
    print(f"  履歴: {LOG_PATH}")
    print(f"  レポート: {report_path}")
    print(f"{'=' * 68}")


if __name__ == "__main__":
    main()
