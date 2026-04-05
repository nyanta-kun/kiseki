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

export async function createInvitationCode(formData: FormData): Promise<{ error?: string }> {
  const grantType = formData.get("grant_type") as string;
  const weeksCount = formData.get("weeks_count") ? Number(formData.get("weeks_count")) : null;
  const targetDate = formData.get("target_date") as string | null;
  const maxUses = Number(formData.get("max_uses") ?? 1);
  const note = (formData.get("note") as string | null) || null;

  const body: Record<string, unknown> = { grant_type: grantType, max_uses: maxUses, note };
  if (grantType === "weeks") body.weeks_count = weeksCount;
  if (grantType === "date") body.target_date = targetDate;

  const res = await fetch(`${BACKEND_URL}/admin/invitation-codes`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const resp = await res.json().catch(() => ({}));
    return { error: (resp as { detail?: string }).detail ?? "作成に失敗しました" };
  }

  revalidatePath("/admin");
  return {};
}

export async function toggleInvitationCode(
  codeId: number,
  isActive: boolean
): Promise<{ error?: string }> {
  const res = await fetch(`${BACKEND_URL}/admin/invitation-codes/${codeId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ is_active: isActive }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { error: (body as { detail?: string }).detail ?? "更新に失敗しました" };
  }

  revalidatePath("/admin");
  return {};
}

export async function triggerFetchData(yearMonth: string): Promise<{ error?: string }> {
  const res = await fetch(`${BACKEND_URL}/admin/fetch-data`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ year_month: yearMonth }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { error: (body as { detail?: string }).detail ?? "取得指示に失敗しました" };
  }
  return {};
}

export async function updatePaidMode(enabled: boolean): Promise<{ error?: string }> {
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ key: "PAID_MODE", value: enabled ? "true" : "false" }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { error: (body as { detail?: string }).detail ?? "更新に失敗しました" };
  }
  return {};
}

export async function grantUserAccess(
  userId: number,
  formData: FormData
): Promise<{ error?: string }> {
  const grantType = formData.get("grant_type") as string;
  const weeksCount = formData.get("weeks_count") ? Number(formData.get("weeks_count")) : null;
  const targetDate = formData.get("target_date") as string | null;
  const note = (formData.get("note") as string | null) || null;

  const body: Record<string, unknown> = { grant_type: grantType, note };
  if (grantType === "weeks") body.weeks_count = weeksCount;
  if (grantType === "date") body.target_date = targetDate;

  const res = await fetch(`${BACKEND_URL}/admin/users/${userId}/grant-access`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const resp = await res.json().catch(() => ({}));
    return { error: (resp as { detail?: string }).detail ?? "付与に失敗しました" };
  }

  revalidatePath("/admin");
  return {};
}
