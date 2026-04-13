"use client";

import { useState, useTransition } from "react";
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

// "カスタム" は日付を変更しない状態表示専用ボタン
const DATE_PRESETS = [
  { label: "全期間",   value: "all" },
  { label: "今年",     value: "this_year" },
  { label: "今月",     value: "this_month" },
  { label: "今日",     value: "today" },
  { label: "カスタム", value: "custom" },
];

function todayStr(): string {
  // JST固定: ブラウザのロケール/タイムゾーン設定に依存しないようにする
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" }).replace(/-/g, "");
}

function thisMonthStartStr(): string {
  const [y, m] = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" }).split("-");
  return `${y}${m}01`;
}

/** プリセット値から { from, to } を返す。"custom" は null（日付変更なし） */
function getPresetDates(preset: string): { from: string; to: string } | null {
  const yy = new Date().getFullYear();
  const to = todayStr();
  switch (preset) {
    case "all":        return { from: "20230101", to };
    case "this_year":  return { from: `${yy}0101`, to };
    case "this_month": return { from: thisMonthStartStr(), to };
    case "today":      return { from: to, to };
    default:           return null; // "custom" — 日付はそのまま
  }
}

/** 現在の from/to がどのプリセットに該当するか判定する */
function currentDatePreset(from?: string, to?: string): string {
  const today = todayStr();
  const yy = new Date().getFullYear();
  // 終了日が今日でなければカスタム
  if (to && to !== today) return "custom";
  if (!from) return "this_month";
  if (from === "20230101")         return "all";
  if (from === `${yy}0101`)        return "this_year";
  if (from === thisMonthStartStr()) return "this_month";
  if (from === today)              return "today";
  return "custom";
}

/** YYYYMMDD → YYYY-MM-DD（input[type=date] 用） */
function toInputDate(d?: string): string {
  if (!d || d.length !== 8) return "";
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
}

/** YYYY-MM-DD → YYYYMMDD */
function fromInputDate(d: string): string {
  return d.replace(/-/g, "");
}

/**
 * RSC シリアライズで string[] が string になるケースを防ぐ正規化関数。
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

type FilterState = {
  from_date?: string;
  to_date?: string;
  course_name: string[];
  surface: string[];
  distance_range: string[];
  condition: string[];
};

type Props = {
  current: PerformanceFilters;
};

export function FilterForm({ current }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [isPending, startTransition] = useTransition();

  const initial: FilterState = {
    from_date:      current.from_date,
    to_date:        current.to_date,
    course_name:    toArr(current.course_name as string[] | string | undefined),
    surface:        toArr(current.surface as string[] | string | undefined),
    distance_range: toArr(current.distance_range as string[] | string | undefined),
    condition:      toArr(current.condition as string[] | string | undefined),
  };

  const [draft, setDraft] = useState<FilterState>(initial);

  function apply() {
    const qs = buildQueryString(draft);
    startTransition(() => {
      router.push(qs ? `${pathname}?${qs}` : pathname);
    });
  }

  const datePreset = currentDatePreset(draft.from_date, draft.to_date);

  const activePillCls = "text-xs px-2.5 py-1 rounded-full font-medium transition-colors bg-blue-600 text-white border border-blue-600";
  const pillCls       = "text-xs px-2.5 py-1 rounded-full font-medium transition-colors border border-gray-200 text-gray-600 hover:border-blue-300 hover:text-blue-600 cursor-pointer";
  const dateCls       = "text-xs border border-gray-200 rounded-md px-2 py-1 text-gray-600 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-200";

  return (
    <>
      {/* 集計中オーバーレイ */}
      {isPending && (
        <>
          <div className="fixed top-0 left-0 right-0 z-50 h-1 bg-emerald-100 overflow-hidden">
            <div
              className="h-full w-1/3 bg-emerald-500"
              style={{ animation: "results-progress 1.4s ease-in-out infinite" }}
            />
          </div>
          <div className="fixed inset-0 z-40 bg-white/70 flex items-center justify-center">
            <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-12 flex flex-col items-center gap-6">
              <div className="w-20 h-20 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-base font-semibold text-gray-700">集計中...</p>
            </div>
          </div>
          <style>{`
            @keyframes results-progress {
              0%   { transform: translateX(-100%); }
              100% { transform: translateX(400%); }
            }
          `}</style>
        </>
      )}

      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-3">

        {/* 期間 — プリセットピル */}
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
                    // "カスタム" はクリックしても日付を変えない（選択状態の表示のみ）
                  }}
                  className={datePreset === p.value ? activePillCls : pillCls}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* 期間 — 日付入力（ラベル幅分スペーサーで位置を揃える） */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-10 shrink-0" aria-hidden />
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={toInputDate(draft.from_date)}
              onChange={(e) =>
                setDraft({ ...draft, from_date: fromInputDate(e.target.value) })
              }
              className={dateCls}
              aria-label="開始日"
            />
            <span className="text-xs text-gray-400">〜</span>
            <input
              type="date"
              value={toInputDate(draft.to_date)}
              onChange={(e) =>
                setDraft({ ...draft, to_date: fromInputDate(e.target.value) })
              }
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
            {COURSES.map((c) => (
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

        {/* 距離 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-500 w-10 shrink-0">距離</span>
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setDraft({ ...draft, distance_range: [] })}
              className={draft.distance_range.length === 0 ? activePillCls : pillCls}
            >
              全て
            </button>
            {DISTANCE_OPTIONS.map((d) => (
              <button
                key={d.value}
                onClick={() =>
                  setDraft({ ...draft, distance_range: toggle(draft.distance_range, d.value) })
                }
                className={draft.distance_range.includes(d.value) ? activePillCls : pillCls}
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
              onClick={() => setDraft({ ...draft, condition: [] })}
              className={draft.condition.length === 0 ? activePillCls : pillCls}
            >
              全て
            </button>
            {CONDITIONS.map((c) => (
              <button
                key={c}
                onClick={() => setDraft({ ...draft, condition: toggle(draft.condition, c) })}
                className={draft.condition.includes(c) ? activePillCls : pillCls}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        {/* 集計ボタン */}
        <div className="flex justify-end pt-2 border-t border-gray-100">
          <button
            onClick={apply}
            disabled={isPending}
            className="px-5 py-2 rounded-lg text-sm font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center gap-2"
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
