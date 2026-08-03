# 並列開発ハーネス 設計書

**作成日:** 2026-08-03
**対象:** kiseki（競輪 / 中央競馬 / 地方競馬の3本柱が同居する単一リポジトリ）
**目的:** 複数の Claude Code セッションで並列改修を行っても、コード管理が破綻しない構成を確立する

---

## 1. 解決したい問題

同一フォルダで複数の Claude Code コンテキストを開いて作業すると、次が起きる。

- **作業ツリーの共有** — 未コミット変更とインデックスは 1 リポジトリに 1 つしかない。
  片方がブランチを切り替えると、もう片方の作業が壊れる。
- **マージ衝突の多発** — 誰がどのファイルを触っているか分からないまま並行作業するため、
  衝突がマージ時まで顕在化しない。
- **Alembic の head 分岐** — 複数ブランチが同時に revision を生成し、マージ時に multiple heads
  や down_revision 不整合を起こす。

### 着手時点の実測（2026-08-03）

| 項目 | 状態 |
|---|---|
| `main` の作業ツリー | **dirty**（未コミット 12 件）— trunk を作業場所として使っていた |
| ローカルブランチ | 16 本（うち稼働中 9 本 / マージ済み 7 本が未削除） |
| Alembic | head は単一（`u4v5w6x7y8z9`）で健全。ただし過去に `fix/migration-down-revision`, `fix/migration-id-collision`, `fix/migration-revision-conflict` の 3 本の修復ブランチを生んでいる |
| revision ID 命名 | 手書き連番 `a1b2c3d4e5f6` 〜 `z5a6b7c8d9e0` で**文字が枯渇済み**。`f2g3h4i5j6k7_*` がファイル名重複（内部 revision は `g3h4i5j6k7l8`） |
| `.claude/` | **全体が gitignore** されており、エージェント定義を共有できない状態だった |
| ブランチ間の衝突 | `backend/src/indices/buy_signal.py`, `services/chihou_recommender.py`, `windows-agent/jvlink_agent.py` が複数ブランチで競合中 |

---

## 2. 採用した構成

**運用モデル:** トランクベース + ハイブリッド制御
（実装は人が柱ごとの別セッションで進め、統合＝マージ順序・検証・rebase を PM エージェントとスクリプトが制御する）

```
main (trunk / 常にクリーン・デプロイ可能・作業禁止)
 │
 ├── ../kiseki-wt/keirin/<topic>   → feat/keirin-<topic>   ← Claude Code セッション A
 ├── ../kiseki-wt/chihou/<topic>   → feat/chihou-<topic>   ← Claude Code セッション B
 ├── ../kiseki-wt/jra/<topic>      → feat/jra-<topic>      ← Claude Code セッション C
 └── ../kiseki-wt/shared/<topic>   → feat/shared-<topic>   ← 単独・最優先で land
                                     │
                          PM (pm-integrator) が順次マージ
                          1本マージ → 検証 → 残りを rebase → 次
```

worktree はリポジトリ**外**（`../kiseki-wt/`）に置く。ruff / mypy / pytest / next が
worktree 内を二重スキャンするのを防ぐため。`KISEKI_WT_ROOT` で変更可能。

### なぜ「柱別インテグレーションブランチ」を採らなかったか

`integ/keirin` 等を挟む案も検討したが、既に PR + CI が機能しており、ブランチが短命で
あるため、中間層は管理コストに見合わないと判断した。柱をまたぐ大規模改修が発生した
場合には再検討する。

---

## 3. ファイル所有マップ（柱の定義）

判定の唯一の情報源は `scripts/dev/pillars.sh` の `pillar_of()`。
ドキュメントとコードが乖離しないよう、判定ロジックはこの 1 ファイルにのみ置く。

| 柱 | 担当ファイル |
|---|---|
| **keirin** | `backend/src/api/keirin_router.py`, `api/yoso_router.py`, `db/keirin_models.py`, `netkeirin/`, `scripts/scrape_netkeirin_*.sh` |
| **chihou** | `api/chihou_*.py`(5本), `db/chihou_models.py`, `importers/chihou_*.py`(3本), `indices/chihou_*.py`(2本), `services/chihou_*.py`(2本), `src/chihou_protocol.py`, `backend/models/chihou_*`, `scripts/chihou_*.sh` |
| **jra** | `indices/`(chihou以外), `importers/`(chihou以外), `windows-agent/`, `api/{races,horses,performance,recommendations,agent_router,import_router}.py`, `services/recommend*` |
| **shared** | `db/models.py`, `db/session.py`, **`backend/alembic/`**, `utils/`, `main.py`, `config.py`, `indices/{base,composite}.py`, `betting/`, `api/{access,users,ws_manager}.py`, `.github/`, `docker-compose*`, `CLAUDE.md`, `scripts/dev/` |

