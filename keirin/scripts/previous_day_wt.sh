#!/bin/bash
# ============================================================================
# 前日処理（結果再収集・採点通知・取りこぼしバックフィル）
#
# 🔴🔴 **2026-09-11 に `daily_picks_wt.sh` から切り出した**（ユーザー判断）。
#    cron は **06:30**。当日分の `daily_picks_wt.sh`（07:00）は据え置き。
#
# 【切り出しの効果と、効果でないもの】
# 🟢 **前日成績の Discord 通知が 07:5x 以降 → 06:40 頃**へ早まる。
#    07:00 バッチが 9分28秒ぶん短くなり、メモリ競合も分離される。
# 🔴 **推奨（型ラボ）の公開は 1秒も早くならない。**
#    前日処理は 2026-08-07 から既に「当日の入稿より後ろ」に置かれており
#    （`run_previous_day_tasks` を型ラボの後ろで呼ぶ形）、元からブロックしていない。
#    早めたいのは当日側で、そちらは「印・ラインの公開時刻が不明」という理由で
#    2026-09-11 時点では 07:00 のまま様子見とした。
#
# 🔴 **中身は `daily_picks_wt.sh` の `run_previous_day_tasks()` と同一**。
#    処理の順序も引数も変えていない。変えたのは「いつ誰が呼ぶか」だけ。
#
# ⚠️ **共有ロック `wt_picks_writer.lock` を必ず取ること。** この処理は
#    `picks_history` を書き換える（`notify_results_wt.py` の採点）ので、
#    `daily_picks_wt.sh`（07:00）や `reconcile_walkforward_tail.sh`（08:40）と
#    同じ行を同時に触りうる。2026-08-06 の rebuild行×live行 混在と同型の事故になる。
# ============================================================================
set -e
set -o pipefail
# cron環境のPATHには /usr/sbin が無く joblib のCPUコア検出(sysctl)が警告を出すため追加
export PATH="/usr/sbin:/sbin:$PATH"
cd "$(dirname "$0")/.."
LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"

if [[ "$(uname)" == "Darwin" ]]; then
  YESTERDAY=$(date -v-1d +%Y-%m-%d)
else
  YESTERDAY=$(date -d "1 day ago" +%Y-%m-%d)
fi

# --- 多重起動防止 ---
LOCK_FILE="$LOG_DIR/previous_day_wt.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date '+%H:%M:%S')] [previous_day_wt] 前回実行がロック中のためスキップします（${LOCK_FILE}）。" \
    | tee -a "$LOG_DIR/lock_skips.log" >&2
  exit 0
fi

# --- 共有ロック: picks_history へ書く処理どうしの競合を防ぐ ---
# ⚠️ **待つ（-w）**。-n でスキップすると前日成績が黙って落ちる。
SHARED_LOCK="$LOG_DIR/wt_picks_writer.lock"
exec 201>"$SHARED_LOCK"
if ! flock -w 1800 201; then
  echo "[$(date '+%H:%M:%S')] [previous_day_wt] 共有ロック待ちが30分を超えました（${SHARED_LOCK}）。" \
    | tee -a "$LOG_DIR/lock_skips.log" >&2
  exit 1
fi

# --- KEIRIN_DB_URL 必須チェック ---
# 各処理は `|| echo "...失敗（継続）"` で握り潰すので、未設定だと全部空振りしつつ
# exit 0 で完走してしまう。ここで早期に中断する。
if [[ -z "${KEIRIN_DB_URL:-}" ]]; then
  echo "[$(date '+%H:%M:%S')] [FATAL] KEIRIN_DB_URL が未設定です。previous_day_wt.sh を中断します。" \
    | tee -a "$LOG_DIR/lock_skips.log" >&2
  exit 1
fi

echo "[$(date '+%H:%M:%S')] === 前日処理開始 $YESTERDAY ==="

# --- 前日成績（winticketで結果再収集→採点通知）---
# 前日処理は当日予想の前提ではないため、失敗しても継続（pipefailで失敗は可視化）。
echo "[$(date '+%H:%M:%S')] 前日($YESTERDAY) winticket結果再収集..."
# --full-scan: midnight の前日取得で拾いきれなかった分（Mac スリープ等）を確実に回収するため全会場走査。
.venv/bin/python3 -m src.cli.main collect-wt --date "$YESTERDAY" --full-scan \
  2>&1 | tee -a "$LOG_DIR/collect_wt_${YESTERDAY}.log" \
  || echo "[$(date '+%H:%M:%S')] 前日再収集に失敗（継続）"

echo "[$(date '+%H:%M:%S')] 前日成績をDiscordへ通知..."
.venv/bin/python3 scripts/notify_results_wt.py "$YESTERDAY" \
  2>&1 | tee -a "$LOG_DIR/notify_wt_${YESTERDAY}.log" \
  || echo "[$(date '+%H:%M:%S')] 前日成績通知に失敗（継続）"

# ワイド朝→直前(確定)ドリフト監視（前日分を記録・しばらく監視・通知なし）
# 朝≥2.5倍で推奨したW12が確定で2.5未満に落ちる問題(6/10:平均-63%)を継続計測。
.venv/bin/python3 scripts/monitor_wide_wt.py "$YESTERDAY" \
  >> "$LOG_DIR/wide_monitor_run.log" 2>&1 \
  || echo "[$(date '+%H:%M:%S')] ワイド監視に失敗（継続）"

# --- 結果バックフィル（直近数日の取りこぼし回収）---
# cron不発(Macスリープ等)で日次が飛ぶと、結果再収集は「前日のみ」なのでその日の
# 結果が永久に取り残される（6/6で39R未取得→勝ち予想が消える事象が発生）。
# 直近2〜4日前の未確定レースを再収集し（collect-wtは結果確定済みのみスキップ＝安価）、
# picks_history を --silent で静かに修復（Discord通知はしない＝重複通知を避ける）。
echo "[$(date '+%H:%M:%S')] 結果バックフィル（T-2〜T-4の取りこぼし回収）..."
for n in 2 3 4; do
  if [[ "$(uname)" == "Darwin" ]]; then
    BD=$(date -v-${n}d +%Y-%m-%d)
  else
    BD=$(date -d "$n days ago" +%Y-%m-%d)
  fi
  .venv/bin/python3 -m src.cli.main collect-wt --date "$BD" --full-scan \
    >> "$LOG_DIR/backfill_wt.log" 2>&1 || echo "  backfill collect $BD 失敗（継続）"
  .venv/bin/python3 scripts/notify_results_wt.py "$BD" --silent \
    >> "$LOG_DIR/backfill_wt.log" 2>&1 || echo "  backfill rescore $BD 失敗（継続）"
done

# 🔴 **完了マーカー。** 07:00 の `daily_picks_wt.sh` はこれを見て保険を飛ばす。
#    途中で落ちたら残らない＝保険が走る、という向きにしてある（fail-safe）。
touch "$LOG_DIR/previous_day_done_${YESTERDAY}"
# 古いマーカーの掃除（7日より前）。残しても害は無いが溜めない。
find "$LOG_DIR" -name 'previous_day_done_*' -type f -mtime +7 -delete 2>/dev/null || true

echo "[$(date '+%H:%M:%S')] === 前日処理完了 $YESTERDAY ==="
