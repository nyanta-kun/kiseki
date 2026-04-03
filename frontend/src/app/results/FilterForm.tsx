"use client";

import { useRouter, usePathname } from "next/navigation";
import type { PerformanceFilters } from "@/lib/api";

const COURSES = [
  "札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉",
];

const SURFACES = [
  { value: "芝", label: "芝" },
  { value: "ダ", label: "ダート" },
  { value: "障", label: "障害" },
];

const DISTANCE_RANGES = [
  "短距離(〜1400m)",
  "マイル(1401〜1799m)",
  "中距離(1800〜2200m)",
  "長距離(2201m〜)",
];

const CONDITIONS = [
  "G1", "G2", "G3", "OP・L",
  "3勝", "2勝", "1勝", "未勝利", "障害",
];

const DATE_PRESETS = [
  { label: "前年以降", value: "prev_year" },
  { label: "今年", value: "this_year" },
  { label: "過去2年", value: "2years" },
  { label: "全期間", value: "all" },
];

function getPresetDates(preset: string): { from: string; to: string } {
  const today = new Date();
  const yy = today.getFullYear();
  const toDate = `${yy}${String(today.getMonth() + 1).padStart(2, "0")}${String(today.getDate()).padStart(2, "0")}`;
  switch (preset) {
    case "this_year":  return { from: `${yy}0101`, to: toDate };
    case "2years":     return { from: `${yy - 2}0101`, to: toDate };
    case "all":        return { from: "20230101", to: toDate };
    default:           return { from: `${yy - 1}0101`, to: toDate };
  }
}

function currentDatePreset(fromDate?: string): string {
  const today = new Date();
  const yy = today.getFullYear();
  if (!fromDate || fromDate === `${yy - 1}0101`) return "prev_year";
  if (fromDate === `${yy}0101`) return "this_year";
  if (fromDate === `${yy - 2}0101`) return "2years";
  if (fromDate === "20230101") return "all";
  return "prev_year";
}

/** URL クエリ文字列を生成する（配列はカンマ区切り） */
function buildQueryString(filters: PerformanceFilters): string {
  const params = new URLSearchParams();
  if (filters.from_date) params.set("from_date", filters.from_date);
  if (filters.to_date) params.set("to_date", filters.to_date);
  if (filters.course_name?.length) params.set("course_name", filters.course_name.join(","));
  if (filters.surface?.length) params.set("surface", filters.surface.join(","));
  if (filters.distance_range?.length) params.set("distance_range", filters.distance_range.join(","));
  if (filters.condition?.length) params.set("condition", filters.condition.join(","));
  return params.toString();
}

/** 配列から値をトグル（あれば除去、なければ追加） */
function toggle(arr: string[] | undefined, value: string): string[] {
  const cur = arr ?? [];
  return cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
}

type Props = {
  current: PerformanceFilters;
};

export function FilterForm({ current }: Props) {
  const router = useRouter();
  const pathname = usePathname();

  function push(next: PerformanceFilters) {
    const qs = buildQueryString(next);
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  const datePreset = currentDatePreset(current.from_date);

  const activePillCls = "text-xs px-2.5 py-1 rounded-full font-medium transition-colors bg-blue-600 text-white border border-blue-600";
  const pillCls = "text-xs px-2.5 py-1 rounded-full font-medium transition-colors border border-gray-200 text-gray-600 hover:border-blue-300 hover:text-blue-600 cursor-pointer";

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-3">
      {/* 期間 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0">期間</span>
        <div className="flex flex-wrap gap-1.5">
          {DATE_PRESETS.map((p) => {
            const { from, to } = getPresetDates(p.value);
            return (
              <button
                key={p.value}
                onClick={() => push({ ...current, from_date: from, to_date: to })}
                className={datePreset === p.value ? activePillCls : pillCls}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 競馬場 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0">競馬場</span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => push({ ...current, course_name: undefined })}
            className={!current.course_name?.length ? activePillCls : pillCls}
          >
            全て
          </button>
          {COURSES.map((c) => (
            <button
              key={c}
              onClick={() => push({ ...current, course_name: toggle(current.course_name, c) || undefined })}
              className={current.course_name?.includes(c) ? activePillCls : pillCls}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* 馬場 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0">馬場</span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => push({ ...current, surface: undefined })}
            className={!current.surface?.length ? activePillCls : pillCls}
          >
            全て
          </button>
          {SURFACES.map((s) => (
            <button
              key={s.value}
              onClick={() => push({ ...current, surface: toggle(current.surface, s.value) || undefined })}
              className={current.surface?.includes(s.value) ? activePillCls : pillCls}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* 距離 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0">距離</span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => push({ ...current, distance_range: undefined })}
            className={!current.distance_range?.length ? activePillCls : pillCls}
          >
            全て
          </button>
          {DISTANCE_RANGES.map((r) => (
            <button
              key={r}
              onClick={() => push({ ...current, distance_range: toggle(current.distance_range, r) || undefined })}
              className={current.distance_range?.includes(r) ? activePillCls : pillCls}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {/* 条件 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0">条件</span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => push({ ...current, condition: undefined })}
            className={!current.condition?.length ? activePillCls : pillCls}
          >
            全て
          </button>
          {CONDITIONS.map((c) => (
            <button
              key={c}
              onClick={() => push({ ...current, condition: toggle(current.condition, c) || undefined })}
              className={current.condition?.includes(c) ? activePillCls : pillCls}
            >
              {c}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
