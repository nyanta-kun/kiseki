# kiseki - 競馬予測指数システム

## プロジェクト概要
JRA-VAN Data Lab SDKからデータを直接取得し、独自の競馬指数を算出。
オッズとの期待値比較で合理的な馬券購入判断を支援するシステム。
競馬新聞風PWA Webページで指数・期待値を表示する。

## アーキテクチャ（構成B改）
```
Windows (Parallels) - Python 32bit + pywin32
  └─ JV-Link SDK (COM) → 全データ取得・オッズ・リアルタイム通知
  └─ HTTP POST → VPS FastAPI (api.galloplab.com)

VPS (160.251.234.83) - Docker
  ├─ galloplab-backend-1  :8003  FastAPI (kiseki)
  ├─ galloplab-frontend-1 :3002  Next.js (kiseki)
  ├─ sekito-backend-1     :5000  Node.js (sekito)
  └─ sekito-frontend-1    :8080  Vue.js  (sekito)

VPS - PostgreSQL（keiba / sekito / chihou スキーマ共存）
  ├─ keiba.*     — JRA レース・指数・オッズ
  ├─ sekito.*    — 穴ぐさ・外部指数（netkeiba/kichiuma）
  │   ├─ sekito.v_races       — keiba.races の sekito 向けビュー
  │   ├─ sekito.v_entries     — keiba.race_entries + odds の sekito 向けビュー
  │   ├─ sekito.v_horse_runs  — keiba+chihou race_results 統合 view（POG 用・重複排除済）
  │   └─ sekito.mv_horse_runs — 同マテビュー（LISTEN/NOTIFY イベント駆動 REFRESH）
  └─ chihou.*    — 地方競馬（UmaConn経由）
```

## 技術スタック
- Backend: Python 3.12+ / FastAPI / SQLAlchemy 2.0 / Alembic
- Frontend: Next.js 14 (App Router) / Tailwind CSS / shadcn/ui / Recharts
- DB: PostgreSQL (VPS既存) / schema: keiba
- Windows Agent: Python 3.x 32bit / pywin32 / JV-Link COM
- パッケージ管理: uv (Python) / pnpm (Node)
- コード品質: Ruff (Python) / ESLint + Prettier (TS)
- テスト: pytest (Python) / Vitest (TS)

## 開発ルール
- Python: Ruff準拠、型ヒント必須、docstring必須
- TypeScript: strict mode、ESLint準拠
- テスト: 指数計算ロジックは必ずユニットテスト作成
- DB: keiba スキーマ（メイン）+ sekito スキーマ（穴ぐさ等外部データ）を使用。Alembic経由のみでDDL変更
- 環境変数: .env に記載、コードにハードコードしない
- Git: .env は絶対にコミットしない

## DBスキーマ構成
- `keiba.*` — races / race_entries / horses / calculated_indices 等メインデータ
- `sekito.anagusa` — 穴ぐさピック情報（date, course_code, race_no, horse_no, rank A/B/C）
  - course_code は JSPK/JHKD/JFKS/JNGT/JTOK/JNKY/JCKO/JKYO/JHSN/JKKR（sekito独自コード）
  - `has_anagusa` 判定はスコア閾値でなく sekito.anagusa のピック有無で行う
  - `anagusa_rank`（A/B/C）は API の `HorseIndexOut` レスポンスに含まれる（DBには未格納）

## コミュニケーションルール
- **応答は常に日本語で行うこと**

## Agent実装パターン
各指数Agentは `backend/src/indices/base.py` の `IndexCalculator` を継承：
```python
class IndexCalculator(ABC):
    @abstractmethod
    def calculate(self, race_id: int, horse_id: int) -> float: ...
    @abstractmethod
    def calculate_batch(self, race_id: int) -> dict[int, float]: ...
```
- 各Agentは独立してテスト可能であること
- 再算出対応: version番号をインクリメントして管理

## 変更検知・再算出ルール
- 出走取消/除外 → そのレース全馬を再算出
- 騎手変更 → 該当馬の騎手指数 + 全馬の展開指数を再算出
- 斤量変更 → 該当馬のスピード指数のみ再算出

## JRA-VAN データ取得
- TARGETは使用しない。JV-Link SDKを直接Pythonから操作
- Windows側: Python 32bit + pywin32 でCOM経由
- 蓄積系: JVOpen() で出馬表・成績・血統・調教を取得
- 速報系: JVRTOpen() でオッズ全券種・リアルタイム通知を取得
- JV-Linkは同時1接続のみ。TARGET使用時はスクリプトを停止すること
- JVRead 戻り値: 0=EOF, -1=ファイル切り替わり(継続), -3=ダウンロード中(待機), <-3=エラー

### 速報系データスペック（JVRTOpen）
| DataSpec | 内容 | key形式 |
|----------|------|---------|
| `0B12` | 速報成績（払戻確定後）| YYYYMMDDJJRR（12文字: 日付8+場所コード2+レース番号2） |
| `0B11` | 速報馬体重 | YYYYMMDD |
| `0B31` | 速報単複枠オッズ（O1レコード）| レースキー16文字 |
| `0B15` | 速報レース情報（出走取消・騎手変更等）| YYYYMMDD |

- **0B12 は RA/SE/HR を返す。RA には馬場状態・天候・ラップ・前半3F が入っている**
  （2026-08-02 以前は SE/HR しか拾っておらず、`races.condition` が週次の蓄積系取込まで
  NULL のままだったため当日の `going_pedigree_index` が全馬ニュートラルになっていた）
- 発走前 RA（データ区分 1:出走馬名表 / 2:出馬表）は馬場状態等が空。`race_importer` は
  condition/weather/first_3f/last_3f_race/lap_times/finishers_count を COALESCE で
  **非 NULL のときだけ更新**し、確定値を空データで潰さないようにしている
- O1レコードには単勝・複勝・枠連の3種が含まれる（O2=馬連、O3=ワイド、O4=枠連 ではない）
- 複勝オッズは最低倍率（low）を使用（最高倍率は参考値）
- realtimeループは約30秒ごとに全36レースキーで各DataSpecをポーリング

## データパイプライン

```
Windows Agent
  JVRead() → SJIS固定長バイナリ文字列
      ↓
  jvlink_parser.py（parse_ra / parse_se）
      ↓ フィールド抽出（1-indexed バイト位置）
  race_importer.py
      ↓ 型変換（MSST→秒, 斤量→kg）
  PostgreSQL (keibaスキーマ)
      ↓
  SpeedIndexCalculator.calculate_batch()
      ↓ 基準タイム比較・加重平均
  calculated_indices テーブル
```

## パーサー実装上の注意点（jvlink_parser.py）

**SJISエンコーディング**
- JVRead は SJIS バイトを Latin-1 として返す（1 Python文字 = 1 SJISバイト）
- バイト位置 = Python文字列インデックス（ずれなし）
- 漢字フィールドは `raw.encode('latin-1').decode('cp932')` で正しくデコードする
- ASCII数字フィールドは変換不要（`data[start-1:end]` でそのまま使用）

**フィールド位置の読み方（JVDF v4.9仕様書）**
- 仕様書のバイト位置は **1-indexed**
- Python では `data[pos-1 : pos-1+length]` または `data[start-1 : end]` で取得

**共通ヘッダー（RA/SE/AV/JC 共通, pos 1-27）**
- pos 4-11: データ作成年月日（≠ 開催日）
- pos 12-15: 開催年（4桁）
- pos 16-19: 開催月日（4桁）← この2フィールドを結合して実際の開催日を構成
- `race_date = year + month_day` が `Race.date` に格納される値

**走破タイム（MSST形式, pos 339-342, 4バイト）**
- "MSST" = 分(1桁) + 秒(2桁) + 1/10秒(1桁)
- 例: "1345" → 1分34.5秒 → `1*600 + 34*10 + 5 = 945`（0.1秒単位整数）
- DB格納時: `Decimal('94.5')` 秒に変換（÷10）

**後3ハロン（SST形式, pos 391-393, 3バイト）**
- "SST" = 秒(2桁) + 1/10秒(1桁)
- 例: "336" → int 336 → DB格納時: `Decimal('33.6')` 秒

**トラックコード（コード表2009, pos 706-707, 2バイト）**
- 1x = 芝, 2x = ダート, 5x = 障害
- `TRACK_CODE_MAP` で (surface, direction) に変換

**レースID形式（16文字）**
- `year(4) + month_day(4) + course(2) + kai(2) + day(2) + race_num(2)`
- 例: `"2026032205010105"`

## 重要な定数
- 斤量補正: 1kg = 約0.5秒（距離係数で調整）
- スピード指数基準: 平均=50、標準偏差=10
- 期待値購入閾値: 1.2以上

## Auth.js v5 (next-auth@beta) 認証構成

### Next.js 16 + Auth.js の既知の罠（解決済み）

