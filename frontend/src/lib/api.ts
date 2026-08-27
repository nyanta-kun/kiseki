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
  /** 地方のみ: 注目馬（人気薄の複勝圏候補）がいる → レース名の右に★ */
  has_place_pick?: boolean;
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
  // 着外率予測（6着以下・オッズ非使用モデル models/jra_out_rate_lgb.txt）
  out_probability: number | null;
  // 足切り候補（グレーアウト表示）= out_probability >= 0.80
  // 判定の単一真実源はバックエンド（composite.py OUT_PROB_CUTOFF）
  is_cut_off: boolean;
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
  anagusa_comment: string | null;  // 穴ぐさ専門紙の推奨コメント（ピックありの場合のみ）
  upside_score: number | null;  // 穴馬スコア 0〜1（指数下位でも馬券になりやすい度合い）
  // 外部指数ランク（sekito.netkeiba / sekito.kichiuma）
  nb_course_rank: number | null;  // netkeibaコース適性指数のレース内順位（1=最高）
  nb_ave_rank: number | null;     // netkeibaタイム平均指数のレース内順位（1=最高）
  km_rank: number | null;         // kichiumaスピードスコアのレース内順位（1=最高）
  // JRA-VAN NEXT DM 指数（タイム型・対戦型）
  jvan_time_dm: number | null;
  jvan_battle_dm: number | null;
  // DM × 穴ぐさ × 既存指数の穴候補タグ（2026-07-25全面簡素化・軸/警戒タグは廃止）
  // 値: "穴"（レース内最有力の穴候補1頭のみ）| "特穴"（穴ぐさ×指数上位3×単勝10倍以上、2026-07-26追加）
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

/**
 * オッズ更新の鮮度。地方の `/chihou/races/{id}/odds` だけが返す（JRA は未対応）。
 *
 * 取得が止まっても DB には最後のスナップショットが残るため、API は 200 と
 * 「それらしい倍率」を返し続ける。値だけでは停止に気づけないので必ず併記する。
 * 判定の正本は backend の `services/chihou_odds_freshness.py`。
 */
export type OddsFreshness = {
  /** live=最新 / delayed=遅延 / stale=停止 / missing=未取得 / closed=発走済み（正常） */
  status: "live" | "delayed" | "stale" | "missing" | "closed";
  /** 最終取得からの経過秒。未取得なら null */
  age_seconds: number | null;
  /** 最終取得時刻 (ISO8601 / UTC)。未取得なら null */
  last_fetched_at: string | null;
};

