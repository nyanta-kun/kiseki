import { describe, expect, it } from "vitest";
import { makeRaceNormalizer, monotoneRow, solveLogitShift } from "./keirinProb";

describe("solveLogitShift", () => {
  it("合計を target に合わせる", () => {
    const probs = [0.5, 0.3, 0.2, 0.15, 0.1, 0.07, 0.05];
    const shift = solveLogitShift(probs, 3);
    const sum = probs.reduce(
      (s, p) => s + 1 / (1 + Math.exp(-(Math.log(p / (1 - p)) + shift))),
      0,
    );
    expect(sum).toBeCloseTo(3, 6);
  });
});

describe("makeRaceNormalizer", () => {
  it("順位を変えない（単調変換）", () => {
    const pcts = [62, 58, 50, 43, 38, 30, 21];
    const f = makeRaceNormalizer(pcts, 3);
    const out = pcts.map((v) => f(v)!);
    for (let i = 1; i < out.length; i++) expect(out[i]).toBeLessThan(out[i - 1]);
  });

  it("全車ゼロなら null", () => {
    const f = makeRaceNormalizer([0, 0, 0], 3);
    expect(f(0)).toBeNull();
  });
});

describe("monotoneRow", () => {
  it("1着率 > 2着内率 の破れを押し上げて直す", () => {
    // 実データで起きる形（最大逸脱は 1着率 − 2着内率 で +31.5pt）
    expect(monotoneRow(12.0, 9.4, 41.0)).toEqual({ win: 12.0, top2: 12.0, top3: 41.0 });
  });

  it("2着内率 > 3着内率 の破れも直す", () => {
    expect(monotoneRow(5.0, 40.0, 33.0)).toEqual({ win: 5.0, top2: 40.0, top3: 40.0 });
  });

  it("破れていなければ何も変えない", () => {
    expect(monotoneRow(12.0, 25.0, 41.0)).toEqual({ win: 12.0, top2: 25.0, top3: 41.0 });
  });

  it("下げる方向には直さない（1着率は元のまま）", () => {
    expect(monotoneRow(30.0, 10.0, 12.0).win).toBe(30.0);
  });

  it("null は伝播させる", () => {
    expect(monotoneRow(null, 9.4, 41.0)).toEqual({ win: null, top2: 9.4, top3: 41.0 });
    expect(monotoneRow(12.0, null, 41.0)).toEqual({ win: 12.0, top2: null, top3: 41.0 });
    expect(monotoneRow(12.0, 9.4, null)).toEqual({ win: 12.0, top2: 12.0, top3: null });
  });
});
