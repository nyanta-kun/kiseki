import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { cookies } from "next/headers";
import { jwtVerify } from "jose";

export const { handlers, auth, signIn, signOut } = NextAuth({
  // basePath は指定しない（Next.js の basePath /kiseki が自動適用される）
  // Auth.js は /api/auth をデフォルトで使用し、/kiseki/api/auth として動作する
  trustHost: true,
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
