import { describe, expect, it } from "vitest";

import {
  DEFAULT_MENU_FLAGS,
  KEIRIN_REQUIRES_ADMIN,
  landingPath,
  menuOfPath,
  normalizeMenuFlags,
  resolveMenuAccess,
  type MenuFlags,
} from "./menuAccess";

const NONE: MenuFlags = { pog: false, jra: false, chihou: false, keirin: false };

describe("normalizeMenuFlags", () => {
  it("POG を ON にすると中央・地方も立つ", () => {
    // 立たないと POG の順位表から張っているレース詳細が全部ガードで弾かれる。
    expect(normalizeMenuFlags({ ...NONE, pog: true })).toEqual({
      pog: true,
      jra: true,
      chihou: true,
      keirin: false,
    });
  });

  it("POG が OFF なら他は与えられたまま", () => {
    expect(normalizeMenuFlags(NONE)).toEqual(NONE);
  });

  it("POG は競輪を巻き込まない", () => {
    expect(normalizeMenuFlags({ ...NONE, pog: true }).keirin).toBe(false);
    expect(normalizeMenuFlags({ ...NONE, pog: true, keirin: true }).keirin).toBe(true);
  });
});

describe("resolveMenuAccess", () => {
  it("admin は 4 つとも OFF でも全部見える", () => {
    expect(resolveMenuAccess("admin", NONE)).toEqual({
      pog: true,
      jra: true,
      chihou: true,
      keirin: true,
    });
  });

  it("一般ユーザーが全部 OFF なら本当に何も見えない", () => {
    expect(resolveMenuAccess("member", NONE)).toEqual(NONE);
  });

  it("読み出し側でも POG⇒中央+地方 が掛かる", () => {
    const got = resolveMenuAccess("member", { ...NONE, pog: true });
    expect([got.pog, got.jra, got.chihou]).toEqual([true, true, true]);
  });

  it.runIf(KEIRIN_REQUIRES_ADMIN)("競輪はフラグが立っていても一般には出さない", () => {
    expect(resolveMenuAccess("member", { ...NONE, keirin: true }).keirin).toBe(false);
  });

  it("role が未定義でも落ちない（未ログイン相当）", () => {
    expect(resolveMenuAccess(undefined, DEFAULT_MENU_FLAGS).jra).toBe(true);
  });
});

describe("menuOfPath", () => {
  it("地方の実績を中央と取り違えない", () => {
    // /chihou を /results より先に見ないと「地方の実績」が中央扱いになる。
    expect(menuOfPath("/chihou/results")).toBe("chihou");
    expect(menuOfPath("/results")).toBe("jra");
  });

  it("予想は中央に属する", () => {
    expect(menuOfPath("/yoso")).toBe("jra");
    expect(menuOfPath("/yoso/stats")).toBe("jra");
  });

  it("POG・競輪・中央を拾う", () => {
    expect(menuOfPath("/pog/2026")).toBe("pog");
    expect(menuOfPath("/keirin/stats")).toBe("keirin");
    expect(menuOfPath("/races/123")).toBe("jra");
  });

  it("誰でも見てよいページは null", () => {
    for (const p of ["/my", "/terms", "/privacy", "/contact", "/admin", "/login"]) {
      expect(menuOfPath(p)).toBeNull();
    }
  });

  it("前方一致で別ルートを巻き込まない", () => {
    // "/racesomething" のような別ルートを中央と誤判定しない。
    expect(menuOfPath("/racesomething")).toBeNull();
    expect(menuOfPath("/pogo")).toBeNull();
  });
});

describe("landingPath", () => {
  it("見えるものが 1 つも無ければマイページ", () => {
    // /races 決め打ちにするとガードと送り合ってリダイレクトが循環する。
    expect(landingPath(NONE)).toBe("/my");
  });

  it("中央が無ければ地方、地方も無ければ POG", () => {
    expect(landingPath({ ...NONE, chihou: true })).toBe("/chihou/races");
    expect(landingPath({ ...NONE, pog: true, jra: false, chihou: false }, 2026)).toBe(
      "/pog/2026",
    );
  });

  it("POG の年度が分からなければ /pog へ送る（そこが最新へ転送する）", () => {
    expect(landingPath({ ...NONE, pog: true }, null)).toBe("/pog");
  });

  it("中央があれば中央", () => {
    expect(landingPath({ pog: true, jra: true, chihou: true, keirin: true })).toBe("/races");
  });
});
