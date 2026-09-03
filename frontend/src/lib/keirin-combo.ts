/**
 * 競輪の買い目をフォーメーション表記へ畳む（2026-09-03 新設）。
 *
 * 🔴🔴 **畳んだ表記が表す集合は、実際に買った集合と完全に一致しなければならない。**
 *    型ラボの `prob_top`（確率上位k点）は任意の目の集合なので、
 *    「1着・2着・3着を独立に掛け合わせる」形にすると**買っていない目が混ざる**
 *    ＝買い目を偽ることになる。だから畳むのは次の2段だけにする:
 *
 *      ① (1着,2着) が同じ目をまとめ、3着を列挙        → `3-7-1,5`
 *      ② ①のうち「1着が同じ ∧ 3着の並びが同じ」行の2着をまとめる → `3-7,1-5,2`
 *
 *    ②で 3着の**並び**（集合ではなく順序つき）が一致することを条件にしているのは、
 *    表示の順序を保ちつつ集合一致を保証するため。
 *
 * ⚠️ この一致は `keirin-combo.test.ts` が**展開して集合比較**で固定している。
 *    「畳み方を強くする」変更は必ずそのテストを通すこと。
 */

/** 三連単の1点（1着・2着・3着の車番）。 */
export type Trifecta = readonly [number, number, number];

/**
 * @param combos 買った目
 * @param sortCars 車番の並べ替え（指数順など）。**集合を変えてはいけない**
 */
export function foldTrifecta(
  combos: readonly Trifecta[],
  sortCars: (cars: number[]) => number[] = (c) => [...c].sort((a, b) => a - b),
): string {
  const byPair = new Map<string, number[]>();
  for (const [a, b, c] of combos) {
    const k = `${a}|${b}`;
    byPair.set(k, [...(byPair.get(k) ?? []), c]);
  }
  type Row = { a: number; bs: number[]; cs: number[] };
  const rows: Row[] = [...byPair.entries()].map(([k, cs]) => {
    const [a, b] = k.split("|").map(Number);
    return { a, bs: [b], cs: sortCars(cs) };
  });
  const merged: Row[] = [];
  for (const r of rows) {
    const hit = merged.find((m) => m.a === r.a && m.cs.join(",") === r.cs.join(","));
    if (hit) hit.bs.push(...r.bs);
    else merged.push({ ...r });
  }
  return merged
    .map((m) => `${m.a}-${sortCars(m.bs).join(",")}-${m.cs.join(",")}`)
    .join(" / ");
}

/** 畳んだ表記を目の集合へ戻す（テストと検算用）。 */
export function expandTrifecta(text: string): Set<string> {
  const out = new Set<string>();
  for (const row of text.split(" / ")) {
    const [a, bs, cs] = row.split("-");
    for (const b of bs.split(",")) {
      for (const c of cs.split(",")) out.add(`${a}-${b}-${c}`);
    }
  }
  return out;
}
