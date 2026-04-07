"use client";

import { useState, useTransition } from "react";
import { useRouter, usePathname } from "next/navigation";
import type { ChihouPerformanceFilters } from "@/lib/api";

const CHIHOU_COURSES = [
  "盛岡", "水沢", "浦和", "船橋", "大井", "川崎", "金沢", "笠松", "名古屋", "園田", "高知", "佐賀",
];

const SURFACES = [
  { value: "芝", label: "芝" },
  { value: "ダ", label: "ダート" },
];

const DATE_PRESETS = [
  { label: "全期間",   value: "all" },
  { label: "今年",     value: "this_year" },
  { label: "今月",     value: "this_month" },
  { label: "今日",     value: "today" },
  { label: "カスタム", value: "custom" },
];

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
}

function thisMonthStartStr(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}01`;
}

function getPresetDates(preset: string): { from: string; to: string } | null {
  const yy = new Date().getFullYear();
  const to = todayStr();
  switch (preset) {
    case "all":        return { from: "20240101", to };
    case "this_year":  return { from: `${yy}0101`, to };
    case "this_month": return { from: thisMonthStartStr(), to };
    case "today":      return { from: to, to };
    default:           return null;
  }
}

function currentDatePreset(from?: string, to?: string): string {
  const today = todayStr();
  const yy = new Date().getFullYear();
  if (to && to !== today) return "custom";
  if (!from) return "this_month";
  if (from === "20240101")          return "all";
  if (from === `${yy}0101`)         return "this_year";
  if (from === thisMonthStartStr()) return "this_month";
  if (from === today)               return "today";
  return "custom";
}

function toInputDate(d?: string): string {
  if (!d || d.length !== 8) return "";
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
}

function fromInputDate(d: string): string {
  return d.replace(/-/g, "");
}

function toArr(v: string[] | string | undefined): string[] {
  if (!v) return [];
  if (Array.isArray(v)) return v;
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}

function toggle(arr: string[], value: string): string[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
}

function buildQueryString(f: {
  from_date?: string;
  to_date?: string;
  course_name: string[];
  surface: string[];
}): string {
  const params = new URLSearchParams();
  if (f.from_date) params.set("from_date", f.from_date);
  if (f.to_date) params.set("to_date", f.to_date);
  if (f.course_name.length) params.set("course_name", f.course_name.join(","));
  if (f.surface.length) params.set("surface", f.surface.join(","));
  return params.toString();
}

type FilterState = {
  from_date?: string;
  to_date?: string;
  course_name: string[];
  surface: string[];
};

type Props = { current: ChihouPerformanceFilters };

export function ChihouFilterForm({ current }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [isPending, startTransition] = useTransition();

  const initial: FilterState = {
    from_date:   current.from_date,
    to_date:     current.to_date,
    course_name: toArr(current.course_name as string[] | string | undefined),
    surface:     toArr(current.surface as string[] | string | undefined),
  };

  const [draft, setDraft] = useState<FilterState>(initial);

  function apply() {
    const qs = buildQueryString(draft);
    startTransition(() => {
      router.push(qs ? `${pathname}?${qs}` : pathname);
    });
  }

  const datePreset = currentDatePreset(draft.from_date, draft.to_date);

  const activePillCls = "text-xs px-2.5 py-1 rounded-full font-medium transition-colors bg-green-600 text-white border border-green-600";
  const pillCls       = "text-xs px-2.5 py-1 rounded-full font-medium transition-colors border border-gray-200 text-gray-600 hover:border-green-300 hover:text-green-600 cursor-pointer";
  const dateCls       = "text-xs border border-gray-200 rounded-md px-2 py-1 text-gray-600 focus:outline-none focus:border-green-400 focus:ring-1 focus:ring-green-200";

  return (
    <>
      {isPending && (
        <>
          <div className="fixed top-0 left-0 right-0 z-50 h-1 bg-green-100 overflow-hidden">
            <div
              className="h-full w-1/3 bg-green-500"
              style={{ animation: "chihou-progress 1.4s ease-in-out infinite" }}
            />
          </div>
          <div className="fixed inset-0 z-40 bg-white/70 flex items-center justify-center">
            <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-12 flex flex-col items-center gap-6">
              <div className="w-20 h-20 border-4 border-green-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-base font-semibold text-gray-700">集計中...</p>
            </div>
          </div>
          <style>{`
            @keyframes chihou-progress {
              0%   { transform: translateX(-100%); }
              100% { transform: translateX(400%); }
            }
          `}</style>
        </>
      )}

      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-3">
        {/* 期間プリセット */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-500 w-10 shrink-0">期間</span>
          <div className="flex flex-wrap gap-1.5">
            {DATE_PRESETS.map((p) => {
              const dates = getPresetDates(p.value);
              return (
                <button
                  key={p.value}
                  onClick={() => {
                    if (dates) setDraft({ ...draft, from_date: dates.from, to_date: dates.to });
                  }}
                  className={datePreset === p.value ? activePillCls : pillCls}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* 日付入力 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-10 shrink-0" aria-hidden />
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={toInputDate(draft.from_date)}
              onChange={(e) => setDraft({ ...draft, from_date: fromInputDate(e.target.value) })}
              className={dateCls}
              aria-label="開始日"
            />
            <span className="text-xs text-gray-400">〜</span>
            <input
              type="date"
              value={toInputDate(draft.to_date)}
              onChange={(e) => setDraft({ ...draft, to_date: fromInputDate(e.target.value) })}
              className={dateCls}
              aria-label="終了日"
            />
          </div>
        </div>

        {/* 競馬場 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-500 w-10 shrink-0">競馬場</span>
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setDraft({ ...draft, course_name: [] })}
              className={draft.course_name.length === 0 ? activePillCls : pillCls}
            >
              全て
            </button>
            {CHIHOU_COURSES.map((c) => (
              <button
                key={c}
                onClick={() => setDraft({ ...draft, course_name: toggle(draft.course_name, c) })}
                className={draft.course_name.includes(c) ? activePillCls : pillCls}
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
              onClick={() => setDraft({ ...draft, surface: [] })}
              className={draft.surface.length === 0 ? activePillCls : pillCls}
            >
              全て
            </button>
            {SURFACES.map((s) => (
              <button
                key={s.value}
                onClick={() => setDraft({ ...draft, surface: toggle(draft.surface, s.value) })}
                className={draft.surface.includes(s.value) ? activePillCls : pillCls}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* 集計ボタン */}
        <div className="flex justify-end pt-2 border-t border-gray-100">
          <button
            onClick={apply}
            disabled={isPending}
            className="px-5 py-2 rounded-lg text-sm font-semibold bg-green-600 text-white shadow-sm hover:bg-green-700 active:scale-95 transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {isPending && (
              <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
            )}
            {isPending ? "集計中..." : "集計"}
          </button>
        </div>
      </div>
    </>
  );
}
