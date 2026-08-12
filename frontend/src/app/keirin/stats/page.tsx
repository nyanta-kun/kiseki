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
import { fetchKeirinStats, type KeirinStatItem, type KeirinStatsResponse, fetchKeirinSummary} from "@/lib/api";
import { fetchNetkeirinSales, type NetkeirinSalesResponse } from "@/lib/api";
import { fetchKeirinSalesAnalysis, type KeirinSalesAnalysisResponse } from "@/lib/api";
import AnalysisTab from "./AnalysisTab";
import { formatYen } from "./format";

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

// formatYen は ./format から import している（分析タブと共用・負数の桁圧縮バグ修正済み）。

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

  // ⚠️ 各セルに min-w-0 が要る。grid の子は既定で min-width:auto のため、
  //    中身が縮まずカードの外へはみ出す（2026-08-11 に損益/ROI で実際に発生）。
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-3 sm:p-4">
      <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2 break-words">{label}</p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3">
        <div className="min-w-0">
          <p className="text-xs text-gray-400 dark:text-gray-500">推奨/的中</p>
          {/* 折り返しを許す。nowrap にすると桁が増えたときに隣の列へ重なる。 */}
          <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">
            {n_picks}<span className="text-xs font-normal text-gray-400 ml-0.5">件</span>
            <span className="text-gray-400 mx-0.5">/</span>
            {n_hits}<span className="text-xs font-normal text-gray-400 ml-0.5">({hitRate})</span>
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-xs text-gray-400 dark:text-gray-500">投資</p>
          <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">{formatYen(total_bet)}</p>
        </div>
        <div className="min-w-0">
          <p className="text-xs text-gray-400 dark:text-gray-500">回収</p>
          <p className={`text-sm font-bold tabular-nums ${total_payout >= total_bet ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"}`}>
            {formatYen(total_payout)}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-xs text-gray-400 dark:text-gray-500">損益 / ROI</p>
          {/* 損益と ROI は改行を許す。1行に押し込むと桁数の多い日にはみ出す。 */}
          <p className={`text-sm font-bold tabular-nums ${roiColor}`}>
            {profit >= 0 ? "+" : ""}{formatYen(profit)}
            <span className="text-xs ml-1 font-normal">({formatROI(roi)})</span>
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 期間プリセット
// ---------------------------------------------------------------------------

type Preset = "7d" | "30d" | "90d" | "thisMonth" | "thisYear" | "all" | "custom";

// honest全期間データの起点（S1/S7のquarters walk-forward再構築が対象とする期間と統一）
const ALL_TIME_FROM = "2024-01-01";

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
    case "all":    return { from: ALL_TIME_FROM, to };
    default:       return { from: addDays(to, -29), to };
  }
}

const PRESETS: { key: Preset; label: string }[] = [
  { key: "7d", label: "7日" },
  { key: "30d", label: "30日" },
  { key: "90d", label: "90日" },
  { key: "thisMonth", label: "当月" },
  { key: "thisYear", label: "当年" },
  { key: "all", label: "全期間" },
  { key: "custom", label: "指定" },
];

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

type Granularity = "daily" | "monthly";
type CumMode = "period" | "month" | "year";
// 2026-08-01〜: S1（2026-07-31全廃）・9SS（gate_label分岐廃止に伴い消滅）は対象外
// （backend/src/api/keirin_router.py の get_stats/_RANK_COND_MAP と揃える）。
// 2026-08-02〜: 7SS（波乱軸選出・穴レース検知）も全廃したため対象外。
// 2026-08-05〜: 同じ "7SS" ラベルで別戦略（entropy不合格×軸2車が同一ライン）を
// 新設したため復活（keirin PR#10。旧7SSとは無関係・picks_historyの旧行は0件）。
type RankFilter =
  | "all" | "7SS" | "7S" | "7A" | "7B" | "9S" | "9A" | "7H1" | "7H2" | "9H1" | "7C"
  | "7H3";