**① route.ts での basePath 手動注入**
Next.js 16 は App Router ルートハンドラに渡す `req.url` から basePath を除去する。
Auth.js は `AUTH_URL` のパス部分を `config.basePath` として使いアクションを解析するが、
除去後の URL では `UnknownAction` → 502 となる。
`frontend/src/app/api/auth/[...nextauth]/route.ts` の `injectBasePath()` でbasePath復元済み。
galloplab.com は basePath なし（`AUTH_URL=https://galloplab.com/api/auth`）のため `injectBasePath()` は no-op。

**② Auth.js セッショントークンは JWE（暗号化）**
Auth.js は `EncryptJWT`/`jwtDecrypt`（A256CBC-HS512）でセッションを暗号化する。
`jwtVerify`（JWS署名検証）は使用不可。`proxy.ts` ではカスタム検証せず `auth()` ラッパーを使う。

**③ proxy.ts での nextUrl.pathname に basePath が混入する**
`auth()` ラッパー内の `reqWithEnvURL` が NextRequest を再構築する際、
`nextUrl.pathname` に basePath が混入する場合がある（サブパス運用時）。
→ `proxy.ts` では pathname 使用前に手動で basePath を除去すること。

**④ AUTH_URL の形式**
`AUTH_URL` は `/api/auth` まで含める形式（例: `https://galloplab.com/api/auth`）。
`/api/auth` まで含めることで `config.basePath` が正しく解決され、コールバック URL が正確に生成される。

## Windows操作（Parallels経由）

**前提**: Windows 11はParallels VMとして動作。Mac側の `Z:\GitHub\kiseki\` にプロジェクトがマウント済み。

### コマンド実行（SSH推奨 — ウィンドウちらつきなし）

**SSH接続を優先して使うこと**。`prlctl exec --current-user` はコマンド実行のたびに ConHost ウィンドウがちらつく。

```bash
# SSH経由でPowerShellコマンド実行（推奨）
ssh windows-vm "powershell -Command \"コマンド\""

# ファイル転送（Mac→Windows）: Zドライブ経由（SSH不要）
# WindowsからZ:\GitHub\kiseki\に直接アクセス可能

# ログ確認
ssh windows-vm "Get-Content C:\kiseki\windows-agent\jvlink_agent.log -Tail 50"
```

SSH接続設定（~/.ssh/config）:
```
Host windows-vm
    # 2026-08-02: mDNS (.local) の名前解決が落ちて ssh も prlctl exec も到達不能になった。
    # Parallels 共有ネットワークの IP を直接使う。変わったら `prlctl list -f` で確認する。
    # mDNS 名で繋ぎたい場合は windows-vm-mdns（同設定で HostName が .local）を使う。
    HostName 10.211.55.6
    User ysuzuki
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
```

**SSH接続できない場合（VM停止中・起動直後等）のフォールバック**:
```bash
# prlctl exec は --current-user なし（SYSTEM権限）を優先、なければ --current-user
prlctl exec "Windows 11" powershell -Command "コマンド"
prlctl exec "Windows 11" --current-user powershell -Command "コマンド"
```

### ログ確認
```bash
# SSH経由（推奨）
ssh windows-vm "Get-Content C:\kiseki\windows-agent\jvlink_agent.log -Tail 50"
# フォールバック
prlctl exec "Windows 11" --current-user powershell -Command "Get-Content 'C:\kiseki\windows-agent\jvlink_agent.log' -Tail 50"
```

### Windows VM再起動
```bash
# ※ prlctl restart は Windows を実際に再起動しない（uptimeがリセットされない）
# 必ず shutdown /r /t 0 を使うこと
prlctl exec "Windows 11" --current-user powershell -Command "shutdown /r /t 0"
# 再起動完了を待つ（約1〜2分）
until prlctl exec "Windows 11" --current-user powershell -Command "Write-Output 'ready'" 2>/dev/null | grep -q ready; do sleep 5; done
```

### Windows Terminal ウィンドウが繰り返し表示される問題（解決済み）

**症状**: `prlctl exec "Windows 11" --current-user` 実行後、Windows 11 で PowerShell ウィンドウが数秒おきに繰り返し表示される。

**根本原因**:
1. Windows 11 のデフォルトターミナルが Windows Terminal に変更されており、`--current-user` で起動したコンソールプロセスが全て Windows Terminal 経由でウィンドウを生成する
2. `prlctl exec` がハングすると Parallels Tools Service（PID 3736 の `prl_tools_service.exe`）が約7秒おきにリトライし、毎回新しいウィンドウが出現する

**恒久対策①（実施済み）**: `set_conhost.py` でデフォルトターミナルを ConHost に変更
```bash
prlctl exec "Windows 11" --current-user powershell -Command "C:\Python312-32\python.exe C:\kiseki\windows-agent\set_conhost.py"
```
`HKCU\Console\%Startup\DelegateFocusToConsoleHost=1` を設定。以後 `prlctl exec --current-user` でウィンドウが出なくなる。

**恒久対策②（実施済み）**: Mac 側 LaunchAgent で 90秒以上ハングした `prlctl exec` を自動 kill
- ファイル: `~/Library/LaunchAgents/com.kiseki.prlctl-watchdog.plist`
- 30秒ごとに実行。経過時間 > 90秒の `prlctl exec` プロセスを自動 kill
- Mac 起動時に自動有効（launchd 管理）
```bash
# 状態確認
launchctl list com.kiseki.prlctl-watchdog
# 手動停止（通常不要）
launchctl unload ~/Library/LaunchAgents/com.kiseki.prlctl-watchdog.plist
```

**ハングプロセスの手動確認と kill（緊急時）**:
```bash
ps aux | grep "prlctl exec" | grep -v grep
pkill -9 -f "prlctl exec"
```

### Windows agent 設定ファイル
- **`.env` の場所**: `C:\kiseki\.env`（`jvlink_agent.py` は `Path(__file__).parent.parent / ".env"` を読む）
- `C:\kiseki\windows-agent\.env` は読まれない（混同注意）
- **BACKEND_URL**: `https://api.galloplab.com`（VPS FastAPI に直接 POST。Mac を経由しないため Mac-VPS 間 RTT を排除）
  - 旧値: `http://YuichironoMacBook-Pro-6.local:8000`（Mac経由。DB書き込みのたびに VPS RTT が発生していた）
  - `10.211.55.2`（Parallels NAT）はWindowsから到達不可なので使用不可
  - `192.168.11.x`（WiFi IP）は変動するので使用不可

### JV-Link / UmaConn 同時接続について（重要）
- **JV-Linkは同一PCで realtime + setup/daily/recent を同時起動できる**（検証済み 2026-04-13）
  - 実際の認証は `HKLM:\Software\WOW6432Node\JRA-VAN Data Lab.\uid_pass\servicekey` で行われる
  - `JRAVAN_SID`（.env）は任意のラベル文字列。認証には無関係（"kiseki"のままでよい）
  - 第2利用キーや `JRAVAN_SID_2` は不要。複数 COM インスタンスが独立動作する
- **UmaConnも同様に realtime + setup を同時起動できる**（検証済み 2026-04-13）
  - 追加API_KEY不要。`NVSetServiceKey rc=-101`（2回目）は正常（既登録の意味）
  - **PC-KEIBA アプリ不要**。NVDTLab.dll（UmaConn SDK）は PC-KEIBA なしで直接動作（2026-04-13 実機確認）
  - 認証は `HKLM\SOFTWARE\WOW6432Node\RateBuster Co.,Ltd\UmaConn\3.5.4.0` で管理（PC-KEIBAのDB設定とは無関係）
- **umaconn_agent realtimeモード 自動管理**（2026-04-18 実装・2026-04-28 安定化強化）
  - Windowsタスクスケジューラ（`kiseki-UmaConn-Realtime`）が毎朝9:00に自動起動
  - 自動停止: 最終レース発走+90分 or 21:30ハードストップ（先に来た方）
  - ウォッチドッグ: NVRTOpenハングを600秒で検知 → `os._exit(1)` 強制終了 → 翌朝9:00に自動復帰（umaconn_agent）
  - タスク状態確認: `ssh windows-vm "schtasks /query /tn 'kiseki-UmaConn-Realtime' /fo list"`
