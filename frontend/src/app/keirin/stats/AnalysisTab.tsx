"use client";

/**
 * 成績／売上ページの「分析」タブ（2026-08-11）。
 *
 * netkeirin「ウマい車券」の **レース別**データ（keirin.netkeirin_sales_race）と
 * 日別データを突き合わせて、「売れたレースで当てられているか」を見る。
 * データ取得は毎日10:30の `scripts/scrape_netkeirin_sales.sh`（前日分を UPSERT）。
 *
 * ⚠️ **「的中」には2種類ある。取り違えると別の数字になる。**
 *    - ガミ含む … 買い目が当たった。払戻＜賭け金でも的中。相関・タイムラインはこちら
 *      （買った人から見て「当たった」かどうかが売上に効く、という仮説を見る図なので）。
 *    - ガミ除く … 払戻＞賭け金。**netkeirin のプロフィールに出る的中率はこちら**。
 * ⚠️ **「売上」は販売*有償*pt**（sold_paid_points）。無償pt を含む総販売pt は
 *    収益にならない。API が両方返すので取り違えないこと。
 * ⚠️ API が返す率はすべて 0〜1 の小数。表示側で ×100 する（formatPct）。
 */

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { KeirinMeetingType, KeirinSalesAnalysisResponse } from "@/lib/api";
import { formatCoef, formatDelta, formatPct, formatYen } from "./format";

// ---------------------------------------------------------------------------
// 配色
// ---------------------------------------------------------------------------

// 的中/不的中。ページ全体（回収額バー等）と同じ緑・灰を使う。
const COLOR_HIT = "#34d399";
const COLOR_MISS = "#d1d5db";
const COLOR_GARAMI = "#fb923c";   // ガミ＝当たったが賭け金割れ。的中の内訳なので橙
const COLOR_LINE = "#ef4444";     // 折れ線（率）
const COLOR_SALES = "#a5b4fc";    // 売上pt。売上タブの棒と同色
const COLOR_COUNT = "#fbbf24";    // 予想レース数

// 開催時間帯。順序は朝→昼→夜→深夜で固定し、系列が減っても色は動かさない。
// モーニング=amber・ミッドナイト=indigo は keirin 一覧ページのカード色と揃えてある
// （同じ概念に別の色を当てると読み手の対応付けが壊れる）。
// 隣接ペアの CVD 分離を dataviz の validate_palette.js で確認済み（最悪 ΔE 11.7 protan）。
const MEETING_COLORS: Record<string, string> = {
  morning: "#f59e0b",
  day: "#14b8a6",
  nighter: "#a855f7",
  midnight: "#4338ca",
  // 発走時刻が取れなかった開催。カテゴリではなく「不明」なので意図的に無彩色。
  unknown: "#cbd5e1",
};
const MEETING_ORDER: (KeirinMeetingType | "unknown")[] = [
  "morning", "day", "nighter", "midnight", "unknown",
];
const MEETING_LABELS: Record<string, string> = {
  morning: "モーニング",
  day: "デイ",
  nighter: "ナイター",
  midnight: "ミッドナイト",
  unknown: "時間帯不明",
};

// ---------------------------------------------------------------------------
// 小物
// ---------------------------------------------------------------------------

function Card({ title, note, children }: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-3 sm:p-4">
      <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">{title}</p>
      {note && <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1 leading-relaxed">{note}</p>}
      <div className="mt-3">{children}</div>
    </div>
  );
}

