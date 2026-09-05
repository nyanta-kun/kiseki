# Windows側 セットアップ手順

## 前提条件

- Parallels Desktop 上の Windows 10/11
- JV-Link SDK インストール済み＋利用キー設定済み
- Mac側 FastAPI（Docker）が起動済みであること

---

## 1. Python 32bit版のインストール

JV-Link SDK が 32bit COM のため、**必ず Python 32bit版** を使用すること。

1. [python.org](https://www.python.org/downloads/windows/) から **Windows installer (32-bit)** をダウンロード
   - Python 3.11.x 推奨（3.12でも可）
   - ファイル名例: `python-3.11.9.exe`（`Windows installer (32-bit)` を選択）

2. インストール時の注意:
   - **「Add Python to PATH」にチェック**
   - インストール先は `C:\Python311-32\` 等、64bit版と混在しないようにすること

3. インストール確認（コマンドプロンプト）:
   ```cmd
   python --version
   python -c "import struct; print(struct.calcsize('P') * 8, 'bit')"
   ```
   → `32 bit` と表示されることを確認

---

## 2. ファイルの配置

Macの `windows-agent/` フォルダをParallels共有フォルダ経由、またはgit cloneでWindows側に配置する。

**推奨ディレクトリ構成:**
```
C:\kiseki\
├── windows-agent\
│   ├── jvlink_agent.py
│   ├── requirements.txt
│   └── start_agent.bat
└── .env               ← ここに設定ファイルを配置
```

### Parallels共有フォルダを使う場合

Macの `~/GitHub/kiseki/` がParallelsで `\\Mac\Home\GitHub\kiseki\` としてマウントされている場合:

```cmd
mkdir C:\kiseki
xcopy "\\Mac\Home\GitHub\kiseki\windows-agent" C:\kiseki\windows-agent /E /I
copy "\\Mac\Home\GitHub\kiseki\.env.example" C:\kiseki\.env
```

### git cloneを使う場合

```cmd
cd C:\
git clone https://github.com/your-org/kiseki.git
```

---

## 3. .env の設定

`C:\kiseki\.env` を編集する（`.env.example` をコピーして作成）。

```env
# VPS PostgreSQL（直接接続は不要。Mac側FastAPIが接続する）
# Windows側では BACKEND_URL のみ設定すればOK

# Mac側 FastAPI のURL
# Parallels環境では host.internal でMac Dockerにアクセス可能
BACKEND_URL=http://host.internal:8000

# JRA-VAN 利用キー
JRAVAN_SID=your-jravan-sid-here

# APIキー（Mac側の .env と同じ値を設定）
CHANGE_NOTIFY_API_KEY=

# JRA-VAN メンテナンス窓（省略可。既定は "TUE 08:00-15:00"）
# この時間帯は JVOpen / JVRTOpen を一切呼ばない
JVLINK_MAINTENANCE_WINDOWS=TUE 08:00-15:00
```

> **注意**: Windows側の `.env` は Mac側と同じリポジトリの `.env` を参照する実装になっている。
> `jvlink_agent.py` は `../.env`（親ディレクトリ）を読み込む。
> `C:\kiseki\windows-agent\` に配置した場合、`C:\kiseki\.env` が読み込まれる。

### JVLINK_MAINTENANCE_WINDOWS

メンテナンス中に JVOpen を呼ぶと、JV-Link が**モーダルダイアログ**を出して
デスクトップセッションを掴む。エージェントは `pythonw.exe` 起動でダイアログを閉じる者が
いないため、COM がそのまま数十分ブロックしたうえで `rc=-504` を返す。

> 実測（2026-08-04）: JVOpen が **1193秒**待たされた末に -504。
> `jvlink_historical` の `time_limit=7200` 秒の処理枠をそれだけで使い切った。

そのため rc を見てから諦めるのでは足りず、**既知の窓では最初から呼ばない**。

書式はカンマ区切りで、3 形式を混在できる:

| 指定 | 意味 |
|---|---|
| `TUE 08:00-15:00` | 毎週火曜（**既定値**） |
| `1ST-TUE 08:00-15:00` | 毎月第一火曜（JRA-VAN 公式 FAQ の記載） |
| `2026-09-10 09:00-12:00` | 特定日（臨時メンテナンスの一時追加用） |

既定を「毎月第一火曜」ではなく「毎週火曜」にしているのは、公式記載以外の火曜にも
ダイアログが観測されているため。**JRA は火曜に開催しない**ので、火曜日中の蓄積系
バックフィル枠を捨てても実害が小さく、安全側に倒せる。

- 開始時刻ちょうどは窓の**中**、終了時刻ちょうどは窓の**外**
- 日跨ぎ（`23:00-02:00` のような開始 >= 終了）は未対応
- 書式が壊れている場合は既定値へフォールバックし警告を出す。
  「窓なし」に倒すとダイアログ地獄に戻るため
- 窓を広げるときは**開催日と重ならないこと**。realtime の JVRTOpen も止まる

判定ロジックは `windows-agent/jvlink_maintenance.py`、テストは
`windows-agent/tests/test_jvlink_maintenance.py`（標準ライブラリのみで動くので Mac 上で実行可）。

```bash
python3 -m pytest windows-agent/tests/test_jvlink_maintenance.py
```

---

## 4. 依存パッケージのインストール

```cmd
cd C:\kiseki\windows-agent
pip install -r requirements.txt
```

インストールされるパッケージ:
- `pywin32` - JV-Link COM操作に必須
- `requests` - Mac FastAPIへのHTTP送信
- `python-dotenv` - .env読み込み

---

## 5. JV-Link の確認

JV-Link がインストール済みで利用キーが設定されていることを確認:

1. スタートメニュー →「JV-Link設定」を起動
2. 利用キー（SID）が設定されていることを確認
3. JV-Link テスト接続が成功することを確認

> **重要**: JV-Link は同時1接続のみ。TARGET等の他ツールが起動中の場合は停止すること。

---

## 6. Mac側 FastAPI の起動確認

Windows から Mac の FastAPI が到達できることを確認:

```cmd
curl http://host.internal:8000/health
```

→ `{"status":"ok","env":"development"}` が返れば OK

---

## 7. エージェントの起動

```cmd
cd C:\kiseki\windows-agent

# 初回セットアップ（過去データ一括取得）
python jvlink_agent.py --mode setup

# 当日データ取得のみ
python jvlink_agent.py --mode daily

# リアルタイム監視のみ（オッズ・変更通知）
python jvlink_agent.py --mode realtime

# 全機能（デイリー取得 → リアルタイム監視）
python jvlink_agent.py
```

---

## 8. 自動起動の設定（オプション）

`start_agent.bat` をタスクスケジューラに登録することでWindows起動時に自動実行できる。

1. タスクスケジューラを開く（`taskschd.msc`）
2. 「タスクの作成」→ トリガー: ログオン時
3. 操作: `C:\kiseki\windows-agent\start_agent.bat`

---

## トラブルシューティング

| 症状 | 確認事項 |
|------|---------|
| `JVInit failed` | Python が32bit版か確認。JV-Link再インストール |
| `Backend unreachable` | Mac Docker起動確認。`curl http://host.internal:8000/health` |
| `JRAVAN_SID が設定されていません` | `.env` のパスとJRAVAN_SID値を確認 |
| `pywin32` インストールエラー | Python 32bit版を使用しているか確認 |
| `-3` エラーが続く | JV-Linkサーバーのダウンロード中。しばらく待機 |

## オッズ取得のタイミング（2026-09-06 追記）

### いつ取れるか

`kiseki-JVLink-Realtime`（`run_jvlink_realtime.vbs` → `jvlink_agent.py --mode realtime`）は
**毎日 07:00 と 09:00 の2本のトリガー**で起動し、約35秒ごとに当日全レースの
単複オッズ（`0B31`/O1）を取る。エキゾチック（馬連〜三連単）は発走30分前以内のレースのみ。

つまり **当日のオッズが DB に入り始めるのは 07:00**。それ以前は空。

### 🔴 起動を取りこぼすと丸一日空になる

2026-09-06（日曜・開催日）に、**Parallels の Windows VM が suspended のまま**だったため
07:00・09:00 の両トリガーを取りこぼし、08:11 時点で当日36レースのオッズが 0件だった。

対策として `StartWhenAvailable = True` を設定した（既定は False）。これで起動時刻に
VM が止まっていても、立ち上がった時点で取りこぼした回を実行する。

```powershell
$t = Get-ScheduledTask -TaskName 'kiseki-JVLink-Realtime'
$t.Settings.StartWhenAvailable = $true
Set-ScheduledTask -TaskName 'kiseki-JVLink-Realtime' -Settings $t.Settings
```

VM 自体が止まっていたら何も動かないので、**開催日の朝は VM が running か確認する**こと:

```bash
prlctl list -a     # STATUS が running であること
```

### 翌日ぶん（前日発売オッズ）

VPS cron の `odds_prefetch_trigger.sh` は agent が **command loop モード**のときしか
処理されない。開催日は realtime モードで動いているので、**キュー投入しても実行されない**
（実測: 2026-09-05 に9回投入して ODDS PREFETCH の実行ログはゼロ）。

そのため **realtime ループ自身が1時間に1回、翌日ぶんを取りに行く**
（`ODDS_PREFETCH_INTERVAL_SEC = 3600`）。cron 経路は非開催日向けとして残してある。
両方走っても upsert なので害はない。
