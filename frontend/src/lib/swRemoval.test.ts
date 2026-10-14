/**
 * Service Worker を撤去した状態を固定する（2026-10-15）。
 *
 * iPhone で間を空けて戻ると、SW の起動・更新確認から始まる回だけ
 * 「HTML → 初 API」が 11〜29 秒かかっていた（続けて操作中は 1 秒）。
 * 経緯は `src/app/sw-register.tsx` の冒頭コメント。
 *
 * ここで止めたいのは「いつの間にか SW が戻る」こと。戻っても型検査もビルドも
 * 通り、遅くなるのは**間を空けた iPhone だけ**なので、PC で見ている限り気づけない。
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(join(root, rel), "utf8");

/** コメントを除いたコード部分だけを見る（経緯の説明に語が出てくるため）。 */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

describe("Service Worker の撤去", () => {
  it("public/sw.js は fetch を扱わない（扱うと全リクエストが SW 経由に戻る）", () => {
    const sw = code(read("public/sw.js"));
    expect(sw).not.toMatch(/["']fetch["']/);
    expect(sw).not.toMatch(/importScripts|workbox/i);
  });

  it("public/sw.js は自分の登録を解除し、キャッシュを消す", () => {
    const sw = code(read("public/sw.js"));
    expect(sw).toMatch(/registration\.unregister\(\)/);
    expect(sw).toMatch(/caches\.delete/);
  });

  it("public/sw.js はクライアントをリロードさせない（復帰時の再読込を消したかった）", () => {
    const sw = code(read("public/sw.js"));
    expect(sw).not.toMatch(/\.navigate\(|location\.reload/);
  });

  it("ページから SW を登録しない", () => {
    const page = code(read("src/app/sw-register.tsx"));
    expect(page).not.toMatch(/serviceWorker\s*\.register\(/);
    expect(page).toMatch(/\.unregister\(\)/);
  });

  it("next-pwa を使わない（既定で SW 登録と reloadOnOnline が付いてくる）", () => {
    expect(code(read("next.config.ts"))).not.toMatch(/next-pwa|withPWA/);
    const pkg = JSON.parse(read("package.json")) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    expect(Object.keys(deps).filter((d) => /next-pwa|workbox/.test(d))).toEqual([]);
  });
});
