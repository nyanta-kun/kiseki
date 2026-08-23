"use client";

/**
 * 競輪の日付ナビ（前月・前日・今日・日付指定・翌日・翌月）。
 *
 * 🔴 **`components/DateNav.tsx` とは別物**。あちらは `prevDate`/`nextDate` を
 *    呼び出し側から渡す「**開催日**送り」で、色もヘッダーバー前提（`text-blue-200`）。
 *    こちらは**暦日送り**で、白背景のページ本文に置く体裁。混ぜないこと。
 *
 * 2026-08-24 に `app/keirin/page.tsx` から切り出した。レビュー画面
 * （`app/keirin/review`）でも同じ操作感で日付を送りたくなったため。
 * 🔴 **写して増やさない。** 日付の進め方（月末クランプ・未来日クランプ）は
 *    ここが正本で、2箇所に置くと片方だけ直したときに黙って食い違う。
 *
 * 日付は **YYYYMMDD** で受け渡す（ISO が要る画面は呼び出し側で変換する）。
 */
import { useRef } from "react";

import { todayYYYYMMDD } from "@/lib/utils";

export function fmtYMD(yyyymmdd: string): string {
  if (yyyymmdd.length !== 8) return yyyymmdd;
  return `${yyyymmdd.slice(0, 4)}/${yyyymmdd.slice(4, 6)}/${yyyymmdd.slice(6, 8)}`;
}

export function toISODate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

export function prevDay(yyyymmdd: string): string {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`);
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

export function nextDay(yyyymmdd: string): string {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`);
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

// 月移動（月末日超過は移動先の月末にクランプ。例: 3/31 → 2/28）
export function addMonths(yyyymmdd: string, delta: number): string {
  const y = parseInt(yyyymmdd.slice(0, 4), 10);
  const m = parseInt(yyyymmdd.slice(4, 6), 10) - 1;
  const day = parseInt(yyyymmdd.slice(6, 8), 10);
  const lastDay = new Date(Date.UTC(y, m + delta + 1, 0)).getUTCDate();
  const d = new Date(Date.UTC(y, m + delta, Math.min(day, lastDay)));
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

// 未来日は今日にクランプ（YYYYMMDD は文字列比較で大小判定可能）
export function clampToToday(yyyymmdd: string): string {
  const today = todayYYYYMMDD();
  return yyyymmdd > today ? today : yyyymmdd;
}

const DATE_NAV_BTN_CLS =
  "px-2 sm:px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 text-xs sm:text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed text-center whitespace-nowrap flex-shrink-0";

export function DateNav({ date, onChange }: { date: string; onChange: (d: string) => void }) {
  const dateInputRef = useRef<HTMLInputElement>(null);
  const isToday = date === todayYYYYMMDD();

  const openPicker = () => {
    const input = dateInputRef.current;
    if (!input) return;
    try { input.showPicker(); } catch { input.click(); }
  };

  return (
    <div className="flex items-center justify-between gap-1 sm:gap-2">
      <button onClick={() => onChange(addMonths(date, -1))} className={DATE_NAV_BTN_CLS} aria-label="前月">
        ≪<span className="hidden sm:inline"> 前月</span>
      </button>
      <button onClick={() => onChange(prevDay(date))} className={DATE_NAV_BTN_CLS} aria-label="前日">
        ←<span className="hidden sm:inline"> 前日</span>
      </button>
      {/* 中央: 今日ボタン（非今日時のみ）+ 日付表示（タップでピッカー） */}
      <div className="flex items-center justify-center gap-1.5 sm:gap-2 flex-1 min-w-0">
        {!isToday && (
          <button
            onClick={() => onChange(todayYYYYMMDD())}
            className="text-[11px] px-1.5 sm:px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors whitespace-nowrap flex-shrink-0"
          >
            今日
          </button>
        )}
        <div className="relative min-w-0">
          <button
            onClick={openPicker}
            className="flex items-center gap-1 text-xs sm:text-sm font-semibold text-gray-800 dark:text-gray-100 hover:text-blue-600 dark:hover:text-blue-400 transition-colors whitespace-nowrap"
            aria-label="日付を選択"
          >
            {fmtYMD(date)}
            <span className="text-sm leading-none">📅</span>
          </button>
          <input
            key={date}
            ref={dateInputRef}
            type="date"
            aria-hidden="true"
            tabIndex={-1}
            className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
            defaultValue={toISODate(date)}
            onChange={(e) => {
              const v = e.target.value.replace(/-/g, "");
              if (v.length === 8) onChange(v);
            }}
          />
        </div>
      </div>
      <button onClick={() => onChange(nextDay(date))} disabled={isToday} className={DATE_NAV_BTN_CLS} aria-label="翌日">
        <span className="hidden sm:inline">翌日 </span>→
      </button>
      <button onClick={() => onChange(clampToToday(addMonths(date, 1)))} disabled={isToday} className={DATE_NAV_BTN_CLS} aria-label="翌月">
        <span className="hidden sm:inline">翌月 </span>≫
      </button>
    </div>
  );
}
