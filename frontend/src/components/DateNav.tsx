"use client";

import { useRef } from "react";
import { useRouter } from "next/navigation";
import { todayYYYYMMDD, formatDate } from "@/lib/utils";

type Props = {
  currentDate: string;
  prevDate: string | null;
  nextDate: string | null;
  basePath?: string;
};

export function DateNav({ currentDate, prevDate, nextDate, basePath = "/races" }: Props) {
  const router = useRouter();
  const dateInputRef = useRef<HTMLInputElement>(null);
  const today = todayYYYYMMDD();
  const isToday = currentDate === today;
  const isChihou = basePath.startsWith("/chihou");

  const go = (date: string) => router.push(`${basePath}?date=${date}`);

  const toInputValue = (d: string) =>
    `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;

  const openPicker = () => {
    const input = dateInputRef.current;
    if (!input) return;
    try {
      input.showPicker();
    } catch {
      input.click();
    }
  };

  return (
    <div className="max-w-3xl mx-auto flex items-center justify-between px-4 pb-2 gap-2">
      {/* 前開催 */}
      <button
        onClick={() => prevDate && go(prevDate)}
        disabled={!prevDate}
        aria-disabled={!prevDate}
        aria-label="前の開催日へ"
        className={`${isChihou ? "text-green-100" : "text-blue-200"} hover:text-white text-sm px-2 py-1 rounded hover:bg-white/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0`}
      >
        <span aria-hidden="true">←</span> 前開催
      </button>

      {/* 中央: 今日ボタン + 日付 + カレンダーアイコン */}
      <div className="flex items-center gap-2 min-w-0">
        {!isToday && (
          <button
            onClick={() => go(today)}
            className={`text-[11px] px-2 py-0.5 rounded border ${isChihou ? "border-green-300 text-green-100" : "border-blue-400 text-blue-200"} hover:bg-white/10 transition-colors flex-shrink-0`}
          >
            今日
          </button>
        )}
        <span className="text-white text-sm font-medium whitespace-nowrap">
          {formatDate(currentDate)}
        </span>
        {/* カレンダーアイコン（クリックでdate picker起動） */}
        <div className="relative flex-shrink-0">
          <button
            onClick={openPicker}
            className={`${isChihou ? "text-green-200" : "text-blue-300"} hover:text-white transition-colors text-base leading-none`}
            aria-label="日付を選択"
          >
            📅
          </button>
          <input
            key={currentDate}
            ref={dateInputRef}
            type="date"
            aria-hidden="true"
            tabIndex={-1}
            className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
            defaultValue={toInputValue(currentDate)}
            onChange={(e) => {
              const v = e.target.value.replace(/-/g, "");
              if (v.length === 8) go(v);
            }}
          />
        </div>
      </div>

      {/* 翌開催 */}
      <button
        onClick={() => nextDate && go(nextDate)}
        disabled={!nextDate}
        aria-disabled={!nextDate}
        aria-label="次の開催日へ"
        className={`${isChihou ? "text-green-100" : "text-blue-200"} hover:text-white text-sm px-2 py-1 rounded hover:bg-white/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0`}
      >
        翌開催 <span aria-hidden="true">→</span>
      </button>
    </div>
  );
}
