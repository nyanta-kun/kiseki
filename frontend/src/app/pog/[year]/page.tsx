import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  currentWeekRange,
  fetchGradedRaces,
  fetchPogGroups,
  fetchPogOwners,
  fetchPogOwnersAsOf,
  fetchPogRecentRaces,
  formatPrize,
  type PogOwner,
} from "@/lib/pog";
import { GradedRaces } from "../GradedRaces";
import { PogShell } from "../PogShell";
import { RecentRaces } from "../RecentRaces";
import {
  Card,
  CardHeader,
  Collapsible,
  EmptyState,
  RankBadge,
  RecordPills,
  StatTile,
} from "../ui";

export const metadata: Metadata = {
  title: "POG 順位表 | GallopLab",
};

/**
 * 順位変動の矢印。1 週間前の順位と比べる。
 *
 * ⚠️ 上昇＝赤・下降＝青（競馬の慣習）。色だけで伝えないよう数字を必ず添える。
 */
function RankDelta({ now, before }: { now: number; before: number | undefined }) {
  if (before === undefined || before === now) {
    return (
      <span className="text-surface-muted opacity-50" aria-label="変動なし">
        —
      </span>
    );
  }
  const up = before > now;
  return (
    <span
      className={`tabular-nums ${up ? "text-rose-500" : "text-sky-500"}`}
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

/**
 * 上位 3 人の表彰台。
 *
 * PC は横 3 枚、スマホは 1 位を大きく・2〜3 位を横並びにする（縦に 3 枚積むと
 * 順位表本体が画面の外へ押し出されるため）。
 */
function Podium({ owners, year }: { owners: PogOwner[]; year: number }) {
  const top = owners.slice(0, 3);
  if (top.length === 0) return null;
  const tone: Record<number, string> = {
    1: "from-amber-100 to-transparent dark:from-amber-900/30",
    2: "from-slate-100 to-transparent dark:from-slate-800/50",
    3: "from-orange-100 to-transparent dark:from-orange-900/30",
  };
  return (
    // 🔴 **スマホでは出さない**（2026-09-10）。上位 3 人は順位表の先頭 3 行そのもので、
    //    メダル色の順位バッジで既に見分けが付く。表彰台を積むと約 150px を使い、
    //    その下の順位表が画面外へ落ちる。
    <div className="mb-4 hidden grid-cols-2 gap-2 md:grid sm:grid-cols-3">
      {top.map((o, i) => (
        <Card
          key={o.user_id}
          className={`bg-gradient-to-b ${tone[o.rank] ?? ""} ${
            // 1 位はスマホで横幅いっぱい（2 列ぶん）を取る。
            i === 0 ? "col-span-2 sm:col-span-1" : ""
          }`}
        >
          <Link
            href={`/pog/${year}/horses?user_id=${o.user_id}`}
            className="block px-3 py-2.5"
          >
            <div className="flex items-center gap-2">
              <RankBadge rank={o.rank} />
              <span className="min-w-0 flex-1 truncate font-semibold text-surface-heading">
                {o.name ?? `ユーザー${o.user_id}`}
              </span>
            </div>
            <div
              className="mt-1.5 text-lg font-bold tabular-nums"
              style={{ color: "var(--pog-accent)" }}
            >
              {formatPrize(o.prize)}
            </div>
            <div className="mt-0.5 text-xs text-surface-muted">
              <RecordPills {...o} />
              {o.top_horse && <span className="ml-2 truncate">最高 {o.top_horse}</span>}
            </div>
          </Link>
        </Card>
      ))}
    </div>
  );
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
  const [weekStart, weekEnd] = currentWeekRange();
  const [owners, lastWeek, recent, graded] = await Promise.all([
    fetchPogOwners(year),
    fetchPogOwnersAsOf(year, isoDaysAgo(7)),
    // 今週の出走。過去年度でも今週の枠で引くので、たいていは空になる。
    fetchPogRecentRaces(year).catch(() => []),
    // 今週の重賞。POG の年度に依らないので、落ちても順位表は出す。
    fetchGradedRaces(weekStart, weekEnd).catch(() => []),
  ]);
  const before = new Map(lastWeek.map((o) => [o.user_id, o.rank]));

  const totalPrize = owners.reduce((a, o) => a + o.prize, 0);
  const totalWins = owners.reduce((a, o) => a + o.win, 0);
  const totalRuns = owners.reduce((a, o) => a + o.win + o.place + o.show + o.out, 0);
  const settled = recent.filter((r) => r.result !== null).length;

  // ⚠️ 説明文はスマホで 2 行に折り返すと 14px 損する。11px・390px 幅に 1 行で
  //    載る長さにしてある（実測）。伸ばすときは実機幅で折り返しを確かめること。
  return (
    <PogShell
      title="POG 順位表"
      description="賞金は本賞金の合計（万円）／矢印は前週比"
      year={year}
      groups={groups}
    >
      {owners.length === 0 ? (
        <EmptyState>{year} 年度の指名がありません。</EmptyState>
      ) : (
        <>
          {/* 年度のあらまし。
              🔴 スマホは**タイル 4 枚（約 130px）をやめて 1 行の帯**（約 30px）にする。
                 数字は「ついでに目に入る」もので、順位表より先に場所を取ってはいけない。 */}
          <div
            className="mb-2 flex items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-[11px] text-surface-muted md:hidden"
            style={{ background: "var(--pog-card)", border: "1px solid var(--pog-card-border)" }}
          >
            <span className="tabular-nums">{owners.length}人</span>
            <span className="tabular-nums font-semibold" style={{ color: "var(--pog-accent)" }}>
              {formatPrize(totalPrize)}
            </span>
            <span className="tabular-nums">
              {totalWins}勝 / {totalRuns}走
            </span>
            <span className="tabular-nums">今週 {recent.length}頭</span>
          </div>
          <div className="mb-4 hidden gap-2 md:grid md:grid-cols-4">
            <StatTile label="参加オーナー" value={`${owners.length} 人`} />
            <StatTile label="獲得賞金 合計" value={formatPrize(totalPrize)} accent />
            <StatTile
              label="勝利 / 出走"
              value={`${totalWins} / ${totalRuns}`}
              sub={totalRuns > 0 ? `勝率 ${((totalWins / totalRuns) * 100).toFixed(1)}%` : undefined}
            />
            <StatTile
              label="今週の出走"
              value={`${recent.length} 頭`}
              sub={recent.length > 0 ? `確定 ${settled} 頭` : "まだ登録がありません"}
            />
          </div>

          <Podium owners={owners} year={year} />

          {/* 本体。PC は右に「今週」の縦列を置く。 */}
          <div className="grid gap-2 md:gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="min-w-0">
              <Card>
                {/* 見出しはページタイトルと重複するので、スマホでは出さない
                    （1 行 = 約 34px は順位表 1 人ぶんに相当する）。 */}
                <div className="hidden md:block">
                  <CardHeader title="順位表" meta={`${owners.length} 人`} />
                </div>

                {/* PC: 表。列が多いので横並びのほうが比べやすい。 */}
                <div className="hidden overflow-x-auto md:block">
                  <table className="w-full text-sm">
                    <thead>
                      <tr
                        className="border-b text-xs text-surface-muted"
                        style={{ borderColor: "var(--pog-card-border)" }}
                      >
                        <th className="px-3 py-2 text-left font-medium">順位</th>
                        <th className="px-3 py-2 text-left font-medium">オーナー</th>
                        <th className="px-3 py-2 text-center font-medium">変動</th>
                        <th className="px-3 py-2 text-left font-medium">成績</th>
                        <th className="px-3 py-2 text-right font-medium">賞金</th>
                        <th className="px-3 py-2 text-right font-medium">うち地方</th>
                        <th className="px-3 py-2 text-left font-medium">最高賞金の馬</th>
                      </tr>
                    </thead>
                    <tbody>
                      {owners.map((o) => (
                        <tr
                          key={o.user_id}
                          className="border-b last:border-b-0 transition-colors hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
                          style={{ borderColor: "var(--pog-card-border)" }}
                        >
                          <td className="px-3 py-2">
                            <RankBadge rank={o.rank} />
                          </td>
                          <td className="px-3 py-2">
                            <Link
                              href={`/pog/${year}/horses?user_id=${o.user_id}`}
                              className="font-medium text-surface-heading underline-offset-2 hover:underline"
                            >
                              {o.name ?? `ユーザー${o.user_id}`}
                            </Link>
                          </td>
                          <td className="px-3 py-2 text-center">
                            <RankDelta now={o.rank} before={before.get(o.user_id)} />
                          </td>
                          <td className="px-3 py-2">
                            <RecordPills {...o} />
                          </td>
                          <td className="px-3 py-2 text-right font-semibold tabular-nums text-surface-heading">
                            {formatPrize(o.prize)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-surface-muted">
                            {o.prize_other > 0 ? formatPrize(o.prize_other) : "—"}
                          </td>
                          <td className="max-w-[12rem] truncate px-3 py-2 text-surface-muted">
                            {o.top_horse ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* スマホ: **1 人 1 行**（約 36px）。9 人でも 1 画面に収まる。
                    🔴 2 行目（成績・地方・最高馬）をやめたのは意図的。あれを足すと
                       1 行 54px になり 9 人で 486px ＝ 使える高さをほぼ食い切る。
                       成績はこの行に畳み込み、最高馬はタップ 1 回先の指名馬ページにある。 */}
                <ol className="md:hidden">
                  {owners.map((o) => (
                    <li
                      key={o.user_id}
                      className="border-b last:border-b-0"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <Link
                        href={`/pog/${year}/horses?user_id=${o.user_id}`}
                        className="flex items-center gap-2 px-3 py-2"
                      >
                        <RankBadge rank={o.rank} />
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-surface-heading">
                          {o.name ?? `ユーザー${o.user_id}`}
                        </span>
                        <span className="shrink-0 text-[11px]">
                          <RecordPills {...o} />
                        </span>
                        <span className="w-6 shrink-0 text-right text-[11px]">
                          <RankDelta now={o.rank} before={before.get(o.user_id)} />
                        </span>
                        {/* ⚠️ 固定幅 (`w-`) にしない。桁が増えると「126,488 / 万」と
                            折り返して行が 41px → 85px に膨らむ（320px 幅で実測）。
                            `min-w-` で列を揃えつつ、はみ出す前に名前側が縮む。 */}
                        <span className="min-w-[4.5rem] shrink-0 whitespace-nowrap text-right text-sm font-bold tabular-nums text-surface-heading">
                          {formatPrize(o.prize)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ol>
              </Card>
            </div>

            {/* 今週。PC では右の縦列に開いたまま置く（横に余地があるので畳む理由が無い）。
                🔴 スマホは**畳む**。開いたまま積むと順位表が画面外へ落ちる
                   （出走 1 件で約 64px。5 件で順位表と同じ高さになる）。 */}
            <div className="hidden min-w-0 space-y-4 md:block">
              <Card>
                <CardHeader title="今週の出走" meta={recent.length > 0 ? `${recent.length} 頭` : undefined} />
                <div className="p-2">
                  <RecentRaces races={recent} />
                </div>
              </Card>
              <Card>
                <CardHeader title="今週の重賞" meta={graded.length > 0 ? `${graded.length} 鞍` : undefined} />
                <div className="p-2">
                  <GradedRaces races={graded} />
                </div>
              </Card>
            </div>
            {/* ⚠️ スマホでは出走と重賞を**1 枚に束ねる**。畳んでいても 1 枚 42px で、
                2 枚だと 92px（＝順位表 2 人ぶん）を使う。中で見出しを分けるので
                中身の区別は失われない。 */}
            <div className="md:hidden">
              <Collapsible
                title="今週の出走・重賞"
                meta={`${recent.length} 頭 / ${graded.length} 鞍`}
              >
                <div className="p-2">
                  <RecentRaces races={recent} />
                </div>
                <div
                  className="border-t p-2"
                  style={{ borderColor: "var(--pog-card-border)" }}
                >
                  <h3 className="mb-1 px-1 text-xs font-bold text-surface-heading">
                    今週の重賞
                  </h3>
                  <GradedRaces races={graded} />
                </div>
              </Collapsible>
            </div>
          </div>
        </>
      )}
    </PogShell>
  );
}
