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

export type GradedRace = {
  race_id: number;
  /** `jra` / `chihou`。詳細ページのリンク先が分かれる。 */
  kind: "jra" | "chihou";
  /** `YYYYMMDD`。 */
  date: string;
  course_name: string | null;
  race_number: number;
  race_name: string | null;
  /** `HHMM`。出馬表が届く前は null。 */
  post_time: string | null;
  grade: string | null;
  winner_name: string | null;
};

/**
 * 今週の重賞（直前の土曜〜翌週日曜）。
 *
 * 移設元は POG 詳細ページの中の重賞パネルで、14 日のログで 106 回叩かれていた。
 * 🔴 あちらは**地方の優勝馬を凍結した `sekito.entries` から引いており**、
 * 2026-06〜09 の地方重賞 10 件すべてが空欄だった（kiseki は 11/11 取得）。
 */
export function fetchGradedRaces(start: string, end: string): Promise<GradedRace[]> {
  // 開催中は優勝馬が入れ替わる。今週の出走と同じ間隔にする。
  return get<GradedRace[]>(`/races/graded?start=${start}&end=${end}`, {
    next: { revalidate: 60 },
  });
}

/**
 * 「今週」の範囲を直前の土曜〜翌週日曜で返す（`YYYYMMDD`）。
 *
 * 移設元 `GradedRacesTable.getWeekRange()` と同じ式。**変えると重賞パネルに
 * 出る範囲が変わる**ので、移植時はここを合わせること。
 *
 * ⚠️ `toISOString()` を使わない。あれは UTC に倒すので、日本時間の 00:00〜08:59 に
 * 見ると**前日**の日付になり、土曜の朝だけ範囲が 1 日ずれる。
 */
export function currentWeekRange(now: Date = new Date()): [string, string] {
  const fmt = (d: Date) =>
    `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  const saturday = new Date(now);
  saturday.setDate(now.getDate() - ((now.getDay() + 1) % 7));
  const sunday = new Date(saturday);
  sunday.setDate(saturday.getDate() + 8);
  return [fmt(saturday), fmt(sunday)];
}

export type PogGradedWin = {
  date: string;
  source: "jra" | "chihou";
  race_id: number;
  course_code: string | null;
  course_name: string | null;
  race_no: number;
  race_name: string | null;
  grade: string | null;
  netkeiba_horse_id: string | null;
  horse_name: string | null;
  owner_name: string | null;
  user_id: number;
  pog_year: number;
};

/**
 * POG 指名馬の重賞勝ち（記録室）。`year` を省略すると全年度。
 *
 * 🔴 移設元は凍結した `sekito.entries` 経由で馬 ID を解決していたため、
 * **2026年度の重賞勝ちを 1 件も計上できていなかった**（実測）。
 */
export function fetchPogGradedWins(year?: number): Promise<PogGradedWin[]> {
  const q = year === undefined ? "" : `?year=${year}`;
  return get<PogGradedWin[]>(`/pog/graded-wins${q}`, { next: { revalidate: 300 } });
}

export type PogWins = {
  derby: number;
  g1: number;
  g2: number;
  g3: number;
  nar: number;
  overseas_derby: number;
  overseas_other: number;
};

export type PogScore = {
  rank: number;
  prize_rank: number;
  user_id: number;
  name: string | null;
  total_prize: number;
  basic_points: number;
  rank_prize: number;
  special_prize: number;
  total_points: number;
  win: number;
  place: number;
  show: number;
  out: number;
  horse_count: number;
  horses_raced: number;
  horses_won: number;
  all_raced: boolean;
  all_won: boolean;
  wins: PogWins;
};

/**
 * スコア集計（精算表）。
 *
 * 🔴 **実際の精算に使う数字。** 計算はバックエンドの純関数
 * `services/pog_score.py` に集約してあり、フロントでは足し引きしない。
 */
export function fetchPogScores(year: number): Promise<PogScore[]> {
  return get<PogScore[]>(`/pog/score-summary?year=${year}`, {
    next: { revalidate: 300 },
  });
}

export type PogRankingRow = {
  key: string;
  count: number;
  value: number;
  sub: number | null;
  total: number | null;
  g1: number;
  g2: number;
  g3: number;
};

export type PogRanking = {
  metric: string;
  label: string;
  rows: PogRankingRow[];
};

/**
 * POG のランキング。`year` を省くと通算。
 *
 * 移設元は 9 指標 × 2 スコープ = 18 エンドポイントに分かれていたが、
 * どれも「何かで束ねて数える」だけなので 1 本にまとめてある。
 */
export function fetchPogRanking(
  metric: string,
  year?: number,
  limit = 30,
): Promise<PogRanking> {
  const y = year === undefined ? "" : `&year=${year}`;
  return get<PogRanking>(`/pog/rankings?metric=${metric}${y}&limit=${limit}`, {
    next: { revalidate: 300 },
  });
}

/** 使える指標と表示名。 */
export function fetchPogRankingMetrics(): Promise<Record<string, string>> {
  return get<Record<string, string>>("/pog/ranking-metrics", {
    next: { revalidate: 3600 },
  });
}

export type PogSiblingHorse = {
  year: number;
  netkeiba_horse_id: string;
  horse_name: string;
  sex: string | null;
  sire: string | null;
  stable: string | null;
  owner_name: string | null;
  pick_order: number;
  win: number;
  place: number;
  show: number;
  out: number;
  prize: number;
};

export type PogSiblingGroup = {
  broodmare: string;
  nomination_count: number;
  horses: PogSiblingHorse[];
};

/**
 * 同じ母から複数回指名されている馬。ドラフトの下調べに使う。
 *
 * ⚠️ 2017年度以前の指名馬は戦績が出ない（`keiba.horses` の生年別カバレッジが
 * 2013年産以前で極端に薄いため）。
 */
export function fetchPogSiblings(minNominations = 2): Promise<PogSiblingGroup[]> {
  return get<PogSiblingGroup[]>(
    `/pog/siblings?min_nominations=${minNominations}`,
    { next: { revalidate: 3600 } },
  );
}
