/**
 * POG ドラフトの確定規則。
 *
 * 🔴 **ここを間違えると春のドラフトが壊れる。** 次に実地で動くのは 2027年春で、
 * 壊れていても半年以上気づけない。だから移設元（sekito `PogDraftPage.tsx` の
 * `toggleConfirmation`）から**規則をそのまま写し**、純関数にして固定する。
 *
 * ## 用語
 *
 *     draft_order   指名の巡（1巡目、2巡目…）
 *     pick_order    **その人の何頭目か**（チーム内の枠番）。0 = その巡は取れなかった
 *     displayOrder  いま全員が取り組んでいる「何頭目」の段階
 *
 * 🔴 **`pick_order` は巡の番号ではない。** 2026年度の実データがそれを示している:
 *
 *     1巡目  A=1  B=0（Aと同じ馬で負け）  C=1（別の馬）
 *     2巡目  B=1  ← 巡は 2 だが、B にとっては 1 頭目
 *
 * ## 段階（displayOrder）の進み方
 *
 * 前の巡までに `pick_order = i` を持つ人が**参加者全員**揃っていれば `i+1` へ進む。
 * 揃っていなければ `i` のまま。上の例なら 2巡目の時点で 1 頭目を持つのは A と C の
 * 2 人だけなので、2巡目もまだ「1 頭目」の段階。
 */

/** 盤面 1 セル。 */
export type DraftCell = {
  user_id: number;
  draft_order: number;
  pick_order: number | null;
  horse_name: string | null;
  broodmare: string | null;
  netkeiba_horse_id: string | null;
};

/** 確定 API へ送る 1 件。 */
export type ConfirmTarget = { user_id: number; pick_order: number };

/**
 * 馬の同一性を判定する鍵。
 *
 * ⚠️ 移設元と同じく**表示名**で比べる（未命名馬は「母○○」）。`netkeiba_horse_id`
 * で比べたくなるが、**同じ母の別の仔を同名表示するケース**があるため移設元は
 * 名前で束ねていた。変えると重複判定が変わる。
 */
export function horseKey(c: DraftCell): string {
  const n = c.horse_name?.trim();
  return n ? n : `母${c.broodmare ?? ""}`;
}

/**
 * 各巡が「何頭目」の段階かを返す。
 *
 * @param picks 全指名。
 * @param memberCount 参加者の人数。**全員揃って初めて次の段階へ進む**。
 * @returns `draft_order` → 段階（1 起点）。
 */
export function computeDisplayOrders(
  picks: DraftCell[],
  memberCount: number,
): Record<number, number> {
  const draftOrders = [...new Set(picks.map((p) => p.draft_order))].sort((a, b) => a - b);
  const out: Record<number, number> = {};
  for (const d of draftOrders) {
    // その巡より前で確定している pick_order ごとに、誰が持っているかを数える。
    const byOrder = new Map<number, Set<number>>();
    for (const p of picks) {
      if (p.draft_order < d && p.pick_order != null && p.pick_order > 0) {
        if (!byOrder.has(p.pick_order)) byOrder.set(p.pick_order, new Set());
        byOrder.get(p.pick_order)!.add(p.user_id);
      }
    }
    const maxOrder = Math.max(0, ...byOrder.keys());
    let display = 1;
    for (let i = 1; i <= maxOrder; i++) {
      if (byOrder.get(i)?.size === memberCount) {
        display = i + 1;
      } else {
        display = i;
        break;
      }
    }
    out[d] = display;
  }
  return out;
}

/**
 * 「この人が勝ち」を押したときに送る更新内容を組み立てる。
 *
 * 🔴 **送らなかった人は更新されない。** 触ってはいけない人を targets に
 * 入れないこと（別の馬を指名した人まで 0 にすると、その人が指名し直す羽目になる）。
 *
 * @param row その巡の全指名。
 * @param targetUserId 押されたセルの人。
 * @param displayOrder その巡の段階（`computeDisplayOrders` の値）。
 * @returns 更新する人だけの配列。何もしないときは空。
 */
export function computeConfirmTargets(
  row: DraftCell[],
  targetUserId: number,
  displayOrder: number,
): ConfirmTarget[] {
  const target = row.find((c) => c.user_id === targetUserId);
  if (!target) return [];

  const count = new Map<string, number>();
  for (const c of row) count.set(horseKey(c), (count.get(horseKey(c)) ?? 0) + 1);

  const targetKey = horseKey(target);
  const isDuplicate = (count.get(targetKey) ?? 0) > 1;
  const orderValue = target.pick_order;

  // ケース1: 未確定セル → 確定する
  if (orderValue == null) {
    if (isDuplicate) {
      // 同じ馬を指名した人だけ。押した人が勝ち、他は 0。
      return row
        .filter((c) => horseKey(c) === targetKey)
        .map((c) => ({
          user_id: c.user_id,
          pick_order: c.user_id === targetUserId ? displayOrder : 0,
        }));
    }
    // 重複していない馬を指名した人は**まとめて**確定できる。
    return row
      .filter((c) => (count.get(horseKey(c)) ?? 0) === 1)
      .map((c) => ({ user_id: c.user_id, pick_order: displayOrder }));
  }

  // ケース2: 確定済み × 重複あり → その馬名の全員を未確定へ戻す
  //   ⚠️ 「未確定」は API 上 pick_order = null。ここでは 0 と区別が要るので
  //      呼び出し側が null を送れるよう -1 を「未確定」の合図にはしない。
  //      移設元と同じく null を送る（下の `UNSET` を使う）。
  if (orderValue > 0 && isDuplicate) {
    return row
      .filter((c) => horseKey(c) === targetKey)
      .map((c) => ({ user_id: c.user_id, pick_order: UNSET }));
  }

  // ケース3: 却下済み（0）× 重複あり → 勝者を差し替える
  if (orderValue === 0 && isDuplicate) {
    return row
      .filter((c) => horseKey(c) === targetKey)
      .map((c) => ({
        user_id: c.user_id,
        pick_order: c.user_id === targetUserId ? displayOrder : 0,
      }));
  }

  // ケース4: 確定済み × 重複なし → 行全体を未確定へ戻す
  if (orderValue > 0 && !isDuplicate) {
    return row.map((c) => ({ user_id: c.user_id, pick_order: UNSET }));
  }

  // それ以外（却下済みの単独クリックなど）は何もしない。
  return [];
}

/**
 * 「未確定へ戻す」を表す番兵。
 *
 * API は `pick_order` に null を受け付けるが、型を `number` に保ったまま
 * 扱うために -1 を使い、送る直前に null へ直す（`toApiTargets`）。
 */
export const UNSET = -1;

/** API へ送る形（`UNSET` を null に直す）。 */
export function toApiTargets(
  targets: ConfirmTarget[],
): { user_id: number; pick_order: number | null }[] {
  return targets.map((t) => ({
    user_id: t.user_id,
    pick_order: t.pick_order === UNSET ? null : t.pick_order,
  }));
}
