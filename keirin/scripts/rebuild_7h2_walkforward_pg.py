"""7H2(RANK_7H2) の walk-forward 全期間再構築（VPS PG 直書き・2026-08-18 新設）。

月次凍結 vintage モデル（`lgbm_wt_eval_mYYMM` / `lgbm_wt_win_mYYMM`）で各月を
採点し直すので model-vintage look-ahead が無い。本番モデル（全期間 full_refit）を
過去へ遡及適用すると in-sample になる。

🔴 **買い方は 2026-08-18 の三連複一本化後のもの**（三連単は破棄）。
   `picks_history` に残っていた 2026-08-10〜14 の旧 44件（三連単 7,000円 +
   三連複 3,000円）とは別物なので、`rebuild_pg_atomic` が該当期間を丸ごと
   置き換える。旧行と混ぜて集計してはいけない。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7h2_walkforward_pg.py [--dry-run]
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7h2_walkforward_pg.py --tail-only
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7h2_rank_wt import build_rows
from src.wt_rebuild_common import (
    format_missing_report,
    notify_discord_warning,
    rebuild_pg_atomic,
    split_by_model_availability,
)
from src.wt_vintage_config import monthly_windows, tail_windows

_RANK_LABEL = "RANK_7H2"
_DELETE_COND = ("rank='RANK_7H2' AND race_key LIKE '%#7H2' "
                "AND race_date BETWEEN ? AND ?")
_SCRIPT_NAME = "rebuild_7h2_walkforward_pg.py"


def _parse_upto(v: str | None):
    """`--upto` を date へ。未指定なら None（＝当日まで）。"""
    return date.fromisoformat(v) if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tail-only", action="store_true",
                    help="直近月（今月）の窓のみ再構築する日次軽量運用向け。")
    ap.add_argument("--skip-missing-models", action="store_true",
                    help="vintageモデルpklが存在しない月をスキップして続行する。"
                         "未指定なら検出時点で計算を一切開始せず終了する。")
    ap.add_argument("--since", metavar="YYYY-MM", default=None,
                    help="この年月以降の窓だけ処理する（部分再構築用）。")
    ap.add_argument("--upto", metavar="YYYY-MM-DD", default=None,
                    help="この日までを再構築する（当日を含めたくないときに使う）。")
    args = ap.parse_args()

    windows = (tail_windows() if args.tail_only
               else monthly_windows(_parse_upto(args.upto)))
    if args.since:
        windows = [w for w in windows if w[0][:7] >= args.since]

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
            print("[rebuild-7h2-pg] --skip-missing-models 未指定のため処理を中断します"
                  "（計算は一切行っていません）。")
            sys.exit(1)
        notify_discord_warning(
            f"⚠️ **[{_SCRIPT_NAME}] vintageモデル不足を検出、"
            f"--skip-missing-models により当該窓を除外して続行します**\n{report}"
        )

    windows = available
    if not windows:
        print("[rebuild-7h2-pg] 処理対象の窓がありません。終了します。")
        sys.exit(1)

    per_window_rows: list[tuple[str, str, list[dict]]] = []
    all_rows: list[dict] = []
    for date_from, date_to, eval_model, win_model in windows:
        print(f"\n[rebuild-7h2-pg] {date_from}〜{date_to}  "
              f"eval={eval_model} win={win_model}", flush=True)
        rows = build_rows(date_from, date_to, eval_model=eval_model,
                          win_model=win_model)
        n_hit = sum(r["hit"] for r in rows)
        n_disp = sum(1 for r in rows if r["hit"] and r["payout"] > r["bet_amount"])
        bet = sum(r["bet_amount"] for r in rows)
        pay = sum(r["payout"] for r in rows)
        n_days = (date.fromisoformat(date_to)
                  - date.fromisoformat(date_from)).days + 1
        print(f"[rebuild-7h2-pg]   7H2: {len(rows)}R ({len(rows)/n_days:.1f}R/日) "
              f"的中{n_hit} ({n_hit / len(rows) * 100 if rows else 0:.1f}%) "
              f"表示的中{n_disp} ({n_disp / len(rows) * 100 if rows else 0:.1f}%) "
              f"投資{bet:,} → 回収{pay:,} "
              f"ROI {pay / bet * 100 if bet else 0:.1f}%", flush=True)
        per_window_rows.append((date_from, date_to, rows))
        all_rows.extend(rows)

    t_hit = sum(r["hit"] for r in all_rows)
    t_disp = sum(1 for r in all_rows if r["hit"] and r["payout"] > r["bet_amount"])
    t_bet = sum(r["bet_amount"] for r in all_rows)
    t_pay = sum(r["payout"] for r in all_rows)
    print("\n[rebuild-7h2-pg] ===== 全期間合計 =====")
    print(f"[rebuild-7h2-pg] 7H2: {len(all_rows)}R 的中{t_hit} "
          f"({t_hit / len(all_rows) * 100 if all_rows else 0:.1f}%) "
          f"表示的中{t_disp} ({t_disp / len(all_rows) * 100 if all_rows else 0:.1f}%) "
          f"投資{t_bet:,} → 回収{t_pay:,} "
          f"ROI {t_pay / t_bet * 100 if t_bet else 0:.1f}%")

    rebuild_pg_atomic(_RANK_LABEL, _DELETE_COND, per_window_rows, args.dry_run)


if __name__ == "__main__":
    main()
