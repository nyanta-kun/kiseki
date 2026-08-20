# kiseki - 競馬予測指数システム

## プロジェクト概要
JRA-VAN Data Lab SDKからデータを直接取得し、独自の競馬指数を算出。
オッズとの期待値比較で合理的な馬券購入判断を支援するシステム。
競馬新聞風PWA Webページで指数・期待値を表示する。

## アーキテクチャ（構成B改）
```
Windows (Parallels) - Python 32bit + pywin32
  └─ JV-Link SDK (COM) → 全データ取得・オッズ・リアルタイム通知
  └─ HTTP POST → VPS FastAPI (api.galloplab.com)

VPS (160.251.234.83) - Docker
  ├─ galloplab-backend-1  :8003  FastAPI (kiseki)
  ├─ galloplab-frontend-1 :3002  Next.js (kiseki)
  ├─ sekito-backend-1     :5000  Node.js (sekito)
  └─ sekito-frontend-1    :8080  Vue.js  (sekito)

VPS - PostgreSQL（keiba / sekito / chihou スキーマ共存）
  ├─ keiba.*     — JRA レース・指数・オッズ
  ├─ sekito.*    — 穴ぐさ・外部指数（netkeiba/kichiuma）
  │   ├─ sekito.v_races       — keiba.races の sekito 向けビュー
  │   ├─ sekito.v_entries     — keiba.race_entries + odds の sekito 向けビュー
  │   ├─ sekito.v_horse_runs  — keiba+chihou race_results 統合 view（POG 用・重複排除済）
  │   └─ sekito.mv_horse_runs — 同マテビュー（LISTEN/NOTIFY イベント駆動 REFRESH）
  └─ chihou.*    — 地方競馬（UmaConn経由）
```

## 技術スタック
- Backend: Python 3.12+ / FastAPI / SQLAlchemy 2.0 / Alembic
- Frontend: Next.js 14 (App Router) / Tailwind CSS / shadcn/ui / Recharts
- DB: PostgreSQL (VPS既存) / schema: keiba
- Windows Agent: Python 3.x 32bit / pywin32 / JV-Link COM
- パッケージ管理: uv (Python) / pnpm (Node)
- コード品質: Ruff (Python) / ESLint + Prettier (TS)
- テスト: pytest (Python) / Vitest (TS)

## 開発ルール
- Python: Ruff準拠、型ヒント必須、docstring必須
- TypeScript: strict mode、ESLint準拠
- テスト: 指数計算ロジックは必ずユニットテスト作成
- DB: keiba スキーマ（メイン）+ sekito スキーマ（穴ぐさ等外部データ）を使用。Alembic経由のみでDDL変更
- 環境変数: .env に記載、コードにハードコードしない
- Git: .env は絶対にコミットしない

## 並列開発プロトコル（複数 Claude Code セッションを同時に走らせる場合）

**同じフォルダで複数の Claude Code を動かしてはいけない。** 作業ツリー（未コミット変更・
インデックス）が共有され、片方のチェックアウトがもう片方の作業を破壊する。
並列作業は必ず **worktree で物理的に隔離**する。

### 3本柱とファイル所有

kiseki は競輪 / 中央競馬 / 地方競馬が 1 リポジトリに同居する。判定の唯一の情報源は
`scripts/dev/pillars.sh` の `pillar_of`。

| 柱 | 主な担当 |
|---|---|
| `keirin` | **`keirin/`（2026-08-10 に別リポジトリから統合）**, `api/keirin_router.py`, `api/yoso_router.py`, `db/keirin_models.py`, `netkeirin/` |
| `chihou` | `api/chihou_*.py`, `db/chihou_models.py`, `importers/chihou_*.py`, `indices/chihou_*.py`, `services/chihou_*.py`, `chihou_protocol.py` |
| `jra` | `indices/`(chihou以外), `importers/`(chihou以外), `windows-agent/`, `api/{races,horses,performance,recommendations,agent_router,import_router}.py` |
| `shared` | `db/models.py`, `db/session.py`, **`backend/alembic/`**, `utils/`, `main.py`, `config.py`, `indices/{base,composite}.py`, `betting/`, `api/{access,users,ws_manager}.py`, `.github/`, `CLAUDE.md` |

**`shared` を触る作業は並列にしない。** 単独 PR で最優先に main へ入れ、他ブランチはその後に rebase する。

### 基本フロー

```bash
# 1. 改修要望を並列可能なタスクに分解（Wave 構成が出る）
/pd-split 競輪の並び予想を修正し、地方のLightGBM特徴量を追加し、中央の指数重みを再最適化したい

# 2. 柱ごとに隔離された worktree を作る
bash scripts/dev/wt.sh new keirin narabi-fix
# → ../kiseki-wt/keirin/narabi-fix に feat/keirin-narabi-fix が作られる
#    そのフォルダで「別の」Claude Code を起動して作業する

# 3. 作業後、コミット前に必ず
/pd-preflight

# 4. 完了したら統合PMが順次マージ
/pd-integrate
```

### 鉄則

1. **`main` では作業しない。** main は常にクリーンでデプロイ可能な trunk として保つ。
   作業は必ず worktree 上の feature ブランチで行う。
   **2026-08-03 から branch protection で機械的に強制**している（下記）。
2. **1 ブランチ = 1 柱。** 柱をまたぐ変更は分割する。
3. **Alembic を並列生成しない。** 同一 Wave で複数タスクがマイグレーションを作る計画は禁止。
   新規 revision は `--rev-id "$(date +%Y%m%d%H%M)_<柱>"` で明示指定する
   （既存の `a1b2c3...z5a6b7` 連番形式は枯渇済み・ファイル名重複を起こしているため使わない）。
   このID形式は `check_migrations.sh` の**接頭辞一致**チェックを通る（`split("_")[0]` 比較ではない）。
4. **着手前に衝突を偵察する。** `bash scripts/dev/scan_collisions.sh`
5. **マージは順次。** 1本マージ → 検証 → 残りを rebase → 次。並列マージ禁止。
6. **衝突は後発ブランチ側で rebase 解決する。** 統合先で無理に解決しない。

### branch protection（2026-08-03 設定）

`main` は保護済み。**直接 push はできない**（push 時点では CI 未実行で required check が
通らないため）。変更は必ず PR 経由で入れる。

| 設定 | 値 |
|---|---|
| required status check | `Guards (並列開発ガード)` のみ |
| `enforce_admins` | `true`（管理者も対象） |
| required reviews | なし（1人開発のためレビュアーが立てられない） |
| `strict`（最新追従の強制） | `false`（毎回の rebase を強制しない） |
| force push / branch 削除 | 禁止 |

障害対応などで直接 push が必要な場合は一時解除する:
```bash
gh api -X DELETE repos/nyanta-kun/kiseki/branches/main/protection   # 解除
# 作業後に再設定（contexts は check-run 名と完全一致させること）
gh api -X PUT repos/nyanta-kun/kiseki/branches/main/protection --input - <<'EOF'
{"required_status_checks":{"strict":false,"contexts":["Guards (並列開発ガード)"]},
 "enforce_admins":true,"required_pull_request_reviews":null,"restrictions":null,
 "allow_force_pushes":false,"allow_deletions":false}
EOF
```
管理者を常に素通しにしたい場合は `enforce_admins` を `false` にする。

**`guards` ジョブでビルドを止められるのは Alembic 整合チェックのみ**。柱(pillar)判定は
`OWNERSHIP_STRICT` 未設定時に必ず 0 を返す設計で、CI では Step Summary へのレポート出力に
留めている（柱をまたぐ正当な PR まで落としてしまうため）。

### ツール一覧

| コマンド | 用途 |
|---|---|
| `/pd-status` | 全体状況（worktree・稼働ブランチ・柱・Alembic） |
| `/pd-split <要望>` | 要望を並列タスクへ分解（`task-splitter`） |
| `/pd-new <柱> <トピック>` | worktree + ブランチ作成（事前に衝突偵察） |
| `/pd-preflight` | コミット前の総合チェック |
| `/pd-integrate` | 順次マージ統合（`pm-integrator`） |

| サブエージェント | 役割 |
|---|---|
| `task-splitter` | 要望を低衝突な独立タスクへ分解 |
| `conflict-scout` | 着手前の衝突偵察 |
| `migration-guard` | Alembic head 分岐の検査・修復 |
| `pm-integrator` | 統合の統括（順序決定・順次マージ・検証） |

| スクリプト | 用途 |
|---|---|
| `scripts/dev/wt.sh` | worktree の new / list / sync / rm |
| `scripts/dev/check_migrations.sh` | Alembic head・ID重複・親不明の検査 |
| `scripts/dev/check_ownership.sh` | 変更ファイルの柱判定・shared 警告 |
| `scripts/dev/scan_collisions.sh` | ブランチ間の同一ファイル変更を検出 |
| `scripts/dev/preflight.sh` | コミット前の総合チェック（下記の注意点あり） |
| `scripts/dev/integrate.sh` | 順次マージ（`--plan` / `--dry-run` 対応） |
| `scripts/dev/pd_status.sh` | ダッシュボード |

### ハーネス実装上の注意点（実運用で踏んだもの・2026-08-03）

- **`preflight.sh` の Python 実行系**: `uv` を優先し、無ければ `backend/.venv/bin` を使う。
  **この Mac には `uv` が入っていない**ため、フォールバックが無いと ruff/mypy/pytest が
  一度も走らないまま「✓ 通過」を返す。実行系が両方とも無い場合は skip ではなく**失敗**扱い。
- **検査の起動条件はファイル種別で判定する**（`^backend/` のような粗い判定にしない）。
  `backend/data/` の生成物や `backend/models/` の学習済みモデルを置いただけで
  ruff/mypy/pytest が起動してしまうため。未追跡ファイルは対象に**含める**
  （新規追加した `.py` / `.ts` も検査するため）。
- **`scan_collisions.sh` は常に exit 0**。同じファイルを触ること自体は違反ではなく、
  ここで落とすと作業中ブランチや消し忘れブランチがあるだけで preflight が通らなくなる。
  `preflight.sh` のブロック条件にしてはいけない。
- **`mapfile` を使わない**。macOS 標準の bash 3.2 には無く、CI（bash 5）では気づけない。
- **`integrate.sh` のロールバックは `HEAD~1` ではなくマージ前の SHA へ戻す**。
  「Already up to date」等でマージコミットが作られなかった場合、`HEAD~1` は統合先の
  既存コミットを指すため `reset --hard` が無関係な作業を破壊する。

## 競輪（keirin/）— 2026-08-10 に別リポジトリから統合

`nyanta-kun/keirin` を `keirin/` へ移植した。デプロイと CI/CD の一本化が目的。
経緯と手順は `docs/keirin_repo_merge_plan.md` / `keirin/MIGRATION.md`。

- **履歴は移植元リポジトリにある**（最終SHA `50a6658`）。`git blame` はそちらで見る
- **`keirin/data/` と `keirin/.venv` は git 管理外**。VPS 上の実体を使う
  （モデル 212MB・実行時状態 159MB・venv 549MB）
- 日次バッチは **Docker ではなくホスト上の素 cron**（kiseki の JRA/地方バッチと同じ方式）。
  `KEIRIN_HOME` を crontab の環境変数で切り替える
- ⚠️ **入稿・採点経路は壊れても例外が出ない箇所が多い。**
  変更後は翌朝の `[marquee]` ログと `netkeirin_submissions` の件数を必ず突き合わせる

### 看板レース（決勝・特選クラス）

売上は看板レースに集中する（2026-08-08 実測: 当日売上の84%）。
**看板レースとその前後には必ず推奨を出す**方針（2026-08-09 ユーザー決定）。

- 判定の**唯一の正本**: `backend/src/services/keirin_marquee.py`（API が `is_marquee` を返す）
- 入稿の実行側 `keirin/src/marquee.py` は**その正本をファイル読み込みして束縛する**
  （2026-08-11 一本化）。キーワードをそちらへ写すと `test_marquee.py` /
  `test_keirin_marquee.py` が落ちる。「前後1R」の展開だけが keirin 側の責務
- ⚠️ **正本には標準ライブラリ以外を import しない**。keirin は自分の venv
  （FastAPI も SQLAlchemy も無い）からこのファイルを直接読むため、依存を足すと
  **Web は無事なまま入稿だけが落ちる**
- ⚠️ **「準決勝」は「決勝」を部分一致で拾う**。除外しないと全体の約14.5%が看板になる

### netkeirin 売上データと「分析」タブ（2026-08-11）

netkeirin の分析支援ツール「予想家成績状況」
（`umaiaggre.yosoka.netkeiba.com/tool_keirin/result/yosoka_result.html`）を
**日別とレース別の2粒度**でスクレイピングし、`/keirin/stats` の「分析」タブで
売上×的中の相関を見る。

| 粒度 | テーブル | 使う画面 |
|---|---|---|
| 日別（`list_detail=day`） | `keirin.netkeirin_sales_daily` | 売上タブ |
| レース別（`list_detail=race`） | `keirin.netkeirin_sales_race` | 分析タブ |

- 取得は `scripts/scrape_netkeirin_sales.sh`（**VPS cron 毎日9:40**・引数なしで両方）。
  サイト側の集計確定は **9:30 頃**（2026-08-14 実測・それまで 10:30 に取っていた）。
  「通常集計日はレース日の翌日」「売上は速報値」のため毎回 UPSERT で上書きする
- **列構成は日別とレース別で完全に同一**。違うのは集計IDの桁数だけ（8桁 / 12桁）なので、
  振り分けを間違えても全列が埋まり値も自然に見える。`tests/test_scrape_netkeirin_sales.py`
  が `re.fullmatch` の振り分けを固定している
- レース別の集計ID `202608104808` → `race_key` `20260810_48_08` を派生列として持つ。
  **netkeirin の場コードは `keirin.venue_info.venue_code` と同一体系**（2026-08-11 確認）
- ⚠️ **ランクの結合キーは `netkeirin_submissions.netkeirin_race_id`**。
  `picks_history.race_key` は `20260801_13_05#7C` とランク接尾辞つきで
  1レースが複数ランクに並ぶため使えない
- ⚠️ **「的中」は2種類ある**。`n_hits_incl_garami`（買い目が当たった）と
  `n_hits_excl_garami`（払戻＞賭け金・**netkeirin の表示的中率はこちら**）。
  差がガミ。相関・タイムラインはガミ含む、サマリーの的中率は両方出す
- ⚠️ **売上は `sold_paid_points`（販売*有償*pt）**。`sold_points` には無償ptが混ざり
  収益にならない（`NETKEIRIN_REVENUE_RATE` を掛ける対象も有償pt）
- 計算本体は `backend/src/services/keirin_sales_analysis.py`（DB にも FastAPI にも
  依存しない純関数）。API は `GET /api/keirin/netkeirin-analysis`
- 開催時間帯の判定は `api/keirin_meeting.py` が正本。発走時刻が取れない開催は
  `unknown` として積み、勝手にどれかへ倒さない

## DBスキーマ構成
- `keiba.*` — races / race_entries / horses / calculated_indices 等メインデータ
- `sekito.anagusa` — 穴ぐさピック情報（date, course_code, race_no, horse_no, rank A/B/C）
  - course_code は JSPK/JHKD/JFKS/JNGT/JTOK/JNKY/JCKO/JKYO/JHSN/JKKR（sekito独自コード）
  - `has_anagusa` 判定はスコア閾値でなく sekito.anagusa のピック有無で行う
  - `anagusa_rank`（A/B/C）は API の `HorseIndexOut` レスポンスに含まれる（DBには未格納）

