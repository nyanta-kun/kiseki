/**
 * POG（ペーパーオーナーゲーム）の API クライアント。
 *
 * バックエンドは `backend/src/api/pog_router.py`（統合 Phase 5 の 5d-3）。
 *
 * 🔴 **年度で指定する。** 移設元 sekito は `group_id` で指定していたが、
 * keiba へ写した時点で ID が別体系になり（2026 = sekito 27 / keiba 22）、
 * sekito 側は年順ですらなかった（2025=3・2024=6）。年は一意で人が読める。
 */

const _rawBase =
  typeof window === "undefined"
    ? (process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
const BASE_URL = _rawBase.replace(/\/api\/?$/, "").replace(/\/$/, "") + "/api";

export type PogGroup = {
  id: number;
  year: number;
  name: string | null;
};

export type PogOwner = {
  rank: number;
  user_id: number;
  name: string | null;
  win: number;
  place: number;
  show: number;
  out: number;
  prize: number;
  prize_jra: number;
  prize_other: number;
  top_horse: string | null;
};

export type PogOwnerRank = {
  rank: number;
  user_id: number;
};

export type PogHorse = {
  user_id: number;
  owner_name: string | null;
  pick_order: number | null;
  horse_name: string | null;
  netkeiba_horse_id: string | null;
  sex: string | null;
  sire: string | null;
  broodmare: string | null;
  broodmare_sire: string | null;
  stable: string | null;
  win: number;
  place: number;
  show: number;
  out: number;
  prize: number;
};

async function get<T>(path: string, init?: RequestInit & { next?: { revalidate?: number } }) {
  const res = await fetch(`${BASE_URL}${path}`, {
    // 成績は 1 日に何度も変わるものではない。レース確定後に効けばよい。
    next: { revalidate: 300 },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`POG API ${path} が ${res.status} を返しました`);
  }
  return (await res.json()) as T;
}

export function fetchPogGroups(): Promise<PogGroup[]> {
  return get<PogGroup[]>("/pog/groups");
}

export function fetchPogOwners(year: number): Promise<PogOwner[]> {
  return get<PogOwner[]>(`/pog/owners?year=${year}`);
}

/** 指定日時点の順位。現在の順位と比べて変動の矢印を出すのに使う。 */
export function fetchPogOwnersAsOf(year: number, asof: string): Promise<PogOwnerRank[]> {
  return get<PogOwnerRank[]>(`/pog/owners-history?year=${year}&asof=${asof}`);
}

export function fetchPogHorses(year: number, userId?: number): Promise<PogHorse[]> {
  const q = userId ? `&user_id=${userId}` : "";
  return get<PogHorse[]>(`/pog/horses?year=${year}${q}`);
}

/** 賞金は万円。3桁区切りで返す。 */
export function formatPrize(man: number): string {
  return `${man.toLocaleString("ja-JP")}万`;
}

/** 「1-2-0-3」形式の成績。 */
export function formatRecord(h: {
  win: number;
  place: number;
  show: number;
  out: number;
}): string {
  return `${h.win}-${h.place}-${h.show}-${h.out}`;
}

export type PogRecentRace = {
  date: string;
  course_code: string;
  course_name: string | null;
  race_no: number;
  race_name: string | null;
  ground: string | null;
  distance: number | null;
  post_time: string | null;
  horse_no: number | null;
  netkeiba_horse_id: string | null;
  horse_name: string | null;
  jockey: string | null;
  ninki: number | null;
  tan: number | null;
  result: string | null;
  prize: number;
  sex: string | null;
  sire: string | null;
  broodmare: string | null;
  broodmare_sire: string | null;
  owners: string[] | null;
};

/** 指名馬の今週の出走。結果が出ていれば着順も入る。 */
export function fetchPogRecentRaces(year: number): Promise<PogRecentRace[]> {
  // 開催中は着順が入れ替わる。短めにする。
  return get<PogRecentRace[]>(`/pog/recent-races?year=${year}`, {
    next: { revalidate: 60 },
  });
}

export type PogMembership = {
  is_member: boolean;
  latest_year: number | null;
};

/**
 * POG の参加者かどうか。ナビに POG を出すかの判定に使う。
 *
 * 🔴 POG は sekito から引き継いだ9人のもので、GallopLab の利用者全員に
 * 見せるものではない。ロールを増やさず**参加実績そのもの**で判定する。
 */
export function fetchPogMembership(userId: number): Promise<PogMembership> {
  // 参加者は年に一度しか変わらない。長めにキャッシュしてよい。
  return get<PogMembership>(`/pog/membership?user_id=${userId}`, {
    next: { revalidate: 3600 },
  });
}
