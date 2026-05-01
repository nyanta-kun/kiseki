"use client";

import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  HorseIndex,
  OddsData,
  RaceResult,
  buildOddsWsUrl,
  buildResultsWsUrl,
  fetchHorseHistory,
} from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { WsStatusBadge } from "@/components/WsStatusBadge";
import { IndexBar } from "./IndexBar";
import { cn, indexColor } from "@/lib/utils";
import { HorseHistorySection } from "./HorseHistorySection";
import { PaywallGate } from "@/components/PaywallGate";

type Props = {
  raceId: number;
  indices: HorseIndex[];
  initialOdds: OddsData;
  initialResults: RaceResult[];
  isPremium?: boolean;
  raceNumber?: number;
  paywallEnabled?: boolean;
};

type SortKey = "composite" | "speed" | "last3f" | "jockey" | "rotation" | "finish";

const SUB_INDICES: { key: keyof HorseIndex; label: string }[] = [
  { key: "speed_index",       label: "速度"   },
  { key: "last3f_index",      label: "後3F"   },
  { key: "course_aptitude",   label: "コース" },
  { key: "jockey_index",      label: "騎手"   },
  { key: "pace_index",        label: "展開"   },
  { key: "rotation_index",    label: "ローテ" },
  { key: "pedigree_index",    label: "血統"   },
  { key: "position_advantage",label: "枠順"   },
  { key: "training_index",    label: "調教"   },
  { key: "paddock_index",     label: "パドック"},
];

const ANAGUSA_RANK_COLOR: Record<string, string> = {
  A: "bg-red-50 text-red-600 border-red-200",
  B: "bg-orange-50 text-orange-600 border-orange-200",
  C: "bg-yellow-50 text-yellow-700 border-yellow-200",
};

/**
 * DM シグナルタグ → 短縮ラベル/色/ツールチップのマッピング
 * バックテスト実証値: 99.0%カバレッジ・8,618レース・3年実績 (2023-2026)
 *
 * 表形式 (RaceDetailClient) では狭いスペースに複数バッジが並ぶため、
 * 馬名カラム直後に短縮ラベル (2文字) で表示。スマホでも視認可能。
 */
