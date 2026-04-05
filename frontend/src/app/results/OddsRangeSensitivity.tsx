"use client";

import { useEffect, useState } from "react";
import {
  ComposedChart,
  Bar,
  Cell,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { fetchOddsData } from "@/lib/api";
import type { OddsDataPoint, PerformanceFilters } from "@/lib/api";

// ---------------------------------------------------------------------------
// バケット定義
// ---------------------------------------------------------------------------

type Bucket = [label: string, low: number, high: number];

const WIN_BUCKETS: Bucket[] = [
  ["1〜2",   1,   2],
  ["2〜3",   2,   3],
  ["3〜5",   3,   5],
  ["5〜7",   5,   7],
  ["7〜10",  7,  10],
  ["10〜15", 10, 15],
  ["15〜20", 15, 20],
  ["20〜30", 20, 30],
  ["30〜50", 30, 50],
  ["50+",    50, Infinity],
];

const PLACE_BUCKETS: Bucket[] = [
  ["1〜1.2",   1,    1.2],
  ["1.2〜1.5", 1.2,  1.5],
  ["1.5〜1.8", 1.5,  1.8],
  ["1.8〜2.0", 1.8,  2.0],
  ["2〜2.5",   2,    2.5],
  ["2.5〜3",   2.5,  3],
  ["3〜4",     3,    4],
  ["4〜5",     4,    5],
  ["5〜8",     5,    8],
  ["8+",       8,    Infinity],
];

// ---------------------------------------------------------------------------
// ROI計算
// ---------------------------------------------------------------------------

type RoiResult = {
  total: number;
  hits: number;
  hitRate: number;
  roi: number;
};

function calcWinRoi(
  data: OddsDataPoint[],
  minOdds: number,
  maxOdds: number,
): RoiResult {
  const inRange = data.filter(
    (d) => d.win_odds !== null && d.win_odds >= minOdds && d.win_odds <= maxOdds,
  );
  const total = inRange.length;
  if (total === 0) return { total: 0, hits: 0, hitRate: 0, roi: 0 };
  const hits = inRange.filter((d) => d.win_hit).length;
  const totalReturn = inRange
    .filter((d) => d.win_hit && d.win_odds)
    .reduce((s, d) => s + d.win_odds! * 100, 0);
  return {
    total,
    hits,
    hitRate: hits / total,
    roi: totalReturn / (total * 100),
  };
}

function calcPlaceRoi(
  data: OddsDataPoint[],
  minOdds: number,
  maxOdds: number,
): RoiResult {
  const inRange = data.filter(
    (d) =>
      d.has_place_odds &&
      d.place_odds !== null &&
      d.place_odds >= minOdds &&
      d.place_odds <= maxOdds,
  );
  const total = inRange.length;
  if (total === 0) return { total: 0, hits: 0, hitRate: 0, roi: 0 };
  const hits = inRange.filter((d) => d.place_hit).length;
  const totalReturn = inRange
    .filter((d) => d.place_hit && d.place_odds)
    .reduce((s, d) => s + d.place_odds! * 100, 0);
  return {
    total,
    hits,
    hitRate: hits / total,
    roi: totalReturn / (total * 100),
  };
}

// ---------------------------------------------------------------------------
// ヒストグラム用バケット集計
// ---------------------------------------------------------------------------

type BucketStat = {
  label: string;
  count: number;
  hits: number;
  roi: number; // %
  inRange: boolean;
};

function calcWinBuckets(
  data: OddsDataPoint[],
  low: number,
  high: number,
): BucketStat[] {
  return WIN_BUCKETS.map(([label, bLow, bHigh]) => {
    const items = data.filter(
      (d) => d.win_odds !== null && d.win_odds >= bLow && d.win_odds < (bHigh === Infinity ? Infinity : bHigh + 0.0001),
    );
    const count = items.length;
    const hits = items.filter((d) => d.win_hit).length;
    const totalReturn = items
      .filter((d) => d.win_hit && d.win_odds)
      .reduce((s, d) => s + d.win_odds! * 100, 0);
    const roiVal = count > 0 ? (totalReturn / (count * 100)) * 100 : 0;
    // バケットの中心がlow〜highに含まれるかで判定
    const center = bHigh === Infinity ? bLow * 1.5 : (bLow + bHigh) / 2;
    return {
      label,
      count,
      hits,
      roi: Math.round(roiVal * 10) / 10,
      inRange: center >= low && center <= high,
    };
  });
}

function calcPlaceBuckets(
  data: OddsDataPoint[],
  low: number,
  high: number,
): BucketStat[] {
  return PLACE_BUCKETS.map(([label, bLow, bHigh]) => {
    const items = data.filter(
      (d) =>
        d.has_place_odds &&
        d.place_odds !== null &&
        d.place_odds >= bLow &&
        d.place_odds < (bHigh === Infinity ? Infinity : bHigh + 0.0001),
    );
    const count = items.length;
    const hits = items.filter((d) => d.place_hit).length;
    const totalReturn = items
      .filter((d) => d.place_hit && d.place_odds)
      .reduce((s, d) => s + d.place_odds! * 100, 0);
    const roiVal = count > 0 ? (totalReturn / (count * 100)) * 100 : 0;
    const center = bHigh === Infinity ? bLow * 1.5 : (bLow + bHigh) / 2;
    return {
      label,
      count,
      hits,
      roi: Math.round(roiVal * 10) / 10,
      inRange: center >= low && center <= high,
    };
  });
}

// ---------------------------------------------------------------------------
// デュアルスライダー
// ---------------------------------------------------------------------------

function DualSlider({
  min,
  max,
  step,
  low,
  high,
  onChange,
}: {
  min: number;
  max: number;
  step: number;
  low: number;
  high: number;
  onChange: (low: number, high: number) => void;
}) {
  const lowPct = ((low - min) / (max - min)) * 100;
  const highPct = ((high - min) / (max - min)) * 100;
  const lowOnTop = low > max - (max - min) * 0.1;

  return (
    <div className="relative h-6 mt-2">
      {/* Track */}
      <div className="absolute top-2.5 left-0 right-0 h-1.5 bg-gray-200 rounded-full">
        <div
          className="absolute h-full bg-blue-500 rounded-full"
          style={{ left: `${lowPct}%`, right: `${100 - highPct}%` }}
        />
      </div>
      {/* Low input */}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={low}
        onChange={(e) =>
          onChange(Math.min(+e.target.value, high - step), high)
        }
        className="absolute inset-0 w-full opacity-0 cursor-pointer"
        style={{ zIndex: lowOnTop ? 5 : 3 }}
      />
      {/* High input */}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={high}
        onChange={(e) =>
          onChange(low, Math.max(+e.target.value, low + step))
        }
        className="absolute inset-0 w-full opacity-0 cursor-pointer"
        style={{ zIndex: 4 }}
      />
      {/* Low thumb */}
      <div
        className="absolute top-1 w-4 h-4 bg-white border-2 border-blue-500 rounded-full shadow pointer-events-none -translate-x-1/2"
        style={{ left: `${lowPct}%` }}
      />
      {/* High thumb */}
      <div
        className="absolute top-1 w-4 h-4 bg-white border-2 border-blue-600 rounded-full shadow pointer-events-none -translate-x-1/2"
        style={{ left: `${highPct}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// カスタム Tooltip
// ---------------------------------------------------------------------------

type TooltipPayloadEntry = {
  name: string;
  value: number;
  color: string;
};

type CustomTooltipProps = {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
};

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-md px-3 py-2 text-xs">
      <p className="font-bold text-gray-700 mb-1">{label}</p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: {p.name === "ROI" ? `${p.value}%` : `${p.value}件`}
        </p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// メトリクス表示
// ---------------------------------------------------------------------------

function MetricsRow({ result }: { result: RoiResult }) {
  const roiPct = result.roi * 100;
  const roiColor =
    roiPct >= 100 ? "text-green-600" : roiPct >= 85 ? "text-yellow-600" : "text-red-500";
  return (
    <div className="grid grid-cols-4 gap-2 text-center mt-3">
      <div className="bg-gray-50 rounded-lg p-2">
        <p className="text-xs text-gray-500">対象レース</p>
        <p className="text-lg font-bold text-gray-800">{result.total}</p>
      </div>
      <div className="bg-gray-50 rounded-lg p-2">
        <p className="text-xs text-gray-500">的中数</p>
        <p className="text-lg font-bold text-gray-800">{result.hits}</p>
      </div>
      <div className="bg-gray-50 rounded-lg p-2">
        <p className="text-xs text-gray-500">的中率</p>
        <p className="text-lg font-bold text-blue-700">
          {result.total > 0 ? `${(result.hitRate * 100).toFixed(1)}%` : "—"}
        </p>
      </div>
      <div className="bg-gray-50 rounded-lg p-2">
        <p className="text-xs text-gray-500">ROI</p>
        <p className={`text-lg font-bold ${roiColor}`}>
          {result.total > 0 ? `${roiPct.toFixed(1)}%` : "—"}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// メインコンポーネント
// ---------------------------------------------------------------------------

type Props = {
  filters: PerformanceFilters;
};

export function OddsRangeSensitivity({ filters }: Props) {
  const [data, setData] = useState<OddsDataPoint[] | null>(null);
  const [error, setError] = useState(false);

  // 単勝スライダー
  const [winLow, setWinLow] = useState(1.0);
  const [winHigh, setWinHigh] = useState(50.0);

  // 複勝スライダー
  const [placeLow, setPlaceLow] = useState(1.0);
  const [placeHigh, setPlaceHigh] = useState(10.0);

  useEffect(() => {
    setData(null);
    setError(false);
    fetchOddsData(filters)
      .then((d) => setData(d))
      .catch(() => setError(true));
  }, [filters]);

  if (error) {
    return (
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4">
        <h2 className="text-sm font-bold text-gray-700 mb-2">オッズ帯別ROI感度分析</h2>
        <p className="text-xs text-gray-400 text-center py-4">データを取得できませんでした</p>
      </section>
    );
  }

  if (data === null) {
    return (
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4">
        <h2 className="text-sm font-bold text-gray-700 mb-2">オッズ帯別ROI感度分析</h2>
        <p className="text-xs text-gray-400 text-center py-6 animate-pulse">分析中...</p>
      </section>
    );
  }

  const winResult = calcWinRoi(data, winLow, winHigh);
  const winBuckets = calcWinBuckets(data, winLow, winHigh);

  const placeResult = calcPlaceRoi(data, placeLow, placeHigh);
  const placeBuckets = calcPlaceBuckets(data, placeLow, placeHigh);

  return (
    <section
      className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-6"
      aria-label="オッズ帯別ROI感度分析"
    >
      <h2 className="text-sm font-bold text-gray-700">オッズ帯別ROI感度分析</h2>

      {/* 単勝セクション */}
      <div>
        <h3 className="text-xs font-semibold text-blue-700 mb-2">単勝</h3>

        {/* ヒストグラム */}
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={winBuckets} margin={{ top: 4, right: 32, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} />
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 10 }}
              width={36}
              label={{ value: "件数", angle: -90, position: "insideLeft", style: { fontSize: 10 } }}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 10 }}
              width={44}
              label={{ value: "ROI%", angle: 90, position: "insideRight", style: { fontSize: 10 } }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar
              yAxisId="left"
              dataKey="count"
              name="件数"
              radius={[2, 2, 0, 0]}
              isAnimationActive={false}
            >
              {winBuckets.map((entry, index) => (
                <Cell key={`win-bar-${index}`} fill={entry.inRange ? "#3b82f6" : "#e5e7eb"} />
              ))}
            </Bar>
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="roi"
              name="ROI"
              stroke="#f59e0b"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>

        {/* スライダー */}
        <div className="mt-3 px-1">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>下限: {winLow.toFixed(1)}倍</span>
            <span>上限: {winHigh.toFixed(1)}倍</span>
          </div>
          <DualSlider
            min={1.0}
            max={50.0}
            step={0.5}
            low={winLow}
            high={winHigh}
            onChange={(l, h) => { setWinLow(l); setWinHigh(h); }}
          />
        </div>

        {/* メトリクス */}
        <MetricsRow result={winResult} />
      </div>

      {/* 複勝セクション */}
      <div>
        <h3 className="text-xs font-semibold text-green-700 mb-2">複勝</h3>

        {/* ヒストグラム */}
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={placeBuckets} margin={{ top: 4, right: 32, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} />
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 10 }}
              width={36}
              label={{ value: "件数", angle: -90, position: "insideLeft", style: { fontSize: 10 } }}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 10 }}
              width={44}
              label={{ value: "ROI%", angle: 90, position: "insideRight", style: { fontSize: 10 } }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar
              yAxisId="left"
              dataKey="count"
              name="件数"
              radius={[2, 2, 0, 0]}
              isAnimationActive={false}
            >
              {placeBuckets.map((entry, index) => (
                <Cell key={`place-bar-${index}`} fill={entry.inRange ? "#22c55e" : "#e5e7eb"} />
              ))}
            </Bar>
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="roi"
              name="ROI"
              stroke="#f59e0b"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>

        {/* スライダー */}
        <div className="mt-3 px-1">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>下限: {placeLow.toFixed(1)}倍</span>
            <span>上限: {placeHigh.toFixed(1)}倍</span>
          </div>
          <DualSlider
            min={1.0}
            max={10.0}
            step={0.1}
            low={placeLow}
            high={placeHigh}
            onChange={(l, h) => { setPlaceLow(l); setPlaceHigh(h); }}
          />
        </div>

        {/* メトリクス */}
        <MetricsRow result={placeResult} />

        <p className="text-xs text-gray-400 mt-2">
          ※ 複勝は race_payouts 確定済みレースまたは odds_history がある場合のみ集計対象
        </p>
      </div>
    </section>
  );
}