// 並び順は 7SS/7S/7A/7B/9S/9A に統一。keirin ページの RANK_ORDER と同一基準。
const RANK_FILTERS: { key: RankFilter; label: string }[] = [
  { key: "all", label: "全体" },
  // 7SS（最上位・entropy不合格×同一ライン・2026-08-05導入）
  { key: "7SS", label: "7SS" },
  { key: "7S", label: "7S" },
  // 7A/9A（境界ランク・2026-07-27導入）。"全体"にも含まれる（/summaryと同じ方針）。
  { key: "7A", label: "7A" },
  // 7B（◎◯一致×順序/相手不一致・相手絞り3点・2026-08-03導入）
  { key: "7B", label: "7B" },
  { key: "9S", label: "9S" },
  { key: "9A", label: "9A" },
  // 7H1（穴推奨・本命バスト型・三連単F+三連複BOXの2券種・2026-08-06導入）。
  // 的中率重視のS/A/Bとは系統が違うため末尾に置く。
  { key: "7H1", label: "7H1" },
  // 9H1（穴推奨・9車高配当・三連単フォーメーション6点・2026-08-08導入）。
  // 7H1 と同じ穴推奨系だが車数で母集団が排他なので隣に置く。
  { key: "9H1", label: "9H1" },
  { key: "7C", label: "7C" },
];

// 成績（予想の投資・回収）と売上（netkeirinの販売実績）は性質もフィルタ条件も
// 異なるため、同一ページ内でタブ分割する（2026-08-03・ユーザー指摘）。
// 期間フィルタは両タブ共通、ランク/粒度/累積ROIは成績タブ専用。
// 2026-08-11: 「分析」タブを追加（netkeirin のレース別データを使った売上×的中の相関）。
type StatsTab = "performance" | "sales" | "analysis";

const STATS_TABS: [StatsTab, string][] = [
  ["performance", "成績"],
  ["sales", "売上"],
  ["analysis", "分析"],
];

