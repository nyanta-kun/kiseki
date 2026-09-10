import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { fetchPogGradedWins, fetchPogGroups } from "@/lib/pog";
import { YearTabs } from "../../YearTabs";

export const metadata: Metadata = {
  title: "POG 記録室 | GallopLab",
};

/** `YYYY-MM-DD` → `YYYY.M.D`。 */
function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${y}.${Number(m)}.${Number(d)}`;
}

/** 格の色分け。G1 だけ目立たせる。 */
function GradeBadge({ grade }: { grade: string | null }) {
  if (!grade) return null;
  const cls = grade.endsWith("1")
    ? "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200"
    : grade.endsWith("2")
      ? "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200"
      : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300";
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${cls}`}>
      {grade}
    </span>
  );
}

export default async function PogRecordsPage({
  params,
}: {
  params: Promise<{ year: string }>;
}) {
  const { year: raw } = await params;
  const year = Number(raw);
  if (!Number.isInteger(year)) notFound();

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  const wins = await fetchPogGradedWins(year);

  // 馬主ごとにまとめる。件数の多い順に出す。
  const byOwner = new Map<string, typeof wins>();
  for (const w of wins) {
    const key = w.owner_name ?? `ユーザー${w.user_id}`;
    byOwner.set(key, [...(byOwner.get(key) ?? []), w]);
  }
  const owners = [...byOwner.entries()].sort((a, b) => b[1].length - a[1].length);

  return (
    <main className="mx-auto max-w-3xl p-4">
      <h1 className="mb-1 text-lg font-bold">POG 記録室</h1>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        指名馬が勝った重賞。中央・地方（中央交流を含む）。
      </p>

      <YearTabs groups={groups} current={year} />

      {wins.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          {year} 年度はまだ重賞勝ちがありません。
        </p>
      ) : (
        <div className="space-y-4">
          {owners.map(([owner, list]) => (
            <section key={owner}>
              <h2 className="mb-2 text-sm font-bold">
                {owner}
                <span className="ml-2 text-xs font-normal text-neutral-500 dark:text-neutral-400">
                  {list.length} 勝
                </span>
              </h2>
              <ul className="space-y-2">
                {list.map((w) => (
                  <li
                    key={`${w.source}-${w.race_id}-${w.netkeiba_horse_id}`}
                    className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="shrink-0 text-xs tabular-nums text-neutral-500 dark:text-neutral-400">
                        {formatDate(w.date)}
                      </span>
                      <GradeBadge grade={w.grade} />
                      <Link
                        href={
                          w.source === "chihou"
                            ? `/chihou/races/${w.race_id}`
                            : `/races/${w.race_id}`
                        }
                        className="min-w-0 flex-1 truncate text-emerald-700 underline decoration-emerald-300 underline-offset-2 dark:text-emerald-400"
                      >
                        {w.race_name ?? `${w.race_no}R`}
                      </Link>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-neutral-500 dark:text-neutral-400">
                      <span className="font-medium text-neutral-700 dark:text-neutral-200">
                        🥇 {w.horse_name ?? "（未命名）"}
                      </span>
                      <span>{w.course_name}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <p className="mt-6 flex justify-center gap-4 text-sm">
        <Link
          href={`/pog/${year}`}
          className="text-emerald-600 underline dark:text-emerald-400"
        >
          順位表へ
        </Link>
        <Link
          href={`/pog/${year}/score`}
          className="text-emerald-600 underline dark:text-emerald-400"
        >
          スコア集計へ
        </Link>
      </p>
    </main>
  );
}
