import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  fetchPogGroups,
  fetchPogRanking,
  fetchPogRankingMetrics,
  type PogRankingRow,
} from "@/lib/pog";
import { PogShell } from "../../PogShell";
import { Card, CardHeader, EmptyState, RankBadge } from "../../ui";

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

/** 重賞の内訳。持っている行だけ出す。 */
function GradeCounts({ row }: { row: PogRankingRow }) {
  const items: [string, number][] = [
    ["G1", row.g1],
    ["G2", row.g2],
    ["G3", row.g3],
  ];
  const shown = items.filter(([, n]) => n > 0);
  if (shown.length === 0) return null;
  return (
    <span className="flex shrink-0 gap-1">
      {shown.map(([g, n]) => (
        <span
          key={g}
          className="rounded px-1 py-0.5 text-[10px] font-bold"
          style={{ background: "var(--pog-accent-soft)", color: "var(--pog-accent)" }}
        >
          {g}×{n}
        </span>
      ))}
    </span>
  );
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
  const metric = rawMetric && rawMetric in metrics ? rawMetric : DEFAULT_METRIC;
  // scope=all で通算（移設元の「全グループ」）。既定はその年度。
  const isAll = scope === "all";
  const ranking = await fetchPogRanking(metric, isAll ? undefined : year);

  const link = (m: string, s: string) => `/pog/${year}/rankings?metric=${m}&scope=${s}`;
  const maxValue = ranking.rows.reduce((m, r) => Math.max(m, r.value), 0);

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2">
      {/* スコープ */}
      <div
        className="flex shrink-0 rounded-lg p-0.5"
        style={{ background: "var(--pog-card)", border: "1px solid var(--pog-card-border)" }}
      >
        {[
          { key: "year", label: `${year} 年度`, on: !isAll },
          { key: "all", label: "通算", on: isAll },
        ].map((s) => (
          <Link
            key={s.key}
            href={link(metric, s.key)}
            aria-current={s.on ? "page" : undefined}
            className="rounded-md px-3 py-1 text-xs font-medium transition-colors"
            style={
              s.on
                ? { background: "var(--pog-accent)", color: "#fff" }
                : { color: "var(--surface-muted)" }
            }
          >
            {s.label}
          </Link>
        ))}
      </div>

      {/* 指標 */}
      <div className="flex flex-wrap gap-1">
        {Object.entries(metrics).map(([key, label]) => {
          const on = key === metric;
          return (
            <Link
              key={key}
              href={link(key, isAll ? "all" : "year")}
              aria-current={on ? "page" : undefined}
              className="rounded-md px-2 py-1 text-xs transition-colors"
              style={
                on
                  ? {
                      background: "var(--pog-accent-soft)",
                      color: "var(--pog-accent)",
                      border: "1px solid var(--pog-accent)",
                      fontWeight: 600,
                    }
                  : {
                      background: "var(--pog-card)",
                      color: "var(--surface-muted)",
                      border: "1px solid var(--pog-card-border)",
                    }
              }
            >
              {label}
            </Link>
          );
        })}
      </div>
    </div>
  );

  return (
    <PogShell
      title="POG ランキング"
      description="指名馬を種牡馬・母父・厩舎などで束ねた集計"
      year={year}
      groups={groups}
      yearBasePath="/pog/:year/rankings"
      toolbar={toolbar}
    >
      {ranking.rows.length === 0 ? (
        <EmptyState>該当がありません。</EmptyState>
      ) : (
        <Card>
          <CardHeader
            title={ranking.label}
            meta={`${isAll ? "通算" : `${year} 年度`} / ${ranking.rows.length} 件`}
          />
          {/* 上位が長くなるので PC は 2 段に折り返す（CSS の段組み）。
              左段を上から埋めてから右段へ移るので、順位は読み下しのまま。
              ⚠️ 段の間の仕切りは `column-rule`。`border-l` を偶数番目に付ける
                 やり方では段の境目と一致しない（何番目で折り返すかは高さ次第）。 */}
          <ol
            className="lg:columns-2 lg:gap-0"
            style={{ columnRule: "1px solid var(--pog-card-border)" }}
          >
            {ranking.rows.map((r, i) => (
              <li
                key={r.key}
                // 🔴 相対量は**行の背景**で見せる（2026-09-10）。棒を別の行に置くと
                //    1 件 56px になり、30 件で 1,680px＝画面 2.5 枚ぶんになる。
                //    背景なら 1 行 37px に収まり、量の比較もできる。
                className="relative isolate break-inside-avoid border-b px-3 py-1.5 last:border-b-0"
                style={{ borderColor: "var(--pog-card-border)" }}
              >
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-0 -z-10"
                  style={{
                    width: `${maxValue > 0 ? Math.max(1.5, (r.value / maxValue) * 100) : 0}%`,
                    background: "var(--pog-accent-soft)",
                  }}
                />
                <div className="flex items-baseline gap-2">
                  <RankBadge rank={i + 1} />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-surface-heading">
                    {r.key}
                  </span>
                  <GradeCounts row={r} />
                  <span className="hidden shrink-0 text-[11px] text-surface-muted sm:inline">
                    {formatSub(metric, r) || `${r.count} 頭`}
                  </span>
                  <span className="shrink-0 text-sm font-semibold tabular-nums text-surface-heading">
                    {formatValue(metric, r)}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </Card>
      )}

      {metric === "sire-win-rate" && (
        <p className="mt-3 text-xs text-surface-muted">
          ⚠️ 出走が 1 走でも母数に入るため、上位は少数の産駒で埋まりやすい（移設元と同じ）。
        </p>
      )}
      {metric === "stable-ranking" && (
        <p className="mt-3 text-xs text-surface-muted">
          ⚠️ 美浦 / 栗東の東西分けは出せない（所属地のデータが kiseki に無いため）。
        </p>
      )}
    </PogShell>
  );
}
