import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { cookies } from "next/headers";
import { jwtVerify } from "jose";

export const { handlers, auth, signIn, signOut } = NextAuth({
  // AUTH_URL に /api/auth まで含めることで正確なコールバックURLを生成する
  // AUTH_URL=https://sekito-stable.com/kiseki/api/auth → callback=/kiseki/api/auth/callback/google
  // trustHost を使うと内部コンテナURL(localhost:3000)が誤検知されるため使用しない
  providers: [Google],
  pages: {
    signIn: "/login",
  },
  session: {
    strategy: "jwt",
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