## コミュニケーションルール
- **応答は常に日本語で行うこと**

## Agent実装パターン
各指数Agentは `backend/src/indices/base.py` の `IndexCalculator` を継承：
```python
class IndexCalculator(ABC):
    @abstractmethod
    def calculate(self, race_id: int, horse_id: int) -> float: ...
    @abstractmethod
    def calculate_batch(self, race_id: int) -> dict[int, float]: ...
```
- 各Agentは独立してテスト可能であること
- 再算出対応: version番号をインクリメントして管理

## 変更検知・再算出ルール
- 出走取消/除外 → そのレース全馬を再算出
- 騎手変更 → 該当馬の騎手指数 + 全馬の展開指数を再算出
- 斤量変更 → 該当馬のスピード指数のみ再算出
- **馬体重の到着 → そのレースを再算出**（`/api/import/weights`・2026-08-08 実装）

### 馬体重による再算出（当日の指数が馬体重なしのままになるのを防ぐ）

`horse_weight` / `weight_change` は総合指数 v27 の特徴量だが、**当日の一括算出は
VPS cron の 07:30 JST 一回きり**（`scripts/jra_calculate_trigger.sh`）で、
**馬体重が届くのは発走の約1時間前**（realtime の `0B11`）。以前は取り込むだけで
再算出しなかったため、当日の指数は最後まで馬体重なしだった
（実測でレース内 sd が約半分に潰れる）。

🔴 **さらにその手前で、0B11 は本番で一度も取り込まれていなかった**（2026-08-08 判明）。
`0B11` が返すのは**全て `WH` レコード**だが、`import_weights` は受け取った分を
`RaceImporter` へ渡すだけで、`RaceImporter` は `rec_id` が `RA`/`SE` のものしか見ない。
そのため 23件/回が毎回まるごと捨てられ **200 が返り続けていた**。
当時「馬体重あり」に見えたのは結果取込の副産物だった。
（⚠️ 旧記述の「`race_entries.horse_weight` は 1〜3着馬にしか入っていない」は**恒常的な
状態としては誤り**。週次の蓄積系 SE 取込で全馬に入るため実測は全期間 99.6〜100%。
1〜3着だけになるのは蓄積系が走る前の一時的な状態で、0B11 の意義は
「**発走前に**入るようになった」ことであって「初めて入るようになった」ことではない）
`parse_wh()` を新設して `WH` を専用経路へ振り分ける（`_apply_wh_records`）。
**振り分けが壊れると同じ無言の取りこぼしに戻る**ので
`test_weight_recalc_trigger.py` で経路自体を固定している。

`import_weights` は取込の前後で**レースごとの `horse_weight` 充足数**を比較し、
**増えたレースだけ** `CompositeIndexCalculator.calculate_and_save()` を
BackgroundTask で走らせる。realtime は同じ 0B11 を約30秒ごとに投げてくるので、
**「増えた分だけ」という差分条件が無いと全レースを延々と再算出し続ける**。
検査: `backend/tests/test_weight_recalc_trigger.py`（差分条件を潰すと落ちることを確認済み）。

⚠️ **過去日の `calculated_indices` と当日朝の値を比べてはいけない。**
過去分はレース後にバックフィルされた行で馬体重が入っており、当日朝より必ず分散が大きい。

## JRA-VAN データ取得
- TARGETは使用しない。JV-Link SDKを直接Pythonから操作
- Windows側: Python 32bit + pywin32 でCOM経由
- 蓄積系: JVOpen() で出馬表・成績・血統・調教を取得
- 速報系: JVRTOpen() でオッズ全券種・リアルタイム通知を取得
- JV-Linkは同時1接続のみ。TARGET使用時はスクリプトを停止すること
- JVRead 戻り値: 0=EOF, -1=ファイル切り替わり(継続), -3=ダウンロード中(待機), <-3=エラー

### サーバーメンテナンス窓では JVOpen を呼ばない（2026-08-04）

**JRA-VAN の定期メンテナンスは毎月第一火曜 8:00〜15:00**（公式FAQ）。
この間に `JVOpen` を呼ぶと `rc=-504`（サーバーメンテナンス中）が返るが、問題はそこではなく、
**JV-Link がモーダルダイアログを出してデスクトップセッションを掴む**こと。
エージェントは `pythonw.exe` 起動でダイアログを閉じる者がいないため COM がブロックする。

> 2026-08-12 以降は `windows-agent/jvlink_dialog_guard.py` が `BlockingCallGuard` から
> 自動応答するので、この型のブロックは自力で復帰する。ただし**窓では最初から呼ばない**
> 方針は変えない（メンテナンス中は呼んでも無駄で、処理枠を食うだけのため）。
> 同じ型の別事例と調べ方は「jvlink_agent トラブルシューティング」を参照。

> 実測 2026-08-04 13:41: JVOpen が **1193秒（約20分）**待たされた末に -504。
> `jvlink_historical` の `time_limit=7200` 秒の処理枠をそれだけで食い潰した。

したがって「rc を見てから諦める」では足りない。**既知の窓では最初から呼ばない**。

- 判定: `windows-agent/jvlink_maintenance.py` / テスト: `windows-agent/tests/test_jvlink_maintenance.py`
- 窓は `.env` の `JVLINK_MAINTENANCE_WINDOWS` で設定。**既定は `TUE 08:00-15:00`**
  - 書式（カンマ区切りで混在可）: `TUE 08:00-15:00`（毎週）/ `1ST-TUE 08:00-15:00`（毎月第一）/
    `2026-09-10 09:00-12:00`（特定日・臨時メンテ用）
  - 公式記載は「第一火曜」だが**それ以外の火曜にも観測されている**ため既定は毎週火曜。
    JRA は火曜に開催しないので、失うのは同日の蓄積系バックフィル枠だけ
  - 開始時刻ちょうどは窓の**中**、終了時刻ちょうどは窓の**外**。日跨ぎ指定は未対応
  - 窓を広げるときは**開催日と重ならないこと**（realtime の JVRTOpen も止まる）

**JVOpen 戻り値は分類して扱う**。従来は `rc<0` を一律 ERROR にしていたため、
待てば直る -504 と復旧作業が要る -303 がログ上で区別できなかった。

| 区分 | rc | 扱い |
|---|---|---|
| `no_data` | -1 | INFO・正常 |
| `maintenance` | -504, -431 | WARNING。その回は丸ごと見送り、次回実行に委ねる |
| `transient` | -402, -403, -411〜-413, -421, -502, -503 | WARNING。次回実行に委ねる |
| `fatal` | -303, -111 等（未知のコードも含む） | ERROR |

`-413` = 通信確立不可（ネットワーク／セキュリティソフト）。VM の DNS 不安定時に出る。

### 速報系データスペック（JVRTOpen）
| DataSpec | 内容 | key形式 |
|----------|------|---------|
| `0B12` | 速報成績（払戻確定後）| YYYYMMDDJJRR（12文字: 日付8+場所コード2+レース番号2） |
| `0B11` | 速報馬体重 | YYYYMMDD |
| `0B31` | 速報単複枠オッズ（O1レコード）| レースキー16文字 |
| `0B15` | 速報レース情報（出走取消・騎手変更等）| YYYYMMDD |

- **0B12 は RA/SE/HR を返す。RA には馬場状態・天候・ラップ・前半3F が入っている**
  （2026-08-02 以前は SE/HR しか拾っておらず、`races.condition` が週次の蓄積系取込まで
  NULL のままだったため当日の `going_pedigree_index` が全馬ニュートラルになっていた）
- 発走前 RA（データ区分 1:出走馬名表 / 2:出馬表）は馬場状態等が空。`race_importer` は
  condition/weather/first_3f/last_3f_race/lap_times/finishers_count を COALESCE で
  **非 NULL のときだけ更新**し、確定値を空データで潰さないようにしている
- O1レコードには単勝・複勝・枠連の3種が含まれる（O2=馬連、O3=ワイド、O4=枠連 ではない）
- 複勝オッズは最低倍率（low）を使用（最高倍率は参考値）
- realtimeループは約30秒ごとに全36レースキーで各DataSpecをポーリング

## データパイプライン

```
Windows Agent
  JVRead() → SJIS固定長バイナリ文字列
      ↓
  jvlink_parser.py（parse_ra / parse_se）
      ↓ フィールド抽出（1-indexed バイト位置）
  race_importer.py
      ↓ 型変換（MSST→秒, 斤量→kg）
  PostgreSQL (keibaスキーマ)
      ↓
  SpeedIndexCalculator.calculate_batch()
      ↓ 基準タイム比較・加重平均
  calculated_indices テーブル
```

## パーサー実装上の注意点（jvlink_parser.py）

**SJISエンコーディング**
- JVRead は SJIS バイトを Latin-1 として返す（1 Python文字 = 1 SJISバイト）
- バイト位置 = Python文字列インデックス（ずれなし）
- 漢字フィールドは `raw.encode('latin-1').decode('cp932')` で正しくデコードする
- ASCII数字フィールドは変換不要（`data[start-1:end]` でそのまま使用）

**フィールド位置の読み方（JVDF v4.9仕様書）**
- 仕様書のバイト位置は **1-indexed**
- Python では `data[pos-1 : pos-1+length]` または `data[start-1 : end]` で取得

**共通ヘッダー（RA/SE/AV/JC 共通, pos 1-27）**
- pos 4-11: データ作成年月日（≠ 開催日）
- pos 12-15: 開催年（4桁）
- pos 16-19: 開催月日（4桁）← この2フィールドを結合して実際の開催日を構成
- `race_date = year + month_day` が `Race.date` に格納される値

**走破タイム（MSST形式, pos 339-342, 4バイト）**
- "MSST" = 分(1桁) + 秒(2桁) + 1/10秒(1桁)
- 例: "1345" → 1分34.5秒 → `1*600 + 34*10 + 5 = 945`（0.1秒単位整数）
- DB格納時: `Decimal('94.5')` 秒に変換（÷10）

**後3ハロン（SST形式, pos 391-393, 3バイト）**
- "SST" = 秒(2桁) + 1/10秒(1桁)
- 例: "336" → int 336 → DB格納時: `Decimal('33.6')` 秒

**トラックコード（コード表2009, pos 706-707, 2バイト）**
- 1x = 芝, 2x = ダート, 5x = 障害
- `TRACK_CODE_MAP` で (surface, direction) に変換

**レースID形式（16文字）**
- `year(4) + month_day(4) + course(2) + kai(2) + day(2) + race_num(2)`
- 例: `"2026032205010105"`

## 重要な定数
- 斤量補正: 1kg = 約0.5秒（距離係数で調整）
- スピード指数基準: 平均=50、標準偏差=10
- 期待値購入閾値: 1.2以上

## Auth.js v5 (next-auth@beta) 認証構成

### Next.js 16 + Auth.js の既知の罠（解決済み）

**① route.ts での basePath 手動注入**
Next.js 16 は App Router ルートハンドラに渡す `req.url` から basePath を除去する。
Auth.js は `AUTH_URL` のパス部分を `config.basePath` として使いアクションを解析するが、
除去後の URL では `UnknownAction` → 502 となる。
`frontend/src/app/api/auth/[...nextauth]/route.ts` の `injectBasePath()` でbasePath復元済み。
galloplab.com は basePath なし（`AUTH_URL=https://galloplab.com/api/auth`）のため `injectBasePath()` は no-op。

**② Auth.js セッショントークンは JWE（暗号化）**
Auth.js は `EncryptJWT`/`jwtDecrypt`（A256CBC-HS512）でセッションを暗号化する。
`jwtVerify`（JWS署名検証）は使用不可。`proxy.ts` ではカスタム検証せず `auth()` ラッパーを使う。

**③ proxy.ts での nextUrl.pathname に basePath が混入する**
`auth()` ラッパー内の `reqWithEnvURL` が NextRequest を再構築する際、
`nextUrl.pathname` に basePath が混入する場合がある（サブパス運用時）。
→ `proxy.ts` では pathname 使用前に手動で basePath を除去すること。

**④ AUTH_URL の形式**
`AUTH_URL` は `/api/auth` まで含める形式（例: `https://galloplab.com/api/auth`）。
`/api/auth` まで含めることで `config.basePath` が正しく解決され、コールバック URL が正確に生成される。

## Windows操作（Parallels経由）

**前提**: Windows 11はParallels VMとして動作。Mac側の `Z:\GitHub\kiseki\` にプロジェクトがマウント済み。

### コマンド実行（SSH推奨 — ウィンドウちらつきなし）

**SSH接続を優先して使うこと**。`prlctl exec --current-user` はコマンド実行のたびに ConHost ウィンドウがちらつく。

```bash
# SSH経由でPowerShellコマンド実行（推奨）
ssh windows-vm "powershell -Command \"コマンド\""

# ファイル転送（Mac→Windows）: Zドライブ経由（SSH不要）
# WindowsからZ:\GitHub\kiseki\に直接アクセス可能

# ログ確認
ssh windows-vm "Get-Content C:\kiseki\windows-agent\jvlink_agent.log -Tail 50"
```

SSH接続設定（~/.ssh/config）:
```
Host windows-vm
    # 2026-08-02: mDNS (.local) の名前解決が落ちて ssh も prlctl exec も到達不能になった。
    # Parallels 共有ネットワークの IP を直接使う。変わったら `prlctl list -f` で確認する。
    # mDNS 名で繋ぎたい場合は windows-vm-mdns（同設定で HostName が .local）を使う。
    HostName 10.211.55.6
    User ysuzuki
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
```

**SSH接続できない場合（VM停止中・起動直後等）のフォールバック**:
```bash
# prlctl exec は --current-user なし（SYSTEM権限）を優先、なければ --current-user
prlctl exec "Windows 11" powershell -Command "コマンド"
prlctl exec "Windows 11" --current-user powershell -Command "コマンド"
```

### ログ確認
```bash
# SSH経由（推奨）
ssh windows-vm "Get-Content C:\kiseki\windows-agent\jvlink_agent.log -Tail 50"
# フォールバック
prlctl exec "Windows 11" --current-user powershell -Command "Get-Content 'C:\kiseki\windows-agent\jvlink_agent.log' -Tail 50"
```

### Windows VM再起動
```bash
# ※ prlctl restart は Windows を実際に再起動しない（uptimeがリセットされない）
# 必ず shutdown /r /t 0 を使うこと
prlctl exec "Windows 11" --current-user powershell -Command "shutdown /r /t 0"
# 再起動完了を待つ（約1〜2分）
until prlctl exec "Windows 11" --current-user powershell -Command "Write-Output 'ready'" 2>/dev/null | grep -q ready; do sleep 5; done
```

### Windows Terminal ウィンドウが繰り返し表示される問題（解決済み）

**症状**: `prlctl exec "Windows 11" --current-user` 実行後、Windows 11 で PowerShell ウィンドウが数秒おきに繰り返し表示される。

**根本原因**:
1. Windows 11 のデフォルトターミナルが Windows Terminal に変更されており、`--current-user` で起動したコンソールプロセスが全て Windows Terminal 経由でウィンドウを生成する
2. `prlctl exec` がハングすると Parallels Tools Service（PID 3736 の `prl_tools_service.exe`）が約7秒おきにリトライし、毎回新しいウィンドウが出現する

**恒久対策①（実施済み）**: `set_conhost.py` でデフォルトターミナルを ConHost に変更
```bash
prlctl exec "Windows 11" --current-user powershell -Command "C:\Python312-32\python.exe C:\kiseki\windows-agent\set_conhost.py"
```
`HKCU\Console\%Startup\DelegateFocusToConsoleHost=1` を設定。以後 `prlctl exec --current-user` でウィンドウが出なくなる。

**恒久対策②（実施済み）**: Mac 側 LaunchAgent で 90秒以上ハングした `prlctl exec` を自動 kill
- ファイル: `~/Library/LaunchAgents/com.kiseki.prlctl-watchdog.plist`
- 30秒ごとに実行。経過時間 > 90秒の `prlctl exec` プロセスを自動 kill
- Mac 起動時に自動有効（launchd 管理）
```bash
# 状態確認
launchctl list com.kiseki.prlctl-watchdog
# 手動停止（通常不要）
launchctl unload ~/Library/LaunchAgents/com.kiseki.prlctl-watchdog.plist
```

**ハングプロセスの手動確認と kill（緊急時）**:
```bash
ps aux | grep "prlctl exec" | grep -v grep
pkill -9 -f "prlctl exec"
```

### Windows agent 設定ファイル
- **`.env` の場所**: `C:\kiseki\.env`（`jvlink_agent.py` は `Path(__file__).parent.parent / ".env"` を読む）
- `C:\kiseki\windows-agent\.env` は読まれない（混同注意）
- **BACKEND_URL**: `https://api.galloplab.com`（VPS FastAPI に直接 POST。Mac を経由しないため Mac-VPS 間 RTT を排除）
  - 旧値: `http://YuichironoMacBook-Pro-6.local:8000`（Mac経由。DB書き込みのたびに VPS RTT が発生していた）
  - `10.211.55.2`（Parallels NAT）はWindowsから到達不可なので使用不可
  - `192.168.11.x`（WiFi IP）は変動するので使用不可

