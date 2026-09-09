import type { PogRecentRace } from "@/lib/pog";

/** 着順のバッジ。1〜3着は色を変える（一覧の中で拾えるように）。 */
function ResultBadge({ result }: { result: string | null }) {
  if (!result) {
    return (
      <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
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
    <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${cls}`}>
      {Number.isNaN(n) ? result : `${n}着`}
    </span>
  );
}

/** 「今週の出走」。結果が出た分と、これからの分をまとめて日付順に出す。 */
export function RecentRaces({ races }: { races: PogRecentRace[] }) {
  if (races.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        今週の出走はありません。
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {races.map((r, i) => (
        <li
          key={`${r.date}-${r.course_code}-${r.race_no}-${r.netkeiba_horse_id ?? i}`}
          className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
        >
          <div className="flex items-baseline gap-2">
            <span className="shrink-0 text-xs tabular-nums text-neutral-500 dark:text-neutral-400">
              {r.date.slice(5)} {r.course_name}
              {r.race_no}R
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">
              {r.horse_name ?? "（未命名）"}
            </span>
            <ResultBadge result={r.result} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-neutral-500 dark:text-neutral-400">
            {r.owners && r.owners.length > 0 && (
              <span className="font-medium text-neutral-700 dark:text-neutral-200">
                {r.owners.join(" / ")}
              </span>
            )}
            {r.jockey && <span>{r.jockey}</span>}
            {r.ninki != null && <span className="tabular-nums">{r.ninki}人気</span>}
            {r.tan != null && <span className="tabular-nums">単{r.tan}</span>}
            {r.prize > 0 && (
              <span className="tabular-nums">{r.prize.toLocaleString("ja-JP")}万</span>
            )}
            {r.horse_no == null && r.result == null && <span>（特別登録）</span>}
          </div>
        </li>
      ))}
    </ul>
  );
}
