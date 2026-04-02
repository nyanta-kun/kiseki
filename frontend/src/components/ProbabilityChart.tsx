"use client";

import { useEffect, useRef, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";
import { HorseIndex, OddsData } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  indices: HorseIndex[];
  initialOdds?: OddsData;
  results?: Map<number, number | null>;
};

type ChartData = {
  name: string;
  num: string;
  horseName: string;
  horseNumber: number;
  win: number;
  place: number;
  winEV?: number;
  placeEV?: number;
  finishPos?: number | null; // undefined=成績なし, null=取消, number=着順
  hasAnagusa?: boolean;
};

// YAxis列レイアウト（左→右）:
// [馬番/着順 右詰 18px] [4px] [馬名 左詰 90px] [4px] [EV単 右詰 24px] [10px] [EV複 右詰 24px] [6px] [穴バッジ 20px]
// 合計: 200px
const NUM_WIDTH = 18;
const GAP = 4;
const NAME_WIDTH = 90;
const EV_WIDTH = 24;
const EV_GAP = 10;
const ANA_GAP = 6;
const ANA_WIDTH = 20;

const NUM_X    = NUM_WIDTH;
const NAME_X   = NUM_WIDTH + GAP;
const WIN_EV_X = NAME_X + NAME_WIDTH + GAP + EV_WIDTH;
const PLACE_EV_X = WIN_EV_X + EV_GAP + EV_WIDTH;
const ANA_X    = PLACE_EV_X + ANA_GAP;
const Y_AXIS_WIDTH = NUM_WIDTH + GAP + NAME_WIDTH + GAP + EV_WIDTH + EV_GAP + EV_WIDTH + ANA_GAP + ANA_WIDTH; // 200

// 着順カラー定義
const FINISH_STYLE: Record<number, { badge: string; text: string; rowFill: string; winBar: string; placeBar: string }> = {
  1: { badge: "#ca8a04", text: "#fff", rowFill: "#fefce8", winBar: "#eab308", placeBar: "#fde047" },
  2: { badge: "#6b7280", text: "#fff", rowFill: "#f9fafb", winBar: "#9ca3af", placeBar: "#d1d5db" },
  3: { badge: "#c2410c", text: "#fff", rowFill: "#fff7ed", winBar: "#f97316", placeBar: "#fdba74" },
};

function evColor(ev: number | undefined): string {
  if (ev === undefined) return "#d1d5db";
  if (ev >= 1.5) return "#16a34a";
  if (ev >= 1.0) return "#f59e0b";
  return "#9ca3af";
}

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

  const finishPos = entry?.finishPos;
  const hasResults = finishPos !== undefined;
  const finishStyle = (finishPos != null && finishPos >= 1 && finishPos <= 3)
    ? FINISH_STYLE[finishPos]
    : null;

  // 着順に応じた文字色（4着以下は薄いグレー）
  const nameColor = finishStyle
    ? "#1f2937"
    : hasResults && finishPos != null
    ? "#9ca3af"
    : "#374151";

  return (
    <g>
      {/* 1〜3着の行背景 */}
      {finishStyle && (
        <rect
          x={0}
          y={ny - 11}
          width={nx}
          height={22}
          fill={finishStyle.rowFill}
        />
      )}

      {/* 馬番 / 着順バッジ */}
      {finishStyle ? (
        // 着順: 色付きバッジ + 白数字
        <>
          <rect x={1} y={ny - 8} width={NUM_WIDTH - 2} height={16} rx={3} fill={finishStyle.badge} />
          <text
            x={NUM_X - 1} y={ny} dy="0.35em"
            textAnchor="end" fontSize={11}
            fill={finishStyle.text} fontWeight="bold"
          >
            {finishPos}
          </text>
        </>
      ) : (
        // 馬番: グレー数字
        <text
          x={NUM_X} y={ny} dy="0.35em"
          textAnchor="end" fontSize={11}
          fill={hasResults && finishPos != null ? "#d1d5db" : "#9ca3af"}
        >
          {num}
        </text>
      )}

      {/* 馬名 */}
      <text
        x={NAME_X} y={ny} dy="0.35em"
        textAnchor="start" fontSize={11}
        fill={nameColor}
        fontWeight={finishStyle ? "600" : "normal"}
      >
        {name}
      </text>

      {/* 単期待値 */}
      {entry?.winEV !== undefined && (
        <text
          x={WIN_EV_X} y={ny} dy="0.35em"
          textAnchor="end" fontSize={11}
          fill={evColor(entry.winEV)}
          fontWeight={entry.winEV >= 1.5 ? "bold" : "normal"}
        >
          {entry.winEV.toFixed(2)}
        </text>
      )}

      {/* 複期待値 */}
      {entry?.placeEV !== undefined && (
        <text
          x={PLACE_EV_X} y={ny} dy="0.35em"
          textAnchor="end" fontSize={11}
          fill={evColor(entry.placeEV)}
          fontWeight={entry.placeEV >= 1.5 ? "bold" : "normal"}
        >
          {entry.placeEV.toFixed(2)}
        </text>
      )}

      {/* 穴ぐさバッジ */}
      {entry?.hasAnagusa && (
        <>
          <rect
            x={ANA_X} y={ny - 7} width={ANA_WIDTH - 2} height={14}
            rx={3} fill="#f97316"
          />
          <text
            x={ANA_X + (ANA_WIDTH - 2) / 2} y={ny} dy="0.35em"
            textAnchor="middle" fontSize={9}
            fill="#fff" fontWeight="bold"
          >
            穴
          </text>
        </>
      )}
    </g>
  );
}

