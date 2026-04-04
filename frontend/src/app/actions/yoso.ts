"use server";

import { auth } from "@/auth";
import { revalidatePath } from "next/cache";
import type { DisplaySetting } from "@/lib/api";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_BASE = BACKEND_URL.replace(/\/api\/?$/, "").replace(/\/$/, "") + "/api";

async function getEmail(): Promise<string> {
  const session = await auth();
  if (!session?.user?.email) throw new Error("認証が必要です");
  return session.user.email;
}

// ---------------------------------------------------------------------------
// 印・指数保存
// ---------------------------------------------------------------------------
export async function savePrediction(
  raceId: number,
  horseId: number,
  mark: string | null,
  userIndex: number | null,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const email = await getEmail();
    const res = await fetch(
      `${API_BASE}/yoso/predictions?x_user_email=${encodeURIComponent(email)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ race_id: raceId, horse_id: horseId, mark, user_index: userIndex }),
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { ok: false, error: (data as { detail?: string }).detail ?? "保存に失敗しました" };
    }
    revalidatePath("/yoso");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------------------
// 表示設定一括更新
// ---------------------------------------------------------------------------
export async function updateDisplaySettings(
  settings: Array<{ target_user_id: number; show_mark: boolean; show_index: boolean }>,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const email = await getEmail();
    const res = await fetch(
      `${API_BASE}/yoso/settings/display?x_user_email=${encodeURIComponent(email)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      },
    );
    if (!res.ok) return { ok: false, error: "設定の保存に失敗しました" };
    revalidatePath("/yoso/settings");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------------------
// CSVファイル投入
// ---------------------------------------------------------------------------
export async function importTargetCsv(
  formData: FormData,
): Promise<{ ok: boolean; error?: string; saved?: number; errors?: number }> {
  try {
    const email = await getEmail();
    const res = await fetch(
      `${API_BASE}/yoso/import?x_user_email=${encodeURIComponent(email)}`,
      {
        method: "POST",
        body: formData,
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { ok: false, error: (data as { detail?: string }).detail ?? "投入に失敗しました" };
    }
    const result = await res.json() as { saved_count: number; error_count: number };
    revalidatePath("/yoso/import");
    revalidatePath("/yoso");
    return { ok: true, saved: result.saved_count, errors: result.error_count };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------------------
// データ取得ヘルパー（Server Components 用）
// ---------------------------------------------------------------------------
export async function fetchYosoRaces(date: string) {
  const email = await getEmail();
  const res = await fetch(
    `${API_BASE}/yoso/races/${date}?x_user_email=${encodeURIComponent(email)}`,
    { cache: "no-store" },
  );
  if (!res.ok) return [];
  return res.json();
}

export async function fetchDisplaySettings(): Promise<DisplaySetting[]> {
  const email = await getEmail();
  const res = await fetch(
    `${API_BASE}/yoso/settings/display?x_user_email=${encodeURIComponent(email)}`,
    { cache: "no-store" },
  );
  if (!res.ok) return [];
  return res.json() as Promise<DisplaySetting[]>;
}

export async function fetchImportHistory() {
  const email = await getEmail();
  const res = await fetch(
    `${API_BASE}/yoso/import/history?x_user_email=${encodeURIComponent(email)}`,
    { cache: "no-store" },
  );
  if (!res.ok) return [];
  return res.json();
}

export async function fetchYosoStats(params: {
  from_date?: string;
  to_date?: string;
  course?: string;
  surface?: string;
  dist_min?: number;
  dist_max?: number;
}) {
  const email = await getEmail();
  const qs = new URLSearchParams({ x_user_email: email });
  if (params.from_date) qs.set("from_date", params.from_date);
  if (params.to_date) qs.set("to_date", params.to_date);
  if (params.course) qs.set("course", params.course);
  if (params.surface) qs.set("surface", params.surface);
  if (params.dist_min != null) qs.set("dist_min", String(params.dist_min));
  if (params.dist_max != null) qs.set("dist_max", String(params.dist_max));
  const res = await fetch(`${API_BASE}/yoso/stats?${qs.toString()}`, { cache: "no-store" });
  if (!res.ok) return null;
  return res.json();
}
