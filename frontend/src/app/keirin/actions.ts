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

/**
 * 入稿案の承認・取消・承認制の切替（2026-08-11 新設）。
 *
 * 承認は netkeirin への POST を伴い **同期で** 走る（keirin 側 webhook が
 * `subprocess.run(timeout=180)`）。確認画面は承認の成否をその場で出す必要があるため
 * 背景起動にしていない。よってここも待ち時間が長くなりうる。
 *
 * 🔴 承認しても **買い目は再計算されない**。keirin 側が入稿案の時点で保存した
 *    買い目をそのまま送る。画面で見たものと違うものが入稿されては確認の意味がない。
 */
export type ApprovalResult = Result & {
  n_ok?: number;
  n_ng?: number;
  results?: { race_key: string; rank_key: string; ok: boolean; message: string }[];
};

async function postApproval(path: string, body: unknown): Promise<ApprovalResult> {
  const denied = await requireAdmin();
  if (denied) return denied;
  try {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const json = (await res.json().catch(() => ({}))) as ApprovalResult & { detail?: string };
    if (!res.ok) {
      return { ok: false, message: json.message ?? json.detail ?? `失敗しました (${res.status})` };
    }
    return { ...json, ok: json.ok ?? true, message: json.message ?? "実行しました" };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "通信に失敗しました" };
  }
}

/** レース単位で承認して netkeirin へ入稿する。 */
export async function approveKeirinRaceAction(
  raceKey: string,
  rankKey: string,
): Promise<ApprovalResult> {
  return postApproval("/keirin/approve", { race_key: raceKey, rank_key: rankKey });
}

/** 場単位でまとめて承認する（その日のその場の入稿案すべて）。 */
export async function approveKeirinVenueAction(
  date: string,
  venueName: string,
): Promise<ApprovalResult> {
  return postApproval("/keirin/approve", { date, venue_name: venueName });
}

/**
 * その日の入稿案を**全場まとめて**承認して netkeirin へ入稿する。
 *
 * 🔴 対象は `proposed`（まだ送っていない入稿案）だけ。既に送った `submitted` は
 *    含めない（含めると二重入稿になる）。
 * 🔴 date は必須（API・CLI の両方でも日付無しは弾く）。過去分まで巻き込まないため。
 * ⚠️ 締切（発走15分前）を過ぎたレースは CLI 側で落として理由が明細に載る。
 *    一括の関門はそこだけなので、成否は必ず明細で確認すること。
 */
export async function approveKeirinAllAction(date: string): Promise<ApprovalResult> {
  return postApproval("/keirin/approve", { date, all_venues: true });
}

/**
 * 入稿データを**公開する**（レース単位）。
 *
 * 🔴 **入稿前（proposed）なら入稿の上で公開する**（2026-08-16・ユーザー指定の
 *    ボタン整理）。画面の操作を 入稿 / 取消 / 公開 の3つに畳むための仕様で、
 *    入稿済かどうかの判断は keirin 側 CLI が持つ。
 * 🔴 **公開は不可逆**。netkeirin の文言「公開後は修正できなくなります」。
 */
export async function publishKeirinRaceAction(
  raceKey: string,
  rankKey: string,
): Promise<ApprovalResult> {
  return postApproval("/keirin/publish", { race_key: raceKey, rank_key: rankKey });
}

/** その日を**全件公開**する（未入稿は入稿の上で公開）。 */
export async function publishKeirinAllAction(date: string): Promise<ApprovalResult> {
  return postApproval("/keirin/publish", { date, all_venues: true });
}

/**
 * 入稿を取り消す。netkeirin の下書きを削除し、記録は論理削除する。
 *
 * ⚠️ netkeirin 側の削除が効くのは**公開待ち**のもの。公開済みに効くかは未確認。
 *
 * `force` は **netkeirin を触らず記録だけ取消にする**最後の手段。
 * netkeirin 側で先に下書きを消していると item_id が引けず、従来はそこで止まって
 * DB も更新されないままだった（取消したはずの行が残り、自動穴埋めでも出し直せない）。
 */
export async function cancelKeirinSubmissionAction(
  raceKey: string,
  rankKey: string,
  force = false,
): Promise<ApprovalResult> {
  return postApproval("/keirin/cancel", { race_key: raceKey, rank_key: rankKey, force });
}

/**
 * 場単位でまとめて取り消す（その日のその場の生きている下書きすべて）。
 *
 * 🔴 **元は用意していなかった**（まとめて消す事故を避けるため API 側も拒否していた）。
 *    2026-08-12 にユーザー要望で追加。事故防止は**呼び出し側の二段確認と件数表示**が担う。
 * ⚠️ 一括では force を使わない。netkeirin 側に無いものは失敗として明細で返るので、
 *    1件ずつ強制取消すること（まとめて記録だけ消す事故を避ける）。
 */
export async function cancelKeirinVenueAction(
  date: string,
  venueName: string,
): Promise<ApprovalResult> {
  return postApproval("/keirin/cancel", { date, venue_name: venueName });
}

/**
 * その日の下書きを**全件**取り消す。
 *
 * 🔴 **最も戻しにくい操作。** 呼び出す前に必ず件数を見せて二段で確認すること。
 * 🔴 date は必須（API・CLI の両方でも日付無しは弾く）。過去分まで巻き込まないため。
 */
export async function cancelKeirinAllAction(date: string): Promise<ApprovalResult> {
  return postApproval("/keirin/cancel", { date, all_venues: true });
}

/**
 * 承認制の ON/OFF。
 *
 * 承認制は一時運用の想定なので画面から自動入稿へ戻せるようにしてある。
 * ⚠️ ON にすると承認するまで netkeirin へ何も出ない。
 */
export async function setKeirinApprovalModeAction(requireApproval: boolean): Promise<Result> {
  const denied = await requireAdmin();
  if (denied) return denied;
  try {
    const res = await fetch(`${BACKEND_URL}/keirin/approval-mode`, {
      method: "PUT",
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify({ require_approval: requireApproval }),
      cache: "no-store",
    });
    const json = (await res.json().catch(() => ({}))) as Partial<Result> & { detail?: string };
    if (!res.ok) {
      return { ok: false, message: json.message ?? json.detail ?? `失敗しました (${res.status})` };
    }
    return { ok: true, message: requireApproval ? "承認制にしました" : "自動入稿に戻しました" };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "通信に失敗しました" };
  }
}
