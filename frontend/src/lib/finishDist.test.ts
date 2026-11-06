import { describe, expect, it } from "vitest";
import type { HorseIndex } from "@/lib/api";
import { FINISH_DIST_KINDS, comboPct, finishDistPath, formatCombo, harvilleNote, top2Of } from "@/lib/finishDist";

describe("comboPct", () => {
  it("10% 以上は整数", () => {
    expect(comboPct(0.123)).toBe("12%");
  });
  it("10% 未満は小数1桁", () => {
    expect(comboPct(0.0243)).toBe("2.4%");
  });
  it("0.1% 未満はまとめる", () => {
    expect(comboPct(0.0004)).toBe("<0.1%");
  });
});

describe("formatCombo", () => {
  it("券面と同じく昇順に並べる", () => {
    expect(formatCombo([12, 2, 5])).toBe("2-5-12");
  });
});

describe("top2Of", () => {
  it("連対率があれば返す", () => {
    expect(top2Of({ top2_probability: 0.6 } as unknown as HorseIndex)).toBe(0.6);
  });
  it("古いバックエンド（項目なし）は null", () => {
    expect(top2Of({} as HorseIndex)).toBeNull();
  });
});

describe("FINISH_DIST_KINDS", () => {
  it("API の配列名と一致する", () => {
    expect(FINISH_DIST_KINDS.map((k) => k.key).sort()).toEqual(["quinella", "trio", "wide"]);
  });
});

describe("finishDistPath", () => {
  it("中央と地方で API を分ける", () => {
    expect(finishDistPath("jra", 5, 10)).toBe("/races/5/finish-dist?limit=10");
    expect(finishDistPath("chihou", 5, 10)).toBe("/chihou/races/5/finish-dist?limit=10");
  });
});

describe("harvilleNote", () => {
  it("能力分布モデルなら注記なし", () => {
    expect(harvilleNote({ source: "r1", place_slots: 3 })).toBeNull();
  });
  it("中央の5頭未満", () => {
    expect(harvilleNote({ source: "harville", place_slots: 0 })).toContain("5頭未満");
  });
  it("地方の4頭未満", () => {
    expect(harvilleNote({ source: "harville", k: 0 })).toContain("4頭未満");
  });
  it("収束しなかった", () => {
    expect(harvilleNote({ source: "harville", k: 3 })).toContain("収束しなかった");
  });
});
