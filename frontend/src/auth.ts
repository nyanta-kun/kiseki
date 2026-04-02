import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export const { handlers, auth, signIn, signOut } = NextAuth({
  // trustHost: nginx/Docker プロキシ経由のリクエストを許可するために必要
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
      // Google 認証のみ許可
      return account?.provider === "google";
    },

    async jwt({ token, account, profile }) {
      // 初回サインイン時（account が存在する）にDBへ upsert してロール等をトークンに格納
      if (account?.provider === "google" && profile) {
        try {
          const res = await fetch(`${BACKEND_URL}/users/upsert`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-API-Key": process.env.INTERNAL_API_KEY ?? "",
            },
            body: JSON.stringify({
              google_sub: profile.sub,
              email: profile.email,
              name: profile.name,
              image_url: profile.picture,
            }),
          });
          if (res.ok) {
            const user = await res.json() as {
              id: number;
              role: string;
              is_active: boolean;
            };
            token.db_id = user.id;
            token.role = user.role;
            token.is_active = user.is_active;
          } else {
            // upsert 失敗時は安全側に倒して無効扱い
            token.is_active = false;
          }
        } catch {
          token.is_active = false;
        }
      }
      return token;
    },

    async session({ session, token }) {
      session.user.role = token.role;
      session.user.is_active = token.is_active;
      return session;
    },
  },
});
