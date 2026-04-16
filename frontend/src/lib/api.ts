/**
 * GallopLab バックエンドAPIクライアント
 */

// SSR（サーバーサイド）はBACKEND_URLを優先（Docker内部URL）。
// ブラウザはNEXT_PUBLIC_API_URLを使用（外部からアクセス可能なURL）。
// NEXT_PUBLIC_API_URLは/api無しで設定される場合があるため正規化する。
const _rawBase =
  typeof window === "undefined"
    ? (process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
const BASE_URL = _rawBase.replace(/\/api\/?$/, "").replace(/\/$/, "") + "/api";

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
  confidence_rank: "S" | "A" | "B" | "C" | null;
  recommend_rank: "S" | "A" | "B" | "C" | null;
  buy_signal: "buy" | "caution" | "pass" | null;
  top_win_odds: number | null;
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
  // 外部指数ランク（sekito.netkeiba / sekito.kichiuma）
  nb_course_rank: number | null;  // netkeibaコース適性指数のレース内順位（1=最高）
  nb_ave_rank: number | null;     // netkeibaタイム平均指数のレース内順位（1=最高）
  km_rank: number | null;         // kichiumaスピードスコアのレース内順位（1=最高）
};

export type OddsData = {
  win: Record<string, number>;   // horse_number (str) → 倍率
  place: Record<string, number>; // horse_number (str) → 倍率
};

export type RaceEntry = {
  id: number;
  frame_number: number;
  horse_number: number;
  horse_name: string;
  jockey_name: string | null;
  trainer_name: string | null;
  weight_carried: number | null;
  horse_weight: number | null;
  weight_change: number | null;
};

export type RaceConfidence = {
  score: number;
  label: "HIGH" | "MID" | "LOW";
  rank: "S" | "A" | "B" | "C";
  recommend_rank: "S" | "A" | "B" | "C";
  gap_1_2: number;
  gap_1_3: number;
  head_count: number;
  win_prob_top: number | null;
  top_win_odds: number | null;
};

export type IndicesResponse = {
  horses: HorseIndex[];
  confidence: RaceConfidence;
};

export type ConfidenceStats = {
  total_races: number;
  win_hit_rate: number;        // 単勝的中率 0-1
  place_hit_rate: number;      // 複勝的中率 0-1
  top3_coverage_rate: number;  // top3カバー率 0-1
  simulated_roi_win: number;   // 単勝シミュレーション回収率 (1.0=±0)
  simulated_roi_place: number; // 複勝シミュレーション回収率
  place_roi_races: number;     // 複勝ROI算出対象レース数
};

export type DimensionStat = {
  label: string;
  total_races: number;
  win_hit_rate: number;
  place_hit_rate: number;
  top3_coverage_rate: number;
  simulated_roi_win: number;
  simulated_roi_place: number;
  place_roi_races: number;
};

export type MonthlyStats = {
  year_month: string;          // "2025-01"
  total_races: number;
  win_hit_rate: number;
  place_hit_rate: number;
  top3_coverage_rate: number;
  simulated_roi_win: number;
  simulated_roi_place: number;
  place_roi_races: number;
  breakdown: {
    HIGH: ConfidenceStats | null;
    MID: ConfidenceStats | null;
    LOW: ConfidenceStats | null;
  };
};

