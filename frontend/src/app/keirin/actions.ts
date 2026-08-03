"use server";

/**
 * 競輪の副作用のある操作（採点再実行・オッズ/結果取得・netkeirin入稿）の Server Action。
 *
 * 【2026-08-03 新設・A案】これらは元々 `lib/api.ts` からブラウザが
 * `api.galloplab.com/api/keirin/*` を **無認証で直接** 叩いていた。URLさえ知って
 * いれば誰でも netkeirin への入稿を実行できる状態だったため、バックエンド側に
 * `ApiKeyDep` を付け、フロントは本ファイルの Server Action 経由に切り替えた
 * （`INTERNAL_API_KEY` はサーバー側にのみ存在しブラウザへ渡らない）。
 *
 * `app/keirin/settings/actions.ts::saveNetkeirinSettings` と同じ方式に揃えてある。
 *
 * さらに、APIキーだけでは「kisekiのログインユーザーであること」を検証できないため、
 * 各アクションの先頭で **admin ロールのセッションを必須**にしている
 * （proxy.ts の /keirin ルートガードと同基準。Server Action は proxy を経由しない
 * 独立したエンドポイントとして呼べるため、ここでも必ず確認する必要がある）。
 */

import { auth } from "@/auth";
import type { ManualKeirinRankKey } from "@/lib/api";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

type Result = { ok: boolean; message: string };

async function requireAdmin(): Promise<Result | null> {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return { ok: false, message: "この操作は管理者のみ実行できます" };
  }
  return null;
}

async function post(path: string, body?: unknown): Promise<Result> {
  const denied = await requireAdmin();
  if (denied) return denied;
  try {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: {
        "X-API-Key": API_KEY,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      cache: "no-store",
    });
    const json = (await res.json().catch(() => ({}))) as Partial<Result> & { detail?: string };
    if (!res.ok) {
      return { ok: false, message: json.message ?? json.detail ?? `失敗しました (${res.status})` };
    }
    return { ok: json.ok ?? true, message: json.message ?? "実行しました" };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "通信に失敗しました" };
  }
}

export async function refreshKeirinPicksAction(date: string): Promise<Result> {
  return post(`/keirin/refresh?date=${encodeURIComponent(date)}`);
}

export async function triggerKeirinFetchOddsAction(): Promise<Result> {
  return post("/keirin/fetch-odds");
}

export async function triggerKeirinFetchResultsAction(): Promise<Result> {
  return post("/keirin/fetch-results");
}

export async function triggerKeirinSubmitRaceAction(
  raceKey: string,
  date: string,
  session: "morning" | "evening",
  manual?: { rankKey: ManualKeirinRankKey; axis1: number; axis2: number },
): Promise<Result> {
  const body: Record<string, unknown> = { race_key: raceKey, date, session };
  if (manual) {
    body.rank_key = manual.rankKey;
    body.axis1 = manual.axis1;
    body.axis2 = manual.axis2;
  }
  return post("/keirin/submit-race", body);
}
