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
      <div
        className="flex gap-1 overflow-x-auto pb-2 mb-3 scrollbar-none"
        role="tablist"
        aria-label="開催場選択"
      >
        {courses.map((course) => {
          const hasAny = courseGroups[course].some((r) => r.has_indices);
          return (
            <button
              key={course}
              role="tab"
              aria-selected={active === course}
              onClick={() => setActive(course)}
              className={cn(
                "flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap",
                active === course
                  ? "text-white shadow-sm"
                  : "bg-white border border-gray-200 text-gray-600 hover:border-blue-300"
              )}
              style={
                active === course
                  ? { background: "var(--primary)" }
                  : undefined
              }
            >
              {course}
              {hasAny && (
                <span
                  className={cn(
                    "ml-1 text-[10px]",
                    active === course ? "text-blue-300" : "text-blue-500"
                  )}
                  aria-hidden="true"
                >
                  ✓
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* レース一覧 */}
      <div role="tabpanel" aria-label={`${active}のレース一覧`} className="space-y-1.5">
        {(courseGroups[active] ?? []).map((race) => (
          <RaceCard key={race.id} race={race} />
        ))}
      </div>
    </div>
  );
}
