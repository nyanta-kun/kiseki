# WEB品質監査レポート

## 監査概要

| 項目 | 内容 |
|------|------|
| 対象 | kiseki 競馬予測指数システム（frontend/ + backend/） |
| 実施日 | 2026-03-31 |
| 監査方式 | 9専門エージェント並列静的解析 + 最新ベストプラクティスWebSearch |
| 技術スタック | React 19 / Next.js 16 / TypeScript / Tailwind CSS v4 / Auth.js v5 / Python 3.12 / FastAPI / SQLAlchemy 2.0 / PostgreSQL / Docker |

---

## 総合スコア

| カテゴリ | スコア | 評価 |
|---------|--------|------|
| シニアエンジニア（コード品質・設計） | **72** / 100 | 良好 |
| UX/UI | **68** / 100 | 良好 |
| パフォーマンス | **62** / 100 | 要改善 |
| セキュリティ | **58** / 100 | 要改善 |
| アクセシビリティ | **48** / 100 | 問題あり |
| QA（テスト・品質プロセス） | **42** / 100 | 問題あり |
| インフラ/DevOps | **42** / 100 | 問題あり |
| SEO/Web標準 | **42** / 100 | 問題あり |
| 法務・コンプライアンス | **38** / 100 | 問題あり |
| **総合（加重平均）** | **52** / 100 | **要改善** |

---

## カテゴリ別サマリー

### シニアエンジニア（72点）
全体的にコード品質は高く、型ヒント・docstring・DRY意識は良好。同期DBセッションと非同期エンドポイントの混在、`debug=True`のデフォルト値、複数ルーターファイルでの重複定義など改善点が存在する。

### UX/UI（68点）
競馬新聞風のモバイルファーストUIは基本的に良好。スケルトンローディング・エラー状態・空状態の表示も実装されているが、`error.tsx`/`not-found.tsx`未実装、PWAアイコン仕様不足、WebSocket切断UX欠如が課題。

### パフォーマンス（62点）
基本的な設計は適切だが、DB接続が同期エンジン（psycopg2）のみで非同期未対応、全APIレスポンスが`cache: 'no-store'`、画像最適化（next/image）未使用が主要課題。

### セキュリティ（58点）
Auth.js v5 + 合言葉二段階認証・SQLAlchemy ORM・CORS明示制限など基本対策は実施済み。WebSocketエンドポイントの無認証、FastAPI自動ドキュメント公開、APIキー空値時の認証スキップがリスク。

### アクセシビリティ（48点）
セマンティックHTMLの基本実装はあるが、Rechartsグラフのアクセシビリティ属性欠落、スキップナビゲーション未実装、タブUIのARIAパターン不適用など重大な課題が複数存在。

### QA（42点）
バックエンド指数計算ロジック（9モジュール/236テスト）は手厚くカバーされているが、フロントエンドのテストは**ゼロ**、APIエンドポイント統合テスト・CI/CDパイプラインも未整備。

### インフラ/DevOps（42点）
基本的なDocker構成・ヘルスチェック・環境変数管理は整備済み。CI/CDパイプライン完全不在、DBバックアップ戦略なし、本番Dockerfileにnon-rootユーザー未設定が最大の懸念事項。

### SEO/Web標準（42点）
認証ありの個人向けPWAとして基本構成は整っているが、robots.txt・sitemap・OGP・Service Workerが未実装。manifest.jsonのstart_urlがbasePath未考慮のため本番PWAが正常動作しない可能性がある。

### 法務・コンプライアンス（38点）
プライバシーポリシー・利用規約・Cookieポリシーが一切存在せず、netkeibaスクレイピングの利用規約リスク・Google OAuth規約遵守確認が未実施。個人利用範囲内でも法的整備が急務。

---

## 課題一覧（優先度順）

### 🔴 Critical（即時対応必須）

