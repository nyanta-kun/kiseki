import Link from "next/link";

import type { PogGroup } from "@/lib/pog";

/** 年度の切り替え。22年分あるので横スクロールで出す（スマホ優先）。 */
export function YearTabs({ groups, current }: { groups: PogGroup[]; current: number }) {
  return (
    <nav
      aria-label="年度"
      className="-mx-4 mb-4 flex gap-1.5 overflow-x-auto px-4 pb-1"
    >
      {groups.map((g) => (
        <Link
          key={g.year}
          href={`/pog/${g.year}`}
          aria-current={g.year === current ? "page" : undefined}
          className={`shrink-0 rounded-full border px-3 py-1 text-xs transition-colors ${
            g.year === current
              ? "border-emerald-500 bg-emerald-500 text-white"
              : "border-neutral-300 text-neutral-600 hover:bg-neutral-100 dark:border-neutral-600 dark:text-neutral-300 dark:hover:bg-neutral-800"
          }`}
        >
          {g.year}
        </Link>
      ))}
    </nav>
  );
}
