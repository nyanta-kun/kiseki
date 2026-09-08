import { describe, it, expect } from "vitest";
import { isExcludedFromPrecache } from "./pwaPrecache";

describe("isExcludedFromPrecache", () => {
  it("_next/static は precache しない（HTTP キャッシュが immutable で持つ）", () => {
    // iOS Safari の「間を空けて戻ると15〜27秒」の対策。SW に 1.3MB を抱えさせない。
    expect(isExcludedFromPrecache("static/chunks/main-abc123.js")).toBe(true);
    expect(isExcludedFromPrecache("static/css/abc.css")).toBe(true);
    expect(isExcludedFromPrecache("static/media/font.woff2")).toBe(true);
  });

  it("🔴 サーバ専用アセットは必ず外す（公開URLが無く install ごと失敗する）", () => {
    expect(isExcludedFromPrecache("server/middleware-build-manifest.js")).toBe(true);
    expect(isExcludedFromPrecache("server/next-font-manifest.json")).toBe(true);
    expect(isExcludedFromPrecache("build-manifest.json")).toBe(true);
    expect(isExcludedFromPrecache("app-build-manifest.json")).toBe(true);
    expect(isExcludedFromPrecache("react-loadable-manifest.json")).toBe(true);
  });

  it("public/ の実ファイルは precache する（オフラインでも殻は出す）", () => {
    expect(isExcludedFromPrecache("favicon.ico")).toBe(false);
    expect(isExcludedFromPrecache("manifest.json")).toBe(false);
    expect(isExcludedFromPrecache("images/logo.png")).toBe(false);
  });
});