### JV-Link / UmaConn 同時接続について（重要）
- **JV-Linkは同一PCで realtime + setup/daily/recent を同時起動できる**（検証済み 2026-04-13）
  - 実際の認証は `HKLM:\Software\WOW6432Node\JRA-VAN Data Lab.\uid_pass\servicekey` で行われる
  - `JRAVAN_SID`（.env）は任意のラベル文字列。認証には無関係（"kiseki"のままでよい）
  - 第2利用キーや `JRAVAN_SID_2` は不要。複数 COM インスタンスが独立動作する
- **UmaConnも同様に realtime + setup を同時起動できる**（検証済み 2026-04-13）
  - 追加API_KEY不要。`NVSetServiceKey rc=-101`（2回目）は正常（既登録の意味）
  - **PC-KEIBA アプリ不要**。NVDTLab.dll（UmaConn SDK）は PC-KEIBA なしで直接動作（2026-04-13 実機確認）
  - 認証は `HKLM\SOFTWARE\WOW6432Node\RateBuster Co.,Ltd\UmaConn\3.5.4.0` で管理（PC-KEIBAのDB設定とは無関係）
- **umaconn_agent realtimeモード 自動管理**（2026-04-18 実装・2026-04-28 安定化強化）
  - Windowsタスクスケジューラ（`kiseki-UmaConn-Realtime`）が毎朝9:00に自動起動
  - 自動停止: 最終レース発走+90分 or 21:30ハードストップ（先に来た方）
  - ウォッチドッグ: NVRTOpenハングを600秒で検知 → `os._exit(1)` 強制終了 → 翌朝9:00に自動復帰（umaconn_agent）
  - タスク状態確認: `ssh windows-vm "schtasks /query /tn 'kiseki-UmaConn-Realtime' /fo list"`
- **realtime 安定化のためのバックアップ・監視タスク**（2026-04-28 追加）
  - `kiseki-UmaConn-FetchResults`: **5分おき** (10:00-22:30) に `umaconn_agent.py --mode fetch-results --fetch-date {today}` を自動実行
    - realtime の 0B12 worker が止まっても結果取得を確実化
    - スクリプト: `C:\kiseki\windows-agent\run_umaconn_fetch_results.vbs`
    - **多重起動禁止**（2026-08-04 追加）。実行中なら起動しない。`STUCK_MINUTES`(30分) 超は taskkill
      - Why: 所要時間はレース数に比例し、46レースあった 2026-08-04 は **1回21分**（17:28→17:49）
        かかった。ガードが無かったため **4本が同時に走り**（16:35/16:46/16:52/16:56）
        UmaConn COM を奪い合って `NVSetServiceKey` が60秒タイムアウト。最古は41分居座った
      - 回収に `taskkill`(=`TerminateProcess`) を使うのは意図的。`DLL_PROCESS_DETACH` を
        走らせないので UmaConn の FastMM リークダイアログを出さずに落とせる（下記参照）
  - `kiseki-UmaConn-Backfill` / `kiseki-UmaConn-Backfill-Stop`: **夜間 23:50 起動 / 翌 08:30 停止**（2026-08-13 追加）
    - 蓄積系（`NVOpen` / RACE dataspec）で `--mode recent --from-year 2024` を回し、
      速報系では埋まらない過去の結果欠損と払戻を回収する
    - ⚠️ **日中に走らせてはいけない**。`from_time` に上限が無いため 2024-01 以降の
      **全ファイル（実測 12,283 本）を再ダウンロード**する動きになり、UmaConn COM を
      長時間占有して当日のオッズ・結果収集を壊す
      （2026-08-13 実測: NVOpen rc=0 / DL数 12,283 / **37分間 NVRead=-3 のまま0件**）
    - `run_umaconn_backfill.vbs` は **realtime が動いていれば起動しない**。
      多重起動もしない（冪等）。ファイルは到着ごとに `mark_file_completed` されるので
      途中で止めても進捗は残り、**複数夜に分割できる**
    - 停止に `Terminate`(=TerminateProcess) を使うのは意図的。`DLL_PROCESS_DETACH` を
      走らせないので NVDTLab.dll(FastMM) のリークダイアログを出さずに落とせる
    - ログ: `C:\kiseki\windows-agent\backfill.log`
    - 登録: `powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_backfill_task.ps1`
  - `kiseki-UmaConn-Watchdog`: **5分おき** (9:00-22:30) に realtime を監視（2026-04-30 から jvlink も対象・2026-08-02 にストール検知を追加・2026-08-03 にサービス検知を追加・2026-08-04 に日跨ぎ検知と起動猶予を追加・2026-08-20 に kill 失敗の検知を追加）
    - **[1] 不在**: プロセスが無ければ `kiseki-UmaConn-Realtime` / `kiseki-JVLink-Realtime` を実行
    - **[2] ストール**: プロセスは生きているが `data\realtime_heartbeat_{jvlink,umaconn}.txt` が
      **15分以上更新されていない**場合、taskkill してから再起動する
      - Why: 2026-08-02 に JVRTOpen が COM レベルでハングし、**プロセス内ウォッチドッグスレッドごと
        凍結**して `os._exit(1)` が発火せず、約95分オッズ取得が死んだ。生存確認だけでは検知できない
      - ハートビートは各エージェントのウォッチドッグスレッドが30秒ごとに書き出す
        （`write_heartbeat_file()`）。ファイルが無い場合はストール判定をスキップ（旧版との互換）
      - **起動猶予がある**（2026-08-04 追加）。heartbeat ファイルは**プロセスをまたいで使い回される**ため、
        起動直後のプロセスは「前のプロセスが残した古いファイル」で判定されてしまう。
        最初の heartbeat が出るのは起動30秒後で、初期化が詰まればさらに遅れる。
        → heartbeat が古くても**プロセス起動から 15分経っていなければ猶予**する。
        本当に固まっていれば猶予明けに捕まえられるので取りこぼしは無い。起動時刻が読めない場合は停止扱い（安全側）
        - Why: これが無いと、上記の COM 競合と噛み合って「起動 → 15分でkill → 再起動」を
          延々と繰り返す（2026-08-04 16:55 / 17:48 に実際に発生）
    - **[3] サービス停止**: `JVLinkAgent` Windows サービスが停止していれば起動する（2026-08-03 追加）
      - Why: サービスが止まっていると JVRTOpen/JVOpen は**エラーログを一切出さずに黙って失敗**する。
        このときエージェントのプロセスは生存し heartbeat も更新され続けるため、
        **[1] でも [2] でも検知できない独立した第3の障害モード**
      - **`net start` を直接呼んではいけない**。watchdog タスクは `RunLevel=Limited` で動作し、
        JVLinkAgent の ACL は `SERVICE_START(RP)` を `BA`/`SY` にしか与えていない
        （`IU` には無い: `D:...(A;;CCLCSWLOCRRC;;;IU)...`）。非昇格の `net start` はアクセス拒否になり、
        さらに `sh.Run(..., 0, False)` は結果を待たないため**失敗が一切表面化しない**
        （「starting」とログに残るのに何も起きない）
      - → `RunLevel=Highest` の専用タスク **`kiseki-Start-JVLinkAgent`** へ委譲する
        - 登録: `powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_start_jvlinkagent_task.ps1`
        - トリガー無し。watchdog からの `schtasks /run` でのみ起動する
        - 削除: `schtasks /delete /tn kiseki-Start-JVLinkAgent /f`
      - 実機検証済み（2026-08-03）: 停止 → 検知 → 昇格起動 → `Running` 復旧。稼働中は no-op でログも出さない
    - **[4] 日跨ぎ**: **前日以前に起動した** realtime を taskkill して再起動する（2026-08-04 追加）
      - Why: `kiseki-EOD-Cleanup` が 8/2・8/3 と2晩連続で無言死し（下記エンコーディングの項）、
        8/2 11:57 起動の jvlink realtime が **8/4 まで3日間**居座った。
        これは **[1]（プロセスは在る）・[2]（heartbeat は新鮮）・[3]（サービスは Running）の
        いずれでも検知できない**。EOD cleanup が唯一の網だったが、それが落ちていた
      - realtime は 9:00 か本 watchdog（9:00-22:30）でしか起動しないので、
        起動日が今日より前なら定義上「残り物」
    - 🔴 **`taskkill /F` は効かないことがある**（2026-08-20 追加）。
      `os._exit(1)` 直後に最後のスレッドがカーネル待ちで固まったプロセスは
      **TerminateProcess を受け付けない**（`taskkill` は「実行中のタスクのインスタンスが
      ありません」で失敗し、プロセス一覧には残り続ける。スレッド1本・CPU 加算なしが目印）。
      - 旧実装は kill の成否を見ずに [1] の再起動へ進み、ランチャ側は
        **プロセスの存在だけ**で「もう動いている」と判定して降りていた。結果
        「STALLED -> terminating / not found -> starting」を5分ごとに繰り返すだけで
        **再起動は一度も起きない**。2026-08-20 に地方のオッズが **14:55〜19:48 の4時間51分**
        停止し、発走直前のオッズが朝の値のまま表示され続けた
      - → watchdog は kill 後に存在を確認して `survived taskkill` をログに残し、
        **死体が残っていても再起動へ進む**。ランチャ（`run_{umaconn,jvlink}_realtime.vbs`）は
        「動いている」ではなく **heartbeat が新しいか（＝進んでいるか）** で判定する
      - 死体と併存しても新しいプロセスは正常に動く（COM は解放済み。同日実測）。
        死体の回収は再起動を待つ
      - ⚠️ ランチャ末尾の多重起動そうじは **直近 120秒以内に起動したプロセスだけ**を対象にする。
        「最古の1本を残す」ままだと死体が最古として生き残り、**たった今起動した健全な方**を殺す
    - watchdog は該当プロセスを**全部**辞書に集めて1本ずつ判定する（死体と健全なプロセスが
      併存しうるため。最後に見つかった1本だけを覚えていると、どちらを掴むかが WMI の列挙順まかせになる）
    - スクリプト: `C:\kiseki\windows-agent\run_realtime_watchdog.vbs`
    - ログ: `C:\kiseki\windows-agent\watchdog.log`
    - `New-ScheduledTaskAction -Execute` は**絶対パス必須**（Task Scheduler は PATH を解決せず、
      `"net.exe"` だと `LastTaskResult=2` = ERROR_FILE_NOT_FOUND で無言で失敗する）
    - スクリプトのエンコーディングは下記「Windows スクリプトのエンコーディング規約」に従うこと
  - `kiseki-EOD-Cleanup`: **毎日 23:45** に以下を強制終了（2026-04-30 新設・2026-08-02 拡張）
    - [A] `jvlink_agent` / `umaconn_agent` の `--mode realtime`（起動日時を問わず）
    - [B] 同エージェントの**モードを問わず前日以前に起動したプロセス**（ゾンビ掃除）
      - 2026-08-02 の障害: `--mode daily` が 7/16 から17日間ハングし JV-Link を占有、
        当日の realtime がオッズを1件も取得できなかった。旧実装は realtime しか掃除しなかった
      - 同日起動の正規バックフィル（`jvlink_historical` 等）は巻き添えにしない
    - スクリプト: `C:\kiseki\windows-agent\run_eod_cleanup.vbs`
    - 翌朝 9:00 起動が常にクリーンな状態になるための safety net（hung プロセスの跨ぎ防止）
    - **これ自身が落ちうる**。2026-08-02〜03 にエンコーディング由来のコンパイルエラーで
      2晩連続 `exit 1`・ログ0行のまま何もしなかった（上記「Windows スクリプトのエンコーディング規約」）。
      開始マーカー `EOD cleanup start.` を必ず書くようにしてあるので、
      **watchdog.log にこの行が無い日は EOD cleanup が起動すらしていない**
    - 取りこぼしは watchdog **[4] 日跨ぎ**が翌日回収する（二重の網）
    - `On Error Resume Next` 下でエラーを記録する関数は、**入口で `Err.Clear` すること**。
      呼び出し元の Err が残っていると「エラーを報告するための関数」が自分の Err チェックで
      弾かれ、記録したい失敗のときだけ何も書けない
  - `run_jvlink_realtime.vbs` / `run_umaconn_realtime.vbs` は冪等（同種 realtime が既に走っていればスキップ）。watchdog × daily 9:00 の二重発火で多重生成しない
  - **Why**: 4/27 の mitmproxy 停止由来 ProxyError 連発 + 4/26 jvlink_agent watchdog 600s誤発火事例（626/643秒）+ 2026-04-30 観測の jvlink_agent ゾンビ多重起動（4/28・4/29 の 9:00 起動分が残存し COM 競合）への対応
  - **jvlink_agent WATCHDOG_TIMEOUT**: 600s → **1800s**（2026-05-02）。レース間の30分待機ループで誤発火していたため延長
  - **Windowsシステムプロキシは無効化済**（`netsh winhttp reset proxy` 完了）。再有効化する場合はバックエンドAPI到達不可になるので注意
