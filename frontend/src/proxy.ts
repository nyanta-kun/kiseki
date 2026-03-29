import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { jwtVerify } from "jose";

const AUTH_COOKIE_NAMES = [
  "__Secure-authjs.session-token",
  "authjs.session-token",
];

async function verifySession(token: string): Promise<boolean> {
  const secret = process.env.AUTH_SECRET;
  if (!secret) return false;
  try {
    const key = new TextEncoder().encode(secret);
    await jwtVerify(token, key);
    return true;
  } catch {
    return false;
  }
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // セッショントークンをクッキーから取得
  let sessionToken: string | undefined;
  for (const name of AUTH_COOKIE_NAMES) {
    const cookie = request.cookies.get(name);
    if (cookie?.value) {
      sessionToken = cookie.value;
      break;
    }
  }

  const isLoggedIn = sessionToken ? await verifySession(sessionToken) : false;

  // ログインページは認証不要
  if (pathname === "/login") {
    if (isLoggedIn) {
      return NextResponse.redirect(
        new URL("/kiseki", request.nextUrl.origin)
      );
    }
    return NextResponse.next();
  }

  // 未認証の場合はログインページへ
  if (!isLoggedIn) {
    const loginUrl = new URL("/kiseki/login", request.nextUrl.origin);
    loginUrl.searchParams.set(
      "callbackUrl",
      request.nextUrl.pathname + request.nextUrl.search
    );
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon\\.ico|manifest\\.json|icon-.*\\.png|api/auth).*)",
  ],
};