| ID | カテゴリ | タイトル | 場所 |
|----|---------|---------|------|
| SEC-001 | セキュリティ | .env.local にシークレット情報が平文記載（AUTH_PASSWORD="nishikawa"等） | frontend/.env.local |
| INFRA-001 | インフラ | CI/CDパイプラインが完全に存在しない | .github/workflows/（未存在） |
| INFRA-002 | インフラ | バックエンド・フロントエンドDockerfileにnon-rootユーザー未設定 | backend/Dockerfile, frontend/Dockerfile |
| INFRA-003 | インフラ | 本番環境でソースコードをvolumeマウント（イメージ再現性なし） | docker-compose.sekito.yml |
| QA-001 | QA | フロントエンドのテストが皆無（フレームワーク未導入） | frontend/package.json |
| QA-002 | QA | FastAPI APIエンドポイントの統合テストが存在しない | backend/tests/ |

### 🟠 High（1週間以内に対応）

| ID | カテゴリ | タイトル | 場所 |
|----|---------|---------|------|
| SE-001 | コード品質 | 同期DBセッションと非同期エンドポイントの混在（イベントループブロック） | backend/src/db/session.py |
| SE-002 | コード品質 | `debug=True` がデフォルト値で本番でも全SQLクエリがログ出力 | backend/src/config.py:32 |
| SEC-002 | セキュリティ | 本番Dockerfileに --reload フラグが残存 | backend/Dockerfile:14 |
| SEC-003 | セキュリティ | WebSocketエンドポイントに認証機構がない | backend/src/api/races.py:540-570 |
| SEC-004 | セキュリティ | FastAPI自動ドキュメント（/docs, /redoc）が認証なしで公開 | backend/src/main.py:13-17 |
| SEC-005 | セキュリティ | Agentエンドポイント（コマンドデキュー等）が無認証 | backend/src/api/agent_router.py:131-168 |
| PERF-001 | パフォーマンス | DB接続が同期エンジン（psycopg2）のみ・非同期エンジン未使用 | backend/src/db/session.py |
| PERF-002 | パフォーマンス | DB接続プールのサイズ未設定（デフォルト5のまま） | backend/src/db/session.py:8 |
| PERF-003 | パフォーマンス | 指数算出（CompositeIndex）がHTTPリクエストスレッドで同期実行 | backend/src/api/import_router.py:85-112 |
| PERF-004 | パフォーマンス | 全APIレスポンスが `cache: 'no-store'`（Next.jsキャッシュ無効） | frontend/src/lib/api.ts:105-109 |
| PERF-005 | パフォーマンス | ログインページの1.9MB画像が `<img>` タグで読み込み（next/image未使用） | frontend/src/app/login/page.tsx:31 |
| QA-003 | QA | CI/CDパイプラインが未整備（自動テスト実行なし） | .github/（未存在） |
| QA-004 | QA | テストカバレッジの計測設定が未整備 | backend/pyproject.toml |
| QA-005 | QA | 5つの指数モジュール（confidence/meet_bias/paddock/training/anagusa）にテストなし | backend/src/indices/ |
| QA-006 | QA | E2Eテストが皆無（認証フロー等の主要パスが自動検証されていない） | frontend/ |
| LEGAL-001 | 法務 | JRA-VAN Data Lab利用規約：Web経由表示の許諾確認が必要 | — |
| LEGAL-002 | 法務 | netkeibaスクレイピングの利用規約違反リスク | backend/src/importers/netkeiba_scraper.py |
| LEGAL-003 | 法務 | プライバシーポリシーが存在しない（個人情報保護法対応） | frontend/src/app/（未存在） |
| A11Y-001 | アクセシビリティ | Rechartsグラフに accessibilityLayer・aria-label が未設定 | frontend/src/components/ProbabilityChart.tsx |
| A11Y-002 | アクセシビリティ | ログインフォームのパスワード入力欄に label 要素がない | frontend/src/app/login/page.tsx |
| A11Y-003 | アクセシビリティ | スパークラインSVGにアクセシビリティ属性がない | frontend/src/components/IndicesTable.tsx:83 |
| A11Y-004 | アクセシビリティ | ソート・タブボタンに aria-pressed/aria-selected が未設定 | 複数コンポーネント |
| A11Y-005 | アクセシビリティ | 馬カード展開ボタンに aria-expanded が未設定 | frontend/src/components/IndicesTable.tsx |
| UX-001 | UX/UI | error.tsx / not-found.tsx が未実装（英語デフォルト画面が表示される） | frontend/src/app/ |
| UX-002 | UX/UI | PWAアイコンが仕様不足（192×192/512×512の明示サイズ未定義） | frontend/public/manifest.json |
| UX-003 | UX/UI | WebSocket切断時にユーザーへのフィードバックが皆無 | IndicesTable.tsx, RaceDetailClient.tsx |
| INFRA-004 | インフラ | DBバックアップ戦略が存在しない（PostgreSQLの定期バックアップなし） | scripts/ |
| INFRA-006 | インフラ | モニタリング・アラート設定が存在しない | docker-compose.sekito.yml |
| INFRA-007 | インフラ | ログローテーションが未設定（ディスクフルリスク） | logs/ |
| INFRA-008 | インフラ | CHANGE_NOTIFY_API_KEYが空の場合に認証スキップ（フェイルオープン） | backend/src/api/import_router.py |
| SEO-003 | SEO | Service Workerが未実装でPWAとして不完全 | frontend/public/ |
| SEO-004 | SEO | manifest.json の start_url がbasePath未考慮（本番PWAインストール失敗） | frontend/public/manifest.json |

