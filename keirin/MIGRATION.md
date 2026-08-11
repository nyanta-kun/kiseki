# keirin リポジトリからの移植記録（2026-08-10）

`nyanta-kun/keirin` を kiseki へ統合した。デプロイと CI/CD の一本化が目的。

## 移植元

- リポジトリ: `nyanta-kun/keirin` — **2026-08-11 に GitHub 上でアーカイブ済み（read-only）**
- ブランチ: `master`
- **最終コミット: `50a6658e744d06fbade890398e4858635bcebbe1`**
  （アーカイブ時点の master と一致＝統合後にあちらへ入ったものは無い）

⚠️ **あちらへ push・PR はできない（read-only）。** 変更は kiseki 側だけに入れること。
未マージのまま残した feature ブランチが9本あるが、アーカイブは削除ではないので
内容は読める（`gh api repos/nyanta-kun/keirin/compare/master...<branch>`）。

## 方式: 新規移植（subtree ではない）

keirin の履歴は 291MB / 455コミットだが、現HEADの追跡ファイルは 31MB。
差分は `data/picks/*.pdf`（1本4MB）や旧モデル `.pkl` の堆積で、
subtree で取り込むと kiseki が約600MBになり clone が倍以上重くなる。

**コードのみ 666ファイル / 8.2MB を移植した**（`data/` と `.github/` を除外）。

⚠️ **履歴を追うときは移植元リポジトリを見ること。** 上記SHAまでの
`git log` / `git blame` はそちらに残っている。kiseki 上では移植コミットが起点になる。

## 除外したもの

| 対象 | 理由 |
|---|---|
| `data/` | 実行時状態・モデル。VPS上の実体を使う（`.gitignore` 済み） |
| `.github/` | CI は kiseki 側へ統合。旧ワークフローは持ち込まない |

## 実行時状態の引き継ぎ

`data/` と `.venv` は**作り直さず VPS 上のものを移動**する。
作り直すとモデル 212MB の再取得と venv 549MB の再構築が発生する。
手順は `docs/keirin_repo_merge_plan.md` を参照。