- **UmaConn の NVLink は必ず解放してからプロセスを終えること**（2026-08-04）
  - NVDTLab.dll は Delphi/FastMM 製で、解放し忘れると `DLL_PROCESS_DETACH` で
    「Unexpected Memory Leak」**モーダルダイアログ**を出す。`pythonw.exe` には閉じる者が
    いないためプロセスが終われず、**UmaConn COM を掴んだまま居座る**。
    次に起動したエージェントの NV 呼び出しがブロックされ（`NVSetServiceKey: 60秒タイムアウト`）、
    それがまたウォッチドッグの `os._exit` を誘発する**自己増殖ループ**になる
  - ダイアログに出る `TNVLink` の個数 = 生存インスタンス数。realtime は main の `nv` と
    bg worker の `nv2` の**2本**を持つので x2、fetch-results 等は x1
  - `umaconn_agent.release_nvlink()`（`NVClose` → 参照破棄 → `gc.collect()`）を必ず通す。
    `gc.collect()` まで行うのは、解放をインタプリタ終了時（＝ダイアログの発火位置）に
    持ち越さないため
  - `os._exit()` は `atexit` も `finally` も走らせない。**強制終了の直前に明示的に解放する**
    （`_exit_after_release()`）。`nv2` は生成した STA スレッド自身に閉じさせる必要があるため
    キュー経由で依頼し、完了を Event で待つ（10秒でタイムアウトして main の解放へ進む）
  - **外部から回収するときは `taskkill /F`**（`TerminateProcess`）。DLL detach を走らせないので
    ダイアログを出さずに落とせる
- **umaconn_agent の起動はデスクトップセッションが必須**（2026-04-21 確認）
  - NVDTLab.dll はシステムトレイアイコン初期化のためデスクトップセッションが必要
  - SSH経由の直接起動は `シェル通知アイコンが削除できません` エラーで初期化失敗する
  - **手動起動は `kiseki-RunAdhoc` タスクスケジューラ経由を使うこと**（ちらつきなし・`prlctl exec` 不要）
  ```bash
  # ---- kiseki-RunAdhoc 経由の起動方法（推奨・ちらつきゼロ） ----
  # adhoc_cmd.txt に「スクリプト名 + 引数のみ」を書く（cd不要・pythonw不要）
  # run_adhoc.vbs が pythonw.exe で直接起動（cmd.exe を経由しない → コンソールウィンドウなし）

  # recent モード（レース終了後の当日エントリ・結果取得）
  ssh windows-vm "echo umaconn_agent.py --mode recent --from-year 2026 > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"

  # fetch-results モード（指定日の成績を0B12で取得）
  ssh windows-vm "echo umaconn_agent.py --mode fetch-results --fetch-date 20260421 > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"

  # fetch-odds モード（指定日のオッズを0B31で取得）
  ssh windows-vm "echo umaconn_agent.py --mode fetch-odds --fetch-date 20260421 > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"

  # ログ確認
  ssh windows-vm "powershell -Command \"Get-Content 'C:\\kiseki\\windows-agent\\umaconn_agent.log' -Tail 10\""
  ```
  - **仕組み**: `kiseki-RunAdhoc` タスクは `InteractiveToken` フラグでデスクトップセッション内で `wscript.exe` を実行
  - `run_adhoc.vbs` が `adhoc_cmd.txt` の1行目を読み取り、**`pythonw.exe`（コンソールウィンドウなし）** で直接起動
  - `cmd.exe` を経由しないため Coherence モードでもちらつきゼロ
  - `prlctl exec --current-user` および `python.exe`（コンソールあり）は使用禁止

### Windows スクリプトのエンコーディング規約（2026-08-04・本番を止めた実バグ）

`cscript` と PowerShell 5.1 は **BOM 無し UTF-8 を CP932(ANSI) として読む**。
日本語の最終バイトが CP932 の先行バイト範囲 (`0x81-0x9F` / `0xE0-0xFC`) に当たると、
その文字が**次の1バイトを飲み込む**。

| 対象 | ルール | 破ったときの壊れ方 |
|---|---|---|
| 行終端 | **CRLF 必須**（`windows-agent/.gitattributes` で強制） | LF だと改行が飲まれ、次の行がコメントに吸収される |
| 文字列リテラル | **ASCII のみ** | `"` が飲まれて文字列が閉じず構文エラー |
| コメント | 日本語で可（CRLF が前提） | — |
| `.ps1` / `.bat` | UTF-8 **BOM** を付ければ文字列リテラルも日本語で可 | — |
| `.vbs` | **BOM は効かない**。ASCII で書くしかない | — |

```
' ...から yyyymmdd を取り出す      LF  : ... e3 81 99 | 0a       ← 0x99 が LF を食う
Function ProcDateStr(wmiDate)          → この行がコメントに吸収され Exit Function が宙に浮く

' ...から yyyymmdd を取り出す      CRLF: ... e3 81 99 | 0d | 0a  ← 0x99 は CR を食う。LF は無事
```

**実害の記録**:
- `run_eod_cleanup.vbs` — `6c6c75b`(2026-08-02) のコメント追加でコンパイルエラーになり、
  wscript が `exit 1` で即死。**ログを1行も残さない**まま毎晩のゾンビ掃除が停止し、
  realtime が3日間居座った（上記 watchdog [4] の背景）
- `setup_task_scheduler.ps1` / `setup_weekly_entry_tasks.ps1` — BOM 無しで日本語文字列リテラルを
  含み、**PowerShell パーサでパース不能**だった（`'&&' は有効なステートメント区切りではありません` /
  `終わりの '}' が存在しません`）。前者の `&&` は文字列内にあり、文字化けで文字列が閉じなかった結果

**検査**: `windows-agent/tests/test_windows_script_encoding.py`（CRLF / 行末先行バイト / 文字列リテラル ASCII）

⚠️ **`eol=crlf` 導入前に作られた worktree / clone は作り直しが必要**。
フィルタはチェックアウト時にしか走らないので、既にあるファイルは LF のまま残る。
`git add --renormalize` では**直らない**（index を直すだけで作業ツリーは LF のまま。
Windows への配備は作業ツリーから scp するので、それでは配備物が壊れたままになる）。

```bash
rm -f windows-agent/*.vbs windows-agent/*.ps1 windows-agent/*.bat
git checkout -- windows-agent/
```

**VBS の構文検査（副作用ゼロ）**: VBScript は全体をコンパイルしてから実行するので、
最初に通る場所に `WScript.Quit 0` を差し込めば構文だけ検査できる。
`Option Explicit` は他の実行文より前になければならないのでその直後に入れること。

### エージェント運用の落とし穴（2026-08-02 実地で踏んだもの）

**① SSH 経由の `Start-Process` はセッション断でプロセスが道連れになる**
`ssh windows-vm "powershell -Command Start-Process pythonw ..."` で起動した realtime は
**SSH コマンドが戻った瞬間に死ぬ**（1サイクルだけログを出して消える）。
常駐させるものは必ず **タスクスケジューラ経由**で起動すること:
```bash
ssh windows-vm 'schtasks /run /tn kiseki-JVLink-Realtime'
# または RunAdhoc（adhoc_cmd.txt 経由）
```

**② プロセス数の確認は `Name='pythonw.exe'` で必ず絞る**
`Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match "jvlink_agent.py"}` は
**そのクエリを実行している cmd.exe / powershell.exe 自身にマッチする**（コマンドラインに
文字列が含まれるため）。常に2件多く見え、「多重起動している」と誤診する。正しくは:
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "pythonw.exe" -and $_.CommandLine -match "jvlink_agent" }
```

**③ 起動タスクとウォッチドッグの同時発火で多重起動しうる**
ランチャ VBS の「実行中か調べる→起動する」は check-then-act の競合。
`data\launcher_{jvlink,umaconn}.lock` の更新時刻で 60 秒以内の二重起動を抑止し、
起動4秒後に重複を検出したら最古の1本だけ残すようにしている（2026-08-02 追加）。

### jvlink_agent.py 起動
※ **jvlink_agent は必ず RunAdhoc（kiseki-RunAdhoc タスクスケジューラ）経由で起動すること。**
※ SSH + Start-Process では JVDTLab.dll がデスクトップセッションを取得できず JVOpen が無限ブロックする（2026-04-25 確認）。
※ RunAdhoc は InteractiveToken でデスクトップセッション内に pythonw.exe を起動するためウィンドウちらつきなし。

```bash
# ---- kiseki-RunAdhoc 経由の起動方法（全モード共通） ----
# adhoc_cmd.txt に「スクリプト名 + 引数」を書き、schtasks /run で起動する

# recentモード（今週分データ取得。完了後に自動終了）
# ⚠️ option=2: 今週分のみ取得。数週間前のデータは届かない。
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode recent --from-year 2026\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# fix-raceモード（指定日以降のRACEデータ差分取得。過去欠損修復用）
# ✅ option=1: from_time が有効。JVOpen は数分で完了。
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode fix-race --from-date 20260425\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# realtimeモード（オッズ・成績・出走取消を30秒間隔でポーリング、常駐）
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode realtime\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# setupモード（全過去データ取得。初回のみ）
# ⚠️ option=4: from_time を無視して全期間スキャン。JVOpen呼び出し自体が数時間ブロックする。
ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"jvlink_agent.py --mode setup\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

