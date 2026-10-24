import { describe, expect, it } from "vitest";
import { mekauchidaUpsetClass } from "./mekauchidaUpset";

describe("mekauchidaUpsetClass", () => {
  it("低・中・高にそれぞれ別の見た目を返す", () => {
    const classes = ["低", "中", "高"].map((l) => mekauchidaUpsetClass(l));
    expect(classes.every((c) => c !== null)).toBe(true);
    expect(new Set(classes).size).toBe(3);
  });

  it("未取得や想定外の値ではチップを出さない", () => {
    expect(mekauchidaUpsetClass(null)).toBeNull();
    expect(mekauchidaUpsetClass(undefined)).toBeNull();
    expect(mekauchidaUpsetClass("")).toBeNull();
    expect(mekauchidaUpsetClass("特高")).toBeNull();
  });
});

describe("mekauchidaUpsetClass（一覧カード）", () => {
  it("白いカードでは白文字を使わない（背景に溶けて読めなくなる）", () => {
    for (const l of ["低", "中", "高"]) {
      const c = mekauchidaUpsetClass(l, "card");
      expect(c).not.toBeNull();
      expect(c).not.toMatch(/text-white|bg-white\//);
    }
  });

  it("見出しとカードで見た目を分ける", () => {
    expect(mekauchidaUpsetClass("低", "card")).not.toBe(mekauchidaUpsetClass("低", "header"));
  });
});
