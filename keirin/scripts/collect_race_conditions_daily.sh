#!/bin/bash
# 毎日 01:20 実行: 走路条件（実測＋予報）を直近ぶんだけ収集する。
#
# ## なぜ要るか（2026-08-21 新設）
#
# `wt_race_conditions` は 2026-08-18 に新設され過去分（102,313行）は backfill
# されたが、**日次で回す仕組みが無いまま止まっていた**。VPS の crontab に条件・
# 気象の収集ジョブが1本も無いことを N-3 監査で発見（2026-08-21 時点で3日ぶん欠損）。
#
# 🔴 **気象は過去へ遡って取り直せない種類のデータではないが、放置する理由も無い。**
#    実測（winticket）は開催ページが消えれば取れなくなり、予報（Open-Meteo の
#    Historical Forecast API）も遡及可能な保証は無い。CLAUDE.md の調教データが
#    「日次ジョブが 2026-06-07 で止まり 08-15〜16 開催週が丸ごと欠けた」のと同型。
#
# ⚠️ **いま気象は特徴量に入っていない**（風速×脚質は 2026-08-18 に不採用）。
#    当面の実害は無く、これは将来の検証のための貯蓄である。
#
# ## 2系統を必ず両方取る
#
#   実測（発走時点）= winticket        → `weather` / `wind_speed`      … 実績の検証用
#   予報（朝時点）  = Open-Meteo       → `fc_*`                        … 予想への投入用
#
# 🔴 **1つの列に混ぜてはいけない**（`backfill_race_forecast.py` の冒頭参照）。
#    混ぜると検証では効いて見えるのに配信では欠損する特徴量が出来る。
#
# ## 実行時刻の根拠
#
# 最終レースは 23:30 頃で、`intraday_results_wt.sh`(*/15 8-23,0) と
# `backfill_missing_prerace_wt.py`(00:40) の後。07:00 の `daily_picks_wt.sh` より前。
# 01:20 はこれらのどれとも重ならない。
set -e
set -o pipefail
cd "$(dirname "$0")/.."

DAYS="${CONDITIONS_LOOKBACK_DAYS:-7}"      # 遡る日数（既定7日）
SINCE=$(date -d "${DAYS} days ago" +%Y-%m-%d 2>/dev/null \
        || date -v-"${DAYS}"d +%Y-%m-%d)   # GNU / BSD 両対応
LOG_DIR="data/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/race_conditions.log"

# 多重起動防止。VPS には util-linux の flock がある（Mac には無いので注意）。
LOCK_FILE="$LOG_DIR/race_conditions.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date '+%m-%d %H:%M:%S')] 前回実行がロック中のためスキップ（${LOCK_FILE}）" \
    | tee -a "$LOG"
  exit 0
fi

echo "[$(date '+%m-%d %H:%M:%S')] === 走路条件の収集 開始（${SINCE} 以降）===" | tee -a "$LOG"

# 🔴 片方が失敗してももう片方は走らせる（`set -e` を局所的に外す）。
#    実測は winticket・予報は Open-Meteo と**別サービス**なので、一方の障害で
#    もう一方まで落とすと欠損が倍になる。
rc=0
set +e
PYTHONPATH=. .venv/bin/python3 scripts/backfill_race_conditions_wt.py \
  --since "$SINCE" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -ne 0 ] && { echo "[$(date '+%H:%M:%S')] 実測の収集に失敗" \
  | tee -a "$LOG"; rc=1; }

PYTHONPATH=. .venv/bin/python3 scripts/backfill_race_forecast.py \
  --since "$SINCE" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" -ne 0 ] && { echo "[$(date '+%H:%M:%S')] 予報の収集に失敗" \
  | tee -a "$LOG"; rc=1; }
set -e

echo "[$(date '+%m-%d %H:%M:%S')] === 走路条件の収集 完了（rc=${rc}）===" | tee -a "$LOG"
exit "$rc"
