import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { fetchPogGroups, fetchPogScores, type PogScore } from "@/lib/pog";
import { PogShell } from "../../PogShell";
import { Card, CardHeader, Collapsible, EmptyState, RankBadge, StatTile } from "../../ui";

export const metadata: Metadata = {
  title: "POG スコア集計 | GallopLab",
};

/**
 * 符号付きのポイント。プラスは赤、マイナスは青（競馬の慣習に合わせる）。
 *
 * ⚠️ 色だけで正負を伝えない。プラスには必ず `+` を付ける。
 */
function Pt({ value, bold = false }: { value: number; bold?: boolean }) {
  const cls =
    value > 0
      ? "text-rose-600 dark:text-rose-400"
      : value < 0
        ? "text-sky-600 dark:text-sky-400"
        : "text-surface-muted";
  return (
    <span className={`tabular-nums ${cls} ${bold ? "font-bold" : ""}`}>
      {value > 0 ? "+" : ""}
      {value.toLocaleString("ja-JP")}
    </span>
  );
}

/** 重賞勝ち・達成条件のバッジ。 */
function badgesOf(s: PogScore): string[] {
  const w = s.wins;
  const out: string[] = [];
  if (w.derby) out.push(`ダービー${w.derby}`);
  if (w.g1) out.push(`G1×${w.g1}`);
  if (w.g2) out.push(`G2×${w.g2}`);
  if (w.g3) out.push(`G3×${w.g3}`);
  if (w.nar) out.push(`地方重賞×${w.nar}`);
  if (w.overseas_derby) out.push(`海外ダービー${w.overseas_derby}`);
  if (w.overseas_other) out.push(`海外重賞×${w.overseas_other}`);
  if (s.all_raced) out.push("全馬出走");
  if (s.all_won) out.push("全馬勝利");
  return out;
}

