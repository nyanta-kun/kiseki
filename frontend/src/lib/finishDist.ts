/**
 * 着順分布（能力分布モデル R1）の型と取得・表示用の関数（中央・地方の両方）。
 *
 * 正本はバックエンド。中央 `services/jra_finish_dist.py`（複勝は払戻対象着順以内）、
 * 地方 `services/chihou_finish_dist.py`（3着以内）。返す形は同じで、当てはめの段数の名前だけ違う
 * （中央 `place_slots` / 地方 `k`）。
 * 計画: docs/prediction_flow_redesign_plan_2026_11_06.md §5.5（中央）・§5.6（地方）。
 *
 * `lib/api.ts` は全柱共通（shared）なので、ここに置く。
 */
import type { ChihouHorseIndex, HorseIndex } from "@/lib/api";

const _rawBase =
  typeof window === "undefined"
    ? (process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
const BASE_URL = _rawBase.replace(/\/api\/?$/, "").replace(/\/$/, "") + "/api";

export type FinishDistKind = "trio" | "quinella" | "wide";

/** どちらの API から取るか。 */
export type FinishDistScope = "jra" | "chihou";

export type FinishDistCombo = {
  horses: number[];
  probability: number;
};

export type FinishDist = {
  race_id: number;
  method_version: string;
  /** "r1"（能力分布モデル）/ "harville"（当てはめ失敗・頭数不足で落とした） */
  source: string;
  fit_status: string;
  /** 中央のみ: 複勝の払戻対象着順（0 = 5頭未満で複勝なし） */
  place_slots?: number;
  /** 地方のみ: 当てはめた上位 k 着（0 = 4頭未満） */
  k?: number;
  horses: { horse_number: number; win: number; top2: number; top3: number }[];
  quinella: FinishDistCombo[];
  wide: FinishDistCombo[];
  trio: FinishDistCombo[];
};

export const FINISH_DIST_KINDS: { key: FinishDistKind; label: string }[] = [
  { key: "trio", label: "三連複" },
  { key: "quinella", label: "馬連" },
  { key: "wide", label: "ワイド" },
];

/** `/indices` の連対率（`top2_probability`）。中央・地方どちらの馬でも可。古いバックエンドでは無いので null。 */
export function top2Of(h: HorseIndex | ChihouHorseIndex): number | null {
  const v = (h as { top2_probability?: number | null }).top2_probability;
  return typeof v === "number" ? v : null;
}

/** 組合せの確率は小さいので、10% 未満は小数1桁まで出す。 */
export function comboPct(p: number): string {
  const v = p * 100;
  if (v >= 10) return `${Math.round(v)}%`;
  if (v >= 0.1) return `${v.toFixed(1)}%`;
  return "<0.1%";
}

/** 券面と同じ並び（昇順・ハイフン区切り）。 */
export function formatCombo(horses: number[]): string {
  return [...horses].sort((a, b) => a - b).join("-");
}

/** Harville に落ちた理由の注記。能力分布モデルで出せたときは null。 */
export function harvilleNote(d: Pick<FinishDist, "source" | "place_slots" | "k">): string | null {
  if (d.source === "r1") return null;
  if (d.place_slots === 0) return "5頭未満で複勝が無いため、勝率だけから求めた値（Harville）です。";
  if (d.k === 0) return "4頭未満のため、勝率だけから求めた値（Harville）です。";
  return "このレースは計算が収束しなかったため、勝率だけから求めた値（Harville）です。";
}

export function finishDistPath(scope: FinishDistScope, raceId: number, limit: number): string {
  const base = scope === "chihou" ? "/chihou/races" : "/races";
  return `${base}/${raceId}/finish-dist?limit=${limit}`;
}

export async function fetchFinishDistBrowser(
  raceId: number,
  limit = 10,
  scope: FinishDistScope = "jra",
): Promise<FinishDist | null> {
  const res = await fetch(`${BASE_URL}${finishDistPath(scope, raceId, limit)}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`finish-dist ${res.status}`);
  return (await res.json()) as FinishDist;
}
