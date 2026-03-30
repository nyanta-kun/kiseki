import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { cookies } from "next/headers";
import { jwtVerify } from "jose";

// 本番環境（galloplab.com）ではコールバックが api.galloplab.com サブドメインに来るため、
// 認証フロー中に galloplab.com でセットされたクッキーを api.galloplab.com でも読めるよう
// domain=".galloplab.com" を設定する。開発環境では undefined（デフォルト）。
const cookieDomain =
  process.env.NODE_ENV === "production" ? ".galloplab.com" : undefined;

const cookieOptions = {
  options: {
    httpOnly: true,
    sameSite: "lax" as const,
    path: "/",
    secure: process.env.NODE_ENV === "production",
    ...(cookieDomain ? { domain: cookieDomain } : {}),
  },
};

export const { handlers, auth, signIn, signOut } = NextAuth({
  // AUTH_URL に /auth まで含めることでコールバックURLを正確に生成する
  // 例: AUTH_URL=https://api.galloplab.com/auth
  //   → redirect_uri = https://api.galloplab.com/auth/callback/google
  // trustHost: nginx/Docker プロキシ経由のリクエストを許可するために必要
  trustHost: true,
  providers: [Google],
  pages: {
    signIn: "/login",
  },
  session: {
    strategy: "jwt",
  },
  cookies: {
    sessionToken: cookieOptions,
    callbackUrl: cookieOptions,
    csrfToken: cookieOptions,
    pkceCodeVerifier: cookieOptions,
    state: cookieOptions,
    nonce: cookieOptions,
  },
  callbacks: {
    async signIn({ account }) {
      // Google認証完了時に合言葉検証済みクッキーを確認
      if (account?.provider === "google") {
        const cookieStore = await cookies();
        const token = cookieStore.get("pw_verified")?.value;

        if (!token) return false;

        try {
          const secret = new TextEncoder().encode(process.env.AUTH_SECRET);
          await jwtVerify(token, secret);
          // 使用済みクッキーを削除
          cookieStore.delete("pw_verified");
          return true;
        } catch {
          return false;
        }
      }
      return false;
    },
  },
});