export type OddsData = {
  win: Record<string, number>;   // horse_number (str) → 倍率
  place: Record<string, number>; // horse_number (str) → 倍率
  /** 地方のみ。JRA のレスポンスには含まれない */
  freshness?: OddsFreshness;
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
  recommend_rank: "S" | "A" | "B" | "C" | "C+";
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
  /** 的中重視tier（市場一致ベース再設計）: S 最強軸 / A 信頼軸 / B 準軸 / C+ 準見送り（旧 SS/3F は降格済） */
  tier: "S" | "A" | "B" | "C+" | "SS" | "3F-2軸" | "3F-BOX" | null;
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
  /** tier固定値でなくレース単位の連続値スコア(confidence_score - entropy_norm*30) */
  priority_score: number;
  /** 市場混戦度(0〜1、1に近いほど大混戦、算出不能時null) Phase3で追加 */
  entropy_norm: number | null;
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
  /** 注目馬（発走前6番人気以下 ∧ 指数3位内 ∧ 開いたレース ∧ 8頭以上）→ 馬名の右に★ */
  is_place_pick?: boolean;
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

/** 地方 注目馬（穴馬複勝）1頭ぶん */
export type ChihouFeaturedPlaceHorse = {
  race_id: number;
  course_name: string;
  race_number: number;
  race_name: string | null;
  post_time: string | null;
  head_count: number | null;
  horse_number: number | null;
  horse_name: string | null;
  win_odds: number | null;
  place_odds: number | null;
  /** 市場上位3頭シェア。小さいほど「開いたレース」 */
  top3_share: number;
  /** 発走前オッズによる人気順位 */
  popularity: number | null;
  /** composite_index のレース内順位 */
  index_rank: number | null;
  finish_position: number | null;
};

export async function fetchChihouFeaturedPlace(date: string): Promise<ChihouFeaturedPlaceHorse[]> {
  return get<ChihouFeaturedPlaceHorse[]>(`/chihou/races/featured-place?date=${date}`, {
    next: { revalidate: 60 },
  });
}

export async function fetchJraTopProbability(date: string): Promise<TopProbHorse[]> {
  return get<TopProbHorse[]>(`/races/top-probability?date=${date}`, { next: { revalidate: 60 } });
}

// ---------------------------------------------------------------------------
// レース信頼度一覧（推奨ページ）
// ---------------------------------------------------------------------------

/**
 * 推奨ページの1行 = 1レース。
 *
 * `confidence_score` / `tier` は**レース単位**の信頼度（指数1位馬ベース）、
 * `horse_number` 以降は**そのレースの単勝1番人気馬**。両者の基準馬は必ずしも一致しない。
 * 指数・オッズ未取得のレースも行として返るため、馬側は全て null になりうる。
 */
export type RaceConfidenceRow = {
  race_id: number;
  course_name: string;
  race_number: number;
  race_name: string | null;
  post_time: string | null;
  surface: string | null;
  distance: number | null;
  head_count: number | null;
  /**
   * 0-100。confidence.py の指数差40+頭数20+分散25+勝率15。
   * ⚠️ **tier とは対応しない。** tier の第一分岐は市場一致で confidence_score は
   * 第二分岐でしか効かないため、「96 なのに tier C」「67 なのに tier A」が普通に起きる。
   * 並び替えの主軸には tier_score を使うこと。
   */
  confidence_score: number | null;
  /** S / A / B / C+ / C */
  tier: string | null;
  /**
   * tier を 0-100 の連続値にしたもの。**降順に並べると tier 順が完全に再現され、
   * かつ同じ tier の中でも priority_score の順に並ぶ。** 一覧の既定の並び替え軸。
   */
  tier_score: number | null;
  /** 指数1位馬が単勝1番人気と一致するか。tier の第一分岐。 */
  market_agree: boolean | null;
  /** 市場の混戦度 0-1。高いほど拮抗。C+/C の分岐と priority_score の減点に使う。 */
  entropy_norm: number | null;
  /** tier 内の並び順に使う連続値（confidence_score - entropy_norm*30）。 */
  priority_score: number | null;
  horse_number: number | null;
  horse_name: string | null;
  win_odds: number | null;
  /** 0〜1。較正済み単勝確率 */
  win_probability: number | null;
  /** 単勝オッズ × 単勝率。表示は小数第1位だが、並び替えのため素の値で持つ */
  ev: number | null;
  finish_position: number | null;
};

export async function fetchRaceConfidence(date: string): Promise<RaceConfidenceRow[]> {
  return get<RaceConfidenceRow[]>(`/races/confidence?date=${date}`, { next: { revalidate: 60 } });
}

/** ブラウザ側ポーリング専用: 毎回サーバーから取得（キャッシュなし） */
export async function fetchRaceConfidenceBrowser(date: string): Promise<RaceConfidenceRow[]> {
  return get<RaceConfidenceRow[]>(`/races/confidence?date=${date}`, { cache: "no-store" });
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
  /** ライン（同じ値の車が同一ライン）。winticket の linePrediction 由来 */
  line_group: string | number | null;
  finish_order: number | null;
  player_class: string | null;
  /** 単勝モデル(lgbm_wt_win)の予測確率（%） */
  pred_win_pct: number | null;
  /** 2着内(連対)モデル(lgbm_wt_top2)の予測確率（%）。2026-08-12 以降のレースのみ */
  pred_top2_pct: number | null;
  /** 複勝(3着内)モデルの予測確率（%） */
  pred_top3_pct: number | null;
  /** WINTICKET公式予想印（0=無印, 1〜4=印あり） */
  prediction_mark: number | null;
};

export type KeirinPick = {
  id: number | null;
  race_key: string;
  has_pick: boolean;
  venue_name: string;
  race_no: number;
  grade: string | null;
  race_type: string | null;
  /** 看板レース（決勝・特選クラス）。判定はAPI側（services/keirin_marquee.py）が正本。 */
  is_marquee?: boolean;
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
  /**
   * 的中したか。**売った商品（入稿）があるならその買い目で判定した結果**で、
   * picks_history（ランクの候補）の成績ではない（2026-08-25 統一）。
   * 🔴 `settled` が false のあいだは「まだ分からない」であって外れではない。
   */
  hit: boolean;
  /** 採点が終わったか。false は未確定（発走前・結果待ち・確定配当待ち）。 */
  settled?: boolean;
  /** 確定した当たり目（同着なら複数）。表記は 3連複 `1=2=4` / 3連単 `1-2-4`。
   *  🔴 着順から画面で組み立て直さないこと（同着を必ず取りこぼす）。 */
  winning_combos?: string[];
  payout: number;
  trio_payout: number;
  trifecta_payout: number;
  bet_amount: number;
  /**
   * **実際に売った商品か**（2026-08-25 新設）。購入表示はこれだけで判定する。
   * 🔴 `bet_amount > 0` で判定してはいけない。あれはゲートを通る前の候補にも
   * 立つ名目値で、見送ったレースまで「購入・的中」と表示していた
   * （08-25 松阪7R 7S ＝ 平均払戻ゲートで売っていないのに「的中 42,400円」）。
   * 売っていない行は `bet_amount` も `payout` も 0 が返る。
   */
  sold?: boolean;
  /** 入稿を見送った理由のコード（売っていない行だけ非 null）。
   *  語彙の正本は backend/src/services/keirin_skip_reasons.py。 */
  skip_reason?: string | null;
  /** バッジに出す短いラベル（例「平均払戻」）。🔴 **文言はサーバーが決める**
   *  （入稿側と表示側が同じ正本を読む。ここで組み立てると三重管理になる）。 */
  skip_reason_label?: string | null;
  /** ツールチップに出す説明。実測値つきの文言（例「平均払戻 19,226円 <= 20,000円」）。 */
  skip_reason_text?: string | null;
  /** 取り消した理由（`netkeirin_submissions.cancel_reason`）。取消行だけ非 null。 */
  cancel_reason?: string | null;
  /**
   * ── モデル（ランクの**候補**）としての結果 ────────────────────────
   * 🔴 **売った商品の成績ではない。** `hit` / `payout` / `bet_amount` とは
   * 別物なので集計へ混ぜないこと。入稿・Discord は売った商品だけを見る。
   * ここはゲートで見送ったレースが当たっていたかを Web で追うために返る。
   * ⚠️ `paper_bet` は「1万円賭けたことにしたら」という**名目値**。実際の投資ではない。
   */
  paper_hit?: boolean | null;
  paper_payout?: number | null;
  paper_bet?: number | null;
  paper_combo?: string | null;
  miwokuri: boolean;
  prerace_gami: number | null;
  /** 過去のgate_label分岐（"SS"|"S"）の名残。2026-08-01〜表示ランクの決定には
   *  使わない（keirin側commit e994758で分岐廃止・常に"S"）。分析用に保持。 */
  gate_label?: string | null;
  /** 最終表示ランク文字列（"7S"|"7A"|"9C"等） */
  display_rank?: string;
  /** ランクのゲートを通らず入稿したレース（手動入稿・看板の穴埋め）。
   *  picks_history に行が無いので、買い目・投資・的中は入稿記録
   *  （netkeirin_submissions.bet_detail）と確定結果から組み立てている。
   *  ⚠️ ランクの成績として読まないこと（同じ 7A でも別経路）。 */
  submission_only?: boolean;
  /** 入稿の出自（`rank` / `marquee_fill` / `manual`）。picks_history 由来の行は null。
   *  🔴 `submission_only` は「picks_history に行が無い」だけで、
   *  **看板の穴埋め（自動）と手動入稿を区別できない**。バッジはこちらで出し分ける。 */
  origin?: string | null;
  /** 開催グレード（winticket の cup.grade。6=GP 5=GI 4=GII 3=GIII 2=FI 1=FII）。
   *  ⚠️ `grade` 列は**級班**（A級/S級/L級）で別物。 */
  cup_grade?: number | null;
  /** 上記の表示ラベル。未知の値は null（対応表を見直す合図）。 */
  cup_grade_label?: string | null;
  /**
   * レース信頼度（0〜100 の整数・四捨五入）。100% ＝ 上位2車の3着内率の合計が
   * 2.00（軸2車がどちらも確実に3着以内）。
   *
   * 🔴 **ランクのゲートが見ているのと同じ量**なので、出る／出ないの理由が
   *    画面から読める（7C は 72% 相当・9C は 65% 相当が下限）。
   *    判定の正本は keirin 側 `src/p3_calibration.confidence_pct`。
   */
  confidence_pct?: number | null;
  /**
   * 信頼度が見ている2車のうち**何車が3着以内に入ったか**（0 / 1 / 2）。未確定なら null。
   * 表示は 2→○ / 1→△ / 0→×。
   *
   * 🔴 **買い目の的中とは別物。** 相手が外れても二軸はそろっていることがあるので、
   *    ○ なのに不的中、は普通に起きる。
   */
  confidence_hit_count?: number | null;

  /** 大会名（例「オールスター競輪」）。 */
  cup_name?: string | null;
  /** 推奨外(has_pick=false)レースの仮想買い目。軸選定不能・7/9車以外はnull */
  hypo_axis1: number | null;
  hypo_axis2: number | null;
  hypo_others: number[] | null;
  hypo_axis_sum: number | null;
  hypo_entropy: number | null;
  hypo_wt_overlap_n: number | null;
  /** 開催（会場×日）の種別。**その開催の第1レース**の発走時刻で決まる。
   *  発走時刻が取れない開催は null（色を付けない）。
   *  netkeirin 入稿の波と境界を揃えてある（backend `keirin_meeting.py` 参照）。 */
  meeting_type: "morning" | "day" | "nighter" | "midnight" | null;
  /** netkeirin へ**入稿した時点の**買い目と金額配分。未入稿なら null。
   *  傾斜配分は入稿時点の想定オッズから決まるため後から再現できないので、
   *  keirin 側が入稿の瞬間に保存した値をそのまま表示する（再計算しない）。 */
  submitted_bet: KeirinSubmittedBet | null;
  /** その入稿を取り消した（＝売っていない）。買い目は記録として出すが、
   *  的中・払戻・投資額はそこから作らない。 */
  submission_cancelled?: boolean;
  entries: KeirinEntry[];
};

export type KeirinSubmittedBetLine = {
  /** "3連複" | "3連単" */
  bet_type: string;
  /** "1=2=5"（3連複・着順なし） / "1-2-5"（3連単・着順あり） */
  combo: string;
  stake: number;
  /** **入稿時点の**オッズ。配分の根拠そのもの。取れなかった場合は null
   *  （0 にすると「オッズ0倍」と読めてしまうので null で残している） */
  odds: number | null;
  /** オッズの出どころ。
   *  - `board` … 実際に板に付いていた値
   *  - `predicted` … 板に無く、構造モデル（src.odds_prediction）が生成した値
   *  - `null` … どちらも無い（三連単は予測できないのでここに落ちる）
   *  🔴 **表示では必ず区別する。** 予測値を板と同じ顔で出すと
   *     「実際に付いていたオッズ」と読まれる。 */
  odds_source?: "board" | "predicted" | null;
  /** 下振れしても割らないオッズ水準（＝下限包絡）。**オッズではない**。
   *  朝の板は買い目の帯で確定までに大きく下がる（実測 中央 確定/表示 0.860・
   *  45%が0.8倍未満）ため、最低払戻とガミ判定はこちらで測る。
   *  三連単・2026-08-16 以前の入稿には付かない（null）。 */
  odds_low?: number | null;
};

export type KeirinSubmittedBet = {
  total: number;
  /** 金額配分の出どころ: blend=朝オッズ×モデル / odds=朝オッズのみ /
   *  model=モデルのみ / equal=均等。均等固定のランクは null */
  source: string | null;
  lines: KeirinSubmittedBetLine[];
};

export type KeirinPeriodSummary = {
  n_picks: number;
  /** オッズ条件で落ちる前の総候補レース数（指数条件のみ・購入+見送り） */
  n_candidates?: number;
  n_hits: number;
  total_bet: number;
  total_payout: number;
  roi: number | null;
  /** 期間内の的中1件あたり最大払戻（円）。的中0件の場合はnull */
  max_payout?: number | null;
  /** 入稿はあるが**買い目の原本が無い**ため集計から外した件数。
   *  bet_detail の保存は 2026-08-07 開始で、それ以前は金額を復元できない
   *  （0円で足すと投資額を過小に見せるので件数だけ出す）。 */
  n_unpriced?: number;
  /** 🔴 この期間の集計に含まれる**ペーパー分**の件数（2026-08-21 追加）。
   *  実販売の開始（`REAL_SALES_FROM` = 2026-08-07）より前は netkeirin へ
   *  出していないので、`picks_history`（現行ランクのみ）で埋めている。
   *  **黙って足すと「当年＝全部実売」と読まれる**ので、値があれば内訳を出すこと。 */
  paper_picks?: number;
  by_rank?: Record<string, { n_picks: number; n_hits: number; total_bet: number; total_payout: number; roi: number | null; n_candidates?: number; max_payout?: number | null }>;
};

export type KeirinSummary = {
  /** 2026-08-02〜: RANK_7S/RANK_7A/RANK_9S/RANK_9A の4ランクをまとめて集計。
   *  by_rankにこれら全ランクが並ぶ（表示ラベル 7S/7A/9S/9A）。
   *  gate_labelによるSS/S分岐は廃止済み（keirin側commit e994758・2026-07-31）。
   *  RANK_7SS（波乱軸選出）は 2026-08-02 に全廃（ROI73.5%・n=16,298）。 */
  today: KeirinPeriodSummary;
  month: KeirinPeriodSummary;
  year: KeirinPeriodSummary;
  /** 🔴 **ペーパー通算**（2026-08-21 追加）。上3行とは**母集団が違う**。
   *
   *  today/month/year の投資・払戻は `netkeirin_submissions`＝**実際に売った商品**
   *  から数えるが、その原本は 2026-07-24 開始しかない。こちらは `picks_history`
   *  ＝「もし買っていたら」の紙の記録で 2024-01 から連続している。
   *  🔴 **無印で同じ表に並べない。必ず「ペーパー」と明示すること**
   *  （`/review`(実売) と `/keirin`(ペーパー) の不一致を不具合と誤診する型に戻る）。
   *  ⚠️ 現行ランクのみ（廃止済みの 7A/7SS/9A/9S は除外）。ただし `rule_version` の
   *     変化までは吸収していないので**世代をまたいだ通算**であることを承知で読む。
   *  古いAPIに当たったら undefined（行を出さない・fail-open）。 */
  paper_total?: KeirinPeriodSummary & { since?: string; is_paper?: boolean };
  /** Web に出してよい表示ラベル（入稿対象ONのランクだけ・2026-08-12）。
   *  未指定の古いAPIに当たったときは絞り込まない（fail-open）。 */
  visible_ranks?: string[];
};

export async function fetchKeirinPicks(date: string, includeAll = false): Promise<KeirinPick[]> {
  const q = includeAll ? `&include_all=true` : "";
  return get<KeirinPick[]>(`/keirin/picks?date=${date}${q}`, { cache: "no-store" });
}

// refreshKeirinPicks / triggerKeirinFetchOdds / triggerKeirinFetchResults /
// triggerKeirinSubmitRace は 2026-08-03 に app/keirin/actions.ts の Server Action へ
// 移行した（バックエンドに ApiKeyDep を付けたため、APIキーをサーバー側に保持する
// 必要がある。ブラウザから直接叩く実装は残さない）。
export async function fetchKeirinSummary(date?: string): Promise<KeirinSummary> {
  const q = date ? `?date=${date}` : "";
  return get<KeirinSummary>(`/keirin/summary${q}`, { cache: "no-store" });
}

/** 推奨外レースの手動入稿用ランク。
 * S1（2026-07-31全廃）・旧gate_label分岐由来の7SS/9SS（同日廃止）に加え、
 * RANK_7SS（波乱軸選出・穴レース検知）も 2026-08-02 に全廃したため対象外
 * （backend/src/api/keirin_router.py の _MANUAL_RANK_KEYS と揃える）。 */
// ⚠️ backend の `keirin_router._MANUAL_RANK_KEYS` と**必ず一致**させること。
// ここに backend が受け付けないキーを載せると、UI では選べるのに送信すると
// 必ず 400「不正なrank_key」になる（2026-08-03〜08-08 の間 "7B" が実際にそうなっていた）。
// 一致は backend/tests/test_keirin_rank_consistency.py が機械的に検査する。
export type ManualKeirinRankKey = "7S" | "7B" | "9C";


export type KeirinStatItem = {
  date: string;
  n_picks: number;
  n_hits: number;
  /** ガミ（払戻 < 賭け金）を不的中と数えたもの。netkeirin の表示的中率はこちら。 */
  n_net_hits?: number;
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
    n_net_hits?: number;
  };
  /**
   * どちらの母集団か。`"sold"` = 実際に売った商品（収支の正）/
   * `"paper"` = モデル（ランクの候補）の名目成績。
   * 🔴 **画面は必ずこれを出すこと。** 同じ期間で数字が2種類あるので、
   * ラベルが無いと必ず誤読される。足し合わせてもいけない。
   */
  source?: "sold" | "paper";
  /** 手動・穴埋め入稿を含めた集計か。⚠️ 2026-08-25 から意味を持たない。 */
  include_manual?: boolean;
  /** 含めた場合に、買い目が記録されておらず集計から外した件数。
   *  ⚠️ `bet_detail` の保存開始は 2026-08-07。それ以前の手動入稿は
   *  「入稿した事実」しか残っておらず金額を復元できない。 */
  manual_missing_bet_detail?: number;
};

