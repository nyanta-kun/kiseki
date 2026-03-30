/**
 * kiseki バックエンドAPIクライアント
 */

// SSR（サーバーサイド）はBACKEND_URLを優先（Docker内部URL）。
// ブラウザはNEXT_PUBLIC_API_URLを使用（外部からアクセス可能なURL）。
const BASE_URL =
  typeof window === "undefined"
    ? (process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

export type Race = {
  id: number;
  date: string;
  course_name: string;
  race_number: number;
  race_name: string | null;
  surface: string;
  distance: number;
  grade: string | null;
  condition: string | null;
  weather: string | null;
  head_count: number | null;
  post_time: string | null;  // 発走時刻 hhmm形式
  race_class_label: string | null;  // 条件戦クラスラベル（例: "3歳未勝利", "4歳以上2勝クラス"）
  has_indices: boolean;
  has_anagusa: boolean;
  confidence_score: number | null;
  confidence_label: "HIGH" | "MID" | "LOW" | null;
};

export type RaceResult = {
  horse_number: number | null;
  finish_position: number | null;
  finish_time: number | null;
  last_3f: number | null;
  horse_name: string;
};

export type HorseIndex = {
  horse_id: number;
  horse_number: number;
  horse_name: string;
  composite_index: number;
  win_probability: number | null;
  place_probability: number | null;
  speed_index: number | null;
  last3f_index: number | null;
  course_aptitude: number | null;
  position_advantage: number | null;
  jockey_index: number | null;
  pace_index: number | null;
  rotation_index: number | null;
  pedigree_index: number | null;
  training_index: number | null;
  anagusa_index: number | null;
  paddock_index: number | null;
  anagusa_rank: string | null;  // "A" | "B" | "C" | null（ピックなし）
  upside_score: number | null;  // 穴馬スコア 0〜1（指数下位でも馬券になりやすい度合い）
};

export type OddsData = {
  win: Record<string, number>;   // horse_number (str) → 倍率
  place: Record<string, number>; // horse_number (str) → 倍率
};

export type RaceConfidence = {
  score: number;
  label: "HIGH" | "MID" | "LOW";
  gap_1_2: number;
  gap_1_3: number;
  head_count: number;
};

export type IndicesResponse = {
  horses: HorseIndex[];
  confidence: RaceConfidence;
};

export type RaceHistoryEntry = {
  date: string;
  course_name: string;
  surface: string;
  distance: number;
  race_name: string | null;
  finish_position: number | null;
  finish_time: number | null;
  last_3f: number | null;
  horse_number: number | null;
  win_odds: number | null;
  win_popularity: number | null;
  composite_index: number | null;
  remarks: string | null;
};

// ---------------------------------------------------------------------------
// API関数
// ---------------------------------------------------------------------------

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status} ${path}`);
  return res.json() as Promise<T>;
}

export async function fetchRace(raceId: number): Promise<Race> {
  return get<Race>(`/api/races/${raceId}`);
}

export async function fetchRacesByDate(date: string): Promise<Race[]> {
  return get<Race[]>(`/api/races?date=${date}`);
}

export async function fetchIndices(raceId: number): Promise<IndicesResponse> {
  return get<IndicesResponse>(`/api/races/${raceId}/indices`);
}

export async function fetchResults(raceId: number): Promise<RaceResult[]> {
  return get<RaceResult[]>(`/api/races/${raceId}/results`);
}

export async function fetchHorseHistory(horseId: number): Promise<RaceHistoryEntry[]> {
  return get<RaceHistoryEntry[]>(`/api/horses/${horseId}/history`);
}

export async function fetchOdds(raceId: number): Promise<OddsData> {
  return get<OddsData>(`/api/races/${raceId}/odds`);
}

export async function fetchNearestDate(
  fromDate: string,
  direction: "prev" | "next"
): Promise<{ date: string }> {
  return get<{ date: string }>(`/api/races/nearest-date?from=${fromDate}&direction=${direction}`);
}

/** WebSocket URLを組み立てる（ブラウザ専用）。
 *  NEXT_PUBLIC_WS_URL が設定されていればそれを使用（ローカル開発用）。
 *  未設定時は window.location から導出（本番環境 nginx プロキシ経由）。
 */
export function buildOddsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) {
    return `${explicit}/api/races/${raceId}/odds/ws`;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}/api/races/${raceId}/odds/ws`;
}

export function buildResultsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) {
    return `${explicit}/api/races/${raceId}/results/ws`;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}/api/races/${raceId}/results/ws`;
}
