"use client";

import { useState, useEffect, useCallback } from "react";
import { RaceHistoryEntry } from "@/lib/api";
import { cn, indexColor } from "@/lib/utils";

type FetchFn = (horseId: number) => Promise<RaceHistoryEntry[]>;

function formatTime(sec: number | null): string {
  if (sec == null) return "-";
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1).padStart(4, "0");
  return m > 0 ? `${m}:${s}` : `${s}`;
}

export function Sparkline({ values }: { values: (number | null)[] }) {
  const valid = values.filter((v): v is number => v !== null);
  if (valid.length < 2) return null;

  const min = Math.min(...valid) - 2;
  const max = Math.max(...valid) + 2;
  const range = max - min || 1;
  const w = 80;
  const h = 28;
  const pts = valid.map((v, i) => {
    const x = (i / (valid.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const last = valid[valid.length - 1];
  const prev = valid[valid.length - 2];
  const color = last >= prev ? "#16a34a" : "#f97316";

  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden="true">
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {valid.map((v, i) => {
        const x = (i / (valid.length - 1)) * w;
        const y = h - ((v - min) / range) * h;
        return <circle key={i} cx={x} cy={y} r="2" fill={color} />;
      })}
    </svg>
  );
}

export function HorseHistorySection({
  horseId,
  fetchHistory,
}: {
  horseId: number;
  fetchHistory: FetchFn;
}) {
  const [history, setHistory] = useState<RaceHistoryEntry[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (history !== null || loading) return;
    setLoading(true);
    try {
      setHistory(await fetchHistory(horseId));
    } catch {
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }, [horseId, history, loading, fetchHistory]);

  useEffect(() => {
    load();
  }, [load]);

  const indexValues = history ? history.map((h) => h.composite_index).reverse() : null;

  if (loading) {
    return (
      <div className="animate-pulse flex gap-2 mt-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-8 flex-1 bg-gray-100 rounded" />
        ))}
      </div>
    );
  }

  if (!history || history.length === 0) {
    return (
      <div className="mt-3 pt-3 border-t border-gray-100 text-[10px] text-gray-400">
        近走成績なし
      </div>
    );
  }

  return (
    <div className="mt-3 pt-3 border-t border-gray-100">
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] text-gray-400">近走成績</p>
        {indexValues && indexValues.some((v) => v !== null) && (
          <div className="flex items-center gap-2">
            <p className="text-[10px] text-gray-400">指数推移</p>
            <Sparkline values={indexValues} />
          </div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px] text-gray-600">
          <caption className="sr-only">近走成績</caption>
          <thead>
            <tr className="text-gray-400 border-b border-gray-100">
              <th scope="col" className="text-left pb-1 pr-2 font-normal whitespace-nowrap">日付</th>
              <th scope="col" className="text-left pb-1 pr-2 font-normal whitespace-nowrap">開催</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">着順</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">タイム</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">後3F</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">人気</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">指数</th>
              <th scope="col" className="text-left pb-1 font-normal whitespace-nowrap">不利</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h, i) => (
              <tr key={i} className="border-b border-gray-50 last:border-0">
                <td className="py-1 pr-2 whitespace-nowrap">
                  {h.date.slice(4, 6)}/{h.date.slice(6, 8)}
                </td>
                <td className="py-1 pr-2 whitespace-nowrap">
                  {h.course_name} {h.distance}m{h.surface === "芝" ? "芝" : "ダ"}
                </td>
                <td className="py-1 pr-2 text-right font-bold whitespace-nowrap">
                  {h.finish_position != null ? (
                    <span className={cn(
                      "px-1 rounded",
                      h.finish_position === 1 ? "bg-yellow-100 text-yellow-800" :
                      h.finish_position === 2 ? "bg-gray-100 text-gray-700" :
                      h.finish_position === 3 ? "bg-orange-100 text-orange-700" :
                      "text-gray-500"
                    )}>
                      {h.finish_position}着
                    </span>
                  ) : "-"}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">
                  {formatTime(h.finish_time)}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">
                  {h.last_3f != null ? h.last_3f.toFixed(1) : "-"}
                </td>
                <td className="py-1 pr-2 text-right whitespace-nowrap">
                  {h.win_popularity != null ? `${h.win_popularity}番人気` : "-"}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">
                  {h.composite_index != null ? (
                    <span className={cn("font-medium", indexColor(h.composite_index))}>
                      {h.composite_index.toFixed(1)}
                    </span>
                  ) : "-"}
                </td>
                <td className="py-1 whitespace-nowrap">
                  {h.remarks ? (
                    <span className="text-[10px] text-orange-600 bg-orange-50 px-1 py-0.5 rounded border border-orange-200">
                      {h.remarks}
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
