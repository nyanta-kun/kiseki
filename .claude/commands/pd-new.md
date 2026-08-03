---
description: 新しい作業用 worktree とブランチを作成する（柱とトピックを指定）
argument-hint: <柱: keirin|chihou|jra|shared> <トピック> [type: feat|fix|chore]
allowed-tools: Bash(bash scripts/dev/wt.sh:*), Bash(bash scripts/dev/scan_collisions.sh:*), Bash(git *)
---

引数: $ARGUMENTS

新しい隔離作業環境を作ります。

## 手順

1. まず `conflict-scout` サブエージェントを使い、このトピックが触りそうなファイルと、
   他ブランチとの衝突リスクを事前調査してください。
2. リスクが高ければ、worktree を作る前にユーザーへ警告し、分割や順序変更を提案してください。
3. 問題なければ次を実行します。

```bash
bash scripts/dev/wt.sh new $ARGUMENTS
```

4. 作成後、ユーザーに次を伝えてください。
   - 作成されたブランチ名とパス
   - **そのパスで新しい Claude Code セッションを開いて作業すること**（今のセッションでは作業しない）
   - 担当範囲（触ってよいファイル）と、触ってはいけない shared ファイル

引数が足りない場合は、柱とトピックをユーザーに確認してください。
