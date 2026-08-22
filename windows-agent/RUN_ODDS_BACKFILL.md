# 確定オッズ（O1〜O6）バックフィルの実行手順

`odds_backfill.py` を Windows VM で走らせる手順。**単独の手順書が無かったので、
`CLAUDE.md` の「Windows操作（Parallels経由）」と VM 上の実測から起こしたもの。**

## なぜ必要か

- 速報系（`JVRTOpen` 0B32〜0B36）で集めたエキゾチックオッズは、2026-08-23 まで
  `EXOTIC_HEADER_SIZE` が 11 バイトずれていて**全て壊れていた**（馬番が出走頭数以内の
  行は三連単 1.4% / 三連複 3.0%）。生レコードを保存していないためオッズ値は復元不能。
  壊れた 12,269,943 行は `backend/scripts/purge_corrupt_exotic_odds.py` で削除済み。
- 蓄積系 `RACE` DataSpec には **O1〜O6（確定オッズ）が含まれる**のに、
  `jvlink_agent.py::_filter_race_records()` が RA/SE/HR だけを残して捨てていた。
  → 確定オッズは一度も DB に入っていない。ここから取り直す。

## 🔴 実行前に必ず: バックエンドへの修正デプロイ

**パースは VPS のバックエンド（`https://api.galloplab.com`）側で走る。**
`backend/src/importers/odds_importer.py` の修正がデプロイされていない状態で
バックフィルを流すと、**壊れたデータを入れ直すだけ**になる。

```bash
# 1. デプロイ済みバージョンを確認（51 なら未デプロイ）
ssh sekito "grep -n 'EXOTIC_HEADER_SIZE =' ~/GitHub/kiseki/backend/src/importers/odds_importer.py"

# 2. 修正を main へマージしてからデプロイ（deploy スクリプトは git pull origin main する）
bash scripts/deploy-galloplab.sh --backend

# 3. 40 になったことを確認
ssh sekito "grep -n 'EXOTIC_HEADER_SIZE =' ~/GitHub/kiseki/backend/src/importers/odds_importer.py"
```

## 前提

| 項目 | 値 |
|---|---|
| VM への接続 | `ssh windows-vm`（`~/.ssh/config` に定義済み・`10.211.55.6`） |
| Windows の Python | `C:\Python312-32\python.exe`（**32bit**。ディレクトリ名に反して中身は 3.13） |
| venv | **無い。** システム Python 直で動かす |
| 配備先 | `C:\kiseki\windows-agent\`（**git clone ではない。** Mac からコピーする） |

## 手順

### 1. スクリプトを VM へ配備する

`C:\kiseki` は git 管理下ではないので、作業ツリーからコピーする。
SSH セッションからは `Z:` ドライブが見えない（マップドライブは対話セッション固有）ので、
**UNC パス `\\Mac\Home\...` を使う**。

```bash
ssh windows-vm 'powershell -Command "Copy-Item \"\\\\Mac\\Home\\GitHub\\kiseki-wt\\jra\\exotic-odds-parser\\windows-agent\\odds_backfill.py\" -Destination \"C:\\kiseki\\windows-agent\\\" -Force"'
ssh windows-vm 'powershell -Command "Test-Path C:\kiseki\windows-agent\odds_backfill.py"'
```

> ⚠️ `.vbs` / `.ps1` / `.bat` を同じ worktree から配ると改行が LF のままで壊れることがある
> （`CLAUDE.md` の eol 規約）。今回は `.py` だけなので影響しない。

### 2. RunAdhoc タスクで起動する

**SSH から `Start-Process` で直接起動してはいけない。** SSH セッションが切れた瞬間に
プロセスが死ぬうえ、JVDTLab.dll がデスクトップセッションを取得できず
**JVOpen が無限ブロックする**（2026-04-25 に確認済み）。

`adhoc_cmd.txt` に「スクリプト名＋引数」を1行だけ書き、タスクスケジューラに実行させる。
`-Encoding ASCII` は必須。

```bash
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\kiseki\windows-agent\adhoc_cmd.txt\" -Value \"odds_backfill.py --from-year 2026 --option 1 --backend-url https://api.galloplab.com\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'
```

> 🔴 **`--backend-url` を明示すること。** VM には `.env` が2つあり、
> `C:\kiseki\.env` は `https://api.galloplab.com`（到達可・200）だが
> `C:\kiseki\windows-agent\.env` は `http://192.168.11.26:8000`（**到達不可**）。
> スクリプト側では親 `.env` を先に読むようにしてあるが、明示が確実。

