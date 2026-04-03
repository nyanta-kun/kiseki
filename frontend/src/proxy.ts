import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

export default async function proxy(req: NextRequest): Promise<NextResponse> {
  const pathname = req.nextUrl.pathname;

  // _next 静的アセット・Auth API ルートは素通し
  if (
    pathname.startsWith("/_next/") ||
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/auth/") ||
    pathname === "/favicon.ico" ||
    pathname === "/manifest.json" ||
    pathname === "/sw.js" ||
    /^\/workbox-.*\.js$/.test(pathname) ||
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

  // トップページ・ログインページは認証不要
  if (pathname === "/" || pathname === "/login") {
    // is_active=false のトークンはログインページに留まらせる（リダイレクトループ防止）
    if (token && token.is_active !== false) {
      return NextResponse.redirect(new URL("/races", req.nextUrl.origin));
    }
    // 未認証の / は /login へ直接リダイレクト（callbackUrl なし）
    if (pathname === "/") {
      return NextResponse.redirect(new URL("/login", req.nextUrl.origin));
    }
    return NextResponse.next();
  }

  // 未認証の場合はログインページへ
  if (!token) {
    const loginUrl = new URL("/login", req.nextUrl.origin);
    loginUrl.searchParams.set("callbackUrl", pathname + req.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }

  // アカウント無効化チェック
  if (token.is_active === false) {
    return NextResponse.redirect(new URL("/login?error=account_suspended", req.nextUrl.origin));
  }

  // 管理画面は admin ロールのみ
  if (pathname.startsWith("/admin")) {
    if (token.role !== "admin") {
      return NextResponse.redirect(new URL("/races", req.nextUrl.origin));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/|images/|favicon\\.ico|manifest\\.json|icon-|api/auth|auth/).*)",
  ],
};
