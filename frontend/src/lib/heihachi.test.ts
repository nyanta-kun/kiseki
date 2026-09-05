import { describe, expect, it } from "vitest";
import {
  HEIHACHI_FALLBACK_DEFAULTS as D,
  HeihachiThresholds,
  isGradedRace,
  matchesHeihachi,
  parseThresholds,
} from "./heihachi";

const subject = (over: Partial<Parameters<typeof matchesHeihachi>[0]> = {}) => ({
  grade: "OP特別" as string | null,
  indexRank: 1 as number | null,
  winOdds: 15 as number | null,
  placeProbability: 0.35 as number | null,
  ...over,
});

describe("isGradedRace", () => {
  it("OP特別以上だけ true", () => {
    expect(isGradedRace("OP特別")).toBe(true);
    expect(isGradedRace("G1")).toBe(true);
    expect(isGradedRace(null)).toBe(false);
    expect(isGradedRace("J.G3")).toBe(false); // 障害は対象外
  });
});

describe("matchesHeihachi", () => {
  it("既定値の条件を満たすと true", () => {
    expect(matchesHeihachi(subject(), D)).toBe(true);
  });

  it("オッズは下限を含み上限を含まない", () => {
    expect(matchesHeihachi(subject({ winOdds: 9.9 }), D)).toBe(false);
    expect(matchesHeihachi(subject({ winOdds: 10 }), D)).toBe(true);
    expect(matchesHeihachi(subject({ winOdds: 39.9 }), D)).toBe(true);
    expect(matchesHeihachi(subject({ winOdds: 40 }), D)).toBe(false);
  });

  it("指数順位・複勝確率の下限を見る", () => {
    expect(matchesHeihachi(subject({ indexRank: 4 }), D)).toBe(false);
    expect(matchesHeihachi(subject({ placeProbability: 0.29 }), D)).toBe(false);
  });

  it("欠損は該当にしない", () => {
    expect(matchesHeihachi(subject({ winOdds: null }), D)).toBe(false);
    expect(matchesHeihachi(subject({ placeProbability: null }), D)).toBe(false);
    expect(matchesHeihachi(subject({ indexRank: null }), D)).toBe(false);
  });

  it("gradedOnly を外すと平場も対象になる", () => {
    expect(matchesHeihachi(subject({ grade: null }), D)).toBe(false);
    expect(matchesHeihachi(subject({ grade: null }), { ...D, gradedOnly: false })).toBe(true);
  });

  it("しきい値を緩めると該当が増える", () => {
    const loose: HeihachiThresholds = {
      maxIndexRank: 5,
      minOdds: 5,
      maxOdds: 100,
      minPlaceProb: 0.1,
      gradedOnly: false,
    };
    expect(matchesHeihachi(subject({ indexRank: 5, winOdds: 80, placeProbability: 0.12 }), loose))
      .toBe(true);
  });
});

describe("parseThresholds", () => {
  it("未保存・壊れた値は既定値", () => {
    expect(parseThresholds(null, D)).toEqual(D);
    expect(parseThresholds("{", D)).toEqual(D);
    expect(parseThresholds("null", D)).toEqual(D);
  });

  it("欠けたキーは既定値で埋める", () => {
    expect(parseThresholds(JSON.stringify({ minOdds: 12 }), D)).toEqual({ ...D, minOdds: 12 });
  });

  it("下限が上限を追い越した保存値はオッズ帯を既定値へ戻す", () => {
    const got = parseThresholds(JSON.stringify({ minOdds: 50, maxOdds: 20 }), D);
    expect(got.minOdds).toBe(D.minOdds);
    expect(got.maxOdds).toBe(D.maxOdds);
  });

  it("数値でない値は無視する", () => {
    expect(parseThresholds(JSON.stringify({ minOdds: "abc", gradedOnly: "yes" }), D)).toEqual(D);
  });
});
