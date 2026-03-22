"use client";

import { useRouter } from "next/navigation";

type Props = {
  currentDate: string;
};

function addDays(yyyymmdd: string, days: number): string {
  const y = parseInt(yyyymmdd.slice(0, 4));
  const m = parseInt(yyyymmdd.slice(4, 6)) - 1;
  const d = parseInt(yyyymmdd.slice(6, 8));
  const date = new Date(y, m, d + days);
  return (
    date.getFullYear().toString() +
    String(date.getMonth() + 1).padStart(2, "0") +
    String(date.getDate()).padStart(2, "0")
  );
}

export function DateNav({ currentDate }: Props) {
  const router = useRouter();

  const go = (date: string) => router.push(`/?date=${date}`);

  return (
    <div className="flex items-center justify-between px-4 pb-2">
      <button
        onClick={() => go(addDays(currentDate, -1))}
        className="text-green-200 hover:text-white text-sm px-2 py-1 rounded hover:bg-white/10 transition-colors"
      >
        ← 前日
      </button>

      <input
        type="date"
        defaultValue={`${currentDate.slice(0, 4)}-${currentDate.slice(4, 6)}-${currentDate.slice(6, 8)}`}
        onChange={(e) => {
          const v = e.target.value.replace(/-/g, "");
          if (v.length === 8) go(v);
        }}
        className="text-xs text-green-100 bg-transparent border border-green-600 rounded px-2 py-0.5 cursor-pointer"
      />

      <button
        onClick={() => go(addDays(currentDate, 1))}
        className="text-green-200 hover:text-white text-sm px-2 py-1 rounded hover:bg-white/10 transition-colors"
      >
        翌日 →
      </button>
    </div>
  );
}
