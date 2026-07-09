import type { NextConfig } from "next";
import withPWAInit from "@ducanh2912/next-pwa";

const withPWA = withPWAInit({
  dest: "public",
  disable: process.env.NODE_ENV === "development",
  // 新ビルドデプロイ時に旧キャッシュを即座に置き換える（Server Action ハッシュ不一致防止）
  workboxOptions: {
    skipWaiting: true,
    clientsClaim: true,
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
