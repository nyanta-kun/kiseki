import type { NextConfig } from "next";
import withPWAInit, { runtimeCaching as defaultRuntimeCaching } from "@ducanh2912/next-pwa";
import { isExcludedFromPrecache } from "./src/lib/pwaPrecache";

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
//
// ⚠️ タイムアウトを入れた副作用として、**回線が遅いだけ**のときもキャッシュを返すように
// なった。既定の保持は 24時間（`maxAgeSeconds: 86400`）で、オッズ・指数を含む RSC が
// 丸一日前のものになりうる。フォールバックは残したまま陳腐化だけ抑えるため、
// ページ系の保持を **1時間**へ縮める。
const PAGE_CACHE_MAX_AGE_SECONDS = 60 * 60;

// `_next/static` の JS は CacheFirst で `maxEntries: 64` が既定。Next.js の
// チャンク数は容易にこれを超え、超えると**古いものから追い出される**。
// 追い出されたチャンクをデプロイ後に要求すると 404 になり、強制リロードが起きる
// （＝復帰時に固まって見える経路のひとつ）。実測で precache だけで 89 件あるため広げる。
//
// ⚠️ 2026-09-08 以降、`_next/static` は下の `BYPASS_IMMUTABLE_STATIC` が先に拾うので
//    **このルートには来ない**。バイパスを外したときの保険として残してある。
const STATIC_JS_MAX_ENTRIES = 256;

// 🔴 **`_next/static` を Service Worker に持たせない**（2026-09-08）。
//
// iOS Safari で「しばらく間を空けてから戻ると表示までに 15〜27 秒かかる」という報告。
// nginx のアクセスログで**HTML が届いてからアプリが最初のデータを取りに行くまで**を
// 全ナビゲーションについて測ると、サーバでも API でも無かった:
//
//   iPhone（間を空けて再表示）  15 / 16 / 16 / 22 / 27 秒
//   iPhone（続けて操作中）       0 /  0 /  1 秒
//   Mac Chrome（同じビルド）     1 秒
//   サーバ                       HTML 100ms 以下・API 80〜485ms
//
// その2日間 `_next/static` へのリクエストは **0件**（＝JS は端末のキャッシュから
// 出ている）ので、15〜27秒はまるごと**端末内の処理**。要求の順番が決定的だった:
//
//   遅いとき  :40 /keirin → :42 /sw.js → ……14秒…… → :56 初API
//   速いとき  :05 /keirin → :05 初API  → :06 /sw.js
//
// 速いときの `/sw.js` はページの `register()`（ハイドレーション後）。遅いときは
// **ページの処理より先に** `/sw.js` が出ている＝**SW を起こすところから**始まる回。
// iOS は放置した SW を終了させるので、久しぶりの表示は「SW 起動 → Cache Storage から
// 1.3MB の JS を出す」をやり直す。それまで precache 93件・ランタイム18本を抱えていた。
//
// `_next/static` は**内容ハッシュ付きで `immutable, max-age=31536000`** を返している
// （下の headers()）。つまり**ブラウザの HTTP キャッシュだけで完結**しており、
// SW が二重に持つ意味が無い。Mac Chrome が 1秒で出ているのはまさにこの経路。
// → precache からもランタイムキャッシュからも外し、SW が扱うのは
//   **HTML / RSC / API だけ**にする。
//
// ⚠️ 失うのは「オフラインでの静的アセット供給」だが、この画面は認証必須で
//    API が無ければ何も表示できないため、オフライン動作は元から成立していない。
// ⚠️ **戻すときは実機で計測すること。** 体感は端末の温まり具合で大きく変わるので、
//    印象ではなく nginx ログの「HTML → 初API」で測る（上表と同じ測り方）。
// 🔴 **`urlPattern` に関数を書くときはクロージャを使ってはいけない**（2026-09-08 実測）。
//    next-pwa は関数を `toString()` して sw.js へ**そのまま埋め込む**ので、外側の
//    変数（正規表現を入れた定数など）は**消える**。生成された sw.js が
//    `!IMMUTABLE_STATIC.test(...)` のように未定義の名前を参照し、実行時に
//    ReferenceError で **SW ごと死ぬ**（ビルドも型検査も通ってしまう）。
//    → ここでは関数を避け、**正規表現のルートを1本先頭に置く**。
//      Workbox のルータは登録順で最初に一致したものを使う。
const BYPASS_IMMUTABLE_STATIC = {
  // `_next/static` は内容ハッシュ付きで `immutable, max-age=31536000`（下の headers()）。
  // NetworkOnly ＝ SW は fetch() へ素通しするだけ。ブラウザの HTTP キャッシュが
  // 応えるのでネットワークには出ないし、Cache Storage も IndexedDB も触らない。
  urlPattern: /\/_next\/static\//i,
  handler: "NetworkOnly" as const,
};

const runtimeCaching = [BYPASS_IMMUTABLE_STATIC, ...defaultRuntimeCaching.map((entry) => {
  const name = String(entry.options?.cacheName ?? "");

  if (["pages", "pages-rsc", "pages-rsc-prefetch"].includes(name)) {
    return {
      ...entry,
      options: {
        ...entry.options,
        networkTimeoutSeconds: 3,
        expiration: {
          ...entry.options?.expiration,
          maxAgeSeconds: PAGE_CACHE_MAX_AGE_SECONDS,
        },
      },
    };
  }

  if (name === "next-static-js-assets") {
    return {
      ...entry,
      options: {
        ...entry.options,
        expiration: {
          ...entry.options?.expiration,
          maxEntries: STATIC_JS_MAX_ENTRIES,
        },
      },
    };
  }

  return entry;
})];

// next-pwa の既定 `exclude`。**自前で渡すと既定は消える**ので必ず含めること
// （実測: 自前の配列だけにすると `server/` 配下と各種 manifest が precache に載る）。
//
// 🟢 `exclude` の関数は **webpack のビルド時に評価される**（sw.js へは埋め込まれない）
//    ので、`urlPattern` と違ってクロージャを使ってよい。
const NEXT_PWA_DEFAULT_EXCLUDE = [
  /\/_next\/static\/.*(?<!\.p)\.woff2/,
  /\.map$/,
  /^manifest.*\.js$/,
];

const withPWA = withPWAInit({
  dest: "public",
  disable: process.env.NODE_ENV === "development",
  // 新ビルドデプロイ時に旧キャッシュを即座に置き換える（Server Action ハッシュ不一致防止）
  workboxOptions: {
    skipWaiting: true,
    clientsClaim: true,
    runtimeCaching,
    // 🔴 precache からも `_next/static` を外す（上記の理由）。判定は
    //    `src/lib/pwaPrecache.ts` が正本（サーバ専用アセットの除外もそこに書いてある。
    //    落とすと SW の install ごと失敗する）。
    exclude: [
      ...NEXT_PWA_DEFAULT_EXCLUDE,
      ({ asset }: { asset: { name: string } }) => isExcludedFromPrecache(asset.name),
    ],
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
