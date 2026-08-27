#!/bin/bash
# モデルファイル(Mac→VPS)配布スクリプト（2026-07-31・D-5対応）。
#
# 背景:
#   従来はMac crontabのweekly_retrain_wt.sh行の末尾に直接rsyncコマンドが
#   書かれており、転送対象ファイルを長いコマンドラインで列挙していた。
#   `docs/vintage_model_policy.md`記載の通り、月次vintageモデル
#   （`lgbm_wt_eval_m????.pkl`等）は2026-07-30に一度きり手動rsyncで配布
#   されたのみで、crontabのrsync行には組み込まれていない。そのため
#   `scripts/train_monthly_vintage_models.py --only-missing`で新しい月の
#   モデルをMacで作っても、VPS側は今後永久に受け取れない構造的欠陥がある。
#
# 設計方針:
#   転送対象ファイルの列挙をcrontab（変更漏れの温床）からこのスクリプトに
#   一本化する。月次vintageモデルはファイル名パターン（`lgbm_wt_eval_m????.pkl`
#   / `lgbm_wt_win_m????.pkl`とその`.meta.json`）で動的に検出するため、
#   新しい月のモデルが追加されても本スクリプトの変更は不要。
#
# 使い方:
#   scripts/sync_models_to_vps.sh --dry-run   # 転送対象一覧を表示するのみ（何も送信しない）
#   scripts/sync_models_to_vps.sh             # 実際にrsyncで転送し、転送後に検証する
#
# crontabへの組み込み例（このスクリプト自体はcrontabを変更しない。
# 変更は別途PM/ユーザー判断で実施すること）:
#   30 23 * * 0 ~/GitHub/kiseki/keirin/scripts/weekly_retrain_wt.sh \
#     >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1 && \
#     ~/GitHub/kiseki/keirin/scripts/sync_models_to_vps.sh \
#     >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1
#
# 注意: 本スクリプトは実際のrsync転送を行う。crontabの変更・実運用への
# 組み込みはPM/ユーザーが確認の上で実施すること（このファイルの新設のみで
# 既存crontabは変更していない）。
set -euo pipefail
export PATH="/usr/sbin:/sbin:$PATH"

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
MODEL_DIR="$REPO_ROOT/data/models"
LOG_DIR="$REPO_ROOT/data/logs"
mkdir -p "$LOG_DIR"
DATE_STAMP=$(date +%Y-%m-%d)
LOG="$LOG_DIR/sync_models_to_vps_${DATE_STAMP}.log"

REMOTE_HOST="sekito"
# 2026-08-10 の kiseki 統合で VPS 側の keirin は ~/keirin から移動した。
# ⚠️ ここはローカルのパス解決（cd "$(dirname "$0")/.."）と違い**リモートの絶対パス**なので、
#    ディレクトリを動かしても自動では追随しない。旧パスのままだと rsync が
#    「宛先が無い」で失敗し、モデル配布だけが静かに止まる。
REMOTE_DIR="~/GitHub/kiseki/keirin/data/models/"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "不明な引数: $arg" >&2
      exit 2
      ;;
  esac
done

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

notify_failure() {
  local msg="$1"
  log "[FAIL] $msg"
  # src/notify/discord.py::send は channel 引数が必須（省略すると別チャンネルに
  # 誤送信される事故を防ぐ設計）。ここでは system チャンネルへ送る。
  if .venv/bin/python3 -c "
from src.notify.discord import send
ok = send('''🚨 **[sync_models_to_vps.sh] モデル配布に失敗しました**\n${msg}''', channel='system')
raise SystemExit(0 if ok else 1)
" >>"$LOG" 2>&1; then
    log "Discordへ失敗を通知しました。"
  else
    log "[FATAL] Discord通知にも失敗しました（DISCORD_WEBHOOK_URL_SYSTEM未設定などの可能性）。"
  fi
}

# --- 多重起動防止（weekly_retrain_wt.shと同様、mkdirの原子性を使ったロック） ---
LOCK_DIR="$LOG_DIR/sync_models_to_vps.lockdir"
if [[ "$DRY_RUN" -eq 0 ]]; then
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    OLD_PID=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
      log "前回実行(PID $OLD_PID)が継続中のためスキップします（${LOCK_DIR}）。"
      exit 0
    fi
    log "古いロック（PID ${OLD_PID:-不明} は不在）を検出。奪って続行します。"
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
  fi
  echo $$ > "$LOCK_DIR/pid"
  trap 'rm -rf "$LOCK_DIR"' EXIT
