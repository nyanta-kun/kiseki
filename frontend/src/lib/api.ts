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
  top_horse_number: number | null;   // 指数1位馬番
  top_horse_name: string | null;     // 指数1位馬名（結果確定後）
  top_horse_finish: number | null;   // 指数1位馬の確定着順（取消はnull）
  result_confirmed: boolean;         // レース結果確定済み
  is_special_only: boolean;          // 出馬表未確定で特別登録のみ
  special_horse_count: number;       // 特別登録馬の頭数（is_special_only=true 時のみ意味あり）
  is_projected_only?: boolean;       // 出馬表未確定で netkeiba 出走想定のみ
  projected_horse_count?: number;    // 出走想定馬の頭数（is_projected_only=true 時のみ意味あり）
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
  // JRA-VAN NEXT DM 指数（タイム型・対戦型）
  jvan_time_dm: number | null;
  jvan_battle_dm: number | null;
  // DM × 穴ぐさ × 既存指数 シグナルタグ（軸/穴/警戒）
  // 値: "三冠一致" | "高得点鉄板" | "穴ぐさDM" | "DM大穴" |
  //     "DM高オッズ" | "穴ぐさ+DMtime" | "人気下振れ" の組み合わせ
  dm_signals: string[] | null;
  // 購入シグナル（v26 breakaway ROI 検証ベース）
  // "super_buy" | "buy" | "watch" | null
  purchase_signal: "super_buy" | "buy" | "watch" | null;
  // 表示補助: composite_index のレース内ランク (1=1位)
  composite_rank: number | null;
  // 期待値 (= win_probability × win_odds)。オッズ未取得時は null
  expected_value: number | null;
  // スイートスポット該当フラグ
  // 条件: 単勝≥10 ∧ 期待値 1.2-5.0 ∧ 何らかのバッジ
  // 3年バックテスト 単ROI 1.182 / 複ROI 0.836
  is_sweet_spot: boolean;
  // 外部指数穴馬フラグ（外◎/外○バッジ）。判定はバックエンド buy_signal.py が単一真実源
  is_ext_dark_horse?: boolean;
  // 複勝EVモデルの「人気薄1頭 複勝EV軸」該当（毎レース最大1頭）
  // 条件: 単勝≥10 ∧ 較正複勝率≥フロア ∧ 複勝最低オッズ≥2.0 のEV最大1頭
  is_place_ev_axis?: boolean;
  place_ev_prob?: number | null;   // 較正複勝圏確率
  place_ev_value?: number | null;  // 複勝EV
  // 夏穴バッジ（牡セン≤470kg × 芝 × 前走比-4〜-6kg × 7番人気以上 × 夏競馬場）
  // 3年バックテスト 単ROI 2.133
  is_natsu_ana?: boolean;
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

/** 地方競馬 馬の近走成績 → 5 分キャッシュ */
export async function fetchChihouHorseHistory(horseId: number): Promise<RaceHistoryEntry[]> {
  return get<RaceHistoryEntry[]>(`/chihou/horses/${horseId}/history`, { next: { revalidate: 300 } });
}

/** オッズ（リアルタイム WebSocket を主に使用。初期値取得のみ）→ 30 秒キャッシュ */
export async function fetchOdds(raceId: number): Promise<OddsData> {
  return get<OddsData>(`/races/${raceId}/odds`, { next: { revalidate: 30 } });
}

/** 出走馬一覧（枠順確定後・指数算出前でも取得可能）→ 30 秒キャッシュ */
export async function fetchEntries(raceId: number): Promise<RaceEntry[]> {
  return get<RaceEntry[]>(`/races/${raceId}/entries`, { next: { revalidate: 30 } });
}

export type SpecialRegistration = {
  jravan_horse_code: string;
  horse_name: string;
  sex: string | null;
  age: number | null;
  trainer_name: string | null;
  race_name: string | null;
  grade_code: string | null;
  distance: number | null;
  track_code: string | null;
};

/** 特別登録馬一覧（出馬表確定前、TOKU DataSpec）→ 5分キャッシュ */
export async function fetchSpecialRegistrations(raceId: number): Promise<SpecialRegistration[]> {
  return get<SpecialRegistration[]>(`/races/${raceId}/special`, { next: { revalidate: 300 } });
}

export type ProjectedEntry = {
  netkeiba_race_id: string;
  horse_name: string;
  sex_age: string | null;
  expected_jockey_name: string | null;
  race_name: string | null;
};

