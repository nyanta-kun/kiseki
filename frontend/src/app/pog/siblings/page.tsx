import type { Metadata } from "next";
import Link from "next/link";

import { fetchPogGroups, fetchPogSiblings, formatPrize } from "@/lib/pog";

export const metadata: Metadata = {
  title: "POG 兄弟馬 | GallopLab",
};

export default async function PogSiblingsPage() {
  const [groups, siblings] = await Promise.all([
    fetchPogGroups(),
    fetchPogSiblings(),
  ]);
  const latest = groups[0]?.year;

  return (
    <main className="mx-auto max-w-3xl p-4">
      <h1 className="mb-1 text-lg font-bold">POG 兄弟馬</h1>
      <p className="mb-4 text-xs text-neutral-500 dark:text-neutral-400">
        同じ母から 2 回以上指名されている馬。上の子がどうだったかを見るためのもの。
        父は問わない（半兄弟を含む）。
      </p>

      {siblings.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          該当がありません。
        </p>
      ) : (
        <div className="space-y-5">
          {siblings.map((g) => (
            <section key={g.broodmare}>
              <h2 className="mb-1.5 text-sm font-bold">
                {g.broodmare}
                <span className="ml-2 text-xs font-normal text-neutral-500 dark:text-neutral-400">
                  {g.nomination_count} 回指名
                </span>
              </h2>
              <ul className="space-y-1.5">
                {g.horses.map((h) => (
                  <li
                    key={h.netkeiba_horse_id}
                    className="rounded-lg border border-neutral-200 bg-white px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="w-11 shrink-0 text-xs tabular-nums text-neutral-500 dark:text-neutral-400">
                        {h.year}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-medium">
                        {h.horse_name}
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-neutral-500 dark:text-neutral-400">
                        {h.win}-{h.place}-{h.show}-{h.out}
                      </span>
                      <span className="shrink-0 tabular-nums text-sm font-semibold">
                        {formatPrize(h.prize)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-3 pl-13 text-xs text-neutral-500 dark:text-neutral-400">
                      {h.sire && <span>父 {h.sire}</span>}
                      {h.owner_name && <span>{h.owner_name}</span>}
                      {h.stable && <span>{h.stable}</span>}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <p className="mt-4 text-xs text-neutral-500 dark:text-neutral-400">
        ⚠️ 2017年度以前の指名馬は戦績が出ない（中央の馬マスタが 2013年産以前で
        極端に薄いため）。
      </p>

      {latest !== undefined && (
        <p className="mt-6 text-center text-sm">
          <Link
            href={`/pog/${latest}`}
            className="text-emerald-600 underline dark:text-emerald-400"
          >
            順位表へ
          </Link>
        </p>
      )}
    </main>
  );
}
