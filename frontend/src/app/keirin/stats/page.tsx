"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BarChart2 } from "lucide-react";
import {
  ComposedChart,
  Bar,
  Cell,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { fetchKeirinStats, type KeirinStatItem, type KeirinStatsResponse } from "@/lib/api";

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

function toISODate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

function addDays(iso: string, days: number): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function todayISO(): string {
  // JST の今日。toLocaleDateString("sv-SE") は YYYY-MM-DD を直接返す
  // （Date への再パース→toISOString は実行環境 TZ で日付がずれるため使わない）
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });
}

function formatROI(roi: number | null): string {
  if (roi == null) return "—";
  return (roi * 100).toFixed(1) + "%";
}

function formatYen(val: number): string {
  if (val >= 1_000_000) return `¥${(val / 10000).toFixed(0)}万`;
  if (val >= 10_000) return `¥${(val / 10000).toFixed(1)}万`;
  return `¥${val.toLocaleString()}`;
}

// ---------------------------------------------------------------------------
// カスタム tooltip
// ---------------------------------------------------------------------------

function CustomTooltip({ active, payload, label, cumMode }: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
  cumMode: "period" | "month" | "year";
}) {
  if (!active || !payload || payload.length === 0) return null;
  const bet = payload.find(p => p.name === "投資額")?.value ?? 0;
  const payout = payload.find(p => p.name === "回収額")?.value ?? 0;
  const roiKey = cumMode === "month" ? "当月累積ROI" : cumMode === "year" ? "当年累積ROI" : "累積ROI";
  const roi = payload.find(p => p.name === roiKey)?.value;
  const profit = payout - bet;

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-lg p-3 shadow-lg text-xs">
      <p className="font-semibold text-gray-700 dark:text-gray-200 mb-1.5">{label}</p>
      <div className="space-y-0.5">
        <div className="flex justify-between gap-4">
          <span className="text-gray-500">投資</span>
          <span className="tabular-nums text-gray-700 dark:text-gray-200">{formatYen(bet)}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-gray-500">回収</span>
          <span className={`tabular-nums font-semibold ${payout >= bet ? "text-emerald-600" : "text-red-500"}`}>{formatYen(payout)}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-gray-500">損益</span>
          <span className={`tabular-nums font-semibold ${profit >= 0 ? "text-emerald-600" : "text-red-500"}`}>
            {profit >= 0 ? "+" : ""}{formatYen(profit)}
          </span>
        </div>
        {roi != null && (
          <div className="flex justify-between gap-4 pt-0.5 border-t border-gray-100 dark:border-gray-700 mt-0.5">
            <span className="text-gray-500">累積ROI</span>
            <span className={`tabular-nums font-semibold ${roi >= 1 ? "text-blue-600" : "text-orange-500"}`}>{(roi * 100).toFixed(1)}%</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// サマリーカード
// ---------------------------------------------------------------------------

function SummaryCard({ label, n_picks, n_hits, total_bet, total_payout, roi }: {
  label: string;
  n_picks: number;
  n_hits: number;
  total_bet: number;
  total_payout: number;
  roi: number | null;
}) {
  const hitRate = n_picks > 0 ? ((n_hits / n_picks) * 100).toFixed(0) + "%" : "—";
  const roiColor = roi == null ? "text-gray-400" : roi >= 1.0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500";
  const profit = total_payout - total_bet;

  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-3 sm:p-4">
      <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2">{label}</p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3">
        <div>
          <p className="text-xs text-gray-400 dark:text-gray-500">推奨/的中</p>
          <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">
            {n_picks}<span className="text-xs font-normal text-gray-400 ml-0.5">件</span>
            <span className="text-gray-400 mx-0.5">/</span>
            {n_hits}<span className="text-xs font-normal text-gray-400 ml-0.5">({hitRate})</span>
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400 dark:text-gray-500">投資</p>
          <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">{formatYen(total_bet)}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 dark:text-gray-500">回収</p>
          <p className={`text-sm font-bold tabular-nums ${total_payout >= total_bet ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"}`}>
            {formatYen(total_payout)}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400 dark:text-gray-500">損益 / ROI</p>
          <p className={`text-sm font-bold tabular-nums ${roiColor}`}>
            {profit >= 0 ? "+" : ""}{formatYen(profit)}
            <span className="text-xs ml-1">({formatROI(roi)})</span>
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 期間プリセット
// ---------------------------------------------------------------------------

type Preset = "7d" | "30d" | "90d" | "thisMonth" | "thisYear" | "custom";

function calcRange(preset: Preset): { from: string; to: string } {
  const to = todayISO();
  switch (preset) {
    case "7d":     return { from: addDays(to, -6), to };
    case "30d":    return { from: addDays(to, -29), to };
    case "90d":    return { from: addDays(to, -89), to };
    case "thisMonth": {
      const d = new Date(to);
      return { from: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`, to };
    }
    case "thisYear":  return { from: `${new Date(to).getFullYear()}-01-01`, to };
    default:       return { from: addDays(to, -29), to };
  }
}

const PRESETS: { key: Preset; label: string }[] = [
  { key: "7d", label: "7日" },
  { key: "30d", label: "30日" },
  { key: "90d", label: "90日" },
  { key: "thisMonth", label: "当月" },
  { key: "thisYear", label: "当年" },
  { key: "custom", label: "指定" },
];

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

type Granularity = "daily" | "monthly";
type CumMode = "period" | "month" | "year";
type RankFilter = "all" | "S1" | "SS" | "S";

const RANK_FILTERS: { key: RankFilter; label: string }[] = [
  { key: "all", label: "全体" },
  { key: "SS", label: "SS" },
  { key: "S", label: "S" },
  { key: "S1", label: "S1" },
];

export default function KeirinStatsPage() {
  const [preset, setPreset] = useState<Preset>("30d");
  const [granularity, setGranularity] = useState<Granularity>("daily");
  const [cumMode, setCumMode] = useState<CumMode>("month");
  const [rankFilter, setRankFilter] = useState<RankFilter>("all");
  const [from, setFrom] = useState(() => calcRange("30d").from);
  const [to, setTo] = useState(() => calcRange("30d").to);
  const [data, setData] = useState<KeirinStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (f: string, t: string, g: Granularity, rank: RankFilter) => {
    setLoading(true);
    try {
      const res = await fetchKeirinStats(f, t, g, rank);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(from, to, granularity, rankFilter);
  }, [from, to, granularity, rankFilter, load]);

  function applyPreset(p: Preset) {
    setPreset(p);
    if (p !== "custom") {
      const { from: f, to: t } = calcRange(p);
      setFrom(f);
      setTo(t);
    }
  }

  // グラフデータ変換
  const chartData = (data?.items ?? []).map((item: KeirinStatItem) => {
    const cumROI = cumMode === "month" ? item.cum_month_roi
                 : cumMode === "year"  ? item.cum_year_roi
                 : item.cum_roi;
    const roiKey = cumMode === "month" ? "当月累積ROI"
                 : cumMode === "year"  ? "当年累積ROI"
                 : "累積ROI";
    return {
      date: item.date,
      投資額: item.total_bet,
      回収額: item.total_payout,
      [roiKey]: cumROI,
      // 回収額バーの色分け用（期間ROIが100%未満=赤・null=賭けなしで通常色）
      _belowHundred: item.roi != null && item.roi < 1,
    };
  });

  const roiLineKey = cumMode === "month" ? "当月累積ROI"
                   : cumMode === "year"  ? "当年累積ROI"
                   : "累積ROI";

  // 当月・当年の集計（最新の累積値を使用）
  const lastItem = data?.items[data.items.length - 1];
  const monthSummary = lastItem ? {
    n_picks: data!.items.filter(i => i.date.slice(0, 7) === todayISO().slice(0, 7)).reduce((s, i) => s + i.n_picks, 0),
    n_hits: data!.items.filter(i => i.date.slice(0, 7) === todayISO().slice(0, 7)).reduce((s, i) => s + i.n_hits, 0),
    total_bet: lastItem.cum_month_bet,
    total_payout: lastItem.cum_month_payout,
    roi: lastItem.cum_month_roi,
  } : null;
  const yearSummary = lastItem ? {
    n_picks: data!.items.filter(i => i.date.slice(0, 4) === todayISO().slice(0, 4)).reduce((s, i) => s + i.n_picks, 0),
    n_hits: data!.items.filter(i => i.date.slice(0, 4) === todayISO().slice(0, 4)).reduce((s, i) => s + i.n_hits, 0),
    total_bet: lastItem.cum_year_bet,
    total_payout: lastItem.cum_year_payout,
    roi: lastItem.cum_year_roi,
  } : null;

  const maxBet = Math.max(...(data?.items ?? []).map(i => Math.max(i.total_bet, i.total_payout)), 1);
  const yAxisMax = Math.ceil(maxBet / 5000) * 5000 + 5000;

  const rankLabel = RANK_FILTERS.find(r => r.key === rankFilter)?.label ?? "全体";
  const chartTitle = rankFilter === "all" ? "全体の投資・回収推移" : `${rankLabel} の投資・回収推移`;

  return (
    <div className="w-full sm:max-w-4xl sm:mx-auto px-3 sm:px-4 py-4 pb-20 space-y-4">
      {/* ヘッダー */}
      <div className="flex items-center gap-2">
        <Link href="/keirin" className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors">
          <ArrowLeft size={18} />
        </Link>
        <BarChart2 size={20} className="text-blue-500" />
        <h1 className="text-lg font-extrabold tracking-widest text-gray-900 dark:text-white">成績グラフ</h1>
      </div>

      {/* コントロール */}
      <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-3 space-y-3">
        {/* 期間プリセット */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-gray-400 dark:text-gray-500 mr-1">期間</span>
          {PRESETS.map(p => (
            <button
              key={p.key}
              onClick={() => applyPreset(p.key)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                preset === p.key
                  ? "bg-blue-500 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* カスタム期間入力 */}
        {preset === "custom" && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-gray-400 dark:text-gray-500">From</span>
            <input
              type="date"
              value={from}
              max={to}
              onChange={e => setFrom(e.target.value)}
              className="text-xs border border-gray-200 dark:border-gray-600 rounded px-2 py-1 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200"
            />
            <span className="text-xs text-gray-400 dark:text-gray-500">〜</span>
            <input
              type="date"
              value={to}
              min={from}
              max={todayISO()}
              onChange={e => setTo(e.target.value)}
              className="text-xs border border-gray-200 dark:border-gray-600 rounded px-2 py-1 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200"
            />
          </div>
        )}

        {/* ランク */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-gray-400 dark:text-gray-500 mr-1">ランク</span>
          {RANK_FILTERS.map(r => (
            <button
              key={r.key}
              onClick={() => setRankFilter(r.key)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                rankFilter === r.key
                  ? "bg-blue-500 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        {/* 粒度・累積モード */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-400 dark:text-gray-500">粒度</span>
            {(["daily", "monthly"] as const).map(g => (
              <button
                key={g}
                onClick={() => setGranularity(g)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  granularity === g
                    ? "bg-gray-700 dark:bg-gray-200 text-white dark:text-gray-900"
                    : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
                }`}
              >
                {g === "daily" ? "日別" : "月別"}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-400 dark:text-gray-500">累積ROI</span>
            {([["period", "全期間"], ["month", "当月"], ["year", "当年"]] as [CumMode, string][]).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setCumMode(key)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  cumMode === key
                    ? "bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 border border-blue-300 dark:border-blue-600"
                    : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* メイングラフ */}
      <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-3 sm:p-4">
        <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2">{chartTitle}</p>
        {loading ? (
          <div className="h-64 flex items-center justify-center text-gray-400 text-sm animate-pulse">読み込み中…</div>
        ) : chartData.length === 0 ? (
          <div className="h-64 flex items-center justify-center text-gray-400 text-sm">データなし</div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartData} margin={{ top: 8, right: 48, left: 8, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: "#9ca3af" }}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 10, fill: "#9ca3af" }}
                tickLine={false}
                tickFormatter={v => formatYen(v)}
                width={52}
                domain={[0, yAxisMax]}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 10, fill: "#60a5fa" }}
                tickLine={false}
                tickFormatter={v => `${(v * 100).toFixed(0)}%`}
                width={44}
                domain={[0, "auto"]}
              />
              <Tooltip
                content={props => (
                  <CustomTooltip
                    active={props.active}
                    payload={(props.payload as unknown) as Array<{ name: string; value: number; color: string }>}
                    label={String(props.label ?? "")}
                    cumMode={cumMode}
                  />
                )}
              />
              <Legend
                wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
                iconSize={10}
              />
              <ReferenceLine yAxisId="right" y={1} stroke="#94a3b8" strokeDasharray="4 2" strokeWidth={1} />
              <Bar yAxisId="left" dataKey="投資額" fill="#d1d5db" radius={[2, 2, 0, 0]} maxBarSize={28} />
              <Bar yAxisId="left" dataKey="回収額" fill="#34d399" radius={[2, 2, 0, 0]} maxBarSize={28}>
                {chartData.map(d => (
                  <Cell key={d.date} fill={d._belowHundred ? "#ef4444" : "#34d399"} />
                ))}
              </Bar>
              <Line
                yAxisId="right"
                type="monotone"
                dataKey={roiLineKey}
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* 期間サマリー */}
      {data && (
        <SummaryCard
          label={`${rankLabel} ・ 選択期間（${from} 〜 ${to}）`}
          {...data.period_summary}
        />
      )}

      {/* 当月・当年サマリー */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {monthSummary && monthSummary.total_bet > 0 && (
          <SummaryCard label={`${rankLabel} ・ 当月累積`} {...monthSummary} />
        )}
        {yearSummary && yearSummary.total_bet > 0 && (
          <SummaryCard label={`${rankLabel} ・ 当年累積`} {...yearSummary} />
        )}
      </div>
    </div>
  );
}