- **realtime 安定化のためのバックアップ・監視タスク**（2026-04-28 追加）
  - `kiseki-UmaConn-FetchResults`: **5分おき** (10:00-22:30) に `umaconn_agent.py --mode fetch-results --fetch-date {today}` を自動実行
    - realtime の 0B12 worker が止まっても結果取得を確実化
    - スクリプト: `C:\kiseki\windows-agent\run_umaconn_fetch_results.vbs`
  - `kiseki-UmaConn-Watchdog`: **5分おき** (9:00-22:30) に realtime を監視（2026-04-30 から jvlink も対象・2026-08-02 にストール検知を追加）
    - **[1] 不在**: プロセスが無ければ `kiseki-UmaConn-Realtime` / `kiseki-JVLink-Realtime` を実行
    - **[2] ストール**: プロセスは生きているが `data\realtime_heartbeat_{jvlink,umaconn}.txt` が
      **15分以上更新されていない**場合、taskkill してから再起動する
      - Why: 2026-08-02 に JVRTOpen が COM レベルでハングし、**プロセス内ウォッチドッグスレッドごと
        凍結**して `os._exit(1)` が発火せず、約95分オッズ取得が死んだ。生存確認だけでは検知できない
      - ハートビートは各エージェントのウォッチドッグスレッドが30秒ごとに書き出す
        （`write_heartbeat_file()`）。ファイルが無い場合はストール判定をスキップ（旧版との互換）
    - スクリプト: `C:\kiseki\windows-agent\run_realtime_watchdog.vbs`
    - ログ: `C:\kiseki\windows-agent\watchdog.log`
  - `kiseki-EOD-Cleanup`: **毎日 23:45** に以下を強制終了（2026-04-30 新設・2026-08-02 拡張）
    - [A] `jvlink_agent` / `umaconn_agent` の `--mode realtime`（起動日時を問わず）
    - [B] 同エージェントの**モードを問わず前日以前に起動したプロセス**（ゾンビ掃除）
      - 2026-08-02 の障害: `--mode daily` が 7/16 から17日間ハングし JV-Link を占有、
        当日の realtime がオッズを1件も取得できなかった。旧実装は realtime しか掃除しなかった
      - 同日起動の正規バックフィル（`jvlink_historical` 等）は巻き添えにしない
    - スクリプト: `C:\kiseki\windows-agent\run_eod_cleanup.vbs`
    - 翌朝 9:00 起動が常にクリーンな状態になるための safety net（hung プロセスの跨ぎ防止）
  - `run_jvlink_realtime.vbs` / `run_umaconn_realtime.vbs` は冪等（同種 realtime が既に走っていればスキップ）。watchdog × daily 9:00 の二重発火で多重生成しない
  - **Why**: 4/27 の mitmproxy 停止由来 ProxyError 連発 + 4/26 jvlink_agent watchdog 600s誤発火事例（626/643秒）+ 2026-04-30 観測の jvlink_agent ゾンビ多重起動（4/28・4/29 の 9:00 起動分が残存し COM 競合）への対応
  - **jvlink_agent WATCHDOG_TIMEOUT**: 600s → **1800s**（2026-05-02）。レース間の30分待機ループで誤発火していたため延長
  - **Windowsシステムプロキシは無効化済**（`netsh winhttp reset proxy` 完了）。再有効化する場合はバックエンドAPI到達不可になるので注意
- **umaconn_agent の起動はデスクトップセッションが必須**（2026-04-21 確認）
  - NVDTLab.dll はシステムトレイアイコン初期化のためデスクトップセッションが必要
  - SSH経由の直接起動は `シェル通知アイコンが削除できません` エラーで初期化失敗する
  - **手動起動は `kiseki-RunAdhoc` タスクスケジューラ経由を使うこと**（ちらつきなし・`prlctl exec` 不要）
  ```bash
  # ---- kiseki-RunAdhoc 経由の起動方法（推奨・ちらつきゼロ） ----
  # adhoc_cmd.txt に「スクリプト名 + 引数のみ」を書く（cd不要・pythonw不要）
  # run_adhoc.vbs が pythonw.exe で直接起動（cmd.exe を経由しない → コンソールウィンドウなし）

  # recent モード（レース終了後の当日エントリ・結果取得）
  ssh windows-vm "echo umaconn_agent.py --mode recent --from-year 2026 > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"

  # fetch-results モード（指定日の成績を0B12で取得）
  ssh windows-vm "echo umaconn_agent.py --mode fetch-results --fetch-date 20260421 > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"

  # fetch-odds モード（指定日のオッズを0B31で取得）
  ssh windows-vm "echo umaconn_agent.py --mode fetch-odds --fetch-date 20260421 > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"

  # ログ確認
  ssh windows-vm "powershell -Command \"Get-Content 'C:\\kiseki\\windows-agent\\umaconn_agent.log' -Tail 10\""
  ```
  - **仕組み**: `kiseki-RunAdhoc` タスクは `InteractiveToken` フラグでデスクトップセッション内で `wscript.exe` を実行
  - `run_adhoc.vbs` が `adhoc_cmd.txt` の1行目を読み取り、**`pythonw.exe`（コンソールウィンドウなし）** で直接起動
  - `cmd.exe` を経由しないため Coherence モードでもちらつきゼロ
  - `prlctl exec --current-user` および `python.exe`（コンソールあり）は使用禁止

### エージェント運用の落とし穴（2026-08-02 実地で踏んだもの）

**① SSH 経由の `Start-Process` はセッション断でプロセスが道連れになる**
`ssh windows-vm "powershell -Command Start-Process pythonw ..."` で起動した realtime は
**SSH コマンドが戻った瞬間に死ぬ**（1サイクルだけログを出して消える）。
常駐させるものは必ず **タスクスケジューラ経由**で起動すること:
```bash
ssh windows-vm 'schtasks /run /tn kiseki-JVLink-Realtime'
# または RunAdhoc（adhoc_cmd.txt 経由）
```

**② プロセス数の確認は `Name='pythonw.exe'` で必ず絞る**
`Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match "jvlink_agent.py"}` は
**そのクエリを実行している cmd.exe / powershell.exe 自身にマッチする**（コマンドラインに
文字列が含まれるため）。常に2件多く見え、「多重起動している」と誤診する。正しくは:
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "pythonw.exe" -and $_.CommandLine -match "jvlink_agent" }
```

**③ 起動タスクとウォッチドッグの同時発火で多重起動しうる**
ランチャ VBS の「実行中か調べる→起動する」は check-then-act の競合。
`data\launcher_{jvlink,umaconn}.lock` の更新時刻で 60 秒以内の二重起動を抑止し、
起動4秒後に重複を検出したら最古の1本だけ残すようにしている（2026-08-02 追加）。

### jvlink_agent.py 起動
※ **jvlink_agent は必ず RunAdhoc（kiseki-RunAdhoc タスクスケジューラ）経由で起動すること。**
※ SSH + Start-Process では JVDTLab.dll がデスクトップセッションを取得できず JVOpen が無限ブロックする（2026-04-25 確認）。
※ RunAdhoc は InteractiveToken でデスクトップセッション内に pythonw.exe を起動するためウィンドウちらつきなし。

```bash
# ---- kiseki-RunAdhoc 経由の起動方法（全モード共通） ----
# adhoc_cmd.txt に「スクリプト名 + 引数」を書き、schtasks /run で起動する

# recentモード（今週分データ取得。完了後に自動終了）
# ⚠️ option=2: 今週分のみ取得。数週間前のデータは届かない。
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode recent --from-year 2026\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# fix-raceモード（指定日以降のRACEデータ差分取得。過去欠損修復用）
# ✅ option=1: from_time が有効。JVOpen は数分で完了。
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode fix-race --from-date 20260425\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# realtimeモード（オッズ・成績・出走取消を30秒間隔でポーリング、常駐）
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode realtime\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# setupモード（全過去データ取得。初回のみ）
# ⚠️ option=4: from_time を無視して全期間スキャン。JVOpen呼び出し自体が数時間ブロックする。
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode setup\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# ログ確認
ssh windows-vm "powershell -Command \"Get-Content 'C:\\kiseki\\windows-agent\\jvlink_agent.log' -Tail 20\""
```

### jvlink_agent トラブルシューティング

**JVOpen が無限ブロックする場合**（JVLinkAgent が外部接続を全くしない）:
```bash
# Step 1: 残存 JVNextCore プロセスを確認・kill
ssh windows-vm "powershell -Command \"Get-Process JVNextCore -ErrorAction SilentlyContinue | Select-Object Id,CPU\""
ssh windows-vm "powershell -Command \"Stop-Process -Name JVNextCore -Force -ErrorAction SilentlyContinue\""

# Step 2: stale event ディレクトリをクリア
ssh windows-vm "powershell -Command \"Remove-Item 'C:\\ProgramData\\JRA-VAN\\Data Lab\\event\\*' -Recurse -Force -ErrorAction SilentlyContinue\""

# Step 3: JVLinkAgent を再起動
ssh windows-vm "powershell -Command \"Restart-Service JVLinkAgent\""

