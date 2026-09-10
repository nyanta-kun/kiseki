import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { fetchPogGradedWins, fetchPogGroups, type PogGradedWin } from "@/lib/pog";
import { PogShell } from "../../PogShell";
import { Card, CardHeader, EmptyState, GradeBadge, StatTile } from "../../ui";

export const metadata: Metadata = {
  title: "POG 記録室 | GallopLab",
};

/** `YYYY-MM-DD` → `YYYY.M.D`。 */
function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${y}.${Number(m)}.${Number(d)}`;
}

/** 格ごとの件数。`G1` / `Jpn1` のどちらも末尾の数字で束ねる。 */
function countByGrade(wins: PogGradedWin[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const w of wins) {
    const key = w.grade
      ? w.grade.endsWith("1")
        ? "G1"
        : w.grade.endsWith("2")
          ? "G2"
          : "G3"
      : "その他";
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
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
  const byOwner = new Map<string, PogGradedWin[]>();
  for (const w of wins) {
    const key = w.owner_name ?? `ユーザー${w.user_id}`;
    byOwner.set(key, [...(byOwner.get(key) ?? []), w]);
  }
  const owners = [...byOwner.entries()].sort((a, b) => b[1].length - a[1].length);

  const grades = countByGrade(wins);
  const jra = wins.filter((w) => w.source === "jra").length;

  return (
    <PogShell
      title="POG 記録室"
      description="指名馬が勝った重賞（中央・地方の交流を含む）"
      year={year}
      groups={groups}
      yearBasePath="/pog/:year/records"
    >
      {wins.length === 0 ? (
        <EmptyState>{year} 年度はまだ重賞勝ちがありません。</EmptyState>
      ) : (
        <>
          <div
            className="mb-2 flex items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-[11px] text-surface-muted md:hidden"
            style={{ background: "var(--pog-card)", border: "1px solid var(--pog-card-border)" }}
          >
            <span className="tabular-nums font-semibold" style={{ color: "var(--pog-accent)" }}>
              {wins.length}勝
            </span>
            <span className="truncate tabular-nums">
              {["G1", "G2", "G3", "その他"]
                .filter((g) => grades[g])
                .map((g) => `${g}×${grades[g]}`)
                .join(" ") || "—"}
            </span>
            <span className="tabular-nums">
              中央{jra} / 地方{wins.length - jra}
            </span>
          </div>
          <div className="mb-4 hidden gap-2 md:grid md:grid-cols-4">
            <StatTile label="重賞勝ち 合計" value={`${wins.length} 勝`} accent />
            <StatTile
              label="格の内訳"
              value={
                ["G1", "G2", "G3", "その他"]
                  .filter((g) => grades[g])
                  .map((g) => `${g}×${grades[g]}`)
                  .join(" ") || "—"
              }
            />
            <StatTile label="中央 / 地方" value={`${jra} / ${wins.length - jra}`} />
            <StatTile label="勝った人" value={`${owners.length} 人`} />
          </div>

          {/* PC は 2 列。オーナーごとのカードは高さがまちまちなので、
              段組み（columns）ではなくグリッドにして頭を揃える。 */}
          <div className="grid gap-2 md:gap-3 lg:grid-cols-2">
            {owners.map(([owner, list]) => (
              <Card key={owner} as="section">
                <CardHeader title={owner} meta={`${list.length} 勝`} />
                <ul>
                  {list.map((w) => (
                    <li
                      key={`${w.source}-${w.race_id}-${w.netkeiba_horse_id}`}
                      className="border-b px-3 py-1.5 last:border-b-0"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <div className="flex items-baseline gap-2">
                        <GradeBadge grade={w.grade} />
                        <Link
                          href={
                            w.source === "chihou"
                              ? `/chihou/races/${w.race_id}`
                              : `/races/${w.race_id}`
                          }
                          className="min-w-0 shrink truncate text-sm underline-offset-2 hover:underline"
                          style={{ color: "var(--pog-accent)" }}
                        >
                          {w.race_name ?? `${w.race_no}R`}
                        </Link>
                        {/* 勝ち馬は同じ行に置く。⚠️ 2 行にすると 1 勝 52px になり、
                            勝ち数が伸びた年に一覧性が落ちる。 */}
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-surface-heading">
                          🥇{" "}
                          {w.netkeiba_horse_id ? (
                            <Link
                              href={`https://db.netkeiba.com/horse/${w.netkeiba_horse_id}/`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="underline-offset-2 hover:underline"
                            >
                              {w.horse_name ?? "（未命名）"}
                            </Link>
                          ) : (
                            (w.horse_name ?? "（未命名）")
                          )}
                        </span>
                        <span className="shrink-0 text-[11px] tabular-nums text-surface-muted">
                          {formatDate(w.date)}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        </>
      )}
    </PogShell>
  );
}