### 🟡 Medium（次回リリースまでに対応）

| ID | カテゴリ | タイトル |
|----|---------|---------|
| SE-003 | コード品質 | verify_api_key・DbDep が複数ルーターで重複定義 |
| SE-004 | コード品質 | デバッグ用 logger.warning が本番コードに残存 |
| SE-005 | コード品質 | `_fetch_anagusa_picks` で生SQL（sqlalchemy.text）使用 |
| SE-006 | コード品質 | races.py が594行・単一ファイルの責務が過多 |
| SE-007 | コード品質 | IndicesTable.tsx が477行・責務混在 |
| SEC-007 | セキュリティ | セキュリティヘッダー（CSP, X-Frame-Options等）が未設定 |
| SEC-008 | セキュリティ | CORS設定で allow_methods/headers がワイルドカード |
| SEC-009 | セキュリティ | callbackUrl パラメータのバリデーション不十分（オープンリダイレクト） |
| SEC-010 | セキュリティ | APIレート制限が未実装 |
| SEC-011 | セキュリティ | AUTH_BYPASS_DEV に NODE_ENV チェックがない |
| PERF-006 | パフォーマンス | next.config.ts が最小設定（画像フォーマット・React Compiler未設定） |
| PERF-007 | パフォーマンス | レンダリングループ内で毎回 sort + findIndex（O(n² log n)） |
| PERF-009 | パフォーマンス | レース詳細ページで fetchRace → fetchRacesByDate がシリアル実行 |
| PERF-012 | パフォーマンス | アプリケーションレベルのキャッシュ（Redis等）が未実装 |
| LEGAL-004 | 法務 | 利用規約が存在しない |
| LEGAL-005 | 法務 | Cookieポリシー・同意バナーが未実装 |
| LEGAL-007 | 法務 | Google OAuthのOAuthコンセントスクリーンにプライバシーポリシーURL未登録 |
| A11Y-006 | アクセシビリティ | ナビゲーションリンク「←」「→」に aria-label がない |
| A11Y-007 | アクセシビリティ | DateNav の非表示input に aria-hidden・tabIndex={-1} が未設定 |
| A11Y-008 | アクセシビリティ | スキップナビゲーションリンクが未実装 |
| A11Y-009 | アクセシビリティ | IndexBar にプログレスバーARIA属性がない |
| A11Y-010 | アクセシビリティ | カラーのみで情報を区別している箇所が複数存在 |
| A11Y-011 | アクセシビリティ | RaceNav・CourseTabView のタブ構造に role="tablist" が未使用 |
| A11Y-012 | アクセシビリティ | ローディングスケルトンに aria-busy・aria-live がない |
| A11Y-013 | アクセシビリティ | フォーカスリングが focus:outline-none で消える箇所がある |
| UX-004 | UX/UI | ログインフォームにラベル要素がない |
| UX-006 | UX/UI | prefers-reduced-motion 対応が未実装 |
| UX-007 | UX/UI | ProbabilityChart の初回レンダリングでレイアウトシフトが発生 |
| UX-009 | UX/UI | ダークモードでハードコードカラーが正しく描画されない |
| INFRA-009 | インフラ | Alembicマイグレーションがデプロイスクリプトに含まれていない |
| INFRA-011 | インフラ | docker-compose.ymlで非推奨の version フィールドを使用 |
| INFRA-012 | インフラ | デプロイ時にダウンタイムが発生する構成 |
| INFRA-013 | インフラ | リソース制限（CPU/メモリ）が未設定 |
| SEO-001 | SEO | robots.txt が存在しない |
| SEO-002 | SEO | OGP（Open Graph Protocol）タグが未設定 |
| SEO-005 | SEO | 各ページにページ固有のメタデータが設定されていない |
| SEO-007 | SEO | canonical URL が未設定 |
| SEO-009 | SEO | viewport メタタグが未設定（Viewport API未使用） |
| SEO-010 | SEO | セキュリティヘッダーが next.config.ts に未設定 |

