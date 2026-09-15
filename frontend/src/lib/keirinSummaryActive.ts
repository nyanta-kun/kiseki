/**
 * 競輪サマリーの「直近で売っていないプラン」を隠す判定（2026-09-15）。
 *
 * ユーザー要望「無効にしたモデルについては表示、集計から通常表示では外して」。
 * 「無効」＝直近14日（商品差し替え日 2026-09-15 より前は数えない）に売っていない
 * プラン。判定そのものは backend `services/keirin_active_ranks.py` が行い、
 * API は `inactive_ranks`（隠すラベル）と `all`（全部込みの合計）を返す。
 * ここはそれを画面の状態（「無効も表示」トグル）に合わせて選ぶだけ。
 *
 * 🔴 どちらのフィールドも**無ければ絞らない**（fail-open）。古い API に当たったとき
 *    合計やランク別が消えないようにするため（`visible_ranks` と同じ規約）。
 */

export type SummaryPeriodKey = "today" | "month" | "year";

/** ランク別展開の行順から、非アクティブを外す（トグル ON なら外さない）。 */
export function filterInactiveRanks(
  order: readonly string[],
  inactive: readonly string[] | undefined,
  showInactive: boolean,
): readonly string[] {
  if (showInactive || !inactive || inactive.length === 0) return order;
  const hidden = new Set(inactive);
  return order.filter((r) => !hidden.has(r));
}

/** `order` のうち非アクティブとして隠れる行の数（「無効 N」の N）。 */
export function countHiddenInactive(
  order: readonly string[],
  inactive: readonly string[] | undefined,
): number {
  if (!inactive || inactive.length === 0) return 0;
  const hidden = new Set(inactive);
  return order.filter((r) => hidden.has(r)).length;
}

/**
 * 表示する期間集計を選ぶ。
 *
 * 既定（トグル OFF）は API の既定フィールド＝直近で売っているプランだけの合計。
 * トグル ON なら `all` の合計に、既定側の `by_rank`（全プラン）を組み合わせる
 * （`all` 側は by_rank を持たない）。`all` が無い古い API なら既定のまま。
 */
export function pickSummaryPeriod<T extends { by_rank?: unknown }>(
  active: T,
  all: Omit<T, "by_rank"> | undefined,
  showInactive: boolean,
): T {
  if (!showInactive || !all) return active;
  return { ...active, ...all, by_rank: active.by_rank } as T;
}
