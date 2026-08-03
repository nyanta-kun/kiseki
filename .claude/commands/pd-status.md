---
description: 並列開発の全体状況（worktree・稼働ブランチ・柱・Alembic）を表示する
allowed-tools: Bash(bash scripts/dev/pd_status.sh), Bash(git *)
---

並列開発の現状を確認します。

!`bash scripts/dev/pd_status.sh`

上の出力を読み、次を簡潔に報告してください。

1. 今どの柱で何本の作業が走っているか
2. `main` の作業ツリーが汚れていないか（汚れていれば、それが並列開発の妨げになる理由を一言添える）
3. Alembic の head が単一か
4. マージ済みで削除できるブランチがあるか
5. 次にとるべきアクション（1〜2 個に絞る）

出力をそのまま貼り直さず、要点だけを述べてください。
