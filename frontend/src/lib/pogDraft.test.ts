import { describe, expect, it } from "vitest";

import {
  computeConfirmTargets,
  computeDisplayOrders,
  horseKey,
  toApiTargets,
  UNSET,
  type DraftCell,
} from "./pogDraft";

/**
 * POG ドラフトの確定規則を固定する。
 *
 * 🔴 **次に実地で動くのは 2027年春**。壊れていても半年以上気づけないので、
 * 2026年度の実データの形をそのまま検査に写す。
 *
 *     1巡目  A=1  B=0（Aと同じ馬で負け）  C=1（別の馬）
 *     2巡目  B=1  ← 巡は 2 だが B にとっては 1 頭目
 *
 * 2026-09-10 にダミーの 2099 年度を画面から操作して、
 * 「押した人以外を全部 0 にする」実装が**別の馬を指名した人まで落とす**のを
 * 見つけた。ここはその再発防止。
 */
const cell = (
  user_id: number,
  draft_order: number,
  horse: string,
  pick_order: number | null = null,
): DraftCell => ({
  user_id,
  draft_order,
  pick_order,
  horse_name: horse,
  broodmare: null,
  netkeiba_horse_id: `h${horse}`,
});

describe("馬の同一性", () => {
  it("未命名馬は母名で束ねる", () => {
    expect(
      horseKey({ ...cell(1, 1, ""), horse_name: null, broodmare: "ビワハイジ" }),
    ).toBe("母ビワハイジ");
  });
});

describe("段階（displayOrder）", () => {
  it("1巡目は必ず1頭目", () => {
    const picks = [cell(1, 1, "X"), cell(2, 1, "X"), cell(3, 1, "Y")];
    expect(computeDisplayOrders(picks, 3)[1]).toBe(1);
  });

  it("全員が1頭目を取るまで段階は進まない", () => {
    // 1巡目で A と C だけが確定（B は負けて 0）。
    const picks = [
      cell(1, 1, "X", 1),
      cell(2, 1, "X", 0),
      cell(3, 1, "Y", 1),
      cell(2, 2, "Z"),
    ];
    // 3人中2人しか1頭目を持っていないので、2巡目もまだ「1頭目」。
    expect(computeDisplayOrders(picks, 3)[2]).toBe(1);
  });

  it("全員が1頭目を取ったら2頭目へ進む", () => {
    const picks = [
      cell(1, 1, "X", 1),
      cell(2, 1, "X", 0),
      cell(3, 1, "Y", 1),
      cell(2, 2, "Z", 1), // B も1頭目を取った
      cell(1, 3, "W"),
    ];
    expect(computeDisplayOrders(picks, 3)[3]).toBe(2);
  });
});

describe("確定（重複あり）", () => {
  const row = [cell(1, 1, "X"), cell(2, 1, "X"), cell(3, 1, "Y")];

  it("🔴 別の馬を指名した人は巻き込まない", () => {
    const t = computeConfirmTargets(row, 1, 1);
    expect(t).toEqual([
      { user_id: 1, pick_order: 1 },
      { user_id: 2, pick_order: 0 },
    ]);
    expect(t.map((x) => x.user_id)).not.toContain(3);
  });

  it("却下済みを押すと勝者が入れ替わる", () => {
    const settled = [
      cell(1, 1, "X", 1),
      cell(2, 1, "X", 0),
      cell(3, 1, "Y", 1),
    ];
    expect(computeConfirmTargets(settled, 2, 1)).toEqual([
      { user_id: 1, pick_order: 0 },
      { user_id: 2, pick_order: 1 },
    ]);
  });

  it("確定済みを押すとその馬名の全員が未確定へ戻る", () => {
    const settled = [
      cell(1, 1, "X", 1),
      cell(2, 1, "X", 0),
      cell(3, 1, "Y", 1),
    ];
    const t = computeConfirmTargets(settled, 1, 1);
    expect(t).toEqual([
      { user_id: 1, pick_order: UNSET },
      { user_id: 2, pick_order: UNSET },
    ]);
    expect(toApiTargets(t)).toEqual([
      { user_id: 1, pick_order: null },
      { user_id: 2, pick_order: null },
    ]);
  });
});

describe("確定（重複なし）", () => {
  it("重複していない馬を指名した全員をまとめて確定する", () => {
    const row = [cell(1, 1, "X"), cell(2, 1, "Y"), cell(3, 1, "Z")];
    expect(computeConfirmTargets(row, 1, 1)).toEqual([
      { user_id: 1, pick_order: 1 },
      { user_id: 2, pick_order: 1 },
      { user_id: 3, pick_order: 1 },
    ]);
  });

  it("重複している人は同時に確定しない", () => {
    // A/B が重複、C/D は単独。C を押すと C と D だけ確定する。
    const row = [
      cell(1, 1, "X"),
      cell(2, 1, "X"),
      cell(3, 1, "Y"),
      cell(4, 1, "Z"),
    ];
    const t = computeConfirmTargets(row, 3, 1);
    expect(t.map((x) => x.user_id).sort()).toEqual([3, 4]);
  });

  it("確定済み・重複なしを押すと行全体が未確定へ戻る", () => {
    const row = [cell(1, 1, "X", 1), cell(2, 1, "Y", 1)];
    expect(computeConfirmTargets(row, 1, 1)).toEqual([
      { user_id: 1, pick_order: UNSET },
      { user_id: 2, pick_order: UNSET },
    ]);
  });
});

describe("2026年度の実データの形", () => {
  it("1巡目で3人が同じ馬・4人が別の馬、という並びを再現する", () => {
    // 実データ: 7人が指名、3人が「ジョドレルバンク」
    const row: DraftCell[] = [
      cell(1, 1, "ジョドレルバンク"),
      cell(2, 1, "ジョドレルバンク"),
      cell(3, 1, "ジョドレルバンク"),
      cell(4, 1, "エルドボルグ"),
      cell(5, 1, "馬E"),
      cell(6, 1, "馬F"),
      cell(7, 1, "馬G"),
    ];
    // 重複していない4人はまとめて確定できる
    expect(computeConfirmTargets(row, 4, 1).map((t) => t.user_id).sort()).toEqual([
      4, 5, 6, 7,
    ]);
    // 重複した3人は「勝ち1・負け2」
    expect(computeConfirmTargets(row, 3, 1)).toEqual([
      { user_id: 1, pick_order: 0 },
      { user_id: 2, pick_order: 0 },
      { user_id: 3, pick_order: 1 },
    ]);
  });
});