export type KeirinStatsRank = | "7S" | "7B" | "9C" | "7H1" | "7H2" | "9H1" | "7C" | "7T1" | "7T3" | "7M1"
  | "all";

// netkeirin（ウマい車券）自動入稿設定。rank_key='_global' は全体ON/OFFの特殊行。
// 2026-08-02〜: S1・9SS・旧7SSは全廃済みのため対象外（backend/src/api/keirin_router.py の
// NETKEIRIN_RANK_KEYS と揃える）。
// 2026-08-05〜: 同じ "7SS" ラベルで別戦略（entropy不合格×軸2車が同一ライン）を
// 新設したため復活。旧7SSの設定行（enabled=false）がDBに残っているので、
// 設定画面で有効化しないと自動入稿されない点に注意。
// 2026-08-06〜: 7H1（穴推奨・本命バスト型・三連単F+三連複BOXの2券種）を追加。
// 2026-08-08〜: 9H1（穴推奨・9車高配当・三連単フォーメーション6点）を追加。
// 2026-08-10〜: 7H2（穴推奨・印なし2軸・三連単倍購入10点+三連複BOX10点）を追加。
// 2026-08-12〜: 7H3（穴推奨・本命連対どまり型）を追加 → 2026-08-13 に全廃。
// 2026-08-13〜: 7T1（三連単・高配当枠／看板×別ライン・点数可変）へ置換。
// 2026-08-24〜: 7T3（三連単・決勝の中配当枠／決勝限定・予測30倍以上から確率上位5点・
//   ライン条件なし）を追加。7T1 は同日に母集団を決勝のみへ絞った。
export type NetkeirinRankKey =
  | "_global" | "7S" | "7B" | "9C" | "7H1" | "7H2" | "9H1" | "7C" | "7M1"
  | "7T1" | "7T3";

