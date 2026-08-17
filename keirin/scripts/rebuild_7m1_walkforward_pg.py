"""7M1(RANK_7M1) の walk-forward 全期間再構築（VPS PG 直書き・2026-08-17 新設）。

月次凍結 vintage モデル（`lgbm_wt_eval_mYYMM`）で各月を採点し直すので
model-vintage look-ahead が無い。本番モデル（全期間 full_refit）を過去へ
遡及適用すると in-sample になる。

⚠️ 7M1 は軸も相手も pred_prob(3着内率) だけで決まるので win/bad モデルは使わない
   （`build_rows` は signature を揃えるためだけに受け取り、無視する）。

⚠️ **公式印（`wt_entries.prediction_mark`）が要る**。印が無い期間は
   `rank_7m1_daily_select` が fail-closed で落とすので静かに0件になる。
   件数が急に落ちたら、まず印の充足を疑うこと。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7m1_walkforward_pg.py [--dry-run]
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7m1_walkforward_pg.py --tail-only
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7m1_rank_wt import build_rows
from src.wt_rebuild_common import (
    format_missing_report,
    notify_discord_warning,
    rebuild_pg_atomic,
    split_by_model_availability,
)
from src.wt_vintage_config import monthly_windows, tail_windows

_RANK_LABEL = "RANK_7M1"
_DELETE_COND = "rank='RANK_7M1' AND race_key LIKE '%#7M1' AND race_date BETWEEN ? AND ?"
_SCRIPT_NAME = "rebuild_7m1_walkforward_pg.py"


def _parse_upto(v: str | None):
    """`--upto` を date へ。未指定なら None（＝当日まで）。"""
    return date.fromisoformat(v) if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tail-only", action="store_true",
                     help="直近月（今月）の窓のみ再構築する日次軽量運用向けオプション。"
                          "確定済み過去月は結果が変わらないため毎日再計算する必要がなく、"
                          "これのみ再実行すれば直近日をhonestな状態に保てる。")
    ap.add_argument("--skip-missing-models", action="store_true",
                     help="vintageモデルpklが存在しない月をスキップして処理を継続する。"
                          "指定しない場合、モデル不足を検出した時点で計算を一切開始せず"
                          "即座にエラー終了する（全期間計算後に失敗して結果を失うのを防ぐ・"
                          "2026-08-01のm2608不足によるFileNotFoundError実害を踏まえた対応）。")
    # 🔴 7M1 固有。**2024-01〜07 は vintage モデルの較正差で母集団が3倍に膨らむ**
    #    （7車レースの33〜47%が選出＝21〜32件/日。2024-08以降は16〜19%＝10.5件/日）。
    #    ゲートが p3合計の**絶対閾値**なので、モデルが自信控えめだと 7C が縮み
    #    7M1 が膨らむ（7H3 が廃止に至った型と同じ）。live は 7C と閾値を共有する
    #    ので追随するが、**その期間の行を picks_history に残すと Web の成績表示で
    #    「昔はもっと出ていた」と誤読される**ため、既定では書かない。
    #    2024 も入れたいときは `--since 2024-01` を明示すること。
    ap.add_argument("--since", metavar="YYYY-MM", default="2025-01",
                    help="この月以降の窓だけを再構築する（既定 2025-01）")
    ap.add_argument("--upto", metavar="YYYY-MM-DD", default=None,
                    help="この日までを再構築する（当日を含めたくないときに使う）。\n"
                         "monthly_windows は既定で当日を含み、結果未確定のレースは\n"
                         "再構築で戻せないため、実行すると当日の行が消える。")
    args = ap.parse_args()

    # --tail-only は当日を含めない（tail_windows の docstring 参照）。
    # 当日分を削除すると再構築では戻せず、Web から推奨が消えるため。
    windows = (tail_windows() if args.tail_only
               else monthly_windows(_parse_upto(args.upto)))
    if not args.tail_only and args.since:
        before = len(windows)
        windows = [w for w in windows if w[0][:7] >= args.since]
        if before != len(windows):
            print(f"[rebuild-7m1-pg] --since {args.since}: "
                  f"{before - len(windows)}窓を除外（{len(windows)}窓を処理）")

    # --- 事前チェック: build_rows(重い計算)を始める前に全窓のモデル存在を検証 ---
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
            print(f"[rebuild-7m1-pg] --skip-missing-models 未指定のため処理を中断します"
                  f"（計算は一切行っていません）。")
            sys.exit(1)
        notify_discord_warning(
            f"⚠️ **[{_SCRIPT_NAME}] vintageモデル不足を検出、"
            f"--skip-missing-models により当該窓を除外して続行します**\n{report}"
        )
        print(f"[rebuild-7m1-pg] --skip-missing-models 指定のため{len(missing)}窓を"
              f"除外して続行します。")

    windows = available
    if not windows:
        print("[rebuild-7m1-pg] 処理対象の窓がありません（全窓でモデル不足）。終了します。")
        sys.exit(1)

    per_window_rows: list[tuple[str, str, list[dict]]] = []
    all_rows: list[dict] = []
    for date_from, date_to, eval_model, win_model in windows:
        print(f"\n[rebuild-7m1-pg] {date_from}〜{date_to}  eval={eval_model} win={win_model}", flush=True)
        rows = build_rows(eval_model, date_from, date_to, win_model_name=win_model)
        n_hit = sum(r["hit"] for r in rows)
        bet = sum(r["bet_amount"] for r in rows)
        pay = sum(r["payout"] for r in rows)
        n_days = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days + 1
        print(f"[rebuild-7m1-pg]   7M1: {len(rows)}R ({len(rows)/n_days:.1f}R/日) 的中{n_hit} "
              f"({n_hit / len(rows) * 100 if rows else 0:.1f}%) "
              f"投資{bet:,} → 回収{pay:,} ROI {pay / bet * 100 if bet else 0:.1f}%", flush=True)
        per_window_rows.append((date_from, date_to, rows))
        all_rows.extend(rows)

    total_hit = sum(r["hit"] for r in all_rows)
    total_bet = sum(r["bet_amount"] for r in all_rows)
    total_pay = sum(r["payout"] for r in all_rows)
    print(f"\n[rebuild-7m1-pg] ===== 全期間合計 =====")
    print(f"[rebuild-7m1-pg] 7M1: {len(all_rows)}R 的中{total_hit} "
          f"({total_hit / len(all_rows) * 100 if all_rows else 0:.1f}%) "
          f"投資{total_bet:,} → 回収{total_pay:,} "
          f"ROI {total_pay / total_bet * 100 if total_bet else 0:.1f}%")

    rebuild_pg_atomic(_RANK_LABEL, _DELETE_COND, per_window_rows, args.dry_run)


if __name__ == "__main__":
    main()
