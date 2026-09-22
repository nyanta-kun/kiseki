# Mac mini 常時稼働ジョブ

2026-09 に MacBook の crontab / LaunchAgent から Mac mini（`macmini-server`）へ移すジョブ群。
計画書は `~/pc-analysis/macmini/MACMINI_SETUP_PLAN.md`（4 章）。

| ファイル | 役割 |
|---|---|
| `jobs.py` | ジョブ定義の正本。plist の生成・導入・死活確認 |
| `run_job.sh` | 全ジョブ共通のラッパー（PATH・ログ・ハートビート・失敗通知・多重起動防止） |
| `_env.sh` | PATH と `~/.config/kiseki/env`（秘密情報・権限 600）の読み込み |
| `prlctl_watchdog.sh` | ハングした `prlctl exec` を kill |
| `launchagents/` | `jobs.py generate` の生成物。手で編集しない |

```bash
python3 scripts/macmini/jobs.py status                       # 各ジョブの最終成功と鮮度
python3 scripts/macmini/jobs.py install --skip-tag vm         # 導入内容の確認（dry-run）
python3 scripts/macmini/jobs.py install --apply --skip-tag vm # VM 以外を導入
tail -f logs/jobs/<ジョブ名>.log
```

- ハートビート: 成功で `~/.local/state/jobs/<名前>.ok`、失敗で `<名前>.fail`
- 失敗通知: `DISCORD_WEBHOOK_URL_SYSTEM` へ。成功→失敗 / 失敗→成功の変わり目だけ送る
- `vm` タグのジョブは Parallels + JV-Link が Mac mini で動いてから入れる

## 移さなかったもの

- **HorseRacingIE の cron 13 本** — 書き込み先の `public.*` は全テーブル 0 行・挿入 0 件
  （2026-09-22 実測）。実データのある `sekito.*` は kiseki が書いている。
  さらに `scrapingOdds.py` / `scrapingResult.py` / `scrapingAnagusa.py` は 2025-04 に
  削除済みで、cron は存在しないファイルを呼び続けていた
- `daily_fetch.sh` / `realtime_start.sh` — 2026-07-11 に無効化済み
- `backend-proxy` / `parallels-proxy` / `autossh-tunnel` — 転送ループを起こした死んだ資産
