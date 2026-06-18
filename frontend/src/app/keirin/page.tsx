"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { Bike, HelpCircle, ChevronDown, ChevronUp } from "lucide-react";
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

function fmtStartAt(startAt: number | string | null): string | null {
  if (startAt == null) return null;
  const ts = typeof startAt === "number" ? startAt : parseInt(String(startAt), 10);
  if (isNaN(ts)) return null;
  return new Date(ts * 1000).toLocaleTimeString("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const RANK_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  SS:        { bg: "#b45309", text: "#fff", label: "SS" },
  S:         { bg: "#1d4ed8", text: "#fff", label: "S" },
  A:         { bg: "#15803d", text: "#fff", label: "A" },
  B:         { bg: "#6b7280", text: "#fff", label: "B" },
  WIDE:      { bg: "#7c3aed", text: "#fff", label: "W" },
  "7PLUS":   { bg: "#0891b2", text: "#fff", label: "7+" },
  "7PLUS_SS": { bg: "#d97706", text: "#fff", label: "7SS" },
  "7PLUS_S":  { bg: "#0891b2", text: "#fff", label: "7S" },
  "7PLUS_A":  { bg: "#0d9488", text: "#fff", label: "7A" },
};

// ---------------------------------------------------------------------------
// サブコンポーネント
// ---------------------------------------------------------------------------

function RankBadge({ rank, miwokuri }: { rank: string; miwokuri?: boolean }) {
  const badgeCls = "inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0";
  if (miwokuri) {
    return (
      <span style={{ background: "#9ca3af", color: "#fff" }} className={badgeCls}>
        ガ
      </span>
    );
  }
  const s = RANK_STYLE[rank];
  if (!s) {
    return (
      <span style={{ background: "#9ca3af", color: "#fff" }} className={badgeCls}>
        非
      </span>
    );
  }
  return (
    <span style={{ background: s.bg, color: s.text }} className={badgeCls}>
      {s.label}
    </span>
  );
}

function HitBadge({ hit, payout, bet, isSettled, isReference, isMiwokuri }: {
  hit: boolean; payout: number; bet: number; isSettled: boolean; isReference?: boolean; isMiwokuri?: boolean;
}) {
  if (isMiwokuri) {
    if (!isSettled) return <span className="text-xs text-gray-400">未確定</span>;
    if (hit && payout > 0) {
      return (
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-purple-50 text-purple-600 border border-purple-200">
            見送り 的中
          </span>
          <span className="text-xs font-semibold text-purple-600">¥{payout.toLocaleString()}</span>
        </div>
      );
    }
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400 dark:text-gray-500">見送り</span>
        {payout > 0 && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            実際払戻 <span className="font-semibold">¥{payout.toLocaleString()}</span>
          </span>
        )}
      </div>
    );
  }

  if (isReference) {
    // 非推奨(参考)レース
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400 dark:text-gray-500">参考払戻</span>
        <span className={`text-xs font-semibold ${payout > 0 ? "text-gray-700 dark:text-gray-200" : "text-gray-400 dark:text-gray-500"}`}>
          {payout > 0 ? `¥${payout.toLocaleString()}` : "—"}
        </span>
      </div>
    );
  }

  // 推奨・購入済みレース
  if (hit) {
    const isGami = bet > 0 && payout < bet;
    return (
      <div className="flex items-center gap-2 flex-wrap">
        {isGami ? (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-orange-100 text-orange-700 border border-orange-300">
            ガ 的中
          </span>
        ) : (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-700 border border-emerald-300">
            ✓ 的中
          </span>
        )}
        <span className="text-xs text-gray-600">
          {bet > 0 && <>¥{bet.toLocaleString()} → </>}
          <span className={`font-semibold ${isGami ? "text-orange-600" : "text-emerald-600"}`}>¥{payout.toLocaleString()}</span>
          {bet > 0 && <span className="text-gray-400 ml-1">({(payout / bet).toFixed(1)}倍)</span>}
        </span>
      </div>
    );
  }
  if (!isSettled) {
    return <span className="text-xs text-gray-400">未確定</span>;
  }
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-red-50 text-red-600 border border-red-200">
        ✗ 不的中
      </span>
      {bet > 0 && <span className="text-xs text-gray-400">¥{bet.toLocaleString()}</span>}
      {payout > 0 ? (
        <span className="text-xs text-gray-500 dark:text-gray-400">
          実際払戻 <span className="font-semibold">¥{payout.toLocaleString()}</span>
        </span>
      ) : (
        <span className="text-xs text-gray-400">払戻 —</span>
      )}
    </div>
  );
}