export type NetkeirinSetting = {
  rank_key: NetkeirinRankKey;
  enabled: boolean;
  title_template: string;
  comment_template: string;
};

export async function fetchNetkeirinSettings(): Promise<NetkeirinSetting[]> {
  return get<NetkeirinSetting[]>(`/keirin/netkeirin-settings`, { cache: "no-store" });
}

export async function fetchKeirinStats(
  fromDate: string,
  toDate: string,
  granularity: "daily" | "monthly",
  rank?: KeirinStatsRank | KeirinStatsRank[],
  /** ⚠️ 2026-08-25 から**サーバー側で無視される**（互換のために残している）。 */
  includeManual = false,
  /** 母集団。`"sold"`（既定）= 売った商品 / `"paper"` = モデルの候補。
   *  🔴 2つは足せない（候補の賭け金は名目値）。切り替えであって合算ではない。 */
  source: "sold" | "paper" = "sold",
): Promise<KeirinStatsResponse> {
  const rankValue = Array.isArray(rank) ? rank.join(",") : rank;
  const rankQuery = rankValue ? `&rank=${encodeURIComponent(rankValue)}` : "";
  const manualQuery = includeManual ? "&include_manual=true" : "";
  const sourceQuery = source === "paper" ? "&source=paper" : "";
  return get<KeirinStatsResponse>(
    `/keirin/stats?from_date=${fromDate}&to_date=${toDate}&granularity=${granularity}${rankQuery}${manualQuery}${sourceQuery}`,
    { cache: "no-store" },
  );
}

