import { describe, expect, it } from "vitest";
import { AUTO_REFRESH_MS, shouldPoll } from "./useAutoRefresh";

describe("shouldPoll", () => {
  const base = { visible: true, live: true, busy: false };

  it("見えていて・動く画面で・手が空いていれば取りに行く", () => {
    expect(shouldPoll(base)).toBe(true);
  });

  it("タブが隠れている間は取りに行かない", () => {
    expect(shouldPoll({ ...base, visible: false })).toBe(false);
  });

  it("過去日など内容が動かない画面では取りに行かない", () => {
    expect(shouldPoll({ ...base, live: false })).toBe(false);
  });

  it("手動更新・承認処理の最中は重ねない", () => {
    expect(shouldPoll({ ...base, busy: true })).toBe(false);
  });

  it("採点 cron は15分ごとなので、間隔をそれより細かくしても意味が無い", () => {
    expect(AUTO_REFRESH_MS).toBeGreaterThanOrEqual(30_000);
    expect(AUTO_REFRESH_MS).toBeLessThanOrEqual(15 * 60_000);
  });
});

// ───────────────────────────────────────────────────────────────────
// 配線の固定（2026-09-03）
//
// 🔴 自動更新は**壊れても何も起きない**（例外も型エラーも出ず、ただ更新が
//    止まる）。実行時に気づけないので、配線そのものを静的に押さえる。
// ───────────────────────────────────────────────────────────────────
import { readFileSync } from "node:fs";
import { join } from "node:path";

const HERE = join(__dirname);
const read = (p: string) => readFileSync(join(HERE, p), "utf8");

describe("自動更新の配線", () => {
  it("一覧は静かな再取得を使う（loadData だとスケルトンへ戻る）", () => {
    const src = read("page.tsx");
    expect(src).toMatch(/useAutoRefresh\(reloadQuiet,/);
    // reloadQuiet が loading を立てていないこと
    const body = src.slice(src.indexOf("const reloadQuiet"), src.indexOf("const isToday"));
    expect(body).not.toMatch(/setLoadingPicks|setLoadingSummary/);
  });

  it("一覧は今日のときだけ回し、手動更新中は重ねない", () => {
    const src = read("page.tsx");
    expect(src).toMatch(/live:\s*isToday,\s*busy:\s*refreshing/);
  });

  it("入稿確認は承認処理中に回さない", () => {
    const src = read("review/ReviewClient.tsx");
    expect(src).toMatch(/busy:\s*pending/);
  });

  it("入稿確認の日付比較は YYYY-MM-DD 同士でする", () => {
    // 🔴 この画面の `date` は `YYYY-MM-DD`。`todayYYYYMMDD()` と直に比べると
    //    永久に false になり、自動更新だけが黙って死ぬ。
    const src = read("review/ReviewClient.tsx");
    expect(src).toMatch(/live:\s*date === toISODate\(todayYYYYMMDD\(\)\)/);
    expect(src).not.toMatch(/live:\s*date === todayYYYYMMDD\(\)/);
  });
});
