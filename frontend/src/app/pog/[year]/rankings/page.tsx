import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  fetchPogGroups,
  fetchPogRanking,
  fetchPogRankingMetrics,
  type PogRankingRow,
} from "@/lib/pog";
import { YearTabs } from "../../YearTabs";

export const metadata: Metadata = {
  title: "POG ランキング | GallopLab",
};

const DEFAULT_METRIC = "sire-count";

/** 指標ごとに value の見せ方が違う。単位はここで決める。 */
function formatValue(metric: string, row: PogRankingRow): string {
  if (metric.endsWith("-rate")) return `${row.value}%`;
  if (metric.includes("prize") || metric === "stable-ranking" || metric === "horse-performance") {
    return `${row.value.toLocaleString("ja-JP")} 万`;
  }
  return row.value.toLocaleString("ja-JP");
}

/** 補助欄の説明。指標ごとに意味が違うので言葉を添える。 */
function formatSub(metric: string, row: PogRankingRow): string {
  if (row.sub === null) return "";
  switch (metric) {
    case "sire-win-rate":
      return `${row.sub} 勝 / ${row.total} 走`;
    case "horse-performance":
      return `${row.sub} 勝 / ${row.total} 走`;
    case "stable-ranking":
      return `${row.sub} 勝`;
    case "debut-rate":
      return `${row.sub} / ${row.count} 頭が出走`;
    case "win-rate":
      return `${row.sub} / ${row.count} 頭が勝利`;
    case "graded-win-count":
      return `${row.sub} 頭`;
    default:
      return "";
  }
}

export default async function PogRankingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ year: string }>;
  searchParams: Promise<{ metric?: string; scope?: string }>;
}) {
  const { year: raw } = await params;
  const { metric: rawMetric, scope } = await searchParams;
  const year = Number(raw);
  if (!Number.isInteger(year)) notFound();

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  const metrics = await fetchPogRankingMetrics();
  const metric =
    rawMetric && rawMetric in metrics ? rawMetric : DEFAULT_METRIC;
  // scope=all で通算（移設元の「全グループ」）。既定はその年度。
  const isAll = scope === "all";
  const ranking = await fetchPogRanking(metric, isAll ? undefined : year);

  const link = (m: string, s: string) =>
    `/pog/${year}/rankings?metric=${m}&scope=${s}`;

  return (
    <main className="mx-auto max-w-3xl p-4">
      <h1 className="mb-1 text-lg font-bold">POG ランキング</h1>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        指名馬を種牡馬・母父・厩舎・馬主などで束ねた集計。成績は出走のたびに数え直す。
      </p>

      <YearTabs groups={groups} current={year} />

      {/* スコープ */}
      <div className="mb-3 flex gap-2 text-sm">
        <Link
          href={link(metric, "year")}
          className={`rounded px-3 py-1 ${
            isAll
              ? "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
              : "bg-emerald-600 font-medium text-white"
          }`}
        >
          {year} 年度
        </Link>
        <Link
          href={link(metric, "all")}
          className={`rounded px-3 py-1 ${
            isAll
              ? "bg-emerald-600 font-medium text-white"
              : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
          }`}
        >
          通算
        </Link>
      </div>

      {/* 指標 */}
      <div className="mb-4 flex flex-wrap gap-1.5 text-xs">
        {Object.entries(metrics).map(([key, label]) => (
          <Link
            key={key}
            href={link(key, isAll ? "all" : "year")}
            className={`rounded border px-2 py-1 ${
              key === metric
                ? "border-emerald-600 bg-emerald-50 font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                : "border-neutral-200 text-neutral-600 dark:border-neutral-700 dark:text-neutral-300"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>

      <h2 className="mb-2 text-sm font-bold">
        {ranking.label}
        <span className="ml-2 text-xs font-normal text-neutral-500 dark:text-neutral-400">
          {isAll ? "通算" : `${year} 年度`}
        </span>
      </h2>

      {ranking.rows.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          該当がありません。
        </p>
      ) : (
        <ol className="space-y-1.5">
          {ranking.rows.map((r, i) => (
            <li
              key={r.key}
              className="flex items-baseline gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
            >
              <span className="w-6 shrink-0 text-right text-xs tabular-nums text-neutral-400">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 truncate">{r.key}</span>
              <span className="shrink-0 text-xs text-neutral-500 dark:text-neutral-400">
                {formatSub(metric, r)}
              </span>
              <span className="shrink-0 font-semibold tabular-nums">
                {formatValue(metric, r)}
              </span>
            </li>
          ))}
        </ol>
      )}

      {metric === "sire-win-rate" && (
        <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
          ⚠️ 出走が 1 走でも母数に入るため、上位は少数の産駒で埋まりやすい（移設元と同じ）。
        </p>
      )}
      {metric === "stable-ranking" && (
        <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
          ⚠️ 美浦 / 栗東の東西分けは出せない（所属地のデータが kiseki に無いため）。
        </p>
      )}

      <p className="mt-6 flex flex-wrap justify-center gap-4 text-sm">
        <Link
          href={`/pog/${year}`}
          className="text-emerald-600 underline dark:text-emerald-400"
        >
          順位表へ
        </Link>
        <Link
          href={`/pog/${year}/records`}
          className="text-emerald-600 underline dark:text-emerald-400"
        >
          記録室へ
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
