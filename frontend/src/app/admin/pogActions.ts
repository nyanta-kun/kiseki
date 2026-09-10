"use server";

import { revalidatePath } from "next/cache";

import { auth } from "@/auth";

/**
 * POG グループ管理の server action。
 *
 * 🔴 **ここで role を毎回確かめる。** 同じディレクトリの `actions.ts` は
 * 「/admin ページ自体が gate されている」前提で role を見ていないが、
 * グループ削除は**指名が道連れで消える**戻せない操作なので、
 * action 単体でも成立させる（呼び出し元が増えたときに守られる）。
 */
const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export type PogMember = {
  user_id: number;
  nickname: string | null;
  name: string | null;
  email: string | null;
  pick_count: number;
};

export type PogGroupDetail = {
  id: number;
  year: number;
  name: string | null;
  members: PogMember[];
  pick_count: number;
};

async function assertAdmin(): Promise<string | null> {
  const session = await auth();
  return session?.user?.role === "admin" ? null : "権限がありません";
}

async function call<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data?: T; error?: string }> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    return { error: (body as { detail?: string }).detail ?? `失敗しました (${res.status})` };
  }
  return { data: body as T };
}

/** 指定年度のグループとメンバーを読む。 */
export async function getPogGroup(
  year: number,
): Promise<{ data?: PogGroupDetail; error?: string }> {
  const denied = await assertAdmin();
  if (denied) return { error: denied };
  return call<PogGroupDetail>(`/pog/admin/groups/${year}`);
}

/** 年度グループを作る。 */
export async function createPogGroup(
  year: number,
  name: string | null,
  members: { user_id: number; nickname: string | null }[],
): Promise<{ data?: PogGroupDetail; error?: string }> {
  const denied = await assertAdmin();
  if (denied) return { error: denied };
  const r = await call<PogGroupDetail>("/pog/admin/groups", {
    method: "POST",
    body: JSON.stringify({ year, name, members }),
  });
  if (!r.error) revalidatePath("/admin");
  return r;
}

/** メンバーを丸ごと置き換える。**指名は消さない。** */
export async function replacePogMembers(
  year: number,
  members: { user_id: number; nickname: string | null }[],
): Promise<{ data?: PogGroupDetail; error?: string }> {
  const denied = await assertAdmin();
  if (denied) return { error: denied };
  const r = await call<PogGroupDetail>(`/pog/admin/groups/${year}/members`, {
    method: "PUT",
    body: JSON.stringify({ members }),
  });
  if (!r.error) revalidatePath("/admin");
  return r;
}

/**
 * 年度グループを消す。**その年の指名が全部消える。**
 *
 * 🔴 `confirmYear` はバックエンドでも `year` との一致を確認する。
 * 移設元にこの確認は無く、押し間違えたら戻せなかった。
 */
export async function deletePogGroup(
  year: number,
  confirmYear: number,
): Promise<{ data?: { deleted_picks: number; deleted_members: number }; error?: string }> {
  const denied = await assertAdmin();
  if (denied) return { error: denied };
  const r = await call<{ deleted_picks: number; deleted_members: number }>(
    `/pog/admin/groups/${year}/delete`,
    { method: "POST", body: JSON.stringify({ confirm_year: confirmYear }) },
  );
  if (!r.error) revalidatePath("/admin");
  return r;
}
