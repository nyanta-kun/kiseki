import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  fetchPogGroups,
  fetchPogHorses,
  formatPrize,
  formatRecord,
} from "@/lib/pog";

export const metadata: Metadata = {
  title: "POG 指名馬 | GallopLab",
};

export default async function PogHorsesPage({
  params,
  searchParams,
}: {
  params: Promise<{ year: string }>;
  searchParams: Promise<{ user_id?: string }>;
}) {
  const { year: raw } = await params;
  const { user_id: rawUser } = await searchParams;
  const year = Number(raw);
  if (!Number.isInteger(year)) notFound();

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  const userId = rawUser ? Number(rawUser) : undefined;
  const horses = await fetchPogHorses(
    year,
    Number.isInteger(userId) ? userId : undefined,
  );
  const ownerName = horses[0]?.owner_name ?? null;

  return (
    <main className="mx-auto max-w-3xl p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h1 className="text-lg font-bold">
          {year} 年度 {userId ? (ownerName ?? "指名馬") : "全馬"}
        </h1>
        <Link
          href={`/pog/${year}`}
          className="shrink-0 text-xs text-emerald-600 underline dark:text-emerald-400"
        >
          順位表へ
        </Link>
      </div>

      {horses.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          指名馬がありません。
        </p>
      ) : (
        <ul className="space-y-2">
          {horses.map((h) => (
            <li
              key={`${h.user_id}-${h.netkeiba_horse_id ?? h.pick_order}`}
              className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
            >
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate font-medium">
                  {h.horse_name ?? "（未命名）"}
                  {h.sex && (
                    <span className="ml-1 text-xs text-neutral-500">{h.sex}</span>
                  )}
                </span>
                <span className="shrink-0 tabular-nums text-sm">
                  {formatRecord(h)}
                </span>
                <span className="shrink-0 tabular-nums font-semibold">
                  {formatPrize(h.prize)}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-neutral-500 dark:text-neutral-400">
                {!userId && h.owner_name && <span>{h.owner_name}</span>}
                {h.sire && <span className="truncate">父 {h.sire}</span>}
                {h.broodmare && <span className="truncate">母 {h.broodmare}</span>}
                {h.stable && <span className="truncate">{h.stable}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
