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

function fromISODate(iso: string): string {
  return iso.replace(/-/g, "");
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

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const RANK_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  SS: { bg: "#b45309", text: "#fff", label: "SS" },
  S:  { bg: "#1d4ed8", text: "#fff", label: "S" },
  A:  { bg: "#15803d", text: "#fff", label: "A" },
  B:  { bg: "#6b7280", text: "#fff", label: "B" },
  WIDE: { bg: "#7c3aed", text: "#fff", label: "W" },
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

  const comboLabel = isWide
    ? `ワイド: ${pick.pred_combo}`
    : pick.pred_combo
      ? `3連単: ${pick.pred_combo}${pick.n_combos && pick.n_combos > 1 ? ` (${pick.n_combos}点)` : ""}`
      : pick.pred_combo;

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      {/* ヘッダー行 */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-50 border-b border-gray-100">
        <RankBadge rank={pick.rank} />
        <div className="flex-1 min-w-0">
          <span className="font-semibold text-gray-800 text-sm">{pick.venue_name}</span>
          <span className="text-gray-500 text-xs ml-2">
            {pick.race_no}R {pick.grade ?? ""} {pick.race_type ?? ""}
          </span>
        </div>
        {pick.start_at && (
          <span className="text-xs text-gray-400 flex-shrink-0">
            {String(pick.start_at).slice(0, 5)}
          </span>
        )}
      </div>

      {/* 買い目 + 合成オッズ */}
      <div className="px-4 py-2 border-b border-gray-50 flex items-center gap-3">
        <span className="text-sm font-medium text-gray-700 flex-1">{comboLabel ?? "—"}</span>
        {pick.synth_odds != null && (
          <span className="text-xs text-gray-500 flex-shrink-0">
            合成 <span className="font-semibold text-gray-700">{pick.synth_odds.toFixed(1)}</span>倍
          </span>
        )}
      </div>

      {/* 出走表 */}
      <EntryTable entries={pick.entries} />

      {/* 結果 */}
      {isSettled && (
        <div className="px-4 py-2.5 border-t border-gray-100 bg-gray-50">
          <HitBadge hit={pick.hit} payout={pick.payout} bet={pick.bet_amount} />
        </div>
      )}
    </div>
  );
}

function SummaryRow({ label, data }: { label: string; data: KeirinSummary["today"] }) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";

  return (
    <tr className="border-b border-gray-100 last:border-0">
      <td className="py-2 px-3 text-sm text-gray-600 font-medium">{label}</td>
      <td className="py-2 px-3 text-right text-sm text-gray-700">{data.n_picks}</td>
      <td className="py-2 px-3 text-right text-sm text-gray-700">{data.n_hits}</td>
      <td className="py-2 px-3 text-right text-sm text-gray-700">¥{data.total_bet.toLocaleString()}</td>
      <td className="py-2 px-3 text-right text-sm text-gray-700">¥{data.total_payout.toLocaleString()}</td>
      <td className={`py-2 px-3 text-right text-sm ${roiColor}`}>{formatROI(data.roi)}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

export default function KeirinPage() {
  const [activeTab, setActiveTab] = useState<"picks" | "summary">("picks");
  const [date, setDate] = useState(todayYYYYMMDD());
  const [picks, setPicks] = useState<KeirinPick[]>([]);
  const [summary, setSummary] = useState<KeirinSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPicks = useCallback(async (d: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchKeirinPicks(toISODate(d));
      setPicks(data);
    } catch {
      setError("ピックの取得に失敗しました。バックエンドの接続を確認してください。");
      setPicks([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSummary = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchKeirinSummary();
      setSummary(data);
    } catch {
      setError("サマリーの取得に失敗しました。");
    }
  }, []);

  useEffect(() => {
    if (activeTab === "picks") loadPicks(date);
    else loadSummary();
  }, [activeTab, date, loadPicks, loadSummary]);

  const goDate = (d: string) => {
    setDate(d);
  };

  const isToday = date === todayYYYYMMDD();

  return (
    <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
      {/* タイトル */}
      <div className="flex items-center gap-2">
        <Bike size={20} className="text-blue-600" />
        <h1 className="text-lg font-bold text-gray-800">競輪ピック</h1>
      </div>

      {/* タブ */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {(["picks", "summary"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 py-1.5 rounded-lg text-sm font-medium transition-colors
              ${activeTab === tab
                ? "bg-white text-gray-800 shadow-sm"
                : "text-gray-500 hover:text-gray-700"}`}
          >
            {tab === "picks" ? "ピック" : "推奨サマリー"}
          </button>
        ))}
      </div>

      {activeTab === "picks" && (
        <>
          {/* 日付ナビ */}
          <div className="flex items-center justify-between">
            <button
              onClick={() => goDate(prevDay(date))}
              className="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 text-gray-600"
            >
              ← 前日
            </button>
            <span className="text-sm font-medium text-gray-700">{fmtYMD(date)}</span>
            <button
              onClick={() => goDate(nextDay(date))}
              disabled={isToday}
              className="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              翌日 →
            </button>
          </div>

          {/* コンテンツ */}
          {loading && (
            <div className="space-y-3 animate-pulse">
              {[1, 2, 3].map((i) => (
                <div key={i} className="bg-white rounded-xl border border-gray-100 h-28" />
              ))}
            </div>
          )}

          {!loading && error && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-700">
              {error}
            </div>
          )}

          {!loading && !error && picks.length === 0 && (
            <div className="text-center py-12 text-gray-400 text-sm">
              この日のピックはありません
            </div>
          )}

          {!loading && !error && picks.length > 0 && (
            <div className="space-y-3">
              {picks.map((p) => (
                <PickCard key={p.id} pick={p} />
              ))}
            </div>
          )}
        </>
      )}

      {activeTab === "summary" && (
        <>
          {error && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-700">
              {error}
            </div>
          )}

          {summary && (
            <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100 bg-gray-50">
                <h2 className="text-sm font-semibold text-gray-700">投資・回収サマリー</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="py-2 px-3 text-left text-xs text-gray-500 font-medium">期間</th>
                      <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">ピック数</th>
                      <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">的中数</th>
                      <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">総投資</th>
                      <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">総回収</th>
                      <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">回収率</th>
                    </tr>
                  </thead>
                  <tbody>
                    <SummaryRow label="当日" data={summary.today} />
                    <SummaryRow label="当月" data={summary.month} />
                    <SummaryRow label="当年" data={summary.year} />
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!summary && !error && (
            <div className="bg-white rounded-xl border border-gray-100 h-28 animate-pulse" />
          )}
        </>
      )}
    </div>
  );
}
