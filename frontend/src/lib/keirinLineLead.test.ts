import { describe, expect, it } from "vitest";

import { expectedPayout, hhmmJst, profit, roiTone, shiftDay } from "./keirinLineLead";

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

  it("日付の前後移動は月・年をまたぐ", () => {
    expect(shiftDay("2026-09-30", 1)).toBe("2026-10-01");
    expect(shiftDay("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("想定払戻は賭け金 × 予測オッズ（無ければ null）", () => {
    expect(expectedPayout({ stake: 3300, pred_odds: 45.5 })).toBe(150150);
    expect(expectedPayout({ stake: 3300, pred_odds: null })).toBeNull();
    expect(expectedPayout({ stake: 3300, pred_odds: 0 })).toBeNull();
  });
});