# ログ確認
ssh windows-vm "powershell -Command \"Get-Content 'C:\\kiseki\\windows-agent\\jvlink_agent.log' -Tail 20\""
```

### jvlink_agent トラブルシューティング

#### JVOpen が無限ブロックする場合 — **まずモーダルダイアログを疑う**

> 🔴 **旧記述（「残存 JVNextCore を kill する」）は誤りだったので削除した。**
> `JVNextCore.exe` は `C:\Program Files (x86)\JRA-VAN\NEXT5\` ＝ **JV-Next(DM取得アプリ)の
> 実行体**で JV-Link Data Lab とは無関係。JVOpen 中に動くことはない（0プロセスのまま）。
> kill しても JVOpen は直らず、DM 取得側を止めるだけ。
> 2026-08-06〜08-12 に蓄積系が **6日間**止まったとき、この手順が最初の一手として
> 書かれていたことが遠回りの一因になった。

**実績のある原因は「JV-Link がモーダルを出して押されるのを待っている」**。
`pythonw.exe` には押す者がいないので永久に返らない。2026-08-06 の実例:

```
JRA-VAN DataLab.   (ウィンドウクラス #32770)
  現在のバージョンより新しいバージョン(5.0.0)のJV-Linkが存在します。
  新しいバージョンをダウンロードしますか？   [はい] [いいえ]
```

`windows-agent/jvlink_dialog_guard.py` が `BlockingCallGuard` から5秒ごとに自動応答する
ようになったので**通常は自動復帰する**。それでも止まる場合の調べ方:

```bash
# 実測診断（JVOpen をワーカースレッドで呼び、接続・CPU・ウィンドウを記録する）
ssh windows-vm 'powershell -NoProfile -Command "Set-Content -Path \"C:\kiseki\windows-agent\adhoc_cmd.txt\" -Value \"probe_jvopen_block.py TOKU 7 1\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'
scp windows-vm:C:/kiseki/windows-agent/probe_jvopen_block.log /tmp/  # 文面が出る

# ダイアログ自動応答そのものの動作確認（合成ダイアログで押せるか）
ssh windows-vm 'powershell -NoProfile -Command "Set-Content -Path \"C:\kiseki\windows-agent\adhoc_cmd.txt\" -Value \"selftest_dialog_guard.py\" -Encoding ASCII"'
ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'
```

🔴 **調べ方を間違えないための2点**（2026-08-12 に6日間誤診した原因そのもの）:

- **`Process.MainWindowHandle` でダイアログの有無を判定してはいけない。**
  この種のダイアログは非表示扱いで MainWindowHandle には出ない。
  必ず `EnumWindows` で全トップレベルを列挙し `class=#32770` を探す
- **SSH 経由の PowerShell からは対話セッションのウィンドウが見えない**（別ウィンドウ
  ステーションになる。実測: 全デスクトップで1件しか見えず、正しくは114件）。
  **ブロックしているプロセス自身から** `EnumWindows` すること

**症状が「通信ゼロ・CPUゼロ・JVLinkAgent へ接続すらしない・再起動しても再発」でも
ネットワーク障害とは限らない。** ダイアログはネットワークに出る**前**に出る。

⚠️ ダイアログの「今後表示しない」チェックは**当てにできない**（実機で入れても再出現した）。
⚠️ **「はい」を押してはいけない**（バージョンアップのダウンロードが始まる）。
`jvlink_dialog_guard` は いいえ / No / キャンセル / Cancel / OK だけを押す。

#### 効果が無かった対処（再試行しないこと）

`event` ディレクトリのクリア / `JVLinkAgent` サービス再起動 / Windows VM の再起動 /
`JVNextCore` の kill。到達性・ファイアウォール・認証・ディスク容量・DataSpec 固有・
他プロセスとの競合・デスクトップセッション不在も**すべて否定済み**。

#### JV-Link のバージョン

**製品バージョン（5.0.0 等）とファイル版数は体系が別**。`JVDTLab.dll` の FileVersion は
5.0.0 導入後も `1,1,8,0` のままなので**バージョン判定に使わないこと**。実体の更新は
ファイルサイズと日付で見る（5.0.0 で 2616320 → 2620928 バイト）。

### JVOpen option の選択指針（重要）

| 目的 | モード | option | from_time | 所要時間 |
|------|--------|--------|-----------|----------|
| 過去特定日の欠損修復 | `fix-race --from-date YYYYMMDD` | 1 | **有効** | 数分 |
| 今週・直近分の取得 | `recent` | 2 | 今週分のみ | 数分 |
| 全期間の初回取得 | `setup` | 4 | **無視** | 数時間 |

- **`--mode setup` は初回セットアップ専用**。欠損修復・再取得には使わないこと。
- **`--mode fix-race --from-date YYYYMMDD`** が過去データ修復の標準手順。
- `--mode recent` は今週以前のデータは取得不可（option=2の制約）。

### 調教データ（坂路 SLOP / ウッド WOOD）の取得

`kiseki-Chokyo-Daily`（**毎日 06:00**・`run_chokyo_daily.vbs`）が差分(option=1)で取り込む。
登録: `powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_chokyo_daily_task.ps1`
ログ: `C:\kiseki\windows-agent\chokyo.log`

- 収録範囲（JV-Data4901 仕様）: **SLOP は 2003年以降・美浦栗東両方 / WOOD は 2021-07-27以降・美浦のみ**
- 06:00 は realtime(9:00-22:30) の外で、07:00 の `kiseki-JRA-Entries-RT` より前の空き枠
- **14日遡り**で回す。追い切りは水木に集中しファイル到着も遅れるため。取得済みは
  `{SLOP,WOOD}_CHOKYO` に記録されスキップされるので窓を広げてもコストはほぼ増えない（実測60秒）
- ガード: chokyo の多重起動と jvlink realtime 稼働中はスキップ

🔴 **過去へのバックフィルはできない**（2026-08-17 実測・`probe_chokyo_retention.py`）。

| 経路 | 実測 |
|---|---|
| option=1（通常データ） | **2025-05-27 が保持限界**。SLOP は 726 ファイル / WOOD は 667 ファイルで頭打ちになり、`from_time` をどれだけ古くしても増えない |
| option=4（セットアップ） | JVOpen が返らない（600秒・900秒とも）。`BlockingCallGuard` のモーダル自動応答を効かせても**ダイアログは検出されず**、サーバー側で本当にブロックしている |

`keiba.slope_training` が 2025-05-27 始まりなのは取得漏れではなく保持限界そのもの。
`run_chokyo` は完了ファイルを記録して再開できるが、ハングは JVOpen の中で1ファイルも
読む前なので再開可能性は救いにならない。UmaConn は SLOP を配信していない（地方側が probe 済み・rc=-1）。

→ **評価窓を伸ばす手段は「これから貯める」しかない。この日次ジョブを止めてはいけない。**
実際 2026-06-07 を最後に自動実行が止まっており、8/15〜8/16 開催週の調教が丸ごと
欠けていた（DB 最終日が 2026-08-11 のまま誰も気づかなかった）。

⚠️ **`training_index` は調教データを使っていない。** 中身は直近レース成績のトレンドで、
初出走馬では 78.5% が中立値 50 に張り付く（sd 3.15 ↔ キャリア3走以上 19.80）。
名前に騙されないこと。実データの調教はキャリア0-2走（出走の 27.7%）で唯一生きている入力。

### blod-um モード 仕様（pedigrees.sire NULL 補完）

**目的**: 2022年以前の馬の `pedigrees.sire` が NULL になっている問題を解消する。

**根本原因**: BLOD の SK レコードが旧形式 sire_code（`20xxx`/`40xxx`）を持ち、`breeding_horses` に存在しないため名前解決できない。UM レコード（競走馬マスタ）は祖先名をテキストで直接保持するため、breeding_code の解決不要。

**DataSpec の選択根拠**（重要）:
- `BLOD` DataSpec には UM レコードが**含まれない**（BT/HN/SK のみ）
- UM レコードは `DIFF` / `DIFN` / `TCOV` / `RCOV` にのみ存在（仕様書 p.20・`jvlink_parser.py` L931 コメントで確認済み）
- **`--mode blod-um` は `DataSpec="DIFN"` + `option=1`（差分モード）を使用**
  - `DIFF`/`DIFN` + `option=4`（セットアップ）はセットアップファイルに UM レコードが**含まれない**ため使用不可（DIFF=111秒・DIFN=82秒で JVOpen は完了するが UM=0件。2026-04-20 確認）
  - UM レコードは UMFW ファイルだけでなく BN/CH/KS/RA/SE 等あらゆるファイルに散在するため、ファイル名フィルタは不要

**JVOpen パラメータ**:
```
JVOpen("DIFN", from_time="20000101000000", option=1, ...)
```
- `from_time="20000101000000"` で全期間の差分を取得
- `option=1` = 通常差分モード。数分で JVOpen が完了する

**正常動作フロー**:
```
JVOpen("DIFN", "20000101000000", option=1)
  → 数分で完了
JVRead ループ（max_errors=1000）:
  → 各ファイルから rec_id="UM" のレコードのみ抽出
  → BRFW 等 rc=-402 エラーファイルは completed マークしてスキップ
  → completed.add(filename) でメモリ上の完了セットも更新（同セッション内の重複処理を防止）
POST /api/import/bloodlines（batch_size=200）
  → pedigree_importer.import_records()
  → INSERT INTO pedigrees ... ON CONFLICT DO UPDATE SET sire=COALESCE(pedigrees.sire, EXCLUDED.sire)
  → 既存の非NULLは保持、NULL のみ補完
```

**実装上の注意**（`jvlink_agent.py` `_run_blod_um`）:
- `batch_size=200`：nginx 1MB 制限対策（2000 だと HTTP 413）
- `max_errors=1000`：BRFW ファイルの rc=-402 多発で中断しないよう緩和
- `retry_pending` を起動時に呼ぶ（`link_common.retry_pending` もバッチ分割対応済み）
- `fetch_stored_data` の `skip_file_fn=lambda fn: fn in completed` でスキップ

**実行結果（2026-04-20 完了）**:
- UM レコード 11,043 件 DB 反映済み（7,917 ファイル処理完了）
- 完了済みファイルは `data/completed/BLOD_UM.txt` に記録

**進捗確認**:
```bash
prlctl exec "Windows 11" --current-user powershell -Command "Get-Content 'C:\kiseki\windows-agent\jvlink_agent.log' -Tail 20"
```

**完了確認（DB）**:
```sql
SELECT COUNT(*) FROM keiba.pedigrees WHERE sire IS NULL;
```

**再実行が必要な場合**:
```bash
prlctl exec "Windows 11" --current-user powershell -Command "
  Get-WmiObject Win32_Process | Where-Object { \$_.Name -eq 'python.exe' } | ForEach-Object { \$_.Terminate() }
  Start-Sleep 3
  Start-Process 'cmd.exe' -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode blod-um' -WindowStyle Hidden
"
```
- 全件再取得する場合は `data/completed/BLOD_UM.txt` を削除してから実行

### bldn-full モード 仕様（breeding_horses + pedigrees.sire 全歴史補完）

**目的**: 2023年以前の馬の `pedigrees.sire` が NULL になっている問題を解消する。

**根本原因**:
- 旧形式 BLOD の SK sire_code（`20xxx`/`40xxx`）は breeding_horses に存在しない
- BLDN の新形式 SK sire_code（`1110000xxx` 等）も、HN（繁殖馬マスタ）が breeding_horses に未登録だと解決できない
- 通常の `--mode bldn`（from_time="20230801000000"）は差分ファイルのみ取得し、累積マスタ HN を含まない

**解決策**: `from_time="20000101000000"`（BLDNサービス開始前）で JVOpen すると、JV-Link はサーバー保持期間外とみなし、**累積マスタファイル（571ファイル）のみ**を返す。これには1986年以降の全 HN + SK が含まれる。

**JVOpen 動作の重要な挙動**:
- `from_time` が BLDN 開始（2023-08-08）**以前** → 累積マスタ 571 ファイルのみ（DL=0、6〜8分）
- `from_time` が BLDN 開始**以降** → 累積マスタ + 差分ファイル（30,919件、~6分）
- いずれも JVOpen は正常完了（rc=0）

**JVOpen パラメータ**:
```
JVOpen("BLDN", from_time="20000101000000", option=4, ...)
```

**正常動作フロー**:
```
Step1: JVOpen("BLDN", "20000101000000", option=4) → 571ファイルを全読み（キャッシュ確定）
Step2: JVOpen("BLDN", "20000101000000", option=4) → HN/SK のみ抽出してDB反映
  → 大きな累積 HNVM ファイル（113,530件など）→ breeding_horses 登録
  → 年度別 HNVM + SKVM ペア → breeding_horses → pedigrees.sire 解決
POST /api/import/bloodlines（batch_size=2000）
  → INSERT INTO breeding_horses ... ON CONFLICT DO UPDATE
  → INSERT INTO pedigrees ... ON CONFLICT DO UPDATE SET sire=COALESCE(pedigrees.sire, EXCLUDED.sire)
```

**完了済みファイルは** `data/completed/BLDN_FULL_completed.txt` に記録（`{dataspec}_completed.txt` 命名）。再実行時は削除不要（スキップ対象）。

**実行結果（2026-04-20 完了）**:
- 569 ファイル / 588,768 件 DB 反映済み
- breeding_horses: 170,315 → 314,246 件（+143,931件）
- pedigrees.sire NULL: 73,159 → 3,152 件（**95.7% 削減**）
- sire 解決済み: 77,924 / 81,076 件（96.1%）
- 残り 3,152 件は外国種牡馬・父不明など構造的に解消困難

**実行コマンド**:
```bash
prlctl exec "Windows 11" --current-user powershell -Command "
  Start-Process -FilePath 'cmd.exe' \`
    -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode bldn-full' \`
    -WindowStyle Hidden -PassThru
"
```

**完了確認（DB）**:
```sql
SELECT COUNT(*) FROM keiba.pedigrees WHERE sire IS NULL;
```

**再実行が必要な場合**:
全件再取得するには `data/completed/BLDN_FULL_completed.txt` を退避してから実行。

⚠️ **ファイル名は `BLDN_FULL_completed.txt`**（`BLDN_FULL.txt` ではない）。
2026-08-16 に旧記載のまま消そうとして「完了ファイルなし」と誤認しかけた。

### 夜間の自動実行（2026-08-16 追加）

`kiseki-JVLink-BldnFull`（**22:45 起動**）/ `kiseki-JVLink-BldnFull-Stop`（**08:00 停止**）。
realtime が動いていれば起動せず、多重起動もしない（`run_jvlink_bldn_full.vbs`）。
登録: `powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_bldn_full_task.ps1`
ログ: `C:\kiseki\windows-agent\bldn_full.log`

⚠️ **開催日の日中に BLDN を呼ばないこと。** 2026-08-16 実測で
`JVOpen(BLDN, option=4)` が **22分戻らず** JV-Link 枠を占有した
（CLAUDE.md は複数インスタンスの同時実行を可としているが、累積マスタ571ファイルは別物）。

### JV-Next 1403 (DM) 取得 — 2 つのオーケストレーター

DM (タイム型・対戦型指数) の取得には 2 つの方式がある:

| 方式 | スクリプト | UIアクセス | K=0 primary venue | 推奨度 |
|------|---------|---------|------------|------|
| **Protocol版** (新) | `backend/scripts/protocol_dm_orchestrator.py` | 不要 | ✅ 福島・新潟・中京・小倉 OK | 🟢 推奨 |
| **Denma版** (旧) | `backend/scripts/dm_fetch_orchestrator.py` | CEF クリック必要 | ❌ 取得不可 (cross-pairing 仕様) | 🟡 fallback |

#### Protocol 版 (新・基本)
JV-Next の `POST /Browsing/GateServlet` プロトコルを直接利用 (DATA=`05900403{date}{CC}{NN}` で 1403 取得)。

```bash
cd backend
# DB駆動: 過去30日内の未取得レースを自動検出して取得
.venv/bin/python scripts/protocol_dm_orchestrator.py --from-db

# K=0 primary venue を集中バックフィル (福島・新潟・中京・小倉)
.venv/bin/python scripts/protocol_dm_orchestrator.py --from-db --courses 03,04,07,10 --since 20230101

# 日付指定
.venv/bin/python scripts/protocol_dm_orchestrator.py --dates 20260419,20260412 --courses 03,06,09

# DRY RUN
.venv/bin/python scripts/protocol_dm_orchestrator.py --from-db --dry-run
```

**動作**:
1. DB から未取得日付・場リストを抽出
2. Windows-side `protocol_dm_pipeline.py` を SSH 経由で実行
3. パイプライン内で:
   - JV-Next 起動状態確認 (停止時は起動)
   - session KEY 確認 (probe 失敗時は pktmon で再抽出 ~30秒)
   - DATA=`05900403YYYYMMDDCCNN` で全レース fetch
   - 永続ストア (`C:\kiseki\data\dm_1403`) に zlib 圧縮保存
   - `jvnext_dm_importer.py --all` で DB 反映

**所要時間** (実測): 1日あたり ~65秒 (KEY refresh 30秒 + 12レース fetch ~3秒)

**詳細**: `memory/jvnext_protocol.md` に完全プロトコル仕様。

#### Denma 版 (旧・fallback)
従来の Denma ページから denm リンクをクリックする方式。secondary venue / K=1単独場のみ対応:

```bash
.venv/bin/python scripts/dm_fetch_orchestrator.py --from-db
```

K=0 primary venue (福島・新潟・中京・小倉) は cross-pairing 仕様で取得できない (Denma リンクは大JJ secondary 側のみ DL される)。  
→ K=0 primary には **必ず Protocol 版を使うこと**。

### JV-Link rc=-303 修復

rc=-303（ファイル存在確認エラー）。

> ⚠️ 旧記述の「JVNextCore が JRA-VAN サーバー確認に失敗」という説明は**誤り**
> （JVNextCore は JV-Next=DM取得アプリの実行体で JV-Link とは無関係。上記
> 「jvlink_agent トラブルシューティング」参照）。`fix_jvlink_303.py` も
> JVNextCore の kill を含むため、この用途では効果が無い。

**まず `JVLinkAgent` サービスが動いているかを見る。** 2026-08-12 の実測では、
サービスが停止した直後の JVOpen が -303 を返し、サービスを起こしたら復旧した。

```bash
# Step 1: サービスの状態を見る（停止していれば起こす）
ssh windows-vm "powershell -NoProfile -Command \"(Get-Service JVLinkAgent).Status\""
ssh windows-vm 'schtasks /run /tn kiseki-Start-JVLinkAgent'   # 昇格が要るので専用タスク経由

# Step 2: それでも -303 が続く場合のみ VM 再起動
# ※ prlctl restart は実際に再起動しない。必ず shutdown /r /t 0 を使うこと
ssh windows-vm "shutdown /r /t 0"
until ssh -o ConnectTimeout=5 windows-vm "powershell -NoProfile -Command \"Write-Output ready\"" 2>/dev/null | grep -q ready; do sleep 5; done
```

⚠️ **再起動後に `--mode setup` を再実行しないこと**（旧記述の誤り）。setup は初回専用で
JVOpen が数時間ブロックする。欠損修復は `--mode fix-race --from-date YYYYMMDD`
（「JVOpen option の選択指針」参照）。

## 指数バックフィル運用ルール

### 対象期間の方針
- **バックフィル対象は「実施日から3年前」を起点とする**
  - 例: 2026-04-13 実施 → `--start 20230413`
  - 3年分あれば ROI シミュレーション・重み最適化・馬のキャリア追跡に十分
  - 月日が進んだ場合も同様に「実施日 - 3年」で計算すること
- 3年以前の古いデータは優先度低（必要時のみ別プロセスで追加実行）
- **理由**: 全期間（2019〜）処理には約12日かかり、直近データが遅れるため
  - 4並列で約5〜6時間で完了（リソース実測: CPU ~60%・RAM ~700MB/16GB）

### 実行コマンド（日付は実施時点で計算）
```bash
cd backend

# 開始日を「今日 - 3年」で動的に計算
START=$(python3 -c "
from datetime import date
d = date.today().replace(year=date.today().year - 3)
print(d.strftime('%Y%m%d'))
")
TODAY=$(python3 -c "from datetime import date; print(date.today().strftime('%Y%m%d'))")

# 3年分を4等分
Q1=$(python3 -c "
from datetime import date
s=date.today().replace(year=date.today().year-3); t=date.today(); span=t-s
print((s+span//4).strftime('%Y%m%d'))
")
Q2=$(python3 -c "
from datetime import date
s=date.today().replace(year=date.today().year-3); t=date.today(); span=t-s
print((s+span//2).strftime('%Y%m%d'))
")
Q3=$(python3 -c "
from datetime import date
s=date.today().replace(year=date.today().year-3); t=date.today(); span=t-s
print((s+span*3//4).strftime('%Y%m%d'))
")
echo "対象: $START 〜 $TODAY  (Q1=$Q1, Q2=$Q2, Q3=$Q3)"

# 4分割並列バックフィル（約5〜6時間で完了）
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $START --end $Q1 --skip-existing > /tmp/v15_p1.log 2>&1 &
echo "P1 PID: $! (${START}〜${Q1})"
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $Q1 --end $Q2 --skip-existing > /tmp/v15_p2.log 2>&1 &
echo "P2 PID: $! (${Q1}〜${Q2})"
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $Q2 --end $Q3 --skip-existing > /tmp/v15_p3.log 2>&1 &
echo "P3 PID: $! (${Q2}〜${Q3})"
nohup .venv/bin/python scripts/calculate_indices_range.py \
  --start $Q3 --end $TODAY --skip-existing > /tmp/v15_p4.log 2>&1 &
echo "P4 PID: $! (${Q3}〜${TODAY})"
```

### 進捗確認
```bash
ps aux | grep calculate_indices | grep -v grep | awk '{print "PID:"$2, "CPU:"$3"%", "RSS:"$6/1024"MB"}'
for i in 1 2 3 4; do echo "=P${i}="; grep -E "\[.*\].*頭 \(累計" /tmp/v15_p${i}.log | tail -2; done
```

### パフォーマンス改善（2026-04-13 適用済み）
- `_bulk_upsert_for_race()`: 馬ごと N 往復 → レース1回の SELECT + bulk add_all
- `asyncio.gather` レース並列（セマフォ4）+ `SireStatsCache` クラス変数共有
- 実測効果: 旧比 **約5倍高速**（シングル51h → 4プロセス並列で約5〜6h）
- スループット: 約12,000件/時間/プロセス
- `_upsert()` はリアルタイム単一馬更新用として引き続き保持

## 中央競馬 再整理（2026-08）— `docs/jra_rebuild_2026_08.md` が作業台帳

地方（`docs/chihou_rebuild_2026_08.md`）と同じ手順を中央へ適用した記録。
以下は**毎日の運用に効く要点だけ**。経緯と数値は台帳を見ること。

### 🔴 JV-Next DM がモデルの生命線（gain の 71.8%）

順位回帰ヘッドの gain は `jvan_battle_dm` 55.2% + `jvan_time_dm` 16.7% で
**2列が 7 割超**を占める。欠けると honest test で

| | 指数1位 勝率 | 複勝率 | 指数1位が変わるレース |
|---|---|---|---|
| DM あり | 28.10% | 60.81% | — |
| **DM 欠損** | **22.81%** | 52.88% | **半分（一致率 50.2%）** |

**本番の指数にもレース内ばらつきの潰れとして現れる**（平常 幅28〜30 → 欠損日 14前後）。
`confidence` の分散スコア経由で **tier 判定まで壊れる**。

- 死活監視: `protocol_dm_orchestrator.report_unrecovered()` が**終了済みの開催日**の
  欠損で終了コード 1 を返す → `dm_auto_fetch.sh` が `ERROR: rc=1` を残し
  `launchctl list com.kiseki.dm-auto-fetch` の `LastExitStatus` に出る
- ⚠️ **`OVERALL: saved=N` を見て「動いている」と判断しないこと。**
  `saved` はファイル取得数で DB 反映数ではない。見るべきは `race_entries.jvan_time_dm` の充足率
- 回収: `jvnext_dm_importer.py --recheck-dates YYYYMMDD,...`（進捗を無視して再POST）
- 過去に踏んだ3バグ（`updated=0` を "ok" と記録 / `saved>0` のときしか importer を起動しない /
  小倉のヘッダ行を DM 行と誤認）は修正済み・テストで固定

### TRAIN/VAL/TEST プロトコル（`backend/src/jra_protocol.py`）

**中央は四半期ローリング**（地方は月次）。月 約288レースでは改善の実効サイズ
0.4〜0.5pt を標準誤差 2.6pt で判定できないため。

- `TRAIN_END=20250630` 固定 / `TEST_START` = 当四半期の初日 / `VAL_END` = その前日
- **本番モデルの refit は `TEST_START` の前日まで**（全期間 refit をやめた）。
  やめないと DB の過去分が全て in-sample になり一度きり評価が成立しない
- TEST を採否判断に使ったら `record_test_usage()` を呼ぶ → `scripts/JRA_TEST_USAGE_LEDGER.md`
- 四半期バッチ: `scripts/jra_quarterly_rollover.py`（evaluate → retrain → backfill）+
  LaunchAgent `com.kiseki.jra-quarterly-rollover`（1/4/7/10 月の1日 03:20）。
  **evaluate → retrain の順序に意味がある**（先に再学習すると評価が in-sample になる）
- ⚠️ **四半期をまたいで数字を比べない。** 中央は開催地が四半期ごとに総入れ替えになる。
  実例: 同一設定でも test 窓が 2026-01〜08 なら Spearman 0.5068、2026-07以降だけなら 0.4612。
  **モデルの差ではなく夏開催が難しいだけ**だった

### 学習ソースの版は固定しない

サブ指数は `composite.SUBINDEX_SOURCE_SQL`（`version >= SUBINDEX_MIN_VERSION` の最大版）で引く。
🔴 **特定の版に固定すると本番が版を上げた瞬間に学習データが静かに凍結する**
（実際 `version = 26` 固定で 2026-08-02 に止まっていた）。
現行版に追従させるのも誤り（版を上げた直後はバックフィル前で 0 件になる）。

### 推奨（hit_tier）の前向き記録（2026-08-15 稼働）

`keiba.hit_tier_races` / `keiba.hit_tier_picks`。発走 **10分前**に全出走馬の指数・
オッズ・tier 判定を保存し、翌日確定を書き戻す（`src/services/jra_hit_tier_log.py`）。

🔴 **後付けでは作れない。** 推奨は都度算出で DB に残らず、指数は上書きされ、
さらに **tier の第一分岐 `market_agree` は発走直前まで動く**
（発走10分前の1番人気が確定と一致するのは **80.7%**・2分前でも 86.5%）。
**確定オッズから tier を作り直してもユーザーが見た tier とは約2割ずれる。**

- cron: `scripts/jra_pick_snapshot_trigger.sh`（毎分）/ `jra_pick_settle_trigger.sh`（`45 23`）
- 集計: `backend/scripts/jra_pick_log_report.py --start --end`
- 発走時刻を過ぎたレースは**撮らない**（撮ると look-ahead になり、欠けているより悪い）
- tier=C（見送り）も `skip_reason` 付きで記録する。棄権側が無いと運用点を評価できない

### タイムゾーンが列ごとに違う（実際に誤読した）

| 列 | TZ |
|---|---|
| `keiba.odds_history.fetched_at` | **UTC**（DB セッションは `Asia/Tokyo`） |
| `keiba.calculated_indices.calculated_at` | **UTC と JST が混在**（更新は `datetime.now()`＝コンテナUTC / 挿入は列default＝DB JST） |

→ **`calculated_at` で処理の前後関係を判断しない。**
→ 最新オッズは `now()` と比べず、**最大 `fetched_at` からの相対**で絞る。

### DB の刈り込み方針（2026-08-15 実施）

- `calculated_indices`: **v22 / v24 / v26 / v27 だけ残す**（他は削除済み）。
  v22 は `backtest_dm` 系、v24 は `inference_v26` 系が明示指定しているので消してはいけない。
  `scripts/prune_calculated_indices.py`（既定 dry-run）
- `odds_history`: **発走後の行**と **exotic 券種の最終スナップショット以外**を削除。
  `win`/`place` の発走前時系列は**触らない**（前向き記録・odds 系分析が使う）。
  `scripts/prune_odds_history.py`（既定 dry-run・**日付ごとに回すこと**。全期間を1本の
  クエリでやると索引が効かず 6,000万行を全走査する）。
  **週次自動化済み**: LaunchAgent `com.kiseki.jra-odds-prune`（月曜 05:00・
  `scripts/prune_odds_history_weekly.sh`）。直近バックアップが無ければ実行しない。
  ⚠️ VPS の backend コンテナには psycopg2 が無いので **Mac から回している**。
  `~/GitHub/kiseki` が main 以外のブランチだとスクリプトが無く失敗する
- ⚠️ DELETE では実サイズは縮まない（領域が再利用可能になるだけ）。
  縮めるなら開催の無い日に `VACUUM FULL`（排他ロック）

## 開発マイルストーン
- MS1: 環境構築 + データ取込 + スピード指数CSV出力
- MS2: コース適性 + 枠順バイアス + 総合指数CSV
- MS3: 騎手・展開・血統・ローテーション指数
- MS4: パドック・調教 + 本格バックテスト
- MS5: リアルタイム対応 + 変更検知
- MS6: 競馬新聞Web (PWA)
- MS7: IPAT連携 + 収支管理
- MS8: 全自動投票 + 継続最適化

## DM 自動収集 LaunchAgent

中央レース情報が DB に入った後、対応する DM 指数を自動取得する。

- **LaunchAgent**: `~/Library/LaunchAgents/com.kiseki.dm-auto-fetch.plist`
- **スクリプト**: `scripts/dm_auto_fetch.sh`
- **オーケストレーター**: `backend/scripts/protocol_dm_orchestrator.py --from-db --courses 01..10`
- **スケジュール**: 12:00 / 14:00 / 18:00 / 22:30 (毎日) + 8:00, 11:00 (土日)
- **多重起動防止**: `/tmp/dm_auto_fetch.lock`

```bash
# 状態確認
launchctl list com.kiseki.dm-auto-fetch
# 手動実行
/Users/ysuzuki/GitHub/kiseki/scripts/dm_auto_fetch.sh
# ログ
tail -f /Users/ysuzuki/GitHub/kiseki/logs/dm_auto_fetch.log
```

## DM × 穴ぐさ × 既存指数 シグナルタグ

`backend/src/indices/dm_signals.py` は **2 タグのみ**を付与する。**1レースにつき「穴」は最大1頭**。

| タグ | 条件 | ねらい |
|------|------|------|
| 穴 (`SIGNAL_UPSET_CANDIDATE`) | 単勝≥10 ∧ 穴ぐさ/netkeiba/kichiuma/DM-battle のいずれか1つ以上が上位評価 → そのうち **badge_cnt 最大の1頭のみ**（同点は composite 降順） | 的中率の頑健な分離（複勝約20%）。**ROIではない** |
| 特穴 (`SIGNAL_ANAGUSA_ELITE`) | 穴ぐさ(A/B/C) ∧ composite順位≤3 ∧ 単勝≥10 | 単勝ROI狙い（FULL n=535 で 1.417・drop1 1.329） |

⚠️ **「7種シグナル」(🔥三冠一致・⭐高得点鉄板・🏆穴ぐさDM・⚡DM大穴・⚡DM高オッズ・
💎anagusa+DMtime・❌人気下振れ) は 2026-07-25 に全廃**（`jra_upset_badge_redesign`）。
軸の信頼度は `recommend_rank`（`confidence.py` の market_agree ベース tier）へ一本化され、
狭いAND条件・小標本(n=10〜184)でOOS不安定だった軸/警戒シグナルは撤去された。
**コース別 deny フィルタも同時に消滅している**。旧タグ名で grep しても実装は無い。
本番実測（2026-08-08 / 462頭）も `穴`28・`特穴`1 のみで、旧7種は1件も出ない＝正常。

**出走取消・発走除外馬はシグナル判定・順位計算の母集団から除外**される
（取消馬のDM欠損で「1頭でもNULL→レース全馬シグナルなし」が誤発動するのを防ぐ）。

API レスポンス: `HorseIndexOut.dm_signals: list[str]` (`/api/races/{id}/indices`)
recommendations 用: `recommender.py` で各馬に付与。
ベース指数 (composite_index) はオッズ非依存のまま。シグナルはオッズ・人気・anagusa を組み合わせたフロント手前レイヤで生成する。
フロントのバッジ定義は `frontend/src/components/DmSignalBadges.tsx`（共通モジュール・単一定義）。

## JRA 推奨エンジン（`/api/recommendations` = 的中重視 hit_tier 方式）

2026-06-05 から JRA 推奨は **hit_tier 方式**（1レース1推奨 = 指数1位馬 + 的中率tier）。
OOS検証（`scripts/jra_verify_signals.py`）で「価値(ROI>1)」を謳うバッジ（旧sweet_spot集約・
super_buy・DM穴・高得点鉄板）は全て OOS 脆弱と判明したため、推奨本体は的中重視に再定義し、
価値系は `value_candidates`（妙味候補・収支保証なし）の注記へ降格した。

**tier（=recommend_rank）**。判定の単一真実源は
`backend/src/indices/confidence.py::calculate_recommend_rank`。

⚠️ **2026-07-25 に再設計済み**（`jra_axis_market_agree_redesign`）。3年+完全OOSの
セグメント異質性分析で、confidence_score 単独より
**「指数1位馬が単勝1番人気とも一致するか」(`market_agree`) の方が的中率の分離を支配する**
と判明したため、**market_agree が第一分岐・confidence_score は第二分岐**になった
（confidence_score≥80 でも市場が支持していなければ最下位の市場一致グループより弱い）。

| tier | 条件 | 1位馬勝率 | bet |
|---|---|---|---|
| S 最強軸 | **単勝<1.5**（market_agree 不問）**または** market_agree ∧ confidence ≥ 80 | 45〜51%（断然人気時 70%+） | 単勝 |
| A 信頼軸 | market_agree ∧ confidence ≥ 65 | 33〜40% | 単勝 |
| B 準軸 | market_agree ∧ confidence < 65 | 27〜35% | 複勝 |
| C+ 準見送り | 市場乖離 ∧ entropy_norm < 閾値（市場内では本命寄り） | 複勝約55% | 複勝 |
| C 混戦 | 市場乖離（真の大混戦） | 15〜26% | 推奨しない（見送り） |

**「S = 単勝<1.5」だけではない。** market_agree が立っていれば単勝5倍台でも S になりうる
（2026-08-08 札幌4R で実際に発生: 指数1位=単勝1番人気 5.1倍）。
`market_agree=None`（全馬オッズ未取得）のときのみ confidence_score だけの旧ロジックへ落ちる。

**実装**:
- `backend/src/services/recommender.py::build_hit_tier_recommendations()` 推奨本体
- `backend/src/services/recommender.py::_value_badges()` 妙味候補バッジ（DM signals / 穴ぐさ非1位 / 外部指数穴馬）
- `backend/src/api/recommendations.py::get_recommendations` 都度算出（DB保存なし・60秒プロセス内キャッシュ）

**個別馬の sweet_spot 表示**（推奨エンジンとは別・`/indices` 専用）:
- `backend/src/indices/buy_signal.py::is_sweet_spot()` — 単勝≥10 ∧ EV∈[1.2,5.0] ∧ バッジ ∧ k≤2
- `backend/src/api/races.py` `HorseIndexOut.is_sweet_spot` に付与、`IndicesTable.tsx` で該当馬名を**赤字**表示
- 3年バックテスト 単ROI 1.182 だが OOS 検証では脆弱（memory `jra_signal_verification.md`）。
  表示バッジとしてのみ維持し、推奨エンジンには使わない

**重要な観察**（is_sweet_spot の EV ゲート設計根拠）:
- EV ≥ 4 で実勝率がモデル予測の 4.8〜6.5倍下振れ → 上限 5.0 必須
- k=3 で単ROI 0.935 → k≤2 制約で混戦レース除外

**地方競馬は対象外**（下記の別系統）。

### 総合指数 v27（順位回帰 + 着外率合成・2026-08-02）

`COMPOSITE_VERSION = 27`。v26 の「LGB LambdaRank 0.3 + v24線形和 0.7」を廃止し、
**レース内正規化着順の回帰（reg_rank）** を土台に **着外率ヘッド** で下位を押し下げる方式へ転換。

```
z_blend         = z( z(-reg_rank) - 0.5 * z(out_probability) )
composite_index = 50 + z_blend * 55.3 * sd_race(reg_rank)   → clip(0, 100)
```

- モデル: `models/jra_reg_rank_lgb.txt`（学習 `scripts/train_jra_reg_rank.py`）+ `models/jra_out_rate_lgb.txt`
- 全期間バックフィル: `scripts/inference_v27.py`（v26 行のサブ指数を流用・冪等）
- honest test 2026-01〜08 (2,046R) の v26 比:
  1位馬 勝率 27.08→28.40% / 複勝率 59.38→61.00% / NDCG@3 0.4975→0.5071 /
  レース内 Spearman 0.4783→0.5094 / 3着内馬を下位30%に沈める率 10.43→9.29%
- **min-max で 15〜85 に固定してはいけない**。composite のレース内ばらつきは
  `confidence.py::calculate_race_confidence` の分散・指数差スコアの入力であり、
  固定すると tier S が 19.0%→30.2% に膨張する。上式のとおり幅は reg_rank の実ばらつきに比例させる
- v27 は 1位-2位差が v26 比 約0.75倍に縮むため、JRA 側は `JRA_GAP_FULL_SCORE = 6.0` を
  `calculate_race_confidence(gap_full_score=...)` に渡してスケール差を吸収する
  （地方は `DEFAULT_GAP_FULL_SCORE = 10.0` のまま。実測で tier 分布が v26 と一致）
- **ROI は改善しない**（控除率の壁）。価値は表示順・足切り・推奨tierの精度に限定される

⚠️ **バックフィル済みの過去分は in-sample**。本番モデルは全期間 refit のため、
DB の composite_index / out_probability を使って過去の ROI・的中率を評価してはいけない。
honest 評価は walk-forward スクリプト（`scripts/jra_rank_quality_review.py` 等）で行う。

### 指数（特徴量）の死活監視

`scripts/check_feature_health.py` が月次ばらつき・欠損率から DEAD / SHIFT / SPARSE を検出する。
既知の問題:
- `paddock_index`: 上流 netkeiba スクレイプが 2026-05 に停止。
  ⚠️ 旧記述の「v26 学習期間中も全月 sd=0」は誤り。**2025-07〜2026-04 は生きている**（sd 5.6〜11.8）。
  そのため本番モデル（全期間 refit）は分岐を持つが配信では必ず定数 50 側へ落ちる。
  ただし **除去しても改善しないことを確認済み**（VAL 3,450R の paired bootstrap で有意差なし・
  `scripts/jra_feature_drop_ab.py` / `docs/jra_rebuild_2026_08.md` 15.1）
- `going_pedigree_index`: **レース当日に算出すると必ず全馬 50**。算出時点で `races.condition` が
  未確定（重/不でない）ため早期 return する。後日バックフィルしたレースだけ値が入る
- `rebound_index`: 2026-04 以降ばらつきが単調減少（sd 2.79→0.57）。
  `going_pedigree_index` ともども、**除去しても改善しない**（15.1 と同じ検定）。
  🔴 **「配信時に定数だから外すべき」は成り立たない。** LightGBM は定数入力を単に無視する

### 着外率による足切り（Web グレーアウト・2026-08-02）

Web の「足切り候補」グレーアウトは **総合指数のトップ差ルールを廃止し、着外率（6着以下確率）** に置き換えた。

- モデル: `backend/models/jra_out_rate_lgb.txt`（LightGBM binary・特徴量は v26 と同一34列・**オッズ/人気は不使用**）
- 学習: `backend/scripts/train_jra_out_rate.py` / バックフィル: `backend/scripts/backfill_jra_out_probability.py`
- 保存先: `keiba.calculated_indices.out_probability`（本番算出は `composite.py` が毎回同時に書き込む）
- 閾値: `composite.py::OUT_PROB_CUTOFF = 0.80` → API が `HorseIndexOut.is_cut_off` として返す（**判定の単一真実源**）
- honest 検証（test 2026-01〜08 / 2,046R）: 除外30% ・除外馬の実着外率 88.6% ・**1着取りこぼし 4.8%**
  （旧・指数差ルールは 除外55% で 1着を 16.8% 取りこぼしていた）。2025年独立追試でも同一挙動＝較正が安定
- **着外率は ROI を作らない**（全帯 0.54〜0.84）。足切り・見送り判定にのみ使うこと。
  詳細: memory `jra_out_rate_3head_verification_2026_08_02.md`
- `inference_v26.py` は out_probability を更新しないため、実行後は同期間で backfill を流すこと

詳細: memory `recommendations_feature.md` / `jra_signal_verification.md`

## 地方競馬 総合指数 v14（市場乖離特徴の削除・2026-08-14）

`CHIHOU_COMPOSITE_VERSION = 14`。**v13 から市場乖離5特徴**
（`odds_rank_n` / `speed_mkt_gap` / `kc_mkt_gap` / `is_heavy_fav` / `is_dark_horse`）
**を削除**し 39特徴にした。モデル: `chihou_prod_lgb.v14_39feat.txt`。

🔴 **理由**: 本番は `calculate_and_save(race_id, odds_map=None)` で算出され、
`_fetch_win_odds` は `race_results.win_odds`（レース確定後にしか入らない）を読む。
つまり**発走前は市場5特徴が常に中立値**で、「市場込みで学習して市場なしで配信」
という状態だった。walk-forward 実測（全9四半期・指数1位馬の勝率）:

| | 勝率 |
|---|---|
| 市場込み学習・市場なし配信（v13 = 旧本番） | 23.7〜32.6% |
| 市場なし学習・市場なし配信（v14） | **34.0〜40.1%** |

**全四半期で +6.6〜+13.6pt**（平均約9pt）。複勝率でも 8〜10pt 差。

⚠️ **市場特徴を戻すなら「配信時に必ずオッズを渡す」経路とセットにすること。**
ただし**穴馬用途では市場を見せてはいけない**（見せると市場が嫌う馬を上位に置かず
「人気薄×指数上位」の条件が空になる）。詳細: `docs/chihou_rebuild_2026_08.md` 10・13章。

⚠️ **版を上げたら「デプロイ → バックフィル → 当日/翌日の calculate」の3段を必ず行う。**
`inference_chihou_v14.py` は `head_count >= 6` で抽出するが `head_count` はレース後にしか
入らないため、**未実施のレースはバックフィルされない**。API は現行 version の行しか読まないので、
放置すると当日の指数が画面に出ない（2026-08-14 に実際に発生）。
`POST /api/import/chihou/calculate?date=YYYYMMDD` を当日・翌日ぶん叩いて埋める。

⚠️ **v14 で tier 分布が動いた**（2026-07 実測）。S+A が 70.4% → 35.8% に減り
`chihou_buy_signal` の buy/caution が半減、pass が増える。ただし tier の質は向上
（A の1位馬勝率 43.1% → 50.8%）。較正定数を比例縮小すると割合は戻るが A の質が落ちるため
**GAP=12.0 / DISP=16.0 のまま据え置いた**。買い目閾値の見直しは未対応。

⚠️ **サブ指数の取得元に `CHIHOU_COMPOSITE_VERSION` を使ってはいけない。**
版を上げた直後はその version の行が DB に無く学習が0件で落ちる。
`CHIHOU_SUBINDEX_MIN_VERSION = 9` を下限として使うこと（v9 以降サブ指数は不変）。

---

## 地方競馬 総合指数 v13（min-max 廃止 + 全期間バックフィル・2026-08-02）

`CHIHOU_COMPOSITE_VERSION = 13`。**モデルと44特徴は v12 と完全に同一**
（`chihou_prod_lgb.v12_44feat.txt` をそのまま使用）。変えたのは 2 点だけ。

**① composite のスケール（min-max 15-85 を廃止）**
```
composite = clip(50 + CHIHOU_INDEX_SCALE(=40) * (p_top3 − レース内平均), 0, 100)
```
- 旧方式はレース内 min-max → 15〜85 固定のため、**全レースで幅がぴったり 70.00（sd=0.000）**
  になり、`confidence.calculate_race_confidence` の
  **分散スコア(25点)が 100% のレースで満点＝完全な定数**・指数差スコア(40点)も 63% が満点。
  100点中65点が情報を失い、**97% のレースが tier S** に張り付いていた（DB 実測 6,496R）
- **min-max は禁止**。JRA v27 も同じ理由で禁止している（memory `jra_rank_quality_redesign_2026_08_02`）
- tier 較正は表示スケールと分離し `confidence.CHIHOU_GAP_FULL_SCORE=12.0` /
  `CHIHOU_DISPERSION_FULL_SCORE=16.0` で吸収する。
  **片方（C か閾値）だけ変えると tier 分布が壊れる。必ず比例させること**
- honest 検証（2026-01〜06 / 6,418R）の tier 分布と1位馬勝率:
  旧 S 97%/A 3%/B 0%/C 0%（分離不能） → 新 **S 22%/A 45%/B 26%/C 6%（S 58.2% → C 33.2%・単調）**
- **min-max も中心化線形も単調変換なのでレース内の順位は一切変わらない**。変わるのは tier だけ

**② 全期間バックフィル（v10 → v13）**
DB の履歴は **version=10（30特徴・市場特徴なし）で止まっており**、本番が serve している
v12（44特徴）が過去に一度も適用されていなかった。honest 比較（train ≤2025-06 / test 2026-01〜06）:

| 指標 | v10相当(DB実測) | 44特徴モデル | 差 |
|---|---|---|---|
| 1位馬 勝率 | 0.3967 | 0.4624 | **+6.6pt** |
| 1位馬 複勝率 | 0.7172 | 0.7661 | +4.9pt |
| レース内 Spearman | 0.5280 | 0.5822 | +0.054 |

paired bootstrap で全て有意。**差の実体は市場（オッズ）特徴**で、market 5本を外すと
44特徴モデルも v10 と同水準（0.3995）まで落ちる。

### 学習・検証・テスト期間（`src/chihou_protocol.py` が正）

| 区分 | 期間 | 用途 |
|---|---|---|
| TRAIN | 〜 **2025-06-30** (`TRAIN_END`) | 学習のみ |
| VAL | **2025-07-01 〜 TEST_START の前日** | 探索・A/B を繰り返してよい（既に6回以上使われ焼けている） |
| TEST | **当月1日 〜** (`TEST_START`・月次ローリング) | 一度きり評価。使ったら `record_test_usage()` で台帳に記録 |

**TEST_START は「当月1日」で月次ローリング**（2026-08-03 に固定値 20260701 から変更）。
固定すると学習終端も固定されモデルが古くなるため、日付から導いて毎月自動で前進させる。
`CHIHOU_TEST_START=YYYYMMDD` で固定可（過去分析の再現用）。VAL_END は TEST_START の前日に追随。

**月次サイクル**（`scripts/chihou_monthly_rollover.py` / LaunchAgent
`com.kiseki.chihou-monthly-rollover` が**毎月1日 03:18** に実行）:

| フェーズ | 内容 | 自動 |
|---|---|---|
| `evaluate` | **先月**を一度きり評価し台帳へ記録。DB の指数値は前回サイクルの backfill（先々月までで学習したモデル）が書いたものなので**この時点では honest** | ✅ |
| `retrain` | 先月までを含めて再学習。旧モデルは `data/backup/model_YYYYMMDD/` へ退避 | ✅ |
| `backfill` | v13 を全期間再計算し DB を新モデルへ揃える | ❌ **人が実行** |

- **順序に意味がある**。先に再学習すると評価が in-sample になる
- ⚠️ **`backfill` はデプロイ後に実行する**。先に走らせると DB=新モデル / live=旧モデルの
  新旧混在になる（`feedback_full_period_migration`）
- コミット・デプロイ・backfill を自動化していないのは、モデル差し替えとデプロイが
  外向きの操作だから。レポート（`backend/docs/monthly_rollover/YYYYMM.md`）末尾に手順が出る
- **指数1位の勝率は季節性が強い**（7月は 2024 41.3% / 2025 43.7% / 2026 42.2% なのに
  1月は 45.6〜49.7%）。前月を冬場の窓と比べて「劣化した」と誤読しないこと。
  レポートは同月比較の表を自動で付ける

**03:18 という時刻の根拠**: LaunchAgent は Mac がスリープ中だと発火しない
（復帰時に遅延実行はされるが電源オフだと走らない）。日次起床の直後に置いている。
DBバックアップ（`com.kiseki.db-backup`）は **03:30〜04:15（実測45分）** VPS へ重い
`pg_dump` を投げるので、その**前**に終わらせて競合を避ける（本ジョブは約2分）。

自動起床は **2026-08-03 に設定済み**（`sudo pmset repeat wakeorpoweron MTWRFSU 03:15:00`）。
詳細と注意点は「DB 自動バックアップ運用 › Mac スリープ時」を参照。
plist は `scripts/launchagents/` に複製あり。

- **本番モデルの学習終端は `TRAIN_DATA_END` = `TEST_START` の前日**（`train_chihou_market_lgb.py`)。
  2026-08-03 以前は `"20260706"` がハードコードされており **TEST 期間の 257レースを学習に含んでいた**ため是正した
