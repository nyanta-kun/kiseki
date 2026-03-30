"use client";

import { SessionProvider as NextAuthSessionProvider } from "next-auth/react";

export default function SessionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  // basePath: Next.js basePath(/kiseki) + Auth.jsパス(/api/auth) を明示指定
  // 未指定だと /api/auth/session を呼び出し404になる
  return (
    <NextAuthSessionProvider basePath="/api/auth">
      {children}
    </NextAuthSessionProvider>
  );
}