### ⚪ Low（余裕がある時に対応）

SE-011（INDEX_WEIGHTS合計値の浮動小数点誤差）、SE-012（インメモリ状態管理のプロセス再起動消失）、SE-013（WebSocket接続コードの重複）、PERF-011（公開画像のWebP未変換）、PERF-013（ProbabilityChart useMemo未使用）、QA-007（pre-commitフック未設定）、QA-008（conftest.py未作成）、QA-010（型チェックコマンド未定義）、A11Y-016（タッチターゲットサイズ）、A11Y-017（テーブルcaption未設定）、A11Y-018（ページタイトルが全ページ同一）、UX-012（manifest start_url）、INFRA-015（uvバージョン固定なし）、INFRA-016（ステージング環境なし）、SEO-011（title/description文字数不足）、SEO-012（h1タグなし）など

---

## 詳細分析

### セキュリティ

**最重要：.env.localのシークレット管理（SEC-001）**

`frontend/.env.local` に `AUTH_PASSWORD="nishikawa"` を含む複数のシークレットが平文記載されている。.gitignoreで除外済みだが、端末紛失・誤コミット時のリスクが極めて高い。AUTH_SECRETが漏洩すると攻撃者が任意のセッショントークンを偽造可能になる。

**WebSocket認証の欠如（SEC-003）**

`/api/races/{race_id}/odds/ws` および `/api/races/{race_id}/results/ws` は認証なしで接続可能。リアルタイムオッズデータが誰でも取得でき、大量接続によるDoS攻撃のリスクもある。

**APIドキュメントの公開（SEC-004）**

本番環境でも `/docs`（Swagger UI）が認証なしで公開されており、全APIエンドポイントの仕様が外部から閲覧可能。`FastAPI(docs_url=None)` で即時無効化が推奨。

---

### テスト・品質

フロントエンドのテスト環境は**ゼロから構築が必要**な状態。バックエンドの指数計算ロジック（236テスト）は質・量ともに高いが、APIエンドポイント・認証フロー・WebSocketの統合テストが完全に欠落している。CI/CDパイプラインの構築とセットで取り組むことを推奨。

---

### パフォーマンス

**非同期移行（PERF-001/SE-001）**