- `TRAIN_DATA_START = "20230101"` は**宣言値で実効は 2024-01-01**。学習クエリが
  `calculated_indices version>=9` を要求する一方サブ指数が 2024-01 からしか無く、2023年は0行
- 再学習は `train_chihou_market_lgb.py --refit-only`（A/B 判定を経由せず2ヘッドを学習・保存）
- 検証スクリプト群（`chihou_rank_quality_review.py` 等）の `test` は
  **2026-01〜06 = プロトコル上は VAL の一部**。TEST とは別物なので混同しないこと

- バックフィル: `scripts/inference_chihou_v14.py --start 20240101 --end YYYYMMDD`
  - `race_results` は **LEFT JOIN**（出走取消・失格も母集団に含む）。本番 `rank_by_hn` と
    母集団を揃えるため。学習用 `BASE_QUERY` は完走馬のみなので流用してはいけない
  - VPS 負荷対策で `--batch-size` / `--sleep` 分割コミット
- ⚠️ **バックフィルした過去分は in-sample**（全期間1回学習モデルの遡及適用＝model-vintage
  look-ahead）。**DB の composite_index / win_probability で過去の ROI・的中率を評価しない**こと。
  honest 評価は `chihou_rebuild_walkforward.py` 等の walk-forward で行う

### 検証・監視スクリプト（2026-08-02 新設）

