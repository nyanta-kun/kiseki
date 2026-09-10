"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import type { PogGroup } from "@/lib/pog";

/**
 * 年度の切り替え。
 *
 * 22 年分あるので見せ方を画面幅で変える:
 *
 * | 幅 | 見せ方 | 理由 |
 * |---|---|---|
 * | スマホ | `<select>` | 22 個の丸を横スクロールさせると、目当ての年に着くまで何度も指を滑らせることになる |
 * | PC | 横並びのピル | 一覧できる幅があるので、押す前に前後の年が見えるほうが速い |
 *
 * ⚠️ どちらか一方だけを描く（`hidden` で隠すのではなく DOM を分ける）と
 *    リンクが二重になるので、**同じリンクを 2 通りで出して CSS で切り替える**。
 *    `<select>` 側は `aria-hidden` にせず、PC では `md:hidden` で消える。
 *
 * @param basePath 年度を差し替える先。`"/pog/:year/horses"` のように書く。
 */
export function YearTabs({
  groups,
  current,
  basePath = "/pog/:year",
}: {
  groups: PogGroup[];
  current: number;
  basePath?: string;
}) {
  const router = useRouter();
  const hrefOf = (year: number) => basePath.replace(":year", String(year));

  return (
    <div className="flex items-center gap-2">
      <span className="shrink-0 text-xs text-surface-muted">年度</span>

      {/* スマホ: プルダウン */}
      <select
        className="md:hidden rounded-lg border px-2 py-1 text-sm"
        style={{
          background: "var(--pog-card)",
          borderColor: "var(--pog-card-border)",
          color: "var(--surface-heading)",
        }}
        value={current}
        onChange={(e) => router.push(hrefOf(Number(e.target.value)))}
        aria-label="年度を選ぶ"
      >
        {groups.map((g) => (
          <option key={g.year} value={g.year}>
            {g.year} 年度
          </option>
        ))}
      </select>

      {/* PC: ピル。年度が多いので横スクロールは残す。 */}
      <nav
        aria-label="年度"
        className="hidden md:flex min-w-0 flex-1 gap-1 overflow-x-auto pb-0.5"
      >
        {groups.map((g) => {
          const isCurrent = g.year === current;
          return (
            <Link
              key={g.year}
              href={hrefOf(g.year)}
              aria-current={isCurrent ? "page" : undefined}
              className="shrink-0 rounded-full px-2.5 py-0.5 text-xs tabular-nums transition-colors"
              style={
                isCurrent
                  ? { background: "var(--pog-accent)", color: "#fff" }
                  : {
                      border: "1px solid var(--pog-card-border)",
                      color: "var(--surface-muted)",
                    }
              }
            >
              {g.year}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
