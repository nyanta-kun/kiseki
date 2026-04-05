"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { Race } from "@/lib/api";
import { RaceCard } from "./RaceCard";
import { cn } from "@/lib/utils";

const RECOMMEND_TAB = "__recommend__";

type Props = {
  courseGroups: Record<string, Race[]>;
  recommendPanel: ReactNode;
};

export function CourseTabView({ courseGroups, recommendPanel }: Props) {
  const courses = Object.keys(courseGroups);
  const [active, setActive] = useState(RECOMMEND_TAB);

  if (courses.length === 0) return null;

  return (
    <div>
      {/* タブ: 推奨（左端） + 競馬場 */}
      <div
        className="flex gap-1 overflow-x-auto pb-2 mb-3 scrollbar-none"
        role="tablist"
        aria-label="開催場・推奨選択"
      >
        {/* 推奨タブ */}
        <button
          id="tab-course-recommend"
          role="tab"
          aria-selected={active === RECOMMEND_TAB}
          aria-controls="panel-course-recommend"
          onClick={() => setActive(RECOMMEND_TAB)}
          className={cn(
            "flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap",
            active === RECOMMEND_TAB
              ? "text-white shadow-sm"
              : "bg-white border border-gray-200 text-gray-600 hover:border-amber-300"
          )}
          style={active === RECOMMEND_TAB ? { background: "var(--primary)" } : undefined}
        >
          ★推奨
        </button>

        {/* 競馬場タブ */}
        {courses.map((course) => {
          const hasAny = courseGroups[course].some((r) => r.has_indices);
          return (
            <button
              key={course}
              id={`tab-course-${course}`}
              role="tab"
              aria-selected={active === course}
              aria-controls={`panel-course-${course}`}
              onClick={() => setActive(course)}
              className={cn(
                "flex-shrink-0 px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap",
                active === course
                  ? "text-white shadow-sm"
                  : "bg-white border border-gray-200 text-gray-600 hover:border-blue-300"
              )}
              style={active === course ? { background: "var(--primary)" } : undefined}
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

      {/* 推奨パネル（Server Componentをスロットで受け取る） */}
      <div
        id="panel-course-recommend"
        role="tabpanel"
        aria-labelledby="tab-course-recommend"
        hidden={active !== RECOMMEND_TAB}
      >
        {recommendPanel}
      </div>

      {/* 競馬場パネル */}
      {active !== RECOMMEND_TAB && (
        <div
          id={`panel-course-${active}`}
          role="tabpanel"
          aria-labelledby={`tab-course-${active}`}
          className="space-y-1.5"
        >
          {(courseGroups[active] ?? []).map((race) => (
            <RaceCard key={race.id} race={race} />
          ))}
        </div>
      )}
    </div>
  );
}
