"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Race } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  currentRaceId: number;
  races: Race[];
  basePath?: string;
};

export function RaceNav({ currentRaceId, races, basePath = "/races" }: Props) {
  const isChihou = basePath.startsWith("/chihou");
  // 競馬場グループ
  const courseGroups: Record<string, Race[]> = {};
  for (const r of races) {
    if (!courseGroups[r.course_name]) courseGroups[r.course_name] = [];
    courseGroups[r.course_name].push(r);
  }
  const JRA_COURSE_ORDER = ["東京", "中山", "阪神", "京都", "札幌", "函館", "福島", "新潟", "中京", "小倉"];
  // 南関東を先頭、以降は地域順
  const CHIHOU_COURSE_ORDER = [
    "浦和", "船橋", "大井", "川崎",          // 南関東
    "門別",                                   // 北海道
    "盛岡", "水沢",                           // 東北
    "金沢", "笠松", "名古屋",                 // 中部
    "園田", "姫路",                           // 近畿
    "高知",                                   // 四国
    "佐賀",                                   // 九州
  ];
  const COURSE_ORDER = isChihou ? CHIHOU_COURSE_ORDER : JRA_COURSE_ORDER;
  const courses = [
    ...COURSE_ORDER.filter((c) => courseGroups[c]),
    ...Object.keys(courseGroups).filter((c) => !COURSE_ORDER.includes(c)),
  ];

  // 現在のレースの競馬場を初期タブに
  const currentRace = races.find((r) => r.id === currentRaceId);
  const initialCourse = currentRace?.course_name ?? courses[0] ?? "";
  const [activeCourse, setActiveCourse] = useState(initialCourse);

  // 前後レース（発走時刻順）
  const sortedRaces = [...races].sort((a, b) => {
    const pa = a.post_time ?? "9999";
    const pb = b.post_time ?? "9999";
    return pa.localeCompare(pb) || a.id - b.id;
  });
  const currentIdx = sortedRaces.findIndex((r) => r.id === currentRaceId);
  const prevRace = currentIdx > 0 ? sortedRaces[currentIdx - 1] : null;
  const nextRace = currentIdx < sortedRaces.length - 1 ? sortedRaces[currentIdx + 1] : null;

  const router = useRouter();

  return (
    <div className="border-t border-white/10">
      <div className="max-w-3xl mx-auto">
        {/* 前後レースナビ + 競馬場タブ（1行） */}
        <div className="flex items-center gap-1.5 px-3 pt-1.5 pb-0.5">
          {/* 前レース */}
          {prevRace ? (
            <Link
              href={`${basePath}/${prevRace.id}`}
              className={`flex-shrink-0 flex items-center gap-0.5 ${isChihou ? "text-green-100" : "text-blue-200"} hover:text-white text-[11px] transition-colors`}
              aria-label={`前のレース: ${prevRace.course_name} ${prevRace.race_number}R`}
            >
              <span className="text-sm leading-none" aria-hidden="true">‹</span>
              <span className="whitespace-nowrap">{prevRace.course_name}{prevRace.race_number}R</span>
            </Link>
          ) : (
            <span className="flex-shrink-0 w-10" />
          )}

          {/* 競馬場タブ（中央） */}
          <div
            className="flex-1 flex gap-1 overflow-x-auto scrollbar-none justify-center"
            role="tablist"
            aria-label="競馬場選択"
          >
            {courses.map((course) => (
              <button
                key={course}
                id={`tab-racenav-${course}`}
                role="tab"
                aria-selected={activeCourse === course}
                aria-controls={`panel-racenav-${course}`}
                onClick={() => setActiveCourse(course)}
                className={cn(
                  "flex-shrink-0 text-xs px-2.5 py-1 rounded-full transition-colors whitespace-nowrap",
                  activeCourse === course
                    ? `bg-white ${isChihou ? "text-green-900" : "text-blue-900"} font-bold`
                    : `${isChihou ? "text-green-100" : "text-blue-200"} hover:text-white hover:bg-white/10`
                )}
              >
                {course}
              </button>
            ))}
          </div>

          {/* 次レース */}
          {nextRace ? (
            <Link
              href={`${basePath}/${nextRace.id}`}
              className={`flex-shrink-0 flex items-center gap-0.5 ${isChihou ? "text-green-100" : "text-blue-200"} hover:text-white text-[11px] transition-colors`}
              aria-label={`次のレース: ${nextRace.course_name} ${nextRace.race_number}R`}
            >
              <span className="whitespace-nowrap">{nextRace.course_name}{nextRace.race_number}R</span>
              <span className="text-sm leading-none" aria-hidden="true">›</span>
            </Link>
          ) : (
            <span className="flex-shrink-0 w-10" />
          )}
        </div>

        {/* レース番号ボタン */}
        <div
          id={`panel-racenav-${activeCourse}`}
          className="overflow-x-auto scrollbar-none pb-2"
          role="tabpanel"
          aria-labelledby={`tab-racenav-${activeCourse}`}
        >
          <div className="flex gap-1 px-4 w-fit mx-auto">
          {(courseGroups[activeCourse] ?? []).sort((a, b) => a.race_number - b.race_number).map((race) => {
            const isCurrent = race.id === currentRaceId;
            return (
              <button
                key={race.id}
                aria-pressed={isCurrent}
                aria-label={`${race.race_number}R${race.has_indices ? "（指数あり）" : ""}`}
                onClick={() => router.push(`${basePath}/${race.id}`)}
                className={cn(
                  "flex-shrink-0 text-xs px-2 py-1 min-h-[28px] rounded transition-colors whitespace-nowrap",
                  isCurrent
                    ? `bg-white ${isChihou ? "text-green-900" : "text-blue-900"} font-bold`
                    : race.has_indices
                    ? `${isChihou ? "text-green-100" : "text-blue-100"} hover:text-white hover:bg-white/10`
                    : `${isChihou ? "text-green-200/60 hover:text-green-100" : "text-blue-300/50 hover:text-blue-200"} hover:bg-white/5`
                )}
              >
                {race.race_number}R
                {race.has_indices && !isCurrent && (
                  <span className={`ml-0.5 text-[9px] ${isChihou ? "text-green-200" : "text-blue-300"}`} aria-hidden="true">✓</span>
                )}
              </button>
            );
          })}
          </div>
        </div>
      </div>
    </div>
  );
}