// netkeirin（ウマい車券）二軸探偵の日別成績・売上。
// scripts/scrape_netkeirin_sales.py が umaiaggre.yosoka.netkeiba.com から日次収集。
export type NetkeirinSalesItem = {
  date: string;
  n_predictions: number | null;
  n_predictions_staked: number | null;
  n_hits_incl_garami: number | null;
  n_hits_excl_garami: number | null;
  n_miss: number | null;
  stake_amount: number;
  payout_amount: number;
  hit_rate_pct: number | null;
  recovery_rate_pct: number | null;
  n_sold: number | null;
  sold_points: number | null;
  sold_paid_points: number | null;
  avg_sold_points: number | null;
  avg_sold_minutes: number | null;
  avg_sold_hour: number | null;
  /** 売上金額(円) = sold_paid_points * revenue_rate（バックエンドで算出）。 */
  revenue_yen: number;
};

export type NetkeirinSalesResponse = {
  items: NetkeirinSalesItem[];
  period_summary: {
    total_stake: number;
    total_payout: number;
    recovery_rate_pct: number | null;
    total_sold_points: number;
    total_sold_paid_points: number;
    total_n_sold: number;
    /** 販売有償ptに対する予想家取り分（0.30）。 */
    revenue_rate: number;
    /** 期間の売上金額(円) = total_sold_paid_points * revenue_rate。 */
    total_revenue_yen: number;
  };
};

export async function fetchNetkeirinSales(
  fromDate?: string,
  toDate?: string,
): Promise<NetkeirinSalesResponse> {
  const params = new URLSearchParams();
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  const qs = params.toString();
  return get<NetkeirinSalesResponse>(
    `/keirin/netkeirin-sales${qs ? `?${qs}` : ""}`,
    { cache: "no-store" },
  );
}

// ---------------------------------------------------------------------------
// netkeirin 売上 × 成績の相関分析（2026-08-11）
// ---------------------------------------------------------------------------
// ⚠️ 率はすべて **0〜1 の小数**（%ではない）。表示側で ×100 すること。
//    サイトの列が % 表記なので取り違えやすい。

/** 開催時間帯。判定の正本は backend `api/keirin_meeting.py`。 */
export type KeirinMeetingType = "morning" | "day" | "nighter" | "midnight";

/**
 * 入稿の出自（`keirin.netkeirin_submissions.origin`）。
 *
 * 🔴 **rank_key では経路を判別できない。** 看板レースの穴埋め入稿は
 *    keirin `submit_marquee_wt.py` の `RANK_BY_CARS={7:"7A",9:"9C"}` により
 *    7A/9A を名乗るため、ランク別集計にはゲート通過分と穴埋めが混ざる
 *    （実測 2026-08-01〜08-10 で 7A 入稿52件中49件＝94%が穴埋め）。
 * - `rank`         ゲートを通った自動入稿
 * - `marquee_fill` 看板レースの穴埋め
 * - `manual`       手動入稿
 * - `unknown`      入稿記録と結合できなかった（origin の値ではない）
 */
export type KeirinSubmissionOrigin = "rank" | "marquee_fill" | "manual" | "unknown";

/**
 * 入稿の**経路**。`origin`（呼び出し経路）に「候補があったか」を掛け合わせたもの。
 *
 * 🔴 origin だけでは**失敗モードが2つ混ざる**（2026-08-11 に実際に誤読した）。
 * - `gate`         ゲートを通った入稿
 * - `renamed`      候補はあったのに**別ランク名義で**入稿された
 * - `no_candidate` 候補が一切ない真の穴埋め
 * - `unknown`      入稿記録なし
 */
export type KeirinSubmissionRoute = "gate" | "renamed" | "no_candidate" | "unknown";

export type KeirinSalesDailyPoint = {
  date: string;
  n_predictions: number;
  /** 的中（ガミ含む）＝買い目が当たった数。 */
  n_hits_incl_garami: number;
  /** 的中（ガミ除く）＝払戻＞賭け金だった数。netkeirin 表示の「的中」はこちら。 */
  n_hits_excl_garami: number;
  n_garami: number;
  hit_rate_incl: number | null;
  hit_rate_excl: number | null;
  /** 的中のうちガミだった割合。的中0件の日は null（0%ではない）。 */
  garami_rate: number | null;
  n_sold: number;
  sold_points: number;
  /** 販売有償pt。収益になるのはこちらだけ。 */
  sold_paid_points: number;
  stake_amount: number;
  payout_amount: number;
  recovery_rate: number | null;
};

export type KeirinSalesRacePoint = {
  race_id: string;
  race_key: string;
  date: string;
  venue_code: string;
  venue_name: string | null;
  race_no: number;
  label: string | null;
  /** 入稿ランク（7S/7A/…）。入稿記録が無ければ null。 */
  rank: string | null;
  /** 入稿の出自。⚠️ ランクだけで経路を判断しないこと。 */
  origin: KeirinSubmissionOrigin;
  /** そのレースに立っていた候補ランク（"7C" / "7B,7C"）。無ければ null。 */
  detected_ranks: string | null;
  /** 入稿経路（出自 × 候補の有無）。 */
  route: KeirinSubmissionRoute;
  meeting_type: KeirinMeetingType | null;
  hit: boolean;
  hit_excl_garami: boolean;
  is_garami: boolean;
  n_sold: number;
  sold_points: number;
  sold_paid_points: number;
  stake_amount: number;
  payout_amount: number;
  recovery_rate: number | null;
  /** 締切の何時間前に売れたか（0=締切直前）。 */
  lead_hours: number | null;
  lead_minutes: number | null;
};

