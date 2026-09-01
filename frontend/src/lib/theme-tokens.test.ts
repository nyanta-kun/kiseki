/**
 * テーマの構造検査。
 *
 * 🔴 **配色は目視でしか分からない**が、目視できない環境でも壊れ方は決まっている。
 *    「面の色を直書きする」と `prefers-color-scheme` が効かなくなり、
 *    「色を media / [data-theme] の中だけで定義する」ともう一方のテーマで消える。
 *    どちらも**静的に検出できる**ので、ここで縛る。
 *
 * 背景（2026-09-02）:
 *   各ページが `style={{ background: "#f0f5fb" }}` のように 16 箇所で面の色を
 *   直書きしており、`globals.css` の `prefers-color-scheme` 対応は実質死んでいた
 *   （`dark:` を使っているのは競輪配下 11 ファイルだけ）。
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(process.cwd(), "src");
const GLOBALS = join(SRC, "app", "globals.css");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

/** 面（ページ全体・フッタ等の広い領域）に使われていた色。ここは必ずトークン経由にする。 */
const SURFACE_HEXES = ["#f0f5fb", "#f0faf4", "#f0f8f3", "#f8fafc", "#f9fafb"];

describe("ページ面の色はトークン経由", () => {
  it("面の色を直書きしているファイルが無い", () => {
    const offenders: string[] = [];
    for (const f of walk(SRC)) {
      const text = readFileSync(f, "utf-8");
      for (const hex of SURFACE_HEXES) {
        if (text.includes(`background: "${hex}"`)) {
          offenders.push(`${f.replace(SRC, "src")} : ${hex}`);
        }
      }
    }
    expect(offenders,
      "面の色は var(--page-bg) 等のトークンを使ってください。" +
      " 直書きすると prefers-color-scheme が効かなくなります"
    ).toEqual([]);
  });
});

describe("globals.css のテーマ定義", () => {
  const css = readFileSync(GLOBALS, "utf-8");

  /** 素の :root ブロック（media / [data-theme] の中でないもの）を抜き出す。 */
  function bareRootBlocks(): string {
    const out: string[] = [];
    const re = /(^|\n)(:root\s*\{)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(css)) !== null) {
      // 直前が @media { なら素の :root ではない（インデントで判定）
      const lineStart = css.lastIndexOf("\n", m.index) + 1;
      if (css.slice(lineStart, m.index + m[0].length).startsWith(" ")) continue;
      const open = css.indexOf("{", m.index);
      const close = css.indexOf("}", open);
      out.push(css.slice(open, close));
    }
    return out.join("\n");
  }

  const bare = bareRootBlocks();

  const TOKENS = [
    "--page-bg",
    "--page-bg-chihou",
    "--page-bg-chihou-alt",
    "--page-bg-subtle",
    "--footer-bg",
    "--background",
    "--foreground",
  ];

  it.each(TOKENS)("%s が素の :root で定義されている", (token) => {
    // 🔴 media / [data-theme] の中だけで定義すると、テーマ未指定の既定状態で
    //    その色が「無い」ことになり、面と文字が別テーマの組み合わせで描画される。
    expect(bare).toContain(`${token}:`);
  });

  it("dark の上書きは [data-theme=\"light\"] を除外している", () => {
    // 明示 light を選んだ人が OS の dark に引きずられないようにする。
    expect(css).toMatch(/@media \(prefers-color-scheme: dark\)[\s\S]*?:root:not\(\[data-theme="light"\]\)/);
  });

  it("[data-theme=\"dark\"] でも同じトークンを上書きしている", () => {
    // トグルを付けたとき、OS が light でも dark にできる。
    // ⚠️ 単純な indexOf だと**コメント中の言及**を拾う（実際に一度それで落ちた）。
    //    セレクタとして書かれている箇所を探すこと。
    const idx = css.indexOf(':root[data-theme="dark"]');
    expect(idx, ':root[data-theme="dark"] のルールがありません').toBeGreaterThan(-1);
    const block = css.slice(idx, css.indexOf("}", idx));
    for (const t of ["--page-bg", "--background", "--foreground"]) {
      expect(block).toContain(`${t}:`);
    }
  });

  it("body は明示的にトークンから背景を取る", () => {
    // 透明のままだとホスト側の地の色を借りてしまう。
    expect(css).toMatch(/body\s*\{[\s\S]*?background:\s*var\(--background\)/);
  });
});
