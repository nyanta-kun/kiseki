/**
 * 平八バッジのしきい値 — 画面表示の単一真実源 [[jra_heihachi_badge]]
 *
 * バッジ条件（既定値）: 平地OP特別以上 ∧ 指数順位3位以内 ∧ 単勝10〜40倍
 * ∧ 複勝確率30%以上。既定値の出所はバックエンド
 * `backend/src/indices/dm_signals.py` の HEIHACHI_* 定数で、推奨ページの
 * `/races/heihachi` レスポンス `defaults` として配信される。
 *
 * ユーザーが推奨ページのスライダーで動かした値は localStorage に入り、
 * **推奨ページの一覧・回収率にも、レース詳細のバッジ表示にも同じ値が効く**。
 * サーバー側 `dm_signals.compute_dm_signals()` が付ける "平八" タグは
 * 既定値のままで、API/LLM プロンプト用の既定として残している（表示は本モジュールが上書きする）。
 */

/** バッジのタグ文字列。DmSignalBadges の DM_SIGNAL_META のキーと一致させること。 */
export const SIGNAL_HEIHACHI = "平八";

export type HeihachiThresholds = {
  /** 指数順位の上限（この順位まで対象） */
  maxIndexRank: number;
  /** 単勝オッズ下限（含む） */
  minOdds: number;
  /** 単勝オッズ上限（含まない） */
  maxOdds: number;
  /** 複勝確率の下限（0〜1） */
  minPlaceProb: number;
  /** true: OP特別以上のみ / false: 全レース */
  gradedOnly: boolean;
};

/** バックエンド既定値のフォールバック（API が落ちているときだけ使う）。 */
export const HEIHACHI_FALLBACK_DEFAULTS: HeihachiThresholds = {
  maxIndexRank: 3,
  minOdds: 10,
  maxOdds: 40,
  minPlaceProb: 0.3,
  gradedOnly: true,
};

/** スライダーの可動域。maxIndexRank の上限は API が返す候補の絞り込み幅と揃えること。 */
export const HEIHACHI_RANGES = {
  maxIndexRank: { min: 1, max: 5, step: 1 },
  minOdds: { min: 1, max: 30, step: 0.5 },
  maxOdds: { min: 10, max: 100, step: 1 },
  minPlaceProb: { min: 0, max: 0.6, step: 0.01 },
} as const;

const STORAGE_KEY = "heihachi_thresholds_v1";
/** 同一タブ内の他コンポーネントへ変更を伝えるイベント（storage は別タブにしか飛ばない）。 */
const CHANGE_EVENT = "heihachi-thresholds-change";

/** 平地OP特別以上とみなす grade（backend HEIHACHI_GRADES と一致させること）。 */
const GRADED = new Set(["OP特別", "Listed", "G3", "G2", "G1", "重賞"]);

export function isGradedRace(grade: string | null | undefined): boolean {
  return grade !== null && grade !== undefined && GRADED.has(grade);
}

/** バッジ判定の対象になりうる1頭ぶんの最小情報。 */
export type HeihachiSubject = {
  grade: string | null | undefined;
  indexRank: number | null | undefined;
  winOdds: number | null | undefined;
  placeProbability: number | null | undefined;
};

/**
 * 平八バッジ該当かどうか。推奨ページの一覧もレース詳細のバッジも必ずこれを通す。
 * どれか1つでも欠損していたら false（判定不能を「該当」にしない）。
 */
export function matchesHeihachi(s: HeihachiSubject, t: HeihachiThresholds): boolean {
  if (t.gradedOnly && !isGradedRace(s.grade)) return false;
  if (s.indexRank === null || s.indexRank === undefined) return false;
  if (s.winOdds === null || s.winOdds === undefined) return false;
  if (s.placeProbability === null || s.placeProbability === undefined) return false;
  return (
    s.indexRank <= t.maxIndexRank &&
    s.winOdds >= t.minOdds &&
    s.winOdds < t.maxOdds &&
    s.placeProbability >= t.minPlaceProb
  );
}

function sanitize(raw: unknown, base: HeihachiThresholds): HeihachiThresholds {
  if (typeof raw !== "object" || raw === null) return base;
  const r = raw as Record<string, unknown>;
  const num = (v: unknown, fallback: number) =>
    typeof v === "number" && Number.isFinite(v) ? v : fallback;
  const t: HeihachiThresholds = {
    maxIndexRank: num(r.maxIndexRank, base.maxIndexRank),
    minOdds: num(r.minOdds, base.minOdds),
    maxOdds: num(r.maxOdds, base.maxOdds),
    minPlaceProb: num(r.minPlaceProb, base.minPlaceProb),
    gradedOnly: typeof r.gradedOnly === "boolean" ? r.gradedOnly : base.gradedOnly,
  };
  // 下限が上限を追い越した保存値で「常に0件」になるのを防ぐ
  if (t.minOdds >= t.maxOdds) return { ...t, minOdds: base.minOdds, maxOdds: base.maxOdds };
  return t;
}

/**
 * localStorage の生文字列を型に落とす。未保存・壊れた値のときは defaults を返す。
 *
 * 文字列を受け取る形にしているのは `useSyncExternalStore` のスナップショットを
 * 「参照が安定する生文字列」にするため（オブジェクトを返すと毎回再レンダになる）。
 */
export function parseThresholds(
  raw: string | null,
  defaults: HeihachiThresholds,
): HeihachiThresholds {
  if (!raw) return defaults;
  try {
    return sanitize(JSON.parse(raw), defaults);
  } catch {
    return defaults;
  }
}

export function saveThresholds(t: HeihachiThresholds): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(t));
  } catch {
    // 保存できなくても表示は続ける（このセッション内では state が真実源）
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

export function clearThresholds(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // noop
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

export const HEIHACHI_CHANGE_EVENT = CHANGE_EVENT;
export const HEIHACHI_STORAGE_KEY = STORAGE_KEY;
