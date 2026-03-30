import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

export default async function proxy(req: NextRequest): Promise<NextResponse> {
  // 開発時バイパス
  if (process.env.AUTH_BYPASS_DEV === "true") {
    return NextResponse.next();
  }

  const pathname = req.nextUrl.pathname;

  // _next 静的アセット・Auth API ルートは素通し
  if (
    pathname.startsWith("/_next/") ||
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/auth/") ||
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
      return NextResponse.redirect(new URL("/races", req.nextUrl.origin));
    }
    return NextResponse.next();
  }

  // 未認証の場合はログインページへ
  if (!token) {
    const loginUrl = new URL("/login", req.nextUrl.origin);
    loginUrl.searchParams.set("callbackUrl", pathname + req.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/|images/|favicon\\.ico|manifest\\.json|icon-|api/auth|auth/).*)",
  ],
};
