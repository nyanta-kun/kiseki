# 性能 チェック項目

## 検索クエリ例
- `web performance optimization {year}`
- `{framework} performance best practices {year}`
- `Core Web Vitals optimization guide`
- `{framework} bundle size optimization`
- `web application caching strategy`

## チェック項目

### 1. Core Web Vitals
- **LCP (Largest Contentful Paint)**: 2.5秒以内が目標
- **INP (Interaction to Next Paint)**: 200ms以内が目標
- **CLS (Cumulative Layout Shift)**: 0.1以下が目標

### 2. フロントエンド性能
- **バンドルサイズ**: JavaScript/CSSのバンドルサイズは適切か
- **コード分割**: 動的インポート/遅延ロードの活用
- **Tree Shaking**: 未使用コードの除去設定
- **画像最適化**: 適切な形式（WebP/AVIF）、サイズ、遅延読み込み
- **フォント最適化**: font-display設定、サブセット化、プリロード
- **CSS最適化**: 未使用CSSの除去、Critical CSSのインライン化
- **レンダリングパフォーマンス**: 不要な再レンダリング、仮想スクロールの活用

### 3. ネットワーク最適化
- **HTTP/2, HTTP/3対応**: プロトコルの最適化
- **圧縮**: gzip/Brotli圧縮の設定
- **キャッシュ戦略**: Cache-Control、ETag、Service Workerの活用
- **CDN利用**: 静的アセットのCDN配信
- **API呼び出し最適化**: バッチリクエスト、データフェッチ戦略
- **プリフェッチ/プリロード**: 重要リソースの先読み

### 4. バックエンド性能
- **データベースクエリ**: N+1問題、インデックス設計、クエリ最適化
- **キャッシング**: アプリケーションレベルのキャッシュ（Redis等）
- **非同期処理**: 重い処理のバックグラウンド化
- **API応答時間**: エンドポイントごとの応答時間の妥当性
- **ペイロードサイズ**: APIレスポンスのデータ量の最適化
- **ページネーション**: 大量データの適切な分割取得

### 5. フレームワーク固有の性能チェック

#### React/Next.js
- React.memo、useMemo、useCallbackの適切な使用
- Suspense/Streamingの活用
- ISR/SSG/SSRの適切な選択
- React Server Componentsの活用（App Router）

#### Vue/Nuxt
- computed vs method の使い分け
- v-once、v-memo の活用
- 遅延ロードルートの設定

### 6. モニタリング
- **パフォーマンス計測**: Lighthouse、WebPageTest等の使用状況
- **リアルユーザーモニタリング (RUM)**: 本番環境での計測体制
- **アラート設定**: 性能劣化時の通知体制
