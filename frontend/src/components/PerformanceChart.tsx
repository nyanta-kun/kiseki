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
import type { MonthlyStats } from "@/lib/api";
import { CHART_GRID, ChartTooltip, chartAxisTick, chartLegendStyle } from "@/lib/chart-theme";

type Props = {
  monthly: MonthlyStats[];
};

export function PerformanceChart({ monthly }: Props) {
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

  const data = monthly.map((s) => ({
    month: s.year_month.replace(/^(\d{4})-(\d{2})$/, "$1/$2"),
    単勝的中率: Math.round(s.win_hit_rate * 1000) / 10,
    複勝的中率: Math.round(s.place_hit_rate * 1000) / 10,
    top3カバー率: Math.round(s.top3_coverage_rate * 1000) / 10,
    回収率: Math.round(s.simulated_roi_win * 1000) / 10,
    レース数: s.total_races,
  }));

  return (
    <div className="space-y-4">
      {data.length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">
          データがありません
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
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis
                    dataKey="month"
                    tick={chartAxisTick(10)}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                    tick={chartAxisTick(10)}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip content={
                    <ChartTooltip
                      formatter={(v, name) => [`${v}%`, name]}
                      labelFormatter={(label) => {
                        const entry = data.find((d) => d.month === label);
                        return entry ? `${label}（${entry.レース数}レース）` : label;
                      }}
                    />
                  } />
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
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis
                    dataKey="month"
                    tick={chartAxisTick(10)}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tickFormatter={(v) => `${v}%`}
                    tick={chartAxisTick(10)}
                    axisLine={false}
                    tickLine={false}
                  />
                  <ReferenceLine y={100} stroke="#9ca3af" strokeDasharray="4 2" label={{ value: "±0", fontSize: 10, fill: "#9ca3af" }} />
                  <Tooltip content={
                    <ChartTooltip
                      formatter={(v, name) => [`${v}%`, name]}
                      labelFormatter={(label) => {
                        const entry = data.find((d) => d.month === label);
                        return entry ? `${label}（${entry.レース数}レース）` : label;
                      }}
                    />
                  } />
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
