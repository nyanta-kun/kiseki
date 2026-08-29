#!/usr/bin/env bash
# 夜間レビューの「課題の取捨」を Claude に書かせて Discord へ出す（2026-08-29 新設）。
#
# 事実を作るのは VPS cron 00:10 の `nightly_review.sh`。ここはその**読み手**で、
# 台帳の発火条件に照らして「今夜やること / 蓄積継続 / 棄却」を仕分けるだけ。
#
# 🔴 **Mac 側で走らせる。** claude CLI が Mac にしか無い。
#    Mac が寝ていれば launchd が起床時に実行する（レポートは VPS に残るので
#    遅れても失われない）。
#
# 🔴 **仕分けの規則はプロンプトに固定する。** ここを緩めると、1日ぶんの
#    ROI に反応して毎晩ルールを足す「後知恵の積み上げ」に戻る。
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DAY="${1:-$(date -v-1d +%F)}"
REMOTE="/home/ysuzuki/GitHub/kiseki/keirin/data/analysis/nightly/${DAY}.md"

REPORT="$(ssh -o ConnectTimeout=20 sekito "cat '$REMOTE'" 2>/dev/null || true)"
if [ -z "$REPORT" ]; then
  echo "[triage] $DAY のレポートが VPS に無い（$REMOTE）。夜間レビューが走ったか確認する。"
  exit 1
fi

PROMPT=$(cat <<'PEOF'
あなたは競輪AI予想「型ラボ」の夜間レビューの読み手です。
以下は本日ぶんの**事実レポート**です。これを読んで、課題の取捨だけを行ってください。

## 守る規則（破ってはいけない）

1. **§1 異常検知だけが「今夜やること」になりうる。** §2〜§5 は1日ぶんでは
   何も言えないので、そこから施策を提案してはいけない。
2. **個別レースの反実仮想を書かない。**「あの相手を1枚広げれば当たった」は
   毎日必ず何件も存在する。拾うと過去にだけ最適化された規則の山ができる。
3. **§2 の参照分布の 5〜95% の内側なら、その数字は情報を持たない。**
   「今日はROIが低い」と書かない。分布のどこかだけを述べる。
4. **§6 の発火条件（100件）に達していないプランについて採否を論じない。**
5. 推測で原因を書かない。レポートに無いことは「レポートからは分からない」と書く。

## 出力形式（この見出しのまま・該当が無ければ「なし」と1行）

**今夜やること**
- （§1 の [NG] のうち、実際に手を打つべきものだけ。何を確認するかまで書く）

**蓄積中（見ているが今は動かさない）**
- （§3〜§5 で目に留まったが、件数が足りず判断できないもの。1〜3行）

**検証候補（発火条件を超えたもの）**
- （§6 で「検証候補」と出たものだけ。無ければ「なし」）

全体で20行以内。日本語。前置き・結びの挨拶は書かない。

--- レポートここから ---
PEOF
)

OUT="$(printf '%s\n%s\n' "$PROMPT" "$REPORT" | claude -p --allowed-tools "" 2>&1)"
if [ -z "$OUT" ]; then
  echo "[triage] Claude の出力が空。Discord へは送らない。"
  exit 1
fi

# Discord へは keirin の正本（`src/notify/discord.py`）から送る。
# チャンネルは results（事実レポートの要約は nightly_review 自身が送っている）。
# 🔴 パスは必ず絶対で書く。launchd は cwd を保証しない（相対の `.venv/bin/python3` は
#    「そんなファイルは無い」で静かに失敗し、Discord へ何も出ないまま終わる）。
printf '**型ラボ 課題の取捨 %s**\n%s\n' "$DAY" "$OUT" \
  | (cd "$REPO" && "$REPO/.venv/bin/python3" -c "import sys; sys.path.insert(0, '.'); \
from src.notify.discord import send; \
sys.exit(0 if send(sys.stdin.read()[:1900], channel='results') else 1)") \
  || echo "[triage] ⚠️ Discord への送信に失敗した（本文は下に出す）"

echo "$OUT"
