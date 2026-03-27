"use client";

import { useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { HorseIndex } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  indices: HorseIndex[];
};

type ChartData = {
  name: string;       // YAxis の dataKey 用（馬番.馬名）
  num: string;        // 馬番（表示用）
  horseName: string;  // 馬名（表示用）
  horseNumber: number;
  win: number;
  place: number;
};

// 馬番2桁 + 馬名9文字（全角）に対応した固定幅
// 全角10px × 9文字 = 90px、馬番エリア = 20px、gap = 4px → 合計 114px
const NUM_AREA = 20;
const GAP = 4;
const NAME_AREA = 90; // 9文字 × 10px
const Y_AXIS_WIDTH = NUM_AREA + GAP + NAME_AREA; // 114

type TickProps = {
  x: string | number;
  y: string | number;
  payload: { value: string };
  data: ChartData[];
};

function CustomYAxisTick({ x, y, payload, data }: TickProps) {
  const entry = data.find((d) => d.name === payload.value);
  const num = entry?.num ?? payload.value.split(".")[0];
  const name = entry?.horseName ?? payload.value.split(".").slice(1).join(".");
  const nx = Number(x);
  const ny = Number(y);

  return (
    <g>
      {/* 馬番: 右寄せ（名前エリアの左端に接する位置で終わる） */}
      <text
        x={nx - NAME_AREA - GAP}
        y={ny}
        dy="0.35em"
        textAnchor="end"
        fontSize={10}
        fill="#9ca3af"
      >
        {num}
      </text>
      {/* 馬名: 左寄せ（固定の開始位置） */}
      <text
        x={nx - NAME_AREA}
        y={ny}
        dy="0.35em"
        textAnchor="start"
        fontSize={10}
        fill="#374151"
      >
        {name}
      </text>
    </g>
  );
}

export function ProbabilityChart({ indices }: Props) {
  const [sortByHorseNumber, setSortByHorseNumber] = useState(false);

  const filtered = [...indices].filter((h) => h.win_probability !== null);

  const sorted = sortByHorseNumber
    ? filtered.sort((a, b) => a.horse_number - b.horse_number)
    : filtered.sort((a, b) => (b.win_probability ?? 0) - (a.win_probability ?? 0));

  const data: ChartData[] = sorted.map((h) => ({
    name: `${h.horse_number}.${h.horse_name}`,
    num: String(h.horse_number),
    horseName: h.horse_name,
    horseNumber: h.horse_number,
    win: Math.round((h.win_probability ?? 0) * 1000) / 10,
    place: Math.round((h.place_probability ?? 0) * 1000) / 10,
  }));

  if (data.length === 0) return null;

  // 馬番順の場合は上から小さい馬番 → BarChartはbottomから描画するため逆順に
  const chartData = sortByHorseNumber ? [...data].reverse() : data;
  // 確率順の場合は1位（最大）を先頭色で強調（reversedなので最後の要素が視覚的上）
  const topHorseNumber = sortByHorseNumber ? null : data[0]?.horseNumber;

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
          <span
            className="w-1 h-4 rounded inline-block"
            style={{ background: "var(--green-mid)" }}
          />
          勝率・複勝率チャート
        </h2>
        <div className="flex gap-1">
          <button
            onClick={() => setSortByHorseNumber(false)}
            className={cn(
              "text-[11px] px-2 py-0.5 rounded-full border transition-colors",
              !sortByHorseNumber
                ? "border-green-600 bg-green-700 text-white"
                : "border-gray-200 text-gray-500 hover:border-green-300"
            )}
          >
            確率順
          </button>
          <button
            onClick={() => setSortByHorseNumber(true)}
            className={cn(
              "text-[11px] px-2 py-0.5 rounded-full border transition-colors",
              sortByHorseNumber
                ? "border-green-600 bg-green-700 text-white"
                : "border-gray-200 text-gray-500 hover:border-green-300"
            )}
          >
            馬番順
          </button>
        </div>
      </div>

      <div className="w-full" style={{ height: Math.max(200, chartData.length * 28 + 60) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            layout="vertical"
            data={chartData}
            margin={{ top: 0, right: 8, left: 0, bottom: 0 }}
            barSize={8}
            barGap={2}
          >
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f0f0f0" />
            <XAxis
              type="number"
              domain={[0, "auto"]}
              tickFormatter={(v) => `${v}%`}
              tick={{ fontSize: 10, fill: "#9ca3af" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={Y_AXIS_WIDTH}
              interval={0}
              tick={(props) => <CustomYAxisTick {...props} data={chartData} />}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              formatter={(value, name) => [
                typeof value === "number" ? `${value.toFixed(1)}%` : `${value}%`,
                name === "win" ? "単勝率" : "複勝率",
              ]}
              labelFormatter={(label) => {
                const entry = chartData.find((d) => d.name === label);
                return entry ? `${entry.num}番 ${entry.horseName}` : label;
              }}
              contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
            />
            <Legend
              formatter={(value) => (value === "win" ? "単勝率" : "複勝率")}
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
            />
            <Bar dataKey="win" name="win" radius={[0, 3, 3, 0]}>
              {chartData.map((entry) => (
                <Cell
                  key={entry.horseNumber}
                  fill={entry.horseNumber === topHorseNumber ? "#1a5c38" : "#4ade80"}
                />
              ))}
            </Bar>
            <Bar dataKey="place" name="place" fill="#a78bfa" radius={[0, 3, 3, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