**好材料:** `db/keirin_models.py` と `db/chihou_models.py` が既に分離済みで、モデル定義の
衝突面積は小さい。残る共有点は `db/models.py`（JRA + 共通）と `alembic/`。

---

## 4. 構築した成果物

### スクリプト（`scripts/dev/`）

| ファイル | 役割 |
|---|---|
| `pillars.sh` | 柱判定の SSOT。`pillar_of` / `pillars_of_files` を提供 |
| `check_migrations.sh` | Alembic の head 単一性・ID 重複・親不明・命名一致を検査。DB 不要の純ファイル解析 |
| `check_ownership.sh` | 変更ファイルの柱を判定し、shared 接触と柱またぎを警告 |
| `scan_collisions.sh` | 他ブランチと同じファイルを触っていないかを事前検出（`--all` で総当たり） |
| `preflight.sh` | コミット前の総合チェック（上記3種 + ruff/mypy/pytest/eslint/tsc）。変更領域のみ検査 |
| `integrate.sh` | 順次マージ。`--plan`（順序提案）/ `--dry-run`（衝突予測）対応 |
| `pd_status.sh` | ダッシュボード |

### サブエージェント（`.claude/agents/`）

| エージェント | 役割 |
|---|---|
| `task-splitter` | 改修要望を「柱ごと・低衝突」の独立タスクへ分解し Wave 構成を出す |
| `conflict-scout` | 着手前に他ブランチとの衝突を偵察する |
| `migration-guard` | Alembic の検査・修復。revision ID 命名規約を強制する |
| `pm-integrator` | 統合の統括（順序決定・順次マージ・検証・rebase 指示） |

### スラッシュコマンド（`.claude/commands/`）

`/pd-status` `/pd-split` `/pd-new` `/pd-preflight` `/pd-integrate`

### 設定変更

- **`.gitignore`** — `.claude/` 全体無視をやめ、`agents/` と `commands/` を追跡対象化。
  これによりハーネスが全 worktree・全セッションで共有される。
  `settings.local.json` と `skills/` は各自環境依存のため引き続き無視。`.worktrees/` を追加。
- **`.github/workflows/ci.yml`** — `guards` ジョブを新設（Alembic 整合 + 柱判定レポート）。
  `build-backend` / `build-frontend` の `needs` に追加し、ガード失敗がデプロイを止めるようにした。
  ただし**ビルドを止められるのは Alembic 整合チェックのみ**。柱判定は柱をまたぐ正当な PR まで
  落としてしまうため CI では強制せず、Step Summary へのレポート出力に留めている
  （強制したい場合はローカルで `OWNERSHIP_STRICT=1`）。
- **`CLAUDE.md`** — 「並列開発プロトコル」節を開発ルール直後に追加。

---

## 5. 運用ルール（鉄則）

1. **`main` では作業しない。** trunk は常にクリーンでデプロイ可能に保つ。
2. **1 ブランチ = 1 柱。** 柱をまたぐ変更は分割する。
3. **`shared` は並列にしない。** 単独 PR で最優先に land し、他ブランチはその後 rebase。
4. **Alembic を並列生成しない。** 同一 Wave で複数タスクが revision を作る計画は禁止。
5. **新規 revision ID は日時形式。** `--rev-id "$(date +%Y%m%d%H%M)_<柱>"`。
   既存の連番形式は枯渇済みのため使わない。
6. **マージは順次。** 1本マージ → 検証 → 残りを rebase → 次。
7. **衝突は後発ブランチ側で rebase 解決する。** 統合先で無理に解決しない。

---

## 6. 残作業（本ハーネス導入後に推奨）

| # | 内容 | 理由 |
|---|---|---|
| 1 | `main` の未コミット 12 件を整理（退避 or ブランチ化 or gitignore） | trunk をクリーンにしないと鉄則1が守れない |
| 2 | マージ済みローカルブランチ 7 本を削除 | `pd_status.sh` の「削除可能」欄に一覧が出る |
| 3 | `f2g3h4i5j6k7_add_trio_payout_to_picks_history.py` をリネーム | ファイル名接頭辞と revision ID の不一致を解消 |
| 4 | 稼働中 9 ブランチの棚卸し（`integrate.sh --plan` で順序決定 → 統合 or 破棄） | 長命ブランチほど衝突が増える |
| 5 | GitHub の branch protection で `guards` を required check に設定 | ガードをすり抜けさせない。ただし `guards` で実際にビルドを止められるのは Alembic 整合チェックのみ（柱判定は情報提供で、Step Summary に出るだけ） |
| 6 | `MIGRATION_CHECK_STRICT=1` を CI で有効化（残作業3の完了後） | 命名不一致を再発させない。鉄則5 の `<日時>_<柱>` 形式は接頭辞一致で検査するため STRICT でも通ることを確認済み |

---

*本ドキュメントは `scripts/dev/` と `.claude/agents/` の設計意図の記録である。*
*運用手順の正本は `CLAUDE.md` の「並列開発プロトコル」節。*
