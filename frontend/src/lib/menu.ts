import { cache } from "react";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import {
  DEFAULT_MENU_FLAGS,
  NO_MENU_ACCESS,
  landingPath,
  type MenuFlags,
  type MenuKey,
} from "@/lib/menuAccess";
import { fetchPogMembership } from "@/lib/pog";

/**
 * ログイン中のユーザーに見えるメニューを取り出す。
 *
 * 🔴 **Auth.js の JWT には載せていない。** v5 の `jwt` コールバックは
 * サーバコンポーネントから呼ばれても Cookie を書き戻せないため、そこに
 * 入れると**管理者が切り替えても再ログインまで反映されない**。毎リクエスト
 * バックエンドに聞く（`GET /api/users/{id}/menu`）。
 *
 * 呼び出しは React の `cache()` で 1 リクエスト内に 1 回へ畳む。ルートレイアウト
 * （ナビの描画）と各セクションのガードが同じ値を使うので、実際の HTTP は
 * ページあたり 1 回で済む。
 */

const BACKEND_URL = (
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000"
)
  .replace(/\/api\/?$/, "")
  .replace(/\/$/, "") + "/api";

const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export type MenuContext = {
  /** ログインしているか。 */
  signedIn: boolean;
  isAdmin: boolean;
  /** 実際に見えるメニュー。未ログイン・取得失敗時はすべて false。 */
  access: MenuFlags;
  /** POG のリンク先の年度。参加実績が無ければ null。 */
  pogYear: number | null;
};

/**
 * 🔴 **取れなかったときは「この機能が入る前の見え方」へ落とす**
 * （中央 ON / 地方 ON / POG OFF / 競輪 OFF ＝ `DEFAULT_MENU_FLAGS`）。
 *
 * 全部隠す案と迷ったが、こちらを採った:
 *
 * - **全部隠すと、バックエンドが数秒詰まっただけで全員がマイページへ弾かれる。**
 *   ログインしている人が「サイトが消えた」状態になり、原因も分からない。
 * - 逆に既定値へ落としても、**追加で見えるのは中央と地方だけ**。絞り込みの
 *   主目的である POG と競輪は隠れたままになる。
 * - デプロイ順の保険にもなる。フロントが先に出てバックエンドがまだ
 *   `/users/{id}/menu` を持っていない間（404）、全員が従来どおりに見える
 *   （🔴 全部隠す実装だと、この数分間サイトが誰にも使えなくなる）。
 */
async function fetchAccess(userId: number): Promise<MenuFlags> {
  try {
    const res = await fetch(`${BACKEND_URL}/users/${userId}/menu`, {
      headers: { "X-API-Key": API_KEY },
      // 管理者の切り替えを待たせない。ナビ 1 個ぶんの往復なので毎回引く。
      cache: "no-store",
    });
    if (!res.ok) return DEFAULT_MENU_FLAGS;
    const body = (await res.json()) as { access?: Partial<MenuFlags> };
    // 欠けたキーは false 扱い。`DEFAULT_MENU_FLAGS` で埋めると、バックエンドが
    // 意図して false にしたキーが true に化ける。
    return { ...NO_MENU_ACCESS, ...(body.access ?? {}) };
  } catch {
    return DEFAULT_MENU_FLAGS;
  }
}

/** ログイン中のユーザーのメニュー文脈。1 リクエスト内でキャッシュされる。 */
export const getMenuContext = cache(async (): Promise<MenuContext> => {
  const session = await auth();
  const userId = session?.user?.db_id;
  const isAdmin = session?.user?.role === "admin";
  if (!userId) {
    return { signedIn: false, isAdmin: false, access: NO_MENU_ACCESS, pogYear: null };
  }

  const access = await fetchAccess(userId);
  // POG のリンク先の年度。フラグが立っている人にだけ聞く。
  // 失敗してもページ全体を落とさない（`/pog` が最新年度へ転送する）。
  const pogYear = access.pog
    ? await fetchPogMembership(userId)
        .then((m) => m.latest_year)
        .catch(() => null)
    : null;

  return { signedIn: true, isAdmin, access, pogYear };
});

/**
 * そのセクションが見えなければ、見える場所へ送り返す。
 *
 * 各セクションの `layout.tsx` から呼ぶ。**ここが実効ガード**で、ナビから
 * リンクを消すだけでは URL 直打ちを止められない。
 *
 * ⚠️ 送り先は `landingPath()` が返す「その人に見える場所」。`/races` 決め打ちに
 * すると、中央が OFF の人でガードとリダイレクトが循環する。
 */
export async function requireMenu(key: MenuKey): Promise<MenuContext> {
  const ctx = await getMenuContext();
  if (!ctx.access[key]) {
    redirect(landingPath(ctx.access, ctx.pogYear));
  }
  return ctx;
}