# Step 4: RunAdhoc 経由で再実行
```

**原因**: 以前の JVOpen 異常終了時に JVNextCore が残存し JVLinkAgent を占有する。複数の JVNextCore が残っていると（特に CPU が 100秒以上のもの）、新しい JVOpen リクエストを処理できなくなる。

### JVOpen option の選択指針（重要）

| 目的 | モード | option | from_time | 所要時間 |
|------|--------|--------|-----------|----------|
| 過去特定日の欠損修復 | `fix-race --from-date YYYYMMDD` | 1 | **有効** | 数分 |
| 今週・直近分の取得 | `recent` | 2 | 今週分のみ | 数分 |
| 全期間の初回取得 | `setup` | 4 | **無視** | 数時間 |

- **`--mode setup` は初回セットアップ専用**。欠損修復・再取得には使わないこと。
- **`--mode fix-race --from-date YYYYMMDD`** が過去データ修復の標準手順。
- `--mode recent` は今週以前のデータは取得不可（option=2の制約）。

### blod-um モード 仕様（pedigrees.sire NULL 補完）

**目的**: 2022年以前の馬の `pedigrees.sire` が NULL になっている問題を解消する。

**根本原因**: BLOD の SK レコードが旧形式 sire_code（`20xxx`/`40xxx`）を持ち、`breeding_horses` に存在しないため名前解決できない。UM レコード（競走馬マスタ）は祖先名をテキストで直接保持するため、breeding_code の解決不要。

**DataSpec の選択根拠**（重要）:
- `BLOD` DataSpec には UM レコードが**含まれない**（BT/HN/SK のみ）
- UM レコードは `DIFF` / `DIFN` / `TCOV` / `RCOV` にのみ存在（仕様書 p.20・`jvlink_parser.py` L931 コメントで確認済み）
- **`--mode blod-um` は `DataSpec="DIFN"` + `option=1`（差分モード）を使用**
  - `DIFF`/`DIFN` + `option=4`（セットアップ）はセットアップファイルに UM レコードが**含まれない**ため使用不可（DIFF=111秒・DIFN=82秒で JVOpen は完了するが UM=0件。2026-04-20 確認）
  - UM レコードは UMFW ファイルだけでなく BN/CH/KS/RA/SE 等あらゆるファイルに散在するため、ファイル名フィルタは不要

**JVOpen パラメータ**:
```
JVOpen("DIFN", from_time="20000101000000", option=1, ...)
```
- `from_time="20000101000000"` で全期間の差分を取得
- `option=1` = 通常差分モード。数分で JVOpen が完了する

**正常動作フロー**:
```
JVOpen("DIFN", "20000101000000", option=1)
  → 数分で完了
JVRead ループ（max_errors=1000）:
  → 各ファイルから rec_id="UM" のレコードのみ抽出
  → BRFW 等 rc=-402 エラーファイルは completed マークしてスキップ
  → completed.add(filename) でメモリ上の完了セットも更新（同セッション内の重複処理を防止）
POST /api/import/bloodlines（batch_size=200）
  → pedigree_importer.import_records()
  → INSERT INTO pedigrees ... ON CONFLICT DO UPDATE SET sire=COALESCE(pedigrees.sire, EXCLUDED.sire)
  → 既存の非NULLは保持、NULL のみ補完
```

**実装上の注意**（`jvlink_agent.py` `_run_blod_um`）:
- `batch_size=200`：nginx 1MB 制限対策（2000 だと HTTP 413）
- `max_errors=1000`：BRFW ファイルの rc=-402 多発で中断しないよう緩和
- `retry_pending` を起動時に呼ぶ（`link_common.retry_pending` もバッチ分割対応済み）
- `fetch_stored_data` の `skip_file_fn=lambda fn: fn in completed` でスキップ

**実行結果（2026-04-20 完了）**:
- UM レコード 11,043 件 DB 反映済み（7,917 ファイル処理完了）
- 完了済みファイルは `data/completed/BLOD_UM.txt` に記録

**進捗確認**:
```bash
prlctl exec "Windows 11" --current-user powershell -Command "Get-Content 'C:\kiseki\windows-agent\jvlink_agent.log' -Tail 20"
```

**完了確認（DB）**:
```sql
SELECT COUNT(*) FROM keiba.pedigrees WHERE sire IS NULL;
```

**再実行が必要な場合**:
```bash
prlctl exec "Windows 11" --current-user powershell -Command "
  Get-WmiObject Win32_Process | Where-Object { \$_.Name -eq 'python.exe' } | ForEach-Object { \$_.Terminate() }
  Start-Sleep 3
  Start-Process 'cmd.exe' -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode blod-um' -WindowStyle Hidden
"
```
- 全件再取得する場合は `data/completed/BLOD_UM.txt` を削除してから実行

### bldn-full モード 仕様（breeding_horses + pedigrees.sire 全歴史補完）

**目的**: 2023年以前の馬の `pedigrees.sire` が NULL になっている問題を解消する。

**根本原因**:
- 旧形式 BLOD の SK sire_code（`20xxx`/`40xxx`）は breeding_horses に存在しない
- BLDN の新形式 SK sire_code（`1110000xxx` 等）も、HN（繁殖馬マスタ）が breeding_horses に未登録だと解決できない
- 通常の `--mode bldn`（from_time="20230801000000"）は差分ファイルのみ取得し、累積マスタ HN を含まない

**解決策**: `from_time="20000101000000"`（BLDNサービス開始前）で JVOpen すると、JV-Link はサーバー保持期間外とみなし、**累積マスタファイル（571ファイル）のみ**を返す。これには1986年以降の全 HN + SK が含まれる。

**JVOpen 動作の重要な挙動**:
- `from_time` が BLDN 開始（2023-08-08）**以前** → 累積マスタ 571 ファイルのみ（DL=0、6〜8分）
- `from_time` が BLDN 開始**以降** → 累積マスタ + 差分ファイル（30,919件、~6分）
- いずれも JVOpen は正常完了（rc=0）

**JVOpen パラメータ**:
```
JVOpen("BLDN", from_time="20000101000000", option=4, ...)
```

**正常動作フロー**:
```
Step1: JVOpen("BLDN", "20000101000000", option=4) → 571ファイルを全読み（キャッシュ確定）
Step2: JVOpen("BLDN", "20000101000000", option=4) → HN/SK のみ抽出してDB反映
  → 大きな累積 HNVM ファイル（113,530件など）→ breeding_horses 登録
  → 年度別 HNVM + SKVM ペア → breeding_horses → pedigrees.sire 解決
POST /api/import/bloodlines（batch_size=2000）
  → INSERT INTO breeding_horses ... ON CONFLICT DO UPDATE
  → INSERT INTO pedigrees ... ON CONFLICT DO UPDATE SET sire=COALESCE(pedigrees.sire, EXCLUDED.sire)
```

**完了済みファイルは** `data/completed/BLDN_FULL.txt` に記録。再実行時は削除不要（スキップ対象）。

**実行結果（2026-04-20 完了）**:
- 569 ファイル / 588,768 件 DB 反映済み
- breeding_horses: 170,315 → 314,246 件（+143,931件）
- pedigrees.sire NULL: 73,159 → 3,152 件（**95.7% 削減**）
- sire 解決済み: 77,924 / 81,076 件（96.1%）
- 残り 3,152 件は外国種牡馬・父不明など構造的に解消困難

**実行コマンド**:
```bash
prlctl exec "Windows 11" --current-user powershell -Command "
  Start-Process -FilePath 'cmd.exe' \`
    -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode bldn-full' \`
    -WindowStyle Hidden -PassThru
"
```

**完了確認（DB）**:
```sql
SELECT COUNT(*) FROM keiba.pedigrees WHERE sire IS NULL;
```

**再実行が必要な場合**:
全件再取得するには `data/completed/BLDN_FULL.txt` を削除してから実行。

### JV-Next 1403 (DM) 取得 — 2 つのオーケストレーター

DM (タイム型・対戦型指数) の取得には 2 つの方式がある:

| 方式 | スクリプト | UIアクセス | K=0 primary venue | 推奨度 |
|------|---------|---------|------------|------|
| **Protocol版** (新) | `backend/scripts/protocol_dm_orchestrator.py` | 不要 | ✅ 福島・新潟・中京・小倉 OK | 🟢 推奨 |
| **Denma版** (旧) | `backend/scripts/dm_fetch_orchestrator.py` | CEF クリック必要 | ❌ 取得不可 (cross-pairing 仕様) | 🟡 fallback |

#### Protocol 版 (新・基本)
JV-Next の `POST /Browsing/GateServlet` プロトコルを直接利用 (DATA=`05900403{date}{CC}{NN}` で 1403 取得)。

```bash
cd backend
# DB駆動: 過去30日内の未取得レースを自動検出して取得
.venv/bin/python scripts/protocol_dm_orchestrator.py --from-db

# K=0 primary venue を集中バックフィル (福島・新潟・中京・小倉)
.venv/bin/python scripts/protocol_dm_orchestrator.py --from-db --courses 03,04,07,10 --since 20230101

# 日付指定
.venv/bin/python scripts/protocol_dm_orchestrator.py --dates 20260419,20260412 --courses 03,06,09

# DRY RUN
.venv/bin/python scripts/protocol_dm_orchestrator.py --from-db --dry-run
```

