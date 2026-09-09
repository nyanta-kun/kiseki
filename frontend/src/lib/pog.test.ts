import { describe, expect, it } from "vitest";

import { currentWeekRange } from "./pog";

/**
 * 重賞パネルの「今週」の範囲を固定する。
 *
 * なぜ必要か:
 *   ここがずれると**パネルに出るレースが丸ごと入れ替わる**が、画面は
 *   それらしく表示され続けるので目視では気づけない。移設元
 *   `GradedRacesTable.getWeekRange()` と同じ式であることを機械的に留める。
 */
describe("currentWeekRange", () => {
  // 2026-09-10 は木曜。直前の土曜は 09-05、その 8 日後が 09-13（翌週日曜）。
  it("木曜なら直前の土曜〜翌週日曜", () => {
    expect(currentWeekRange(new Date(2026, 8, 10))).toEqual(["20260905", "20260913"]);
  });

  it("土曜は当日が起点（前の週へ戻らない）", () => {
    expect(currentWeekRange(new Date(2026, 8, 12))).toEqual(["20260912", "20260920"]);
  });

  it("日曜は前日の土曜が起点", () => {
    expect(currentWeekRange(new Date(2026, 8, 13))).toEqual(["20260912", "20260920"]);
  });

  it("月曜は前の土曜が起点（週明けも先週の結果が見える）", () => {
    expect(currentWeekRange(new Date(2026, 8, 14))).toEqual(["20260912", "20260920"]);
  });

  it("月をまたいでも繰り下がる", () => {
    // 2026-10-01 は木曜。直前の土曜は 09-26。
    expect(currentWeekRange(new Date(2026, 9, 1))).toEqual(["20260926", "20261004"]);
  });

  /**
   * 🔴 移設元は `toISOString()` で日付を作っており、UTC に倒れるため
   * **日本時間の 00:00〜08:59 に見ると 1 日前**になっていた。
   * 土曜の朝に見ると起点が金曜になり、範囲が丸ごとずれる。
   */
  it("早朝でも日付がずれない", () => {
    expect(currentWeekRange(new Date(2026, 8, 12, 3, 0))).toEqual([
      "20260912",
      "20260920",
    ]);
    expect(currentWeekRange(new Date(2026, 8, 12, 23, 30))).toEqual([
      "20260912",
      "20260920",
    ]);
  });
});
