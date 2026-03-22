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
```

> **注意**: Windows側の `.env` は Mac側と同じリポジトリの `.env` を参照する実装になっている。
> `jvlink_agent.py` は `../.env`（親ディレクトリ）を読み込む。
> `C:\kiseki\windows-agent\` に配置した場合、`C:\kiseki\.env` が読み込まれる。

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
