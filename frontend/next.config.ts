import type { NextConfig } from "next";
import withPWAInit, { runtimeCaching as defaultRuntimeCaching } from "@ducanh2912/next-pwa";

// 🔴 HTML / RSC のルートに **ネットワークタイムアウトを足す**（2026-08-20）。
//
// next-pwa の既定 runtimeCaching は NetworkFirst を使うが、
// `networkTimeoutSeconds` が入っているのは `apis`（10秒）と `cross-origin`（10秒）だけで、
// **`pages` / `pages-rsc` / `pages-rsc-prefetch` には無い**。
//
// NetworkFirst はタイムアウトが無いと、ネットワークの fetch が決着するまで
// **いつまでもキャッシュへフォールバックしない**。
//
// iOS Safari で他アプリへ切り替えて戻ると、OS が張り直す前の死んだ接続へ
// リクエストが出て長時間ハングする。その間:
//   - 画面は（描画済みなので）見えているのにタップだけ効かない
//   - Next.js の router 遷移も RSC 取得待ちで止まる
// ＝「復帰後しばらく操作できない」。ドメイン内の**全ページ**で起きる
// （/chihou/races/[id] と /keirin の双方で報告された）。
//
// 3秒で諦めてキャッシュを返す。キャッシュが無ければ従来どおりネットワークを待つので
// 表示できなくなることはない。
const runtimeCaching = defaultRuntimeCaching.map((entry) =>
  ["pages", "pages-rsc", "pages-rsc-prefetch"].includes(
    String(entry.options?.cacheName ?? ""),
  )
    ? {
        ...entry,
        options: { ...entry.options, networkTimeoutSeconds: 3 },
      }
    : entry,
);

const withPWA = withPWAInit({
  dest: "public",
  disable: process.env.NODE_ENV === "development",
  // 新ビルドデプロイ時に旧キャッシュを即座に置き換える（Server Action ハッシュ不一致防止）
  workboxOptions: {
    skipWaiting: true,
    clientsClaim: true,
    runtimeCaching,
  },
});

const isDev = process.env.NODE_ENV === "development";

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "X-Robots-Tag", value: "noindex, nofollow" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.googletagmanager.com",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: https:",
      "font-src 'self' data:",
      `connect-src 'self' wss://galloplab.com wss://api.galloplab.com wss://sekito-stable.com https://galloplab.com https://api.galloplab.com https://sekito-stable.com https://accounts.google.com https://www.google-analytics.com https://analytics.google.com https://www.googletagmanager.com${isDev ? " ws://localhost:8000 http://localhost:8000" : ""}`,
      "frame-src https://accounts.google.com",
      "frame-ancestors 'none'",
    ].join("; "),
  },
  ...(process.env.NODE_ENV === "production"
    ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" }]
    : []),
];

const nextConfig: NextConfig = {
  output: "standalone",
  reactCompiler: true,
  // 型チェックは `next build` の中で走らせない。
  //
  // CI の frontend ジョブが `pnpm exec tsc --noEmit` を独立したステップとして実行しており、
  // deploy ジョブはその成功を needs で要求している。`next build` の中でもう一度
  // 型チェックするのは**純粋な二度手間**で、GitHub Actions の runner では
  // 1 ビルドあたり約 20 秒を捨てていた（ローカル実測 17.5s → 13.7s。runner は約4.6倍遅い）。
  //
  // ⚠️ CI から `tsc --noEmit` のステップを消してはいけない。消すと型エラーが
  //    誰にも検出されなくなる（このフラグは検査を抑制するのではなく完全に飛ばす）。
  //
  // なお Next.js 16 では `eslint` 設定キーが廃止され `next build` は ESLint を
  // 実行しないため、ESLint 側には同種の設定は不要。
  typescript: { ignoreBuildErrors: true },
  // next-pwa injects webpack config; turbopack: {} tells Next.js 16 this is intentional
  turbopack: {},
  images: {
    formats: ["image/avif", "image/webp"],
    remotePatterns: [
      {
        protocol: "https",
        hostname: "lh3.googleusercontent.com",
      },
    ],
  },
  async headers() {
    return [
      {
        // HTML ページは毎回サーバーで鮮度検証（デプロイ後の Server Action ハッシュ不一致防止）。
        // no-store だと iOS Safari の bfcache/Page Cache 対象外になりタブ復帰・戻る操作が
        // 毎回フル再読み込みになるため、no-cache（再検証必須・保存は許可）に緩和。
        source: "/((?!_next/static|_next/image|favicon).*)",
        headers: [
          { key: "Cache-Control", value: "private, no-cache" },
          ...securityHeaders,
        ],
      },
      // /_next/static/ は本番ビルドのみ永続キャッシュ（devモードはchunkがcontent-hash付きでないため除外）
      ...(!isDev ? [{
        source: "/_next/static/(.*)",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      }] : []),
    ];
  },
  async redirects() {
    return [
      // galloplab.com移行: /kiseki 旧URLを新URLにリダイレクト
      { source: "/kiseki", destination: "/races", permanent: true },
      { source: "/kiseki/:path*", destination: "/:path*", permanent: true },
    ];
  },
};

export default withPWA(nextConfig);