export function ProbabilityChart({ indices, initialOdds, results }: Props) {
  const [sortByHorseNumber, setSortByHorseNumber] = useState(false);
  const [chartWidth, setChartWidth] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setChartWidth(w);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  const odds = initialOdds ?? { win: {}, place: {} };
  const hasResults = results && results.size > 0;

  const filtered = [...indices].filter((h) => h.win_probability !== null);

  const sorted = sortByHorseNumber
    ? filtered.sort((a, b) => a.horse_number - b.horse_number)
    : filtered.sort((a, b) => (b.win_probability ?? 0) - (a.win_probability ?? 0));

  const data: ChartData[] = sorted.map((h) => {
    const hn = String(h.horse_number);
    const winOdds = odds.win[hn];
    const placeOdds = odds.place[hn];
    const winProb = h.win_probability ?? 0;
    const placeProb = h.place_probability ?? 0;

    return {
      name: `${h.horse_number}.${h.horse_name}`,
      num: String(h.horse_number),
      horseName: h.horse_name,
      horseNumber: h.horse_number,
      win: Math.round(winProb * 1000) / 10,
      place: Math.round(placeProb * 1000) / 10,
      winEV: winOdds !== undefined && winProb > 0
        ? Math.round(winOdds * winProb * 100) / 100
        : undefined,
      placeEV: placeOdds !== undefined && placeProb > 0
        ? Math.round(placeOdds * placeProb * 100) / 100
        : undefined,
      finishPos: hasResults ? (results.get(h.horse_number) ?? null) : undefined,
      hasAnagusa: h.anagusa_rank !== null,
    };
  });

  if (data.length === 0) return null;

  const chartData = sortByHorseNumber ? [...data].reverse() : data;
  // 成績あり時は着順ハイライトを使うので topHorseNumber は無効化
  const topHorseNumber = (!hasResults && !sortByHorseNumber) ? data[0]?.horseNumber : null;

  const hasEV = data.some((d) => d.winEV !== undefined || d.placeEV !== undefined);

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
            <span
              className="w-1 h-4 rounded inline-block"
              style={{ background: "var(--green-mid)" }}
            />
            勝率・複勝率チャート
          </h2>
          {hasEV && (
            <p className="text-[10px] text-gray-400 mt-0.5 ml-2.5">
              右2列: 単期待値 / 複期待値（
              <span className="text-green-600 font-bold">緑</span>≥1.5 /
              <span className="text-amber-500"> 黄</span>≥1.0 /
              <span className="text-gray-400"> 灰</span>&lt;1.0）
            </p>
          )}
          {hasResults && (
            <p className="text-[10px] text-gray-400 mt-0.5 ml-2.5 flex items-center gap-1.5">
              <span className="inline-block w-3 h-3 rounded-sm bg-yellow-400" />1着
              <span className="inline-block w-3 h-3 rounded-sm bg-gray-400" />2着
              <span className="inline-block w-3 h-3 rounded-sm bg-orange-500" />3着
            </p>
          )}
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => setSortByHorseNumber(false)}
            aria-pressed={!sortByHorseNumber}
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
            aria-pressed={sortByHorseNumber}
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

      {/* 凡例 */}
      <div className="flex items-center gap-3 mb-1 ml-1 text-[11px] text-gray-500">
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: "#4ade80" }} />
          単勝率
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: "#a78bfa" }} />
          複勝率
        </span>
      </div>

      <div
        ref={containerRef}
        className="w-full"
        style={{ height: Math.max(200, chartData.length * 28 + 60) }}
      >
        {chartWidth > 0 && (
          <BarChart
            layout="vertical"
            data={chartData}
            width={chartWidth}
            height={Math.max(200, chartData.length * 28 + 60)}
            margin={{ top: 0, right: 8, left: 0, bottom: 0 }}
            barSize={8}
            barGap={2}
            accessibilityLayer
            aria-label="勝率・複勝率チャート"
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
                if (!entry) return label;
                const base = entry.finishPos != null
                  ? `${entry.finishPos}着 ${entry.num}番 ${entry.horseName}`
                  : `${entry.num}番 ${entry.horseName}`;
                const evParts = [];
                if (entry.winEV !== undefined) evParts.push(`単EV:${entry.winEV.toFixed(2)}`);
                if (entry.placeEV !== undefined) evParts.push(`複EV:${entry.placeEV.toFixed(2)}`);
                return evParts.length ? `${base} | ${evParts.join(" ")}` : base;
              }}
              contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
            />
            <Bar dataKey="win" name="win" radius={[0, 3, 3, 0]}>
              {chartData.map((entry) => {
                const fs = entry.finishPos != null && entry.finishPos <= 3
                  ? FINISH_STYLE[entry.finishPos]
                  : null;
                const fill = fs
                  ? fs.winBar
                  : hasResults
                  ? "#d1d5db"
                  : entry.horseNumber === topHorseNumber ? "#1a5c38" : "#4ade80";
                return <Cell key={entry.horseNumber} fill={fill} />;
              })}
            </Bar>
            <Bar dataKey="place" name="place" radius={[0, 3, 3, 0]}>
              {chartData.map((entry) => {
                const fs = entry.finishPos != null && entry.finishPos <= 3
                  ? FINISH_STYLE[entry.finishPos]
                  : null;
                const fill = fs
                  ? fs.placeBar
                  : hasResults
                  ? "#e5e7eb"
                  : "#a78bfa";
                return <Cell key={entry.horseNumber} fill={fill} />;
              })}
            </Bar>
          </BarChart>
        )}
      </div>

      {/* スクリーンリーダー向けのアクセシブルなデータテーブル */}
      <table className="sr-only" aria-label="勝率・複勝率データ">
        <caption>勝率・複勝率チャートのデータ一覧</caption>
        <thead>
          <tr>
            <th scope="col">馬番</th>
            <th scope="col">馬名</th>
            <th scope="col">単勝率(%)</th>
            <th scope="col">複勝率(%)</th>
            {chartData.some((d) => d.winEV !== undefined) && <th scope="col">単勝期待値</th>}
            {chartData.some((d) => d.placeEV !== undefined) && <th scope="col">複勝期待値</th>}
            {hasResults && <th scope="col">着順</th>}
          </tr>
        </thead>
        <tbody>
          {chartData.map((entry) => (
            <tr key={entry.horseNumber}>
              <td>{entry.num}</td>
              <td>{entry.horseName}</td>
              <td>{entry.win.toFixed(1)}</td>
              <td>{entry.place.toFixed(1)}</td>
              {chartData.some((d) => d.winEV !== undefined) && (
                <td>{entry.winEV !== undefined ? entry.winEV.toFixed(2) : "-"}</td>
              )}
              {chartData.some((d) => d.placeEV !== undefined) && (
                <td>{entry.placeEV !== undefined ? entry.placeEV.toFixed(2) : "-"}</td>
              )}
              {hasResults && (
                <td>{entry.finishPos != null ? `${entry.finishPos}着` : "-"}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
