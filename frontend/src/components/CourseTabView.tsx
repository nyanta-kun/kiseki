"use client";

import { useState } from "react";
import { Race } from "@/lib/api";
import { RaceCard } from "./RaceCard";
import { cn } from "@/lib/utils";

type Props = {
  courseGroups: Record<string, Race[]>;
};

export function CourseTabView({ courseGroups }: Props) {
  const courses = Object.keys(courseGroups);
  const [active, setActive] = useState(courses[0] ?? "");

  if (courses.length === 0) return null;

  return (
    <div>
      {/* 開催場タブ */}
      <div className="flex gap-1 overflow-x-auto pb-2 mb-3 scrollbar-none">
        {courses.map((course) => {
          const hasAny = courseGroups[course].some((r) => r.has_indices);
          return (
            <button
              key={course}
              onClick={() => setActive(course)}
              className={cn(
                "flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap",
                active === course
                  ? "text-white shadow-sm"
                  : "bg-white border border-gray-200 text-gray-600 hover:border-green-300"
              )}
              style={
                active === course
                  ? { background: "var(--green-deep)" }
                  : undefined
              }
            >
              {course}
              {hasAny && (
                <span
                  className={cn(
                    "ml-1 text-[10px]",
                    active === course ? "text-green-300" : "text-green-500"
                  )}
                >
                  ✓
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* レース一覧 */}
      <div className="space-y-1.5">
        {(courseGroups[active] ?? []).map((race) => (
          <RaceCard key={race.id} race={race} />
        ))}
      </div>
    </div>
  );
}