fi

# --- 転送対象ファイルの収集 ---
# 1) 本番モデル（従来crontabのrsync行と同一の明示列挙。今後増える予定は薄いため固定リスト）
PROD_FILES=(
  "lgbm_wt.pkl" "lgbm_wt.meta.json"
  "lgbm_wt_train_only.pkl" "lgbm_wt_train_only.meta.json"
  "lgbm_wt_win.pkl" "lgbm_wt_win.meta.json"
  "lgbm_wt_bad.pkl" "lgbm_wt_bad.meta.json"
  # 2026-08-12 追加: Web表示専用の2着内率モデル（wave-picks-wt が読む）。
  # 候補選定・ゲートには使わないので、無くても入稿は止まらない
  # ＝**画面から2着内率が消えるだけで誰も気づかない**。配布漏れに注意。
  "lgbm_wt_top2.pkl" "lgbm_wt_top2.meta.json"
  "lgbm_wt_top2_eval.pkl" "lgbm_wt_top2_eval.meta.json"
  "lgbm_wt_eval.pkl" "lgbm_wt_eval.meta.json"
  "lgbm_wt_win_eval.pkl" "lgbm_wt_win_eval.meta.json"
  "lgbm_wt_favbust.pkl" "lgbm_wt_favbust.meta.json"
  # 2026-08-08 追加: RANK_9H1 の波乱スコアモデル（レース単位・6/7/9車統合学習・31特徴）。
  # meta.json は無い（save_model は .pkl のみ書く）。
  # 配布されないと build_9h1_candidates.py が落ち、daily_picks_wt.sh は
  # `|| echo ...継続` で握り潰すので **ログ1行だけ残して 9H1 が永久に0件**になる。
  # 抜けは tests/test_model_sync_coverage.py が機械的に検出する。
  "lgbm_upset_screen.pkl"
  # 2026-08-11 追加: 最終三連複オッズの予測モデル（7車/9車・`src/odds_prediction.py`）。
  # ⚠️ **.pkl ではなく LightGBM のテキスト形式**で、読み込みも `load_model()` ではなく
  #    `lgb.Booster(model_file=...)` なので、`tests/test_model_sync_coverage.py` の
  #    従来の走査（load_model の第1引数を AST で拾う）には**引っかからない**。
  #    そのため同テストへ専用の検査を足してある。名前を変えるときは両方直すこと。
  # 無くても入稿は止まらない（WARNING を出して従来の傾斜配分へ落ちる）が、
  # **黙って実質的中率が 3〜5pt 落ちた状態で回り続ける**ので配布漏れは実害になる。
  "odds_trio_n7.txt"
  "odds_trio_n9.txt"
  "odds_trio_meta.json"
  # 2026-08-28 追加: 最終**三連単**オッズの予測モデル（`src/odds_prediction_tf.py`）。
  # 🔴 **2026-08-11 に三連複を足したとき、三連単は入れ忘れていた。**
  #    VPS の `odds_tf_n7.txt` は 2026-08-13 に手で置かれたきり同期されておらず、
  #    Mac で再学習しても**本番は古いモデルのまま黙って回り続ける**状態だった。
  # 🔴 三連単は PR#316/#317（2026-08-26）で**入稿の配分そのもの**に使うようになった。
  #    無いと `predict_board` が `OddsPredictionUnavailable` を投げ、
  #    7T1/7T3/7H1 の買い目が組めない（＝その枠が黙って0件になる）。
  # ⚠️ 9車（`odds_tf_n9.txt`）は**まだ本番に置いていない**。学習して
  #    `data/models/` へ入れた時点で `tests/test_model_sync_coverage.py` が
  #    「配布リストに無い」と落ちるので、そこで足すこと。
  "odds_tf_n7.txt"
  "odds_tf_meta.json"
)
# 🔴 **GitHub Releases への配布は 2026-08-12 に停止した（既定 OFF）。**
#
# 元の意図は「CI がデプロイ時に取得する最小セットを置き、マージと同時にコードと
# モデルを揃える」だったが、
#
#   1. **`nyanta-kun/kiseki` は public リポジトリ**。Release アセットは
#      **誰でもダウンロードできる**。本番の学習済みモデルをそのまま公開していた
#   2. **取得する側が存在しない**。`.github/workflows/` に `models-latest` を
#      参照する記述は1つも無く、VPS への配布は rsync が単独で担っている
#
# つまり公開する利益はゼロで、リスクだけがあった（2026-08-12 に実際に
# Release が作られ、本番4モデル8ファイルが公開状態になったため削除した）。
#
# 再開するなら **リポジトリを private にする**か、**Release ではない非公開の
# 配布先**（VPS 上の配布ディレクトリ等）を用意してからにすること。
# その場合は `SYNC_MODELS_TO_RELEASES=1` を明示的に立てる。
UPLOAD_TO_RELEASES="${SYNC_MODELS_TO_RELEASES:-0}"
RELEASE_TAG="models-latest"
RELEASE_FILES=(
  "lgbm_wt.pkl" "lgbm_wt.meta.json"
  "lgbm_wt_win.pkl" "lgbm_wt_win.meta.json"
  "lgbm_wt_bad.pkl" "lgbm_wt_bad.meta.json"
  "lgbm_wt_top2.pkl" "lgbm_wt_top2.meta.json"
)
EXTRA_FILES=("upset_cuts_wt.json")

