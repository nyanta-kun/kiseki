"use client";

import { useEffect, useState, useCallback } from "react";
import { Bike } from "lucide-react";
import { fetchKeirinPicks, fetchKeirinSummary, type KeirinPick, type KeirinSummary } from "@/lib/api";
import { todayYYYYMMDD } from "@/lib/utils";

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

function fmtYMD(yyyymmdd: string): string {
  if (yyyymmdd.length !== 8) return yyyymmdd;
  return `${yyyymmdd.slice(0, 4)}/${yyyymmdd.slice(4, 6)}/${yyyymmdd.slice(6, 8)}`;
}

function toISODate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

function prevDay(yyyymmdd: string): string {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`);
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

function nextDay(yyyymmdd: string): string {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`);
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

function formatROI(roi: number | null): string {
  if (roi == null) return "—";
  return (roi * 100).toFixed(1) + "%";
}

function fmtDate(iso: string): string {
  return iso.slice(0, 7).replace("-", "/");
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const RANK_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  SS:     { bg: "#b45309", text: "#fff", label: "SS" },
  S:      { bg: "#1d4ed8", text: "#fff", label: "S" },
  A:      { bg: "#15803d", text: "#fff", label: "A" },
  B:      { bg: "#6b7280", text: "#fff", label: "B" },
  WIDE:   { bg: "#7c3aed", text: "#fff", label: "W" },
  "7PLUS": { bg: "#0891b2", text: "#fff", label: "7+" },
};

// ---------------------------------------------------------------------------
// サブコンポーネント
// ---------------------------------------------------------------------------

function RankBadge({ rank }: { rank: string }) {
  const s = RANK_STYLE[rank] ?? { bg: "#6b7280", text: "#fff", label: rank };
  return (
    <span
      style={{ background: s.bg, color: s.text }}
      className="inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0"
    >
      {s.label}
    </span>
  );
}

function HitBadge({ hit, payout, bet }: { hit: boolean; payout: number; bet: number }) {
  if (payout === 0 && !hit) {
    return <span className="text-xs text-gray-400">未確定</span>;
  }
  if (hit) {
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-700 border border-emerald-300">
          ✓ 的中
        </span>
        <span className="text-xs text-gray-600">
          ¥{bet.toLocaleString()} → <span className="font-semibold text-emerald-600">¥{payout.toLocaleString()}</span>
          <span className="text-gray-400 ml-1">({(payout / bet).toFixed(1)}倍)</span>
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-red-50 text-red-600 border border-red-200">
        ✗ 不的中
      </span>
      <span className="text-xs text-gray-400">¥{bet.toLocaleString()}</span>
    </div>
  );
}

