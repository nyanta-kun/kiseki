#!/usr/bin/env python3
"""7T3(RANK_7T3) の honest 再構築（月次凍結vintageモデル使用）。

`rebuild_7t3_walkforward_pg.py` の 7T3 版。7T3 は買い目の確率を
**3着内率(eval) と 1着率(win) の位置別合成**で作り、帯（30倍以上）を切るのに
**三連単オッズ予測モデル**を使う。

## 🔴 2026-01 が下限（他ランクと違う）

三連単オッズ予測モデル（`data/models/odds_tf_n7.txt`）は学習終端 2025-12-31 で
**月次 vintage が無い**。2025年以前へ遡ると model-vintage look-ahead になるため、
`monthly_windows()` が返す窓のうち **2026-01 より前は機械的に落とす**。

⚠️ したがって 7T3 の再構築結果を他ランク（2024-01 から）と**期間を揃えずに
   並べてはいけない**。比較するなら相手側も 2026-01 以降に切ること。

⚠️ 本番 `lgbm_wt_eval` / `lgbm_wt_win` は full_refit でホールドアウト無し。
   過去へ遡って使うと in-sample になるので、必ず月次vintage を通すこと。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7t3_walkforward_pg.py [--dry-run]
    PYTHONPATH=. .venv/bin/python scripts/rebuild_7t3_walkforward_pg.py --tail-only
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t3_rank_wt import ODDS_TF_TRAIN_END, build_rows
from src.wt_rebuild_common import (
    format_missing_report,
    notify_discord_warning,
    rebuild_pg_atomic,
    split_by_model_availability,
)
from src.wt_vintage_config import monthly_windows, tail_windows

_RANK_LABEL = "RANK_7T3"
_DELETE_COND = "rank='RANK_7T3' AND race_key LIKE '%#7T3' AND race_date BETWEEN ? AND ?"
_SCRIPT_NAME = "rebuild_7t3_walkforward_pg.py"


def _parse_upto(v: str | None):
    return date.fromisoformat(v) if v else None


def drop_windows_before_odds_model(windows):
    """三連単オッズモデルの学習終端より前の窓を落とす。

    🔴 **黙って落とさない。** 落とした窓を報告しないと「7T3 だけ期間が短い」
       ことに気づかないまま他ランクと並べることになる。
    """
    keep = [w for w in windows if w[1] > ODDS_TF_TRAIN_END]
    dropped = len(windows) - len(keep)
    if dropped:
        print(f"[rebuild-7t3-pg] 三連単オッズモデルの学習終端({ODDS_TF_TRAIN_END})以前の "
              f"{dropped}窓を除外しました（vintage が無く look-ahead になるため）。"
              f"7T3 の honest 期間は 2026-01 以降のみです。", flush=True)
    return keep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tail-only", action="store_true",
                    help="直近月（今月）の窓のみ再構築する日次軽量運用向けオプション。")
    ap.add_argument("--skip-missing-models", action="store_true",
                    help="vintageモデルpklが存在しない月をスキップして処理を継続する。")
    ap.add_argument("--upto", metavar="YYYY-MM-DD", default=None,
                    help="この日までを再構築する（当日を含めたくないときに使う）。")
    args = ap.parse_args()

    windows = (tail_windows() if args.tail_only
               else monthly_windows(_parse_upto(args.upto)))
    windows = drop_windows_before_odds_model(windows)
    if not windows:
        print("[rebuild-7t3-pg] 対象の窓がありません（全窓がオッズモデルの学習期間内）。")
        sys.exit(1)

    # 7T3 は eval と win の両方を使う（位置別合成 PL・bad は使わない）。
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
            print("[rebuild-7t3-pg] --skip-missing-models 未指定のため処理を中断します"
                  "（計算は一切行っていません）。")
            sys.exit(1)
        notify_discord_warning(
            f"⚠️ **[{_SCRIPT_NAME}] vintageモデル不足を検出、"
            f"--skip-missing-models により当該窓を除外して続行します**\n{report}"
        )
    windows = available
    if not windows:
        print("[rebuild-7t3-pg] 処理対象の窓がありません（全窓でモデル不足）。終了します。")
        sys.exit(1)

    per_window_rows: list[tuple[str, str, list[dict]]] = []
    all_rows: list[dict] = []
    for date_from, date_to, eval_model, win_model in windows:
        print(f"\n[rebuild-7t3-pg] {date_from}〜{date_to}  eval={eval_model} win={win_model}",
              flush=True)
        rows = build_rows(date_from, date_to, eval_model=eval_model, win_model=win_model)
        n_hit = sum(r["hit"] for r in rows)
        bet = sum(r["bet_amount"] for r in rows)
        pay = sum(r["payout"] for r in rows)
        n_days = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days + 1
        print(f"[rebuild-7t3-pg]   7T3: {len(rows)}R ({len(rows)/n_days:.1f}R/日) 的中{n_hit} "
              f"({n_hit / len(rows) * 100 if rows else 0:.1f}%) "
              f"投資{bet:,} → 回収{pay:,} ROI {pay / bet * 100 if bet else 0:.1f}%", flush=True)
        per_window_rows.append((date_from, date_to, rows))
        all_rows.extend(rows)

    total_hit = sum(r["hit"] for r in all_rows)
    total_bet = sum(r["bet_amount"] for r in all_rows)
    total_pay = sum(r["payout"] for r in all_rows)
    print("\n[rebuild-7t3-pg] ===== 全期間合計（2026-01 以降のみ）=====")
    print(f"[rebuild-7t3-pg] 7T3: {len(all_rows)}R 的中{total_hit} "
          f"({total_hit / len(all_rows) * 100 if all_rows else 0:.1f}%) "
          f"投資{total_bet:,} → 回収{total_pay:,} "
          f"ROI {total_pay / total_bet * 100 if total_bet else 0:.1f}%")
    # 7T3 は3ヘッド軸を使わない（そもそも軸を置かない）。フラグを立ててはいけない。
    rebuild_pg_atomic(_RANK_LABEL, _DELETE_COND, per_window_rows, args.dry_run)


if __name__ == "__main__":
    main()