export type KeirinSalesAnalysisResponse = {
  from_date: string;
  to_date: string;
  summary: {
    n_days: number;
    n_races: number;
    n_predictions: number;
    n_hits_incl_garami: number;
    n_hits_excl_garami: number;
    n_garami: number;
    hit_rate_incl: number | null;
    hit_rate_excl: number | null;
    garami_rate: number | null;
    n_sold: number;
    sold_points: number;
    sold_paid_points: number;
    stake_amount: number;
    payout_amount: number;
    recovery_rate: number | null;
    latest: (KeirinSalesDailyPoint & {
      /** 前日比。初日は null。 */
      delta: Record<"sold_paid_points" | "sold_points" | "n_sold" | "n_predictions", number> | null;
    }) | null;
  };
  daily: KeirinSalesDailyPoint[];
  races: KeirinSalesRacePoint[];
  /** 標本不足・分散ゼロなら null。 */
  correlations: Record<
    | "n_races_x_hit_rate" | "n_races_x_n_sold" | "n_races_x_sales"
    | "hit_rate_x_sales" | "race_sales_x_hit" | "race_buyers_x_hit",
    number | null
  >;
  link_check: {
    date: string;
    recent_days: number;
    baseline_from: string;
    baseline_to: string;
    metrics: Record<string, { latest: number; recent_avg: number; delta_ratio: number | null }>;
    linked: boolean;
  } | null;
  /** リードタイム(時間)ごとの売上pt。時間帯キーは存在するものだけ入る。 */
  leadtime: Array<{ lead_hours: number } & Partial<Record<KeirinMeetingType | "unknown", number>>>;
  by_rank: Array<KeirinSalesBucket & { rank: string; by_origin: KeirinSalesOriginBucket[] }>;
  /** 出自別（ゲート通過 / 穴埋め / 手動）の内訳。 */
  by_origin: KeirinSalesOriginBucket[];
  /** 経路別（ゲート通過 / 名義違い / 真の穴埋め）の内訳。 */
  by_route: Array<KeirinSalesBucket & { route: KeirinSubmissionRoute }>;
  revenue_rate: number;
};

/** 売上×成績の集計バケット（ランク別・出自別で共通）。 */
export type KeirinSalesBucket = {
  n_races: number;
  n_hits: number;
  n_garami: number;
  n_sold: number;
  sold_paid_points: number;
  stake_amount: number;
  payout_amount: number;
  hit_rate: number | null;
  garami_rate: number | null;
  recovery_rate: number | null;
  /** 期間全体の売上に占める割合（0〜1）。 */
  sales_share: number | null;
};

export type KeirinSalesOriginBucket = KeirinSalesBucket & { origin: KeirinSubmissionOrigin };

export async function fetchKeirinSalesAnalysis(
  fromDate?: string,
  toDate?: string,
): Promise<KeirinSalesAnalysisResponse> {
  const params = new URLSearchParams();
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  const qs = params.toString();
  return get<KeirinSalesAnalysisResponse>(
    `/keirin/netkeirin-analysis${qs ? `?${qs}` : ""}`,
    { cache: "no-store" },
  );
}

// ---------------------------------------------------------------------------
// 実際に売った商品の成績（2026-08-15）
//
// picks_history は**ペーパー成績**（各ランクが条件を満たした全レース）だが、
// netkeirin で売れるのは **1レース1商品**。母集団が違うので、picks_history を
// いくら足しても「いくら売って、いくら返ってきたか」は出ない
// （実測: 入稿472件のうち250件＝53% に picks_history 行が無い）。
// こちらは情報源を netkeirin_submissions + bet_detail だけに固定した別系統。
// ---------------------------------------------------------------------------
export type KeirinSoldSummary = {
  n_races: number;
  n_hits: number;
  /** ガミ（払戻<賭け金）を除いた的中。**netkeirin の表示的中率はこちら**。 */
  n_net_hits: number;
  hit_rate: number | null;
  net_hit_rate: number | null;
  gami_rate: number | null;
  total_bet: number;
  total_payout: number;
  roi: number | null;
  median_payout: number | null;
};

export type KeirinSoldPerformanceResponse = {
  from_date: string;
  to_date: string;
  group_by: string;
  total: KeirinSoldSummary;
  items: (KeirinSoldSummary & { key: string })[];
  /** 買い目が記録されていない入稿（bet_detail の保存は 2026-08-07 開始）。 */
  missing_bet_detail: number;
};

export async function fetchKeirinSoldPerformance(
  fromDate?: string,
  toDate?: string,
  groupBy: "rank" | "date" | "origin" = "rank",
): Promise<KeirinSoldPerformanceResponse> {
  const params = new URLSearchParams();
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  params.set("group_by", groupBy);
  return get<KeirinSoldPerformanceResponse>(
    `/keirin/sold-performance?${params.toString()}`,
    { cache: "no-store" },
  );
}

// ---------------------------------------------------------------------------
// 入稿案の確認（2026-08-11）
// ---------------------------------------------------------------------------
/** `keirin.netkeirin_submissions` の状態。 */
/** 入稿の状態。netkeirin は「入稿（下書きとして送る）」と「公開」が別操作。
 *
 *   proposed（未入稿）→ submitted（入稿済＝公開待ち）→ published（公開済）
 *                                   ↘ deleted（取消・論理削除）
 *
 * 🔴 `published` は不可逆（netkeirin の文言「公開後は修正できなくなります」）。 */
/** 入稿1件の確定成績。**未確定は null**（0円と区別する）。 */
export type KeirinProposalResult = {
  bet: number;
  payout: number;
  /** 買い目が当たった。 */
  hit: boolean;
  /** 払戻 >= 賭け金。🔴 **netkeirin の表示的中率はこちら**（ガミを不的中と数える）。 */
  net_hit: boolean;
};

/** 当日サマリー。netkeirin の「回収率 / 的中率 / 予想数 / 購入 / 払戻 / 収支」に合わせる。
 *
 * 🔴 **集計は確定した分だけ**（netkeirin と同じ）。`n_races` も確定数で、
 *    未確定は `n_pending` に分けてある。混ぜると回収率が誤読される。 */
export type KeirinProposalSummary = {
  /** 予想数＝**確定した**レース数（netkeirin と同じ数え方）。 */
  n_races: number;
  /** まだ確定していないレース数。**分母には入らない**が画面には必ず出す。 */
  n_pending: number;
  bet: number;
  payout: number;
  balance: number;
  recovery_rate: number | null;
  /** ガミ（払戻<投資）を不的中と数える（netkeirin の表示と同じ）。 */
  hit_rate: number | null;
};

export type KeirinProposalStatus = "proposed" | "submitted" | "published" | "deleted";

