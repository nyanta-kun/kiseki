# --- 依存関係インストール ---
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
# BuildKit キャッシュマウントでpnpmストアを再利用（lockfile変更時も高速化）
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# --- ビルド ---
FROM node:22-alpine AS builder
WORKDIR /app
# NEXT_PUBLIC_* はビルド時にクライアントバンドルへ静的埋め込みされるため ARG で受け取る
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    corepack enable && pnpm build

# --- 実行 ---
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
