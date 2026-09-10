/**
 * ユーザーごとの表示メニュー（POG / 中央 / 地方 / 競輪）の判定。
 *
 * 🔴 **バックエンド `backend/src/services/menu_access.py` の写し。**
 * 規則を変えるときは両方を直す。`backend/tests/test_menu_access.py` が
 * 対応を固定しており、片方だけ変えると落ちる。
 *
 * 写しを置いている理由は、ナビの描画（サーバコンポーネント）とルートガード
 * （`proxy.ts` = Edge）がどちらもこの判定を要るのに、**Edge から FastAPI を
 * 毎リクエスト叩けない**ため。`proxy.ts` は Auth.js の JWT に載せた保存値から
 * ここで可視性を組み立てる。
 *
 * ## 規則
 *
 * 1. POG が ON なら中央と地方も必ず ON（POG は両者のレースへ直接リンクする）
 * 2. admin はフラグを無視して全部見える
 * 3. 競輪は当面 admin 限定（`KEIRIN_REQUIRES_ADMIN`）
 */

/** 競輪を admin 限定に据え置くか。false にするとフラグが効く。 */
export const KEIRIN_REQUIRES_ADMIN = true;

/** メニューのキー。表示順もこの順。 */
export const MENU_KEYS = ["pog", "jra", "chihou", "keirin"] as const;

export type MenuKey = (typeof MENU_KEYS)[number];

export type MenuFlags = {
  pog: boolean;
  jra: boolean;
  chihou: boolean;
  keirin: boolean;
};

/** 何も分からないとき（未ログイン・取得失敗）の値。全部隠す。 */
export const NO_MENU_ACCESS: MenuFlags = {
  pog: false,
  jra: false,
  chihou: false,
  keirin: false,
};

/** 管理画面の既定（新規ユーザーの初期値）。DB の server_default と揃える。 */
export const DEFAULT_MENU_FLAGS: MenuFlags = {
  pog: false,
  jra: true,
  chihou: true,
  keirin: false,
};

/**
 * 保存する値を規則に合わせて整える。
 *
 * POG が ON なら中央・地方を ON へ引き上げる。管理画面のチェックボックスも
 * これを通してから描くので、POG を入れた瞬間に中央・地方が点く。
 */
export function normalizeMenuFlags(flags: MenuFlags): MenuFlags {
  if (flags.pog) {
    return { pog: true, jra: true, chihou: true, keirin: flags.keirin };
  }
  return flags;
}

/**
 * その人に**実際に見えるもの**を返す。
 *
 * @param role `keiba.users.role`（`admin` / `member`）
 * @param flags DB に入っている保存値
 */
export function resolveMenuAccess(
  role: string | undefined,
  flags: MenuFlags,
): MenuFlags {
  if (role === "admin") {
    return { pog: true, jra: true, chihou: true, keirin: true };
  }
  const n = normalizeMenuFlags(flags);
  if (KEIRIN_REQUIRES_ADMIN) {
    return { pog: n.pog, jra: n.jra, chihou: n.chihou, keirin: false };
  }
  return n;
}

/**
 * ルートのパスがどのメニューに属するか。`null` は誰でも見てよいもの
 * （マイページ・利用規約・お問い合わせなど）。
 *
 * ⚠️ **前方一致の順序に意味がある。** `/chihou/results` は「地方」であって
 * 「中央の実績」ではないので、`/chihou` を `/results` より先に見る。
 */
export function menuOfPath(pathname: string): MenuKey | null {
  if (pathname === "/pog" || pathname.startsWith("/pog/")) return "pog";
  if (pathname === "/chihou" || pathname.startsWith("/chihou/")) return "chihou";
  if (pathname === "/keirin" || pathname.startsWith("/keirin/")) return "keirin";
  // 予想（/yoso）は中央のレースに紐づくので中央に含める。
  for (const prefix of ["/races", "/results", "/yoso"]) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return "jra";
  }
  return null;
}

/**
 * ログイン直後・締め出し時に送る先。
 *
 * 🔴 **`/races` 決め打ちにしてはいけない。** 中央が OFF の人を `/races` へ
 * 送ると、ガードがまた別の場所へ送り返してリダイレクトが循環する。
 * 見えるものが 1 つも無ければマイページへ送る（そこは常に見える）。
 */
export function landingPath(access: MenuFlags, pogYear?: number | null): string {
  if (access.jra) return "/races";
  if (access.chihou) return "/chihou/races";
  if (access.pog) return pogYear ? `/pog/${pogYear}` : "/pog";
  if (access.keirin) return "/keirin";
  return "/my";
}
