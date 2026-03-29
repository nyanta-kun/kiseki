"use server";

import { SignJWT } from "jose";
import { cookies } from "next/headers";
import { signIn } from "@/auth";

export async function verifyPasswordAndRedirect(
  callbackUrl: string,
  formData: FormData
): Promise<string | null> {
  const password = formData.get("password") as string;

  if (password !== process.env.AUTH_PASSWORD) {
    return "合言葉が違います。";
  }

  // 合言葉検証済みフラグを署名付きクッキーにセット（10分有効）
  const secret = new TextEncoder().encode(process.env.AUTH_SECRET);
  const token = await new SignJWT({ pw_verified: true })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("10m")
    .sign(secret);

  const cookieStore = await cookies();
  cookieStore.set("pw_verified", token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 600,
    path: "/",
  });

  // Google認証へリダイレクト（redirectTo はbasePath込みの絶対パスまたはルート相対パス）
  // callbackUrl がbasePath除きのパス（例: /races/123）の場合でも正しく処理される
  await signIn("google", { redirectTo: callbackUrl || "/kiseki" });

  return null;
}
