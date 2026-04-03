"use client";

import { useState } from "react";
import type { DimensionStat, PerformanceSummary } from "@/lib/api";

type TabKey = "confidence" | "course" | "surface" | "distance" | "condition";

const TABS: { key: TabKey; label: string }[] = [
  { key: "confidence", label: "信頼度別" },
  { key: "course",     label: "競馬場別" },
  { key: "surface",    label: "馬場別" },
  { key: "distance",   label: "距離別" },
  { key: "condition",  label: "条件別" },
];

function roiClass(roi: number): string {
  if (roi >= 1.0) return "text-green-600 font-bold";
  if (roi >= 0.85) return "text-yellow-600";
  return "text-red-500";
}

function StatRow({ row }: { row: DimensionStat }) {
  return (
    <tr className="border-t border-gray-50 hover:bg-gray-50/60 transition-colors">
      <td className="py-2 px-3 text-xs font-medium text-gray-700 whitespace-nowrap">{row.label}</td>
      <td className="py-2 px-3 text-right text-xs text-gray-600">{row.total_races.toLocaleString()}</td>
      <td className="py-2 px-3 text-right text-xs font-medium text-blue-700">
        {(row.win_hit_rate * 100).toFixed(1)}%
      </td>
      <td className="py-2 px-3 text-right text-xs font-medium text-green-700">
        {(row.place_hit_rate * 100).toFixed(1)}%
      </td>
      <td className="py-2 px-3 text-right text-xs text-purple-700">
        {(row.top3_coverage_rate * 100).toFixed(1)}%
      </td>
      <td className={`py-2 px-3 text-right text-xs ${roiClass(row.simulated_roi_win)}`}>
        {(row.simulated_roi_win * 100).toFixed(1)}%
      </td>
      <td className={`py-2 px-3 text-right text-xs ${row.place_roi_races > 0 ? roiClass(row.simulated_roi_place) : "text-gray-300"}`}>
        {row.place_roi_races > 0 ? `${(row.simulated_roi_place * 100).toFixed(1)}%` : "—"}
      </td>
    </tr>
  );
}

type Props = {
  summary: PerformanceSummary;
};

export function BreakdownTabs({ summary }: Props) {
  const [tab, setTab] = useState<TabKey>("confidence");

  // 信頼度別を DimensionStat 形式に変換
  const confidenceRows: DimensionStat[] = (
    [
      { key: "HIGH", label: "HIGH（高信頼）" },
      { key: "MID",  label: "MID（中信頼）"  },
      { key: "LOW",  label: "LOW（低信頼）"  },
    ] satisfies { key: "HIGH" | "MID" | "LOW"; label: string }[]
  )
    .flatMap(({ key, label }) => {
      const s = summary.breakdown[key];
      if (!s) return [];
      return [{ label, ...s } as DimensionStat];
    });

  const rows: DimensionStat[] = {
    confidence: confidenceRows,
    course:    summary.by_course,
    surface:   summary.by_surface,
    distance:  summary.by_distance_range,
    condition: summary.by_condition,
  }[tab];

  return (
    <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      {/* タブヘッダー */}
      <div
        className="flex overflow-x-auto border-b border-gray-100"
        role="tablist"
        aria-label="ディメンション別成績"
      >
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`shrink-0 px-4 py-2.5 text-xs font-medium transition-colors whitespace-nowrap border-b-2 ${
              tab === key
                ? "border-blue-600 text-blue-700 bg-blue-50/50"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* テーブル */}
      <div className="overflow-x-auto">
        {rows.length === 0 ? (
          <p className="text-center py-8 text-xs text-gray-400">データがありません</p>
        ) : (
          <table className="w-full text-sm" aria-label={`${TABS.find((t) => t.key === tab)?.label}成績`}>
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="py-2 px-3 text-left text-xs text-gray-500 font-medium">
                  {TABS.find((t) => t.key === tab)?.label.replace("別", "")}
                </th>
                <th scope="col" className="py-2 px-3 text-right text-xs text-gray-500 font-medium">
                  レース数
                </th>
                <th scope="col" className="py-2 px-3 text-right text-xs text-gray-500 font-medium">
                  単勝的中率
                </th>
                <th scope="col" className="py-2 px-3 text-right text-xs text-gray-500 font-medium">
                  複勝的中率
                </th>
                <th scope="col" className="py-2 px-3 text-right text-xs text-gray-500 font-medium">
                  top3カバー
                </th>
                <th scope="col" className="py-2 px-3 text-right text-xs text-gray-500 font-medium">
                  単勝ROI
                </th>
                <th scope="col" className="py-2 px-3 text-right text-xs text-gray-500 font-medium">
                  複勝ROI
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <StatRow key={row.label} row={row} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
