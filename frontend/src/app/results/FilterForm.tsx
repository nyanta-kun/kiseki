"use client";

import { useState } from "react";
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
  include_nonJRA: boolean;
}): string {
  const params = new URLSearchParams();
  if (f.from_date) params.set("from_date", f.from_date);
  if (f.to_date) params.set("to_date", f.to_date);
  if (f.course_name.length) params.set("course_name", f.course_name.join(","));
  if (f.surface.length) params.set("surface", f.surface.join(","));
  if (f.distance_range.length) params.set("distance_range", f.distance_range.join(","));
  if (f.condition.length) params.set("condition", f.condition.join(","));
  if (f.include_nonJRA) params.set("include_nonJRA", "true");
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
  include_nonJRA: boolean;
};

type Props = {
  current: PerformanceFilters;
};

export function FilterForm({ current }: Props) {
  const router = useRouter();
  const pathname = usePathname();

  // URL パラメータから初期状態を作成（ページリロード時のみ評価、key prop で制御）
  const initial: FilterState = {
    from_date:      current.from_date,
    to_date:        current.to_date,
    course_name:    toArr(current.course_name as string[] | string | undefined),
    surface:        toArr(current.surface as string[] | string | undefined),
    distance_range: toArr(current.distance_range as string[] | string | undefined),
    condition:      toArr(current.condition as string[] | string | undefined),
    include_nonJRA: current.include_nonJRA ?? false,
  };

  // フィルタ変更はローカル state に蓄積し、検索ボタン押下時のみ URL に反映
  const [draft, setDraft] = useState<FilterState>(initial);

  function apply() {
    const qs = buildQueryString(draft);
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  const datePreset = currentDatePreset(draft.from_date);

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
                onClick={() => setDraft({ ...draft, from_date: from, to_date: to })}
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

      {/* 地方・海外トグル */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500 w-10 shrink-0" />
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={draft.include_nonJRA}
            onChange={(e) => setDraft({ ...draft, include_nonJRA: e.target.checked })}
            className="w-3.5 h-3.5 rounded accent-blue-600"
          />
          <span className="text-xs text-gray-500">地方・海外を含める</span>
        </label>
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
              onClick={() => setDraft({ ...draft, distance_range: toggle(draft.distance_range, d.value) })}
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

      {/* 検索ボタン */}
      <div className="flex justify-end pt-1 border-t border-gray-50">
        <button
          onClick={apply}
          className="text-xs px-4 py-1.5 rounded-full font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
        >
          検索
        </button>
      </div>
    </div>
  );
}
