"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { Bike, HelpCircle, ChevronDown, ChevronUp, BarChart2 } from "lucide-react";
import { fetchKeirinPicks, fetchKeirinSummary, refreshKeirinPicks, triggerKeirinFetchOdds, triggerKeirinFetchResults, type KeirinPick, type KeirinSummary } from "@/lib/api";
import { todayYYYYMMDD } from "@/lib/utils";

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

// ガミ足切り閾値（keirin側と揃える）: レース単位 min(全目)≥7.0（2026-07-10 SS/S→R置き換え）
const GAMI_THRESHOLD = 7.0;

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
  // 2026-07〜: SS = 内部rank "7PLUS_R"（三連複・レース単位min≥7・全目購入）、
  // S/S+ = "7PLUS_ST"/"7PLUS_STP"（三連単1着固定フォーメーション・S+は200円/点増額）。
  // 旧方式(7PLUS_SS/7PLUS_S)の行は全期間を新方式で再構築済みのため存在しない。
  "7PLUS_R":    { bg: "#d97706", text: "#fff", label: "SS" },
  "7PLUS_ST":   { bg: "#1d4ed8", text: "#fff", label: "S" },
  "7PLUS_STP":  { bg: "#4338ca", text: "#fff", label: "S+" },
  "7PLUS_CAND": { bg: "#9ca3af", text: "#fff", label: "候補" },
};

// ---------------------------------------------------------------------------
// サブコンポーネント
// ---------------------------------------------------------------------------

function RankBadge({ rank, miwokuri, gamiStatus }: { rank: string; miwokuri?: boolean; gamiStatus?: "ok" | "ng" | null }) {
  const badgeCls = "inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0";

  // ジャッジ済みの場合、緑(OK) or 橙(NG)の ring を外側に付ける
  const ringStyle: React.CSSProperties | undefined = gamiStatus === "ok"
    ? { outline: "2px solid #10b981", outlineOffset: "2px" }
    : gamiStatus === "ng"
      ? { outline: "2px solid #f97316", outlineOffset: "2px" }
      : undefined;

  if (miwokuri) {
    return (
      <span style={{ background: "#9ca3af", color: "#fff", ...ringStyle }} className={badgeCls}>
        ガ
      </span>
    );
  }
  const s = RANK_STYLE[rank];
  if (!s) {
    return (
      <span style={{ background: "#9ca3af", color: "#fff", ...ringStyle }} className={badgeCls}>
        非
      </span>
    );
  }
  return (
    <span style={{ background: s.bg, color: s.text, ...ringStyle }} className={badgeCls}>
      {s.label}
    </span>
  );
}

function PayoutInfo({ trio, trifecta }: { trio: number; trifecta?: number }) {
  if (trio <= 0 && (trifecta ?? 0) <= 0) {
    return <span className="text-xs text-gray-400 dark:text-gray-500 flex-shrink-0">払戻 —</span>;
  }
  return (
    <span className="text-xs text-gray-500 dark:text-gray-400 tabular-nums flex-shrink-0">
      {trio > 0 && (
        <>三連複 <span className="font-semibold text-gray-700 dark:text-gray-200">¥{trio.toLocaleString()}</span></>
      )}
      {(trifecta ?? 0) > 0 && (
        <>{trio > 0 && <span className="mx-1 text-gray-300 dark:text-gray-600">|</span>}三連単 <span className="font-semibold text-gray-700 dark:text-gray-200">¥{(trifecta ?? 0).toLocaleString()}</span></>
      )}
    </span>
  );
}

