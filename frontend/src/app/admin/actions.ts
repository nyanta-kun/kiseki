"use server";

import { revalidatePath } from "next/cache";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function updateUser(
  userId: number,
  patch: { role?: string; is_active?: boolean }
): Promise<{ error?: string }> {
  const res = await fetch(`${BACKEND_URL}/admin/users/${userId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(patch),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { error: (body as { detail?: string }).detail ?? "更新に失敗しました" };
  }

  revalidatePath("/admin");
  return {};
}
