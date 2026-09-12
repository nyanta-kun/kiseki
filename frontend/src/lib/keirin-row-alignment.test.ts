/**
 * 競輪一覧の「推奨カード」と「推奨外カード」で、行の始まりと終わりが
 * ずれないことを静的に縛る（2026-09-12・ユーザー報告）。
 *
 * ## 何が起きていたか
 *
 * `PickCard`（推奨）と `NoPickRow`（推奨外）がヘッダのマークアップを
 * **別々に持っていた**ため、
 *
 * | | 左（水平 padding） | 右（入稿ボタン） |
 * |---|---|---|
 * | `PickCard` | 外 `px-3 sm:px-4` | 常にある |
 * | `NoPickRow` | 外 `px-1 sm:px-2` ＋ ボタン `px-2 sm:px-3` | 条件つき |
 *
 * となり、**sm 以上で左端が 4px ずれ**、入稿ボタンが無い行だけ
 * **シェブロンが 22px 右へ寄っていた**。
 *
 * ## 縛り方
 *
 * 値を突き合わせるのではなく（合わせ直しても次の変更でまたずれる）、
 * **同じ部品を通していること**を縛る。
 *
 * - 会場 / R / 発走時刻は `RaceHeadCols` でしか描かない
 * - 入稿ボタンが無い行は `SendSlot` で場所を空ける
 *
 * ⚠️ スマホ（`sm` 未満）では列幅も送信欄の空きも**効かせていない**。
 *    390px では横に 134〜189px しか残らず、揃えにいくと折り返して
 *    カードが高くなるため（実測 128px → 170px）。縦が予算なのは POG と同じ。
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = readFileSync(
  join(process.cwd(), "src/app/keirin/page.tsx"), "utf-8");

/** `function <Name>(` から、次のトップレベル `function ` までを切り出す。 */
function fnBody(name: string): string {
  const start = SRC.indexOf(`function ${name}(`);
  expect(start, `${name} が見つからない`).toBeGreaterThan(-1);
  const next = SRC.indexOf("\nfunction ", start + 1);
  return SRC.slice(start, next === -1 ? undefined : next);
}

describe("競輪一覧カードのヘッダ", () => {
  for (const card of ["PickCard", "NoPickRow"]) {
    it(`${card} は RaceHeadCols で会場・R・発走時刻を描く`, () => {
      const body = fnBody(card);
      expect(body).toContain("<RaceHeadCols");
      // 生の `{pick.venue_name}` / `{pick.race_no}R` を直接置かない
      expect(body).not.toMatch(/>\s*\{pick\.venue_name\}\s*</);
      expect(body).not.toMatch(/\{pick\.race_no\}R\s*</);
    });

    it(`${card} のヘッダは px-3 sm:px-4 で始まる`, () => {
      const body = fnBody(card);
      expect(body).toMatch(/flex items-center gap-1 px-3 sm:px-4 py-2/);
    });
  }

  it("NoPickRow は入稿ボタンが無いとき SendSlot で場所を空ける", () => {
    expect(fnBody("NoPickRow")).toContain("<SendSlot />");
  });

  it("列幅と送信欄の空きは sm 以上でだけ効かせる", () => {
    const cols = fnBody("RaceHeadCols");
    expect(cols).toContain("sm:min-w-[3.5rem]");
    // `sm:` の付かない `min-w-[...]` を置かない（スマホで時刻が折り返す）
    expect(cols).not.toMatch(/(?<!sm:)min-w-\[/);
    expect(fnBody("SendSlot")).toContain("hidden sm:block");
  });
});
