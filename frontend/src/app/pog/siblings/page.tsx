import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { fetchPogGroups, fetchPogSiblings, formatPrize } from "@/lib/pog";
import { PogShell } from "../PogShell";
import { Card, CardHeader, Collapsible, EmptyState, RecordPills, StatTile } from "../ui";

export const metadata: Metadata = {
  title: "POG 兄弟馬 | GallopLab",
};

export default async function PogSiblingsPage() {
  const [groups, siblings] = await Promise.all([
    fetchPogGroups(),
    fetchPogSiblings(),
  ]);
  // 年度に依らないページだが、外枠（年度・ページタブ）は共通にする。
  // 最新年度を「今いる年」として扱う。
  const latest = groups[0]?.year;
  if (latest === undefined) notFound();

  const horses = siblings.flatMap((g) => g.horses);
  const totalPrize = horses.reduce((a, h) => a + h.prize, 0);
  const best = horses.reduce(
    (b, h) => (b === null || h.prize > b.prize ? h : b),
    null as (typeof horses)[number] | null,
  );

  return (
    <PogShell
      title="POG 兄弟馬"
      description="同じ母から 2 回以上指名された馬（父は問わない）"
      year={latest}
      groups={groups}
      showYear={false}
    >
      {siblings.length === 0 ? (
        <EmptyState>該当がありません。</EmptyState>
      ) : (
        <>
          <div
            className="mb-2 flex items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-[11px] text-surface-muted md:hidden"
            style={{ background: "var(--pog-card)", border: "1px solid var(--pog-card-border)" }}
          >
            <span className="tabular-nums">母 {siblings.length}頭</span>
            <span className="tabular-nums">のべ {horses.length}頭</span>
            <span className="tabular-nums font-semibold" style={{ color: "var(--pog-accent)" }}>
              {formatPrize(totalPrize)}
            </span>
          </div>
          <div className="mb-4 hidden gap-2 md:grid md:grid-cols-4">
            <StatTile label="対象の母" value={`${siblings.length} 頭`} />
            <StatTile label="のべ指名" value={`${horses.length} 頭`} />
            <StatTile label="獲得賞金 合計" value={formatPrize(totalPrize)} accent />
            <StatTile
              label="最高賞金"
              value={best?.horse_name ?? "—"}
              sub={best ? formatPrize(best.prize) : undefined}
            />
          </div>

          {/* 🔴 スマホは畳む。母は数十頭あり、開いたまま並べると
                 「どの母が何回指名されたか」を見るだけで延々とスクロールになる。 */}
          <div className="grid gap-2 md:hidden">
            {siblings.map((g) => (
              <Collapsible
                key={g.broodmare}
                title={g.broodmare}
                meta={`${g.nomination_count} 回`}
              >
                <ul>
                  {g.horses.map((h) => (
                    <li
                      key={h.netkeiba_horse_id}
                      className="border-b px-3 py-1.5 last:border-b-0"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="w-9 shrink-0 text-[11px] tabular-nums text-surface-muted">
                          {h.year}
                        </span>
                        <Link
                          href={`https://db.netkeiba.com/horse/${h.netkeiba_horse_id}/`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="min-w-0 flex-1 truncate text-sm font-medium text-surface-heading"
                        >
                          {h.horse_name}
                        </Link>
                        <span className="shrink-0 text-[11px]">
                          <RecordPills {...h} />
                        </span>
                        <span className="min-w-16 shrink-0 whitespace-nowrap text-right text-xs font-semibold tabular-nums text-surface-heading">
                          {h.prize > 0 ? formatPrize(h.prize) : "—"}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </Collapsible>
            ))}
          </div>
          <div className="hidden gap-3 md:grid lg:grid-cols-2">
            {siblings.map((g) => (
              <Card key={g.broodmare} as="section">
                <CardHeader title={g.broodmare} meta={`${g.nomination_count} 回指名`} />
                <ul>
                  {g.horses.map((h) => (
                    <li
                      key={h.netkeiba_horse_id}
                      className="border-b px-3 py-2 last:border-b-0"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <div className="flex items-baseline gap-2">
                        <Link
                          href={`/pog/${h.year}/horses`}
                          className="w-11 shrink-0 text-xs tabular-nums text-surface-muted underline-offset-2 hover:underline"
                        >
                          {h.year}
                        </Link>
                        <Link
                          href={`https://db.netkeiba.com/horse/${h.netkeiba_horse_id}/`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="min-w-0 flex-1 truncate font-medium text-surface-heading underline-offset-2 hover:underline"
                        >
                          {h.horse_name}
                        </Link>
                        <span className="shrink-0 text-xs">
                          <RecordPills {...h} />
                        </span>
                        <span className="min-w-16 shrink-0 whitespace-nowrap text-right text-sm font-semibold tabular-nums text-surface-heading">
                          {h.prize > 0 ? formatPrize(h.prize) : "—"}
                        </span>
                      </div>
                      <div className="mt-0.5 flex flex-wrap gap-x-3 pl-13 text-[11px] text-surface-muted">
                        {h.sire && <span>父 {h.sire}</span>}
                        {h.owner_name && <span>{h.owner_name}</span>}
                        {h.stable && <span>{h.stable}</span>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>

          <p className="mt-4 text-xs text-surface-muted">
            ⚠️ 2017年度以前の指名馬は戦績が出ない（中央の馬マスタが 2013年産以前で
            極端に薄いため）。
          </p>
        </>
      )}
    </PogShell>
  );
}