/** 数値タイル。`min-w-0` + `break-words` で長い値でも枠から出さない。 */
function Stat({ label, value, sub, tone = "default" }: {
  label: string;
  value: string;
  sub?: string;
  tone?: "default" | "good" | "bad";
}) {
  const toneClass =
    tone === "good" ? "text-emerald-600 dark:text-emerald-400"
    : tone === "bad" ? "text-red-500"
    : "text-gray-800 dark:text-gray-100";
  return (
    <div className="min-w-0 bg-gray-50 dark:bg-gray-800/60 rounded-lg px-2.5 py-2">
      <p className="text-[11px] text-gray-500 dark:text-gray-400 truncate">{label}</p>
      <p className={`text-base font-bold tabular-nums break-words ${toneClass}`}>{value}</p>
      {sub && <p className="text-[11px] text-gray-400 dark:text-gray-500 break-words">{sub}</p>}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="h-40 flex items-center justify-center text-gray-400 text-sm">{label}</div>;
}

const AXIS_TICK = { fontSize: 10, fill: "#9ca3af" };

// ---------------------------------------------------------------------------
// 本体
// ---------------------------------------------------------------------------

export default function AnalysisTab({ data, loading }: {
  data: KeirinSalesAnalysisResponse | null;
  loading: boolean;
}) {
  // レース単位タイムライン。累積的中率はレース順（＝時系列）に積み上げる。
  const timeline = useMemo(() => {
    if (!data) return [];
    let hits = 0;
    return data.races.map((r, i) => {
      if (r.hit) hits += 1;
      return {
        idx: i,
        date: r.date,
        label: r.label ?? `${r.venue_name ?? r.venue_code} ${r.race_no}R`,
        売上pt: r.sold_paid_points,
        累積的中率: Math.round((hits / (i + 1)) * 1000) / 10,
        _hit: r.hit,
      };
    });
  }, [data]);

  // 日付が変わる最初のレースにだけ目盛りを置く（196本すべてに日付を書くと潰れる）。
  const timelineTicks = useMemo(() => {
    const seen = new Set<string>();
    const ticks: number[] = [];
    for (const t of timeline) {
      if (!seen.has(t.date)) { seen.add(t.date); ticks.push(t.idx); }
    }
    return ticks;
  }, [timeline]);

  const dailyChart = useMemo(() => (data?.daily ?? []).map(d => ({
    date: d.date.slice(5),
    予想レース数: d.n_predictions,
    売上pt: d.sold_paid_points,
    的中率: d.hit_rate_incl == null ? null : Math.round(d.hit_rate_incl * 1000) / 10,
    実質的中: d.n_hits_excl_garami,
    ガミ的中: d.n_garami,
    ガミ率: d.garami_rate == null ? null : Math.round(d.garami_rate * 1000) / 10,
  })), [data]);

  // リードタイム帯に実際に現れた時間帯だけを積む（0本の系列を凡例に出さない）。
  const leadtimeKeys = useMemo(() => {
    const present = new Set<string>();
    for (const b of data?.leadtime ?? []) {
      for (const k of Object.keys(b)) if (k !== "lead_hours") present.add(k);
    }
    return MEETING_ORDER.filter(k => present.has(k));
  }, [data]);

  const raceTable = useMemo(
    () => [...(data?.races ?? [])].sort((a, b) => b.sold_paid_points - a.sold_paid_points),
    [data],
  );

  if (loading) {
    return (
      <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-8 text-center text-sm text-gray-400 animate-pulse">
        読み込み中…
      </div>
    );
  }
  if (!data || data.summary.n_days === 0) {
    return (
      <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm p-8 text-center text-sm text-gray-400">
        データなし（netkeirin の集計はレース日の翌日10時台に反映されます）
      </div>
    );
  }

  const s = data.summary;
  const latest = s.latest;
  const corr = data.correlations;

  return (
    <div className="space-y-4">
      {/* ── 最新日サマリー ─────────────────────────────── */}
      {latest && (
        <Card
          title={`${latest.date} サマリー（前日比つき）`}
          note="netkeirin「予想家成績状況」の値。売上＝販売有償pt（無償ptは収益にならないため除く）。当日分は速報値で、翌日に確定値へ上書きされる。"
        >
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            <Stat
              label="売上（有償pt）"
              value={latest.sold_paid_points.toLocaleString()}
              sub={latest.delta ? `${formatDelta(latest.delta.sold_paid_points)} 前日比` : undefined}
            />
            <Stat
              label="販売個数"
              value={latest.n_sold.toLocaleString()}
              sub={latest.delta ? `${formatDelta(latest.delta.n_sold)} 前日比` : undefined}
            />
            <Stat
              label="予想レース数"
              value={latest.n_predictions.toLocaleString()}
              sub={latest.delta ? `${formatDelta(latest.delta.n_predictions)} 前日比` : undefined}
            />
            <Stat
              label="的中率（ガミ除く）"
              value={formatPct(latest.hit_rate_excl)}
              sub={`${latest.n_hits_excl_garami}/${latest.n_predictions}・サイト表示値`}
            />
            <Stat
              label="的中率（ガミ含む）"
              value={formatPct(latest.hit_rate_incl)}
              sub={`${latest.n_hits_incl_garami}/${latest.n_predictions}・当たった数`}
            />
            <Stat
              label="回収率"
              value={formatPct(latest.recovery_rate)}
              tone={(latest.recovery_rate ?? 0) >= 1 ? "good" : "bad"}
              sub={`${formatYen(latest.payout_amount)} / ${formatYen(latest.stake_amount)}`}
            />
          </div>
        </Card>
      )}

      {/* ── 期間合計 ───────────────────────────────── */}
      <Card title={`期間合計（${data.from_date} 〜 ${data.to_date}・${s.n_days}日 / ${s.n_races}レース）`}>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          <Stat label="売上（有償pt）" value={s.sold_paid_points.toLocaleString()}
                sub={`総販売pt ${s.sold_points.toLocaleString()}`} />
          <Stat label="売上金額" value={formatYen(Math.round(s.sold_paid_points * data.revenue_rate))}
                sub={`有償pt × ${(data.revenue_rate * 100).toFixed(0)}%`} tone="good" />
          <Stat label="販売個数" value={s.n_sold.toLocaleString()} />
          <Stat label="的中率（ガミ除く）" value={formatPct(s.hit_rate_excl)}
                sub={`${s.n_hits_excl_garami}/${s.n_predictions}`} />
          <Stat label="的中率（ガミ含む）" value={formatPct(s.hit_rate_incl)}
                sub={`${s.n_hits_incl_garami}/${s.n_predictions}`} />
          <Stat label="回収率" value={formatPct(s.recovery_rate)}
                tone={(s.recovery_rate ?? 0) >= 1 ? "good" : "bad"} />
        </div>
      </Card>

      {/* ── レース単位タイムライン ──────────────────────── */}
      <Card
        title="レース単位タイムライン ― 売上（棒）× 的中（色）× 累積的中率（線）"
        note="左から時系列。棒＝そのレースの売上(有償pt)、緑＝的中／灰＝不的中。折れ線＝そこまでの累積的中率（ガミ含む・件数ベース）。大きな棒（＝売れたレース）が灰色ばかりなら「売れた時に当てられていない」。"
      >
        {timeline.length === 0 ? <EmptyState label="レース別データなし" /> : (
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={timeline} margin={{ top: 8, right: 44, left: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis
                dataKey="idx"
                type="number"
                domain={["dataMin", "dataMax"]}
                ticks={timelineTicks}
                tickFormatter={(v: number) => timeline[v]?.date.slice(5) ?? ""}
                tick={AXIS_TICK}
                tickLine={false}
              />
              <YAxis yAxisId="left" tick={AXIS_TICK} tickLine={false} width={46}
                     tickFormatter={(v: number) => v.toLocaleString()} />
              <YAxis yAxisId="right" orientation="right" tick={{ ...AXIS_TICK, fill: COLOR_LINE }}
                     tickLine={false} width={40} domain={[0, 100]}
                     tickFormatter={(v: number) => `${v}%`} />
              <Tooltip
                formatter={(value, name) => [
                  name === "累積的中率" ? `${value}%` : Number(value).toLocaleString(),
                  name,
                ]}
                labelFormatter={(v) => timeline[Number(v)]?.label ?? ""}
                contentStyle={{ fontSize: 11 }}
              />
              {/* Recharts の Legend は使わない。棒の色は Cell 単位（的中/不的中）で
                  決まるため、系列としての色が無く凡例が黒く出てしまう。下の自前凡例で示す。 */}
              <Bar yAxisId="left" dataKey="売上pt" radius={[2, 2, 0, 0]} maxBarSize={10}>
                {timeline.map(t => (
                  <Cell key={t.idx} fill={t._hit ? COLOR_HIT : COLOR_MISS} />
                ))}
              </Bar>
              <Line yAxisId="right" type="monotone" dataKey="累積的中率" stroke={COLOR_LINE}
                    strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 flex items-center gap-x-3 gap-y-1 flex-wrap justify-center">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: COLOR_HIT }} />
            的中したレースの売上
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: COLOR_MISS }} />
            不的中レースの売上
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block w-4 h-0.5" style={{ background: COLOR_LINE }} />
            累積的中率
          </span>
        </p>
      </Card>

      {/* ── 相関分析 ───────────────────────────────── */}
      <Card
        title="日別：予想レース数 × 的中率"
        note="棒＝その日の予想レース数、線＝的中率（ガミ含む）。「数を撃つと当たらなくなる」のか「量と精度は両立できる」のかを見る。"
      >
        {dailyChart.length === 0 ? <EmptyState label="日別データなし" /> : (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={dailyChart} margin={{ top: 8, right: 44, left: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} tickLine={false} interval="preserveStartEnd" />
              <YAxis yAxisId="left" tick={AXIS_TICK} tickLine={false} width={34} />
              <YAxis yAxisId="right" orientation="right" tick={{ ...AXIS_TICK, fill: COLOR_LINE }}
                     tickLine={false} width={40} domain={[0, 100]}
                     tickFormatter={(v: number) => `${v}%`} />
              <Tooltip contentStyle={{ fontSize: 11 }}
                       formatter={(value, name) => [name === "的中率" ? `${value}%` : value, name]} />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconSize={10} />
              <Bar yAxisId="left" dataKey="予想レース数" fill={COLOR_COUNT} radius={[2, 2, 0, 0]} maxBarSize={22} />
              <Line yAxisId="right" type="monotone" dataKey="的中率" stroke={COLOR_LINE}
                    strokeWidth={2} dot={{ r: 2 }} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card
        title="日別：売上 × 的中率"
        note="棒＝その日の売上(有償pt)、線＝的中率（ガミ含む）。当てた翌日に売上が伸びるか、逆行しているかを見る。"
      >
        {dailyChart.length === 0 ? <EmptyState label="日別データなし" /> : (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={dailyChart} margin={{ top: 8, right: 44, left: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} tickLine={false} interval="preserveStartEnd" />
              <YAxis yAxisId="left" tick={AXIS_TICK} tickLine={false} width={46}
                     tickFormatter={(v: number) => v.toLocaleString()} />
              <YAxis yAxisId="right" orientation="right" tick={{ ...AXIS_TICK, fill: COLOR_LINE }}
                     tickLine={false} width={40} domain={[0, 100]}
                     tickFormatter={(v: number) => `${v}%`} />
              <Tooltip contentStyle={{ fontSize: 11 }}
                       formatter={(value, name) => [
                         name === "的中率" ? `${value}%` : Number(value).toLocaleString(), name]} />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconSize={10} />
              <Bar yAxisId="left" dataKey="売上pt" fill={COLOR_SALES} radius={[2, 2, 0, 0]} maxBarSize={22} />
              <Line yAxisId="right" type="monotone" dataKey="的中率" stroke={COLOR_LINE}
                    strokeWidth={2} dot={{ r: 2 }} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card
        title="相関係数"
        note="ピアソンの相関係数。±1に近いほど連動、0なら無関係。標本が5点未満、または片方が定数の場合は「—」（少数点の相関は必ず±1付近に出て誤読を招くため出さない）。"
      >
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          <Stat label="予想レース数 × 的中率" value={formatCoef(corr.n_races_x_hit_rate)} sub="日別" />
          <Stat label="予想レース数 × 販売個数" value={formatCoef(corr.n_races_x_n_sold)} sub="日別" />
          <Stat label="予想レース数 × 売上" value={formatCoef(corr.n_races_x_sales)} sub="日別" />
          <Stat label="的中率 × 売上" value={formatCoef(corr.hit_rate_x_sales)} sub="日別" />
          <Stat label="レース売上 × 的中" value={formatCoef(corr.race_sales_x_hit)} sub="レース別" />
          <Stat label="購入者数 × 的中" value={formatCoef(corr.race_buyers_x_hit)} sub="レース別" />
        </div>
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-3 leading-relaxed border-l-2 border-gray-300 dark:border-gray-600 pl-2">
          ⚠️ これは相関であって因果ではない。期間全体では「デビュー直後にトラフィックが多く、
          その後減衰した」という時系列の影響が支配的になりやすく、
          <span className="font-semibold">予想レース数を増やしたから売上が減った</span>とは読めない。
          個別の施策効果は下の「レース数増 → 販売数増 → 売上増」検証（最新日 vs 直近平均）で見ること。
        </p>
      </Card>

      {/* ── リンク検証 ─────────────────────────────── */}
      {data.link_check && (
        <Card
          title={`「レース数増 → 販売数増 → 売上増」検証（${data.link_check.date}）`}
          note={`最新日の実績を直近${data.link_check.recent_days}日平均（${data.link_check.baseline_from} 〜 ${data.link_check.baseline_to}）と比較する。`}
        >
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {([
              ["n_predictions", "予想レース数", ""],
              ["n_sold", "販売個数", "個"],
              ["sold_paid_points", "売上（有償pt）", "pt"],
            ] as const).map(([key, label, unit]) => {
              const m = data.link_check!.metrics[key];
              if (!m) return null;
              const up = m.latest > m.recent_avg;
              return (
                <Stat
                  key={key}
                  label={label}
                  value={`${m.latest.toLocaleString()}${unit}`}
                  tone={up ? "good" : "bad"}
                  sub={`${m.delta_ratio == null ? "—" : `${up ? "▲" : "▼"} 直近平均比 ${formatPct(Math.abs(m.delta_ratio), 0)}`}（平均 ${m.recent_avg.toLocaleString()}）`}
                />
              );
            })}
          </div>
          <p className={`text-[11px] mt-3 leading-relaxed border-l-2 pl-2 ${
            data.link_check.linked
              ? "border-emerald-400 text-emerald-700 dark:text-emerald-400"
              : "border-orange-400 text-orange-700 dark:text-orange-400"
          }`}>
            {data.link_check.linked
              ? "✔ リンク成立：予想レース数・販売個数・売上のいずれも直近平均を上回った。「レース数増→販売数増→売上増」の狙いが機能している。"
              : "リンク不成立：3指標のうち直近平均を下回ったものがある。量を増やしても販売・売上に繋がっていない日なので、注目レース（決勝・準決勝・特選）の的中に絞るべき局面。"}
          </p>
        </Card>
      )}

      {/* ── ガミ分析 ───────────────────────────────── */}
      <Card
        title="ガミ分析（的中したのに賭け金割れ）"
        note="「的中(ガミ含む)」と「的中(ガミ除く)」の差＝ガミ。棒＝日々の的中数の内訳（濃い緑＝実質プラスの的中／橙＝ガミ的中）、折れ線＝ガミ率（的中に占めるガミの割合）。"
      >
        {dailyChart.length === 0 ? <EmptyState label="日別データなし" /> : (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={dailyChart} margin={{ top: 8, right: 44, left: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} tickLine={false} interval="preserveStartEnd" />
              <YAxis yAxisId="left" tick={AXIS_TICK} tickLine={false} width={30} allowDecimals={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ ...AXIS_TICK, fill: COLOR_LINE }}
                     tickLine={false} width={40} domain={[0, 100]}
                     tickFormatter={(v: number) => `${v}%`} />
              <Tooltip contentStyle={{ fontSize: 11 }}
                       formatter={(value, name) => [name === "ガミ率" ? `${value}%` : value, name]} />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconSize={10} />
              <Bar yAxisId="left" dataKey="実質的中" stackId="hit" fill={COLOR_HIT} maxBarSize={22} />
              <Bar yAxisId="left" dataKey="ガミ的中" stackId="hit" fill={COLOR_GARAMI} radius={[2, 2, 0, 0]} maxBarSize={22} />
              <Line yAxisId="right" type="monotone" dataKey="ガミ率" stroke={COLOR_LINE}
                    strokeWidth={2} strokeDasharray="4 2" dot={{ r: 2 }} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        )}
        <div className="grid grid-cols-3 gap-2 mt-3">
          <Stat label="累計 的中（ガミ含む）" value={s.n_hits_incl_garami.toLocaleString()} />
          <Stat label="うち ガミ的中" value={s.n_garami.toLocaleString()} />
          <Stat label="ガミ率" value={formatPct(s.garami_rate, 0)}
                tone={(s.garami_rate ?? 0) >= 0.3 ? "bad" : "default"} />
        </div>
      </Card>

      {/* ── リードタイム ───────────────────────────── */}
      <Card
        title="販売リードタイム × 売上（開催時間帯別・積み上げ）"
        note="netkeirin の「平均販売時」＝締切の何時間前に買われたか。0＝締切直前、右にいくほど早い先行購入（時刻JSTではない）。棒はそのリードタイム帯の売上(有償pt)を、対象レースの開催時間帯で色分け・積み上げ。"
      >
        {(data.leadtime.length === 0) ? <EmptyState label="リードタイムデータなし" /> : (
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={data.leadtime} margin={{ top: 8, right: 8, left: 4, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="lead_hours" tick={AXIS_TICK} tickLine={false}
                     label={{ value: "← 締切直前　　締切の何時間前　　早い先行購入 →",
                              position: "insideBottom", offset: -10,
                              style: { fontSize: 10, fill: "#9ca3af" } }} />
              <YAxis tick={AXIS_TICK} tickLine={false} width={46}
                     tickFormatter={(v: number) => v.toLocaleString()} />
              <Tooltip contentStyle={{ fontSize: 11 }}
                       labelFormatter={(v) => `締切 ${v} 時間前`}
                       formatter={(value, name) => [
                         `${Number(value).toLocaleString()} pt`,
                         MEETING_LABELS[String(name)] ?? name]} />
              {/* 凡例は下に自前で置く。Recharts の Legend は dataKey のアルファベット順に
                  並んでしまい、朝→深夜という読み順が崩れるため（v3 で payload 指定も不可）。 */}
              {leadtimeKeys.map((k, i) => (
                <Bar key={k} dataKey={k} stackId="lead" fill={MEETING_COLORS[k]}
                     radius={i === leadtimeKeys.length - 1 ? [2, 2, 0, 0] : undefined}
                     maxBarSize={30} />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        )}
        {leadtimeKeys.length > 0 && (
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 flex items-center gap-3 flex-wrap justify-center">
            {leadtimeKeys.map(k => (
              <span key={k} className="inline-flex items-center gap-1">
                <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: MEETING_COLORS[k] }} />
                {MEETING_LABELS[k] ?? k}
              </span>
            ))}
          </p>
        )}
      </Card>

      {/* ── ランク別（kiseki 側の断面） ─────────────────── */}
      <Card
        title="入稿ランク別 売上 × 成績"
        note="netkeirin 側にランクの概念は無く、kiseki の入稿記録（netkeirin_submissions）と突き合わせて初めて出せる断面。どのランクが売れていて、そのランクは当たっているのかを見る。「未入稿」は入稿記録と結び付かなかったレース。"
      >
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-xs min-w-[520px]">
            <thead>
              <tr className="text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
                <th className="text-left font-normal py-1.5 px-2">ランク</th>
                <th className="text-right font-normal py-1.5 px-2">レース</th>
                <th className="text-right font-normal py-1.5 px-2">販売個数</th>
                <th className="text-right font-normal py-1.5 px-2">売上pt</th>
                <th className="text-right font-normal py-1.5 px-2">的中率</th>
                <th className="text-right font-normal py-1.5 px-2">ガミ率</th>
                <th className="text-right font-normal py-1.5 px-2">回収率</th>
              </tr>
            </thead>
            <tbody>
              {data.by_rank.map(r => (
                <tr key={r.rank} className="border-b border-gray-50 dark:border-gray-800 last:border-0">
                  <td className="py-1.5 px-2 font-semibold text-gray-700 dark:text-gray-200">{r.rank}</td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-gray-600 dark:text-gray-300">{r.n_races}</td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-gray-600 dark:text-gray-300">{r.n_sold.toLocaleString()}</td>
                  <td className="py-1.5 px-2 text-right tabular-nums font-semibold text-gray-800 dark:text-gray-100">{r.sold_paid_points.toLocaleString()}</td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-gray-600 dark:text-gray-300">
                    {formatPct(r.hit_rate, 0)}<span className="text-gray-400 ml-0.5">({r.n_hits})</span>
                  </td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-gray-600 dark:text-gray-300">{formatPct(r.garami_rate, 0)}</td>
                  <td className={`py-1.5 px-2 text-right tabular-nums font-semibold ${
                    (r.recovery_rate ?? 0) >= 1 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"
                  }`}>{formatPct(r.recovery_rate, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ── レース明細 ─────────────────────────────── */}
      <Card title={`レース明細（全 ${raceTable.length} 件）`} note="売上の大きい順。スクロールできます。">
        <div className="overflow-auto max-h-96 -mx-1">
          <table className="w-full text-xs min-w-[560px]">
            <thead className="sticky top-0 bg-white dark:bg-gray-900">
              <tr className="text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
                <th className="text-left font-normal py-1.5 px-2">日</th>
                <th className="text-left font-normal py-1.5 px-2">レース</th>
                <th className="text-left font-normal py-1.5 px-2">ランク</th>
                <th className="text-center font-normal py-1.5 px-2">的中</th>
                <th className="text-right font-normal py-1.5 px-2">購入者</th>
                <th className="text-right font-normal py-1.5 px-2">売上pt</th>
                <th className="text-right font-normal py-1.5 px-2">回収率</th>
              </tr>
            </thead>
            <tbody>
              {raceTable.map(r => (
                <tr key={r.race_id} className="border-b border-gray-50 dark:border-gray-800 last:border-0">
                  <td className="py-1.5 px-2 text-gray-500 dark:text-gray-400 whitespace-nowrap">{r.date.slice(5)}</td>
                  <td className="py-1.5 px-2 text-gray-700 dark:text-gray-200">
                    {r.label ?? `${r.venue_name ?? r.venue_code} ${r.race_no}R`}
                  </td>
                  <td className="py-1.5 px-2 text-gray-500 dark:text-gray-400 whitespace-nowrap">{r.rank ?? "—"}</td>
                  <td className="py-1.5 px-2 text-center whitespace-nowrap">
                    {r.hit ? (
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                        r.is_garami
                          ? "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300"
                          : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                      }`}>{r.is_garami ? "ガミ的中" : "的中"}</span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">不的中</span>
                    )}
                  </td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-gray-600 dark:text-gray-300">{r.n_sold}</td>
                  <td className="py-1.5 px-2 text-right tabular-nums font-semibold text-gray-800 dark:text-gray-100">{r.sold_paid_points.toLocaleString()}</td>
                  <td className={`py-1.5 px-2 text-right tabular-nums ${
                    (r.recovery_rate ?? 0) >= 1 ? "text-emerald-600 dark:text-emerald-400" : "text-gray-500 dark:text-gray-400"
                  }`}>{formatPct(r.recovery_rate, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <p className="text-[11px] text-gray-400 dark:text-gray-500 leading-relaxed">
        データ取得元：netkeirin 分析支援ツール「予想家成績状況」（umaiaggre.yosoka.netkeiba.com）。
        レース別・日別とも毎日10:30に前日分を取得して上書き保存している（売上は速報値のため後日変動しうる）。
      </p>
    </div>
  );
}