function EntryTable({ entries }: { entries: KeirinPick["entries"] }) {
  if (!entries.length) return <p className="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">出走情報なし</p>;
  const sorted = [...entries].sort((a, b) => (b.race_point ?? -Infinity) - (a.race_point ?? -Infinity));
  return (
    <table className="w-full">
      <thead>
        <tr className="border-b border-gray-100 dark:border-gray-700">
          <th className="text-center px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-7 sm:w-8">車</th>
          <th className="text-left px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs">選手名</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-9 sm:w-12">戦法</th>
          <th className="text-right px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14">指数</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-8 sm:w-10">着</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((e) => (
          <tr key={e.frame_no} className="border-b border-gray-50 dark:border-gray-700 last:border-0">
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 font-bold text-center text-xs sm:text-sm text-gray-700 dark:text-gray-200">{e.frame_no}</td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-xs sm:text-sm text-gray-800 dark:text-gray-100">{e.name ?? "—"}</td>
            <td className="px-1 sm:px-3 py-1 sm:py-1.5 text-center text-gray-500 dark:text-gray-400 text-xs">{e.style ?? "—"}</td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
              {e.race_point != null ? e.race_point.toFixed(1) : "—"}
            </td>
            <td className="px-1 sm:px-3 py-1 sm:py-1.5 text-center">
              {e.finish_order != null && e.finish_order > 0 ? (
                <span
                  className={`inline-flex items-center justify-center w-5 h-5 sm:w-6 sm:h-6 rounded-full text-xs font-bold
                    ${e.finish_order === 1 ? "bg-amber-400 text-white" :
                      e.finish_order <= 3 ? "bg-blue-100 text-blue-700" : "text-gray-400"}`}
                >
                  {e.finish_order}
                </span>
              ) : e.finish_order === 0 ? (
                <span className="text-xs text-gray-400">失</span>
              ) : (
                <span className="text-gray-300">—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// コンポーネント外に置くことで react-hooks/purity を回避
function computeIsSettled(status: number, startAt: number | string | null): boolean {
  if (status === 3) return true;
  const sec = typeof startAt === "number" ? startAt : parseInt(String(startAt ?? ""), 10);
  // VPS同期遅延を考慮し発走から90分後も確定とみなす
  return !isNaN(sec) && sec + 5400 < Date.now() / 1000;
}

function CollapsedResult({ hit, payout, bet, isPurchased, isMiwokuri }: {
  hit: boolean; payout: number; bet: number; isPurchased: boolean; isMiwokuri: boolean;
}) {
  if (isMiwokuri) {
    if (hit && payout > 0) return <span className="text-xs text-purple-500">見送 ¥{payout.toLocaleString()}</span>;
    if (payout > 0) return <span className="text-xs text-gray-400 dark:text-gray-500">実際 ¥{payout.toLocaleString()}</span>;
    return null;
  }
  if (isPurchased) {
    if (hit) {
      const isGami = bet > 0 && payout < bet;
      return (
        <span className={`text-xs font-semibold ${isGami ? "text-orange-500" : "text-emerald-600 dark:text-emerald-400"}`}>
          ✓ ¥{payout.toLocaleString()}
        </span>
      );
    }
    if (payout > 0) return <span className="text-xs text-gray-400 dark:text-gray-500">実際 ¥{payout.toLocaleString()}</span>;
    return <span className="text-xs text-red-500 font-semibold">✗</span>;
  }
  if (payout > 0) {
    return <span className="text-xs text-gray-400 dark:text-gray-500">参考 ¥{payout.toLocaleString()}</span>;
  }
  return null;
}

function PickCard({ pick, cardId }: { pick: KeirinPick; cardId?: string }) {
  const isSettled = computeIsSettled(pick.status, pick.start_at);
  const [collapsed, setCollapsed] = useState(true);
  const isWide = pick.rank === "WIDE";
  const is7Plus = pick.rank.startsWith("7PLUS");
  const isMiwokuri = pick.miwokuri;
  const isPurchased = !isMiwokuri && pick.bet_amount > 0;

  const betTypeLabel = isWide ? "ワイド" : is7Plus ? "3連複" : pick.rank === "SS" ? "3連単" : "3連複";
  const comboLabel = pick.pred_combo
    ? `${betTypeLabel}: ${pick.pred_combo}${pick.n_combos && pick.n_combos > 1 ? ` (${pick.n_combos}点)` : ""}`
    : undefined;

  const startTime = fmtStartAt(pick.start_at);

  return (
    <div id={cardId} className={`bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden${isMiwokuri ? " opacity-55" : ""}`}>
      {/* ヘッダー行（クリックで折りたたみトグル） */}
      <button
        type="button"
        onClick={() => setCollapsed(v => !v)}
        className={`w-full flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-50 dark:bg-gray-800 text-left${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}
      >
        <RankBadge rank={pick.rank} miwokuri={isMiwokuri} />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-1.5 sm:gap-2 flex-wrap">
            <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{pick.venue_name}</span>
            <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{pick.race_no}R</span>
            {startTime && (
              <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{startTime}</span>
            )}
            {(pick.grade || pick.race_type) && (
              <span className="text-gray-500 dark:text-gray-400 text-xs">{pick.grade ?? ""} {pick.race_type ?? ""}</span>
            )}
          </div>
        </div>
        {/* 折りたたみ時: 結果サマリーをインライン表示 */}
        {collapsed && isSettled && (
          <CollapsedResult hit={pick.hit} payout={pick.payout} bet={pick.bet_amount} isPurchased={isPurchased} isMiwokuri={isMiwokuri} />
        )}
        <ChevronDown
          size={15}
          className={`flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-150${collapsed ? "" : " rotate-180"}`}
        />
      </button>

      {/* 展開時コンテンツ */}
      {!collapsed && (
        <>
          {/* 買い目行 */}
          <div className="px-3 sm:px-4 py-1.5 border-b border-gray-50 dark:border-gray-700 flex items-center gap-2 sm:gap-3">
            <span className="text-xs sm:text-sm font-medium text-gray-700 dark:text-gray-200 flex-1 min-w-0 break-words">
              {comboLabel ?? "—"}
            </span>
            {pick.synth_odds != null && !isMiwokuri && (
              <span className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
                合成 <span className="font-semibold text-gray-700 dark:text-gray-200">{pick.synth_odds.toFixed(1)}</span>倍
              </span>
            )}
            {pick.prerace_gami != null && !isMiwokuri && (
              pick.prerace_gami >= 5.0 ? (
                <span className="text-xs flex-shrink-0 text-emerald-600 dark:text-emerald-400 font-medium">
                  直前 {pick.prerace_gami.toFixed(1)}倍✓
                </span>
              ) : (
                <span className="text-xs flex-shrink-0 text-orange-500 dark:text-orange-400 font-medium">
                  直前 {pick.prerace_gami.toFixed(1)}倍⚠
                </span>
              )
            )}
          </div>

          <EntryTable entries={pick.entries} />

          {(isSettled || pick.hit) && (
            <div className="px-3 sm:px-4 py-2 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
              <HitBadge
                hit={pick.hit}
                payout={pick.payout}
                bet={pick.bet_amount}
                isSettled={isSettled}
                isReference={!isPurchased && !isMiwokuri}
                isMiwokuri={isMiwokuri}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

type PeriodData = KeirinSummary["today"];
type RankStats = NonNullable<PeriodData["by_rank"]>[string];

const RANK_ORDER = ["SS", "S", "A"] as const;
const RANK_BADGE_STYLE: Record<string, string> = {
  SS: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  S:  "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  A:  "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-400",
};

function RankSubRow({ rankKey, data }: { rankKey: string; data: RankStats }) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";
  const hitRate = data.n_picks > 0
    ? `${((data.n_hits / data.n_picks) * 100).toFixed(0)}%`
    : "—";
  const badgeClass = RANK_BADGE_STYLE[rankKey] ?? "bg-gray-100 text-gray-600";

  return (
    <tr className="border-b border-gray-50 dark:border-gray-800 last:border-0 bg-gray-50/50 dark:bg-gray-800/30">
      <td className="py-1 px-2 sm:px-3">
        <span className="flex items-center gap-1.5 pl-3">
          <span className={`inline-flex items-center justify-center w-6 h-5 rounded text-xs font-bold ${badgeClass}`}>
            {rankKey}
          </span>
        </span>
      </td>
      <td className="py-1 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums">
        {data.n_picks}
      </td>
      <td className="py-1 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums">
        {data.n_hits}
        <span className="text-gray-400 dark:text-gray-500 ml-0.5">({hitRate})</span>
      </td>
      <td className="hidden sm:table-cell py-1 px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums">
        ¥{data.total_bet.toLocaleString()}
      </td>
      <td className="hidden sm:table-cell py-1 px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums">
        ¥{data.total_payout.toLocaleString()}
      </td>
      <td className={`py-1 px-1.5 sm:px-3 text-right text-xs tabular-nums ${roiColor}`}>
        {formatROI(data.roi)}
      </td>
    </tr>
  );
}

function SummaryRow({ label, sub, data, showRanks }: { label: string; sub?: string; data: PeriodData; showRanks?: boolean }) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";
  const hitRate = data.n_picks > 0
    ? `${((data.n_hits / data.n_picks) * 100).toFixed(0)}%`
    : "—";
  const byRank = data.by_rank ?? {};
  const hasRanks = showRanks && RANK_ORDER.some(r => (byRank[r]?.n_picks ?? 0) > 0);

  return (
    <>
      <tr className="border-b border-gray-100 dark:border-gray-700">
        {/* 期間 */}
        <td className="py-1.5 px-2 sm:px-3 text-xs sm:text-sm text-gray-700 dark:text-gray-200 font-medium">
          {label}
          {sub && <span className="block text-xs text-gray-400 dark:text-gray-500 font-normal">{sub}</span>}
        </td>
        {/* 件数 */}
        <td className="py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm text-gray-700 dark:text-gray-200 tabular-nums">
          {data.n_picks}
        </td>
        {/* 的中 */}
        <td className="py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm text-gray-700 dark:text-gray-200 tabular-nums">
          {data.n_hits}
          <span className="text-xs text-gray-400 dark:text-gray-500 ml-0.5">({hitRate})</span>
        </td>
        {/* 投資・回収: sm以上のみ表示 */}
        <td className="hidden sm:table-cell py-1.5 px-3 text-right text-sm text-gray-700 dark:text-gray-200 tabular-nums">
          ¥{data.total_bet.toLocaleString()}
        </td>
        <td className="hidden sm:table-cell py-1.5 px-3 text-right text-sm text-gray-700 dark:text-gray-200 tabular-nums">
          ¥{data.total_payout.toLocaleString()}
        </td>
        {/* 回収率 */}
        <td className={`py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm tabular-nums ${roiColor}`}>
          {formatROI(data.roi)}
        </td>
      </tr>
      {hasRanks && RANK_ORDER.map(rk => {
        const rd = byRank[rk];
        if (!rd || rd.n_picks === 0) return null;
        return <RankSubRow key={rk} rankKey={rk} data={rd} />;
      })}
    </>
  );
}

function SummaryCard({ summary }: { summary: KeirinSummary }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div className="px-3 sm:px-4 py-2 border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex items-center">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 flex-1">投資・回収サマリー</h2>
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors px-1.5 py-0.5 rounded"
          aria-label={expanded ? "ランク詳細を閉じる" : "ランク詳細を開く"}
        >
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          <span className="hidden sm:inline">{expanded ? "閉じる" : "ランク別"}</span>
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 dark:border-gray-700">
              <th className="py-1.5 px-2 sm:px-3 text-left text-xs text-gray-500 dark:text-gray-400 font-medium">期間</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">件数</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">的中</th>
              <th className="hidden sm:table-cell py-1.5 px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">投資</th>
              <th className="hidden sm:table-cell py-1.5 px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">回収</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">回収率</th>
            </tr>
          </thead>
          <tbody>
            <SummaryRow label="当日" data={summary.today} showRanks={expanded} />
            <SummaryRow label="当月" data={summary.month} showRanks={expanded} />
            <SummaryRow label="当年" data={summary.year} showRanks={expanded} />
            <SummaryRow
              label="HOLD精度"
              sub={summary.test_from && summary.test_to ? `${summary.test_from}〜${summary.test_to}` : undefined}
              data={summary.test}
              showRanks={expanded}
            />
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

function nextPickId(picks: KeirinPick[]): string | null {
  const nowSec = Date.now() / 1000;
  const upcoming = picks
    .filter((p) => {
      const ts = typeof p.start_at === "number" ? p.start_at : parseInt(String(p.start_at ?? ""), 10);
      return !isNaN(ts) && ts > nowSec && p.status < 3;
    })
    .sort((a, b) => {
      const ta = typeof a.start_at === "number" ? a.start_at : parseInt(String(a.start_at ?? ""), 10);
      const tb = typeof b.start_at === "number" ? b.start_at : parseInt(String(b.start_at ?? ""), 10);
      return ta - tb;
    });
  return upcoming.length > 0 ? `pick-${upcoming[0].id}` : null;
}

export default function KeirinPage() {
  const [date, setDate] = useState(todayYYYYMMDD());
  const [picks, setPicks] = useState<KeirinPick[]>([]);
  const [summary, setSummary] = useState<KeirinSummary | null>(null);
  const [loadingPicks, setLoadingPicks] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dateInputRef = useRef<HTMLInputElement>(null);
  const isToday = date === todayYYYYMMDD();
  const nextId = isToday ? nextPickId(picks) : null;

  const openPicker = () => {
    const input = dateInputRef.current;
    if (!input) return;
    try { input.showPicker(); } catch { input.click(); }
  };

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
    void loadData(date); // eslint-disable-line react-hooks/set-state-in-effect
  }, [date, loadData]);

  return (
    <div className="w-full sm:max-w-3xl sm:mx-auto px-3 sm:px-4 py-4 pb-20 space-y-4">
      {/* タイトル */}
      <div className="flex items-center gap-2">
        <Bike size={22} className="text-blue-500" />
        <h1 className="text-xl font-extrabold tracking-widest text-gray-900 dark:text-white">KEIRIN</h1>
        <Link
          href="/keirin/help"
          className="ml-auto flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
        >
          <HelpCircle size={15} />
          推奨ガイド
        </Link>
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
          className="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 text-sm hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200"
        >
          ← 前日
        </button>
        <div className="flex items-center gap-2">
          {!isToday && (
            <button
              onClick={() => setDate(todayYYYYMMDD())}
              className="text-[11px] px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              今日
            </button>
          )}
          <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{fmtYMD(date)}</span>
          <div className="relative">
            <button
              onClick={openPicker}
              className="text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors text-base leading-none"
              aria-label="日付を選択"
            >
              📅
            </button>
            <input
              key={date}
              ref={dateInputRef}
              type="date"
              aria-hidden="true"
              tabIndex={-1}
              className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
              defaultValue={toISODate(date)}
              onChange={(e) => {
                const v = e.target.value.replace(/-/g, "");
                if (v.length === 8) setDate(v);
              }}
            />
          </div>
        </div>
        <button
          onClick={() => setDate(nextDay(date))}
          disabled={isToday}
          className="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 text-sm hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          翌日 →
        </button>
      </div>

      {/* エラー */}
      {error && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-3 text-sm text-amber-700">
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
            <PickCard key={p.id} pick={p} cardId={`pick-${p.id}`} />
          ))}
        </div>
      )}

      {/* スティッキーボトムナビ */}
      <div
        style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
        className="fixed bottom-0 left-0 right-0 z-50 bg-white/90 dark:bg-gray-900/90 backdrop-blur-sm border-t border-gray-200 dark:border-gray-700"
      >
        <div className="flex items-center justify-between max-w-3xl mx-auto px-3 py-2 gap-2">
          <button
            onClick={() => setDate(prevDay(date))}
            className="flex-1 px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 text-center"
          >
            ← 前日
          </button>
          {nextId ? (
            <button
              onClick={() => {
                document.getElementById(nextId)?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
              className="flex-[2] px-3 py-1.5 rounded-lg border border-blue-400 dark:border-blue-500 text-sm font-semibold text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 text-center truncate"
            >
              次のレース ↓
            </button>
          ) : (
            <div className="flex-[2] px-3 py-1.5 text-sm text-gray-400 dark:text-gray-500 text-center">
              {isToday ? "終了" : fmtYMD(date)}
            </div>
          )}
          <button
            onClick={() => setDate(nextDay(date))}
            disabled={isToday}
            className="flex-1 px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed text-center"
          >
            翌日 →
          </button>
        </div>
      </div>
    </div>
  );
}
