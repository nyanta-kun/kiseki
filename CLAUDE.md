# kiseki - 競馬予測指数システム

## プロジェクト概要
JRA-VAN Data Lab SDKからデータを直接取得し、独自の競馬指数を算出。
オッズとの期待値比較で合理的な馬券購入判断を支援するシステム。
競馬新聞風PWA Webページで指数・期待値を表示する。

## アーキテクチャ（構成B改）
```
Windows (Parallels) - Python 32bit + pywin32
  └─ JV-Link SDK (COM) → 全データ取得・オッズ・リアルタイム通知
  └─ HTTP POST → Mac側 FastAPI

Mac (Docker) - Python 3.12 + FastAPI
  └─ 指数算出エンジン（14 Agents）
  └─ REST API + WebSocket
  └─ Next.js Frontend (PWA)

VPS - PostgreSQL（既存DB / keiba スキーマ）
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
| `0B12` | 速報成績（払戻確定後）| レースキー16文字（YYYYMMDDJJKKHHRR） |
| `0B11` | 速報馬体重 | YYYYMMDD |
| `0B31` | 速報単複枠オッズ（O1レコード）| レースキー16文字 |
| `0B15` | 速報レース情報（出走取消・騎手変更等）| YYYYMMDD |

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
    HostName YUICHIROSUZDC35.local
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
- **umaconn_agent realtimeモード 自動管理**（2026-04-18 実装済み）
  - Windowsタスクスケジューラ（`kiseki-UmaConn-Realtime`）が毎朝9:00に自動起動
  - 自動停止: 最終レース発走+90分 or 21:30ハードストップ（先に来た方）
  - ウォッチドッグ: NVRTOpenハングを180秒で検知 → `os._exit(1)` 強制終了 → 翌朝9:00に自動復帰
  - タスク状態確認: `ssh windows-vm "schtasks /query /tn 'kiseki-UmaConn-Realtime' /fo list"`

### jvlink_agent.py 起動
※ setup/daily/recent は完了後にターミナルが自動で閉じる。realtime は監視用のため開いたまま。
※ SSH経由で実行するためウィンドウちらつきなし。ログは jvlink_agent.log で確認。
```bash
# setupモード（全過去データ取得）
ssh windows-vm "Start-Process cmd.exe -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode setup' -WindowStyle Hidden -PassThru | Select-Object Id"

# dailyモード（当日データ取得）
ssh windows-vm "Start-Process cmd.exe -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode daily' -WindowStyle Hidden -PassThru | Select-Object Id"

# recentモード（指定年以降を取得、完了後に自動終了）
ssh windows-vm "Start-Process cmd.exe -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode recent --from-year 2023' -WindowStyle Hidden -PassThru | Select-Object Id"

# realtimeモード（オッズ・成績・出走取消を30秒間隔でポーリング、常駐）
ssh windows-vm "Start-Process cmd.exe -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode realtime' -WindowStyle Hidden -PassThru | Select-Object Id"

# odds-prefetchモード（前日発売オッズを1回取得して終了。VPS cronから1時間ごとに呼び出す）
ssh windows-vm "Start-Process cmd.exe -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode odds-prefetch' -WindowStyle Hidden -PassThru | Select-Object Id"

# blod-umモード（pedigrees.sire NULL補完。2022年以前の馬が対象。1回のみ実行）
ssh windows-vm "Start-Process cmd.exe -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode blod-um' -WindowStyle Hidden -PassThru | Select-Object Id"
```

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