FILES=()
MISSING=()
for name in "${PROD_FILES[@]}" "${EXTRA_FILES[@]}"; do
  if [[ -f "$MODEL_DIR/$name" ]]; then
    FILES+=("$MODEL_DIR/$name")
  else
    MISSING+=("$name")
  fi
done

# 2) 月次vintageモデル（命名規則: lgbm_wt_{eval,win,bad}_mYYMM.{pkl,meta.json}。
#    docs/vintage_model_policy.md参照）。ファイル名パターンでの動的検出のため、
#    新しい「月」が増えてもこのスクリプトの変更は不要。
#
#    ⚠️ **新しい「種類」を増やしたらここへ足すこと。** 2026-08-05 に大敗モデルの
#    月次vintage（lgbm_wt_bad_mYYMM）を新設した際にここへの追加が漏れ、VPSへ
#    1本も配布されていなかった。結果、2026-08-06 に再開した tail 再構築が
#    7車4ランクすべて「lgbm_wt_bad_m2608 が無い」で中断した
#    （ガードが計算前に止めたので実害はゼロ。設計どおり）。
#    同種の「一覧の手書き二重管理」は同日 netkeirin の RANK_ORDER・
#    _THREE_HEAD_RANKS・ガードテストの対象リストでも事故を起こしている。
shopt -s nullglob
VINTAGE_FILES=(
  "$MODEL_DIR"/lgbm_wt_eval_m[0-9][0-9][0-9][0-9].pkl
  "$MODEL_DIR"/lgbm_wt_eval_m[0-9][0-9][0-9][0-9].meta.json
  "$MODEL_DIR"/lgbm_wt_win_m[0-9][0-9][0-9][0-9].pkl
  "$MODEL_DIR"/lgbm_wt_win_m[0-9][0-9][0-9][0-9].meta.json
  "$MODEL_DIR"/lgbm_wt_bad_m[0-9][0-9][0-9][0-9].pkl
  "$MODEL_DIR"/lgbm_wt_bad_m[0-9][0-9][0-9][0-9].meta.json
  # 2026-08-06 追加: 穴推奨 RANK_7H1 のバスト予測モデル（レース単位・67特徴）。
  # ⚠️ 「モデルの新しい"種類"を足したときに配布glob が取り残される」事故は
  #    lgbm_wt_bad で一度踏んでいる（同日）。種別を増やしたら必ずここも見ること。
  "$MODEL_DIR"/lgbm_wt_favbust_m[0-9][0-9][0-9][0-9].pkl
  "$MODEL_DIR"/lgbm_wt_favbust_m[0-9][0-9][0-9][0-9].meta.json
  # 2026-08-19 追加: 2着内モデルの月次vintage。`pred_top2_pct` のバックフィルに使う。
  # 🔴 本番の `lgbm_wt_top2` は full_refit なので過去の採点に使うと
  #    model-vintage look-ahead になる。vintage が VPS に無いと
  #    `backfill_index_pct_wt.py` が「書かない」側へ落ちて**静かに欠測する**。
  # ⚠️ 上の警告のとおり、種別を足したらここも必ず見ること（今回もこの glob が
  #    取り残されており、同じ事故を繰り返すところだった）。
  "$MODEL_DIR"/lgbm_wt_top2_m[0-9][0-9][0-9][0-9].pkl
  "$MODEL_DIR"/lgbm_wt_top2_m[0-9][0-9][0-9][0-9].meta.json
)
shopt -u nullglob
# ⚠️ macOS 標準の bash 3.2 では `set -u` 下で **空配列の展開が unbound variable になる**
#    （bash 4.4+ では通る）。vintage モデルが1本も無い環境（新規 clone・worktree）で
#    `VINTAGE_FILES[@]: unbound variable` で落ちていた。CI は bash 5 なので気づけない。
#    CLAUDE.md の「mapfile を使わない」と同じ、bash 3.2 起因の罠。
if [[ ${#VINTAGE_FILES[@]} -gt 0 ]]; then
  FILES+=("${VINTAGE_FILES[@]}")
fi

N_PROD=$(( ${#PROD_FILES[@]} + ${#EXTRA_FILES[@]} - ${#MISSING[@]} ))
N_VINTAGE=${#VINTAGE_FILES[@]}
N_TOTAL=${#FILES[@]}

log "=== sync_models_to_vps: 転送対象 本番${N_PROD}件 + vintage${N_VINTAGE}件 = 計${N_TOTAL}件 ==="
if [[ ${#MISSING[@]} -gt 0 ]]; then
  log "[警告] 未検出のためスキップされた本番/追加ファイル: ${MISSING[*]}"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "--- DRY RUN: 転送対象ファイル一覧 (${N_TOTAL}件) ---"
  for f in "${FILES[@]}"; do
    echo "  $(basename "$f")"
  done
  echo "--- DRY RUN: 何も転送していません ---"
  exit 0
fi

if [[ "$N_TOTAL" -eq 0 ]]; then
  notify_failure "転送対象ファイルが1件も見つかりませんでした（${MODEL_DIR}）。"
  exit 1
fi

# --- 実転送 ---
log "rsync 実行中 → ${REMOTE_HOST}:${REMOTE_DIR}"
if ! rsync -av "${FILES[@]}" "${REMOTE_HOST}:${REMOTE_DIR}" >>"$LOG" 2>&1; then
  notify_failure "rsync がゼロ以外の終了コードで失敗しました。ログ: $LOG"
  exit 1
fi

# --- 転送後の検証 ---
# (a) チェックサム照合: --checksum --dry-run で「転送したはずなのに内容が
#     一致しないファイル」が無いかを確認する（サイズ・mtime一致で誤ってskipされる
#     ケースを検出。VPS側で追加でハッシュ計算プロセスを起動しないためVPSの
#     メモリ負荷は増やさない＝rsyncプロトコル内のチェックサム比較のみ）。
log "検証(1/2): チェックサム照合中..."
CHECKSUM_DIFF=$(rsync -av --checksum --dry-run --itemize-changes "${FILES[@]}" "${REMOTE_HOST}:${REMOTE_DIR}" \
  | grep -E '^[<>ch]' || true)
if [[ -n "$CHECKSUM_DIFF" ]]; then
  log "[検証NG] 転送後もチェックサムが一致しないファイルがあります:"
  echo "$CHECKSUM_DIFF" | tee -a "$LOG"
  notify_failure "転送後チェックサム照合で不一致を検出しました。詳細はログ($LOG)参照。"
  exit 1
fi
log "検証(1/2) OK: 全ファイルのチェックサムが一致しました。"

# (b) 実在確認: **転送した1件1件が VPS 側に在るか**を名前で突き合わせる。
#
# 🔴 2026-08-12 に「パターンに一致する**件数**の比較」から書き換えた。
#    旧実装は転送リストとは別に検証用の regex を手で持っており、
#    **モデルの種類を足すたびに2箇所を揃える必要**があった（スクリプト自身が
#    その旨を警告していたが、それでも 2026-08-06 に `lgbm_wt_bad`・
#    `lgbm_wt_favbust` で2回踏んでいる）。
#    実際 `lgbm_upset_screen.pkl`(08-08 追加)・`odds_trio_*`(08-11 追加) の4件は
#    **転送されているのに regex 側へ入っておらず**、件数がちょうど釣り合って
#    いるあいだだけ検査が通っていた。2着内モデルを足した時点で釣り合いが崩れ、
#    転送も照合も成功しているのに「不足」と誤報した。
#
#    名前で突き合わせれば **転送リストが唯一の正本**になり、二重管理が消える。
log "検証(2/2): VPS側に転送物が実在するか照合中..."
REMOTE_LS=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE_HOST" \
  "ls -1 ${REMOTE_DIR} 2>/dev/null" 2>>"$LOG" || echo "")
if [[ -z "$REMOTE_LS" ]]; then
  notify_failure "VPS側のファイル一覧を取得できませんでした（SSH到達不可の可能性）。転送自体は完了している可能性があります。"
  exit 1
fi
MISSING_REMOTE=()
for f in "${FILES[@]}"; do
  # 完全一致で探す（部分一致だと lgbm_wt.pkl が lgbm_wt_win.pkl に当たる）
  grep -qxF "$(basename "$f")" <<<"$REMOTE_LS" || MISSING_REMOTE+=("$(basename "$f")")
done
if [[ ${#MISSING_REMOTE[@]} -gt 0 ]]; then
  log "[検証NG] VPS側に見つからないファイル(${#MISSING_REMOTE[@]}件): ${MISSING_REMOTE[*]}"
  notify_failure "転送したはずのファイル${#MISSING_REMOTE[@]}件がVPS側にありません: ${MISSING_REMOTE[*]}"
  exit 1
fi
log "検証(2/2) OK: 転送した${N_TOTAL}件すべてがVPS側に存在します。"

# --- GitHub Releases へのアップロード（既定 OFF・上記の理由を必ず読むこと） ---
# gh 不在・認証なし・ネットワーク断でも rsync 自体は成功しているので、
# ここでの失敗は警告に留めて全体は成功扱いにする（VPSへの配布は完了している）。
if [[ "$UPLOAD_TO_RELEASES" != "1" ]]; then
  log "Releases への配布は無効です（public リポジトリへモデルを公開しないため）。"
  log "  有効化するには SYNC_MODELS_TO_RELEASES=1。**先にリポジトリの公開範囲を確認すること**。"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  log "[dry-run] GitHub Releases へのアップロードはスキップします"
elif ! command -v gh >/dev/null 2>&1; then
  log "[警告] gh CLI が無いため Releases へのアップロードをスキップしました。"
  log "        CI デプロイ時は古いモデルが配られる可能性があります。"
else
  UP=()
  for name in "${RELEASE_FILES[@]}"; do
    [[ -f "$MODEL_DIR/$name" ]] && UP+=("$MODEL_DIR/$name")
  done
  if [[ ${#UP[@]} -eq 0 ]]; then
    log "[警告] Releases へ上げるファイルが1つも見つかりませんでした。"
  else
    if ! gh release view "$RELEASE_TAG" >/dev/null 2>&1; then
      log "Release '$RELEASE_TAG' が無いため作成します..."
      gh release create "$RELEASE_TAG" --title "本番モデル（最新）" \
        --notes "CI デプロイが取得する本番モデル。sync_models_to_vps.sh が毎回上書きする。" \
        >>"$LOG" 2>&1 || log "[警告] Release の作成に失敗しました。"
    fi
    log "Releases へアップロード中（${#UP[@]}件 → ${RELEASE_TAG}）..."
    if gh release upload "$RELEASE_TAG" "${UP[@]}" --clobber >>"$LOG" 2>&1; then
      log "Releases アップロード OK（${#UP[@]}件）"
    else
      log "[警告] Releases へのアップロードに失敗しました。VPSへのrsyncは成功しています。"
    fi
  fi
fi

log "=== sync_models_to_vps: 完了（本番${N_PROD}件 + vintage${N_VINTAGE}件 = 計${N_TOTAL}件を配布・検証OK） ==="
