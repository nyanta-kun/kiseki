---
description: コミット/PR 前の総合チェック（Alembic・柱所属・衝突・lint・型・テスト）
argument-hint: [--quick]
allowed-tools: Bash(bash scripts/dev/*), Bash(git *)
---

コミット前チェックを実行します。

!`bash scripts/dev/preflight.sh $ARGUMENTS`

結果を読み、次を報告してください。

- 通過したか、落ちたか
- 落ちた場合は **どの項目が、なぜ落ちたか**と、具体的な修正方法
- `shared` ゾーンを触っている場合は、単独 PR にすべきかどうかの判断
- Alembic の警告があれば、その意味と対処の要否

問題がなければ「通過」とだけ述べ、コミットメッセージ案を提示してください。
