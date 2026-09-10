/**
 * 横スクロールを生む構造を静的に禁じる。
 *
 * 🔴 **2026-09-10 に本番で実際に起きた壊れ方。**
 * POG の各ページが `<main className="mx-auto max-w-3xl p-4">` を
 * **ルートレイアウトの直下**に置いていた。ルートレイアウトの
 * `<div className="flex-1 min-h-0 flex flex-col">` は **flex コンテナ**で、
 * その子に `margin-left/right: auto` が付くと **`align-items: stretch` が無効になり、
 * 幅が「内容の最大幅」で決まる**（CSS Flexbox の仕様）。
 *
 * 結果、一覧に長い行（「最高 ヴェトロテンペスタ」等）が 1 つ入るだけで
 * `<main>` がビューポートより広くなり、カードが画面からはみ出す。
 * 最小再現で実測した: **幅 494px のコンテナに対し `<main>` が 507px**。
 *
 * ⚠️ **`md:` 未満でしか出ない。** `max-w-3xl`(768px) 以上の画面では上限で
 *    頭打ちになって収まるので、PC で見ている限り永久に気づけない。
 *
 * ## 何が安全で何が危険か
 *
 * | ルート要素 | 判定 | 理由 |
 * |---|---|---|
 * | `mx-auto max-w-3xl p-4` | 🔴 危険 | 幅が内容依存になる |
 * | `w-full sm:max-w-3xl sm:mx-auto` | ✅ 安全 | `w-full`＝明示幅があるので縮まない |
 * | `min-h-screen`（中で `mx-auto`） | ✅ 安全 | 直下の子に auto マージンが無い |
 *
 * → 判定は「ルート要素に `mx-auto` があり、かつ `w-full` が無い」。
 *
 * 直し方は「全幅のブロックで包み、`mx-auto` はその**内側**に置く」。
 * `app/pog/PogShell.tsx` がその形になっている。
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const APP = join(process.cwd(), "src", "app");

function walk(dir: string, match: RegExp, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, match, out);
    else if (match.test(p)) out.push(p);
  }
  return out;
}

/**
 * **デフォルトエクスポートの**返すルート要素の属性を集める。
 *
 * ⚠️ ファイル内の `return (` を全部見てはいけない。スケルトンや部分 RSC など、
 *    ページのルートにならない下位コンポーネントまで拾って誤検知する
 *    （実際 `races/page.tsx` と `chihou/races/[id]/page.tsx` で 4 件誤検知した）。
 *    早期 return（エラー・空表示）はルートになりうるので、デフォルト
 *    エクスポート**の中**にある return は全部見る。
 */
function defaultExportRoots(source: string): string[] {
  const lines = source.split("\n");
  const start = lines.findIndex((l) => /^export default (async )?function/.test(l));
  if (start === -1) return [];
  // 次のトップレベル宣言までがデフォルトエクスポートの本体。
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^(export |async function |function |const |class )/.test(lines[i])) {
      end = i;
      break;
    }
  }

  const roots: string[] = [];
  for (let i = start; i < end; i++) {
    if (!/^\s*return \(\s*$/.test(lines[i])) continue;
    let j = i + 1;
    while (j < end && (lines[j].trim() === "" || /^\s*(\/\/|\/\*|\*)/.test(lines[j]))) j++;
    if (j >= end) continue;
    const head = lines[j].trim();
    // フラグメント（`<>`）は箱を作らないので対象外。
    if (!head.startsWith("<") || head.startsWith("<>")) continue;
    const chunk = lines.slice(j, j + 12).join("\n");
    const gt = chunk.indexOf(">");
    roots.push(gt === -1 ? chunk : chunk.slice(0, gt));
  }
  return roots;
}

describe("ページのルート要素が幅を内容依存にしない", () => {
  const files = walk(APP, /\/(page|layout)\.tsx$/);

  it("走査対象のページが見つかっている", () => {
    // 走査が空振りしていると、この検査は**何も守らないまま緑になる**。
    expect(files.length).toBeGreaterThan(15);
  });

  it.each(files.map((f) => [f.replace(APP, "src/app"), f] as const))(
    "%s",
    (_label, file) => {
      const offenders = defaultExportRoots(readFileSync(file, "utf-8")).filter(
        (attrs) => /\bmx-auto\b/.test(attrs) && !/\bw-full\b/.test(attrs),
      );
      expect(
        offenders.map((a) => a.replace(/\s+/g, " ").slice(0, 100)),
        "ルート要素は全幅で敷き、mx-auto はその内側に置いてください" +
          "（`w-full` を併記して明示幅を与えるのでも可）。" +
          " ルートレイアウトは flex コンテナなので、直下の子に auto マージンが付くと" +
          " 幅が内容依存になり、狭い画面で横にはみ出します",
      ).toEqual([]);
    },
  );
});

describe("POG の負マージンはシェルの余白と一致する", () => {
  /**
   * 横スクロールの原因になるもう 1 つの形。
   *
   * `PogShell` の水平余白はスマホで `px-3`。中の横スクロール帯（ページタブ・
   * オーナー絞り込み）は端まで伸ばすために負マージンを使うが、`-mx-4` にすると
   * 左右 4px ずつシェルの外へ出て、ページ全体に横スクロールが出る。
   */
  const POG = join(APP, "pog");
  const files = walk(POG, /\.tsx$/);

  /** コメント文中の `-mx-4` を拾わないよう、注釈を落としてから走査する。 */
  function stripComments(src: string): string {
    return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  }

  it("シェルの水平余白は px-3（スマホ）", () => {
    // 下の 2 つが前提にしている値。変えたら負マージン側も揃えること。
    const shell = readFileSync(join(POG, "PogShell.tsx"), "utf-8");
    expect(shell).toContain("mx-auto max-w-6xl px-3 py-2 md:px-4 md:py-6");
  });

  it("POG 配下の負マージンは -mx-3 だけ", () => {
    const offenders: string[] = [];
    for (const f of files) {
      for (const m of stripComments(readFileSync(f, "utf-8")).matchAll(/-mx-(\d+|\[[^\]]+\])/g)) {
        if (m[1] !== "3") offenders.push(`${f.replace(APP, "src/app")} : -mx-${m[1]}`);
      }
    }
    expect(offenders, "PogShell の px-3 と揃えてください").toEqual([]);
  });

  it("負マージンには同じ大きさの px が対になっている", () => {
    const offenders: string[] = [];
    for (const f of files) {
      for (const line of stripComments(readFileSync(f, "utf-8")).split("\n")) {
        if (line.includes("-mx-3") && !/\bpx-3\b/.test(line)) {
          offenders.push(`${f.replace(APP, "src/app")} : ${line.trim().slice(0, 80)}`);
        }
      }
    }
    expect(offenders, "-mx-3 には px-3 を対で付けてください").toEqual([]);
  });
});
