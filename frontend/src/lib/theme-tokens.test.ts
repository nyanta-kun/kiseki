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
    "--surface-heading",
    "--surface-muted",
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
    for (const t of ["--page-bg", "--surface-heading", "--surface-muted", "--background", "--foreground"]) {
      expect(block).toContain(`${t}:`);
    }
  });

  it("body は明示的にトークンから背景を取る", () => {
    // 透明のままだとホスト側の地の色を借りてしまう。
    expect(css).toMatch(/body\s*\{[\s\S]*?background:\s*var\(--background\)/);
  });
});

/**
 * 面の上の文字色。
 *
 * 🔴 2026-09-02 の目視で見つけた壊れ方: 面だけトークン化して暗くしたのに、
 *    その上に載る見出しが `text-gray-800` のままだった。面はテーマで反転するが
 *    Tailwind の固定色は反転しないので、dark で見出しが背景に溶ける
 *    （`/my` の「マイページ」で実測 **コントラスト比 1.18**・ほぼ不可視）。
 *
 * カードの中（`bg-white`）は面ではないので `text-gray-*` のままでよい。
 * ここで縛るのは「面に直接置く文字」だけ。
 */
describe("面の上の文字色", () => {
  const css = readFileSync(GLOBALS, "utf-8");

  /** `--token: #rrggbb;` を全部拾う（後勝ち＝dark 側で上書きされる）。 */
  function hexes(token: string): string[] {
    const re = new RegExp(`${token}:\\s*(#[0-9a-fA-F]{6})`, "g");
    const out: string[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(css)) !== null) out.push(m[1].toLowerCase());
    return out;
  }

  function luminance(hex: string): number {
    const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const f = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * f(v[0]) + 0.7152 * f(v[1]) + 0.0722 * f(v[2]);
  }

  function ratio(a: string, b: string): number {
    const [l1, l2] = [luminance(a), luminance(b)];
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }

  const SURFACES = ["--page-bg", "--page-bg-chihou", "--page-bg-chihou-alt", "--page-bg-subtle", "--footer-bg"];
  const TEXTS = ["--surface-heading", "--surface-muted"];

  // light 同士 / dark 同士だけを突き合わせる（混ぜると意味のない比較になる）。
  const THEMES = ["light", "dark"] as const;

  it.each(
    SURFACES.flatMap((s) => TEXTS.flatMap((t) => THEMES.map((theme) => [s, t, theme] as const))),
  )("%s × %s が %s で 4.5:1 以上", (surface, text, theme) => {
    // 各トークンは [light, dark(media), dark(data-theme)] の順に並ぶ。
    const idx = theme === "light" ? 0 : 1;
    const bg = hexes(surface)[idx];
    const fg = hexes(text)[idx];
    expect(bg, `${surface} の ${idx} 番目の定義がありません`).toBeTruthy();
    expect(fg, `${text} の ${idx} 番目の定義がありません`).toBeTruthy();
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("ユーティリティクラスが定義されている", () => {
    // JSX 側がこの名前を使うので、消すと文字色が **無指定** に戻る（気づけない）。
    for (const cls of [".text-surface-heading", ".text-surface-muted", ".link-surface-muted"]) {
      expect(css).toContain(cls);
    }
  });

  /** 面に直接文字を置いているファイル（カードを持たない）。 */
  const SURFACE_ONLY_FILES = [
    "app/loading.tsx",
    "app/not-found.tsx",
    "app/error.tsx",
    "components/Footer.tsx",
  ];

  /** 面に直接置かれている見出し（カードの外）。ここを text-gray-* に戻すと dark で消える。 */
  const SURFACE_HEADINGS = [
    "app/results/page.tsx",
    "app/chihou/results/page.tsx",
    "app/my/page.tsx",
    "app/yoso/page.tsx",
  ];

  it.each(SURFACE_HEADINGS)("%s の h1 は面用の色を使う", (rel) => {
    const text = readFileSync(join(SRC, rel), "utf-8");
    expect(text, "面の上の見出しは text-surface-heading を使ってください")
      .toMatch(/<h1 className="[^"]*text-surface-heading/);
  });

  it.each(SURFACE_ONLY_FILES)("%s は面の上で text-gray-400/500 を使わない", (rel) => {
    const text = readFileSync(join(SRC, rel), "utf-8");
    expect(text, "面の上の補足文は text-surface-muted を使ってください").not.toMatch(/text-gray-(400|500)/);
  });
});