**動作**:
1. DB から未取得日付・場リストを抽出
2. Windows-side `protocol_dm_pipeline.py` を SSH 経由で実行
3. パイプライン内で:
   - JV-Next 起動状態確認 (停止時は起動)
   - session KEY 確認 (probe 失敗時は pktmon で再抽出 ~30秒)
   - DATA=`05900403YYYYMMDDCCNN` で全レース fetch
   - 永続ストア (`C:\kiseki\data\dm_1403`) に zlib 圧縮保存
   - `jvnext_dm_importer.py --all` で DB 反映

**所要時間** (実測): 1日あたり ~65秒 (KEY refresh 30秒 + 12レース fetch ~3秒)

**詳細**: `memory/jvnext_protocol.md` に完全プロトコル仕様。

#### Denma 版 (旧・fallback)
従来の Denma ページから denm リンクをクリックする方式。secondary venue / K=1単独場のみ対応:

```bash
.venv/bin/python scripts/dm_fetch_orchestrator.py --from-db
```

K=0 primary venue (福島・新潟・中京・小倉) は cross-pairing 仕様で取得できない (Denma リンクは大JJ secondary 側のみ DL される)。  
→ K=0 primary には **必ず Protocol 版を使うこと**。

### JV-Link rc=-303 修復
rc=-303（ファイル存在確認エラー）= JVNextCoreがJRA-VANサーバー確認に失敗。
**最も確実な対処**: Windows VMの再起動。
```bash
# Step 1: 修復スクリプト試行（JVNextCoreをkill+テスト）
prlctl exec "Windows 11" --current-user powershell -Command "cd C:\kiseki\windows-agent; python fix_jvlink_303.py"

# Step 2: それでも-303が続く場合はVM再起動
# ※ prlctl restart は実際に再起動しない。必ず shutdown /r /t 0 を使うこと
prlctl exec "Windows 11" --current-user powershell -Command "shutdown /r /t 0"
until prlctl exec "Windows 11" --current-user powershell -Command "Write-Output 'ready'" 2>/dev/null | grep -q ready; do sleep 5; done
# 再起動後、jvlink_agent --mode setup を再実行
```

## 指数バックフィル運用ルール

### 対象期間の方針
- **バックフィル対象は「実施日から3年前」を起点とする**
  - 例: 2026-04-13 実施 → `--start 20230413`
  - 3年分あれば ROI シミュレーション・重み最適化・馬のキャリア追跡に十分
  - 月日が進んだ場合も同様に「実施日 - 3年」で計算すること
- 3年以前の古いデータは優先度低（必要時のみ別プロセスで追加実行）
- **理由**: 全期間（2019〜）処理には約12日かかり、直近データが遅れるため
  - 4並列で約5〜6時間で完了（リソース実測: CPU ~60%・RAM ~700MB/16GB）

### 実行コマンド（日付は実施時点で計算）
```bash
cd backend

# 開始日を「今日 - 3年」で動的に計算
START=$(python3 -c "
from datetime import date
d = date.today().replace(year=date.today().year - 3)
print(d.strftime('%Y%m%d'))
")
TODAY=$(python3 -c "from datetime import date; print(date.today().strftime('%Y%m%d'))")

# 3年分を4等分
Q1=$(python3 -c "
from datetime import date
s=date.today().replace(year=date.today().year-3); t=date.today(); span=t-s
print((s+span//4).strftime('%Y%m%d'))
")
Q2=$(python3 -c "
from datetime import date
s=date.today().replace(year=date.today().year-3); t=date.today(); span=t-s
print((s+span//2).strftime('%Y%m%d'))
")
Q3=$(python3 -c "
from datetime import date
s=date.today().replace(year=date.today().year-3); t=date.today(); span=t-s
print((s+span*3//4).strftime('%Y%m%d'))
")
echo "対象: $START 〜 $TODAY  (Q1=$Q1, Q2=$Q2, Q3=$Q3)"

# 4分割並列バックフィル（約5〜6時間で完了）
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $START --end $Q1 --skip-existing > /tmp/v15_p1.log 2>&1 &
echo "P1 PID: $! (${START}〜${Q1})"
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $Q1 --end $Q2 --skip-existing > /tmp/v15_p2.log 2>&1 &
echo "P2 PID: $! (${Q1}〜${Q2})"
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $Q2 --end $Q3 --skip-existing > /tmp/v15_p3.log 2>&1 &
echo "P3 PID: $! (${Q2}〜${Q3})"
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $Q3 --end $TODAY --skip-existing > /tmp/v15_p4.log 2>&1 &
echo "P4 PID: $! (${Q3}〜${TODAY})"
```

### 進捗確認
```bash
ps aux | grep calculate_indices | grep -v grep | awk '{print "PID:"$2, "CPU:"$3"%", "RSS:"$6/1024"MB"}'
for i in 1 2 3 4; do echo "=P${i}="; grep -E "\[.*\].*頭 \(累計" /tmp/v15_p${i}.log | tail -2; done
```

### パフォーマンス改善（2026-04-13 適用済み）
- `_bulk_upsert_for_race()`: 馬ごと N 往復 → レース1回の SELECT + bulk add_all
- `asyncio.gather` レース並列（セマフォ4）+ `SireStatsCache` クラス変数共有
- 実測効果: 旧比 **約5倍高速**（シングル51h → 4プロセス並列で約5〜6h）
- スループット: 約12,000件/時間/プロセス
- `_upsert()` はリアルタイム単一馬更新用として引き続き保持

## 開発マイルストーン
- MS1: 環境構築 + データ取込 + スピード指数CSV出力
- MS2: コース適性 + 枠順バイアス + 総合指数CSV
- MS3: 騎手・展開・血統・ローテーション指数
- MS4: パドック・調教 + 本格バックテスト
- MS5: リアルタイム対応 + 変更検知
- MS6: 競馬新聞Web (PWA)
- MS7: IPAT連携 + 収支管理
- MS8: 全自動投票 + 継続最適化

## DM 自動収集 LaunchAgent

中央レース情報が DB に入った後、対応する DM 指数を自動取得する。

- **LaunchAgent**: `~/Library/LaunchAgents/com.kiseki.dm-auto-fetch.plist`
- **スクリプト**: `scripts/dm_auto_fetch.sh`
- **オーケストレーター**: `backend/scripts/protocol_dm_orchestrator.py --from-db --courses 01..10`
- **スケジュール**: 12:00 / 14:00 / 18:00 / 22:30 (毎日) + 8:00, 11:00 (土日)
- **多重起動防止**: `/tmp/dm_auto_fetch.lock`

```bash
# 状態確認
launchctl list com.kiseki.dm-auto-fetch
# 手動実行
/Users/ysuzuki/GitHub/kiseki/scripts/dm_auto_fetch.sh
# ログ
tail -f /Users/ysuzuki/GitHub/kiseki/logs/dm_auto_fetch.log
```

## DM × 穴ぐさ × 既存指数 シグナルタグ

`backend/src/indices/dm_signals.py` で 7 種類のシグナルを馬ごとに付与。
バックテスト実証 (3年・8,618レース・99.0%カバレッジ):

| タグ | 条件 | 勝率 | ROI | n |
|------|------|------|------|---|
| 🔥三冠一致 | base=1 ∧ time=1 ∧ battle=1 | 39.1% | 84.9% | 1622 |
| ⭐高得点鉄板 | composite≥60 ∧ battle≥65 ∧ **composite順位≤2** | 46.5% | 101.2% | 86 |
| 🏆穴ぐさDM | anagusa∈{A,B} ∧ battle=1 ∧ 人気≥5 | 10.2% | **188.8%** | 49 |
| ⚡DM大穴 | battle=1 ∧ 人気≥7 ∧ battle≥65 | 7.6% | 154.0% | 184 |
| ⚡DM高オッズ | battle=1 ∧ オッズ≥10 ∧ time≤2 | 9.0% | 130.0% | 156 |
| 💎anagusa+DMtime | anagusa=A ∧ time=1 | 9.4% | 103.5% | 106 |
| ❌人気下振れ | 人気≤3 ∧ base≥4位 ∧ battle≥4位 | 15.3% | 73.9% | 3563 |

上表に加えて **コース/セグメント別 deny フィルタ**（低信頼セグメントで非発動、2026-06-07）が
実装されている: 三冠一致=福島/阪神/京都・芝マイル・ダ中距離、穴ぐさDM=東京/小倉/札幌/阪神ほか、
DM高オッズ=芝マイル、人気下振れ=福島/小倉/阪神/京都。詳細は `dm_signals.py` 参照。
また**出走取消・発走除外馬はシグナル判定・順位計算の母集団から除外**される
（取消馬のDM欠損で「1頭でもNULL→レース全馬シグナルなし」が誤発動するのを防ぐ）。

API レスポンス: `HorseIndexOut.dm_signals: list[str]` (`/api/races/{id}/indices`)
recommendations 用: `recommender.py` で各馬に付与。
ベース指数 (composite_index) はオッズ非依存のまま。シグナルはオッズ・人気・anagusa を組み合わせたフロント手前レイヤで生成する。
フロントのバッジ定義は `frontend/src/components/DmSignalBadges.tsx`（共通モジュール・単一定義）。

