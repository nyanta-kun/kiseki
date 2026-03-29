import { auth } from "@/auth";
import { NextResponse } from "next/server";
import type { NextAuthRequest } from "next-auth";

const BASEPATH = "/kiseki";

export const proxy = auth((req: NextAuthRequest) => {
  const { nextUrl } = req;
  const session = req.auth;

  // ログインページは認証不要
  if (nextUrl.pathname === "/login") {
    if (session) {
      return NextResponse.redirect(new URL(BASEPATH, nextUrl.origin));
    }
    return NextResponse.next();
  }

  // 未認証の場合はログインページへ
  if (!session) {
    const loginUrl = new URL(BASEPATH + "/login", nextUrl.origin);
    loginUrl.searchParams.set(
      "callbackUrl",
      BASEPATH + nextUrl.pathname + nextUrl.search
    );
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
});

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon\\.ico|manifest\\.json|icon-.*\\.png|api/auth).*)",
  ],
};
