# hrdb バックアップ・リストア運用

VPS PostgreSQL `hrdb` の自動バックアップ・リストア手順をまとめる。

## 概要

- **バックアップ実行**: Mac 側 (`~/kiseki-backups/`)。VPS には dump を残さない (容量逼迫対策)。
- **方式**: Mac から SSH 経由で `pg_dump -Fc` をパイプで直送 (custom format・最大圧縮)。
- **頻度**: 毎日 03:30 JST (launchd `com.kiseki.db-backup`)。
- **世代**: 日次 7・週次 4・月次 12 (合計約 23 世代 ≒ 12〜24 GB)。

## ディレクトリ構成

```
~/kiseki-backups/
├── daily/
│   ├── hrdb-YYYYMMDD.dump            # フルダンプ (約 500 MB)
│   ├── hrdb-keiba-YYYYMMDD.dump      # keiba スキーマのみ
│   ├── hrdb-sekito-YYYYMMDD.dump     # sekito スキーマのみ
│   └── hrdb-chihou-YYYYMMDD.dump     # chihou スキーマのみ
├── weekly/                           # 毎週日曜のフルダンプ
│   └── hrdb-YYYYMMDD.dump
├── monthly/                          # 毎月1日のフルダンプ
│   └── hrdb-YYYYMMDD.dump
└── backup.log                        # 実行ログ
```

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `scripts/backup_hrdb.sh` | バックアップ実行スクリプト |
| `~/Library/LaunchAgents/com.kiseki.db-backup.plist` | launchd スケジュール定義 |
| `~/kiseki-backups/backup.log` | 実行ログ (1ファイル追記) |
| `logs/db_backup_launchd.log` | launchd stdout/stderr |

## 状態確認

```bash
# launchd 登録状態
launchctl list com.kiseki.db-backup

# 直近のバックアップ
ls -lh ~/kiseki-backups/daily/ | tail -10

# 実行ログ
tail -50 ~/kiseki-backups/backup.log

# 容量
du -sh ~/kiseki-backups/{daily,weekly,monthly}
```

## 手動実行

```bash
/Users/ysuzuki/GitHub/kiseki/scripts/backup_hrdb.sh
```

## launchd 操作

```bash
# 初回ロード (or plist 修正後の再ロード)
launchctl unload ~/Library/LaunchAgents/com.kiseki.db-backup.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.kiseki.db-backup.plist

# 即座に実行 (動作確認用)
launchctl start com.kiseki.db-backup

# 停止・解除
launchctl unload ~/Library/LaunchAgents/com.kiseki.db-backup.plist
```

## リストア手順

> **前提**: Mac に PostgreSQL 16 client (`brew install postgresql@16`) が必要。
> VPS は PostgreSQL 16.13、dump 形式 v1.15 のため pg_restore 14 では読めない。

### 1. dump の中身を確認 (実行せず TOC 確認)

```bash
PG_RESTORE=/opt/homebrew/opt/postgresql@16/bin/pg_restore
"$PG_RESTORE" -l ~/kiseki-backups/daily/hrdb-YYYYMMDD.dump | less
```

### 2. テスト DB に丸ごと復元 (動作確認)

```bash
# 別 DB を VPS 上に作成して復元
ssh sekito 'sudo -u postgres createdb hrdb_restore_test'
"$PG_RESTORE" --no-owner --no-privileges \
  -h <VPS> -U postgres -d hrdb_restore_test \
  ~/kiseki-backups/daily/hrdb-YYYYMMDD.dump

# テーブル件数比較で完全性チェック
ssh sekito "sudo -u postgres psql -d hrdb -c \"SELECT schemaname,relname,n_live_tup FROM pg_stat_user_tables ORDER BY 1,2;\"" > /tmp/orig.tsv
ssh sekito "sudo -u postgres psql -d hrdb_restore_test -c \"SELECT schemaname,relname,n_live_tup FROM pg_stat_user_tables ORDER BY 1,2;\"" > /tmp/restored.tsv
diff /tmp/orig.tsv /tmp/restored.tsv

# 終わったら削除
ssh sekito 'sudo -u postgres dropdb hrdb_restore_test'
```

