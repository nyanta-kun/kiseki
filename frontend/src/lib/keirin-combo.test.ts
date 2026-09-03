import { describe, expect, it } from "vitest";

import { expandTrifecta, foldTrifecta, type Trifecta } from "./keirin-combo";

/** 畳む前の集合。 */
const asSet = (cs: readonly Trifecta[]) => new Set(cs.map((c) => c.join("-")));

describe("foldTrifecta", () => {
  it("(1着,2着) が同じ目は3着をまとめる", () => {
    const cs: Trifecta[] = [[3, 7, 1], [3, 7, 5]];
    expect(foldTrifecta(cs)).toBe("3-7-1,5");
  });

  it("1着が同じで3着の並びも同じなら2着をまとめる", () => {
    const cs: Trifecta[] = [[3, 7, 1], [3, 7, 5], [3, 2, 1], [3, 2, 5]];
    expect(foldTrifecta(cs)).toBe("3-2,7-1,5");
  });

  it("🔴 1着・2着が入れ替わる目を畳んで着順を偽らない", () => {
    const cs: Trifecta[] = [[1, 2, 3], [2, 1, 3]];
    // 別行になる（`1-2-3 / 2-1-3`）。1行に畳むと着順が壊れる
    expect(expandTrifecta(foldTrifecta(cs))).toEqual(asSet(cs));
    expect(foldTrifecta(cs).split(" / ")).toHaveLength(2);
  });

  it("🔴🔴 どんな集合でも、畳んで展開すると元の集合に戻る", () => {
    // 疑似乱数（seed 固定）で 200 通りの買い目集合を作って往復させる
    let seed = 12345;
    const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    for (let t = 0; t < 200; t++) {
      const k = 2 + Math.floor(rnd() * 14);
      const set = new Set<string>();
      while (set.size < k) {
        const pick: number[] = [];
        while (pick.length < 3) {
          const car = 1 + Math.floor(rnd() * 7);
          if (!pick.includes(car)) pick.push(car);
        }
        set.add(pick.join("-"));
      }
      const cs = [...set].map((s) => s.split("-").map(Number) as unknown as Trifecta);
      expect(expandTrifecta(foldTrifecta(cs))).toEqual(asSet(cs));
    }
  });
});
