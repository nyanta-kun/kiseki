import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

// BACKEND_URL は /api まで含む形式が必要。
// NEXT_PUBLIC_API_URL はベース URL のみ（/api なし）の場合があるため正規化する。
const _backendBase = (
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000"
).replace(/\/api\/?$/, "").replace(/\/$/, "");
const BACKEND_URL = `${_backendBase}/api`;

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
              is_premium: boolean;
              can_input_index: boolean;
              access_expires_at: string | null;
            };
            token.db_id = user.id;
            token.role = user.role;
            token.is_active = user.is_active;
            token.is_premium = user.is_premium;
            token.can_input_index = user.can_input_index;
            token.access_expires_at = user.access_expires_at;
          } else {
            // upsert 失敗時は安全側に倒して無効扱い
            token.db_id = undefined;
            token.role = "user";
            token.is_active = false;
            token.is_premium = false;
          }
        } catch {
          token.db_id = undefined;
          token.role = "user";
          token.is_active = false;
          token.is_premium = false;
        }
      }
      return token;
    },

    async session({ session, token }) {
      session.user.db_id = token.db_id;
      session.user.role = token.role;
      session.user.is_active = token.is_active;
      session.user.is_premium = token.is_premium;
      session.user.can_input_index = token.can_input_index;
      session.user.access_expires_at = token.access_expires_at;
      return session;
    },
  },
});
