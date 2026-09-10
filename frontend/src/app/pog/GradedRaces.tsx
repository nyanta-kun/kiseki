import Link from "next/link";

import type { GradedRace } from "@/lib/pog";
import { GradeBadge } from "./ui";

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

/**
 * 今週の重賞。移設元 sekito の POG 詳細ページにあったパネルの移植。
 *
 * 中央と地方をまとめて日付順に出す。レース名から詳細ページへ飛ぶ
 * （中央は `/races/{id}`・地方は `/chihou/races/{id}` と行き先が分かれるので
 * `kind` を見て切り替える）。
 *
 * 🔴 POG が ON なら中央・地方も必ず ON（`lib/menuAccess.ts` の規則）なので、
 * ここのリンク先は必ず開ける。規則を外すとこの列のリンクが全部弾かれる。
 */
export function GradedRaces({ races }: { races: GradedRace[] }) {
  if (races.length === 0) {
    return (
      <p className="px-2 py-6 text-center text-sm text-surface-muted">
        今週の重賞はありません。
      </p>
    );
  }
  return (
    <ul className="divide-y" style={{ borderColor: "var(--pog-card-border)" }}>
      {races.map((r) => (
        <li
          key={`${r.kind}-${r.race_id}`}
          className="px-2 py-2 first:pt-1 last:pb-1"
          style={{ borderColor: "var(--pog-card-border)" }}
        >
          <div className="flex items-baseline gap-1.5">
            <span className="shrink-0 text-[11px] tabular-nums text-surface-muted">
              {formatDate(r.date)}
            </span>
            <GradeBadge grade={r.grade} />
            <Link
              href={
                r.kind === "chihou"
                  ? `/chihou/races/${r.race_id}`
                  : `/races/${r.race_id}`
              }
              className="min-w-0 flex-1 truncate font-medium underline-offset-2 hover:underline"
              style={{ color: "var(--pog-accent)" }}
            >
              {r.race_name ?? `${r.race_number}R`}
            </Link>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-surface-muted">
            <span>
              {r.course_name}
              {r.race_number}R
            </span>
            <span className="tabular-nums">{formatTime(r.post_time)}</span>
            {r.winner_name && (
              <span className="font-medium text-surface-heading">🥇 {r.winner_name}</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
