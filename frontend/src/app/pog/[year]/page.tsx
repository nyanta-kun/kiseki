import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  fetchPogGroups,
  fetchPogOwners,
  fetchPogOwnersAsOf,
  fetchPogRecentRaces,
  formatPrize,
  formatRecord,
} from "@/lib/pog";
import { RecentRaces } from "../RecentRaces";
import { YearTabs } from "../YearTabs";

export const metadata: Metadata = {
  title: "POG 順位表 | GallopLab",
};

/** 順位変動の矢印。1 週間前の順位と比べる。 */
function RankDelta({ now, before }: { now: number; before: number | undefined }) {
  if (before === undefined || before === now) {
    return <span className="text-neutral-400" aria-label="変動なし">—</span>;
  }
  const up = before > now;
  return (
    <span
      className={up ? "text-rose-500" : "text-sky-500"}
      aria-label={up ? `${before - now}つ上昇` : `${now - before}つ下降`}
    >
      {up ? "▲" : "▼"}
      {Math.abs(before - now)}
    </span>
  );
}

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default async function PogStandingsPage({
  params,
}: {
  params: Promise<{ year: string }>;
}) {
  const { year: raw } = await params;
  const year = Number(raw);
  if (!Number.isInteger(year)) notFound();

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  // 🔴 順位変動は `/owners` と同じ集計に asof を足したものを使う。
  //    別クエリで出すと、変動していないのに矢印が出る（移設元が踏んだ）。
  const [owners, lastWeek, recent] = await Promise.all([
    fetchPogOwners(year),
    fetchPogOwnersAsOf(year, isoDaysAgo(7)),
    // 今週の出走。過去年度でも今週の枠で引くので、たいていは空になる。
    fetchPogRecentRaces(year).catch(() => []),
  ]);
  const before = new Map(lastWeek.map((o) => [o.user_id, o.rank]));

  return (
    <main className="mx-auto max-w-3xl p-4">
      <h1 className="mb-1 text-lg font-bold">POG 順位表</h1>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        賞金は本賞金の合計（万円）。矢印は 1 週間前との比較。
      </p>

      <YearTabs groups={groups} current={year} />

      {owners.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          {year} 年度の指名がありません。
        </p>
      ) : (
        <ol className="space-y-2">
          {owners.map((o) => (
            <li
              key={o.user_id}
              className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
            >
              <Link
                href={`/pog/${year}/horses?user_id=${o.user_id}`}
                className="block"
              >
                <div className="flex items-baseline gap-2">
                  <span className="w-7 shrink-0 text-lg font-bold tabular-nums">
                    {o.rank}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {o.name ?? `ユーザー${o.user_id}`}
                  </span>
                  <RankDelta now={o.rank} before={before.get(o.user_id)} />
                  <span className="shrink-0 tabular-nums font-semibold">
                    {formatPrize(o.prize)}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 pl-9 text-xs text-neutral-500 dark:text-neutral-400">
                  <span className="tabular-nums">{formatRecord(o)}</span>
                  {o.prize_other > 0 && (
                    <span>地方 {formatPrize(o.prize_other)}</span>
                  )}
                  {o.top_horse && <span className="truncate">最高 {o.top_horse}</span>}
                </div>
              </Link>
            </li>
          ))}
        </ol>
      )}

      {recent.length > 0 && (
        <section className="mt-6">
          <h2 className="mb-2 text-sm font-bold">今週の出走</h2>
          <RecentRaces races={recent} />
        </section>
      )}

      <p className="mt-4 text-center">
        <Link
          href={`/pog/${year}/horses`}
          className="text-sm text-emerald-600 underline dark:text-emerald-400"
        >
          全馬の一覧を見る
        </Link>
      </p>
    </main>
  );
}
