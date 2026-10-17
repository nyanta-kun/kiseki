"use client";

/**
 * 成績ページの「予想家比較」タブ（2026-10-13）。
 *
 * netkeirin の全予想家の成績（予想数・的中・回収率）を期間で集計し、
 * 自分（二軸探偵）が全体の何位かを見る。計算の正本は backend
 * `services/keirin_rivals_analysis.py`。
 *
 * ⚠️ **予想数が「最低予想数」未満の予想家には順位を付けない。**
 *    1レースだけ出して当てた予想家が回収率 2,000% で常に1位になるため。
 * ⚠️ **的中率は2種類ある。** ガミ含む（払戻>0・一覧の「的中」アイコン）と
 *    ガミ除く（払戻>購入・netkeirin のプロフィールの的中率）。両方出す。
 * 🔴 **推移グラフの既定の比較相手は「表示期間の前の1年で継続して回収率が高い人」。**
 *    表示期間の上位で選ぶと、その期間に運が良かった人が並び線が実力以上に高く見える
 *    （選び方の正本は backend `steady_compare_candidates`）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchKeirinRivals,
  type KeirinRivalGranularity,
  type KeirinRivalMetric,
  type KeirinRivalRow,
  type KeirinRivalsResponse,
} from "@/lib/api";
import { CHART_GRID, ChartTooltip, chartAxisTick, chartLegendStyle } from "@/lib/chart-theme";
import { formatPct, formatYen } from "./format";

const METRICS: [KeirinRivalMetric, string][] = [
  ["roi", "回収率"],
  ["net_hit_rate", "的中率(ガミ除く)"],
  ["hit_rate", "的中率(ガミ含む)"],
  ["profit", "収支"],
  ["n_goods", "予想数"],
];
const MIN_GOODS_OPTIONS = [10, 20, 50, 100];
const GRANULARITIES: [KeirinRivalGranularity, string][] = [
  ["day", "日"],
  ["week", "週"],
  ["month", "月"],
];

const SELF_COLOR = "#2563eb";
const FIELD_COLOR = "#9ca3af";
const COMPARE_COLORS = ["#f97316", "#10b981", "#a855f7", "#ef4444", "#eab308", "#06b6d4", "#ec4899"];

function roiClass(roi: number | null): string {
  if (roi == null) return "text-gray-400";
  return roi >= 1 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500";
}

function bucketLabel(b: string, g: KeirinRivalGranularity): string {
  const [, m, d] = b.split("-");
  return g === "month" ? `${b.slice(0, 4)}/${m}` : `${Number(m)}/${Number(d)}${g === "week" ? "週" : ""}`;
}

function Chip({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full text-xs border whitespace-nowrap ${
        active
          ? "bg-blue-600 border-blue-600 text-white"
          : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300"
      }`}
    >
      {children}
    </button>
  );
}

function RankTile({ label, rank, n, value }: {
  label: string; rank: number | null; n: number; value: string;
}) {
  const pct = rank != null && n > 0 ? Math.round((rank / n) * 100) : null;
  return (
    <div className="min-w-0 rounded-lg bg-gray-50 dark:bg-gray-800/60 p-2">
      <p className="text-[11px] text-gray-500 dark:text-gray-400">{label}</p>
      <p className="text-base font-bold tabular-nums text-gray-800 dark:text-gray-100">
        {rank != null ? `${rank}位` : "—"}
        <span className="text-xs font-normal text-gray-400 ml-1">/ {n}人</span>
      </p>
      <p className="text-xs tabular-nums text-gray-500">
        {value}
        {pct != null && <span className="ml-1">（上位{pct}%）</span>}
      </p>
    </div>
  );
}

export default function RivalsTab({ from, to }: { from: string; to: string }) {
  const [metric, setMetric] = useState<KeirinRivalMetric>("roi");
  const [minGoods, setMinGoods] = useState(20);
  const [granularity, setGranularity] = useState<KeirinRivalGranularity>("week");
  const [compare, setCompare] = useState<number[] | null>(null); // null = 継続上位（既定）
  const [showUnranked, setShowUnranked] = useState(false);
  const [data, setData] = useState<KeirinRivalsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchKeirinRivals({
        fromDate: from, toDate: to, metric, minGoods, granularity,
        compare: compare ?? undefined,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [from, to, metric, minGoods, granularity, compare]);

  useEffect(() => { void load(); }, [load]);

  const compareIds = useMemo(() => (data?.compare ?? []).map(c => c.yosoka_id), [data]);

  function toggleCompare(id: number) {
    const base = compare ?? compareIds;
    setCompare(base.includes(id) ? base.filter(x => x !== id) : [...base, id]);
  }

  const chartData = useMemo(() => (data?.series ?? []).map(p => {
    const row: Record<string, number | string | null> = {
      label: bucketLabel(p.bucket, data!.granularity),
      self: p.self_cum_roi != null ? p.self_cum_roi * 100 : null,
      field: p.field_cum_roi != null ? p.field_cum_roi * 100 : null,
      rank: p.self?.rank ?? null,
      n_ranked: p.n_ranked,
      self_roi: p.self?.roi != null ? p.self.roi * 100 : null,
      median: p.median_roi != null ? p.median_roi * 100 : null,
    };
    for (const [id, v] of Object.entries(p.compare_cum_roi)) row[`c${id}`] = v != null ? v * 100 : null;
    return row;
  }), [data]);

  const nameOf = useMemo(() => {
    const m = new Map<number, string>();
    for (const r of data?.ranking ?? []) m.set(r.yosoka_id, r.yosoka_name ?? `#${r.yosoka_id}`);
    return m;
  }, [data]);

  const rows: KeirinRivalRow[] = useMemo(
    () => (data?.ranking ?? []).filter(r => showUnranked || r.ranked || r.is_self),
    [data, showUnranked],
  );

  const me = data?.self ?? null;
  const n = data?.n_ranked;

  return (
    <div className="space-y-4">
      {/* ── 操作 ───────────────────────── */}
      <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 p-3 space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-gray-500 mr-1">順位の指標</span>
          {METRICS.map(([k, label]) => (
            <Chip key={k} active={metric === k} onClick={() => setMetric(k)}>{label}</Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-gray-500 mr-1">最低予想数</span>
          {MIN_GOODS_OPTIONS.map(v => (
            <Chip key={v} active={minGoods === v} onClick={() => setMinGoods(v)}>{v}</Chip>
          ))}
          <span className="text-xs text-gray-500 ml-3 mr-1">推移</span>
          {GRANULARITIES.map(([k, label]) => (
            <Chip key={k} active={granularity === k} onClick={() => setGranularity(k)}>{label}</Chip>
          ))}
        </div>
        <p className="text-[11px] text-gray-400">
          予想数が最低予想数に届かない予想家は順位の対象外（1レースだけ当てた予想家が常に1位になるのを防ぐ）。
          {data && data.n_unsettled > 0 && ` 未確定 ${data.n_unsettled}件は集計に含めていません。`}
          {data?.last_collected_at && ` 最終取り込み ${data.last_collected_at.slice(0, 16).replace("T", " ")}`}
        </p>
      </div>

      {error && <p className="text-sm text-red-500">読み込みに失敗しました: {error}</p>}
      {loading && !data && <p className="text-sm text-gray-400">読み込み中…</p>}
      {data && data.ranking.length === 0 && !loading && (
        <p className="text-sm text-gray-400">この期間のデータがありません（収集前の期間かもしれません）。</p>
      )}

      {data && data.ranking.length > 0 && (<>
        {/* ── 自分の位置 ───────────────────────── */}
        <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 p-3">
          <p className="text-xs font-semibold text-gray-500 mb-2">
            自分（{me?.yosoka_name ?? "二軸探偵"}）の位置
            {me && !me.ranked && (
              <span className="ml-2 text-amber-600">予想数 {me.n_goods} が最低予想数 {data.min_goods} 未満のため順位なし</span>
            )}
          </p>
          {me ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <RankTile label="回収率" rank={me.rank_roi} n={n?.roi ?? 0} value={formatPct(me.roi)} />
              <RankTile label="的中率(ガミ除く)" rank={me.rank_net_hit_rate} n={n?.net_hit_rate ?? 0}
                value={formatPct(me.net_hit_rate)} />
              <RankTile label="収支" rank={me.rank_profit} n={n?.profit ?? 0} value={formatYen(me.profit)} />
              <RankTile label="予想数" rank={me.rank_n_goods} n={n?.n_goods ?? 0} value={`${me.n_goods}件`} />
            </div>
          ) : (
            <p className="text-sm text-gray-400">この期間に自分の予想はありません。</p>
          )}
          <p className="mt-2 text-xs text-gray-500">
            全体: 予想家 {data.field.n_yosoka}人（順位対象 {data.field.n_ranked}人）・
            予想 {data.field.n_goods.toLocaleString()}件・
            回収率 <span className={roiClass(data.field.roi)}>{formatPct(data.field.roi)}</span>・
            回収率の中央値 {formatPct(data.field.median_roi)}・
            的中率(ガミ除く)の中央値 {formatPct(data.field.median_net_hit_rate)}
          </p>
        </div>

        {/* ── 累積回収率の推移 ───────────────────────── */}
        <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 p-3">
          <div className="flex items-center justify-between gap-2 mb-1">
            <p className="text-xs font-semibold text-gray-500">累積回収率の推移</p>
            {compare != null && (
              <Chip active={false} onClick={() => setCompare(null)}>既定の比較相手に戻す</Chip>
            )}
          </div>
          <p className="text-[11px] text-gray-400 mb-2">
            {data.compare_basis === "steady" && (
              <>
                比較相手は<b>継続して回収率が高い予想家</b>：表示期間の前の1年
                （{data.steady_window.from_date}〜{data.steady_window.to_date}）で予想{data.steady_window.min_goods}件以上・
                {data.steady_window.min_quarters}四半期以上にわたり、その過半で全体を上回った人を、その1年の回収率順に（表示期間の成績では選んでいません。四半期は窓の端の短い分も1つと数えます）。
              </>
            )}
            {data.compare_basis === "period_top" && (
              <span className="text-amber-600">
                継続上位の該当者がいない（またはこの期間に予想を出していない）ため、この期間の指標上位を表示しています（運の良かった人が並びやすい）。
              </span>
            )}
            {data.compare_basis === "manual" && <>比較相手は手動で選択中。</>}
            {" "}表の行をタップで追加・削除。
          </p>
          {data.compare_basis === "steady" && data.compare.length > 0 && (
            <ul className="mb-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
              {data.compare.map((c, i) => (
                <li key={c.yosoka_id} className="flex items-center gap-1 min-w-0">
                  <span aria-hidden className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                    style={{ background: COMPARE_COLORS[i % COMPARE_COLORS.length] }} />
                  {c.yosoka_name ?? `#${c.yosoka_id}`}
                  <span className="tabular-nums text-gray-400">
                    前の1年 {formatPct(c.lookback_roi ?? null)}・{c.lookback_n_goods?.toLocaleString()}件・
                    {c.quarters_above}/{c.quarters_counted}四半期で全体超
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 5, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                <XAxis dataKey="label" tick={chartAxisTick()} minTickGap={12} />
                <YAxis tick={chartAxisTick()} unit="%" domain={["auto", "auto"]} />
                <ReferenceLine y={100} stroke={FIELD_COLOR} strokeDasharray="4 4" />
                <Tooltip content={<ChartTooltip
                  formatter={(v, name) => [typeof v === "number" ? `${v.toFixed(1)}%` : v, name]} />} />
                <Legend wrapperStyle={chartLegendStyle()} />
                <Line type="monotone" dataKey="self" name="自分" stroke={SELF_COLOR}
                  strokeWidth={3} dot={false} connectNulls />
                <Line type="monotone" dataKey="field" name="全体" stroke={FIELD_COLOR}
                  strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
                {compareIds.map((id, i) => (
                  <Line key={id} type="monotone" dataKey={`c${id}`} name={nameOf.get(id) ?? `#${id}`}
                    stroke={COMPARE_COLORS[i % COMPARE_COLORS.length]} strokeWidth={1.5}
                    dot={false} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* ── 区間ごとの順位 ───────────────────────── */}
        <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 p-3">
          <p className="text-xs font-semibold text-gray-500 mb-1">
            {GRANULARITIES.find(g => g[0] === data.granularity)?.[1]}ごとの順位（{METRICS.find(m => m[0] === data.metric)?.[1]}）
          </p>
          <p className="text-[11px] text-gray-400 mb-2">
            区間内の予想数が {data.min_goods_per_bucket} 件以上の予想家の中での順位。上ほど良い。
          </p>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 5, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                <XAxis dataKey="label" tick={chartAxisTick()} minTickGap={12} />
                <YAxis tick={chartAxisTick()} reversed allowDecimals={false} domain={[1, "auto"]} />
                <Tooltip content={<ChartTooltip
                  formatter={(v, name) => {
                    if (name === "順位") return [typeof v === "number" ? `${v}位` : "—", name];
                    if (name === "対象人数") return [`${v}人`, name];
                    return [typeof v === "number" ? `${v.toFixed(1)}%` : "—", name];
                  }} />} />
                <Legend wrapperStyle={chartLegendStyle()} />
                <Line type="monotone" dataKey="rank" name="順位" stroke={SELF_COLOR}
                  strokeWidth={2} connectNulls />
                <Line type="monotone" dataKey="n_ranked" name="対象人数" stroke={FIELD_COLOR}
                  strokeWidth={1} strokeDasharray="4 3" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* ── 順位表 ───────────────────────── */}
        <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-gray-500">
              順位表（{METRICS.find(m => m[0] === data.metric)?.[1]}順）
            </p>
            <label className="text-xs text-gray-500 flex items-center gap-1">
              <input type="checkbox" checked={showUnranked}
                onChange={e => setShowUnranked(e.target.checked)} />
              順位対象外も表示
            </label>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-100 dark:border-gray-700">
                  <th className="px-2 py-1 text-right">順位</th>
                  <th className="px-2 py-1 text-left">予想家</th>
                  <th className="px-2 py-1 text-right">レース</th>
                  <th className="px-2 py-1 text-right">予想数</th>
                  <th className="px-2 py-1 text-right">的中</th>
                  <th className="px-2 py-1 text-right whitespace-nowrap">的中率<br />ガミ除く</th>
                  <th className="px-2 py-1 text-right whitespace-nowrap">的中率<br />ガミ含む</th>
                  <th className="px-2 py-1 text-right">回収率</th>
                  <th className="px-2 py-1 text-right">収支</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const ci = compareIds.indexOf(r.yosoka_id);
                  return (
                    <tr
                      key={r.yosoka_id}
                      onClick={() => !r.is_self && toggleCompare(r.yosoka_id)}
                      className={`border-b border-gray-50 dark:border-gray-800 ${
                        r.is_self ? "bg-blue-50 dark:bg-blue-900/30 font-semibold" : "cursor-pointer"
                      } ${r.ranked ? "" : "text-gray-400"}`}
                    >
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.rank ?? "—"}</td>
                      <td className="px-2 py-1.5 min-w-[8rem]">
                        <span className="flex items-center gap-1.5">
                          {ci >= 0 && (
                            <span aria-hidden className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                              style={{ background: COMPARE_COLORS[ci % COMPARE_COLORS.length] }} />
                          )}
                          <a href={`https://keirin.netkeiba.com/yoso/profile/?id=${r.yosoka_id}`}
                            target="_blank" rel="noopener noreferrer"
                            onClick={e => e.stopPropagation()}
                            className="truncate hover:underline">
                            {r.yosoka_name ?? `#${r.yosoka_id}`}
                          </a>
                          {r.is_self && <span className="text-[10px] text-blue-600">自分</span>}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.n_races}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.n_goods}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap">
                        {r.n_net_hits}<span className="text-gray-400">/{r.n_hits}</span>
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{formatPct(r.net_hit_rate)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{formatPct(r.hit_rate)}</td>
                      <td className={`px-2 py-1.5 text-right tabular-nums ${roiClass(r.roi)}`}>
                        {formatPct(r.roi)}
                      </td>
                      <td className={`px-2 py-1.5 text-right tabular-nums whitespace-nowrap ${
                        r.profit >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"}`}>
                        {r.profit >= 0 ? "+" : ""}{formatYen(r.profit)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-gray-400">
            的中は「ガミ除く / ガミ含む」の件数。レースは予想したレース数、予想数は商品数（同じレースに2商品なら2）。
          </p>
        </div>
      </>)}
    </div>
  );
}