export type PerformanceSummary = {
  from_date: string;
  to_date: string;
  total_races: number;
  win_hit_rate: number;
  place_hit_rate: number;
  top3_coverage_rate: number;
  simulated_roi_win: number;
  simulated_roi_place: number;
  place_roi_races: number;
  breakdown: {
    HIGH: ConfidenceStats | null;
    MID: ConfidenceStats | null;
    LOW: ConfidenceStats | null;
  };
  monthly_stats: MonthlyStats[];
  by_course: DimensionStat[];
  by_surface: DimensionStat[];
  by_distance_range: DimensionStat[];
  by_condition: DimensionStat[];
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

type CacheInit =
  | { cache: RequestCache }
  | { next: { revalidate: number } };

/**
 * バックエンド API への GET リクエスト。
 *
 * @param path APIパス（`/races/123` など）
 * @param cacheInit Next.js fetch キャッシュ設定。省略時は `next: { revalidate: 30 }`。
 */
async function get<T>(path: string, cacheInit?: CacheInit): Promise<T> {
  const init: RequestInit = cacheInit ?? { next: { revalidate: 30 } };
  const res = await fetch(`${BASE_URL}${path}`, init);
  if (!res.ok) throw new Error(`API error: ${res.status} ${path}`);
  return res.json() as Promise<T>;
}

/** レース基本情報（更新頻度低・発走後はほぼ変化なし）→ 5 分キャッシュ */
export async function fetchRace(raceId: number): Promise<Race> {
  return get<Race>(`/races/${raceId}`, { next: { revalidate: 300 } });
}

/** 日付別レース一覧（レース削除・追加はほぼない）→ 5 分キャッシュ */
export async function fetchRacesByDate(date: string): Promise<Race[]> {
  return get<Race[]>(`/races?date=${date}`, { next: { revalidate: 300 } });
}

/** 指数（再算出はあるが頻繁ではない）→ 60 秒キャッシュ */
export async function fetchIndices(raceId: number): Promise<IndicesResponse> {
  return get<IndicesResponse>(`/races/${raceId}/indices`, { next: { revalidate: 60 } });
}

/** 成績（確定後は不変、確定前はリアルタイム WebSocket を使用）→ 30 秒キャッシュ */
export async function fetchResults(raceId: number): Promise<RaceResult[]> {
  return get<RaceResult[]>(`/races/${raceId}/results`, { next: { revalidate: 30 } });
}

/** 馬の近走成績（一度確定すると変化しない）→ 5 分キャッシュ */
export async function fetchHorseHistory(horseId: number): Promise<RaceHistoryEntry[]> {
  return get<RaceHistoryEntry[]>(`/horses/${horseId}/history`, { next: { revalidate: 300 } });
}

/** オッズ（リアルタイム WebSocket を主に使用。初期値取得のみ）→ 30 秒キャッシュ */
export async function fetchOdds(raceId: number): Promise<OddsData> {
  return get<OddsData>(`/races/${raceId}/odds`, { next: { revalidate: 30 } });
}

/** 出走馬一覧（枠順確定後・指数算出前でも取得可能）→ 30 秒キャッシュ */
export async function fetchEntries(raceId: number): Promise<RaceEntry[]> {
  return get<RaceEntry[]>(`/races/${raceId}/entries`, { next: { revalidate: 30 } });
}

/** 最近開催日検索（カレンダーナビゲーション用）→ 30 秒キャッシュ */
export async function fetchNearestDate(
  fromDate: string,
  direction: "prev" | "next"
): Promise<{ date: string }> {
  return get<{ date: string }>(
    `/races/nearest-date?from=${fromDate}&direction=${direction}`,
    { next: { revalidate: 30 } },
  );
}

/** WebSocket URLを組み立てる（ブラウザ専用）。
 *  NEXT_PUBLIC_WS_URL が設定されていればそれを使用（ローカル開発用）。
 *  未設定時は window.location から導出（本番環境 nginx プロキシ経由）。
 */
export type PerformanceFilters = {
  from_date?: string;
  to_date?: string;
  course_name?: string[];
  surface?: string[];
  distance_range?: string[];
  condition?: string[];
};

/** AI指数精度サマリー（成績確定済みレースの集計）→ 5分キャッシュ */
export async function fetchPerformanceSummary(
  filters: PerformanceFilters = {},
): Promise<PerformanceSummary> {
  const params = new URLSearchParams();
  if (filters.from_date) params.set("from_date", filters.from_date);
  if (filters.to_date) params.set("to_date", filters.to_date);
  // カンマ区切りで送信（バックエンドが分割）
  if (filters.course_name?.length) params.set("course_name", filters.course_name.join(","));
  if (filters.surface?.length) params.set("surface", filters.surface.join(","));
  if (filters.distance_range?.length) params.set("distance_range", filters.distance_range.join(","));
  if (filters.condition?.length) params.set("condition", filters.condition.join(","));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return get<PerformanceSummary>(`/performance/summary${qs}`, { next: { revalidate: 300 } });
}

// ---------------------------------------------------------------------------
// 地方競馬 パフォーマンス
// ---------------------------------------------------------------------------

export type ChihouMonthlyStats = {
  year_month: string;
  total_races: number;
  win_hit_rate: number;
  place_hit_rate: number;
  top3_coverage_rate: number;
  simulated_roi_win: number;
  simulated_roi_place: number;
  place_roi_races: number;
};

export type ChihouPerformanceSummary = {
  from_date: string;
  to_date: string;
  total_races: number;
  win_hit_rate: number;
  place_hit_rate: number;
  top3_coverage_rate: number;
  simulated_roi_win: number;
  simulated_roi_place: number;
  place_roi_races: number;
  monthly_stats: ChihouMonthlyStats[];
  by_course: DimensionStat[];
  by_surface: DimensionStat[];
};

export type ChihouPerformanceFilters = {
  from_date?: string;
  to_date?: string;
  course_name?: string[];
  surface?: string[];
};

/** 地方競馬 AI指数精度サマリー → 5分キャッシュ */
export async function fetchChihouPerformanceSummary(
  filters: ChihouPerformanceFilters = {},
): Promise<ChihouPerformanceSummary> {
  const params = new URLSearchParams();
  if (filters.from_date) params.set("from_date", filters.from_date);
  if (filters.to_date) params.set("to_date", filters.to_date);
  if (filters.course_name?.length) params.set("course_name", filters.course_name.join(","));
  if (filters.surface?.length) params.set("surface", filters.surface.join(","));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return get<ChihouPerformanceSummary>(`/chihou/performance/summary${qs}`, { next: { revalidate: 300 } });
}

export function buildOddsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) {
    const base = explicit.replace(/\/api\/?$/, "").replace(/\/$/, "");
    return `${base}/api/races/${raceId}/odds/ws`;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}/api/races/${raceId}/odds/ws`;
}

// ---------------------------------------------------------------------------
// 予想（Yoso）型定義
// ---------------------------------------------------------------------------
export type YosoPrediction = {
  horse_id: number;
  horse_number: number;
  horse_name: string;
  frame_number: number | null;
  mark: string | null;
  user_index: number | null;
  index_share: number | null;    // 占有率 0〜1
  galloplab_index: number | null;
  win_odds: number | null;
  place_odds: number | null;
  finish_position: number | null;
};

export type OtherHorsePrediction = {
  horse_id: number;
  mark: string | null;
  user_index: number | null;
};

export type OtherUserPrediction = {
  user_id: number;
  yoso_name: string;
  show_index: boolean;
  predictions: OtherHorsePrediction[];
};

export type YosoRace = {
  race_id: number;
  race_name: string | null;
  race_number: number;
  course_name: string;
  horses: YosoPrediction[];
  other_users: OtherUserPrediction[];
};

export type DisplaySetting = {
  target_user_id: number;
  yoso_name: string;
  target_can_input_index: boolean;
  show_mark: boolean;
  show_index: boolean;
};

export type MyPublicSetting = {
  is_yoso_public: boolean;
  yoso_name: string | null;
};

export type ImportLog = {
  id: number;
  filename: string;
  race_date: string;
  total_count: number;
  saved_count: number;
  error_count: number;
  created_at: string;
};

export type YosoStats = {
  by_mark: MarkStats[];
  by_index_range: IndexRangeStats[];
  by_share_range: ShareRangeStats[];
};

export type MarkStats = {
  mark: string;
  count: number;
  win_count: number;
  place_count: number;
  win_rate: number;
  place_rate: number;
  win_roi: number;
  place_roi: number;
};

export type IndexRangeStats = {
  label: string;
  min_val: number;
  max_val: number | null;
  count: number;
  win_rate: number;
  place_rate: number;
  win_roi: number;
  place_roi: number;
};

export type ShareRangeStats = {
  label: string;
  min_val: number;
  max_val: number | null;
  count: number;
  win_rate: number;
  place_rate: number;
  win_roi: number;
  place_roi: number;
};

export function buildResultsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) {
    const base = explicit.replace(/\/api\/?$/, "").replace(/\/$/, "");
    return `${base}/api/races/${raceId}/results/ws`;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}/api/races/${raceId}/results/ws`;
}