| スクリプト | 用途 |
|---|---|
| `scripts/check_chihou_feature_health.py` | 44特徴の DEAD/SHIFT/DEGENERATE/DECLINE 検出。学習と同じ `prep` を通して「モデルが実際に見ている値」を検査する |
| `scripts/chihou_rank_quality_review.py` | HEAD/TAIL/ALL 分離のランキング品質比較 + レース単位 paired bootstrap |
| `scripts/chihou_feature_ab.py` | 特徴量セットの A/B（死んだ特徴の除去・新規列の追加） |
| `scripts/chihou_composite_scale_review.py` | composite スケール係数と tier 較正閾値の掃引 |
| `scripts/inference_chihou_v14.py` | v14 バッチ推論・全期間バックフィル |

### 検証済みの否定結果（再検証不要）

- **外部指数 netkeiba（`nk_idx_z` / `nk_rank_n`）は 2026-06 以降フォールバック占有率100%＝死んでいる**
  が、**実害なし**。serve 時欠損を再現した A/B で top1勝率 +0.0017（むしろ僅かに改善）、
  2特徴を落としても −0.0005（有意差なし）。kichiuma は 95%→76% に劣化中で要監視
- **未使用の高充足列6本（賞金クラス・ナイター・減量騎手・重量種別）は効果なし**。
  top1勝率 +0.0017 [CI −0.0003, +0.0037] で有意差なし → 不採用
- **目的関数の変更は地方では割に合わない**。`reg_rank`（JRA v27 の採用形）は
  Spearman を +0.0068 改善する一方 **top1勝率を −0.0045 悪化させる**（どちらも有意）。
  JRA では両方改善したが地方はトレードオフ。現行 `is_top3` 二値が HEAD の最適点