/** 出走想定馬一覧（netkeiba 由来・全レース・出馬表確定前）→ 5分キャッシュ */
export async function fetchProjectedEntries(raceId: number): Promise<ProjectedEntry[]> {
  return get<ProjectedEntry[]>(`/races/${raceId}/projected`, { next: { revalidate: 300 } });
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

function _wsBase(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit.replace(/\/api\/?$/, "").replace(/\/$/, "");
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (apiUrl) {
    const base = apiUrl.replace(/\/api\/?$/, "").replace(/\/$/, "");
    return base.replace(/^https:\/\//, "wss://").replace(/^http:\/\//, "ws://");
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}

export function buildOddsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  return `${_wsBase()}/api/races/${raceId}/odds/ws`;
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
  return `${_wsBase()}/api/races/${raceId}/results/ws`;
}

export function buildChihouResultsWsUrl(raceId: number): string {
  if (typeof window === "undefined") return "";
  return `${_wsBase()}/api/chihou/races/${raceId}/results/ws`;
}

/** ブラウザ側ポーリング専用: 毎回サーバーから取得（キャッシュなし）*/
export async function fetchOddsBrowser(raceId: number): Promise<OddsData> {
  return get<OddsData>(`/races/${raceId}/odds`, { cache: "no-store" });
}

/** ブラウザ側ポーリング専用: 毎回サーバーから取得（キャッシュなし）*/
export async function fetchResultsBrowser(raceId: number): Promise<RaceResult[]> {
  return get<RaceResult[]>(`/races/${raceId}/results`, { cache: "no-store" });
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

/** 妙味候補（穴・収支保証なし）。的中重視推奨の副次情報。 */
export type ValueCandidate = {
  horse_number: number;
  horse_name: string | null;
  win_odds: number | null;
  index_rank: number | null;
  badges: string[];
  /** 複勝EVモデルの人気薄1頭軸該当（単勝10倍+×較正複勝率フロア×EV最大の1頭）。 */
  is_place_axis?: boolean;
  /** 軸の強度: "strong"(バッジ2+) / "standard"(バッジ1+/0) / null。 */
  upset_tier?: string | null;
  /** ワイド相手＝モデル指数1位（=本命）の馬番。 */
  wide_partner_horse_number?: number | null;
  /** 複勝EVモデルの較正済み複勝圏確率（軸該当馬のみ）。 */
  place_prob_cal?: number | null;
  /** 複勝EV = 較正複勝率 × 複勝最低オッズ近似（軸該当馬のみ）。 */
  place_ev?: number | null;
  /** 確定着順（レース後表示用）。 */
  finish_position?: number | null;
};

export type Recommendation = {
  id: number;
  rank: number;
  race: RecommendationRace;
  bet_type: "win" | "place" | "trifecta";
  /** 的中重視tier: S 鉄板 / A 信頼軸 / B 複勝圏（旧 SS/3F は降格済） */
  tier: "S" | "A" | "B" | "SS" | "3F-2軸" | "3F-BOX" | null;
  /** 実際の買い目組み合わせ 単勝: [[馬番]] / 3連複: [[1,2,3],[1,2,4],...] */
  ticket_combos: number[][] | null;
  points: number | null;
  roi_basis: number | null;
  is_verified: boolean | null;
  /** 妙味候補（穴・収支保証なし）。的中重視推奨の副次情報。 */
  value_candidates: ValueCandidate[] | null;
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
  return get<Recommendation[]>(`/recommendations?date=${date}`, {
    next: { revalidate: 60 },
  });
}

/** ブラウザ側ポーリング専用: JRA推奨を毎回サーバーから取得（キャッシュなし）*/
export async function fetchRecommendationsBrowser(date: string): Promise<Recommendation[]> {
  return get<Recommendation[]>(`/recommendations?date=${date}`, { cache: "no-store" });
}

// ---------------------------------------------------------------------------
// 穴ぐさルール推奨
// ---------------------------------------------------------------------------

export type AnagusaRuleItem = {
  rule_label: string;
  rule_desc: string;
  bet_type: "place" | "win_place";
  race_id: number;
  course_name: string;
  race_number: number;
  race_name: string | null;
  post_time: string | null;
  distance: number;
  surface: string;
  horse_number: number;
  horse_name: string | null;
  win_odds: number | null;
  place_odds: number | null;
  popularity: number | null;
  is_preferred_pop: boolean;
  finish_position: number | null;
  backtest_place_roi: number;
  backtest_win_roi: number | null;
  backtest_n: number;
  snapshot_at: string | null;
};

export async function fetchAnagusaRules(date: string): Promise<AnagusaRuleItem[]> {
  return get<AnagusaRuleItem[]>(`/recommendations/anagusa-rules?date=${date}`, {
    next: { revalidate: 60 },
  });
}

export async function fetchAnagusaRulesBrowser(date: string): Promise<AnagusaRuleItem[]> {
  return get<AnagusaRuleItem[]>(`/recommendations/anagusa-rules?date=${date}`, { cache: "no-store" });
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
  /** 前走着差指数（バックエンドは返しているが現状UI未表示） */
  last_margin_index?: number | null;
  /** 複勝期待値指数（バックエンドは返しているが現状UI未表示） */
  place_ev_index?: number | null;
  /** kichiuma/netkeibaで1位になった数: 0〜2、null=外部データなし */
  external_consensus: number | null;
  win_odds: number | null;
  /** 期待値 win_probability × win_odds */
  ev: number | null;
  /** スイートスポット（Phase2: 指数1位 ∧ 単勝10-30倍 ∧ 割安5場） */
  is_sweet_spot: boolean;
  /** 断然人気R複穴（Phase2: 1番人気<2.0 ∧ 単勝≥10 ∧ 指数3位以内） */
  is_place_bet: boolean;
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

/** 地方競馬 前後開催日検索 → 600秒キャッシュ（前後日付はほぼ変化しない） */
export async function fetchChihouNearestDate(
  fromDate: string,
  direction: "prev" | "next",
): Promise<{ date: string }> {
  return get<{ date: string }>(
    `/chihou/races/nearest-date?from=${fromDate}&direction=${direction}`,
    { next: { revalidate: 600 } },
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

/** ブラウザ側ポーリング専用: 地方競馬オッズ（キャッシュなし）*/
export async function fetchChihouOddsBrowser(raceId: number): Promise<OddsData> {
  return get<OddsData>(`/chihou/races/${raceId}/odds`, { cache: "no-store" });
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
  win_odds: number | null;
  place_odds: number | null;
  ev: number | null;
};

/** 地方競馬スイートスポット推奨カテゴリ。 */
export type ChihouRecommendCategory =
  | "sweet_spot"          // 高オッズ穴狙い (単勝≥10 ∧ EV 1.0-2.0 ∧ ROI陽性9場 ∧ k≤2)
  | "place_bet"           // 複穴 (1番人気<2.0 ∧ 単勝≥10 ∧ EV 1.2-2.0、複勝買い)
  | "upset_place"         // 穴軸複勝 (単勝10-15倍×人気薄リランカー×外部バッジ、的中精度特化)
  | "low_odds_trusted"    // 信頼できる本命 (単勝<1.5)
  | "low_odds_untrusted"; // 信頼できない本命 (1.5≤単勝<2.0)

/** レース内の複勝確率集中度。top2_share>0.873=high(76.5%ヒット率) / ≤0.715=low(57%) */
export type RaceConcentration = {
  top2_share: number | null;
  hhi: number | null;
  confidence_level: "high" | "medium" | "low" | null;
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
  category: ChihouRecommendCategory | null;
  target_horses: ChihouTargetHorse[];
  reason: string;
  confidence: number;
  race_concentration: RaceConcentration | null;
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

export type ChihouCategorySummary = {
  n_total: number;
  n_settled: number;
  n_hits: number;
  hit_rate: number | null;
  win_roi: number | null;          // bet_type に応じた ROI（単勝 or 複勝）
  bet_type: "win" | "place" | null;
};

export type ChihouSweetSpotResponse = {
  items: ChihouRecommendation[];
  summaries: Partial<Record<ChihouRecommendCategory, ChihouCategorySummary>>;
};

/** 地方競馬 推奨一覧（Claude Routine）→ 60秒キャッシュ */
export async function fetchChihouRecommendations(date: string): Promise<ChihouRecommendation[]> {
  return get<ChihouRecommendation[]>(`/chihou/recommendations?date=${date}`, { next: { revalidate: 60 } });
}

/** 地方競馬スイートスポット自動推奨（v10 LightGBM）→ 60秒キャッシュ
 *  3カテゴリ（高オッズ穴 / 信頼本命 / 不信頼本命）+ カテゴリ別当日集計を返す。
 */
export async function fetchChihouSweetSpotRecommendations(date: string): Promise<ChihouSweetSpotResponse> {
  return get<ChihouSweetSpotResponse>(`/chihou/recommendations/sweet-spot?date=${date}`, { next: { revalidate: 60 } });
}

/** ブラウザ側ポーリング専用: 地方推奨一覧（キャッシュなし）*/
export async function fetchChihouRecommendationsBrowser(date: string): Promise<ChihouRecommendation[]> {
  return get<ChihouRecommendation[]>(`/chihou/recommendations?date=${date}`, { cache: "no-store" });
}

/** ブラウザ側ポーリング専用: 地方スイートスポット推奨（キャッシュなし）*/
export async function fetchChihouSweetSpotRecommendationsBrowser(date: string): Promise<ChihouSweetSpotResponse> {
  return get<ChihouSweetSpotResponse>(`/chihou/recommendations/sweet-spot?date=${date}`, { cache: "no-store" });
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

// ---------------------------------------------------------------------------
// 勝率上位馬（当日 50%以上）
// ---------------------------------------------------------------------------

export type TopProbHorse = {
  course_name: string;
  race_number: number;
  race_name: string | null;
  post_time: string | null;
  horse_number: number | null;
  horse_name: string | null;
  win_probability: number;
  win_odds: number | null;
  finish_position: number | null;
};

export async function fetchChihouTopProbability(date: string): Promise<TopProbHorse[]> {
  return get<TopProbHorse[]>(`/chihou/races/top-probability?date=${date}`, { next: { revalidate: 60 } });
}

export async function fetchJraTopProbability(date: string): Promise<TopProbHorse[]> {
  return get<TopProbHorse[]>(`/races/top-probability?date=${date}`, { next: { revalidate: 60 } });
}

// ---------------------------------------------------------------------------
// 競輪
// ---------------------------------------------------------------------------

export type KeirinEntry = {
  frame_no: number;
  name: string | null;
  race_point: number | null;
  style: string | null;
  line_pos: number | null;
  finish_order: number | null;
  player_class: string | null;
};

export type KeirinPick = {
  id: number | null;
  race_key: string;
  has_pick: boolean;
  venue_name: string;
  race_no: number;
  grade: string | null;
  race_type: string | null;
  start_at: number | string | null;
  status: number;
  n_entries: number | null;
  rank: string | null;
  pred_combo: string | null;
  n_combos: number | null;
  synth_odds: number | null;
  /** 指数1-2位の予測確率差（0-1スケール） */
  gap12: number | null;
  /** 指数2-3位の予測確率差（ptスケール=×100済み） */
  gap23: number | null;
  /** 指数3-4位の予測確率差（0-1スケール） */
  gap34: number | null;
  hit: boolean;
  payout: number;
  trio_payout: number;
  trifecta_payout: number;
  bet_amount: number;
  miwokuri: boolean;
  prerace_gami: number | null;
  entries: KeirinEntry[];
};

export type KeirinPeriodSummary = {
  n_picks: number;
  /** オッズ条件で落ちる前の総候補レース数（指数条件のみ・購入+見送り） */
  n_candidates?: number;
  n_hits: number;
  total_bet: number;
  total_payout: number;
  roi: number | null;
  by_rank?: Record<string, { n_picks: number; n_hits: number; total_bet: number; total_payout: number; roi: number | null; n_candidates?: number }>;
};

export type KeirinSummary = {
  today: KeirinPeriodSummary;
  month: KeirinPeriodSummary;
  year: KeirinPeriodSummary;
  test: KeirinPeriodSummary;
  test_from: string;
  test_to: string;
};

export async function fetchKeirinPicks(date: string, includeAll = false): Promise<KeirinPick[]> {
  const q = includeAll ? `&include_all=true` : "";
  return get<KeirinPick[]>(`/keirin/picks?date=${date}${q}`, { cache: "no-store" });
}

export async function fetchKeirinSummary(date?: string): Promise<KeirinSummary> {
  const q = date ? `?date=${date}` : "";
  return get<KeirinSummary>(`/keirin/summary${q}`, { cache: "no-store" });
}

export async function refreshKeirinPicks(date: string): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}/api/keirin/refresh?date=${date}`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`refresh failed: ${res.status}`);
  return res.json();
}

export async function triggerKeirinFetchOdds(): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}/api/keirin/fetch-odds`, {
    method: "POST",
    cache: "no-store",
  });
  return res.json();
}

export async function triggerKeirinFetchResults(): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}/api/keirin/fetch-results`, {
    method: "POST",
    cache: "no-store",
  });
  return res.json();
}

export type KeirinStatItem = {
  date: string;
  n_picks: number;
  n_hits: number;
  total_bet: number;
  total_payout: number;
  roi: number | null;
  cum_bet: number;
  cum_payout: number;
  cum_roi: number | null;
  cum_month_roi: number | null;
  cum_month_bet: number;
  cum_month_payout: number;
  cum_year_roi: number | null;
  cum_year_bet: number;
  cum_year_payout: number;
};

export type KeirinStatsResponse = {
  items: KeirinStatItem[];
  period_summary: {
    n_picks: number;
    n_hits: number;
    total_bet: number;
    total_payout: number;
    roi: number | null;
  };
};

export async function fetchKeirinStats(
  fromDate: string,
  toDate: string,
  granularity: "daily" | "monthly",
): Promise<KeirinStatsResponse> {
  return get<KeirinStatsResponse>(
    `/keirin/stats?from_date=${fromDate}&to_date=${toDate}&granularity=${granularity}`,
    { cache: "no-store" },
  );
}
