import { describe, expect, it } from "vitest";
import type { ChihouGekisouPick } from "./api";
import { sortGekisouPicks } from "./chihouGekisou";

function pick(over: Partial<ChihouGekisouPick>): ChihouGekisouPick {
  return {
    race_id: 1, course_name: "大井", race_number: 1, race_name: null, post_time: "1500",
    horse_number: 1, horse_name: null, popularity: 7, place_prob: 0.2, win_odds: 20,
    place_odds: 4, room: 1.4, source: "snapshot", finish_position: null, place_payout: null,
    ...over,
  };
}

describe("sortGekisouPicks", () => {
  const a = pick({ race_id: 1, post_time: "1500", place_prob: 0.18 });
  const b = pick({ race_id: 2, post_time: "1230", place_prob: 0.31 });
  const c = pick({ race_id: 3, post_time: "1945", place_prob: 0.25 });

  it("確率順は高い順", () => {
    expect(sortGekisouPicks([a, b, c], "prob").map((p) => p.race_id)).toEqual([2, 3, 1]);
  });

  it("発走順は時刻の早い順", () => {
    expect(sortGekisouPicks([a, b, c], "time").map((p) => p.race_id)).toEqual([2, 1, 3]);
  });

  it("確率が無い馬は確率順で最後", () => {
    const n = pick({ race_id: 4, post_time: "1000", place_prob: null });
    expect(sortGekisouPicks([n, a], "prob").map((p) => p.race_id)).toEqual([1, 4]);
  });

  it("同じ確率なら発走順", () => {
    const x = pick({ race_id: 5, post_time: "1700", place_prob: 0.2 });
    const y = pick({ race_id: 6, post_time: "1100", place_prob: 0.2 });
    expect(sortGekisouPicks([x, y], "prob").map((p) => p.race_id)).toEqual([6, 5]);
  });

  it("発走時刻が未定のレースは最後", () => {
    const u = pick({ race_id: 7, post_time: null });
    expect(sortGekisouPicks([u, a], "time").map((p) => p.race_id)).toEqual([1, 7]);
  });

  it("元の配列を書き換えない", () => {
    const src = [a, b, c];
    sortGekisouPicks(src, "prob");
    expect(src.map((p) => p.race_id)).toEqual([1, 2, 3]);
  });
});
