/**
 * Next.js 16 ではミドルウェアのエントリーポイントは proxy.ts（middleware.ts は非推奨）。
 * Next.js 16 では proxy.ts はパスを basePath(/kiseki) 込みで受け取るため、
 * matcher および pathname 判定の両方でこれを考慮する必要がある。
 */
import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const BASEPATH = "/kiseki";

/** basePath を除去して正規化したパスを返す */
function normalizePath(rawPathname: string): string {
  return rawPathname.startsWith(BASEPATH)
    ? rawPathname.slice(BASEPATH.length) || "/"
    : rawPathname;
}

export default async function proxy(req: NextRequest): Promise<NextResponse> {
  // 開発時バイパス
  if (process.env.AUTH_BYPASS_DEV === "true") {
    return NextResponse.next();
  }

  const pathname = normalizePath(req.nextUrl.pathname);

  // _next 静的アセット・Auth API ルートは素通し
  if (
    pathname.startsWith("/_next/") ||
    pathname.startsWith("/api/auth") ||
    pathname === "/favicon.ico" ||
    pathname === "/manifest.json" ||
    pathname.startsWith("/images/") ||
    /^\/icon-.*\.png$/.test(pathname)
  ) {
    return NextResponse.next();
  }

  // セッション確認: JWE 対応の getToken を使用
  const token = await getToken({
    req,
    secret: process.env.AUTH_SECRET!,
    secureCookie: process.env.NODE_ENV === "production",
  }).catch(() => null);

  // ログインページは認証不要
  if (pathname === "/login") {
    if (token) {
      return NextResponse.redirect(new URL(BASEPATH, req.nextUrl.origin));
    }
    return NextResponse.next();
  }

  // 未認証の場合はログインページへ
  if (!token) {
    const loginUrl = new URL(BASEPATH + "/login", req.nextUrl.origin);
    loginUrl.searchParams.set(
      "callbackUrl",
      BASEPATH + pathname + req.nextUrl.search
    );
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Next.js 16 の proxy.ts は basePath 込みのパスを受け取るため
  // kiseki/_next, kiseki/api/auth なども除外パターンに含める
  matcher: [
    "/((?!_next/|kiseki/_next/|images/|kiseki/images/|favicon\\.ico|manifest\\.json|icon-|api/auth|kiseki/api/auth).*)",
  ],
};
