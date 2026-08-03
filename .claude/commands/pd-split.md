---
description: 複数の改修要望を、柱ごとの並列実行可能なタスクに分解して worktree 計画を出す
argument-hint: <改修したい内容を列挙>
allowed-tools: Read, Grep, Glob, Bash(git *), Bash(bash scripts/dev/*)
---

改修要望: $ARGUMENTS

`task-splitter` サブエージェントを起動し、上の要望を **並列実行可能な独立タスク**に分解してください。

分解にあたっては次を必ず守らせてください。

- 影響ファイルは推測せず、Grep/Glob/Read で実際に特定する
- 柱の判定は `scripts/dev/pillars.sh` の `pillar_of` を根拠にする
- `db/models.py` と `backend/alembic/` を複数タスクに分散させない（1 本に集約する）
- 1 タスク = 1 柱 = 1 ブランチ

結果を受け取ったら、ユーザーに **Wave 構成（先行 shared → 並列 → 統合後）** と
**そのまま実行できる `wt.sh new` コマンド列**を提示してください。

ユーザーが承認したら worktree を作成します。承認前に作成しないでください。
