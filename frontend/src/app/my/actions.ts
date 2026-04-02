"use server";

import { revalidatePath } from "next/cache";
import { auth } from "@/auth";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function redeemCode(
  _prev: string | null,
  formData: FormData
): Promise<string> {
  const code = (formData.get("code") as string | null)?.trim();
  if (!code) return "コードを入力してください";

  const session = await auth();
  const dbId = session?.user?.db_id;
  if (!dbId) return "ログインが必要です";

  const res = await fetch(`${BACKEND_URL}/users/${dbId}/redeem-code`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ code }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string };
    return body.detail ?? "コードの利用に失敗しました";
  }

  revalidatePath("/my");
  // "SUCCESS" を返すことでフォーム側でのフィードバック判定に使用
  return "SUCCESS";
}
