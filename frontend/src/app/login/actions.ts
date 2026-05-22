"use server";

import { SignJWT } from "jose";
import { cookies } from "next/headers";

// パスワード検証のみを行い、Google OAuth の開始はクライアントに委ねる。
// Server Action 内で signIn() を呼ぶと Auth.js beta.30 で MissingCSRF エラーになるため。
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

  // new URL() でパースしてパス部分のみを抽出（オープンリダイレクト防止）
  let safeCallback = "/races";
  if (callbackUrl) {
    try {
      const parsed = new URL(callbackUrl, "http://localhost");
      const path = parsed.pathname + parsed.search + parsed.hash;
      if (path.startsWith("/")) {
        safeCallback = path;
      }
    } catch {
      // パース失敗時はデフォルトにフォールバック
    }
  }

  // "__verified__:{callbackUrl}" を返してクライアントに Google OAuth 開始を指示
  return `__verified__:${safeCallback}`;
}
