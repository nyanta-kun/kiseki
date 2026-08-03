---
description: 完了ブランチを順次マージして main に統合する（統合PMを起動）
argument-hint: [ブランチ名...] 省略時は全稼働ブランチから順序を提案
allowed-tools: Bash(bash scripts/dev/*), Bash(git *)
---

統合対象: $ARGUMENTS

`pm-integrator` サブエージェントを起動し、統合を統括させてください。

必ず次の順で進めさせます。

1. `bash scripts/dev/pd_status.sh` で現状把握
2. `bash scripts/dev/integrate.sh --plan` で順序提案と衝突予測
3. **提案順序の妥当性を自分で検証する**（shared 先行になっているか、同じファイルを触る組が連続していないか）
4. `bash scripts/dev/integrate.sh --dry-run ...` で衝突を予測
5. ユーザーに順序を提示して**承認を得てから**実マージを実行

衝突が出た場合、PM が勝手に解決してはいけません。
衝突ファイルと両者の意図を提示し、後発ブランチ側で rebase 解決するよう指示させてください。
Alembic の衝突は `migration-guard` サブエージェントに委譲させてください。

統合後は、残っている worktree の `wt.sh sync` を忘れずに案内してください。