## JRA 推奨エンジン（`/api/recommendations` = 的中重視 hit_tier 方式）

2026-06-05 から JRA 推奨は **hit_tier 方式**（1レース1推奨 = 指数1位馬 + 的中率tier）。
OOS検証（`scripts/jra_verify_signals.py`）で「価値(ROI>1)」を謳うバッジ（旧sweet_spot集約・
super_buy・DM穴・高得点鉄板）は全て OOS 脆弱と判明したため、推奨本体は的中重視に再定義し、
価値系は `value_candidates`（妙味候補・収支保証なし）の注記へ降格した。

**tier（=recommend_rank、OOS test 1位馬実績）**:
| tier | 条件 | 実績 | bet |
|---|---|---|---|
| S 鉄板 | 指数1位が断然人気（単勝<1.5） | 勝率67% / 複勝93% | 単勝 |
| A 信頼軸 | confidence_score ≥ 80 | 勝率34% / 複勝71% | 単勝 |
| B 複勝圏 | confidence_score ≥ 65 | 複勝64% | 複勝 |
| C 混戦 | 上記以外 | — | 推奨しない（見送り） |

**実装**:
- `backend/src/services/recommender.py::build_hit_tier_recommendations()` 推奨本体
- `backend/src/services/recommender.py::_value_badges()` 妙味候補バッジ（DM signals / 穴ぐさ非1位 / 外部指数穴馬）
- `backend/src/api/recommendations.py::get_recommendations` 都度算出（DB保存なし・60秒プロセス内キャッシュ）

**個別馬の sweet_spot 表示**（推奨エンジンとは別・`/indices` 専用）:
- `backend/src/indices/buy_signal.py::is_sweet_spot()` — 単勝≥10 ∧ EV∈[1.2,5.0] ∧ バッジ ∧ k≤2
- `backend/src/api/races.py` `HorseIndexOut.is_sweet_spot` に付与、`IndicesTable.tsx` で該当馬名を**赤字**表示
- 3年バックテスト 単ROI 1.182 だが OOS 検証では脆弱（memory `jra_signal_verification.md`）。
  表示バッジとしてのみ維持し、推奨エンジンには使わない

**重要な観察**（is_sweet_spot の EV ゲート設計根拠）:
- EV ≥ 4 で実勝率がモデル予測の 4.8〜6.5倍下振れ → 上限 5.0 必須
- k=3 で単ROI 0.935 → k≤2 制約で混戦レース除外

**地方競馬は対象外**（下記の別系統）。

### 総合指数 v27（順位回帰 + 着外率合成・2026-08-02）

`COMPOSITE_VERSION = 27`。v26 の「LGB LambdaRank 0.3 + v24線形和 0.7」を廃止し、
**レース内正規化着順の回帰（reg_rank）** を土台に **着外率ヘッド** で下位を押し下げる方式へ転換。

```
z_blend         = z( z(-reg_rank) - 0.5 * z(out_probability) )
composite_index = 50 + z_blend * 55.3 * sd_race(reg_rank)   → clip(0, 100)
```

- モデル: `models/jra_reg_rank_lgb.txt`（学習 `scripts/train_jra_reg_rank.py`）+ `models/jra_out_rate_lgb.txt`
- 全期間バックフィル: `scripts/inference_v27.py`（v26 行のサブ指数を流用・冪等）
- honest test 2026-01〜08 (2,046R) の v26 比:
  1位馬 勝率 27.08→28.40% / 複勝率 59.38→61.00% / NDCG@3 0.4975→0.5071 /
  レース内 Spearman 0.4783→0.5094 / 3着内馬を下位30%に沈める率 10.43→9.29%
- **min-max で 15〜85 に固定してはいけない**。composite のレース内ばらつきは
  `confidence.py::calculate_race_confidence` の分散・指数差スコアの入力であり、
  固定すると tier S が 19.0%→30.2% に膨張する。上式のとおり幅は reg_rank の実ばらつきに比例させる
- v27 は 1位-2位差が v26 比 約0.75倍に縮むため、JRA 側は `JRA_GAP_FULL_SCORE = 6.0` を
  `calculate_race_confidence(gap_full_score=...)` に渡してスケール差を吸収する
  （地方は `DEFAULT_GAP_FULL_SCORE = 10.0` のまま。実測で tier 分布が v26 と一致）
- **ROI は改善しない**（控除率の壁）。価値は表示順・足切り・推奨tierの精度に限定される

⚠️ **バックフィル済みの過去分は in-sample**。本番モデルは全期間 refit のため、
DB の composite_index / out_probability を使って過去の ROI・的中率を評価してはいけない。
honest 評価は walk-forward スクリプト（`scripts/jra_rank_quality_review.py` 等）で行う。

### 指数（特徴量）の死活監視

`scripts/check_feature_health.py` が月次ばらつき・欠損率から DEAD / SHIFT / SPARSE を検出する。
既知の問題:
- `paddock_index`: 上流 netkeiba スクレイプが 2026-05 に停止。**v26 学習期間中も全月 sd=0** でモデル寄与ゼロ
- `going_pedigree_index`: **レース当日に算出すると必ず全馬 50**。算出時点で `races.condition` が
  未確定（重/不でない）ため早期 return する。後日バックフィルしたレースだけ値が入る
- `rebound_index`: 2026-04 以降ばらつきが単調減少（sd 2.79→0.57）。要調査

### 着外率による足切り（Web グレーアウト・2026-08-02）

Web の「足切り候補」グレーアウトは **総合指数のトップ差ルールを廃止し、着外率（6着以下確率）** に置き換えた。

- モデル: `backend/models/jra_out_rate_lgb.txt`（LightGBM binary・特徴量は v26 と同一34列・**オッズ/人気は不使用**）
- 学習: `backend/scripts/train_jra_out_rate.py` / バックフィル: `backend/scripts/backfill_jra_out_probability.py`
- 保存先: `keiba.calculated_indices.out_probability`（本番算出は `composite.py` が毎回同時に書き込む）
- 閾値: `composite.py::OUT_PROB_CUTOFF = 0.80` → API が `HorseIndexOut.is_cut_off` として返す（**判定の単一真実源**）
- honest 検証（test 2026-01〜08 / 2,046R）: 除外30% ・除外馬の実着外率 88.6% ・**1着取りこぼし 4.8%**
  （旧・指数差ルールは 除外55% で 1着を 16.8% 取りこぼしていた）。2025年独立追試でも同一挙動＝較正が安定
- **着外率は ROI を作らない**（全帯 0.54〜0.84）。足切り・見送り判定にのみ使うこと。
  詳細: memory `jra_out_rate_3head_verification_2026_08_02.md`
- `inference_v26.py` は out_probability を更新しないため、実行後は同期間で backfill を流すこと

詳細: memory `recommendations_feature.md` / `jra_signal_verification.md`

## 地方競馬 総合指数 v13（min-max 廃止 + 全期間バックフィル・2026-08-02）

`CHIHOU_COMPOSITE_VERSION = 13`。**モデルと44特徴は v12 と完全に同一**
（`chihou_prod_lgb.v12_44feat.txt` をそのまま使用）。変えたのは 2 点だけ。

**① composite のスケール（min-max 15-85 を廃止）**
```
composite = clip(50 + CHIHOU_INDEX_SCALE(=40) * (p_top3 − レース内平均), 0, 100)
```
- 旧方式はレース内 min-max → 15〜85 固定のため、**全レースで幅がぴったり 70.00（sd=0.000）**
  になり、`confidence.calculate_race_confidence` の
  **分散スコア(25点)が 100% のレースで満点＝完全な定数**・指数差スコア(40点)も 63% が満点。
  100点中65点が情報を失い、**97% のレースが tier S** に張り付いていた（DB 実測 6,496R）
- **min-max は禁止**。JRA v27 も同じ理由で禁止している（memory `jra_rank_quality_redesign_2026_08_02`）
- tier 較正は表示スケールと分離し `confidence.CHIHOU_GAP_FULL_SCORE=12.0` /
  `CHIHOU_DISPERSION_FULL_SCORE=16.0` で吸収する。
  **片方（C か閾値）だけ変えると tier 分布が壊れる。必ず比例させること**
- honest 検証（2026-01〜06 / 6,418R）の tier 分布と1位馬勝率:
  旧 S 97%/A 3%/B 0%/C 0%（分離不能） → 新 **S 22%/A 45%/B 26%/C 6%（S 58.2% → C 33.2%・単調）**
- **min-max も中心化線形も単調変換なのでレース内の順位は一切変わらない**。変わるのは tier だけ

**② 全期間バックフィル（v10 → v13）**
DB の履歴は **version=10（30特徴・市場特徴なし）で止まっており**、本番が serve している
v12（44特徴）が過去に一度も適用されていなかった。honest 比較（train ≤2025-06 / test 2026-01〜06）:

