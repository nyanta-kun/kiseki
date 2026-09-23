import { describe, expect, it } from "vitest";

import { hhmmJst, profit, roiTone } from "./keirinLineLead";

describe("keirinLineLead", () => {
  it("発走時刻は JST で表示する（端末のタイムゾーンに依存しない）", () => {
    // 2026-09-23 11:01 JST = 02:01 UTC
    expect(hhmmJst(Date.UTC(2026, 8, 23, 2, 1) / 1000)).toBe("11:01");
    expect(hhmmJst(null)).toBeNull();
    expect(hhmmJst(0)).toBeNull();
  });

  it("収支は払戻 − 投資", () => {
    expect(profit({ invest: 10000, payout: 2549500 })).toBe(2539500);
  });

  it("回収率の色は 100% 以上だけ緑", () => {
    expect(roiTone(100)).toContain("emerald");
    expect(roiTone(99.9)).not.toContain("emerald");
    expect(roiTone(50)).toContain("rose");
    expect(roiTone(null)).toContain("gray-400");
  });
});
