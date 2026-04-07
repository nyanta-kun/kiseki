"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { Race } from "@/lib/api";
import { RaceCard } from "./RaceCard";
import { cn } from "@/lib/utils";

const RECOMMEND_TAB = "__recommend__";

type Props = {
  courseGroups: Record<string, Race[]>;
  recommendPanel: ReactNode;
  basePath?: string;
  hideRecommend?: boolean;
};

const JRA_COURSE_ORDER = ["東京", "中山", "阪神", "京都", "札幌", "函館", "福島", "新潟", "中京", "小倉"];
const CHIHOU_COURSE_ORDER = [
  "浦和", "船橋", "大井", "川崎",
  "門別",
  "盛岡", "水沢",
  "金沢", "笠松", "名古屋",
  "園田", "姫路",
  "高知",
  "佐賀",
];

function sortedCourses(courseGroups: Record<string, unknown[]>, isChihou: boolean): string[] {
  const order = isChihou ? CHIHOU_COURSE_ORDER : JRA_COURSE_ORDER;
  return [
    ...order.filter((c) => courseGroups[c]),
    ...Object.keys(courseGroups).filter((c) => !order.includes(c)),
  ];
}

export function CourseTabView({ courseGroups, recommendPanel, basePath = "/races", hideRecommend = false }: Props) {
  const isChihou = basePath.startsWith("/chihou");
  const courses = sortedCourses(courseGroups, isChihou);
  const searchParams = useSearchParams();
  const courseParam = searchParams.get("course");
  const initialTab = courseParam && courses.includes(courseParam)
    ? courseParam
    : hideRecommend ? (courses[0] ?? RECOMMEND_TAB) : RECOMMEND_TAB;
  const [active, setActive] = useState(initialTab);

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
        {!hideRecommend && (
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
        )}

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
              style={active === course ? { background: basePath.startsWith("/chihou") ? "var(--chihou-primary)" : "var(--primary)" } : undefined}
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
            <RaceCard key={race.id} race={race} basePath={basePath} />
          ))}
        </div>
      )}
    </div>
  );
}
