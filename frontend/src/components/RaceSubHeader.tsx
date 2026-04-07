import Link from "next/link";
import type { fetchRace, Race } from "@/lib/api";
import { surfaceIcon, gradeClass, raceClassBadgeClass, raceClassShort, formatDate } from "@/lib/utils";
import { RaceNav } from "@/components/RaceNav";

function formatPostTime(postTime: string | null): string {
  if (!postTime || postTime.length < 4) return "";
  return `${postTime.slice(0, 2)}:${postTime.slice(2, 4)}`;
}

export function RaceSubHeader({
  raceId,
  race,
  date,
  allRaces,
  basePath = "/races",
}: {
  raceId: number;
  race: Awaited<ReturnType<typeof fetchRace>> | null;
  date: string;
  allRaces: Race[];
  basePath?: string;
}) {
  const isChihou = basePath.startsWith("/chihou");
  const subheaderBg = isChihou ? "var(--chihou-primary-mid)" : "var(--primary-mid)";
  const linkColorClass = isChihou ? "text-green-100 hover:text-white" : "text-blue-200 hover:text-white";
  const metaColorClass = isChihou ? "text-green-100" : "text-blue-200";

  return (
    <div style={{ background: subheaderBg, flexShrink: 0 }} className="shadow-sm">
      {/* レース情報 */}
      <div className="max-w-3xl mx-auto px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Link
            href={`${basePath}?date=${date}${race?.course_name ? `&course=${encodeURIComponent(race.course_name)}` : ""}`}
            className={`${linkColorClass} text-lg leading-none flex-shrink-0`}
            aria-label="レース一覧に戻る"
          >
            ←
          </Link>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-white font-bold text-base leading-tight">
                {race
                  ? `${race.course_name} ${race.race_number}R ${race.race_name ?? race.race_class_label ?? ""}`
                  : `Race #${raceId}`}
              </h1>
              {race?.grade && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 ${gradeClass(race.grade)}`}>
                  {race.grade}
                </span>
              )}
              {race && !race.grade && raceClassShort(race.race_class_label) && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 ${raceClassBadgeClass(race.race_class_label)}`}>
                  {raceClassShort(race.race_class_label)}
                </span>
              )}
            </div>
            {race && (
              <p className={`${metaColorClass} text-[11px] mt-0.5`}>
                {formatDate(date)}
                {race.post_time && (
                  <span className="ml-1.5 font-medium text-white/90">{formatPostTime(race.post_time)} 発走</span>
                )}
                {" · "}{surfaceIcon(race.surface)} {race.surface} {race.distance}m
                {race.condition ? ` · ${race.condition}` : ""}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* 前後ナビ + 競馬場タブ + レース番号 */}
      {allRaces.length > 0 && (
        <RaceNav currentRaceId={raceId} races={allRaces} basePath={basePath} />
      )}
    </div>
  );
}