### 3. 進捗を見る

```bash
ssh windows-vm 'powershell -Command "Get-Content C:\kiseki\windows-agent\odds_backfill.log -Tail 30"'
ssh windows-vm 'powershell -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime"'
```

DB 側での確認（Mac から）:

```sql
SELECT bet_type, count(*) FROM keiba.odds_history
WHERE bet_type IN ('trio','trifecta') GROUP BY 1;
```

正しく入っていれば、馬番は必ず出走頭数以内・重複なしになる。検算:

```sql
WITH r AS (SELECT id, head_count FROM keiba.races WHERE date='YYYYMMDD' AND course<='10')
SELECT oh.bet_type, count(*) AS n,
  round(100.0*avg((SELECT bool_and(p::int BETWEEN 1 AND r.head_count)
    FROM unnest(string_to_array(oh.combination,'-')) p)::int),1) AS pct_ok,
  min(oh.odds), max(oh.odds)
FROM keiba.odds_history oh JOIN r ON r.id=oh.race_id
WHERE oh.bet_type IN ('trio','trifecta') GROUP BY 1;
```

`pct_ok` が 99% 以上で、三連複の最小オッズが数倍〜十数倍なら成功。
（壊れていた頃は pct_ok が 1.4〜3.0%、最小オッズが 2040 / 10204 だった）

## オプションの選び方

| option | 意味 | 備考 |
|---|---|---|
| `1` | 通常（ローカルキャッシュ優先） | **まずこれで試す。** `payout_backfill` は 109ファイル/約9分で完了した実績あり |
| `3` | セットアップ（全再ダウンロード） | 消費済みファイルも取り直せる。**旧セットアップダイアログが出る**ので対話セッションが要る |

> `payout_backfill_out.txt` の実測では **RACE + option=3 の JVOpen は 11秒で rc=0**。
> 「option=3 は数時間」という一般論は DataSpec 依存で、RACE には当てはまらなかった
> （ただし 2026-04-04 の 1 回きりの記録）。

## 落とし穴

1. **メンテナンス窓**（既定 `TUE 08:00-15:00`）は JVOpen を呼ばない設計。火曜午前は避ける。
   実測で JVOpen が 1193 秒待たされて rc=-504 になった記録あり（2026-08-04）。
2. **JVOpen が返らないときは、まずモーダルダイアログを疑う。**
   `jvlink_dialog_guard.py` が自動応答するが、SSH 越しの PowerShell からは対話セッションの
   ウィンドウが見えない。`Process.MainWindowHandle` で判定してはいけない（6日間誤診した記録あり）。
3. **ログファイルの掴み合い。** 先行インスタンスが `odds_backfill.log` を掴んでいると
   `FileHandler` が `PermissionError` で落ちる（`payout_backfill` で実際に起きた）。
   `odds_backfill.py` は PID 付きの別名へ逃がすようにしてあるが、
   同時に2本走らせないこと。
4. **`kiseki-EOD-Cleanup`**（毎日 23:45）は前日以前に起動したプロセスを掃除する。
   マッチ対象は `jvlink_agent` / `umaconn_agent` / `jvlink_historical` なので
   `odds_backfill` は巻き添えにならないが、日をまたぐ長時間実行は避けるのが無難。
5. **VM の再起動に `prlctl restart` は効かない。** `shutdown /r /t 0` を使う。

## 実行後にやること

1. 上の検算 SQL で `pct_ok` を確認する
2. 期待値ベースの買い目検証（三連複の EV 選別）が初めて可能になる。
   これまでは `race_payouts` の的中組み合わせしか無く、確率上位K点でしか選べなかった。
