import { auth } from "@/auth";
import { NextResponse } from "next/server";
import type { NextAuthRequest } from "next-auth";

const BASEPATH = "/kiseki";

export const proxy = auth((req: NextAuthRequest) => {
  // 開発時バイパス: frontend/.env.local に AUTH_BYPASS_DEV=true を追加すると認証スキップ
  if (process.env.AUTH_BYPASS_DEV === "true") {
    return NextResponse.next();
  }

  const { nextUrl } = req;
  const session = req.auth;

  // auth()ラッパーがreqWithEnvURLでNextRequestを再構築するため
  // nextUrl.pathnameがbasePath込みの場合がある（例: /kiseki/login）
  // → basePath重複を防ぐため手動で除去する
  const rawPathname = nextUrl.pathname;
  const pathname = rawPathname.startsWith(BASEPATH)
    ? rawPathname.slice(BASEPATH.length) || "/"
    : rawPathname;

  // ログインページは認証不要
  if (pathname === "/login") {
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
      BASEPATH + pathname + nextUrl.search
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
