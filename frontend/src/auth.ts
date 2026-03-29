import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import Credentials from "next-auth/providers/credentials";

export const { handlers, auth, signIn, signOut } = NextAuth({
  basePath: "/kiseki/api/auth",
  providers: [
    Google,
    Credentials({
      name: "合言葉",
      credentials: {
        password: { label: "合言葉", type: "password", placeholder: "合言葉を入力" },
      },
      authorize(credentials) {
        const password = credentials?.password;
        if (
          typeof password === "string" &&
          password === process.env.AUTH_PASSWORD
        ) {
          return { id: "guest", name: "ゲスト" };
        }
        return null;
      },
    }),
  ],
  pages: {
    signIn: "/login",
  },
  session: {
    strategy: "jwt",
  },
});