| 指標 | v10相当(DB実測) | 44特徴モデル | 差 |
|---|---|---|---|
| 1位馬 勝率 | 0.3967 | 0.4624 | **+6.6pt** |
| 1位馬 複勝率 | 0.7172 | 0.7661 | +4.9pt |
| レース内 Spearman | 0.5280 | 0.5822 | +0.054 |

paired bootstrap で全て有意。**差の実体は市場（オッズ）特徴**で、market 5本を外すと
44特徴モデルも v10 と同水準（0.3995）まで落ちる。

### 学習・検証・テスト期間（`src/chihou_protocol.py` が正）

| 区分 | 期間 | 用途 |
|---|---|---|
| TRAIN | 〜 **2025-06-30** (`TRAIN_END`) | 学習のみ |
| VAL | **2025-07-01 〜 TEST_START の前日** | 探索・A/B を繰り返してよい（既に6回以上使われ焼けている） |
| TEST | **当月1日 〜** (`TEST_START`・月次ローリング) | 一度きり評価。使ったら `record_test_usage()` で台帳に記録 |

**TEST_START は「当月1日」で月次ローリング**（2026-08-03 に固定値 20260701 から変更）。
固定すると学習終端も固定されモデルが古くなるため、日付から導いて毎月自動で前進させる。
`CHIHOU_TEST_START=YYYYMMDD` で固定可（過去分析の再現用）。VAL_END は TEST_START の前日に追随。

**月次サイクル**（`scripts/chihou_monthly_rollover.py` / LaunchAgent
`com.kiseki.chihou-monthly-rollover` が**毎月1日 03:18** に実行）:

| フェーズ | 内容 | 自動 |
|---|---|---|
| `evaluate` | **先月**を一度きり評価し台帳へ記録。DB の指数値は前回サイクルの backfill（先々月までで学習したモデル）が書いたものなので**この時点では honest** | ✅ |
| `retrain` | 先月までを含めて再学習。旧モデルは `data/backup/model_YYYYMMDD/` へ退避 | ✅ |
| `backfill` | v13 を全期間再計算し DB を新モデルへ揃える | ❌ **人が実行** |

- **順序に意味がある**。先に再学習すると評価が in-sample になる
- ⚠️ **`backfill` はデプロイ後に実行する**。先に走らせると DB=新モデル / live=旧モデルの
  新旧混在になる（`feedback_full_period_migration`）
- コミット・デプロイ・backfill を自動化していないのは、モデル差し替えとデプロイが
  外向きの操作だから。レポート（`backend/docs/monthly_rollover/YYYYMM.md`）末尾に手順が出る
- **指数1位の勝率は季節性が強い**（7月は 2024 41.3% / 2025 43.7% / 2026 42.2% なのに
  1月は 45.6〜49.7%）。前月を冬場の窓と比べて「劣化した」と誤読しないこと。
  レポートは同月比較の表を自動で付ける

**03:18 という時刻の根拠**: LaunchAgent は Mac がスリープ中だと発火しない
（復帰時に遅延実行はされるが電源オフだと走らない）。日次起床の直後に置いている。
DBバックアップ（`com.kiseki.db-backup`）は **03:30〜04:15（実測45分）** VPS へ重い
`pg_dump` を投げるので、その**前**に終わらせて競合を避ける（本ジョブは約2分）。

```bash
# 深夜の自動起床（DBバックアップと月次ローリングの両方をカバーする。要 sudo・1回だけ）
sudo pmset repeat wakeorpoweron MTWRFSU 03:15:00
pmset -g sched          # 設定確認
```
⚠️ `pmset repeat` は**繰り返しスケジュールを1つしか持てない**。別の用途で上書きすると
バックアップとローリングの両方が起床しなくなる。plist は `scripts/launchagents/` に複製あり。

- **本番モデルの学習終端は `TRAIN_DATA_END` = `TEST_START` の前日**（`train_chihou_market_lgb.py`)。
  2026-08-03 以前は `"20260706"` がハードコードされており **TEST 期間の 257レースを学習に含んでいた**ため是正した
- `TRAIN_DATA_START = "20230101"` は**宣言値で実効は 2024-01-01**。学習クエリが
  `calculated_indices version>=9` を要求する一方サブ指数が 2024-01 からしか無く、2023年は0行
- 再学習は `train_chihou_market_lgb.py --refit-only`（A/B 判定を経由せず2ヘッドを学習・保存）
- 検証スクリプト群（`chihou_rank_quality_review.py` 等）の `test` は
  **2026-01〜06 = プロトコル上は VAL の一部**。TEST とは別物なので混同しないこと

- バックフィル: `scripts/inference_chihou_v13.py --start 20240101 --end YYYYMMDD`
  - `race_results` は **LEFT JOIN**（出走取消・失格も母集団に含む）。本番 `rank_by_hn` と
    母集団を揃えるため。学習用 `BASE_QUERY` は完走馬のみなので流用してはいけない
  - VPS 負荷対策で `--batch-size` / `--sleep` 分割コミット
- ⚠️ **バックフィルした過去分は in-sample**（全期間1回学習モデルの遡及適用＝model-vintage
  look-ahead）。**DB の composite_index / win_probability で過去の ROI・的中率を評価しない**こと。
  honest 評価は `chihou_rebuild_walkforward.py` 等の walk-forward で行う

### 検証・監視スクリプト（2026-08-02 新設）

| スクリプト | 用途 |
|---|---|
| `scripts/check_chihou_feature_health.py` | 44特徴の DEAD/SHIFT/DEGENERATE/DECLINE 検出。学習と同じ `prep` を通して「モデルが実際に見ている値」を検査する |
| `scripts/chihou_rank_quality_review.py` | HEAD/TAIL/ALL 分離のランキング品質比較 + レース単位 paired bootstrap |
| `scripts/chihou_feature_ab.py` | 特徴量セットの A/B（死んだ特徴の除去・新規列の追加） |
| `scripts/chihou_composite_scale_review.py` | composite スケール係数と tier 較正閾値の掃引 |
| `scripts/inference_chihou_v13.py` | v13 バッチ推論・全期間バックフィル |

### 検証済みの否定結果（再検証不要）

- **外部指数 netkeiba（`nk_idx_z` / `nk_rank_n`）は 2026-06 以降フォールバック占有率100%＝死んでいる**
  が、**実害なし**。serve 時欠損を再現した A/B で top1勝率 +0.0017（むしろ僅かに改善）、
  2特徴を落としても −0.0005（有意差なし）。kichiuma は 95%→76% に劣化中で要監視
- **未使用の高充足列6本（賞金クラス・ナイター・減量騎手・重量種別）は効果なし**。
  top1勝率 +0.0017 [CI −0.0003, +0.0037] で有意差なし → 不採用
- **目的関数の変更は地方では割に合わない**。`reg_rank`（JRA v27 の採用形）は
  Spearman を +0.0068 改善する一方 **top1勝率を −0.0045 悪化させる**（どちらも有意）。
  JRA では両方改善したが地方はトレードオフ。現行 `is_top3` 二値が HEAD の最適点
- **JRA戦歴のクロス活用は市場に織り込み済みで不採用**（`scripts/chihou_jra_history_ab.py`）。
  地方出走馬の 57.7% に JRA 出走歴があり point-in-time 8特徴を作れるが、
  本番構成では top1勝率 +0.0003（ns）。**市場特徴を外した土台でだけ Spearman +0.0028 が有意**
  ＝情報は本物だがオッズが完全に織り込んでいる
- **着外率ヘッド（out_probability）の新設も不採用**。同じ除外率で較正済みの指数差ルールと同等
  （31%帯: ルール 93.6/2.9/6.9 vs モデル 93.9/3.0/6.7）。alembic 移行に見合わない。
  **JRA で「指数差→着外率モデル」が効いたのはモデルの力でなく旧ルールの較正崩れが原因**の可能性が高い

## 地方競馬 推奨カテゴリ（`/api/chihou/recommendations/sweet-spot`）

JRA とは別系統で、**5 カテゴリ** を都度算出して返す（オッズ取得後に毎リクエスト計算・
30秒プロセス内キャッシュ）。

**Phase2（2026-06-05, commit `909124ac`）で sweet_spot / place_bet は EVゲートから
ランキング規則へ全面移行済み**。較正済 win_probability では高オッズ馬の honest EV が
概ね <1 となり旧 EV ゲートは機能しない。Phase1 クリーンOOSで黒字だったのは
「指数1位 × 単勝10-30倍 × 割安場」のランキング規則だったため、これを定義とする。