function Badges({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="text-surface-muted opacity-50">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((b) => (
        <span
          key={b}
          className="rounded px-1.5 py-0.5 text-[10px] font-medium"
          style={{ background: "var(--pog-accent-soft)", color: "var(--pog-accent)" }}
        >
          {b}
        </span>
      ))}
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

  // 合計は必ず 0 になる（徴収と支払いが釣り合う）。ずれていたら計算が壊れている
  // 合図なので、隠さず出す。
  const sumPoints = scores.reduce((a, s) => a + s.total_points, 0);
  const topGain = scores.reduce(
    (best, s) => (best === null || s.total_points > best.total_points ? s : best),
    null as PogScore | null,
  );

  return (
    <PogShell
      title="POG スコア集計"
      description={`順位賞＋特別賞・合計は必ず 0／対象 ${year}/6〜${year + 1}/5`}
      year={year}
      groups={groups}
      yearBasePath="/pog/:year/score"
    >
      {scores.length === 0 ? (
        <EmptyState>{year} 年度の指名がありません。</EmptyState>
      ) : (
        <>
          {/* スマホはタイル 4 枚（約 130px）をやめて 1 行の帯（約 30px）にする。
              精算表そのものを 1 画面に収めるため（`app/pog/[year]/page.tsx` と同じ方針）。 */}
          <div
            className="mb-2 flex items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-[11px] text-surface-muted md:hidden"
            style={{ background: "var(--pog-card)", border: "1px solid var(--pog-card-border)" }}
          >
            <span className="tabular-nums">{scores.length}人</span>
            <span className="truncate">
              首位 <span className="font-semibold text-surface-heading">{topGain?.name ?? "—"}</span>
            </span>
            <span className={sumPoints === 0 ? "" : "font-bold text-rose-600"}>
              {sumPoints === 0 ? "収支は釣り合い" : `⚠️ 合計 ${sumPoints}`}
            </span>
          </div>
          <div className="mb-4 hidden gap-2 md:grid md:grid-cols-4">
            <StatTile label="参加オーナー" value={`${scores.length} 人`} />
            <StatTile
              label="首位"
              value={topGain?.name ?? "—"}
              sub={topGain ? <Pt value={topGain.total_points} /> : undefined}
            />
            <StatTile
              label="ポイント合計"
              value={sumPoints}
              sub={sumPoints === 0 ? "釣り合っている" : "⚠️ 0 になっていない"}
            />
            <StatTile
              label="賞金 合計"
              value={`${scores.reduce((a, s) => a + s.total_prize, 0).toLocaleString("ja-JP")} 万`}
            />
          </div>

          <Card>
            <div className="hidden md:block">
              <CardHeader title="精算表" meta={`${scores.length} 人`} />
            </div>

            {/* PC: 表。列の対応が見えるほうが精算の確認が速い。 */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead>
                  <tr
                    className="border-b text-xs text-surface-muted"
                    style={{ borderColor: "var(--pog-card-border)" }}
                  >
                    <th className="px-3 py-2 text-left font-medium">順位</th>
                    <th className="px-3 py-2 text-left font-medium">オーナー</th>
                    <th className="px-3 py-2 text-right font-medium">合計pt</th>
                    <th className="px-3 py-2 text-right font-medium">順位賞</th>
                    <th className="px-3 py-2 text-right font-medium">特別賞</th>
                    <th className="px-3 py-2 text-right font-medium">賞金(万)</th>
                    <th className="px-3 py-2 text-left font-medium">重賞勝ち・達成</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((s) => (
                    <tr
                      key={s.user_id}
                      className="border-b last:border-b-0 transition-colors hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <td className="px-3 py-2">
                        <RankBadge rank={s.rank} />
                      </td>
                      <td className="px-3 py-2 font-medium text-surface-heading">
                        {s.name ?? `ユーザー${s.user_id}`}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Pt value={s.total_points} bold />
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Pt value={s.rank_prize} />
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Pt value={s.special_prize} />
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-surface-muted">
                        {s.total_prize.toLocaleString("ja-JP")}
                        <span className="ml-0.5 text-[10px]">(#{s.prize_rank})</span>
                      </td>
                      <td className="px-3 py-2">
                        <Badges items={badgesOf(s)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="text-xs text-surface-muted">
                    <td className="px-3 py-2" colSpan={2}>
                      合計
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Pt value={sumPoints} bold />
                    </td>
                    <td colSpan={4} className="px-3 py-2">
                      {sumPoints === 0
                        ? "徴収と支払いが釣り合っている"
                        : "⚠️ 0 になっていない（計算を確認すること）"}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>

            {/* スマホ: **5 列の細い表**。
                🔴 1 人 1 枚のカード（見出し + 3 列の内訳 + バッジ）にすると 1 人 90px で、
                   9 人＝810px ＝ 画面に入らない。精算で要るのは
                   「誰が・順位賞いくら・特別賞いくら・合計いくら」の 4 数字なので、
                   賞金と重賞バッジは表から外して下の折りたたみへ回す。
                ⚠️ 文字は 13px。ここを 11px に落とすと数字の読み取りが辛くなる。 */}
            <div className="md:hidden">
              <table className="w-full text-[13px]">
                <thead>
                  <tr
                    className="border-b text-[10px] text-surface-muted"
                    style={{ borderColor: "var(--pog-card-border)" }}
                  >
                    <th className="px-1.5 py-1 text-left font-medium">順位</th>
                    <th className="px-1 py-1 text-left font-medium">オーナー</th>
                    <th className="px-1 py-1 text-right font-medium">順位賞</th>
                    <th className="px-1 py-1 text-right font-medium">特別賞</th>
                    <th className="px-1.5 py-1 text-right font-medium">合計</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((s) => (
                    <tr
                      key={s.user_id}
                      className="border-b last:border-b-0"
                      style={{ borderColor: "var(--pog-card-border)" }}
                    >
                      <td className="px-1.5 py-1.5">
                        <RankBadge rank={s.rank} />
                      </td>
                      {/* `max-w-0` + `truncate` で、名前が長くても数字の列を押さない。 */}
                      <td className="max-w-0 truncate px-1 py-1.5 font-medium text-surface-heading">
                        {s.name ?? `ユーザー${s.user_id}`}
                      </td>
                      <td className="px-1 py-1.5 text-right">
                        <Pt value={s.rank_prize} />
                      </td>
                      <td className="px-1 py-1.5 text-right">
                        <Pt value={s.special_prize} />
                      </td>
                      <td className="px-1.5 py-1.5 text-right">
                        <Pt value={s.total_points} bold />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* 表から外した「賞金」と「重賞勝ち・達成」はここに畳んでおく。
              精算のときだけ開けばよく、常時 9 人ぶん並べる価値は無い。 */}
          <div className="mt-2 md:hidden">
            <Collapsible title="賞金と重賞勝ちの内訳" meta={`${scores.length} 人`}>
              <ul>
                {scores.map((s) => (
                  <li
                    key={s.user_id}
                    className="border-b px-3 py-2 last:border-b-0"
                    style={{ borderColor: "var(--pog-card-border)" }}
                  >
                    <div className="flex items-baseline gap-2 text-[13px]">
                      <span className="min-w-0 flex-1 truncate font-medium text-surface-heading">
                        {s.name ?? `ユーザー${s.user_id}`}
                      </span>
                      <span className="shrink-0 tabular-nums text-surface-muted">
                        {s.total_prize.toLocaleString("ja-JP")} 万
                        <span className="ml-0.5 text-[10px]">(#{s.prize_rank})</span>
                      </span>
                    </div>
                    <div className="mt-1">
                      <Badges items={badgesOf(s)} />
                    </div>
                  </li>
                ))}
              </ul>
            </Collapsible>
          </div>
        </>
      )}
    </PogShell>
  );
}
