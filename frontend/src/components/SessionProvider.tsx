"use client";

import { SessionProvider as NextAuthSessionProvider } from "next-auth/react";

export default function SessionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  // basePath: Auth.js ハンドラーのパスを明示指定
  // 本番環境では api.galloplab.com/auth/ に配置されているが、
  // クライアントからは同一オリジン(galloplab.com)の /auth/ として参照する
  return (
    <NextAuthSessionProvider basePath="/auth">
      {children}
    </NextAuthSessionProvider>
  );
}
