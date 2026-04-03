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

// URL パラメータは ASCII キー（〜等の特殊文字を避ける）、ラベルは表示用
// SURFACES と同じ { value, label } 構造を使用
const DISTANCE_OPTIONS = [
  { value: "sprint", label: "短距離(〜1400m)" },
  { value: "mile",   label: "マイル(1401〜1799m)" },
  { value: "middle", label: "中距離(1800〜2200m)" },
  { value: "long",   label: "長距離(2201m〜)" },
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

/**
 * RSC シリアライズで string[] が string になるケースを防ぐ正規化関数。
 * string / string[] / undefined のいずれでも string[] を返す。
 */
function toArr(v: string[] | string | undefined): string[] {
  if (!v) return [];
  if (Array.isArray(v)) return v;
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}

/** URL クエリ文字列を生成する（配列はカンマ区切り） */
function buildQueryString(f: {
  from_date?: string;
  to_date?: string;
  course_name: string[];
  surface: string[];
  distance_range: string[];
  condition: string[];
}): string {
  const params = new URLSearchParams();
  if (f.from_date) params.set("from_date", f.from_date);
  if (f.to_date) params.set("to_date", f.to_date);
  if (f.course_name.length) params.set("course_name", f.course_name.join(","));
  if (f.surface.length) params.set("surface", f.surface.join(","));
  if (f.distance_range.length) params.set("distance_range", f.distance_range.join(","));
  if (f.condition.length) params.set("condition", f.condition.join(","));
  return params.toString();
}

/** 配列から値をトグル（あれば除去、なければ追加） */
function toggle(arr: string[], value: string): string[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
}

type Props = {
  current: PerformanceFilters;
};

export function FilterForm({ current }: Props) {
  const router = useRouter();
  const pathname = usePathname();

  // RSC シリアライズで string になる場合があるため常に string[] に正規化
  const norm = {
    from_date:      current.from_date,
    to_date:        current.to_date,
    course_name:    toArr(current.course_name as string[] | string | undefined),
    surface:        toArr(current.surface as string[] | string | undefined),
    distance_range: toArr(current.distance_range as string[] | string | undefined),
    condition:      toArr(current.condition as string[] | string | undefined),
  };

  function push(next: typeof norm) {
    const qs = buildQueryString(next);
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  const datePreset = currentDatePreset(norm.from_date);

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
                onClick={() => push({ ...norm, from_date: from, to_date: to })}
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
            onClick={() => push({ ...norm, course_name: [] })}
            className={norm.course_name.length === 0 ? activePillCls : pillCls}
          >
            全て
          </button>
          {COURSES.map((c) => (
            <button
              key={c}
              onClick={() => push({ ...norm, course_name: toggle(norm.course_name, c) })}
              className={norm.course_name.includes(c) ? activePillCls : pillCls}
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
            onClick={() => push({ ...norm, surface: [] })}
            className={norm.surface.length === 0 ? activePillCls : pillCls}
          >
            全て
          </button>
          {SURFACES.map((s) => (
            <button
              key={s.value}
              onClick={() => push({ ...norm, surface: toggle(norm.surface, s.value) })}
              className={norm.surface.includes(s.value) ? activePillCls : pillCls}
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
            onClick={() => push({ ...norm, distance_range: [] })}
            className={norm.distance_range.length === 0 ? activePillCls : pillCls}
          >
            全て
          </button>
          {DISTANCE_OPTIONS.map((d) => (
            <button
              key={d.value}
              onClick={() => push({ ...norm, distance_range: toggle(norm.distance_range, d.value) })}
              className={norm.distance_range.includes(d.value) ? activePillCls : pillCls}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {/* 条件 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0">条件</span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => push({ ...norm, condition: [] })}
            className={norm.condition.length === 0 ? activePillCls : pillCls}
          >
            全て
          </button>
          {CONDITIONS.map((c) => (
            <button
              key={c}
              onClick={() => push({ ...norm, condition: toggle(norm.condition, c) })}
              className={norm.condition.includes(c) ? activePillCls : pillCls}
            >
              {c}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