export default function KeirinStatsPage() {
  const [tab, setTab] = useState<StatsTab>("performance");
  const [preset, setPreset] = useState<Preset>("30d");
  const [granularity, setGranularity] = useState<Granularity>("daily");
  const [cumMode, setCumMode] = useState<CumMode>("month");
  const [rankFilters, setRankFilters] = useState<RankFilter[]>(["all"]);
  // 集計対象。false = ランクのゲートを通った推奨だけ（＝ランクの実力）、
  // true = 手動入稿・看板の穴埋めも含めた実際の収支。
  // 🔴 既定は false。ROI の意味が変わるので、切り替えたことが分かる状態でだけ含める。
  const [includeManual, setIncludeManual] = useState(false);
  const [from, setFrom] = useState(() => calcRange("30d").from);
  const [to, setTo] = useState(() => calcRange("30d").to);
  // 入稿対象OFFのランクは絞り込みチップからも外す（2026-08-12・ユーザー要望）。
  // 一覧は /keirin/summary が返す（判定は netkeirin_settings.enabled の1点）。
  // ⚠️ null のあいだ・古いAPIでは絞らない（fail-open）。
  const [visibleRanks, setVisibleRanks] = useState<string[] | null>(null);
  const [data, setData] = useState<KeirinStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // netkeirin（ウマい車券）二軸探偵の売上推移。ランク別ではないため
  // rankFilters/granularityとは独立に、選択期間（from/to）だけで再取得する。
  const [salesData, setSalesData] = useState<NetkeirinSalesResponse | null>(null);
  const [salesLoading, setSalesLoading] = useState(false);
  // 売上タブの粒度。成績タブの granularity とは別 state にする
  //（成績側はサーバー集計で API 再取得を伴うのに対し、売上はフロントで畳むだけ。
  //  共有すると売上の切り替えのたびに成績APIを無駄に再取得してしまう）。
  const [salesGranularity, setSalesGranularity] = useState<Granularity>("daily");

  // 分析タブ。レース別データを含むため応答が重く、開いたときだけ取りに行く。
  const [analysis, setAnalysis] = useState<KeirinSalesAnalysisResponse | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);

  const load = useCallback(async (
    f: string, t: string, g: Granularity, ranks: RankFilter[], manual: boolean,
  ) => {
    setLoading(true);
    try {
      const res = await fetchKeirinStats(f, t, g, ranks, manual);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSales = useCallback(async (f: string, t: string) => {
    setSalesLoading(true);
    try {
      const res = await fetchNetkeirinSales(f, t);
      setSalesData(res);
    } catch {
      setSalesData(null);
    } finally {
      setSalesLoading(false);
    }
  }, []);

  const loadAnalysis = useCallback(async (f: string, t: string) => {
    setAnalysisLoading(true);
    try {
      setAnalysis(await fetchKeirinSalesAnalysis(f, t));
    } catch {
      setAnalysis(null);
    } finally {
      setAnalysisLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchKeirinSummary()
      .then(r => setVisibleRanks(r.visible_ranks ?? null))
      .catch(() => setVisibleRanks(null));
  }, []);

  useEffect(() => {
    void load(from, to, granularity, rankFilters, includeManual);
  }, [from, to, granularity, rankFilters, includeManual, load]);

  useEffect(() => {
    void loadSales(from, to);
  }, [from, to, loadSales]);

  useEffect(() => {
    if (tab !== "analysis") return;
    void loadAnalysis(from, to);
  }, [tab, from, to, loadAnalysis]);

  // ランクフィルタのトグル（複数選択可）。「全体」は排他、それ以外は積み上げ選択。
  // 選択がゼロになる場合は「全体」に自動復帰する。
  function toggleRank(key: RankFilter) {
    setRankFilters(prev => {
      if (key === "all") return ["all"];
      const withoutAll = prev.filter(k => k !== "all");
      if (withoutAll.includes(key)) {
        const next = withoutAll.filter(k => k !== key);
        return next.length > 0 ? next : ["all"];
      }
      return [...withoutAll, key];
    });
  }

  function applyPreset(p: Preset) {
    setPreset(p);
    // 全期間は日次だと900日超のバーになり読めないため月次表示をデフォルトにする
    if (p === "all") setGranularity("monthly");
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

  // netkeirin売上グラフ用データ変換（販売pt棒 + 回収率(%)線）。
  // 月別は API を叩き直さずフロントで日別を畳む（売上は1日1行しか無く軽量なため）。
  // ⚠️ 回収率は月内の平均ではなく **合計払戻 / 合計賭け金** で再計算すること
  //    （日別の率を平均すると賭け金の小さい日が過大に効いて実勢とズレる）。
  const salesRows = (() => {
    const items = salesData?.items ?? [];
    if (salesGranularity === "daily") {
      return items.map(i => ({
        key: i.date,
        soldPoints: i.sold_points ?? 0,
        stake: i.stake_amount,
        payout: i.payout_amount,
      }));
    }
    const byMonth = new Map<string, { soldPoints: number; stake: number; payout: number }>();
    for (const i of items) {
      const m = i.date.slice(0, 7);
      const cur = byMonth.get(m) ?? { soldPoints: 0, stake: 0, payout: 0 };
      cur.soldPoints += i.sold_points ?? 0;
      cur.stake += i.stake_amount;
      cur.payout += i.payout_amount;
      byMonth.set(m, cur);
    }
    return [...byMonth.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, v]) => ({ key, ...v }));
  })();
  const salesChartData = salesRows.map(r => ({
    date: r.key,
    販売pt: r.soldPoints,
    回収率: r.stake > 0 ? Math.round((r.payout / r.stake) * 1000) / 10 : null,
  }));
  const maxSoldPoints = Math.max(...salesRows.map(r => r.soldPoints), 1);
  const salesYAxisMax = Math.ceil(maxSoldPoints / 100) * 100 + 100;

  const rankLabel = rankFilters.includes("all")
    ? "全体"
    : rankFilters.map(k => RANK_FILTERS.find(r => r.key === k)?.label ?? k).join("＋");
  const chartTitle = rankFilters.includes("all") ? "全体の投資・回収推移" : `${rankLabel} の投資・回収推移`;

  return (
    <div className="w-full sm:max-w-4xl sm:mx-auto px-3 sm:px-4 py-4 pb-20 space-y-4">
      {/* ヘッダー */}
      <div className="flex items-center gap-2">
        <Link href="/keirin" className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors">
          <ArrowLeft size={18} />
        </Link>
        <BarChart2 size={20} className="text-blue-500" />
        <h1 className="text-lg font-extrabold tracking-widest text-gray-900 dark:text-white">成績／売上</h1>
      </div>

      {/* タブ切り替え（成績 / 売上） */}
      <div className="flex items-center gap-1 border-b border-gray-200 dark:border-gray-700">
        {STATS_TABS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            aria-current={tab === key ? "page" : undefined}
            className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
              tab === key
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            }`}
          >
            {label}
          </button>
        ))}
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

        {/* ランク（成績タブ専用。売上はランク別集計が存在しない） */}
        {tab === "performance" && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-gray-400 dark:text-gray-500 mr-1">ランク</span>
          {RANK_FILTERS.filter(r => r.key === "all" || !visibleRanks || visibleRanks.includes(r.key)).map(r => (
            <button
              key={r.key}
              onClick={() => toggleRank(r.key)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                rankFilters.includes(r.key)
                  ? "bg-blue-500 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
        )}

        {/* 粒度・累積モード（成績タブ専用） */}
        {tab === "performance" && (
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
            {/* 🔴 ランクのゲートを通った推奨だけか、実際に賭けた全部か。
                ROI の意味が変わるので、どちらを見ているかを常に画面に出す。 */}
            <span className="text-xs text-gray-400 dark:text-gray-500">集計対象</span>
            {([[false, "ゲート通過のみ"], [true, "全入稿"]] as [boolean, string][]).map(([key, label]) => (
              <button
                key={label}
                onClick={() => setIncludeManual(key)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  includeManual === key
                    ? "bg-gray-700 dark:bg-gray-200 text-white dark:text-gray-900"
                    : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
                }`}
              >
                {label}
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
        )}
      </div>

      {/* ── 成績タブ ───────────────────────────────────────── */}
      {tab === "performance" && (<>
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

      {/* 集計対象の注記。「全入稿」は買い目の記録がある分しか足せない。
          黙って落とすと完全な数字に見えてしまうので、除外件数を必ず出す。 */}
      {includeManual && (
        <p className={`text-[11px] leading-relaxed border-l-2 pl-2 ${
          (data?.manual_missing_bet_detail ?? 0) > 0
            ? "border-amber-400 text-amber-700 dark:text-amber-400"
            : "border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400"
        }`}>
          手動入稿・看板の穴埋め（ランクのゲートを通っていない入稿）を含めた数字です。
          ランク自体の実力を見るときは「ゲート通過のみ」に戻してください。
          {(data?.manual_missing_bet_detail ?? 0) > 0 && (
            <>
              <br />
              ⚠️ うち <strong>{data!.manual_missing_bet_detail!}件</strong> は買い目が記録されておらず
              集計から除外しています（買い目の保存開始は 2026-08-07。それ以前は入稿した事実しか
              残っておらず、投資額を復元できません）。
            </>
          )}
        </p>
      )}

      {/* 期間サマリー */}
      {data && (
        <SummaryCard
          label={`${rankLabel}${includeManual ? "＋手動入稿" : ""} ・ 選択期間（${from} 〜 ${to}）`}
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
      </>)}

      {/* ── 売上タブ ───────────────────────────────────────── */}
      {tab === "sales" && (
      <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-3 sm:p-4">
        <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
          <p className="text-xs font-semibold text-gray-500 dark:text-gray-400">
            netkeirin 売上推移（販売pt・回収率）
          </p>
          {/* 粒度切り替え（売上タブ専用・フロントで日別を月別に畳む） */}
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-400 dark:text-gray-500">粒度</span>
            {(["daily", "monthly"] as const).map(g => (
              <button
                key={g}
                onClick={() => setSalesGranularity(g)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                  salesGranularity === g
                    ? "bg-gray-700 dark:bg-gray-200 text-white dark:text-gray-900"
                    : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
                }`}
              >
                {g === "daily" ? "日別" : "月別"}
              </button>
            ))}
          </div>
        </div>
        {salesLoading ? (
          <div className="h-64 flex items-center justify-center text-gray-400 text-sm animate-pulse">読み込み中…</div>
        ) : salesChartData.length === 0 ? (
          <div className="h-64 flex items-center justify-center text-gray-400 text-sm">
            データなし（通常集計はレース日の翌日反映・売上は速報値）
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={salesChartData} margin={{ top: 8, right: 48, left: 8, bottom: 4 }}>
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
                width={44}
                domain={[0, salesYAxisMax]}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 10, fill: "#60a5fa" }}
                tickLine={false}
                tickFormatter={v => `${v}%`}
                width={44}
                domain={[0, "auto"]}
              />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconSize={10} />
              <ReferenceLine yAxisId="right" y={100} stroke="#94a3b8" strokeDasharray="4 2" strokeWidth={1} />
              <Bar yAxisId="left" dataKey="販売pt" fill="#a5b4fc" radius={[2, 2, 0, 0]} maxBarSize={28} />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="回収率"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
        {salesData && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
            <div>
              <p className="text-xs text-gray-400 dark:text-gray-500">販売個数</p>
              <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">
                {salesData.period_summary.total_n_sold.toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-400 dark:text-gray-500">販売pt合計</p>
              <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">
                {salesData.period_summary.total_sold_points.toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-400 dark:text-gray-500">投資/回収</p>
              <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">
                {formatYen(salesData.period_summary.total_stake)} / {formatYen(salesData.period_summary.total_payout)}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-400 dark:text-gray-500">回収率</p>
              <p className={`text-sm font-bold tabular-nums ${
                (salesData.period_summary.recovery_rate_pct ?? 0) >= 100 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"
              }`}>
                {salesData.period_summary.recovery_rate_pct != null ? `${salesData.period_summary.recovery_rate_pct}%` : "—"}
              </p>
            </div>
          </div>
        )}
        {/* 売上金額（= 販売有償pt × 30%）。総販売ptではなく**有償pt**が対象。
            料率はバックエンド NETKEIRIN_REVENUE_RATE が正で、APIが算出済みの
            値をそのまま表示する（フロントで再計算しない）。 */}
        {salesData && (
          <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 flex items-end justify-between gap-3 flex-wrap">
            <div>
              <p className="text-xs text-gray-400 dark:text-gray-500">
                販売有償pt（売上対象）
              </p>
              <p className="text-sm font-bold text-gray-800 dark:text-gray-100 tabular-nums">
                {salesData.period_summary.total_sold_paid_points.toLocaleString()}
                <span className="text-xs font-normal text-gray-400 ml-1">
                  × {(salesData.period_summary.revenue_rate * 100).toFixed(0)}%
                </span>
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs text-gray-400 dark:text-gray-500">売上金額</p>
              <p className="text-xl font-extrabold text-emerald-600 dark:text-emerald-400 tabular-nums">
                {`¥${salesData.period_summary.total_revenue_yen.toLocaleString()}`}
              </p>
            </div>
          </div>
        )}
      </div>
      )}

      {/* ── 分析タブ ───────────────────────────────────────── */}
      {tab === "analysis" && <AnalysisTab data={analysis} loading={analysisLoading} />}
    </div>
  );
}