`psycopg2`（同期）を使用しながらFastAPIの`async def`エンドポイントで直接DBアクセスしているため、イベントループをブロックしている。`asyncpg` + `create_async_engine` + `AsyncSession` への移行が根本解決策。

**画像最適化（PERF-005/PERF-011）**

ログインページの1.9MB PNGを`<img>`タグで直接読み込み。`next/image`に切り替えるだけで自動WebP変換・lazy loading・srcset生成が有効になる。

---

### 法務・コンプライアンス

**最優先対応：プライバシーポリシーの整備（LEGAL-003）**

Google OAuthを使用するシステムとして、個人情報保護法上のプライバシーポリシー公開は実質必須。`/privacy` ページと `/terms` ページの作成を推奨。

**netkeibaスクレイピング（LEGAL-002）**

`backend/src/importers/netkeiba_scraper.py` でnetkeibaのプレミアム会員セッションを使用したスクレイピングを実施。利用規約上のグレーゾーンであり、代替手段（JV-Dataのみでの補完）への移行を検討することを推奨。

---

### アクセシビリティ

**WCAG 2.2 AA準拠には複数の改修が必要**。特にRechartsグラフへの`accessibilityLayer`追加と、タブ・ボタン群への`aria-selected`/`aria-expanded`設定は工数が少なく効果が大きい。スキップナビゲーションリンクの追加（`layout.tsx` 1箇所の変更）も推奨。

---

### インフラ/DevOps

**本番volumeマウントの廃止（INFRA-003）**

`docker-compose.sekito.yml`で`./backend/src:/app/src`をマウントしているため、Dockerイメージのビルド成果物が使われず再現性が失われている。本番ではvolumeマウントを廃止し`docker compose build`でイメージにソースを組み込む形を推奨。

**DBバックアップ（INFRA-004）**

PostgreSQLの定期バックアップが存在しない。`pg_dump` + cronによる自動バックアップをVPS外ストレージ（S3等）への保存と合わせて早急に整備することを推奨。

---

## 修正推奨ロードマップ

### Phase 1：即時対応（1週間以内）
1. `config.py` の `debug: bool = False` に変更（SE-002、工数: 5分）
2. FastAPIドキュメントの本番非公開化（SEC-004、工数: 10分）
3. Agentエンドポイントへの ApiKeyDep 追加（SEC-005、工数: 30分）
4. DockerfileからNon-rootユーザー設定（INFRA-002、工数: 1時間）
5. Dockerfileから --reload 削除（SEC-002、工数: 5分）
6. manifest.json の start_url と scope を `/kiseki/` に修正（SEO-004、工数: 10分）
7. AUTH_BYPASS_DEV に NODE_ENV チェック追加（SEC-011、工数: 10分）

### Phase 2：短期対応（2〜4週間）
1. WebSocketエンドポイントへの認証追加（SEC-003）
2. プライバシーポリシー・利用規約ページの作成（LEGAL-003/004）
3. error.tsx / not-found.tsx の実装（UX-001）
4. next/image への画像置き換え（PERF-005）
5. DBバックアップスクリプトの作成（INFRA-004）
6. GitHub Actions CI/CDパイプラインの構築（INFRA-001/QA-003）
7. セキュリティヘッダーの追加（SEC-007/SEO-010）

### Phase 3：中期対応（1〜3ヶ月）
1. 非同期DBエンジン（asyncpg）への移行（PERF-001/SE-001）
2. フロントエンドテスト環境の構築（Vitest + Testing Library）（QA-001）
3. APIエンドポイント統合テストの実装（QA-002）
4. アクセシビリティ改善（Recharts対応・タブARIA・スキップリンク）（A11Y系）
5. 本番volumeマウントの廃止（INFRA-003）
6. Service Worker実装（next-pwa）（SEO-003）
7. APIキャッシュ戦略の整備（PERF-004）

---

*生成日時: 2026-03-31*
*監査エンジン: kiseki Web品質監査スキル v1.0*