// ─────────── グラフの視認性（2026-09-03・実際に読めなくなった箇所から） ───────────
//
// 🔴 Recharts の既定ツールチップは「白い面 ＋ 系列色そのままの文字」。
//    暗いテーマでは面ごと浮き、明るいテーマでも淡い系列（#c7d2fe / #d1d5db）が
//    白地に載って読めない。実際 2026-09-03 に競輪の売上グラフで
//    「販売無償pt」「内訳不明」「日付」が消えていた。

/** 相対輝度（WCAG）。 */
function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const v = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}
function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
/** globals.css の指定ブロックからトークンを読む。 */
function tokensIn(css: string, startMarker: string): Record<string, string> {
  const i = css.indexOf(startMarker);
  const block = css.slice(i, css.indexOf("}", i));
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)) out[m[1]] = m[2];
  return out;
}

describe("グラフの配色", () => {
  it("ツールチップの文字が面に対して 4.5:1 以上（両テーマ）", () => {
    const css = readFileSync(GLOBALS, "utf-8");
    const light = tokensIn(css, "--chart-surface");
    const dark = tokensIn(css, '[data-theme="dark"]');
    for (const [name, t] of [["light", light], ["dark", dark]] as const) {
      expect(t["chart-surface"], `${name}: --chart-surface が無い`).toBeTruthy();
      expect(contrast(t["chart-fg"], t["chart-surface"]),
        `${name}: --chart-fg が面に対して薄すぎる`).toBeGreaterThanOrEqual(4.5);
      // 補足文は 4.5:1（本文と同じ扱い。数値の隣に出るため）
      expect(contrast(t["chart-muted"], t["chart-surface"]),
        `${name}: --chart-muted が面に対して薄すぎる`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("🔴 素の <Tooltip /> と contentStyle 頼みの Tooltip が残っていない", () => {
    const offenders: string[] = [];
    for (const f of walk(SRC)) {
      const text = readFileSync(f, "utf-8");
      // `content=` を渡していない Tooltip は Recharts 既定の面と文字色になる。
      // ⚠️ `>` までで切ると `content={props => …}` のアロー関数で切れて誤検知する。
      //    次の兄弟要素までを見る。
      let at = text.indexOf("<Tooltip");
      while (at >= 0) {
        const rest = text.slice(at + 8);
        const end = rest.search(/<(Legend|Bar|Line|Area|CartesianGrid|XAxis|YAxis|Reference|\/)/);
        const body = rest.slice(0, end < 0 ? 600 : end);
        if (!body.includes("content=")) {
          offenders.push(`${f.replace(SRC, "src")} : <Tooltip${body.slice(0, 40).replace(/\n/g, " ")}…`);
        }
        at = text.indexOf("<Tooltip", at + 8);
      }
    }
    expect(offenders,
      "Recharts 既定のツールチップは白い面＋系列色の文字で読めない。"
      + " lib/chart-theme.tsx の <ChartTooltip /> を content に渡すこと").toEqual([]);
  });

  it("🔴 グラフの線・目盛り・凡例に色を直書きしていない", () => {
    const offenders: string[] = [];
    for (const f of walk(SRC)) {
      if (f.endsWith("chart-theme.tsx")) continue;
      const text = readFileSync(f, "utf-8");
      if (/CartesianGrid[^>]*stroke="#/.test(text)) offenders.push(`${f} : CartesianGrid stroke`);
      if (/wrapperStyle=\{\{[^}]*color: "#/.test(text)) offenders.push(`${f} : Legend color`);
      // 軸の目盛りの色は chartAxisTick() 経由にする
      if (/tick=\{\{\s*fontSize: \d+,\s*fill: "#/.test(text)) offenders.push(`${f} : axis tick fill`);
    }
    expect(offenders.map((o) => o.replace(SRC, "src")),
      "lib/chart-theme.tsx の CHART_GRID / chartAxisTick / chartLegendStyle を使うこと")
      .toEqual([]);
  });
});
