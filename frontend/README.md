# kiseki フロントエンド

競馬予測指数システム kiseki の Next.js 16 フロントエンド。  
本番: https://galloplab.com/kiseki/

## 技術スタック

- **Next.js 16** (App Router / standalone output)
- **Tailwind CSS** / shadcn/ui
- **Recharts** — 勝率・複勝率チャート
- **Auth.js v5** (next-auth@beta) — Google OAuth 認証
- **pnpm** — パッケージ管理

## ローカル開発

```bash
pnpm install
pnpm dev        # http://localhost:3000/
pnpm build      # 本番ビルド確認
pnpm lint       # ESLint
```

## 環境変数

| 変数 | 説明 |
|---|---|
| `BACKEND_URL` | SSR 用バックエンド URL（例: `http://backend:8000/api`） |
| `NEXT_PUBLIC_API_URL` | クライアント用バックエンド URL（例: `https://api.galloplab.com`） |
| `AUTH_SECRET` | Auth.js セッション暗号化キー |
| `AUTH_URL` | Auth.js コールバックベース URL（例: `https://galloplab.com/auth`） |
| `AUTH_GOOGLE_ID` | Google OAuth クライアント ID |
| `AUTH_GOOGLE_SECRET` | Google OAuth クライアントシークレット |

## 主要コンポーネント

| ファイル | 役割 |
|---|---|
| `src/app/races/page.tsx` | レース一覧（日付ナビ・グレードバッジ） |
| `src/app/races/[id]/page.tsx` | レース詳細（SSR + WebSocket） |
| `src/components/ProbabilityChart.tsx` | 勝率/複勝率横棒グラフ（ResizeObserver で幅計測） |
| `src/components/IndicesTable.tsx` | 出馬表 指数一覧テーブル |
| `src/components/EVSummary.tsx` | 期待値サマリー（単複EV上位馬） |
| `src/components/RaceDetailClient.tsx` | WebSocket で成績をリアルタイム受信 |
| `src/components/DateNav.tsx` | 開催日ナビゲーション（カレンダー選択） |
| `src/lib/api.ts` | REST API / WebSocket ユーティリティ |
| `src/proxy.ts` | Middleware（未ログイン→ログイン画面リダイレクト） |
| `src/auth.ts` | Auth.js 設定（Google OAuth / JWT strategy） |

## デプロイ

```bash
# galloplab.com へデプロイ（プロジェクトルートから）
bash scripts/deploy-galloplab.sh
```

Docker イメージは `node:22-alpine` ベースの multi-stage build。  
`next.config.ts` で `output: "standalone"` を指定。

## Auth.js v5 注意点

- `signIn` / `signOut` はサーバーアクション経由（`src/app/actions/auth.ts`）
- セッション確認は `auth()` を直接呼び出す（`useSession` は使用しない）
- コールバック URL: `https://galloplab.com/auth/callback/google`
- `AUTH_URL` は `/auth` まで（`/api/auth` ではない）