function HitBadge({ hit, payout, trioPayout, trifectaPayout, bet, isSettled, isReference, isMiwokuri, isGamiSkip }: {
  hit: boolean; payout: number; trioPayout: number; trifectaPayout?: number; bet: number; isSettled: boolean; isReference?: boolean; isMiwokuri?: boolean; isGamiSkip?: boolean;
}) {
  if (isGamiSkip) {
    if (!isSettled) return <span className="text-xs text-orange-400 dark:text-orange-500">ガミ落ち</span>;
    return (
      <div className="flex items-center justify-between w-full gap-2">
        <span className="text-xs text-orange-400 dark:text-orange-500">ガミ条件落ち</span>
        <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
      </div>
    );
  }

  if (isMiwokuri) {
    if (!isSettled) return <span className="text-xs text-gray-400">未確定</span>;
    return (
      <div className="flex items-center justify-between w-full gap-2">
        {hit ? (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-purple-50 text-purple-600 border border-purple-200">
            見送り 的中
          </span>
        ) : (
          <span className="text-xs text-gray-400 dark:text-gray-500">見送り</span>
        )}
        <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
      </div>
    );
  }

  if (isReference) {
    return (
      <div className="flex items-center justify-between w-full gap-2">
        <span className="text-xs text-gray-400 dark:text-gray-500">参考</span>
        <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
      </div>
    );
  }

  // 購入済みレース
  if (hit) {
    const isGami = bet > 0 && payout < bet;
    return (
      <div className="flex items-center justify-between w-full gap-2">
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
        {trioPayout > 0 && <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />}
      </div>
    );
  }
  if (!isSettled) {
    return <span className="text-xs text-gray-400">未確定</span>;
  }
  return (
    <div className="flex items-center justify-between w-full gap-2">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-red-50 text-red-600 border border-red-200">
          ✗ 不的中
        </span>
        {bet > 0 && <span className="text-xs text-gray-400">¥{bet.toLocaleString()}</span>}
      </div>
      <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
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

function CollapsedResult({ hit, payout, trioPayout, trifectaPayout, bet, isPurchased, isMiwokuri, isGamiSkip }: {
  hit: boolean; payout: number; trioPayout: number; trifectaPayout?: number; bet: number; isPurchased: boolean; isMiwokuri: boolean; isGamiSkip?: boolean;
}) {
  const tp = trifectaPayout ?? 0;
  const trioEl = (trioPayout > 0 || tp > 0)
    ? (
      <span className="text-xs text-gray-400 dark:text-gray-500 tabular-nums">
        {trioPayout > 0 && <>複¥{trioPayout.toLocaleString()}</>}
        {tp > 0 && <>{trioPayout > 0 && " "}単¥{tp.toLocaleString()}</>}
      </span>
    )
    : null;

  if (isGamiSkip) {
    const label = <span className="text-xs text-orange-400 dark:text-orange-500">ガミ落ち</span>;
    if (!trioEl) return label;
    return <div className="flex items-center gap-1.5 flex-shrink-0">{label}{trioEl}</div>;
  }

  if (isMiwokuri) {
    const label = hit
      ? <span className="text-xs text-purple-500 font-semibold">見送 的中</span>
      : <span className="text-xs text-gray-400 dark:text-gray-500">見送り</span>;
    if (!trioEl) return label;
    return <div className="flex items-center gap-1.5 flex-shrink-0">{label}{trioEl}</div>;
  }

  if (isPurchased) {
    if (hit) {
      const isGami = bet > 0 && payout < bet;
      const hitEl = (
        <span className={`text-xs font-semibold ${isGami ? "text-orange-500" : "text-emerald-600 dark:text-emerald-400"}`}>
          ✓ ¥{payout.toLocaleString()}
        </span>
      );
      if (!trioEl) return hitEl;
      return <div className="flex items-center gap-1.5 flex-shrink-0">{hitEl}{trioEl}</div>;
    }
    const missEl = <span className="text-xs text-red-500 font-semibold">✗</span>;
    if (!trioEl) return missEl;
    return <div className="flex items-center gap-1.5 flex-shrink-0">{missEl}{trioEl}</div>;
  }

  return trioEl;
}

function NoPickRow({ pick }: { pick: KeirinPick }) {
  const [collapsed, setCollapsed] = useState(true);
  const startTime = fmtStartAt(pick.start_at);
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden opacity-75">
      <button
        type="button"
        onClick={() => setCollapsed(v => !v)}
        className={`w-full flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-50 dark:bg-gray-800 text-left${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}
      >
        <span className="inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0 bg-gray-200 dark:bg-gray-700 text-gray-400 dark:text-gray-500">—</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-1.5 sm:gap-2 flex-wrap">
            <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{pick.venue_name}</span>
            <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{pick.race_no}R</span>
            {startTime && <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{startTime}</span>}
            {(pick.grade || pick.race_type) && (
              <span className="text-gray-400 dark:text-gray-500 text-xs">{pick.grade ?? ""} {pick.race_type ?? ""}</span>
            )}
          </div>
        </div>
        <span className="text-[10px] text-gray-300 dark:text-gray-600 flex-shrink-0 mr-1">推奨外</span>
        <ChevronDown
          size={15}
          className={`flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-150${collapsed ? "" : " rotate-180"}`}
        />
      </button>
      {!collapsed && (
        <EntryTable entries={pick.entries} />
      )}
    </div>
  );
}

function PickCard({ pick, cardId }: { pick: KeirinPick; cardId?: string }) {
  const isSettled = computeIsSettled(pick.status, pick.start_at);
  const [collapsed, setCollapsed] = useState(true);
  const isWide = pick.rank === "WIDE";
  const is7Plus = (pick.rank ?? "").startsWith("7PLUS");
  const isMiwokuri = pick.miwokuri;
  const isPurchased = !isMiwokuri && pick.bet_amount > 0;
  // ガミ落ち = オッズ条件（三連複 <閾値倍）で購入不成立になった候補。
  // 未購入行は採点で全て miwokuri=TRUE になるため（2026-07-08 正本化）、
  // 見送り行は prerace_gami<閾値 を「ガミ落ち」として灰色の見送りと区別する。
  // 未購入なら SS も対象（購入済み SS はカット後最安値が prerace_gami のため 閾値未満 にならない）。
  // prerace_gami>=閾値 の見送りは別条件（合成オッズ/gap23/gap12）不成立 → 通常の見送り表示。
  const gamiThr = GAMI_THRESHOLD;
  // 三連単行(S/S+)の prerace_gami は三連複基準の値のためガミ判定に使わない
  // （三連単のガミ条件は三連単オッズ min≥10 で判定済み・購入行にガミ落ちは存在しない）
  const isTrifectaRow = (pick.rank ?? "").startsWith("7PLUS_ST");
  const pgBelow = !isTrifectaRow && pick.prerace_gami !== null && pick.prerace_gami !== undefined && pick.prerace_gami < gamiThr;
  const isGamiSkip = pgBelow && (isMiwokuri || pick.rank !== "7PLUS_SS");
  const gamiStatus: "ok" | "ng" | null = !isTrifectaRow && pick.prerace_gami != null && (!isMiwokuri || isGamiSkip)
    ? pick.prerace_gami >= gamiThr ? "ok" : "ng"
    : null;

  const rankStr = pick.rank ?? "";
  // 三連単S/S+ (7PLUS_ST/STP) の pred_combo は「3連単F: 1→2,3→全」形式（券種プレフィックス込み）
  const isST = rankStr.startsWith("7PLUS_ST");
  const betTypeLabel = isWide ? "ワイド" : is7Plus ? "3連複" : pick.rank === "SS" ? "3連単" : "3連複";
  const comboLabel = pick.pred_combo
    ? `${isST ? pick.pred_combo : `${betTypeLabel}: ${pick.pred_combo}`}${pick.n_combos && pick.n_combos > 1 ? ` (${pick.n_combos}点)` : ""}`
    : undefined;

  const startTime = fmtStartAt(pick.start_at);

  return (
    <div id={cardId} className={`bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden${isMiwokuri || isGamiSkip ? " opacity-55" : ""}`}>
      {/* ヘッダー行（クリックで折りたたみトグル） */}
      <button
        type="button"
        onClick={() => setCollapsed(v => !v)}
        className={`w-full flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-50 dark:bg-gray-800 text-left${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}
      >
        {/* ガミ落ちはランクバッジを残す（推奨候補だった事実を表示）＋橙リング */}
        <RankBadge rank={rankStr} miwokuri={isMiwokuri && !isGamiSkip} gamiStatus={gamiStatus} />
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
        {/* 折りたたみ時: 結果サマリー or ガミ判定チップをインライン表示 */}
        {collapsed && isSettled && (
          <CollapsedResult hit={pick.hit} payout={pick.payout} trioPayout={pick.trio_payout} trifectaPayout={pick.trifecta_payout} bet={pick.bet_amount} isPurchased={isPurchased} isMiwokuri={isMiwokuri} isGamiSkip={isGamiSkip} />
        )}
        {collapsed && !isSettled && gamiStatus === "ok" && (
          <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium flex-shrink-0">
            {pick.prerace_gami!.toFixed(1)}倍✓
          </span>
        )}
        {collapsed && !isSettled && gamiStatus === "ng" && (
          <span className="text-xs text-orange-500 dark:text-orange-400 font-medium flex-shrink-0">
            {pick.prerace_gami!.toFixed(1)}倍⚠
          </span>
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
            {pick.gap23 != null && !isMiwokuri && (
              <span className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
                g23 <span className="font-semibold text-gray-700 dark:text-gray-200">{(pick.gap23 * 100).toFixed(1)}</span>pt
              </span>
            )}
            {pick.prerace_gami != null && !isMiwokuri && !isTrifectaRow && (
              pick.prerace_gami >= gamiThr ? (
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
                trioPayout={pick.trio_payout}
                trifectaPayout={pick.trifecta_payout}
                bet={pick.bet_amount}
                isSettled={isSettled}
                isReference={!isPurchased && !isMiwokuri && !isGamiSkip}
                isMiwokuri={isMiwokuri}
                isGamiSkip={isGamiSkip}
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

// by_rank キー: "R"=SS / "ST"・"STP"=三連単S/S+（2026-07〜の現行体系のみ表示）
const RANK_ORDER = ["R", "ST", "STP"] as const;
const RANK_LABEL: Record<string, string> = { R: "SS", ST: "S", STP: "S+" };
const RANK_BADGE_STYLE: Record<string, string> = {
  R:   "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  ST:  "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  STP: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-400",
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
          <span className={`inline-flex items-center justify-center min-w-6 px-1 h-5 rounded text-xs font-bold ${badgeClass}`}>
            {RANK_LABEL[rankKey] ?? rankKey}
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
      if (!p.has_pick || p.id == null) return false;
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

const HIDE_NOPICK_KEY = "keirin:hideNoPickRows";

export default function KeirinPage() {
  const [date, setDate] = useState(todayYYYYMMDD());
  const [picks, setPicks] = useState<KeirinPick[]>([]);
  const [summary, setSummary] = useState<KeirinSummary | null>(null);
  const [loadingPicks, setLoadingPicks] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);
  const [fetchingOdds, setFetchingOdds] = useState(false);
  const [fetchingResults, setFetchingResults] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [hideNoPickRows, setHideNoPickRows] = useState(false);
  const dateInputRef = useRef<HTMLInputElement>(null);
  const isToday = date === todayYYYYMMDD();
  const nextId = isToday ? nextPickId(picks) : null;
  const hasCand = picks.some((p) => p.race_key.includes("#CAND"));

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
      fetchKeirinPicks(iso, true),
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

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshMsg(null);
    try {
      const result = await refreshKeirinPicks(toISODate(date));
      setRefreshMsg(result.message);
      await loadData(date);
    } catch {
      setRefreshMsg("採点更新に失敗しました");
    } finally {
      setRefreshing(false);
    }
  }, [date, loadData]);

  const handleFetchOdds = useCallback(async () => {
    setFetchingOdds(true);
    setActionMsg(null);
    try {
      const result = await triggerKeirinFetchOdds();
      setActionMsg(result.ok ? "オッズ更新を開始しました（約30秒後に再読込）" : `エラー: ${result.message}`);
      if (result.ok) setTimeout(() => void loadData(date), 35000);
    } catch {
      setActionMsg("オッズ更新に失敗しました");
    } finally {
      setFetchingOdds(false);
    }
  }, [date, loadData]);

  const handleFetchResults = useCallback(async () => {
    setFetchingResults(true);
    setActionMsg(null);
    try {
      const result = await triggerKeirinFetchResults();
      setActionMsg(result.ok ? "結果取得を開始しました（約60秒後に再読込）" : `エラー: ${result.message}`);
      if (result.ok) setTimeout(() => void loadData(date), 65000);
    } catch {
      setActionMsg("結果取得に失敗しました");
    } finally {
      setFetchingResults(false);
    }
  }, [date, loadData]);

  useEffect(() => {
    void loadData(date); // eslint-disable-line react-hooks/set-state-in-effect
  }, [date, loadData]);

  useEffect(() => {
    setHideNoPickRows(localStorage.getItem(HIDE_NOPICK_KEY) === "true");
    const onStorage = (e: StorageEvent) => {
      if (e.key === HIDE_NOPICK_KEY) setHideNoPickRows(e.newValue === "true");
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return (
    <div className="w-full sm:max-w-3xl sm:mx-auto px-3 sm:px-4 py-4 pb-44 md:pb-20 space-y-4">
      {/* タイトル */}
      <div className="flex items-center gap-2">
        <Bike size={22} className="text-blue-500" />
        <h1 className="text-xl font-extrabold tracking-widest text-gray-900 dark:text-white">KEIRIN</h1>
        <div className="ml-auto flex items-center gap-3">
          <Link
            href="/keirin/stats"
            className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
            aria-label="成績グラフ"
          >
            <BarChart2 size={16} />
            <span className="hidden sm:inline">成績グラフ</span>
          </Link>
          <Link
            href="/keirin/help"
            className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
          >
            <HelpCircle size={15} />
            <span className="hidden sm:inline">推奨ガイド</span>
          </Link>
        </div>
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

      {/* 採点更新ボタン：#CAND レコードがある場合のみ表示 */}
      {hasCand && (
        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex-1 px-3 py-2 rounded-lg border border-orange-300 dark:border-orange-600 text-sm font-semibold text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20 hover:bg-orange-100 dark:hover:bg-orange-900/40 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {refreshing ? "採点中…" : "⚡ 採点更新"}
          </button>
          {refreshMsg && (
            <span className="text-xs text-gray-500 dark:text-gray-400">{refreshMsg}</span>
          )}
        </div>
      )}

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
        <>
          {picks.some(p => !p.has_pick) && (
            <div className="flex items-center justify-end gap-2">
              <span className="text-xs text-gray-400">推奨外を非表示</span>
              <button
                role="switch"
                aria-checked={hideNoPickRows}
                onClick={() => {
                  const next = !hideNoPickRows;
                  setHideNoPickRows(next);
                  localStorage.setItem(HIDE_NOPICK_KEY, String(next));
                }}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${
                  hideNoPickRows ? "bg-blue-500" : "bg-gray-300"
                }`}
              >
                <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
                  hideNoPickRows ? "translate-x-5" : "translate-x-1"
                }`} />
              </button>
            </div>
          )}
          <div className="space-y-2">
            {picks.map((p, idx) => {
              if (!p.has_pick) {
                if (hideNoPickRows) return null;
                return <NoPickRow key={`nopick-${p.race_key}-${idx}`} pick={p} />;
              }
              return <PickCard key={`pick-${p.id}-${p.race_key}`} pick={p} cardId={`pick-${p.id}`} />;
            })}
          </div>
        </>
      )}

      {/* スティッキーボトムナビ */}
      <div
        style={{ paddingBottom: "4px" }}
        className="fixed bottom-14 left-0 right-0 z-50 bg-white/90 dark:bg-gray-900/90 backdrop-blur-sm border-t border-gray-200 dark:border-gray-700 md:bottom-0"
      >
        <div className="max-w-3xl mx-auto px-3 py-2 space-y-1.5">
          {/* 行1: 日付ナビ */}
          <div className="flex items-center gap-2">
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
          {/* 行2: 今日のみ — オッズ更新・結果取得 */}
          {isToday && (
            <div className="flex items-center gap-2">
              <button
                onClick={handleFetchOdds}
                disabled={fetchingOdds}
                className="flex-1 px-2 py-1.5 rounded-lg border border-cyan-300 dark:border-cyan-600 text-xs font-semibold text-cyan-600 dark:text-cyan-400 bg-cyan-50 dark:bg-cyan-900/20 hover:bg-cyan-100 dark:hover:bg-cyan-900/40 disabled:opacity-50 disabled:cursor-not-allowed text-center"
              >
                {fetchingOdds ? "更新中…" : "📊 オッズ更新"}
              </button>
              <button
                onClick={handleFetchResults}
                disabled={fetchingResults}
                className="flex-1 px-2 py-1.5 rounded-lg border border-violet-300 dark:border-violet-600 text-xs font-semibold text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-900/20 hover:bg-violet-100 dark:hover:bg-violet-900/40 disabled:opacity-50 disabled:cursor-not-allowed text-center"
              >
                {fetchingResults ? "取得中…" : "📋 結果取得"}
              </button>
              {actionMsg && (
                <span className="text-[10px] text-gray-500 dark:text-gray-400 leading-tight">{actionMsg}</span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