export interface KeirinProposalEntry {
  frame_no: number;
  name: string | null;
  race_point: number | null;
  style: string | null;
  line_group: number | null;
  line_pos: number | null;
  player_class: string | null;
  prediction_mark: number | null;
  /** モデルの1着率（%） */
  pred_win_pct: number | null;
  /** モデルの2着内率（%）。列追加（2026-08-12）以降のレースのみ値が入る */
  pred_top2_pct: number | null;
  /** モデルの3着内率（%） */
  pred_top3_pct: number | null;
  /** 確定着順。**発走前は null、欠車・失格は 0**（着外）。
   *  🔴 0 と null を同じに扱わないこと。「まだ走っていない」と
   *     「走ったが着外」が区別できなくなる。 */
  finish_order: number | null;
}

export interface KeirinProposal {
  race_key: string;
  rank_key: string;
  /** 入稿の出自。`marquee_fill` はゲートを通っていない穴埋め商品。 */
  origin: KeirinSubmissionOrigin;
  status: KeirinProposalStatus;
  session: string | null;
  venue_name: string;
  race_no: number;
  grade: string | null;
  race_type: string | null;
  is_marquee: boolean;
  /** 勝負アイコン「自信あり」に選ばれた1レース。netkeirin は1日1つしか付けられず、
   *  当日全レースの期待値（予測オッズ × PLの三連複的中率）で1件だけ選ばれる。 */
  is_confident: boolean;
  /** 「自信あり」の選定に使った期待値（全点を予測オッズで統一）。
   *  🔴 `expected_value`（板由来）とは別物。夜開催は朝の板が育っていないため、
   *  終日を同じ土俵で比べるにはこちらを使う。 */
  confident_ev: number | null;
  start_at: number | null;
  n_entries: number | null;
  axis1: number | null;
  axis2: number | null;
  title: string | null;
  comment: string | null;
  bet_detail: {
    total: number;
    source: string | null;
    // 買い目の1点。⚠️ 一覧側（KeirinSubmittedBetLine）と**同じ形**にすること。
    // 別々に書いていたため odds_source の追加が片側だけになり型エラーで気づいた。
    lines: KeirinSubmittedBetLine[];
  } | null;
  /**
   * 見込み回収率（1.0 で収支トントン）。オッズが1点でも欠けると null。
   *
   * ⚠️ **購入判断の根拠に使わないこと。** 競輪の市場は効率的で、モデル由来の
   *    期待値による選別は繰り返し否定されている。異常値の検知が目的で、
   *    実用上は「最低払戻がガミ域に入っていないか」を見るほうが確実。
   */
  expected_value: number | null;
  /* 🔴 `mean_payout` / `cheap_mean_payout` は 2026-08-26 に削除した。
        平均払戻が安いレースは**入稿データを作る時点で自動的に見送る**ように
        なり（`keirin/src/stake_allocation.py::MIN_MEAN_PAYOUT`）、画面から
        取り消す口が無くなったため。**画面へ戻さないこと**——見えると
        「まだ手で落とす余地がある」と読まれ、自動ゲートの死活が濁る。 */
  /** 当たったときの最低払戻（円）。オッズが1点でも欠けると null。 */
  min_payout: number | null;
  /** 当たったときの最高払戻（円）。 */
  max_payout: number | null;
  /** **下振れしても**割らない最低払戻（円）。`odds_low` が全点に無ければ null。
   *  🔴 `min_payout` は入稿時点の板由来で楽観的（当たったとき実際より高い額を
   *     約束していた）。承認判断はこちらを見ること。 */
  min_payout_low: number | null;
  /** 最低払戻が投資額を下回る＝当たってもガミになりうる。
   *  下限側（`min_payout_low`）が取れていればそちらで判定している。 */
  gami_risk: boolean | null;
  /** ガミ判定に下限側を使えたか。false なら板由来＝楽観的な判定。 */
  gami_risk_is_conservative?: boolean;
  /** 確定成績。**未確定（発走前・確定待ち）は null**。 */
  result?: KeirinProposalResult | null;
  /**
   * **取消したレースを売っていたら**の確定成績（2026-08-24）。取消以外は null。
   *
   * 🔴 **実績ではない。** `result` とは別のキーで渡すのは、あちらが
   *    netkeirin の成績とサマリーの回収率の元になるため——混ぜると売って
   *    いないものが実績になる。画面は文言で必ず区別する。
   * 🔴 採点は `summary_cancelled` と**同じ経路（確定オッズ）**。以前カード側は
   *    `bet_detail` の入稿時点オッズで計算しており、同じレースでサマリーと
   *    数字が合わなかった（実測 16,910円 ↔ 20,710円）。
   */
  result_if_sold?: KeirinProposalResult | null;
  /** 最低払戻・最高払戻・期待値に予測オッズが混ざっているか。
   *  🔴 混ざっているのに黙って出すと「実際の板でこの払戻」と読まれる。 */
  odds_has_predicted?: boolean;
  /**
   * レース信頼度指標 — 出走者の落車性向（point-in-time・経験ベイズ縮約）の平均。
   *
   * 実測（2026-08-21・7C ゲート通過 6,425R の四分位）: 軸2車のどちらかが落車する率が
   * Q1(安全) 1.56% → Q4(危険) 3.11% と**約2倍**になる。
   *
   * 🔴 **判断材料であってゲートではない。** 危険帯は二軸的中がほぼ変わらない一方で
   *    ROI が最も高い（Q4 78.1% vs Q1 71.6%）ため、自動で落とすと回収率の高い
   *    四分位を捨てることになる。表示だけに使う。
   */
  /**
   * レース信頼度（0〜100 の整数・四捨五入）。100% ＝ 上位2車の3着内率の合計が
   * 2.00（軸2車がどちらも確実に3着以内）。
   *
   * 🔴 **ランクのゲートが見ているのと同じ量**なので、出る／出ないの理由が
   *    画面から読める（7C は 72% 相当・9C は 65% 相当が下限）。
   *    判定の正本は keirin 側 `src/p3_calibration.confidence_pct`。
   */
  confidence_pct?: number | null;
  /**
   * 信頼度が見ている2車のうち**何車が3着以内に入ったか**（0 / 1 / 2）。未確定なら null。
   * 表示は 2→○ / 1→△ / 0→×。
   *
   * 🔴 **買い目の的中とは別物。** 相手が外れても二軸はそろっていることがあるので、
   *    ○ なのに不的中、は普通に起きる。
   */
  confidence_hit_count?: number | null;

