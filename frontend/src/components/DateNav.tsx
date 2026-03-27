"use client";

import { useRouter } from "next/navigation";
import { todayYYYYMMDD } from "@/lib/utils";

type Props = {
  currentDate: string;
  /** 直前の開催日（なければ null） */
  prevDate: string | null;
  /** 直後の開催日（なければ null） */
  nextDate: string | null;
};

export function DateNav({ currentDate, prevDate, nextDate }: Props) {
  const router = useRouter();
  const today = todayYYYYMMDD();
  const isToday = currentDate === today;

  const go = (date: string) => router.push(`/?date=${date}`);

  const toInputValue = (d: string) =>
    `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;

  return (
    <div className="flex items-center justify-between px-4 pb-2 gap-2">
      <button
        onClick={() => prevDate && go(prevDate)}
        disabled={!prevDate}
        className="text-green-200 hover:text-white text-sm px-2 py-1 rounded hover:bg-white/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
      >
        ← 前開催
      </button>

      <div className="flex items-center gap-1.5">
        {!isToday && (
          <button
            onClick={() => go(today)}
            className="text-[11px] px-2 py-0.5 rounded border border-green-500 text-green-200 hover:bg-white/10 transition-colors"
          >
            今日
          </button>
        )}
        {/* key でマウントし直すことで currentDate 変化時に表示を更新 */}
        <input
          key={currentDate}
          type="date"
          defaultValue={toInputValue(currentDate)}
          onChange={(e) => {
            const v = e.target.value.replace(/-/g, "");
            if (v.length === 8) go(v);
          }}
          className="text-xs text-green-100 bg-transparent border border-green-600 rounded px-2 py-0.5 cursor-pointer"
        />
      </div>

      <button
        onClick={() => nextDate && go(nextDate)}
        disabled={!nextDate}
        className="text-green-200 hover:text-white text-sm px-2 py-1 rounded hover:bg-white/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
      >
        翌開催 →
      </button>
    </div>
  );
}
