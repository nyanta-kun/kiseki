import Link from "next/link";

import type { GradedRace } from "@/lib/pog";

/** `YYYYMMDD` → `M/D(曜)`。 */
function formatDate(ymd: string): string {
  const y = Number(ymd.slice(0, 4));
  const m = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const w = "日月火水木金土"[new Date(y, m - 1, d).getDay()];
  return `${m}/${d}(${w})`;
}

/** `HHMM` → `HH:MM`。未確定なら `—`。 */
function formatTime(hhmm: string | null): string {
  if (!hhmm || hhmm.length < 4) return "—";
  return `${hhmm.slice(0, 2)}:${hhmm.slice(2, 4)}`;
}

/** 格の色分け。G1 だけ目立たせ、他は落ち着かせる。 */
function GradeBadge({ grade }: { grade: string | null }) {
  if (!grade) return null;
  const cls = grade.endsWith("G1")
    ? "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200"
    : grade.endsWith("G2")
      ? "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200"
      : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300";
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${cls}`}>
      {grade}
    </span>
  );
}

/**
 * 今週の重賞。移設元 sekito の POG 詳細ページにあったパネルの移植。
 *
 * 中央と地方をまとめて日付順に出す。レース名から詳細ページへ飛ぶ
 * （中央は `/races/{id}`・地方は `/chihou/races/{id}` と行き先が分かれるので
 * `kind` を見て切り替える）。
 */
export function GradedRaces({ races }: { races: GradedRace[] }) {
  if (races.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        今週の重賞はありません。
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {races.map((r) => (
        <li
          key={`${r.kind}-${r.race_id}`}
          className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
        >
          <div className="flex items-baseline gap-2">
            <span className="shrink-0 text-xs tabular-nums text-neutral-500 dark:text-neutral-400">
              {formatDate(r.date)}
            </span>
            <GradeBadge grade={r.grade} />
            <Link
              href={
                r.kind === "chihou"
                  ? `/chihou/races/${r.race_id}`
                  : `/races/${r.race_id}`
              }
              className="min-w-0 flex-1 truncate font-medium text-emerald-700 underline decoration-emerald-300 underline-offset-2 dark:text-emerald-400"
            >
              {r.race_name ?? `${r.race_number}R`}
            </Link>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-neutral-500 dark:text-neutral-400">
            <span>
              {r.course_name}
              {r.race_number}R
            </span>
            <span className="tabular-nums">{formatTime(r.post_time)}</span>
            {r.winner_name && (
              <span className="font-medium text-neutral-700 dark:text-neutral-200">
                🥇 {r.winner_name}
              </span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