  crash_risk?: number | null;
  /** `crash_risk` の区分。low=安全 / mid / high=危険 / unknown=算出できず。 */
  crash_risk_band?: "low" | "mid" | "high" | "unknown";
  /**
   * 確定後の当たり目。`bet_detail.lines[].combo` とそのまま比較できる表記
   * （三連複 `1=3=4` / 三連単 `4-3-1`）。**未確定は空配列**。
   *
   * 🔴 **同着では複数になる**（3着同着で三連複2通り・1着/2着同着で三連単2通り）。
   *    判定はサーバー（`services/keirin_result_top3.py`）が持つので、
   *    画面は**この配列に入っているかを見るだけ**にすること。実着順から
   *    組み立て直すと同着で必ず取りこぼす。
   */
  winning_combos?: string[];
  netkeirin_race_id: string | null;
  proposed_at: string | null;
  approved_at: string | null;
  deleted_at: string | null;
  /** なぜ取り消したか（画面のボタンごとの固定文言）。
   *  2026-08-25 より前の取消は記録が無いので null。 */
  cancel_reason?: string | null;
  entries: KeirinProposalEntry[];
}

export interface KeirinProposalsResponse {
  date: string;
  n_proposed: number;
  /** 未公開＝netkeirin へ送ったが公開していない件数（**自前の記録**）。
   *  ⚠️ netkeirin の画面から人が直接公開すると submitted のまま取り残されるので
   *     実数と食い違いうる。netkeirin 側の実数は別 API で取る。 */
  n_unpublished?: number;
  /** 当日サマリー（netkeirin の表示と項目を合わせたもの）。 */
  summary?: KeirinProposalSummary;
  /**
   * **取り消したレースを、そのまま売っていたら**の参考値（2026-08-24）。
   *
   * 🔴 **実績ではない。** 売っていないので netkeirin の成績にも `summary` にも
   *    入らない。落とした判断が正しかったかを見るためだけの数字。
   * ⚠️ 母集団は**取り消したレースだけ**（入稿前は含めない）。採点は実績と
   *    同じ経路（確定オッズ）なので、両者は同じ土俵で比べられる。
   */
  summary_cancelled?: KeirinProposalSummary;
  items: KeirinProposal[];
}

export async function fetchKeirinProposals(date: string): Promise<KeirinProposalsResponse> {
  return get<KeirinProposalsResponse>(`/keirin/proposals?date=${date}`, { cache: "no-store" });
}

/**
 * 未承認バッジ用の**件数だけ**を取る（2026-08-23 新設）。
 *
 * 🔴 **バッジのために `fetchKeirinProposals` を呼んではいけない。**
 *    あちらは全レースの買い目・出走表・落車リスクを含み、本番実測で
 *    **201〜282KB / 約3秒**。トップページ表示の支配項になっていた。
 */
export async function fetchKeirinProposalsCount(date: string): Promise<{ n_proposed: number }> {
  return get<{ n_proposed: number }>(`/keirin/proposals/count?date=${date}`, { cache: "no-store" });
}

export async function fetchKeirinApprovalMode(): Promise<{ require_approval: boolean }> {
  return get<{ require_approval: boolean }>("/keirin/approval-mode", { cache: "no-store" });
}

// ---------------------------------------------------------------------------
// 型ラボ（検証用・2026-08-27）
// 🔴 既存の keirin 一覧・統計とは**別テーブル**（keirin.type_lab_picks）を見る。
//    既存商品の全面置き換えを想定した設計の、ペーパー検証と実地検証の確認窓口。
//    設計と実測は keirin/docs/type_lab/SUMMARY.md
// ---------------------------------------------------------------------------
export type TypeLabLeg = {
  combo: string; stake: number; pred_odds: number; prob: number;
};
export type TypeLabCurrentPick = {
  rank: string; pred_combo: string | null; n_combos: number | null;
  bet_amount: number | null; hit: boolean | null; payout: number | null;
  settled: boolean; sold_rank_key: string | null;
};
export type TypeLabComparisonRow = {
  plan_key: string; n_races: number; n_days: number;
  lab_shown_hit: number; cur_shown_hit: number;
  lab_median_payout: number; cur_median_payout: number;
  lab_two_per_day: number; cur_two_per_day: number;
  lab_roi: number; cur_roi: number;
};
export type TypeLabPick = {
  race_key: string; race_date: string; venue_name: string | null;
  race_no: number | null; race_type: string | null; day_index: number | null;
  type_label: string; axis_sum: number | null; arare: number | null;
  axis1: number | null; axis2: number | null;
  mode: string; plan_key: string; bet_type: string;
  n_legs: number; budget: number; legs: TypeLabLeg[];
  pred_mean_payout: number | null; pred_min_payout: number | null;
  settled: boolean; win_combo: string | null; hit: boolean | null;
  payout: number | null; final_odds: number | null;
  current: TypeLabCurrentPick | null;
};
export type TypeLabSummary = {
  plan_key: string; type_label: string; bet_type: string;
  n: number; n_days: number; per_day: number;
  n_settled: number; n_hit: number; n_gami: number;
  hit_rate: number; shown_hit_rate: number; gami_rate: number;
  median_payout: number; median_pred_mean: number;
  two_plus_per_day: number; big_per_day: number;
  invested: number; returned: number; roi: number;
};
export type TypeLabResponse = {
  mode: string; date_from: string; date_to: string;
  rule_versions: string[];
  venues: string[]; venue: string | null;
  summaries: TypeLabSummary[]; comparison: TypeLabComparisonRow[];
  picks: TypeLabPick[];
};

export async function fetchKeirinTypeLab(params: {
  mode?: "paper" | "live"; dateFrom?: string; dateTo?: string;
  venue?: string; limit?: number;
} = {}): Promise<TypeLabResponse> {
  const q = new URLSearchParams();
  if (params.mode) q.set("mode", params.mode);
  if (params.dateFrom) q.set("date_from", params.dateFrom);
  if (params.dateTo) q.set("date_to", params.dateTo);
  if (params.venue) q.set("venue", params.venue);
  if (params.limit) q.set("limit", String(params.limit));
  const s = q.toString();
  return get<TypeLabResponse>(`/keirin/type-lab${s ? `?${s}` : ""}`, { cache: "no-store" });
}
