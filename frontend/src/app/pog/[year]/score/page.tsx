import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { fetchPogGroups, fetchPogScores } from "@/lib/pog";
import { YearTabs } from "../../YearTabs";

export const metadata: Metadata = {
  title: "POG スコア集計 | GallopLab",
};

/** 符号付きで出す。プラスは赤、マイナスは青（競馬の慣習に合わせる）。 */
function Pt({ value, bold = false }: { value: number; bold?: boolean }) {
  const cls =
    value > 0
      ? "text-rose-600 dark:text-rose-400"
      : value < 0
        ? "text-sky-600 dark:text-sky-400"
        : "text-neutral-500 dark:text-neutral-400";
  return (
    <span className={`tabular-nums ${cls} ${bold ? "font-bold" : ""}`}>
      {value > 0 ? "+" : ""}
      {value.toLocaleString("ja-JP")}
    </span>
  );
}

export default async function PogScorePage({
  params,
}: {
  params: Promise<{ year: string }>;
}) {
  const { year: raw } = await params;
  const year = Number(raw);
  if (!Number.isInteger(year)) notFound();

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  const scores = await fetchPogScores(year);

  return (
    <main className="mx-auto max-w-3xl p-4">
      <h1 className="mb-1 text-lg font-bold">POG スコア集計</h1>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        順位賞 + 特別賞。重賞を勝つと他の全員から徴収し、勝たれると支払う（合計は必ず 0）。
        対象期間は {year}/6/1 〜 {year + 1}/5/31。
      </p>

      <YearTabs groups={groups} current={year} />

      {scores.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          {year} 年度の指名がありません。
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr className="border-b-2 border-neutral-300 text-xs text-neutral-500 dark:border-neutral-600 dark:text-neutral-400">
                <th className="px-1 py-2 text-left">順位</th>
                <th className="px-1 py-2 text-left">オーナー</th>
                <th className="px-1 py-2 text-right">合計pt</th>
                <th className="px-1 py-2 text-right">順位賞</th>
                <th className="px-1 py-2 text-right">特別賞</th>
                <th className="px-1 py-2 text-right">賞金(万)</th>
                <th className="px-1 py-2 text-left">重賞勝ち</th>
              </tr>
            </thead>
            <tbody>
              {scores.map((s) => {
                const w = s.wins;
                const badges: string[] = [];
                if (w.derby) badges.push(`ダービー${w.derby}`);
                if (w.g1) badges.push(`G1×${w.g1}`);
                if (w.g2) badges.push(`G2×${w.g2}`);
                if (w.g3) badges.push(`G3×${w.g3}`);
                if (w.nar) badges.push(`地方重賞×${w.nar}`);
                if (w.overseas_derby) badges.push(`海外ダービー${w.overseas_derby}`);
                if (w.overseas_other) badges.push(`海外重賞×${w.overseas_other}`);
                if (s.all_raced) badges.push("全馬出走");
                if (s.all_won) badges.push("全馬勝利");
                return (
                  <tr
                    key={s.user_id}
                    className="border-b border-neutral-200 dark:border-neutral-700"
                  >
                    <td className="px-1 py-2 tabular-nums font-bold">{s.rank}</td>
                    <td className="px-1 py-2">{s.name ?? `ユーザー${s.user_id}`}</td>
                    <td className="px-1 py-2 text-right">
                      <Pt value={s.total_points} bold />
                    </td>
                    <td className="px-1 py-2 text-right">
                      <Pt value={s.rank_prize} />
                    </td>
                    <td className="px-1 py-2 text-right">
                      <Pt value={s.special_prize} />
                    </td>
                    <td className="px-1 py-2 text-right tabular-nums text-neutral-500 dark:text-neutral-400">
                      {s.total_prize.toLocaleString("ja-JP")}
                      <span className="ml-0.5 text-[10px]">(#{s.prize_rank})</span>
                    </td>
                    <td className="px-1 py-2 text-xs text-neutral-500 dark:text-neutral-400">
                      {badges.join(" / ") || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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
          href={`/pog/${year}/records`}
          className="text-emerald-600 underline dark:text-emerald-400"
        >
          記録室へ
        </Link>
      </p>
    </main>
  );
}
