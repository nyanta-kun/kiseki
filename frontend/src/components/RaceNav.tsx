"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Race } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  currentRaceId: number;
  races: Race[];
};

export function RaceNav({ currentRaceId, races }: Props) {
  // 競馬場グループ
  const courseGroups: Record<string, Race[]> = {};
  for (const r of races) {
    if (!courseGroups[r.course_name]) courseGroups[r.course_name] = [];
    courseGroups[r.course_name].push(r);
  }
  const COURSE_ORDER = ["東京", "中山", "阪神", "京都", "札幌", "函館", "福島", "新潟", "中京", "小倉"];
  const courses = [
    ...COURSE_ORDER.filter((c) => courseGroups[c]),
    ...Object.keys(courseGroups).filter((c) => !COURSE_ORDER.includes(c)),
  ];

  // 現在のレースの競馬場を初期タブに
  const currentRace = races.find((r) => r.id === currentRaceId);
  const initialCourse = currentRace?.course_name ?? courses[0] ?? "";
  const [activeCourse, setActiveCourse] = useState(initialCourse);

  const router = useRouter();

  return (
    <div className="border-t border-white/10">
      <div className="max-w-3xl mx-auto">
      {/* 競馬場タブ */}
      <div
        className="flex gap-1 overflow-x-auto px-4 pt-2 pb-1 scrollbar-none"
        role="tablist"
        aria-label="競馬場選択"
      >
        {courses.map((course) => (
          <button
            key={course}
            role="tab"
            aria-selected={activeCourse === course}
            onClick={() => setActiveCourse(course)}
            className={cn(
              "flex-shrink-0 text-xs px-2.5 py-1 rounded-full transition-colors whitespace-nowrap",
              activeCourse === course
                ? "bg-white text-blue-900 font-bold"
                : "text-blue-200 hover:text-white hover:bg-white/10"
            )}
          >
            {course}
          </button>
        ))}
      </div>

      {/* レース番号ボタン */}
      <div
        className="flex gap-1 overflow-x-auto px-4 pb-2 scrollbar-none"
        role="tabpanel"
        aria-label={`${activeCourse}のレース一覧`}
      >
        {(courseGroups[activeCourse] ?? []).sort((a, b) => a.race_number - b.race_number).map((race) => {
          const isCurrent = race.id === currentRaceId;
          return (
            <button
              key={race.id}
              aria-pressed={isCurrent}
              aria-label={`${race.race_number}R${race.has_indices ? "（指数あり）" : ""}`}
              onClick={() => router.push(`/races/${race.id}`)}
              className={cn(
                "flex-shrink-0 text-xs px-2 py-1 min-h-[28px] rounded transition-colors whitespace-nowrap",
                isCurrent
                  ? "bg-white text-blue-900 font-bold"
                  : race.has_indices
                  ? "text-blue-100 hover:text-white hover:bg-white/10"
                  : "text-blue-300/50 hover:text-blue-200 hover:bg-white/5"
              )}
            >
              {race.race_number}R
              {race.has_indices && !isCurrent && (
                <span className="ml-0.5 text-[9px] text-blue-300" aria-hidden="true">✓</span>
              )}
            </button>
          );
        })}
      </div>
      </div>
    </div>
  );
}
