"use server";

import { auth } from "@/auth";

/**
 * POG ドラフトの server action。
 *
 * 🔴 **誰が誰として指名したかを確かめているのはここだけ。**
 * バックエンド（`api/pog_draft_router.py`）は `X-API-Key` しか見ないので、
 * この層を飛ばす経路を作ってはいけない。
 *
 *     自分の指名 … `user_id` がセッションの利用者と一致すること
 *     代理入力 …… 呼び出し側が `role === "admin"` であること
 *     管理操作 …… 同上
 *
 * 🔴 **DB のユーザー ID は `session.user.db_id`。`user.id` ではない。**
 * `user.id` は Auth.js 自身が振る ID（Google の sub 由来）で、`keiba.users.id`
 * とは無関係。取り違えると `Number(...)` が NaN になり、**全員が
 * 「ログインが必要です」で弾かれる**（2026-09-10 にダミーの 2099 年度で
 * 実際に踏んだ。画面はログインへリダイレクトされるだけで理由が出ない）。
 */
const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export type DraftPick = {
  user_id: number;
  owner_name: string | null;
  draft_order: number;
  pick_order: number | null;
  visible: boolean;
  netkeiba_horse_id: string | null;
  horse_name: string | null;
  sex: string | null;
  sire: string | null;
  broodmare: string | null;
  stable: string | null;
};

export type DraftBoard = {
  year: number;
  members: { user_id: number; name: string | null }[];
  picks: DraftPick[];
  max_draft_order: number;
};

export type DraftRoll = {
  user_id: number;
  die1: number;
  die2: number;
  die3: number;
  sum: number;
};

type Who = { userId: number; isAdmin: boolean };

/** ログイン中の利用者。未ログインなら null。 */
async function who(): Promise<Who | null> {
  const session = await auth();
  const userId = session?.user?.db_id;
  if (typeof userId !== "number") return null;
  return { userId, isAdmin: session?.user?.role === "admin" };
}

async function call<T>(path: string, init?: RequestInit): Promise<{ data?: T; error?: string }> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY, ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    return { error: typeof detail === "string" ? detail : `失敗しました (${res.status})` };
  }
  return { data: body as T };
}

/** 盤面を読む。伏せ札の出し分けはバックエンドが担う。 */
export async function getDraftBoard(
  year: number,
): Promise<{ data?: DraftBoard; error?: string }> {
  const me = await who();
  if (!me) return { error: "ログインが必要です" };
  return call<DraftBoard>(
    `/pog/draft?year=${year}&viewer_user_id=${me.userId}&is_admin=${me.isAdmin}`,
  );
}

/** その巡のサイコロ結果。 */
export async function getDraftRolls(
  year: number,
  draftOrder: number,
): Promise<{ data?: DraftRoll[]; error?: string }> {
  const me = await who();
  if (!me) return { error: "ログインが必要です" };
  return call<DraftRoll[]>(`/pog/draft/rolls?year=${year}&draft_order=${draftOrder}`);
}

/**
 * 指名する。**自分ぶんか、管理者による代理入力のみ**。
 */
export async function savePick(
  year: number,
  userId: number,
  draftOrder: number,
  netkeibaHorseId: string,
): Promise<{ data?: DraftPick; error?: string }> {
  const me = await who();
  if (!me) return { error: "ログインが必要です" };
  if (userId !== me.userId && !me.isAdmin) {
    return { error: "他の人の指名はできません" };
  }
  return call<DraftPick>(`/pog/draft/picks?year=${year}`, {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      draft_order: draftOrder,
      netkeiba_horse_id: netkeibaHorseId,
    }),
  });
}

/** 指名を取り消す。**自分ぶんか、管理者のみ**。 */
export async function deletePick(
  year: number,
  userId: number,
  draftOrder: number,
): Promise<{ error?: string }> {
  const me = await who();
  if (!me) return { error: "ログインが必要です" };
  if (userId !== me.userId && !me.isAdmin) {
    return { error: "他の人の指名は取り消せません" };
  }
  const r = await call(
    `/pog/draft/picks?year=${year}&user_id=${userId}&draft_order=${draftOrder}`,
    { method: "DELETE" },
  );
  return { error: r.error };
}

/** サイコロを振った結果を残す。**自分ぶんか、管理者のみ**。 */
export async function saveRoll(
  year: number,
  userId: number,
  draftOrder: number,
  dice: [number, number, number],
): Promise<{ data?: DraftRoll; error?: string }> {
  const me = await who();
  if (!me) return { error: "ログインが必要です" };
  if (userId !== me.userId && !me.isAdmin) {
    return { error: "他の人の代わりには振れません" };
  }
  return call<DraftRoll>(`/pog/draft/rolls?year=${year}`, {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      draft_order: draftOrder,
      die1: dice[0],
      die2: dice[1],
      die3: dice[2],
    }),
  });
}

/** その巡を一斉公開／非公開にする（管理者のみ）。 */
export async function setVisible(
  year: number,
  draftOrder: number,
  visible: boolean,
): Promise<{ error?: string }> {
  const me = await who();
  if (!me?.isAdmin) return { error: "管理者のみ操作できます" };
  const r = await call(`/pog/draft/visible?year=${year}`, {
    method: "PUT",
    body: JSON.stringify({ draft_order: draftOrder, visible }),
  });
  return { error: r.error };
}

/**
 * その巡を確定する（管理者のみ）。
 *
 * 🔴 **送った人だけが更新される。** 全員ぶんを渡すこと。
 * 勝った人に枠番、同じ馬で負けた人に 0 を入れる。
 */
export async function setOrder(
  year: number,
  draftOrder: number,
  targets: { user_id: number; pick_order: number }[],
): Promise<{ error?: string }> {
  const me = await who();
  if (!me?.isAdmin) return { error: "管理者のみ操作できます" };
  const r = await call(`/pog/draft/order?year=${year}`, {
    method: "PUT",
    body: JSON.stringify({ draft_order: draftOrder, targets }),
  });
  return { error: r.error };
}

/** 未入力の人を不参加にする／戻す（管理者のみ）。 */
export async function setSkip(
  year: number,
  draftOrder: number,
  userId: number,
  skip: boolean,
): Promise<{ error?: string }> {
  const me = await who();
  if (!me?.isAdmin) return { error: "管理者のみ操作できます" };
  const r = await call(`/pog/draft/skip?year=${year}`, {
    method: "PUT",
    body: JSON.stringify({ draft_order: draftOrder, user_id: userId, skip }),
  });
  return { error: r.error };
}

/** 候補馬を検索する（ドラフトの指名欄から使う）。 */
export async function searchDraftHorses(
  birthYear: number,
  q: { name?: string; sire?: string; broodmare?: string },
  page = 1,
): Promise<{ data?: { items: Record<string, string>[]; total: number }; error?: string }> {
  const me = await who();
  if (!me) return { error: "ログインが必要です" };
  const p = new URLSearchParams({
    birth_year: String(birthYear),
    name: q.name ?? "",
    sire: q.sire ?? "",
    broodmare: q.broodmare ?? "",
    page: String(page),
  });
  return call(`/pog/horse-search?${p}`);
}