export function buildChihouResultsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) {
    const base = explicit.replace(/\/api\/?$/, "").replace(/\/$/, "");
    return `${base}/api/chihou/races/${raceId}/results/ws`;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}/api/chihou/races/${raceId}/results/ws`;
}

// ---------------------------------------------------------------------------
// 推奨レース・馬券
// ---------------------------------------------------------------------------

export type RecommendationHorse = {
  horse_number: number;
  horse_name: string | null;
  composite_index: number | null;
  win_probability: number | null;
  place_probability: number | null;
  ev_win: number | null;
  ev_place: number | null;
  win_odds: number | null;
  place_odds: number | null;
  finish_position: number | null;  // 結果更新後に追記
};

export type RecommendationRace = {
  race_id: number;
  course_name: string;
  race_number: number;
  race_name: string | null;
  post_time: string | null;
  surface: string | null;
  distance: number | null;
  grade: string | null;
  head_count: number | null;
};

export type Recommendation = {
  id: number;
  rank: number;
  race: RecommendationRace;
  bet_type: "win" | "place" | "quinella";
  target_horses: RecommendationHorse[];
  snapshot_win_odds: Record<string, number> | null;
  snapshot_place_odds: Record<string, number> | null;
  snapshot_at: string | null;
  reason: string;
  confidence: number;
  result_correct: boolean | null;
  result_payout: number | null;
  result_updated_at: string | null;
  created_at: string;
};

export type OddsDataPoint = {
  win_odds: number | null;
  win_hit: boolean;
  place_odds: number | null;
  place_hit: boolean;
  has_place_odds: boolean;
};

/** オッズ帯別ROI感度分析用データ（クライアント側でインタラクティブ集計）→ no-store */
export async function fetchOddsData(
  filters: PerformanceFilters = {},
): Promise<OddsDataPoint[]> {
  const params = new URLSearchParams();
  if (filters.from_date) params.set("from_date", filters.from_date);
  if (filters.to_date) params.set("to_date", filters.to_date);
  if (filters.course_name?.length) params.set("course_name", filters.course_name.join(","));
  if (filters.surface?.length) params.set("surface", filters.surface.join(","));
  if (filters.distance_range?.length) params.set("distance_range", filters.distance_range.join(","));
  if (filters.condition?.length) params.set("condition", filters.condition.join(","));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return get<OddsDataPoint[]>(`/performance/odds-data${qs}`, { cache: "no-store" });
}

export async function fetchRecommendations(date: string): Promise<Recommendation[]> {
  const res = await fetch(`${BASE_URL}/recommendations?date=${date}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`fetchRecommendations failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// 地方競馬 型定義
// ---------------------------------------------------------------------------

export type ChihouHorseIndex = {
  horse_id: number;
  horse_number: number | null;
  horse_name: string;
  composite_index: number;
  win_probability: number | null;
  place_probability: number | null;
  speed_index: number | null;
  last3f_index: number | null;
  jockey_index: number | null;
  rotation_index: number | null;
  /** kichiuma/netkeibaで1位になった数: 0〜2、null=外部データなし */
  external_consensus: number | null;
};

export type ChihouRaceRanks = {
  score: number;
  confidence_rank: "S" | "A" | "B" | "C";
  recommend_rank: "S" | "A" | "B" | "C";
  gap_1_2: number;
  gap_1_3: number;
  win_prob_top: number | null;
  top_win_odds: number | null;
};

export type ChihouIndicesResponse = {
  horses: ChihouHorseIndex[];
  ranks: ChihouRaceRanks | null;
};

// ---------------------------------------------------------------------------
// 地方競馬 API関数
// ---------------------------------------------------------------------------

/** 地方競馬 日付別レース一覧 → 5分キャッシュ */
export async function fetchChihouRacesByDate(date: string): Promise<Race[]> {
  return get<Race[]>(`/chihou/races?date=${date}`, { next: { revalidate: 300 } });
}

/** 地方競馬 レース詳細 → 5分キャッシュ */
export async function fetchChihouRace(raceId: number): Promise<Race> {
  return get<Race>(`/chihou/races/${raceId}`, { next: { revalidate: 300 } });
}

/** 地方競馬 前後開催日検索 → 30秒キャッシュ */
export async function fetchChihouNearestDate(
  fromDate: string,
  direction: "prev" | "next",
): Promise<{ date: string }> {
  return get<{ date: string }>(
    `/chihou/races/nearest-date?from=${fromDate}&direction=${direction}`,
    { next: { revalidate: 30 } },
  );
}

/** 地方競馬 指数 → 60秒キャッシュ */
export async function fetchChihouIndices(raceId: number): Promise<ChihouIndicesResponse> {
  return get<ChihouIndicesResponse>(`/chihou/races/${raceId}/indices`, { next: { revalidate: 60 } });
}

/** 地方競馬 成績 → 30秒キャッシュ */
export async function fetchChihouResults(raceId: number): Promise<RaceResult[]> {
  return get<RaceResult[]>(`/chihou/races/${raceId}/results`, { next: { revalidate: 30 } });
}

/** 地方競馬 単勝・複勝オッズ → 30秒キャッシュ */
export async function fetchChihouOdds(raceId: number): Promise<OddsData> {
  return get<OddsData>(`/chihou/races/${raceId}/odds`, { next: { revalidate: 30 } });
}

// ---------------------------------------------------------------------------
// 地方競馬 推奨
// ---------------------------------------------------------------------------

export type ChihouTargetHorse = {
  horse_number: number;
  horse_name: string | null;
  composite_index: number | null;
  win_probability: number | null;
  place_probability: number | null;
  finish_position: number | null;
  /** kichiuma/netkeibaで1位になった数: 0〜2、null=外部データなし */
  external_consensus: number | null;
};

export type ChihouRecommendation = {
  id: number;
  rank: number;
  race: {
    race_id: number;
    course_name: string;
    race_number: number;
    race_name: string | null;
    post_time: string | null;
    surface: string | null;
    distance: number | null;
  };
  bet_type: string;
  target_horses: ChihouTargetHorse[];
  reason: string;
  confidence: number;
  odds_decision: "buy" | "pass" | null;
  odds_decision_at: string | null;
  odds_decision_reason: string | null;
  snapshot_win_odds: Record<string, number> | null;
  snapshot_place_odds: Record<string, number> | null;
  snapshot_at: string | null;
  result_correct: boolean | null;
  result_payout: number | null;
  result_updated_at: string | null;
  created_at: string;
};

/** 地方競馬 推奨一覧 → 30秒キャッシュ */
export async function fetchChihouRecommendations(date: string): Promise<ChihouRecommendation[]> {
  return get<ChihouRecommendation[]>(`/chihou/recommendations?date=${date}`, { next: { revalidate: 30 } });
}

// ---------------------------------------------------------------------------
// 購入指針統計
// ---------------------------------------------------------------------------

export type BuyingGuideRow = {
  label: string;
  races: number;
  win_pct: number;
  place_pct: number;
  win_roi: number;
};

export type BuyingGuide = {
  odds_cutoff: BuyingGuideRow[];
  by_course: BuyingGuideRow[];
  by_distance: BuyingGuideRow[];
  since: string;
};

export async function fetchJraBuyingGuide(since = "20250101"): Promise<BuyingGuide> {
  return get<BuyingGuide>(`/performance/buying-guide?since=${since}`, { next: { revalidate: 3600 } });
}

export async function fetchChihouBuyingGuide(since = "20250101"): Promise<BuyingGuide> {
  return get<BuyingGuide>(`/chihou/performance/buying-guide?since=${since}`, { next: { revalidate: 3600 } });
}
