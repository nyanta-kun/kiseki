import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  fetchPogGroups,
  fetchPogHorses,
  fetchPogOwners,
  formatPrize,
  type PogHorse,
} from "@/lib/pog";
import { PogShell } from "../../PogShell";
import { Card, CardHeader, Collapsible, EmptyState, RecordPills, StatTile } from "../../ui";

export const metadata: Metadata = {
  title: "POG 指名馬 | GallopLab",
};

/** 馬名。netkeiba の馬 ID があれば馬ページへ張る（下調べの導線）。 */
function HorseName({ horse }: { horse: PogHorse }) {
  const label = horse.horse_name ?? "（未命名）";
  if (!horse.netkeiba_horse_id) {
    return <span className="font-medium text-surface-heading">{label}</span>;
  }
  return (
    <Link
      href={`https://db.netkeiba.com/horse/${horse.netkeiba_horse_id}/`}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-surface-heading underline-offset-2 hover:underline"
    >
      {label}
    </Link>
  );
}

/** 出走のあった馬か。まだ走っていない馬は薄く出す。 */
function hasRun(h: PogHorse): boolean {
  return h.win + h.place + h.show + h.out > 0;
}

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

  const parsed = rawUser ? Number(rawUser) : undefined;
  const userId = Number.isInteger(parsed) ? parsed : undefined;

  // オーナーの一覧は絞り込みの有無に依らず要る（切り替えのチップに使う）。
  const [horses, owners] = await Promise.all([
    fetchPogHorses(year, userId),
    fetchPogOwners(year),
  ]);
  const ownerName = owners.find((o) => o.user_id === userId)?.name ?? null;

  // オーナーごとに束ねる（全馬表示のとき）。API は user_id → pick_order の順で
  // 返すので、出てきた順にグループを作れば並びは保たれる。
  const byOwner = new Map<number, { name: string | null; horses: PogHorse[] }>();
  for (const h of horses) {
    const g = byOwner.get(h.user_id) ?? { name: h.owner_name, horses: [] };
    g.horses.push(h);
    byOwner.set(h.user_id, g);
  }

  const totalPrize = horses.reduce((a, h) => a + h.prize, 0);
  const raced = horses.filter(hasRun).length;
  const won = horses.filter((h) => h.win > 0).length;

  const toolbar = (
    <div className="-mx-3 overflow-x-auto px-3 md:mx-0 md:px-0">
      <div className="flex w-max gap-1.5 md:w-full md:flex-wrap">
        <Link
          href={`/pog/${year}/horses`}
          aria-current={userId === undefined ? "page" : undefined}
          className="shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors"
          style={
            userId === undefined
              ? { background: "var(--pog-accent)", color: "#fff" }
              : {
                  background: "var(--pog-card)",
                  color: "var(--surface-muted)",
                  border: "1px solid var(--pog-card-border)",
                }
          }
        >
          全馬
        </Link>
        {owners.map((o) => {
          const on = o.user_id === userId;
          return (
            <Link
              key={o.user_id}
              href={`/pog/${year}/horses?user_id=${o.user_id}`}
              aria-current={on ? "page" : undefined}
              className="shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors"
              style={
                on
                  ? { background: "var(--pog-accent)", color: "#fff" }
                  : {
                      background: "var(--pog-card)",
                      color: "var(--surface-muted)",
                      border: "1px solid var(--pog-card-border)",
                    }
              }
            >
              {o.name ?? `ユーザー${o.user_id}`}
            </Link>
          );
        })}
      </div>
    </div>
  );

  return (
    <PogShell
      title={userId ? `${ownerName ?? "指名馬"} の指名馬` : "POG 指名馬"}
      description={
        userId
          ? "馬名から netkeiba の馬ページへ飛べる"
          : "オーナーを選ぶと 1 人ぶんだけ出る"
      }
      year={year}
      groups={groups}
      yearBasePath="/pog/:year/horses"
      toolbar={toolbar}
    >
      {horses.length === 0 ? (
        <EmptyState>指名馬がありません。</EmptyState>
      ) : (
        <>
          <div
            className="mb-2 flex items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-[11px] text-surface-muted md:hidden"
            style={{ background: "var(--pog-card)", border: "1px solid var(--pog-card-border)" }}
          >
            <span className="tabular-nums">{horses.length}頭</span>
            <span className="tabular-nums font-semibold" style={{ color: "var(--pog-accent)" }}>
              {formatPrize(totalPrize)}
            </span>
            <span className="tabular-nums">出走 {raced}</span>
            <span className="tabular-nums">勝ち上がり {won}</span>
          </div>
          <div className="mb-4 hidden gap-2 md:grid md:grid-cols-4">
            <StatTile label="指名頭数" value={`${horses.length} 頭`} />
            <StatTile label="獲得賞金" value={formatPrize(totalPrize)} accent />
            <StatTile
              label="出走済み"
              value={`${raced} 頭`}
              sub={`未出走 ${horses.length - raced} 頭`}
            />
            <StatTile
              label="勝ち上がり"
              value={`${won} 頭`}
              sub={raced > 0 ? `出走馬の ${((won / raced) * 100).toFixed(0)}%` : undefined}
            />
          </div>

          {userId ? (
            /* 1 人ぶん: 血統まで出す表（PC）とカード（スマホ）。 */
            <Card>
              <CardHeader
                title={ownerName ?? `ユーザー${userId}`}
                meta={`${horses.length} 頭`}
              />
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full text-sm">
                  <thead>
                    <tr
                      className="border-b text-xs text-surface-muted"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <th className="px-3 py-2 text-left font-medium">巡</th>
                      <th className="px-3 py-2 text-left font-medium">馬名</th>
                      <th className="px-3 py-2 text-left font-medium">性</th>
                      <th className="px-3 py-2 text-left font-medium">父</th>
                      <th className="px-3 py-2 text-left font-medium">母（母父）</th>
                      <th className="px-3 py-2 text-left font-medium">厩舎</th>
                      <th className="px-3 py-2 text-left font-medium">成績</th>
                      <th className="px-3 py-2 text-right font-medium">賞金</th>
                    </tr>
                  </thead>
                  <tbody>
                    {horses.map((h) => (
                      <tr
                        key={`${h.user_id}-${h.netkeiba_horse_id ?? h.pick_order}`}
                        className={`border-b last:border-b-0 ${hasRun(h) ? "" : "opacity-60"}`}
                        style={{ borderColor: "var(--pog-card-border)" }}
                      >
                        <td className="px-3 py-2 tabular-nums text-surface-muted">
                          {h.pick_order ?? "—"}
                        </td>
                        <td className="px-3 py-2">
                          <HorseName horse={h} />
                        </td>
                        <td className="px-3 py-2 text-surface-muted">{h.sex ?? "—"}</td>
                        <td className="max-w-[10rem] truncate px-3 py-2 text-surface-muted">
                          {h.sire ?? "—"}
                        </td>
                        <td className="max-w-[14rem] truncate px-3 py-2 text-surface-muted">
                          {h.broodmare ?? "—"}
                          {h.broodmare_sire && (
                            <span className="opacity-70">（{h.broodmare_sire}）</span>
                          )}
                        </td>
                        <td className="max-w-[8rem] truncate px-3 py-2 text-surface-muted">
                          {h.stable ?? "—"}
                        </td>
                        <td className="px-3 py-2">
                          <RecordPills {...h} />
                        </td>
                        <td className="px-3 py-2 text-right font-semibold tabular-nums text-surface-heading">
                          {h.prize > 0 ? formatPrize(h.prize) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="md:hidden">
                {horses.map((h) => (
                  <li
                    key={`${h.user_id}-${h.netkeiba_horse_id ?? h.pick_order}`}
                    className={`border-b px-3 py-2.5 last:border-b-0 ${hasRun(h) ? "" : "opacity-60"}`}
                    style={{ borderColor: "var(--pog-card-border)" }}
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="w-5 shrink-0 text-xs tabular-nums text-surface-muted">
                        {h.pick_order ?? "—"}
                      </span>
                      <span className="min-w-0 flex-1 truncate">
                        <HorseName horse={h} />
                        {h.sex && <span className="ml-1 text-xs text-surface-muted">{h.sex}</span>}
                      </span>
                      <span className="shrink-0 text-xs">
                        <RecordPills {...h} />
                      </span>
                      <span className="shrink-0 font-semibold tabular-nums text-surface-heading">
                        {h.prize > 0 ? formatPrize(h.prize) : "—"}
                      </span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 pl-7 text-xs text-surface-muted">
                      {h.sire && <span className="truncate">父 {h.sire}</span>}
                      {h.broodmare && <span className="truncate">母 {h.broodmare}</span>}
                      {h.stable && <span className="truncate">{h.stable}</span>}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          ) : (
            <>
            {/* 全馬: オーナーごとにまとめる。
               🔴 スマホは**畳む**（2026-09-10）。9 人 × 7 頭 = 63 行を開いたまま
                  並べると 2,000px を超え、誰が何頭持っているかを見るだけで
                  延々とスクロールすることになる。畳めば 9 行で一望できる。
               PC は横に 2〜3 列並ぶので畳む必要がない。 */}
            <div className="grid gap-2 md:hidden">
              {[...byOwner.entries()].map(([uid, g]) => {
                const prize = g.horses.reduce((a, h) => a + h.prize, 0);
                return (
                  <Collapsible
                    key={uid}
                    title={g.name ?? `ユーザー${uid}`}
                    meta={
                      <>
                        {g.horses.length} 頭{" "}
                        <span className="font-semibold" style={{ color: "var(--pog-accent)" }}>
                          {formatPrize(prize)}
                        </span>
                      </>
                    }
                  >
                    <ul>
                      {g.horses.map((h) => (
                        <li
                          key={h.netkeiba_horse_id ?? `${uid}-${h.pick_order}`}
                          className={`border-b px-3 py-1.5 last:border-b-0 ${hasRun(h) ? "" : "opacity-60"}`}
                          style={{ borderColor: "var(--pog-card-border)" }}
                        >
                          <div className="flex items-baseline gap-2">
                            <span className="w-4 shrink-0 text-[11px] tabular-nums text-surface-muted">
                              {h.pick_order ?? "—"}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-sm">
                              <HorseName horse={h} />
                            </span>
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
                );
              })}
            </div>
            <div className="hidden gap-3 md:grid md:grid-cols-2 xl:grid-cols-3">
              {[...byOwner.entries()].map(([uid, g]) => {
                const prize = g.horses.reduce((a, h) => a + h.prize, 0);
                return (
                  <Card key={uid} className="flex flex-col">
                    <CardHeader
                      title={
                        <Link
                          href={`/pog/${year}/horses?user_id=${uid}`}
                          className="underline-offset-2 hover:underline"
                        >
                          {g.name ?? `ユーザー${uid}`}
                        </Link>
                      }
                      meta={
                        <span style={{ color: "var(--pog-accent)" }} className="font-semibold">
                          {formatPrize(prize)}
                        </span>
                      }
                    />
                    <ul className="flex-1">
                      {g.horses.map((h) => (
                        <li
                          key={h.netkeiba_horse_id ?? `${uid}-${h.pick_order}`}
                          className={`border-b px-3 py-1.5 last:border-b-0 ${hasRun(h) ? "" : "opacity-60"}`}
                          style={{ borderColor: "var(--pog-card-border)" }}
                        >
                          <div className="flex items-baseline gap-2">
                            <span className="w-4 shrink-0 text-[11px] tabular-nums text-surface-muted">
                              {h.pick_order ?? "—"}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-sm">
                              <HorseName horse={h} />
                            </span>
                            <span className="shrink-0 text-[11px]">
                              <RecordPills {...h} />
                            </span>
                            <span className="min-w-16 shrink-0 whitespace-nowrap text-right text-xs font-semibold tabular-nums text-surface-heading">
                              {h.prize > 0 ? formatPrize(h.prize) : "—"}
                            </span>
                          </div>
                          {h.sire && (
                            <div className="truncate pl-6 text-[11px] text-surface-muted">
                              父 {h.sire}
                              {h.stable && ` / ${h.stable}`}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </Card>
                );
              })}
            </div>
            </>
          )}
        </>
      )}
    </PogShell>
  );
}
