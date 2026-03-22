"use client";

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

type Props = {
  indices: HorseIndex[];
};

type ChartData = {
  name: string;
  horseNumber: number;
  win: number;
  place: number;
};

export function ProbabilityChart({ indices }: Props) {
  const data: ChartData[] = [...indices]
    .filter((h) => h.win_probability !== null)
    .sort((a, b) => (b.win_probability ?? 0) - (a.win_probability ?? 0))
    .map((h) => ({
      name: `${h.horse_number}.${h.horse_name.length > 4 ? h.horse_name.slice(0, 4) + "…" : h.horse_name}`,
      horseNumber: h.horse_number,
      win: Math.round((h.win_probability ?? 0) * 1000) / 10,
      place: Math.round((h.place_probability ?? 0) * 1000) / 10,
    }));

  if (data.length === 0) return null;

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <span
          className="w-1 h-4 rounded inline-block"
          style={{ background: "var(--green-mid)" }}
        />
        勝率・複勝率チャート
      </h2>

      <div className="w-full" style={{ height: Math.max(200, data.length * 28 + 60) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            layout="vertical"
            data={data}
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
              width={72}
              tick={{ fontSize: 10, fill: "#374151" }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              formatter={(value, name) => [
                typeof value === "number" ? `${value.toFixed(1)}%` : `${value}%`,
                name === "win" ? "単勝率" : "複勝率",
              ]}
              labelFormatter={(label) => label}
              contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
            />
            <Legend
              formatter={(value) => (value === "win" ? "単勝率" : "複勝率")}
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
            />
            <Bar dataKey="win" name="win" radius={[0, 3, 3, 0]}>
              {data.map((entry, index) => (
                <Cell
                  key={entry.horseNumber}
                  fill={index === 0 ? "#1a5c38" : "#4ade80"}
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