### 3. 単一テーブルだけ復元 (誤削除など)

```bash
# 対象テーブル (例: sekito.pog_user) を一旦リネーム退避
ssh sekito 'sudo -u postgres psql -d hrdb -c "ALTER TABLE sekito.pog_user RENAME TO pog_user_old;"'

# dump から該当テーブルだけ復元
"$PG_RESTORE" --no-owner --no-privileges \
  -h <VPS> -U postgres -d hrdb \
  --schema=sekito --table=pog_user \
  ~/kiseki-backups/daily/hrdb-YYYYMMDD.dump

# 確認後に旧テーブル削除
ssh sekito 'sudo -u postgres psql -d hrdb -c "DROP TABLE sekito.pog_user_old;"'
```

### 4. スキーマ単位で復元

```bash
# 例: sekito スキーマ全体を復元
"$PG_RESTORE" --no-owner --no-privileges \
  -h <VPS> -U postgres -d hrdb \
  --schema=sekito --clean --if-exists \
  ~/kiseki-backups/daily/hrdb-sekito-YYYYMMDD.dump
```

`--clean --if-exists` は復元前に該当オブジェクトを DROP する。**実本番では事前にバックアップ済みであること**。

## Disaster Recovery シナリオ

### A. VPS 全損 (新 VPS への移行)

1. 新 VPS を立ち上げ (Ubuntu 24.04・PostgreSQL 16・Docker)
2. 旧 VPS の `/etc/postgresql/16/main/` 設定を再現 (もしくは既存 conf を流用)
3. `createdb hrdb` 後、最新フルダンプを復元
   ```bash
   "$PG_RESTORE" --no-owner --no-privileges \
     -h <NEW_VPS> -U postgres -d hrdb \
     -j 4 \
     ~/kiseki-backups/daily/hrdb-YYYYMMDD.dump
   ```
4. Docker コンテナ (galloplab/sekito) を起動
5. RTO 目標: 30 分〜1 時間 (新 VPS プロビジョニング含む)

### B. 単一テーブル誤削除

「リストア手順 3」を実行。所要 5〜10 分。

### C. スキーマ全体破損 (例: sekito スキーマのアプリバグで全件 DELETE)

「リストア手順 4」を実行。

## 隔月リストアテスト (推奨)

毎月 1 日のバックアップを使って隔月でリストアテストを実施する:

```bash
# 月次 dump を別 DB に流し込み件数比較
"$PG_RESTORE" -h <VPS> -U postgres -d hrdb_restore_test \
  --no-owner --no-privileges -j 4 \
  ~/kiseki-backups/monthly/hrdb-YYYYMM01.dump

# 件数比較スクリプト (上記「2. テスト DB に丸ごと復元」を参照)
```

## RPO / RTO

| シナリオ | RPO | RTO |
|---|---|---|
| 単一テーブル誤削除 | 24 時間 | 5〜10 分 |
| スキーマ破損 | 24 時間 | 10〜20 分 |
| VPS 全損 | 24 時間 | 30 分〜1 時間 |

## トラブルシューティング

### `unsupported version (1.15) in file header`

Mac の pg_restore が古い (14 系)。`brew install postgresql@16` で 16 client を入れて `/opt/homebrew/opt/postgresql@16/bin/pg_restore` を使う。

### バックアップが途中で失敗する

- VPS 容量を確認 (`ssh sekito "df -h /"`)。`pg_dump` は一時ファイルを作らないが、PostgreSQL の WAL や temp_tablespaces で消費する可能性。
- SSH 接続が切れる場合: `~/.ssh/config` に `ServerAliveInterval 60` を追加。
- Mac の容量を確認 (`df -h ~`)。

### launchd で発火しているのにバックアップされない

```bash
# 終了コード確認
launchctl list com.kiseki.db-backup | grep LastExitStatus

# launchd ログ
tail -50 /Users/ysuzuki/GitHub/kiseki/logs/db_backup_launchd.log
```

Mac がスリープ中だった場合は次回 wake 時に発火しない。深夜稼働必須なら `pmset` で wake 設定:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 03:25:00
```
