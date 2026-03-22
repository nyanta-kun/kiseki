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
};

export type HorseIndex = {
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
};

export type OddsEntry = {
  combination: string;
  odds: number;
};

// ---------------------------------------------------------------------------
// API関数
// ---------------------------------------------------------------------------

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) throw new Error(`API error: ${res.status} ${path}`);
  return res.json() as Promise<T>;
}

export async function fetchRacesByDate(date: string): Promise<Race[]> {
  return get<Race[]>(`/api/races?date=${date}`);
}

export async function fetchIndices(raceId: number): Promise<HorseIndex[]> {
  return get<HorseIndex[]>(`/api/races/${raceId}/indices`);
}
