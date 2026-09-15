import { describe, expect, it } from "vitest";

import {
  countHiddenInactive,
  filterInactiveRanks,
  pickSummaryPeriod,
} from "./keirinSummaryActive";

const ORDER = ["T_firm", "T_mid", "A_hit", "B_hit", "F_sign"] as const;

describe("filterInactiveRanks", () => {
  it("非アクティブを外す", () => {
    expect(filterInactiveRanks(ORDER, ["A_hit", "F_sign"], false)).toEqual(["T_firm", "T_mid", "B_hit"]);
  });
  it("トグル ON なら外さない", () => {
    expect(filterInactiveRanks(ORDER, ["A_hit"], true)).toEqual(ORDER);
  });
  it("inactive_ranks が無い／空なら絞らない（fail-open）", () => {
    expect(filterInactiveRanks(ORDER, undefined, false)).toEqual(ORDER);
    expect(filterInactiveRanks(ORDER, [], false)).toEqual(ORDER);
  });
});

describe("countHiddenInactive", () => {
  it("行順に含まれる非アクティブだけを数える", () => {
    // 7S は行順に無い（設定 OFF 等で既に出していない）ので数えない
    expect(countHiddenInactive(ORDER, ["A_hit", "F_sign", "7S"])).toBe(2);
    expect(countHiddenInactive(ORDER, undefined)).toBe(0);
  });
});

describe("pickSummaryPeriod", () => {
  const byRank = { T_firm: { n_picks: 2 }, A_hit: { n_picks: 1 } };
  const active = { n_picks: 2, total_bet: 2000, n_candidates: 1, by_rank: byRank };
  const all = { n_picks: 3, total_bet: 3000, n_candidates: 10 };

  it("既定は直近で売っているプランだけの合計", () => {
    expect(pickSummaryPeriod(active, all, false)).toBe(active);
  });
  it("トグル ON で全部込みの合計＋既定側の by_rank", () => {
    expect(pickSummaryPeriod(active, all, true)).toEqual({ ...all, by_rank: byRank });
  });
  it("all が無い古い API なら既定のまま", () => {
    expect(pickSummaryPeriod(active, undefined, true)).toBe(active);
  });
});