| カテゴリ | bet | 条件（Phase2 現行） |
|---|---|---|
| `sweet_spot` 高オッズ穴 | 単勝 | **指数1位 ∧ 単勝10〜30倍 ∧ 割安5場（浦和/金沢/高知/笠松/盛岡）**（旧 5seed 単ROI 1.17 ※要注意・下記参照） |
| `place_bet` 複穴 | 複勝 | 1番人気<2.0 ∧ 単勝≥10 ∧ **指数3位以内** ∧ k≤2 ∧ **頭数≥8** |
| `upset_place` 穴軸複勝 | 複勝 | 人気薄リランカー軸（単勝[10,15)×非オッズスコア×外部バッジ） |
| `low_odds_trusted` 信頼本命 | 単勝 | 単勝<1.5（hit 約70% / 単ROI 0.8台） |
| `low_odds_untrusted` 不信頼本命 | 単勝 | 1.5≤単勝<2.0（hit 約48% / 単ROI 0.8台） |

- 判定本体: `backend/src/indices/buy_signal.py::chihou_is_sweet_spot() / chihou_is_place_bet() / chihou_low_odds_trust_level()`
- `sweet_spot` と `place_bet` は同一馬が両方に入ることを許容（並列）。低オッズ系とは構造的に排他
- 実勢集計: `scripts/aggregate_chihou_recent.py` は上記の本番判定関数を import して同一条件で集計する

**⚠️ 生存者バイアス監査・修正（2026-07-23）**: 旧バックテスト系スクリプトは
`race_results` を INNER JOIN し「完走・正常決着馬のみ」で idx_rank（指数順位）を
再計算していたため、本番の指数1位馬が出走取消/失格になると2位馬が繰り上がって
1位扱いになる生存者バイアスを含んでいた（本番 `chihou_recommender.rank_by_hn` は
出走予定馬全体で順位を確定するため、この乖離は起きない）。`aggregate_chihou_recent.py`
と `backtest_chihou_sweetspot.py`（新設）は出走予定馬全体（LEFT JOIN）で idx_rank
を計算してから確定結果のみに絞り込むよう修正済み。v10全期間(2024-01〜2026-07,
32,976レース)で honest 再計算した結果、**sweet_spot 単ROI 1.028→0.983
（黒字→ほぼ損益分岐）**、**place_bet 複ROI 1.056→1.046** に低下（idx_rankが変化した
レースは全体の11.7%）。

**⚠️⚠️ walk-forward honest再構築でさらに深刻な結果（2026-07-23、
`backend/scripts/chihou_rebuild_walkforward.py`）**: 本番モデル(v12)は
2023-01〜直近の全期間を1回だけ学習した単一モデルを、backfillで2024-01以降の
全historical raceにretroactivelyに適用している＝「モデルの学習パラメータ自体が
対象レースより未来のデータを反映している」model-vintage look-ahead が存在した
（keirinの「モデルが賢くなるたびに過去分析がリークする」問題と同型）。四半期ごとに
その時点までのデータだけで学習しなおした vintage別モデルで2024-10〜2026-07の
全8四半期(24,067レース)を honest再評価した結果、**sweet_spot該当レースが0件**
（Phase0時点のn=381→0）。**sweet_spotの「黒字」主張は生存者バイアス修正後もなお
model-vintage look-aheadにほぼ全面的に依存していたと判断される。** place_betは
複勝ROI 0.987（Phase0の1.046から低下、ほぼ損益分岐で残存）。
sweet_spotは事実上エッジなしとして扱うこと。

**Phase2 非効率性の系統的スイープ（2026-07-23、`backend/scripts/chihou_walkforward_sweep.py`）**:
walk-forward honest予測(24,069レース)を場×指数順位帯・オッズ帯・市場一致状況・
距離帯等で153セグメントに系統的に分解して調査した結果、ほぼ全セグメントがROI
0.6〜0.9台に収束（控除率にほぼ支配される）。ROI≥1.10の候補は5件のみで、いずれも
n=46〜123の小標本（多重比較の必然として説明可能）。唯一注目すべきは**高知の
「断然人気R×指数上位×単勝≥10」複勝母集団(n=105, 複勝ROI 1.291)**だが、
TEST_START(2026-07-01〜)以降の新規データで一度きり評価するまでは採用しないこと。
現行データ・特徴量セットでは近い将来にrobustな黒字戦略を見つけるのは構造的に
困難という前提でtier設計を進めるべき（keirinの「控除率の壁」と同型の結論）。

**⚠️ 複勝7頭以下ルールの母集団バグ・本番修正済み（2026-07-23、ユーザー指摘）**:
複勝は出走7頭以下だと2着までしか払い戻されない(JRA/NAR共通ルール)。
`chihou_is_place_bet()`にはこのゲートがなく、6-7頭立てで3着入着馬を誤って複勝
的中扱いしうる状態だった（`upset_place`も同型の穴）。`CHIHOU_PLACE_MIN_HEAD_COUNT=8`
未満(Noneも含む)は必ずFalseを返すよう本番修正済み（`chihou_recommender.py`/
`chihou_races_router.py`全呼び出し元更新、walk-forward/backtest系スクリプトも同様に
修正・再検証済み）。修正後の最終honest数値: **place_bet 複勝ROI = 0.972**
（n=1,066、walk-forward全8四半期）。
詳細: memory `chihou_survivor_bias_audit_2026_07_23.md`

**API レスポンス構造**:
```
ChihouSweetSpotResponse {
  items: ChihouRecommendationOut[]  # category フィールドで分類
  summaries: { [category]: { n_total, n_settled, n_hits, hit_rate, win_roi, bet_type } }
}
```
`bet_type="place"` の場合 `win_roi` は複勝ROI を返す（フィールド名は互換のため維持）。

### 複勝オッズ永続化

`chihou.race_results.place_odds` は HR 払戻からだと 1〜3着のみで充足率28%だったため、`chihou.odds_history`（bet_type='place'）の発走前最終スナップショットで全馬補完する。
- 自動補完: `backend/src/api/chihou_import_router.py::_fill_loser_place_odds_from_history()` が HR 取込後に呼ばれる
- 過去補完: `backend/scripts/backfill_chihou_place_odds.py --start YYYYMMDD --end YYYYMMDD`
- `chihou.odds_history` は **2026-04-07 以降** のみ蓄積。それ以前は恒久的に補完不可

### 推奨パネル UI（`/chihou/races` の推奨タブ）

- カテゴリ別 **コンパクト table**（カードではなく一覧表形式）
- 上部に **競馬場別 当日サマリ table**（rows=場 × cols=5カテゴリ、各セル: hits/n + ROI）
- 着順バッジ: 1着=🥇金 / 2-3着=🥈🥉青 / 4着以下=灰
- 単勝推奨で 2-3 着の場合「△ 複圏」青系バッジ（複勝なら馬券になったケースが分かる）
- レース名から `/chihou/races/{id}` 詳細ページへリンク

詳細: memory `chihou_place_bet_category.md`

### 集計・分析スクリプト

| スクリプト | 用途 |
|---|---|
| `backend/scripts/aggregate_chihou_recent.py --days 30` | 直近30日の各カテゴリ実勢 hit_rate / ROI を DB 直接集計 |
| `backend/scripts/backfill_chihou_place_odds.py` | 過去 race_results の place_odds NULL を odds_history で補完 |
| `backend/scripts/backtest_chihou_sweetspot.py --version N [--show-bias]` | sweet_spot/place_bet の honest バックテスト（本番同一母集団・2026-07-23新設） |
| `backend/scripts/backtest_chihou_low_odds.py` | low_odds 信頼/不信頼分割の検証 |

## DB 自動バックアップ運用

VPS PostgreSQL `hrdb` (4.96GB) を Mac に毎日 03:30 JST 自動バックアップ。
詳細・リストア手順は `docs/backup-restore.md` 参照。

- 実行スクリプト: `scripts/backup_hrdb.sh`
- launchd: `~/Library/LaunchAgents/com.kiseki.db-backup.plist`
- 保存先: `~/kiseki-backups/{daily,weekly,monthly}/`
- 世代: 日次 7・週次 4・月次 12 (合計 ≒ 12〜15GB)
- ログ: `~/kiseki-backups/backup.log` / `logs/db_backup_launchd.log`
- 圧縮: `pg_dump -Fc -Z 9` で 4.96GB → 516MB (10.4%)

### 状態確認

```bash
launchctl list com.kiseki.db-backup           # LastExitStatus 確認
ls -lh ~/kiseki-backups/daily/                # 直近 dump
tail -30 ~/kiseki-backups/backup.log          # 実行ログ
```

### 手動実行

```bash
/Users/ysuzuki/GitHub/kiseki/scripts/backup_hrdb.sh
```

### リストア時の注意

VPS は PostgreSQL 16.13 / dump 形式 v1.15。Mac の `pg_restore` は **16 系必須** (`/opt/homebrew/opt/postgresql@16/bin/pg_restore`)。14 系では `unsupported version` エラー。

### Mac スリープ時

`StartCalendarInterval` 単独では Mac スリープ中の発火はスキップされる。深夜稼働必須なら:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 03:25:00
```