const DM_SIGNAL_META: Record<string, { label: string; cls: string; title: string }> = {
  "三冠一致":      { label: "🔥三冠", cls: "bg-rose-100 text-rose-800 border-rose-300",          title: "総合・DMtime・DMbattle 全1位 (勝率39%/複勝72%)" },
  "高得点鉄板":    { label: "⭐鉄板", cls: "bg-amber-100 text-amber-800 border-amber-300",        title: "総合≥60 ∧ DM-battle≥65 (ROI 101%, 勝率47%)" },
  "穴ぐさDM":      { label: "🏆穴DM", cls: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-300", title: "穴ぐさA/B ∧ DM-battle1位 ∧ 人気≥5 (ROI 189% / 最強)" },
  "DM大穴":        { label: "⚡大穴", cls: "bg-purple-100 text-purple-800 border-purple-300",    title: "DM-battle1位 ∧ 人気≥7 ∧ battle≥65 (ROI 154%)" },
  "DM高オッズ":    { label: "⚡高オ", cls: "bg-violet-100 text-violet-800 border-violet-300",    title: "DM-battle1位 ∧ 単勝≥10倍 ∧ DM-time≤2位 (ROI 130%)" },
  "穴ぐさ+DMtime": { label: "💎穴T",  cls: "bg-cyan-100 text-cyan-800 border-cyan-300",          title: "穴ぐさA ∧ DM-time1位 (ROI 103%)" },
  "人気下振れ":    { label: "❌警戒", cls: "bg-slate-200 text-slate-700 border-slate-400",       title: "人気≤3位だが総合・DM-battle両方が4位以下 (ROI 74%、軸候補から除外推奨)" },
};

function DmSignalBadges({ signals }: { signals: string[] | null | undefined }) {
  if (!signals || signals.length === 0) return null;
  return (
    <>
      {signals.map((sig) => {
        const meta = DM_SIGNAL_META[sig];
        if (!meta) return null;
        return (
          <span
            key={sig}
            title={meta.title}
            className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap", meta.cls)}
          >
            {meta.label}
          </span>
        );
      })}
    </>
  );
}

/**
 * 購入シグナル (v26 breakaway 検証 2026-05-02 / 3年138,728 horse-races)。
 * バッジは IndicesTable と統一。レース詳細ページの行内・末尾凡例で使用。
 */
const PURCHASE_SIGNAL_META: Record<
  "super_buy" | "buy" | "watch",
  { label: string; cls: string; title: string }
> = {
  super_buy: {
    label: "🔥超推奨",
    cls: "bg-rose-100 text-rose-800 border-rose-300",
    title: "上位2頭抜け出し(2位vs3位差≥7) ∧ rank≤2 ∧ オッズ≥10 → 単勝ROI 1.593",
  },
  buy: {
    label: "◎推奨",
    cls: "bg-emerald-100 text-emerald-800 border-emerald-300",
    title: "上位2頭抜け出し(2位vs3位差≥5) ∧ rank≤2 ∧ オッズ≥10 → 単勝ROI 1.290",
  },
  watch: {
    label: "○注目",
    cls: "bg-sky-50 text-sky-700 border-sky-200",
    title: "rank≤3 ∧ オッズ≥10 → 単勝ROI 1.042",
  },
};

function PurchaseSignalBadge({
  signal,
}: {
  signal: HorseIndex["purchase_signal"];
}) {
  if (!signal) return null;
  const meta = PURCHASE_SIGNAL_META[signal];
  if (!meta) return null;
  return (
    <span
      title={meta.title}
      className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap", meta.cls)}
    >
      {meta.label}
    </span>
  );
}

function horseNumToFrame(horseNum: number, totalHorses: number): number {
  if (totalHorses <= 8) return horseNum;
  const extra = totalHorses - 8;
  const singleFrames = 8 - extra;
  if (horseNum <= singleFrames) return horseNum;
  return singleFrames + Math.ceil((horseNum - singleFrames) / 2);
}

function frameColorClass(frame: number): string {
  switch (frame) {
    case 1: return "bg-white border border-gray-400 text-gray-800";
    case 2: return "bg-gray-800 text-white";
    case 3: return "bg-red-600 text-white";
    case 4: return "bg-blue-600 text-white";
    case 5: return "bg-yellow-400 text-gray-900";
    case 6: return "bg-green-600 text-white";
    case 7: return "bg-orange-500 text-white";
    case 8: return "bg-pink-500 text-white";
    default: return "bg-gray-200 text-gray-700";
  }
}

function barWidth(v: number | null): string {
  if (v === null) return "0%";
  return `${Math.max(0, Math.min(100, v))}%`;
}

function pct(v: number | null): string {
  if (v === null) return "–";
  return `${Math.round(v * 100)}%`;
}

function winOddsColorClass(odds: number | null): string {
  if (odds === null) return "text-gray-600";
  if (odds < 10) return "text-red-600 font-semibold";
  if (odds >= 100) return "text-blue-600";
  return "text-gray-600";
}

function evColorClass(ev: number | null): string {
  if (ev === null) return "text-gray-400";
  if (ev >= 1.5) return "text-green-600 font-bold";
  if (ev >= 1.2) return "text-green-500 font-semibold";
  if (ev >= 1.0) return "text-gray-600";
  return "text-gray-400";
}

function finishBadgeClass(pos: number | null | undefined): string {
  if (pos == null) return "text-gray-400";
  if (pos === 1) return "bg-yellow-100 text-yellow-800 font-bold px-1 rounded";
  if (pos === 2) return "bg-gray-100 text-gray-700 font-bold px-1 rounded";
  if (pos === 3) return "bg-orange-100 text-orange-700 font-bold px-1 rounded";
  return "text-gray-400";
}

function isExternalDarkHorse(horse: HorseIndex, compositeRank: number): boolean {
  if (compositeRank < 4) return false;
  return horse.nb_course_rank === 1 ||
    (horse.nb_ave_rank !== null && horse.nb_ave_rank <= 2 && horse.km_rank === 1);
}

function toResultsMap(results: RaceResult[]): Map<number, number | null> {
  return new Map(
    results
      .filter((r) => r.horse_number !== null)
      .map((r) => [r.horse_number as number, r.finish_position])
  );
}

function useIsMounted() {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

function SortButton({
  k, label, sortKey, setSortKey,
}: {
  k: SortKey; label: string; sortKey: SortKey; setSortKey: (k: SortKey) => void;
}) {
  return (
    <button
      onClick={() => setSortKey(k)}
      className={cn(
        "text-[10px] px-2 py-0.5 rounded-full border transition-colors whitespace-nowrap",
        sortKey === k
          ? "text-white border-green-600 bg-green-700"
          : "text-gray-500 border-gray-200 hover:border-green-400 bg-white"
      )}
    >
      {label}
    </button>
  );
}

function formatTime(sec: number | null): string {
  if (sec === null) return "–";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function RaceDetailClient({
  raceId,
  indices,
  initialOdds,
  initialResults,
  isPremium = false,
  raceNumber = 1,
  paywallEnabled = false,
}: Props) {
  const mounted = useIsMounted();
  const [resultsMap, setResultsMap] = useState<Map<number, number | null>>(
    () => toResultsMap(initialResults)
  );
  const [odds, setOdds] = useState<OddsData>(initialOdds ?? { win: {}, place: {} });
  const liveRegionRef = useRef<HTMLDivElement | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("composite");
  const [expandedHorse, setExpandedHorse] = useState<number | null>(null);
  const hasResults = resultsMap.size > 0;

  // 成績 WebSocket
  const resultsWsUrl = mounted ? buildResultsWsUrl(raceId) : null;
  const handleResultsMessage = useCallback((data: unknown) => {
    if (Array.isArray(data) && data.length > 0) {
      setResultsMap(toResultsMap(data as RaceResult[]));
    }
  }, []);
  const { isConnected: wsConnected } = useWebSocket(resultsWsUrl, handleResultsMessage);

  // オッズ WebSocket
  const oddsWsUrl = mounted ? buildOddsWsUrl(raceId) : null;
  const handleOddsMessage = useCallback((data: unknown) => {
    setOdds(data as OddsData);
    if (liveRegionRef.current) {
      liveRegionRef.current.textContent = "オッズが更新されました";
      setTimeout(() => {
        if (liveRegionRef.current) liveRegionRef.current.textContent = "";
      }, 3000);
    }
  }, []);
  useWebSocket(oddsWsUrl, handleOddsMessage, { reconnectInterval: 30_000 });

  const totalHorses = indices.length;

  const maxComposite = useMemo(
    () => Math.max(...indices.map((h) => h.composite_index ?? 0)),
    [indices]
  );

  const compositeRankMap = useMemo(() => {
    const sorted = [...indices].sort((a, b) => (b.composite_index ?? 0) - (a.composite_index ?? 0));
    return new Map(sorted.map((h, i) => [h.horse_number, i + 1]));
  }, [indices]);

  const topHorseNumber = useMemo(
    () =>
      indices.reduce(
        (best, h) => (h.composite_index > best.composite_index ? h : best),
        indices[0]
      )?.horse_number,
    [indices]
  );

  const sorted = useMemo(() => {
    return [...indices].sort((a, b) => {
      if (sortKey === "finish" && hasResults) {
        const pa = resultsMap.get(a.horse_number) ?? 999;
        const pb = resultsMap.get(b.horse_number) ?? 999;
        return pa - pb;
      }
      const keyMap: Partial<Record<SortKey, keyof HorseIndex>> = {
        composite: "composite_index",
        speed:     "speed_index",
        last3f:    "last3f_index",
        jockey:    "jockey_index",
        rotation:  "rotation_index",
      };
      const k = keyMap[sortKey] ?? "composite_index";
      const av = (a[k] as number | null) ?? 0;
      const bv = (b[k] as number | null) ?? 0;
      return bv - av;
    });
  }, [indices, sortKey, hasResults, resultsMap]);

  function isCutOff(horse: HorseIndex): boolean {
    const gap = maxComposite - (horse.composite_index ?? 0);
    const rank = compositeRankMap.get(horse.horse_number) ?? 999;
    return gap >= 20 || (gap >= 15 && rank >= 5);
  }

  const colSpan = hasResults ? 12 : 11;

  return (
    <PaywallGate isPremium={isPremium} raceNumber={raceNumber} paywallEnabled={paywallEnabled ?? false}>
      <>
        <div ref={liveRegionRef} aria-live="polite" aria-atomic="true" className="sr-only" />

        <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
          {/* ヘッダー + ソートボタン */}
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
              <span className="w-1 h-4 rounded inline-block bg-green-600" />
              出馬表 指数一覧
              <span className="text-xs text-gray-400 font-normal ml-1">{indices.length}頭</span>
              {mounted && wsConnected !== undefined && (
                <span className="ml-1">
                  <WsStatusBadge connected={wsConnected} label="成績更新: 再接続中…" />
                </span>
              )}
            </h2>
            <div className="flex gap-1 ml-auto flex-wrap">
              <SortButton k="composite" label="総合" sortKey={sortKey} setSortKey={setSortKey} />
              <SortButton k="speed" label="速度" sortKey={sortKey} setSortKey={setSortKey} />
              <span className="hidden sm:contents">
                <SortButton k="last3f" label="後3F" sortKey={sortKey} setSortKey={setSortKey} />
                <SortButton k="jockey" label="騎手" sortKey={sortKey} setSortKey={setSortKey} />
                <SortButton k="rotation" label="ローテ" sortKey={sortKey} setSortKey={setSortKey} />
              </span>
              {hasResults && (
                <SortButton k="finish" label="着順" sortKey={sortKey} setSortKey={setSortKey} />
              )}
            </div>
          </div>

          {/* テーブル */}
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-xs min-w-[320px]">
              <thead>
                <tr className="border-b border-gray-100 text-gray-400 text-[10px]">
                  <th className="text-right py-1 pl-2 pr-2 w-8">馬番</th>
                  <th className="text-left py-1 px-1">馬名</th>
                  <th className="text-right py-1 px-1 w-20">総合</th>
                  <th className="text-right py-1 px-1 w-12">速度</th>
                  <th className="hidden sm:table-cell text-right py-1 px-1 w-12">後3F</th>
                  <th className="hidden sm:table-cell text-right py-1 px-1 w-12">騎手</th>
                  <th className="hidden sm:table-cell text-right py-1 px-1 w-12">ローテ</th>
                  <th className="text-right py-1 px-1 w-12">勝率</th>
                  <th className="text-right py-1 px-1 w-12">複率</th>
                  <th className="text-right py-1 px-1 w-14">単オッズ</th>
                  <th className="text-right py-1 pr-2 w-12">期待値</th>
                  {hasResults && <th className="text-right py-1 pr-2 w-10">着順</th>}
                </tr>
              </thead>
              <tbody>
                {sorted.flatMap((horse) => {
                  const finishPos = resultsMap.get(horse.horse_number);
                  const isWin = finishPos === 1;
                  const isPlace = finishPos !== undefined && finishPos !== null && finishPos <= 3;
                  const winOdds = odds.win[String(horse.horse_number)] ?? null;
                  const ev =
                    horse.win_probability !== null && winOdds !== null
                      ? horse.win_probability * winOdds
                      : null;
                  const frameNum = horseNumToFrame(horse.horse_number, totalHorses);
                  const cutOff = isCutOff(horse);
                  const isTop = horse.horse_number === topHorseNumber;
                  const compositeRank = compositeRankMap.get(horse.horse_number) ?? 99;
                  const isAnagusa = horse.anagusa_rank !== null && !isTop;
                  const isExtDark = !isTop && isExternalDarkHorse(horse, compositeRank);
                  const isExpanded = expandedHorse === horse.horse_number;

                  const rows = [
                    <tr
                      key={horse.horse_number}
                      onClick={() => setExpandedHorse(isExpanded ? null : horse.horse_number)}
                      className={cn(
                        "border-b border-gray-50 transition-colors whitespace-nowrap cursor-pointer",
                        cutOff ? "opacity-40 bg-gray-50" :
                        isWin ? "bg-yellow-50" :
                        isPlace ? "bg-orange-50/40" :
                        isTop ? "bg-green-50/40 hover:bg-green-50" :
                        "hover:bg-gray-50"
                      )}
                    >
                      {/* 馬番 */}
                      <td className="py-1.5 pl-2 pr-2 text-right">
                        <span className={cn(
                          "inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold tabular-nums",
                          frameColorClass(frameNum)
                        )}>
                          {horse.horse_number}
                        </span>
                      </td>

                      {/* 馬名 + バッジ */}
                      <td className="py-2 px-1 whitespace-normal">
                        <div className="flex items-center gap-1 flex-wrap">
                          <span className="text-gray-800 font-medium truncate block max-w-[110px]">
                            {horse.horse_name}
                            {isTop && (
                              <span className="ml-1 text-[9px] text-green-600 font-normal">◎</span>
                            )}
                          </span>
                          {isAnagusa && (
                            <span className={cn(
                              "text-[9px] px-1 py-0.5 rounded border font-bold",
                              ANAGUSA_RANK_COLOR[horse.anagusa_rank!] ?? "bg-yellow-50 text-yellow-700 border-yellow-200"
                            )}>
                              ☆{horse.anagusa_rank}
                            </span>
                          )}
                          {isExtDark && (
                            <span className="text-[9px] bg-teal-50 text-teal-700 border border-teal-200 px-1 py-0.5 rounded font-bold">
                              {horse.nb_course_rank === 1 ? "外◎" : "外○"}
                            </span>
                          )}
                          {/* DM × 穴ぐさ × 既存指数のシグナルタグ (軸/穴/警戒) */}
                          <DmSignalBadges signals={horse.dm_signals} />
                          {/* 購入シグナル (v26 breakaway ROI 検証ベース) */}
                          <PurchaseSignalBadge signal={horse.purchase_signal} />
                        </div>
                      </td>

                      {/* 総合 + バー */}
                      <td className="py-2 px-1">
                        <div className="flex items-center gap-1 justify-end">
                          <span className={indexColor(horse.composite_index)}>
                            {horse.composite_index.toFixed(1)}
                          </span>
                          <div className="w-12 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-green-500 rounded-full"
                              style={{ width: barWidth(horse.composite_index) }}
                            />
                          </div>
                        </div>
                      </td>

                      {/* 速度 */}
                      <td className={`py-2 px-1 text-right ${indexColor(horse.speed_index)}`}>
                        {horse.speed_index !== null ? horse.speed_index.toFixed(1) : "–"}
                      </td>

                      {/* 後3F */}
                      <td className={`hidden sm:table-cell py-2 px-1 text-right ${indexColor(horse.last3f_index)}`}>
                        {horse.last3f_index !== null ? horse.last3f_index.toFixed(1) : "–"}
                      </td>

                      {/* 騎手 */}
                      <td className={`hidden sm:table-cell py-2 px-1 text-right ${indexColor(horse.jockey_index)}`}>
                        {horse.jockey_index !== null ? horse.jockey_index.toFixed(1) : "–"}
                      </td>

                      {/* ローテ */}
                      <td className={`hidden sm:table-cell py-2 px-1 text-right ${indexColor(horse.rotation_index)}`}>
                        {horse.rotation_index !== null ? horse.rotation_index.toFixed(1) : "–"}
                      </td>

                      {/* 勝率 */}
                      <td className="py-2 px-1 text-right text-gray-600">
                        {pct(horse.win_probability)}
                      </td>

                      {/* 複率 */}
                      <td className="py-2 px-1 text-right text-gray-600">
                        {pct(horse.place_probability)}
                      </td>

                      {/* 単オッズ */}
                      <td className={`py-2 px-1 text-right ${winOddsColorClass(winOdds)}`}>
                        {winOdds !== null ? `${winOdds.toFixed(1)}倍` : "–"}
                      </td>

                      {/* 期待値 */}
                      <td className={`py-2 pr-2 text-right ${evColorClass(ev)}`}>
                        {ev !== null ? ev.toFixed(2) : "–"}
                      </td>

                      {/* 着順 */}
                      {hasResults && (
                        <td className="py-2 pr-2 text-right">
                          {finishPos != null ? (
                            <span className={finishBadgeClass(finishPos)}>{finishPos}着</span>
                          ) : (
                            <span className="text-gray-300">–</span>
                          )}
                        </td>
                      )}
                    </tr>,
                  ];

                  if (isExpanded) {
                    rows.push(
                      <tr key={`${horse.horse_number}-detail`}>
                        <td colSpan={colSpan} className="border-b border-gray-100 bg-gray-50 px-3 py-3">
                          {/* 指数内訳ヘッダー */}
                          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                            <p className="text-[10px] text-gray-400">指数内訳</p>
                            <div className="flex items-center gap-2 flex-wrap">
                              {/* 外部指数ランク */}
                              {(horse.nb_course_rank !== null || horse.nb_ave_rank !== null || horse.km_rank !== null) && (
                                <div className="flex items-center gap-1 flex-wrap">
                                  <span className="text-[10px] text-gray-400">外部指数</span>
                                  {horse.nb_course_rank !== null && (
                                    <span className={cn(
                                      "text-[10px] px-1 py-0.5 rounded border",
                                      horse.nb_course_rank === 1
                                        ? "bg-teal-50 text-teal-700 border-teal-200 font-bold"
                                        : "bg-gray-50 text-gray-500 border-gray-200"
                                    )}>
                                      コース{horse.nb_course_rank}位
                                    </span>
                                  )}
                                  {horse.nb_ave_rank !== null && (
                                    <span className={cn(
                                      "text-[10px] px-1 py-0.5 rounded border",
                                      horse.nb_ave_rank <= 2
                                        ? "bg-teal-50 text-teal-700 border-teal-200 font-bold"
                                        : "bg-gray-50 text-gray-500 border-gray-200"
                                    )}>
                                      NB{horse.nb_ave_rank}位
                                    </span>
                                  )}
                                  {horse.km_rank !== null && (
                                    <span className={cn(
                                      "text-[10px] px-1 py-0.5 rounded border",
                                      horse.km_rank === 1
                                        ? "bg-teal-50 text-teal-700 border-teal-200 font-bold"
                                        : "bg-gray-50 text-gray-500 border-gray-200"
                                    )}>
                                      KM{horse.km_rank}位
                                    </span>
                                  )}
                                </div>
                              )}
                              {/* 穴スコア */}
                              {horse.upside_score !== null && horse.upside_score !== undefined && (
                                <div className="flex items-center gap-1">
                                  <span className="text-[10px] text-gray-400">穴スコア</span>
                                  <span className={cn(
                                    "text-[11px] font-bold px-1.5 py-0.5 rounded border",
                                    horse.upside_score >= 0.7
                                      ? "bg-purple-100 text-purple-800 border-purple-300"
                                      : horse.upside_score >= 0.5
                                      ? "bg-purple-50 text-purple-700 border-purple-200"
                                      : "bg-gray-50 text-gray-500 border-gray-200"
                                  )}>
                                    {(horse.upside_score * 100).toFixed(0)}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>

                          {/* 指数グリッド */}
                          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                            {SUB_INDICES.map(({ key, label }) => {
                              const val = horse[key] as number | null;
                              return (
                                <div key={key} className="flex items-center gap-1.5">
                                  <span className="text-[10px] text-gray-500 w-10 flex-shrink-0">
                                    {label}
                                  </span>
                                  <span className={cn(
                                    "text-[11px] font-mono tabular-nums w-7 text-right flex-shrink-0",
                                    indexColor(val)
                                  )}>
                                    {val !== null ? val.toFixed(0) : "-"}
                                  </span>
                                  <div className="flex-1">
                                    <IndexBar value={val} />
                                  </div>
                                </div>
                              );
                            })}
                          </div>

                          {/* 近走成績 */}
                          <HorseHistorySection
                            horseId={horse.horse_id}
                            fetchHistory={fetchHorseHistory}
                          />
                        </td>
                      </tr>
                    );
                  }

                  return rows;
                })}
              </tbody>
            </table>
          </div>

          {/* 確定着順サマリ */}
          {hasResults && (
            <div className="mt-4 pt-3 border-t border-gray-100">
              <h3 className="text-xs font-semibold text-gray-500 mb-2">確定着順</h3>
              <div className="space-y-1">
                {initialResults
                  .filter((r) => r.finish_position !== null)
                  .sort((a, b) => (a.finish_position ?? 99) - (b.finish_position ?? 99))
                  .slice(0, 5)
                  .map((r) => (
                    <div key={r.horse_name} className="flex items-center gap-2 text-xs">
                      <span className={cn(
                        "min-w-[2.5rem] text-center text-[11px] py-0.5 rounded font-bold",
                        r.finish_position === 1 ? "bg-yellow-100 text-yellow-800" :
                        r.finish_position === 2 ? "bg-gray-100 text-gray-700" :
                        r.finish_position === 3 ? "bg-orange-100 text-orange-700" :
                        "text-gray-500"
                      )}>
                        {r.finish_position}着
                      </span>
                      <span className="font-medium text-gray-800">{r.horse_name}</span>
                      {r.finish_time !== null && (
                        <span className="text-gray-400 tabular-nums">{formatTime(r.finish_time)}</span>
                      )}
                      {r.last_3f !== null && (
                        <span className="text-gray-400 tabular-nums">後3F {r.last_3f.toFixed(1)}</span>
                      )}
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* 凡例 */}
          <div className="mt-3 text-[10px] text-gray-400 border-t border-gray-50 pt-2 space-y-0.5">
            <p>
              <span className="text-green-600">緑</span>=高評価 / <span className="text-red-500">赤</span>=低評価（65↑: 強 / 55–65: 良 / 45–55: 並 / 35–45: 劣 / ↓35: 弱）
            </p>
            <p>
              <span className="opacity-50">グレー</span>=足切り候補（トップ差20以上、または差15以上かつ5位以下）
            </p>
            <p>行クリックで指数内訳・近走成績を表示</p>
          </div>

          {/* バッジ凡例 (馬名横の 🔥◎○⚡💎❌ 等の意味) */}
          <details className="mt-4 text-[11px] text-gray-600 border border-gray-200 rounded-md bg-gray-50">
            <summary className="cursor-pointer font-bold px-3 py-2 select-none">
              バッジ凡例（クリックで展開）
            </summary>
            <div className="px-3 pb-3 pt-1 space-y-3">
              {/* 購入シグナル (v26) */}
              <div>
                <p className="font-bold text-gray-700 mb-1">
                  🛒 購入シグナル <span className="font-normal text-gray-500 text-[10px]">(v26 breakaway 検証 / 3年138,728 horse-races)</span>
                </p>
                <ul className="space-y-1 ml-1">
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", PURCHASE_SIGNAL_META.super_buy.cls)}>
                      🔥超推奨
                    </span>
                    <span>
                      上位2頭が3位以下から実力差つけて抜け出し（差≥7）&amp; 上位2頭で単勝オッズ≥10 →
                      <span className="font-bold text-rose-700"> 単勝ROI 1.593</span>（年46R）
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", PURCHASE_SIGNAL_META.buy.cls)}>
                      ◎推奨
                    </span>
                    <span>
                      上位2頭抜け出し（差≥5）&amp; 上位2頭で単勝オッズ≥10 →
                      <span className="font-bold text-emerald-700"> 単勝ROI 1.290</span>（年79R）
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", PURCHASE_SIGNAL_META.watch.cls)}>
                      ○注目
                    </span>
                    <span>
                      上位3頭で単勝オッズ≥10 → <span className="font-bold text-sky-700">単勝ROI 1.042</span>（年1786R）
                    </span>
                  </li>
                </ul>
                <p className="mt-1 ml-1 text-[10px] text-gray-500">
                  ※ 1〜3 番人気（オッズ&lt;6）の指数1位は単勝ROI 0.85〜0.89 で確実マイナス → 見送り推奨
                </p>
              </div>

              {/* DM シグナル */}
              <div>
                <p className="font-bold text-gray-700 mb-1">
                  📊 DM シグナル <span className="font-normal text-gray-500 text-[10px]">(JV-Next DM × 穴ぐさ × 既存指数 / 3年8,618R)</span>
                </p>
                <ul className="space-y-1 ml-1">
                  {Object.entries(DM_SIGNAL_META).map(([name, meta]) => (
                    <li key={name} className="flex items-start gap-2">
                      <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", meta.cls)}>
                        {meta.label}
                      </span>
                      <span>{meta.title}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* 穴ぐさランク */}
              <div>
                <p className="font-bold text-gray-700 mb-1">⭐ 穴ぐさランク <span className="font-normal text-gray-500 text-[10px]">(専門紙 sekito.anagusa ピック)</span></p>
                <ul className="space-y-1 ml-1">
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", ANAGUSA_RANK_COLOR.A)}>
                      ☆A
                    </span>
                    <span>最高評価ピック（穴推し本命）</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", ANAGUSA_RANK_COLOR.B)}>
                      ☆B
                    </span>
                    <span>準本命の穴推し</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", ANAGUSA_RANK_COLOR.C)}>
                      ☆C
                    </span>
                    <span>注目穴馬</span>
                  </li>
                </ul>
              </div>

              {/* 外部指数穴馬 */}
              <div>
                <p className="font-bold text-gray-700 mb-1">🎯 外部指数穴馬 <span className="font-normal text-gray-500 text-[10px]">(自指数4位以下だが netkeiba/kichiuma で上位)</span></p>
                <ul className="space-y-1 ml-1">
                  <li className="flex items-start gap-2">
                    <span className="text-[9px] bg-teal-50 text-teal-700 border border-teal-200 px-1 py-0.5 rounded font-bold whitespace-nowrap shrink-0">
                      外◎
                    </span>
                    <span>netkeiba コース指数 1位（自指数より外部評価が高い）</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-[9px] bg-teal-50 text-teal-700 border border-teal-200 px-1 py-0.5 rounded font-bold whitespace-nowrap shrink-0">
                      外○
                    </span>
                    <span>netkeiba 上位2 × kichiuma 1位（外部指数一致）</span>
                  </li>
                </ul>
              </div>

              {/* 馬名印 */}
              <div>
                <p className="font-bold text-gray-700 mb-1">🏆 その他</p>
                <ul className="space-y-1 ml-1">
                  <li className="flex items-start gap-2">
                    <span className="text-[10px] text-green-600 font-bold whitespace-nowrap shrink-0">◎</span>
                    <span>レース内の指数1位馬</span>
                  </li>
                </ul>
              </div>

              <p className="text-[10px] text-gray-500 italic pt-1 border-t border-gray-200">
                ※ ROI = 100円賭けた時の平均回収額 / 100。1.0 以上で期待値プラス。
                各バッジにマウスを合わせるとツールチップで詳細条件が表示されます。
              </p>
            </div>
          </details>
        </section>
      </>
    </PaywallGate>
  );
}
