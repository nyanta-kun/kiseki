import Link from "next/link";

import type { PogRecentRace } from "@/lib/pog";

/**
 * 「今週の出走」。結果が出た分と、これからの分をまとめて日付順に出す。
 *
 * PC では順位表の右の縦列（幅 20rem）に入る。**カードを入れ子にしない**
 * 平らな行にしてあるのはそのため（枠の中に枠があると幅が食われて名前が
 * 読めなくなる）。
 */

/** 着順のバッジ。1〜3着は色を変える（一覧の中で拾えるように）。 */
function ResultBadge({ result }: { result: string | null }) {
  if (!result) {
    return (
      <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
        予定
      </span>
    );
  }
  const n = Number(result);
  const cls =
    n === 1
      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
      : n === 2 || n === 3
        ? "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200"
        : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300";
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>
      {Number.isNaN(n) ? result : `${n}着`}
    </span>
  );
}

export function RecentRaces({ races }: { races: PogRecentRace[] }) {
  if (races.length === 0) {
    return (
      <p className="px-2 py-6 text-center text-sm text-surface-muted">
        今週の出走はありません。
      </p>
    );
  }
  return (
    <ul className="divide-y" style={{ borderColor: "var(--pog-card-border)" }}>
      {races.map((r, i) => (
        <li
          key={`${r.date}-${r.course_code}-${r.race_no}-${r.netkeiba_horse_id ?? i}`}
          className="px-2 py-2 first:pt-1 last:pb-1"
          style={{ borderColor: "var(--pog-card-border)" }}
        >
          <div className="flex items-baseline gap-1.5">
            <span className="shrink-0 text-[11px] tabular-nums text-surface-muted">
              {r.date.slice(5)} {r.course_name}
              {r.race_no}R
            </span>
            <ResultBadge result={r.result} />
          </div>
          <div className="mt-0.5 flex items-baseline gap-1.5">
            {r.netkeiba_horse_id ? (
              // 馬名から netkeiba の馬ページへ。移設元も同じ導線だった。
              <Link
                href={`https://db.netkeiba.com/horse/${r.netkeiba_horse_id}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="min-w-0 flex-1 truncate font-medium text-surface-heading underline-offset-2 hover:underline"
              >
                {r.horse_name ?? "（未命名）"}
              </Link>
            ) : (
              <span className="min-w-0 flex-1 truncate font-medium text-surface-heading">
                {r.horse_name ?? "（未命名）"}
              </span>
            )}
            {r.prize > 0 && (
              <span
                className="shrink-0 text-xs font-semibold tabular-nums"
                style={{ color: "var(--pog-accent)" }}
              >
                {r.prize.toLocaleString("ja-JP")}万
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-surface-muted">
            {r.owners && r.owners.length > 0 && (
              <span className="font-medium">{r.owners.join(" / ")}</span>
            )}
            {r.jockey && <span>{r.jockey}</span>}
            {r.ninki != null && <span className="tabular-nums">{r.ninki}人気</span>}
            {r.tan != null && <span className="tabular-nums">単{r.tan}</span>}
            {r.horse_no == null && r.result == null && <span>（特別登録）</span>}
          </div>
        </li>
      ))}
    </ul>
  );
}
