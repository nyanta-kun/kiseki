"use server";

import { revalidatePath } from "next/cache";
import type { NetkeirinSetting } from "@/lib/api";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function saveNetkeirinSettings(
  settings: NetkeirinSetting[]
): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${BACKEND_URL}/keirin/netkeirin-settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(settings),
  });

  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { message?: string; detail?: string };
    return { ok: false, message: body.message ?? body.detail ?? `保存に失敗しました (${res.status})` };
  }

  revalidatePath("/keirin/settings");
  return { ok: true, message: "保存しました" };
}
