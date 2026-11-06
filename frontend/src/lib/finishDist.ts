/**
 * 中央の着順分布（能力分布モデル R1）の型と取得・表示用の関数。
 *
 * 正本はバックエンド `services/jra_finish_dist.py`（勝率と複勝率の両方に合う上位3着の同時分布）。
 * 計画: docs/prediction_flow_redesign_plan_2026_11_06.md §5.5（前向き確認 2026-09 で合格）。
 *
 * `lib/api.ts` は全柱共通（shared）なので、中央だけが使う型はここに置く。
 */
import type { HorseIndex } from "@/lib/api";

const _rawBase =
  typeof window === "undefined"
    ? (process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
const BASE_URL = _rawBase.replace(/\/api\/?$/, "").replace(/\/$/, "") + "/api";

export type FinishDistKind = "trio" | "quinella" | "wide";

export type FinishDistCombo = {
  horses: number[];
  probability: number;
};

export type FinishDist = {
  race_id: number;
  method_version: string;
  /** "r1"（能力分布モデル）/ "harville"（当てはめ失敗・5頭未満で落とした） */
  source: string;
  fit_status: string;
  place_slots: number;
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

/** `/indices` の連対率（`top2_probability`）。古いバックエンドでは無いので null。 */
export function top2Of(h: HorseIndex): number | null {
  const v = (h as HorseIndex & { top2_probability?: number | null }).top2_probability;
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

export async function fetchFinishDistBrowser(raceId: number, limit = 10): Promise<FinishDist | null> {
  const res = await fetch(`${BASE_URL}/races/${raceId}/finish-dist?limit=${limit}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`finish-dist ${res.status}`);
  return (await res.json()) as FinishDist;
}
