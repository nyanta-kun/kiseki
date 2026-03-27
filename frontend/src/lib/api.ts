/**
 * kiseki バックエンドAPIクライアント
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  has_indices: boolean;
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
};

export type OddsEntry = {
  combination: string;
  odds: number;
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
