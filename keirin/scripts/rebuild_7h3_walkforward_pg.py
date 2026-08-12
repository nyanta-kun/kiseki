#!/usr/bin/env python3
"""7H3(RANK_7H3) の全期間honest再構築（月次凍結vintageモデル使用）。

`rebuild_7h1_walkforward_pg.py` の 7H3 版。設計は同一（月次窓ごとに、その月より
前のデータだけで学習した vintage モデルで候補を作り直し、単一トランザクションで
picks_history を置き換える）。

## 7H3 固有の点

- **必要なモデルは eval / win の2つだけ**。7H3 はレース単位の学習モデルを持たず、
  3着内率（軸・相手・ゲート）と1着率（Plackett-Luce 配分）だけで決まる。
  したがって `require_bad` / `require_favbust` はいずれも False で、
  **2024-01 の窓から全期間そろう**（7H1 のように 2024-01〜03 が欠けない）。
- 軸は `rank_7h3_axis()`（3着内率の上位2車）で、**3ヘッド軸ではない**。
  `_THREE_HEAD_RANKS` に RANK_7H3 は含まれないので、旧軸ガードの対象外
  （`axis_is_three_head` を渡す必要も無い）。
- ⚠️ **`RANK_7H3_AXIS_PRODUCT_MIN`(0.70) は本番モデルの較正に合わせた絶対閾値**。
  vintage モデルは較正が違うので、月ごとの該当件数は本番と多少ずれる。
  これは 7H1 の `RANK_7H1_BUST_PROB_MIN` と同じ性質で、honest 再構築では
  「その vintage で見たときの件数」が正しい。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7h3_walkforward_pg.py [--dry-run]
    # 当日を含めたくないとき（未確定レースが消えるのを避ける）
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7h3_walkforward_pg.py --upto 2026-08-11
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7h3_rank_wt import build_rows  # noqa: E402
from src.wt_rebuild_common import (  # noqa: E402
    format_missing_report,
    notify_discord_warning,
    rebuild_pg_atomic,
    split_by_model_availability,
)
from src.wt_vintage_config import monthly_windows  # noqa: E402

_RANK_LABEL = "RANK_7H3"
_DELETE_COND = "rank='RANK_7H3' AND race_key LIKE '%#7H3' AND race_date BETWEEN ? AND ?"
_SCRIPT_NAME = "rebuild_7h3_walkforward_pg.py"


def _parse_upto(v: str | None):
    """`--upto` を date へ。未指定なら None（＝当日まで）。"""
    return date.fromisoformat(v) if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tail-only", action="store_true",
                    help="直近月（今月）の窓のみ再構築する日次軽量運用向けオプション。")
    ap.add_argument("--skip-missing-models", action="store_true",
                    help="vintageモデルpklが存在しない月をスキップして続行する。"
                         "**7H3 は eval/win しか要らないので通常は不要**。")
    ap.add_argument("--upto", metavar="YYYY-MM-DD", default=None,
                    help="この日までを再構築する（当日を含めたくないときに使う）。\n"
                         "monthly_windows は既定で当日を含み、結果未確定のレースは\n"
                         "再構築で戻せないため、実行すると当日の行が消える。")
    args = ap.parse_args()

    windows = monthly_windows(_parse_upto(args.upto))
    if args.tail_only:
        windows = windows[-1:]

    # --- 事前チェック: 重い計算を始める前に全窓のモデル存在を検証 ---
    available, missing = split_by_model_availability(windows)
    if missing:
        report = format_missing_report(_RANK_LABEL, missing)
        print(report)
        if not args.skip_missing_models:
            notify_discord_warning(
                f"🚨 **[{_SCRIPT_NAME}] vintageモデル不足のため計算を開始せず中断しました**\n"
                f"{report}\n"
                f"`train_monthly_vintage_models.py --only-missing` で不足月を学習するか、"
                f"`--skip-missing-models` を指定して当該月を除外して続行してください。"
            )
            print("[rebuild-7h3-pg] --skip-missing-models 未指定のため処理を中断します"
                  "（計算は一切行っていません）。")
            sys.exit(1)
        print(f"[rebuild-7h3-pg] --skip-missing-models 指定のため{len(missing)}窓を"
              f"除外して続行します。")

    windows = available
    if not windows:
        print("[rebuild-7h3-pg] 処理対象の窓がありません（全窓でモデル不足）。終了します。")
        sys.exit(1)

    per_window_rows: list[tuple[str, str, list[dict]]] = []
    all_rows: list[dict] = []
    for date_from, date_to, eval_model, win_model in windows:
        print(f"\n[rebuild-7h3-pg] {date_from}〜{date_to}  eval={eval_model} "
              f"win={win_model}", flush=True)
        rows = build_rows(date_from, date_to, eval_model=eval_model,
                          win_model=win_model)
        n_hit = sum(r["hit"] for r in rows)
        bet = sum(r["bet_amount"] for r in rows)
        pay = sum(r["payout"] for r in rows)
        big = sum(1 for r in rows if r["payout"] >= 100_000)
        n_days = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days + 1
        print(f"[rebuild-7h3-pg]   7H3: {len(rows)}R ({len(rows)/n_days:.1f}R/日) "
              f"的中{n_hit} ({n_hit / len(rows) * 100 if rows else 0:.1f}%) "
              f"投資{bet:,} → 回収{pay:,} "
              f"ROI {pay / bet * 100 if bet else 0:.1f}% / 10万円超 {big}件", flush=True)
        per_window_rows.append((date_from, date_to, rows))
        all_rows.extend(rows)

    total_hit = sum(r["hit"] for r in all_rows)
    total_bet = sum(r["bet_amount"] for r in all_rows)
    total_pay = sum(r["payout"] for r in all_rows)
    max_pay = max((r["payout"] for r in all_rows), default=0)
    total_big = sum(1 for r in all_rows if r["payout"] >= 100_000)
    print("\n[rebuild-7h3-pg] ===== 全期間合計 =====")
    print(f"[rebuild-7h3-pg] 7H3: {len(all_rows)}R 的中{total_hit} "
          f"({total_hit / len(all_rows) * 100 if all_rows else 0:.1f}%) "
          f"投資{total_bet:,} → 回収{total_pay:,} "
          f"ROI {total_pay / total_bet * 100 if total_bet else 0:.1f}% "
          f"最高払戻 {max_pay:,}円 / 10万円超 {total_big}件")

    rebuild_pg_atomic(_RANK_LABEL, _DELETE_COND, per_window_rows, args.dry_run)


if __name__ == "__main__":
    main()
