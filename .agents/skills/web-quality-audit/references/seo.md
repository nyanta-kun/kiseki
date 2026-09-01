# SEO/Web標準 チェック項目

## 検索クエリ例
- `SEO best practices {year}`
- `{framework} SEO optimization guide`
- `structured data schema.org best practices`
- `Core Web Vitals SEO impact {year}`
- `technical SEO checklist {year}`
- `meta tags best practices {year}`

## チェック項目

### 1. 基本的なSEO要素
- **titleタグ**: 各ページにユニークで適切なタイトル（30-60文字）
- **meta description**: 各ページに適切な説明文（120-158文字）
- **見出し構造**: h1は各ページに1つ、適切な階層構造
- **URL構造**: 人間が読める、意味のあるURL
- **canonical**: 重複コンテンツの正規化
- **robots.txt**: 適切なクロール制御
- **sitemap.xml**: 最新のサイトマップの提供

### 2. 構造化データ（Schema.org）
- **JSON-LD**: 適切な構造化データの実装
- **パンくずリスト**: BreadcrumbListスキーマ
- **組織情報**: Organizationスキーマ
- **FAQ**: FAQPageスキーマ（該当する場合）
- **製品/レビュー**: Product、Reviewスキーマ（ECの場合）
- **バリデーション**: 構造化データのエラー確認

### 3. OGP（Open Graph Protocol）
- **og:title**: ページタイトル
- **og:description**: ページ説明
- **og:image**: シェア画像（1200x630px推奨）
- **og:url**: 正規URL
- **og:type**: コンテンツタイプ
- **Twitter Card**: twitter:card、twitter:image等

### 4. テクニカルSEO
- **クロール効率**: 不要ページのnoindex/nofollow
- **ページ速度**: Core Web Vitals（性能エージェントと連携）
- **モバイルフレンドリー**: モバイル対応（UXエージェントと連携）
- **HTTPS**: 全ページのHTTPS化
- **リダイレクト**: 適切な301/302リダイレクト、リダイレクトチェーン回避
- **404処理**: 壊れたリンクの検出と対処
- **国際化**: hreflang属性の適切な設定（多言語サイトの場合）

### 5. HTML品質
- **HTMLバリデーション**: W3C Validator準拠
- **セマンティックHTML**: 適切なHTML5要素の使用（nav, article, section, aside等）
- **画像のalt属性**: 全画像に適切な代替テキスト
- **リンクテキスト**: 説明的なアンカーテキスト（「こちら」の回避）

### 6. パフォーマンスとSEO
- **Core Web Vitals**: LCP、INP、CLSの最適化
- **JavaScript SEO**: SSR/SSGによるクロール可能なコンテンツ
- **遅延レンダリング**: 検索エンジンのJS実行能力への配慮
- **画像SEO**: 適切なファイル名、alt、サイズ最適化

### 7. SPA特有のSEO対策
- **プリレンダリング/SSR**: 検索エンジン向けのHTML生成
- **動的レンダリング**: ボット向けの別レンダリング（推奨されないが現状対応として）
- **History API**: 適切なURL管理
- **メタタグの動的更新**: ページ遷移時のmeta情報更新
