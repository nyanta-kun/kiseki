# セキュリティ チェック項目

## 検索クエリ例
- `OWASP Top 10 {year}`
- `web application security checklist {year}`
- `{framework} security best practices {year}`
- `{language} common vulnerabilities`
- `web security headers best practices`

## チェック項目

### 1. OWASP Top 10 対応
- **インジェクション**: SQLインジェクション、NoSQLインジェクション、コマンドインジェクション
- **認証の不備**: パスワードポリシー、セッション管理、多要素認証
- **機密データの露出**: 暗号化、ハッシュ化、データマスキング
- **XML外部実体参照 (XXE)**: XMLパーサーの設定
- **アクセス制御の不備**: 水平/垂直権限昇格、IDOR
- **セキュリティ設定ミス**: デフォルト設定、不要な機能の有効化
- **XSS**: 入力のサニタイズ、出力のエスケープ、CSP設定
- **安全でないデシリアライゼーション**: オブジェクトの検証
- **既知の脆弱性を持つコンポーネントの使用**: 依存パッケージの脆弱性
- **不十分なログとモニタリング**: セキュリティイベントの記録

### 2. 認証・認可
- **認証方式**: JWT、セッション、OAuth2の適切な実装
- **パスワード管理**: bcrypt/argon2等による適切なハッシュ化
- **セッション管理**: セッション固定化攻撃対策、適切な有効期限
- **CSRF対策**: トークンベースの対策実装
- **API認証**: APIキー、Bearer Token、OAuth2の適切な使い分け
- **RBAC/ABAC**: ロールベース/属性ベースのアクセス制御

### 3. データ保護
- **HTTPS強制**: HTTP→HTTPSリダイレクト、HSTS設定
- **機密情報のハードコード**: ソースコード内のシークレット、APIキー、パスワード
- **環境変数**: .envファイルの.gitignore設定、シークレット管理
- **データベース暗号化**: 保存時暗号化（at-rest）、通信時暗号化（in-transit）
- **個人情報の取り扱い**: PII（個人識別情報）の適切な保護

### 4. HTTPセキュリティヘッダー
- `Content-Security-Policy (CSP)`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options` / `frame-ancestors`
- `Strict-Transport-Security (HSTS)`
- `Referrer-Policy`
- `Permissions-Policy`
- `X-XSS-Protection`（レガシーブラウザ対応）

### 5. 依存パッケージの脆弱性
- `npm audit` / `yarn audit` / `pip audit` 等の結果確認
- 既知のCVEを持つパッケージの検出
- 依存パッケージの更新状況（メジャーバージョンの遅れ）
- サプライチェーン攻撃への対策（lock ファイルの存在）

### 6. フロントエンド固有
- **DOM Based XSS**: dangerouslySetInnerHTML/v-html 等の使用箇所
- **オープンリダイレクト**: URLパラメータによるリダイレクト処理
- **postMessage**: origin検証の実装
- **localStorage/sessionStorage**: 機密情報の保存状況
- **CORS設定**: 適切なOrigin制限

### 7. インフラセキュリティ
- **ファイアウォール**: 不要なポートの開放がないか
- **コンテナセキュリティ**: ベースイメージの脆弱性、root実行の回避
- **シークレット管理**: Vault、AWS Secrets Manager等の使用