- **JRA戦歴のクロス活用は市場に織り込み済みで不採用**（`scripts/chihou_jra_history_ab.py`）。
  地方出走馬の 57.7% に JRA 出走歴があり point-in-time 8特徴を作れるが、
  本番構成では top1勝率 +0.0003（ns）。**市場特徴を外した土台でだけ Spearman +0.0028 が有意**
  ＝情報は本物だがオッズが完全に織り込んでいる
- **着外率ヘッド（out_probability）の新設も不採用**。同じ除外率で較正済みの指数差ルールと同等
  （31%帯: ルール 93.6/2.9/6.9 vs モデル 93.9/3.0/6.7）。alembic 移行に見合わない。
  **JRA で「指数差→着外率モデル」が効いたのはモデルの力でなく旧ルールの較正崩れが原因**の可能性が高い

## 地方競馬 推奨カテゴリ（`/api/chihou/recommendations/sweet-spot`）

JRA とは別系統で、**5 カテゴリ** を都度算出して返す（オッズ取得後に毎リクエスト計算・
30秒プロセス内キャッシュ）。

**Phase2（2026-06-05, commit `909124ac`）で sweet_spot / place_bet は EVゲートから
ランキング規則へ全面移行済み**。較正済 win_probability では高オッズ馬の honest EV が
概ね <1 となり旧 EV ゲートは機能しない。Phase1 クリーンOOSで黒字だったのは
「指数1位 × 単勝10-30倍 × 割安場」のランキング規則だったため、これを定義とする。

| カテゴリ | bet | 条件（Phase2 現行） |
|---|---|---|
| `sweet_spot` 高オッズ穴 | 単勝 | **指数1位 ∧ 単勝10〜30倍 ∧ 割安5場（浦和/金沢/高知/笠松/盛岡）**（旧 5seed 単ROI 1.17 ※要注意・下記参照） |
| `place_bet` 複穴 | 複勝 | 1番人気<2.0 ∧ 単勝≥10 ∧ **指数3位以内** ∧ k≤2 ∧ **頭数≥8** |
| `upset_place` 穴軸複勝 | 複勝 | 人気薄リランカー軸（単勝[10,15)×非オッズスコア×外部バッジ） |
| `low_odds_trusted` 信頼本命 | 単勝 | 単勝<1.5（hit 約70% / 単ROI 0.8台） |
| `low_odds_untrusted` 不信頼本命 | 単勝 | 1.5≤単勝<2.0（hit 約48% / 単ROI 0.8台） |

- 判定本体: `backend/src/indices/buy_signal.py::chihou_is_sweet_spot() / chihou_is_place_bet() / chihou_low_odds_trust_level()`
- `sweet_spot` と `place_bet` は同一馬が両方に入ることを許容（並列）。低オッズ系とは構造的に排他
- 実勢集計: `scripts/aggregate_chihou_recent.py` は上記の本番判定関数を import して同一条件で集計する

**⚠️ 生存者バイアス監査・修正（2026-07-23）**: 旧バックテスト系スクリプトは
`race_results` を INNER JOIN し「完走・正常決着馬のみ」で idx_rank（指数順位）を
再計算していたため、本番の指数1位馬が出走取消/失格になると2位馬が繰り上がって
1位扱いになる生存者バイアスを含んでいた（本番 `chihou_recommender.rank_by_hn` は
出走予定馬全体で順位を確定するため、この乖離は起きない）。`aggregate_chihou_recent.py`
と `backtest_chihou_sweetspot.py`（新設）は出走予定馬全体（LEFT JOIN）で idx_rank
を計算してから確定結果のみに絞り込むよう修正済み。v10全期間(2024-01〜2026-07,
32,976レース)で honest 再計算した結果、**sweet_spot 単ROI 1.028→0.983
（黒字→ほぼ損益分岐）**、**place_bet 複ROI 1.056→1.046** に低下（idx_rankが変化した
レースは全体の11.7%）。

**⚠️⚠️ walk-forward honest再構築でさらに深刻な結果（2026-07-23、
`backend/scripts/chihou_rebuild_walkforward.py`）**: 本番モデル(v12)は
2023-01〜直近の全期間を1回だけ学習した単一モデルを、backfillで2024-01以降の
全historical raceにretroactivelyに適用している＝「モデルの学習パラメータ自体が
対象レースより未来のデータを反映している」model-vintage look-ahead が存在した
（keirinの「モデルが賢くなるたびに過去分析がリークする」問題と同型）。四半期ごとに
その時点までのデータだけで学習しなおした vintage別モデルで2024-10〜2026-07の
全8四半期(24,067レース)を honest再評価した結果、**sweet_spot該当レースが0件**
（Phase0時点のn=381→0）。**sweet_spotの「黒字」主張は生存者バイアス修正後もなお
model-vintage look-aheadにほぼ全面的に依存していたと判断される。** place_betは
複勝ROI 0.987（Phase0の1.046から低下、ほぼ損益分岐で残存）。
sweet_spotは事実上エッジなしとして扱うこと。

**Phase2 非効率性の系統的スイープ（2026-07-23、`backend/scripts/chihou_walkforward_sweep.py`）**:
walk-forward honest予測(24,069レース)を場×指数順位帯・オッズ帯・市場一致状況・
距離帯等で153セグメントに系統的に分解して調査した結果、ほぼ全セグメントがROI
0.6〜0.9台に収束（控除率にほぼ支配される）。ROI≥1.10の候補は5件のみで、いずれも
n=46〜123の小標本（多重比較の必然として説明可能）。唯一注目すべきは**高知の
「断然人気R×指数上位×単勝≥10」複勝母集団(n=105, 複勝ROI 1.291)**だが、
TEST_START(2026-07-01〜)以降の新規データで一度きり評価するまでは採用しないこと。
現行データ・特徴量セットでは近い将来にrobustな黒字戦略を見つけるのは構造的に
困難という前提でtier設計を進めるべき（keirinの「控除率の壁」と同型の結論）。

**⚠️ 複勝7頭以下ルールの母集団バグ・本番修正済み（2026-07-23、ユーザー指摘）**:
複勝は出走7頭以下だと2着までしか払い戻されない(JRA/NAR共通ルール)。
`chihou_is_place_bet()`にはこのゲートがなく、6-7頭立てで3着入着馬を誤って複勝
的中扱いしうる状態だった（`upset_place`も同型の穴）。`CHIHOU_PLACE_MIN_HEAD_COUNT=8`
未満(Noneも含む)は必ずFalseを返すよう本番修正済み（`chihou_recommender.py`/
`chihou_races_router.py`全呼び出し元更新、walk-forward/backtest系スクリプトも同様に
修正・再検証済み）。修正後の最終honest数値: **place_bet 複勝ROI = 0.972**
（n=1,066、walk-forward全8四半期）。
詳細: memory `chihou_survivor_bias_audit_2026_07_23.md`

**API レスポンス構造**:
```
ChihouSweetSpotResponse {
  items: ChihouRecommendationOut[]  # category フィールドで分類
  summaries: { [category]: { n_total, n_settled, n_hits, hit_rate, win_roi, bet_type } }
}
```
`bet_type="place"` の場合 `win_roi` は複勝ROI を返す（フィールド名は互換のため維持）。

### 注目馬の前向き記録（`chihou.place_pick_races` / `chihou.place_picks`・2026-08-14）

注目馬（`chihou_is_place_pick`）の運用点は探索で選ばれた値で、確認窓が無い
（HOLDOUT は開封済み）。**後付け集計もできない**——`calculated_indices` の現行 version 行は
当日 21:30 JST の再算出で上書きされ、そのとき市場特徴の入力が確定オッズに変わるため、
**日中ユーザーに提示された指数は DB に残らない**。そこで発走前に撮って保存する。

- 本体: `backend/src/services/chihou_place_pick_log.py`
- cron: `scripts/chihou_pick_snapshot_trigger.sh`（**毎分**）/ `chihou_pick_settle_trigger.sh`（`30 23 * * *`）
  - ⚠️ **VPS の cron は JST で動く**（`timedatectl` 実測）。UTC のつもりで書くと 9 時間ずれる
- 集計: `backend/scripts/chihou_pick_log_report.py --start --end`
- 🔴 **発走時刻を過ぎたレースは撮らない**（撮ると締切間際の資金移動が混ざり look-ahead になる）。
  撮り逃しは記録から欠けるが、欠けている方が安全。テストで固定してある
- ⚠️ **毎分で回すこと**。5分間隔だと発走6分前の窓を跨げないレースが出る
- 推奨が出なかったレースも `skip_reason` 付きで記録する（棄権側の答え合わせに要る）。
  推奨馬だけでなく**全出走馬**の指数を残す（別案の事後評価は上書き後には不可能）
- 判定は必ず本番関数（`chihou_is_place_pick` / `chihou_select_place_picks`）を呼ぶ。
  閾値は `rule_version` として毎行に埋まるので、変更しても世代が自動で分かれる
- 🔴 **オッズ SQL は `latest_odds_sql(["win", "place"])` で組み立てること**。
  `VALUES ('win', 'place')` は 2 行ではなく**2列の1行**になり、`AS bt(bet_type)` は
  先頭列にしか名前を付けないため**複勝が丸ごと落ちる**。2026-08-14 まで
  `/featured-place` の `place_odds` がずっと NULL だったのがこれ（エラーにならないので
  「複勝オッズ未取得の日」に見えて気付けない）

詳細: `docs/chihou_rebuild_2026_08.md` 16章

### 複勝オッズ永続化

`chihou.race_results.place_odds` は HR 払戻からだと 1〜3着のみで充足率28%だったため、`chihou.odds_history`（bet_type='place'）の発走前最終スナップショットで全馬補完する。
- 自動補完: `backend/src/api/chihou_import_router.py::_fill_loser_place_odds_from_history()` が HR 取込後に呼ばれる
- 過去補完: `backend/scripts/backfill_chihou_place_odds.py --start YYYYMMDD --end YYYYMMDD`
- `chihou.odds_history` は **2026-04-07 以降** のみ蓄積。それ以前は恒久的に補完不可

### オッズ鮮度シグナル（レース詳細に常時表示・2026-08-21）

🔴 **オッズが止まっても API は 200 と「それらしい倍率」を返し続ける。**
最後のスナップショットが DB に残るため、値だけを見て停止に気づくことはできない。
2026-08-20 に取得が **4時間51分**止まったとき、発走直前の画面に朝の倍率が出ていたが
異常を示すものは何も無く、公式オッズと見比べるまで誰も気づかなかった。

- 判定の正本: `backend/src/services/chihou_odds_freshness.py`（DB にも FastAPI にも
  依存しない純関数）。`GET /api/chihou/races/{id}/odds` が `freshness` として返す
- 表示: `frontend/src/components/OddsFreshnessBadge.tsx`（`ChihouRaceDetailClient` のヘッダ）

| status | 条件 | 表示 |
|---|---|---|
| `live` | 経過 ≤ 5分 | 緑「オッズ最新」 |
| `delayed` | 5〜15分 | 黄「更新遅延」 |
| `stale` | **15分以上・かつ未発走** | 赤「更新停止」 |
| `missing` | 取得実績なし | 灰「オッズ未取得」 |
| `closed` | **発走済み** | 灰「発走済み」 |

- ⚠️ **発走済みの停止を異常にしてはいけない**。終わったレースが全部赤くなり信号が死ぬ
- ⚠️ **サーバの status をそのまま描画してはいけない**。ポーリングが失敗している・
  圏外・API 停止のとき、最後に受け取った「最新です」が画面に残り続ける。
  バッジは**受信からの経過を足して判定し直す**ので、更新が届かなければ自分で赤へ進む
  （端末の時計が狂っていても影響しない。絶対時刻ではなく経過時間しか使わないため）
- ⚠️ **`fetched_at` は naive UTC・DB セッションは Asia/Tokyo**。
  SQL で `now() - fetched_at` すると9時間ずれる。`now() AT TIME ZONE 'UTC'` を使うこと
- 15分という赤の閾値は Windows 側 `run_realtime_watchdog.vbs` の `STALL_MINUTES` と同値。
  片方だけ変えると「画面は赤いのに watchdog は無反応」になるので必ず揃える

### 推奨パネル UI（`/chihou/races` の推奨タブ）

- カテゴリ別 **コンパクト table**（カードではなく一覧表形式）
- 上部に **競馬場別 当日サマリ table**（rows=場 × cols=5カテゴリ、各セル: hits/n + ROI）
- 着順バッジ: 1着=🥇金 / 2-3着=🥈🥉青 / 4着以下=灰
- 単勝推奨で 2-3 着の場合「△ 複圏」青系バッジ（複勝なら馬券になったケースが分かる）
- レース名から `/chihou/races/{id}` 詳細ページへリンク

詳細: memory `chihou_place_bet_category.md`

### 集計・分析スクリプト

| スクリプト | 用途 |
|---|---|
| `backend/scripts/aggregate_chihou_recent.py --days 30` | 直近30日の各カテゴリ実勢 hit_rate / ROI を DB 直接集計 |
| `backend/scripts/backfill_chihou_place_odds.py` | 過去 race_results の place_odds NULL を odds_history で補完 |
| `backend/scripts/backtest_chihou_sweetspot.py --version N [--show-bias]` | sweet_spot/place_bet の honest バックテスト（本番同一母集団・2026-07-23新設） |
| `backend/scripts/backtest_chihou_low_odds.py` | low_odds 信頼/不信頼分割の検証 |

## DB 自動バックアップ運用

VPS PostgreSQL `hrdb` (4.96GB) を Mac に毎日 03:30 JST 自動バックアップ。
詳細・リストア手順は `docs/backup-restore.md` 参照。

- 実行スクリプト: `scripts/backup_hrdb.sh`
- launchd: `~/Library/LaunchAgents/com.kiseki.db-backup.plist`
- 保存先: `~/kiseki-backups/{daily,weekly,monthly}/`
- 世代: 日次 7・週次 4・月次 12 (合計 ≒ 12〜15GB)
- ログ: `~/kiseki-backups/backup.log` / `logs/db_backup_launchd.log`
- 圧縮: `pg_dump -Fc -Z 9` で 4.96GB → 516MB (10.4%)

### 状態確認

```bash
launchctl list com.kiseki.db-backup           # LastExitStatus 確認
ls -lh ~/kiseki-backups/daily/                # 直近 dump
tail -30 ~/kiseki-backups/backup.log          # 実行ログ
```

### 手動実行

```bash
/Users/ysuzuki/GitHub/kiseki/scripts/backup_hrdb.sh
```

### リストア時の注意

VPS は PostgreSQL 16.13 / dump 形式 v1.15。Mac の `pg_restore` は **16 系必須** (`/opt/homebrew/opt/postgresql@16/bin/pg_restore`)。14 系では `unsupported version` エラー。

### Mac スリープ時（**2026-08-03 設定済み**）

`launchd` はスリープで逃した `StartCalendarInterval` を復帰時に実行する（cron と違い
スキップはしない）が、**電源オフだと走らない**。そのため日次の自動起床を設定してある:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 03:15:00   # 設定済み
pmset -g sched                                     # 確認
```

深夜の実行チェーン: **03:15 起床 → 03:18 地方の月次ローリング（毎月1日）→ 03:30 本バックアップ（毎日・約45分）**。

⚠️ `pmset repeat` は**繰り返しスケジュールを1つしか持てない**。別の時刻で上書きすると
バックアップと月次ローリングの**両方**が起床しなくなる。変更するときは両方を見ること。