function EntryTable({ entries }: { entries: KeirinPick["entries"] }) {
  if (!entries.length) return <p className="text-xs text-gray-400 px-4 py-2">出走情報なし</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100">
            <th className="text-left px-3 py-1.5 font-medium text-gray-500 text-xs w-8">車番</th>
            <th className="text-left px-3 py-1.5 font-medium text-gray-500 text-xs">選手名</th>
            <th className="text-center px-3 py-1.5 font-medium text-gray-500 text-xs w-12">戦法</th>
            <th className="text-right px-3 py-1.5 font-medium text-gray-500 text-xs w-14">指数</th>
            <th className="text-center px-3 py-1.5 font-medium text-gray-500 text-xs w-10">着順</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.frame_no} className="border-b border-gray-50 last:border-0 hover:bg-gray-50">
              <td className="px-3 py-1.5 font-bold text-center text-gray-700">{e.frame_no}</td>
              <td className="px-3 py-1.5 text-gray-800">{e.name ?? "—"}</td>
              <td className="px-3 py-1.5 text-center text-gray-500 text-xs">{e.style ?? "—"}</td>
              <td className="px-3 py-1.5 text-right font-mono text-gray-700">
                {e.race_point != null ? e.race_point.toFixed(1) : "—"}
              </td>
              <td className="px-3 py-1.5 text-center">
                {e.finish_order != null ? (
                  <span
                    className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold
                      ${e.finish_order === 1 ? "bg-amber-400 text-white" :
                        e.finish_order <= 3 ? "bg-blue-100 text-blue-700" : "text-gray-400"}`}
                  >
                    {e.finish_order}
                  </span>
                ) : (
                  <span className="text-gray-300">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PickCard({ pick }: { pick: KeirinPick }) {
  const isSettled = pick.status === 3;
  const isWide = pick.rank === "WIDE";
  const is7Plus = pick.rank === "7PLUS";

  const betTypeLabel = isWide ? "ワイド" : is7Plus ? "3連複" : pick.rank === "SS" ? "3連単" : "3連複";
  const comboLabel = pick.pred_combo
    ? `${betTypeLabel}: ${pick.pred_combo}${pick.n_combos && pick.n_combos > 1 ? ` (${pick.n_combos}点)` : ""}`
    : undefined;

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-50 border-b border-gray-100">
        <RankBadge rank={pick.rank} />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="font-semibold text-gray-800 text-sm">{pick.venue_name}</span>
            <span className="font-semibold text-gray-800 text-sm">{pick.race_no}R</span>
            {pick.start_at && (
              <span className="font-semibold text-gray-800 text-sm">
                {String(pick.start_at).slice(0, 5)}
              </span>
            )}
            {(pick.grade || pick.race_type) && (
              <span className="text-gray-400 text-xs">{pick.grade ?? ""} {pick.race_type ?? ""}</span>
            )}
          </div>
        </div>
      </div>

      <div className="px-4 py-2 border-b border-gray-50 flex items-center gap-3">
        <span className="text-sm font-medium text-gray-700 flex-1">{comboLabel ?? "—"}</span>
        {pick.synth_odds != null && (
          <span className="text-xs text-gray-500 flex-shrink-0">
            合成 <span className="font-semibold text-gray-700">{pick.synth_odds.toFixed(1)}</span>倍
          </span>
        )}
      </div>

      <EntryTable entries={pick.entries} />

      {isSettled && (
        <div className="px-4 py-2.5 border-t border-gray-100 bg-gray-50">
          <HitBadge hit={pick.hit} payout={pick.payout} bet={pick.bet_amount} />
        </div>
      )}
    </div>
  );
}

type PeriodData = KeirinSummary["today"];

function SummaryRow({ label, sub, data }: { label: string; sub?: string; data: PeriodData }) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";
  const hitRate = data.n_picks > 0
    ? `${((data.n_hits / data.n_picks) * 100).toFixed(0)}%`
    : "—";

  return (
    <tr className="border-b border-gray-100 last:border-0">
      <td className="py-2 px-3 text-sm text-gray-700 font-medium whitespace-nowrap">
        {label}
        {sub && <span className="block text-xs text-gray-400 font-normal">{sub}</span>}
      </td>
      <td className="py-2 px-3 text-right text-sm text-gray-700 tabular-nums">{data.n_picks}</td>
      <td className="py-2 px-3 text-right text-sm text-gray-700 tabular-nums">
        {data.n_hits}
        <span className="text-xs text-gray-400 ml-1">({hitRate})</span>
      </td>
      <td className="py-2 px-3 text-right text-sm text-gray-700 tabular-nums">¥{data.total_bet.toLocaleString()}</td>
      <td className="py-2 px-3 text-right text-sm text-gray-700 tabular-nums">¥{data.total_payout.toLocaleString()}</td>
      <td className={`py-2 px-3 text-right text-sm tabular-nums ${roiColor}`}>{formatROI(data.roi)}</td>
    </tr>
  );
}

function SummaryCard({ summary }: { summary: KeirinSummary }) {
  const testLabel = `${fmtDate(summary.test_from)}〜${fmtDate(summary.test_to)}`;
  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
        <h2 className="text-sm font-semibold text-gray-700">投資・回収サマリー</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100">
              <th className="py-2 px-3 text-left text-xs text-gray-500 font-medium">期間</th>
              <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">件数</th>
              <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">的中</th>
              <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">投資</th>
              <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">回収</th>
              <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">回収率</th>
            </tr>
          </thead>
          <tbody>
            <SummaryRow label="当日" data={summary.today} />
            <SummaryRow label="当月" data={summary.month} />
            <SummaryRow label="当年" data={summary.year} />
            <SummaryRow label="検証期間" sub={testLabel} data={summary.test} />
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

export default function KeirinPage() {
  const [date, setDate] = useState(todayYYYYMMDD());
  const [picks, setPicks] = useState<KeirinPick[]>([]);
  const [summary, setSummary] = useState<KeirinSummary | null>(null);
  const [loadingPicks, setLoadingPicks] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async (d: string) => {
    setLoadingPicks(true);
    setError(null);
    const iso = toISODate(d);
    const [picksResult, summaryResult] = await Promise.allSettled([
      fetchKeirinPicks(iso),
      fetchKeirinSummary(iso),
    ]);
    if (picksResult.status === "fulfilled") {
      setPicks(picksResult.value);
    } else {
      setError("ピックの取得に失敗しました。");
      setPicks([]);
    }
    if (summaryResult.status === "fulfilled") {
      setSummary(summaryResult.value);
    }
    setLoadingPicks(false);
  }, []);

  useEffect(() => {
    loadData(date);
  }, [date, loadData]);

  const isToday = date === todayYYYYMMDD();

  return (
    <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
      {/* タイトル */}
      <div className="flex items-center gap-2">
        <Bike size={20} className="text-blue-600" />
        <h1 className="text-lg font-bold text-gray-800">競輪ピック</h1>
      </div>

      {/* サマリー */}
      {summary ? (
        <SummaryCard summary={summary} />
      ) : (
        <div className="bg-white rounded-xl border border-gray-100 h-24 animate-pulse" />
      )}

      {/* 日付ナビ */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => setDate(prevDay(date))}
          className="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 text-gray-600"
        >
          ← 前日
        </button>
        <span className="text-sm font-medium text-gray-700">{fmtYMD(date)}</span>
        <button
          onClick={() => setDate(nextDay(date))}
          disabled={isToday}
          className="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          翌日 →
        </button>
      </div>

      {/* エラー */}
      {error && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-700">
          {error}
        </div>
      )}

      {/* ピック一覧 */}
      {loadingPicks ? (
        <div className="space-y-3 animate-pulse">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 h-28" />
          ))}
        </div>
      ) : !error && picks.length === 0 ? (
        <div className="text-center py-12 text-gray-400 text-sm">
          この日のピックはありません
        </div>
      ) : (
        <div className="space-y-3">
          {picks.map((p) => (
            <PickCard key={p.id} pick={p} />
          ))}
        </div>
      )}
    </div>
  );
}
