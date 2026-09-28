/**
 * 地方 激走馬一覧の並び替え（純関数）。画面は `components/ChihouGekisouTable.tsx`。
 *
 * ⚠️ 確率順は「当たりやすい順」であって「買うべき順」ではない
 *    （前向き記録で確率の帯ごとの回収率はどれも 0.68〜0.91）。
 */
import type { ChihouGekisouPick } from "./api";

export type GekisouSortMode = "prob" | "time";

/** 発走時刻（未定は最後）→ レース番号 → 競馬場 */
function byTime(a: ChihouGekisouPick, b: ChihouGekisouPick): number {
  const ta = a.post_time ?? "9999";
  const tb = b.post_time ?? "9999";
  if (ta !== tb) return ta < tb ? -1 : 1;
  if (a.race_number !== b.race_number) return a.race_number - b.race_number;
  return a.course_name.localeCompare(b.course_name);
}

/** 確率の高い順（確率なしは最後）→ 発走順 */
function byProb(a: ChihouGekisouPick, b: ChihouGekisouPick): number {
  const pa = a.place_prob ?? -1;
  const pb = b.place_prob ?? -1;
  return pa === pb ? byTime(a, b) : pb - pa;
}

export function sortGekisouPicks(
  picks: ChihouGekisouPick[],
  mode: GekisouSortMode,
): ChihouGekisouPick[] {
  return [...picks].sort(mode === "prob" ? byProb : byTime);
}
