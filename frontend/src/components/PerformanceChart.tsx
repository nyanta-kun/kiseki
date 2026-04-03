"use client";

import { useEffect, useRef, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import type { MonthlyStats, ConfidenceStats } from "@/lib/api";

type ConfidenceFilter = "ALL" | "HIGH" | "MID" | "LOW";

type Props = {
  monthly: MonthlyStats[];
  initialFilter: ConfidenceFilter;
};

function getStats(month: MonthlyStats, filter: ConfidenceFilter): ConfidenceStats | null {
  if (filter === "ALL") return month;
  return month.breakdown[filter];
}

export function PerformanceChart({ monthly, initialFilter }: Props) {
  const [filter, setFilter] = useState<ConfidenceFilter>(initialFilter);
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setWidth(w);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const data = monthly
    .map((m) => {
      const s = getStats(m, filter);
      if (!s) return null;
      return {
        month: m.year_month.replace(/^(\d{4})-(\d{2})$/, "$1/$2"),
        単勝的中率: Math.round(s.win_hit_rate * 1000) / 10,
        複勝的中率: Math.round(s.place_hit_rate * 1000) / 10,
        top3カバー率: Math.round(s.top3_coverage_rate * 1000) / 10,
        回収率: Math.round(s.simulated_roi_win * 1000) / 10,
        レース数: s.total_races,
      };
    })
    .filter((d): d is NonNullable<typeof d> => d !== null);

  const filters: { key: ConfidenceFilter; label: string; color: string }[] = [
    { key: "ALL", label: "全レース", color: "#6b7280" },
    { key: "HIGH", label: "HIGH（高信頼）", color: "#16a34a" },
    { key: "MID", label: "MID（中信頼）", color: "#ca8a04" },
    { key: "LOW", label: "LOW（低信頼）", color: "#dc2626" },
  ];

  return (
    <div className="space-y-4">
      {/* 信頼度フィルタ */}
      <div className="flex flex-wrap gap-2">
        {filters.map(({ key, label, color }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            aria-pressed={filter === key}
            className={`text-xs px-3 py-1.5 rounded-full border transition-colors font-medium ${
              filter === key
                ? "text-white border-transparent"
                : "text-gray-600 border-gray-200 hover:border-gray-400"
            }`}
            style={filter === key ? { background: color, borderColor: color } : undefined}
          >
            {label}
          </button>
        ))}
      </div>

      {data.length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">
          {filter !== "ALL" ? `${filter}信頼度のデータがありません` : "データがありません"}
        </div>
      ) : (
        <>
          {/* 的中率グラフ */}
          <div ref={containerRef} className="w-full">
            <p className="text-xs text-gray-500 mb-2 font-medium">的中率推移（%）</p>
            <div style={{ height: 200 }}>
              {width > 0 && (
                <LineChart
                  width={width}
                  height={200}
                  data={data}
                  margin={{ top: 4, right: 16, left: -8, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis
                    dataKey="month"
                    tick={{ fontSize: 10, fill: "#9ca3af" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                    tick={{ fontSize: 10, fill: "#9ca3af" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    formatter={(v, name) => [`${v}%`, name]}
                    labelFormatter={(label) => {
                      const entry = data.find((d) => d.month === label);
                      return entry ? `${label}（${entry.レース数}レース）` : label;
                    }}
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
                  />
                  <Legend
                    iconType="circle"
                    iconSize={8}
                    wrapperStyle={{ fontSize: 11 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="単勝的中率"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="複勝的中率"
                    stroke="#22c55e"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="top3カバー率"
                    stroke="#a78bfa"
                    strokeWidth={2}
                    strokeDasharray="4 2"
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                </LineChart>
              )}
            </div>
          </div>

          {/* 回収率グラフ */}
          <div>
            <p className="text-xs text-gray-500 mb-2 font-medium">
              単勝シミュレーション回収率（%）
              <span className="text-gray-400 font-normal ml-1">— 毎レース予測1位に同額賭けた場合</span>
            </p>
            <div style={{ height: 180 }}>
              {width > 0 && (
                <LineChart
                  width={width}
                  height={180}
                  data={data}
                  margin={{ top: 4, right: 16, left: -8, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis
                    dataKey="month"
                    tick={{ fontSize: 10, fill: "#9ca3af" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tickFormatter={(v) => `${v}%`}
                    tick={{ fontSize: 10, fill: "#9ca3af" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <ReferenceLine y={100} stroke="#9ca3af" strokeDasharray="4 2" label={{ value: "±0", fontSize: 10, fill: "#9ca3af" }} />
                  <Tooltip
                    formatter={(v, name) => [`${v}%`, name]}
                    labelFormatter={(label) => {
                      const entry = data.find((d) => d.month === label);
                      return entry ? `${label}（${entry.レース数}レース）` : label;
                    }}
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
                  />
                  <Line
                    type="monotone"
                    dataKey="回収率"
                    stroke="#f97316"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                </LineChart>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
