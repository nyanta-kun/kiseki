import { describe, expect, it } from "vitest";

import { formatAnnouncedJst } from "./oddsAnnounced";

describe("formatAnnouncedJst", () => {
  it("UTC の ISO を JST の時:分にする", () => {
    // 05:32 UTC = 14:32 JST
    expect(formatAnnouncedJst("2026-09-29T05:32:00Z")).toBe("発表 14:32");
  });

  it("日付をまたいでも時刻は JST で出す", () => {
    // 15:05 UTC = 翌 00:05 JST（h24 表記の "24:05" にしない）
    expect(formatAnnouncedJst("2026-09-29T15:05:00Z")).toBe("発表 00:05");
  });

  it("未記録・不正値は null（表示しない）", () => {
    expect(formatAnnouncedJst(null)).toBeNull();
    expect(formatAnnouncedJst(undefined)).toBeNull();
    expect(formatAnnouncedJst("")).toBeNull();
    expect(formatAnnouncedJst("not-a-date")).toBeNull();
  });
});
