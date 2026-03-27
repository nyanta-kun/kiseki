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
  const courses = Object.keys(courseGroups);

  // 現在のレースの競馬場を初期タブに
  const currentRace = races.find((r) => r.id === currentRaceId);
  const initialCourse = currentRace?.course_name ?? courses[0] ?? "";
  const [activeCourse, setActiveCourse] = useState(initialCourse);

  const router = useRouter();

  return (
    <div className="border-t border-white/10">
      {/* 競馬場タブ */}
      <div className="flex gap-1 overflow-x-auto px-4 pt-2 pb-1 scrollbar-none">
        {courses.map((course) => (
          <button
            key={course}
            onClick={() => setActiveCourse(course)}
            className={cn(
              "flex-shrink-0 text-xs px-2.5 py-1 rounded-full transition-colors whitespace-nowrap",
              activeCourse === course
                ? "bg-white text-green-800 font-bold"
                : "text-green-200 hover:text-white hover:bg-white/10"
            )}
          >
            {course}
          </button>
        ))}
      </div>

      {/* レース番号ボタン */}
      <div className="flex gap-1 overflow-x-auto px-4 pb-2 scrollbar-none">
        {(courseGroups[activeCourse] ?? []).map((race) => {
          const isCurrent = race.id === currentRaceId;
          return (
            <button
              key={race.id}
              onClick={() => router.push(`/races/${race.id}`)}
              className={cn(
                "flex-shrink-0 text-xs px-2 py-0.5 rounded transition-colors whitespace-nowrap",
                isCurrent
                  ? "bg-white text-green-800 font-bold"
                  : race.has_indices
                  ? "text-green-100 hover:text-white hover:bg-white/10"
                  : "text-green-300/50 hover:text-green-200 hover:bg-white/5"
              )}
            >
              {race.race_number}R
              {race.has_indices && !isCurrent && (
                <span className="ml-0.5 text-[9px] text-green-300">✓</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
